"""FocusController that reaches the ClearCore through Kmopoflight when it can.

Kmopoflight (the navigation app) holds the ClearCore's RS232 port open for its
own ``$Click*`` traffic and, in X-Parallax mode, runs a loopback UDP bridge
(``kmopoflight/bridge.py``) that forwards raw USB-style commands onto that port
and relays every line the ClearCore prints back. With both apps up, ALL of this
app's PCB traffic goes that way and the USB port is released; if Kmopoflight
goes away (no ``#PONG`` for ``LOST_S``) we reopen the USB port and carry on, and
switch back the moment it answers again.

Wire format is the unchanged USB one (``$M4*\\n``, ``+++\\n`` ...), so every
command method of :class:`FocusController` works as is; only ``_send`` and the
connection lifecycle differ. Replies reach ``on_response`` from the monitor
thread, exactly as pyserial's reader thread would deliver them.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable, Optional

from serial_comm import FocusController  # repo root; see sys.path bootstrap in app.py

log = logging.getLogger(__name__)

BRIDGE_ADDR = ("127.0.0.1", 56611)
BRIDGE_PORT_NAME = "Kmopoflight"   # pseudo COM port: bridge only, no USB fallback
PING_S = 1.0
LOST_S = 3.0
_MAX_DATAGRAM = 1024


def probe_bridge(timeout_s: float = 0.3) -> bool:
    """One ``#PING``; True if Kmopoflight answers (its camera port is open)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout_s)
        try:
            s.sendto(b"#PING", BRIDGE_ADDR)
            data, addr = s.recvfrom(_MAX_DATAGRAM)
        except OSError:
            return False
    return addr[0] == BRIDGE_ADDR[0] and data == b"#PONG"


class BridgingFocusController(FocusController):
    """``connect(port)`` picks the link: Kmopoflight bridge if it answers, else
    USB *port*; a monitor thread keeps re-evaluating and flips between them."""

    def __init__(self, on_link_changed: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self._on_link_changed = on_link_changed
        self._usb_port: Optional[str] = None
        self._sock: Optional[socket.socket] = None
        self._bridged = False
        self._last_pong = 0.0
        self._mon_running = False
        self._mon_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()   # serialises link flips against connect/disconnect

    # ── state ──────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._bridged or super().connected

    @property
    def bridged(self) -> bool:
        return self._bridged

    @property
    def link(self) -> str:
        if self._bridged:
            return "via Kmopoflight"
        if super().connected:
            return f"USB {self._usb_port}"
        return "no link"

    # ── lifecycle ──────────────────────────────────────────────────────
    def connect(self, port: str, on_response: Optional[Callable[[str], None]] = None) -> None:
        self.disconnect()
        self._usb_port = None if port == BRIDGE_PORT_NAME else port
        self._on_response = on_response
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.settimeout(0.2)
        if self._ping_wait(0.3):
            self._set_bridged(True)
        elif self._usb_port is None:
            self._sock.close()
            self._sock = None
            raise ConnectionError("Kmopoflight bridge is not answering and no USB port was given")
        else:
            super().connect(self._usb_port, on_response)
            self._notify()
        self._mon_running = True
        self._mon_thread = threading.Thread(target=self._monitor, name="kmopo-bridge-monitor", daemon=True)
        self._mon_thread.start()

    def disconnect(self) -> None:
        self._mon_running = False
        if self._mon_thread is not None:
            self._mon_thread.join(timeout=2)
            self._mon_thread = None
        with self._lock:
            self._bridged = False
            if self._sock is not None:
                self._sock.close()
                self._sock = None
            super().disconnect()

    # ── commands ───────────────────────────────────────────────────────
    def _send(self, data: str) -> None:
        if self._bridged and self._sock is not None:
            try:
                self._sock.sendto(data.encode("ascii"), BRIDGE_ADDR)
                log.debug("TX(bridge): %r", data)
            except OSError:
                log.exception("bridge send failed")
            return
        super()._send(data)

    # ── link monitor ───────────────────────────────────────────────────
    def _ping_wait(self, timeout_s: float) -> bool:
        """Send one PING and drain replies for *timeout_s*; True on PONG."""
        self._sock.sendto(b"#PING", BRIDGE_ADDR)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self._recv_one():
                return True
        return False

    def _recv_one(self) -> bool:
        """Receive one datagram; deliver lines to on_response. True if it was a PONG."""
        try:
            data, addr = self._sock.recvfrom(_MAX_DATAGRAM)
        except socket.timeout:
            return False
        except OSError:
            return False
        if addr[0] != BRIDGE_ADDR[0]:
            return False
        if data == b"#PONG":
            self._last_pong = time.monotonic()
            return True
        text = data.decode("ascii", errors="replace").strip()
        if text and self._bridged and self._on_response:
            log.debug("RX(bridge): %s", text)
            self._on_response(text)
        return False

    def _monitor(self) -> None:
        next_ping = 0.0
        while self._mon_running:
            now = time.monotonic()
            if now >= next_ping:
                next_ping = now + PING_S
                try:
                    self._sock.sendto(b"#PING", BRIDGE_ADDR)
                except OSError:
                    pass
                if not self._bridged and self._usb_port and not super().connected:
                    self._open_usb()   # USB open failed earlier (unplugged?) -- retry at ping cadence
            got_pong = self._recv_one()   # also delivers relayed lines; blocks <= 0.2 s
            if got_pong and not self._bridged:
                self._set_bridged(True)
            elif self._bridged and time.monotonic() - self._last_pong > LOST_S:
                self._set_bridged(False)

    def _set_bridged(self, on: bool) -> None:
        # Only ever called from connect() or the monitor thread, both of which
        # disconnect() has already stopped/joined before it tears the socket down.
        with self._lock:
            if on:
                if super().connected:
                    super().disconnect()   # release the USB port; the tee would double every line
                self._bridged = True
                self._last_pong = time.monotonic()
                log.info("ClearCore link: via Kmopoflight bridge %s:%d", *BRIDGE_ADDR)
            else:
                self._bridged = False
                log.warning("ClearCore link: Kmopoflight bridge lost")
                if self._usb_port:
                    self._open_usb()
        self._notify()

    def _open_usb(self) -> None:
        # Callers guarantee self.connected is False here, so FocusController.connect
        # does not re-enter our disconnect() (which would join the monitor thread
        # from itself).
        try:
            super().connect(self._usb_port, self._on_response)
            log.info("ClearCore link: USB %s", self._usb_port)
        except Exception as exc:  # pyserial raises SerialException subclasses of IOError
            log.warning("ClearCore USB %s not available: %s", self._usb_port, exc)

    def _notify(self) -> None:
        if self._on_link_changed is not None:
            try:
                self._on_link_changed(self.link)
            except Exception:
                log.exception("link-changed callback failed")
