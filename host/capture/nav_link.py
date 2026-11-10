"""UDP receiver for Kmopoflight navigation events (X-Parallax mode).

Kmopoflight sends one JSON object per datagram to 127.0.0.1:56610::

    {"type": "project"|"line_start"|"click"|"line_end", "project": str, "line": int,
     "seq": int, "lat": float, "lon": float, "alt": float, "utc": str|null}

``project`` (sent when Kmopoflight opens a project: the archive name without
suffix) sets the project name; ``line_start`` sets project + line number; the
others are logged so the session log shows the navigator's view of the line.
Every field is validated before use -- the datagram is network input even
though it normally comes from the same machine.
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from .file_naming import sanitize_project_name

log = logging.getLogger(__name__)

DEFAULT_PORT = 56610
MAX_DATAGRAM = 2048
_TYPES = frozenset({"project", "line_start", "click", "line_end"})
_LINE_RANGE = (-9999, 9999)  # matches the Line spinbox range in app.py


def parse_nav_event(payload: bytes) -> dict | None:
    """Validate a datagram; return a clean dict or None (with a log line)."""
    if len(payload) > MAX_DATAGRAM:
        log.warning("nav_link: datagram too large (%d bytes)", len(payload))
        return None
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        log.warning("nav_link: bad JSON: %s", exc)
        return None
    if not isinstance(obj, dict) or obj.get("type") not in _TYPES:
        log.warning("nav_link: unknown event %r", obj if isinstance(obj, dict) else type(obj))
        return None
    line = obj.get("line")
    if isinstance(line, bool) or not isinstance(line, int) or not _LINE_RANGE[0] <= line <= _LINE_RANGE[1]:
        log.warning("nav_link: bad line %r", line)
        return None
    try:
        project = sanitize_project_name(str(obj.get("project", "")))
    except ValueError as exc:
        log.warning("nav_link: bad project: %s", exc)
        return None
    seq = obj.get("seq", 0)
    return {
        "type": obj["type"],
        "project": project,
        "line": line,
        "seq": seq if isinstance(seq, int) and not isinstance(seq, bool) else 0,
    }


class NavLink(QObject):
    """Bind a loopback UDP port and emit validated navigator events."""

    event = Signal(dict)

    def __init__(self, port: int = DEFAULT_PORT, parent=None, log_fn: Callable[[str], None] | None = None) -> None:
        super().__init__(parent)
        self._log = log_fn or (lambda m: log.info("%s", m))
        self._sock = QUdpSocket(self)
        self._sock.readyRead.connect(self._on_ready)
        if self._sock.bind(QHostAddress.LocalHost, port):
            self._log(f"nav link: listening for Kmopoflight on 127.0.0.1:{port}")
        else:
            self._log(f"nav link: could not bind UDP {port}: {self._sock.errorString()}")

    def _on_ready(self) -> None:
        while self._sock.hasPendingDatagrams():
            dg = self._sock.receiveDatagram(MAX_DATAGRAM + 1)
            if not dg.senderAddress().isLoopback():
                self._log("nav link: ignored datagram from non-loopback sender")
                continue
            ev = parse_nav_event(bytes(dg.data()))
            if ev is not None:
                self.event.emit(ev)

    def close(self) -> None:
        self._sock.close()
