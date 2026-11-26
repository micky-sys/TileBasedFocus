"""Byte-exact Teensy log receiver. No GUI or serial-port dependencies."""
from __future__ import annotations
import datetime as dt
import os
from pathlib import Path
import re
import uuid
import zlib

BEGIN = re.compile(rb"=== LOG_CSV_BEGIN bytes=(\d+) ===")
END = re.compile(rb"=== LOG_CSV_END bytes=(\d+) crc32=([0-9A-Fa-f]{8}) ===")


class LogReceiver:
    def __init__(self, folder: Path, event, acknowledge):
        self.folder = Path(folder)
        self.event = event
        self.acknowledge = acknowledge
        self.buffer = bytearray()
        self.mode = "lines"
        self.file = None
        self.part = None
        self.expected = self.received = self.crc = 0
        self.unframed_warning = False

    @property
    def busy(self):
        return self.mode != "lines"

    def abort(self, reason):
        if self.file is not None:
            try:
                self.file.close()
            except OSError:
                pass
            self.file = None
        partial = str(self.part) if self.part else None
        self.mode = "lines"
        self.part = None
        self.buffer.clear()
        self.event("error", {"message": reason, "partial": partial})

    def _begin(self, size):
        self.folder.mkdir(parents=True, exist_ok=True)
        name = dt.datetime.now().strftime("vibration_%Y%m%d_%H%M%S_%f_") + uuid.uuid4().hex[:6]
        self.part = self.folder / (name + ".csv.part")
        self.file = self.part.open("xb")
        self.expected, self.received, self.crc = size, 0, 0
        self.mode = "body" if size else "footer"
        self.event("begin", {"bytes": size, "path": str(self.part)})

    def feed(self, data: bytes):
        self.buffer.extend(data)
        try:
            while self.buffer:
                if self.mode == "body":
                    count = min(len(self.buffer), self.expected - self.received)
                    block = bytes(self.buffer[:count])
                    if self.file.write(block) != count:
                        raise OSError("Incomplete disk write")
                    self.crc = zlib.crc32(block, self.crc)
                    self.received += count
                    del self.buffer[:count]
                    if self.received == self.expected:
                        self.mode = "footer"
                    self.event("progress", {"received": self.received, "bytes": self.expected})
                    continue
                newline = self.buffer.find(b"\n")
                if newline < 0:
                    if len(self.buffer) > 8192:
                        raise ValueError("Overlong protocol line; download not acknowledged")
                    break
                line = bytes(self.buffer[:newline]).rstrip(b"\r")
                del self.buffer[:newline+1]
                if self.mode == "footer":
                    if not line:
                        continue
                    match = END.fullmatch(line)
                    if not match:
                        raise ValueError("Missing log end marker; partial file retained")
                    size, expected_crc = int(match[1]), int(match[2], 16)
                    if size != self.expected or self.received != size or expected_crc != self.crc:
                        raise ValueError("Log size/CRC mismatch; no acknowledgement sent; partial file retained")
                    self.file.flush()
                    os.fsync(self.file.fileno())
                    self.file.close()
                    self.file = None
                    final = self.part.with_suffix("")  # remove .part only
                    os.replace(self.part, final)
                    self.part = None
                    self.mode = "lines"
                    # Only after full file, CRC verification, flush/fsync, close and rename.
                    self.acknowledge(f"ACK_LOG {size} {expected_crc:08X}\n".encode("ascii"))
                    self.event("saved", {"path": str(final), "bytes": size, "crc32": f"{expected_crc:08X}"})
                    continue
                match = BEGIN.fullmatch(line)
                if match:
                    self.unframed_warning = False
                    self._begin(int(match[1]))
                elif line[:1].isdigit() and line.count(b",") >= 10:
                    # Joining halfway through a legacy/aborted export must not
                    # flood the GUI with hundreds of thousands of unframed rows.
                    if not self.unframed_warning:
                        self.event("line", "Unframed CSV received: wait for the current export to end, then retry Download. No file acknowledged.")
                        self.unframed_warning = True
                elif line:
                    self.event("line", line.decode("utf-8", errors="replace"))
        except (OSError, ValueError) as exc:
            self.abort(str(exc))

