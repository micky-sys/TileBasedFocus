"""Qt-friendly wrapper around the GNSS receiver's NMEA serial feed.

The receiver is no longer read directly over USB: a bridge takes the
Septentrio's SBF and emits plain NMEA on an RS232 line, so this reads NMEA
and the rest of the app sees only :class:`NmeaEvent` objects. ``sbf_parser``
stays for the offline geotag tools, which still read recorded SBF from past
flights.

Modelled on :mod:`capture.serial_client`: owns a ``pyserial`` handle and a
background reader thread, exposes signals on the GUI thread, and centralises
the wire-format details.

Lifecycle:

* :meth:`list_ports` / :meth:`auto_detect` are class-level helpers used by
  the GUI to fill its COM dropdown and pre-select the most likely port.
* :meth:`connect_port` opens the port at 115200 8N1, spins up the reader
  thread, and feeds raw bytes into a :class:`NmeaParser`. Each decoded
  position sentence triggers ``event_received`` and is forwarded to the
  optional ``on_event`` callable for callers that prefer a non-Qt hook
  (the correlator does).
* :meth:`disconnect_port` joins the reader and closes the handle. The
  ``connection_changed`` signal mirrors the port state for the status
  label.

Note that opening the port proves nothing: the bridge only speaks when an
EXT_EVENT pulse reaches the receiver, so a healthy idle link and a dead one
look identical. The app verifies the link by firing ``$E*`` at the PCB and
waiting for a sentence -- see ``MainWindow._start_gps_link_probe``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal

import serial
import serial.tools.list_ports

from .nmea_parser import NmeaEvent, NmeaParser

log = logging.getLogger(__name__)


BAUD_RATE = 115200

# The SBF-to-NMEA bridge hangs off a physical RS232 port, which Windows
# describes as a generic "Communications Port" -- nothing in the descriptor
# identifies it, so auto-detect cannot sniff it out the way it can a USB
# receiver. On this rig it is COM3, so that is what we try first.
DEFAULT_NMEA_PORT = "COM3"

# Substrings we look for in ``ListPortInfo.description`` / ``.manufacturer``
# during auto-detect. Septentrio receivers expose multiple USB CDC ports
# (one for commands, one for SBF). Either matches.
SEPTENTRIO_ID_HINTS = ("septentrio", "polarx", "mosaic", "asterx", "altus")
# A Septentrio receiver enumerates several virtual USB COM ports; only one
# carries the SBF stream we want. On this rig that is the port whose Windows
# driver description reads "Septentrio Virtual USB COM Port 1", so auto-detect
# prefers it over any other Septentrio-looking port before falling back to a
# generic hint match. Lower-cased substring (see ``auto_detect``).
SEPTENTRIO_PREFERRED_LABEL = "virtual usb com port 1"

# Diagnostic raw-wire tap. When this env var points at a directory, every
# connected receiver tees the raw wire bytes (+ per-chunk arrival timing)
# into that directory in the same three-file layout ``tools/sbf_record.py``
# produces. Unset => zero overhead (no file handles opened).
#
# The env var keeps its original name so existing launch scripts still work,
# but the capture is NMEA text now, not SBF -- it is written as ``.nmea`` and
# the SBF replay tools (``tools/sbf_replay.py``) cannot read it.
SBF_TAP_DIR_ENV = "GPS_SBF_TAP_DIR"


class _RawTap:
    """Append-only raw-wire recorder mirroring ``tools/sbf_record.py``'s layout.

    Writes ``<basename>.nmea`` (raw bytes) and ``<basename>.timing.jsonl``
    (one ``{"t_perf_ns", "offset", "n"}`` line per chunk) live, then
    ``<basename>.meta.json`` on close. All writes happen on the receiver's
    reader thread except :meth:`close`, which the GUI thread calls after
    the reader has been joined -- so no locking is needed.
    """

    def __init__(self, tap_dir: str, port: str, baud: int) -> None:
        os.makedirs(tap_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        safe_port = "".join(c if c.isalnum() else "_" for c in port)
        self._basename = os.path.join(tap_dir, f"nmeatap_{safe_port}_{stamp}")
        self._port = port
        self._baud = baud
        self._t0_perf = time.perf_counter_ns()
        self._t0_wall = time.time()
        self._total = 0
        self._sbf_f = open(self._basename + ".nmea", "wb")
        self._timing_f = open(self._basename + ".timing.jsonl", "w")
        log.info("[gps] raw-NMEA tap -> %s.nmea", self._basename)

    @property
    def basename(self) -> str:
        return self._basename

    def write(self, chunk: bytes) -> None:
        t = time.perf_counter_ns() - self._t0_perf
        try:
            self._sbf_f.write(chunk)
            self._timing_f.write(
                json.dumps({"t_perf_ns": t, "offset": self._total, "n": len(chunk)})
                + "\n"
            )
        except Exception:
            log.exception("[gps] raw-NMEA tap write failed; disabling tap")
            self._safe_close_streams()
            raise
        self._total += len(chunk)

    def _safe_close_streams(self) -> None:
        for f in (self._sbf_f, self._timing_f):
            try:
                f.close()
            except Exception:
                pass

    def close(self, parser_stats: Optional[dict] = None) -> None:
        self._safe_close_streams()
        meta = {
            "port": self._port,
            "baud": self._baud,
            "started_iso_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._t0_wall)
            ),
            "total_bytes": self._total,
            "parser_stats": parser_stats or {},
        }
        try:
            with open(self._basename + ".meta.json", "w") as f:
                json.dump(meta, f, indent=2)
        except Exception:
            log.exception("[gps] raw-NMEA tap meta write failed")
        log.info(
            "[gps] raw-NMEA tap closed: %d bytes -> %s.nmea",
            self._total, self._basename,
        )


class GpsReceiver(QObject):
    """QObject facade over a pyserial-based NMEA feed.

    Emits ``event_received(NmeaEvent, host_perf_ns)`` for every decoded
    position sentence. ``host_perf_ns`` is captured the moment the sentence
    was finalised by the parser, so the correlator can match it against the
    camera frame's host arrival time on the same monotonic clock.
    """

    event_received = Signal(object, "qint64")  # NmeaEvent, host_perf_ns
    connection_changed = Signal(bool)
    parser_stats = Signal(dict)
    error_logged = Signal(str)

    # Reader-loop timing
    _SERIAL_TIMEOUT_S = 0.1
    _STATS_INTERVAL_S = 5.0

    def __init__(
        self,
        *,
        on_event: Optional[Callable[[NmeaEvent, int], None]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._ser: Optional[serial.Serial] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._on_event = on_event
        self._parser = NmeaParser()
        self._port_name: Optional[str] = None
        # Stats throttle so the GUI log doesn't get spammed; emit one
        # parser_stats every _STATS_INTERVAL_S.
        self._last_stats_t = 0.0
        # Optional diagnostic raw-wire tap (see SBF_TAP_DIR_ENV). Created
        # in connect_port if the env var is set; written on the reader
        # thread; closed on disconnect.
        self._tap: Optional[_RawTap] = None

    # ── port enumeration ────────────────────────────────────────────

    @staticmethod
    def list_ports() -> list[str]:
        return [p.device for p in serial.tools.list_ports.comports()]

    @staticmethod
    def auto_detect() -> Optional[str]:
        """Return the most likely GNSS COM port, or None.

        We match on the ``description`` and ``manufacturer`` strings only;
        opening every port to probe it would block the GUI thread for several
        hundred ms per port and conflict with whatever else might be listening
        (e.g. ClearCore on the same machine).

        Selection order:
          1. ``DEFAULT_NMEA_PORT`` (COM3) when present -- the RS232 bridge,
             which no descriptor can identify.
          2. The preferred Septentrio port, whose label matches
             ``SEPTENTRIO_PREFERRED_LABEL`` ("Septentrio Virtual USB COM Port 1").
          3. Fallback: the first Septentrio-looking port by generic hint.

        Returning a port means only "worth opening" -- the link itself is
        proven later by the ``$E*`` probe, not here.
        """
        ports = list(serial.tools.list_ports.comports())

        def _blob(p) -> str:
            return " ".join(
                str(x or "") for x in (p.description, p.manufacturer, p.product)
            ).lower()

        # 1. The RS232 NMEA bridge, which advertises nothing useful.
        for p in ports:
            if p.device.upper() == DEFAULT_NMEA_PORT.upper():
                log.info("GPS auto-detect (default NMEA bridge port): %s (%s)",
                         p.device, p.description)
                return p.device

        # 2. Preferred: the specific Septentrio virtual COM port.
        for p in ports:
            blob = _blob(p)
            if SEPTENTRIO_PREFERRED_LABEL in blob and any(
                h in blob for h in SEPTENTRIO_ID_HINTS
            ):
                log.info(
                    "GPS auto-detect (preferred SBF port): %s (%s | %s)",
                    p.device, p.description, p.manufacturer,
                )
                return p.device

        # 3. Fallback: any Septentrio-looking device (first match).
        for p in ports:
            blob = _blob(p)
            for hint in SEPTENTRIO_ID_HINTS:
                if hint in blob:
                    log.info(
                        "GPS auto-detect (fallback hint '%s'): %s (%s | %s)",
                        hint, p.device, p.description, p.manufacturer,
                    )
                    return p.device
        return None

    # ── connection ──────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    @property
    def port(self) -> Optional[str]:
        return self._port_name

    def connect_port(self, port: str) -> None:
        """Open *port* at 115200 8N1 and start the reader thread.

        Raises ``serial.SerialException`` (or its subclasses) if the
        port can't be opened; the caller is expected to surface the
        error to the GUI log. Idempotent on the same port: a second
        ``connect_port`` while already connected to that port returns
        without action.
        """
        if self.connected and self._port_name == port:
            return
        if self.connected:
            self.disconnect_port()
        self._ser = serial.Serial(
            port=port,
            baudrate=BAUD_RATE,
            bytesize=8,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=self._SERIAL_TIMEOUT_S,
        )
        self._port_name = port
        self._running = True
        self._parser = NmeaParser()
        # Open the diagnostic tap BEFORE starting the reader so the very
        # first chunk is captured. A tap-open failure must not stop the
        # receiver from running -- log and continue without a tap.
        tap_dir = os.environ.get(SBF_TAP_DIR_ENV, "").strip()
        self._tap = None
        if tap_dir:
            try:
                self._tap = _RawTap(tap_dir, port, BAUD_RATE)
            except Exception:
                log.exception("[gps] failed to open raw-NMEA tap in %r", tap_dir)
                self._tap = None
        self._thread = threading.Thread(
            target=self._read_loop, name=f"nmea-reader[{port}]", daemon=True,
        )
        self._thread.start()
        log.info("GPS connected on %s @ %d baud", port, BAUD_RATE)
        self.connection_changed.emit(True)

    def disconnect_port(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._ser is not None:
            try:
                self._ser.close()
            except Exception:
                log.warning("GPS serial close raised", exc_info=True)
            self._ser = None
        # Reader is joined by now, so closing the tap from this (GUI)
        # thread races with nothing.
        if self._tap is not None:
            try:
                self._tap.close(self._parser.stats())
            except Exception:
                log.exception("[gps] raw-NMEA tap close raised")
            self._tap = None
        prev = self._port_name
        self._port_name = None
        log.info("GPS disconnected from %s", prev or "<no port>")
        self.connection_changed.emit(False)

    # ── reader thread ───────────────────────────────────────────────

    def _read_loop(self) -> None:
        """Pump raw bytes from pyserial into the parser.

        Reads in chunks (whatever ``read(4096)`` returns within the
        100 ms timeout), feeds them to the parser, and dispatches every
        decoded event. ``read`` returns an empty bytes object on timeout
        which we use to flush stats periodically.
        """
        ser = self._ser
        assert ser is not None
        log.info("[gps] reader thread starting (port=%s)", self._port_name)
        try:
            while self._running:
                try:
                    data = ser.read(4096)
                except (serial.SerialException, OSError) as exc:
                    if self._running:
                        msg = f"GPS read error: {exc}"
                        log.warning(msg)
                        self.error_logged.emit(msg)
                    break
                if data:
                    # Tee raw bytes to the diagnostic tap first, so the
                    # capture reflects exactly what hit the parser. A tap
                    # write failure disables the tap but never the feed.
                    if self._tap is not None:
                        try:
                            self._tap.write(data)
                        except Exception:
                            self._tap = None
                    try:
                        events = self._parser.feed(data)
                    except Exception:
                        log.exception("[gps] parser raised while feeding %d bytes",
                                      len(data))
                        events = []
                    if events:
                        now_ns = time.perf_counter_ns()
                        for evt in events:
                            # Forward to the non-Qt hook FIRST (the correlator
                            # consumes synchronously and we want the worker
                            # thread to see new events before the GUI signal
                            # round-trips).
                            if self._on_event is not None:
                                try:
                                    self._on_event(evt, now_ns)
                                except Exception:
                                    log.exception("[gps] on_event hook raised")
                            self.event_received.emit(evt, int(now_ns))
                now = time.perf_counter()
                if now - self._last_stats_t >= self._STATS_INTERVAL_S:
                    self._last_stats_t = now
                    try:
                        self.parser_stats.emit(self._parser.stats())
                    except Exception:
                        log.debug("[gps] parser_stats emit raised", exc_info=True)
        finally:
            log.info("[gps] reader thread exiting")

    # ── on_event override ───────────────────────────────────────────

    def set_event_callback(
        self, on_event: Optional[Callable[[NmeaEvent, int], None]]
    ) -> None:
        """Install/replace the synchronous worker-thread event callback.

        The reader thread invokes this BEFORE emitting the Qt signal so the
        correlator can ingest the new event with the same monotonic
        ``host_perf_ns`` we tag it with. Safe to call while the reader is
        running -- assignments to a single attribute on a CPython object are
        atomic.
        """
        self._on_event = on_event

    # ── diagnostics ─────────────────────────────────────────────────

    def parse_stats(self) -> dict:
        """Snapshot the underlying parser's counters (last seen so far)."""
        return self._parser.stats()
