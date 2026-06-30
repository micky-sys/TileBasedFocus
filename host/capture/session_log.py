"""Human-readable per-session log file for the Iron 661 capture app.

This is intentionally separate from the Python :mod:`logging` machinery
the app already uses for stderr. The goal here is a single plain-text
file that an operator (not a developer) can open after a flight and read
top-to-bottom to reconstruct what happened:

* the session start time (date + ``HH:MM:SS``),
* every line that scrolled past the on-screen log window,
* every button the operator pressed,
* every error/question dialog that popped up,
* a periodic snapshot of the GPS position decoded off the COM port,
* a per-line summary (acquired / written / dropped images per camera).

Design notes
------------

* **Thread-safe**: :meth:`SessionLogger.write` takes a lock and flushes
  on every line so a crash mid-session still leaves a readable tail.
* **Never fatal**: every method swallows I/O errors. A logging hiccup
  must never take down the capture app, so failures are best-effort and
  reported once to stderr.
* **Writable location**: frozen builds often live under ``Program Files``
  where the working directory is not writable, so :func:`default_log_dir`
  walks a list of candidate directories and picks the first one it can
  actually create a file in.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
import threading
from typing import Optional

# Subfolder under the app directory (or fallback base) where session logs
# are written. Created on first use if it does not exist.
SESSION_LOGS_FOLDER = "Session logs"


def app_dir() -> Optional[str]:
    """Directory the app runs from.

    For a frozen PyInstaller build that's the folder holding the
    ``.exe``; for a ``py -3 host/main_capture.py`` dev run it's the
    repo's ``host/`` directory (the closest equivalent to "where the
    app lives"). Returns ``None`` if it can't be determined.
    """
    try:
        if getattr(sys, "frozen", False):
            return os.path.dirname(os.path.abspath(sys.executable))
        # .../host/capture/session_log.py -> .../host
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        return None


def build_date(fmt: str = "%Y-%m-%d %H:%M:%S") -> Optional[str]:
    """Best-effort build date of the running app.

    For a frozen PyInstaller build this is the modification time of the
    ``.exe`` (set when the bundle was produced). For a dev run we use the
    entry script (``main_capture.py``) as a stand-in so the date is still
    meaningful. Returns the date formatted per ``fmt`` (default includes
    the time), or ``None`` if it can't be resolved.
    """
    try:
        if getattr(sys, "frozen", False):
            target = os.path.abspath(sys.executable)
        else:
            host = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            entry = os.path.join(host, "main_capture.py")
            target = entry if os.path.exists(entry) else os.path.abspath(__file__)
        mtime = os.path.getmtime(target)
        return _dt.datetime.fromtimestamp(mtime).strftime(fmt)
    except Exception:
        return None


def _session_logs_subdir(base: str) -> str:
    """``<base>/Session logs`` — the folder session ``.txt`` files live in."""
    return os.path.join(base, SESSION_LOGS_FOLDER)


def _candidate_log_dirs() -> list[str]:
    """Ordered list of directories to try for the session-log file.

    Session logs are written under a ``Session logs`` subfolder of the
    app directory (next to the EXE when frozen). The folder is created if
    missing. Remaining entries are fallbacks when that path is not
    writable (e.g. an EXE under ``Program Files``).
    """
    candidates: list[str] = []

    # 1. Explicit operator override (still uses ``Session logs`` beneath it).
    env_dir = os.environ.get("IRON661_LOG_DIR", "").strip()
    if env_dir:
        candidates.append(_session_logs_subdir(env_dir))

    # 2. ``<app dir>/Session logs`` (primary).
    base = app_dir()
    if base:
        candidates.append(_session_logs_subdir(base))

    # 3. Per-user app data (always writable on Windows).
    local_appdata = os.environ.get("LOCALAPPDATA", "").strip()
    if local_appdata:
        candidates.append(
            _session_logs_subdir(os.path.join(local_appdata, "Iron661Capture"))
        )

    # 4. Home directory fallback.
    try:
        candidates.append(
            _session_logs_subdir(
                os.path.join(os.path.expanduser("~"), "Iron661Capture")
            )
        )
    except Exception:
        pass

    # 5. Last resort: the OS temp directory.
    candidates.append(
        _session_logs_subdir(os.path.join(tempfile.gettempdir(), "Iron661Capture"))
    )
    return candidates


def default_log_dir() -> str:
    """Return the first candidate directory we can write to (created if missing)."""
    for cand in _candidate_log_dirs():
        try:
            os.makedirs(cand, exist_ok=True)
            probe = os.path.join(cand, ".write_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            return cand
        except Exception:
            continue
    # If literally nothing is writable, fall back to the temp dir path
    # string anyway; the open() in SessionLogger will then fail and be
    # handled gracefully.
    return _session_logs_subdir(
        os.path.join(tempfile.gettempdir(), "Iron661Capture")
    )


class SessionLogger:
    """Append-only, flush-on-write, human-readable session log file.

    One instance per app session. Construction opens the file and writes
    a header containing the session start time; all subsequent activity
    is appended via :meth:`write` (or the convenience helpers).
    """

    def __init__(self, log_dir: Optional[str] = None) -> None:
        self._lock = threading.Lock()
        self._fh = None
        self.path: Optional[str] = None
        self.start_time = _dt.datetime.now()

        directory = log_dir or default_log_dir()
        stamp = self.start_time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(directory, f"Iron661_session_{stamp}.txt")
        try:
            os.makedirs(directory, exist_ok=True)
            # Line-buffered text mode so each line is pushed to the OS
            # promptly; we also explicitly flush in ``write``.
            self._fh = open(path, "a", encoding="utf-8", buffering=1)
            self.path = path
        except Exception as exc:  # pragma: no cover - I/O environment dependent
            print(f"SessionLogger: could not open log file: {exc!r}", file=sys.stderr)
            self._fh = None
            return

        self._write_header()

    # ── internals ────────────────────────────────────────────────────

    def _write_header(self) -> None:
        start = self.start_time
        built = build_date()
        header = (
            "=" * 70 + "\n"
            "Iron 661 Capture - session log\n"
            f"Build date:    {built if built else '(unknown)'}\n"
            f"Session start: {start.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Log file:      {self.path}\n"
            + "=" * 70 + "\n"
        )
        self._raw_write(header)

    def _raw_write(self, text: str) -> None:
        fh = self._fh
        if fh is None:
            return
        try:
            fh.write(text)
            fh.flush()
        except Exception as exc:  # pragma: no cover - I/O environment dependent
            print(f"SessionLogger: write failed: {exc!r}", file=sys.stderr)

    @staticmethod
    def _timestamp() -> str:
        return _dt.datetime.now().strftime("%H:%M:%S.%f")[:-3]

    # ── public API ──────────────────────────────────────────────────

    def write(self, msg: str, *, category: str = "") -> None:
        """Append one timestamped line.

        ``category`` is an optional short tag (e.g. ``BUTTON``, ``ERROR``,
        ``GPS``, ``LINE``) rendered between the timestamp and the message
        so the file is easy to scan / grep.
        """
        with self._lock:
            tag = f"[{category}] " if category else ""
            self._raw_write(f"{self._timestamp()}  {tag}{msg}\n")

    def section(self, title: str) -> None:
        """Append a visual separator line with a title."""
        with self._lock:
            self._raw_write(f"\n----- {title} -----\n")

    def button(self, label: str) -> None:
        self.write(f"pressed: {label}", category="BUTTON")

    def error(self, title: str, body: str = "") -> None:
        text = title if not body else f"{title} -- {body}"
        # Collapse newlines so a multi-line dialog body stays on one line.
        text = " ".join(text.split())
        self.write(text, category="ERROR")

    def gps(self, msg: str) -> None:
        self.write(msg, category="GPS")

    def line_summary(self, msg: str) -> None:
        self.write(msg, category="LINE")

    def close(self) -> None:
        with self._lock:
            if self._fh is None:
                return
            try:
                end = _dt.datetime.now()
                dur = end - self.start_time
                self._raw_write(
                    "=" * 70 + "\n"
                    f"Session end: {end.strftime('%Y-%m-%d %H:%M:%S')} "
                    f"(duration {str(dur).split('.')[0]})\n"
                    + "=" * 70 + "\n"
                )
                self._fh.close()
            except Exception:
                pass
            finally:
                self._fh = None
