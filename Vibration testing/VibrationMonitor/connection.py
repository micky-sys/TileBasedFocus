"""Serial I/O lives off the Qt UI thread. Never clears incoming startup bytes."""
from pathlib import Path
import queue
import threading
import time
import serial
from PySide6.QtCore import QThread, Signal
from transfer import LogReceiver


class SerialSession(QThread):
    event = Signal(str, object)

    def __init__(self, port, folder, parent=None):
        super().__init__(parent)
        self.port, self.folder = port, Path(folder)
        self.commands = queue.Queue()
        self.stopping = threading.Event()

    def stop(self):
        self.stopping.set()

    def command(self, text):
        if text not in {"STOP", "DUMP", "NEW", "STATUS", "HELP"}:
            raise ValueError("Unsupported command")
        self.commands.put((text + "\n").encode("ascii"))

    def run(self):
        receiver = None
        try:
            with serial.Serial(self.port, 115200, timeout=0.1, write_timeout=2) as port:
                port.dtr = True
                def send(data):
                    if port.write(data) != len(data):
                        raise OSError("Serial command/acknowledgement write was incomplete")
                # Progress can arrive thousands of times/sec: throttle UI signals.
                last_progress = 0.0
                def report(kind, payload):
                    nonlocal last_progress
                    if kind == "progress":
                        now = time.monotonic()
                        if now - last_progress < 0.1 and payload["received"] < payload["bytes"]:
                            return
                        last_progress = now
                    self.event.emit(kind, payload)
                receiver = LogReceiver(self.folder, report, send)
                self.event.emit("connected", self.port)
                last_data = time.monotonic()
                while not self.stopping.is_set():
                    # UI commands cannot interrupt a byte-counted transfer.
                    if not receiver.busy:
                        while not self.commands.empty():
                            send(self.commands.get_nowait())
                    data = port.read(min(max(port.in_waiting, 1), 65536))
                    if data:
                        last_data = time.monotonic()
                        receiver.feed(data)
                    elif receiver.busy and time.monotonic()-last_data > 20:
                        receiver.abort("Download stalled for 20 seconds; reconnect or retry Download")
        except (serial.SerialException, OSError) as exc:
            self.event.emit("error", {"message": str(exc), "partial": None})
        finally:
            if receiver and receiver.busy:
                receiver.abort("Disconnected during download; incomplete file retained, no acknowledgement")
            self.event.emit("disconnected", self.port)
