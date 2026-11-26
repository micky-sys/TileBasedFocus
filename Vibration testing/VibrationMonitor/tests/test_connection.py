import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from connection import SerialSession
from test_monitor import fixture, frame


class FakeSerial:
    def __init__(self, data):
        self.data = bytearray(data)
        self.writes = []
        self.dtr = False
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    @property
    def in_waiting(self):
        return min(73, len(self.data))
    def read(self, n):
        if not self.data:
            raise OSError("Test exhausted bytes without completing")
        data = bytes(self.data[:n]);del self.data[:n]
        return data
    def write(self, data):
        self.writes.append(data)
        return len(data)


class ConnectionTests(unittest.TestCase):
    def test_worker_full_receive_save_ack(self):
        app = QCoreApplication.instance() or QCoreApplication([])
        del app  # signal connections below are direct in this synchronous run
        payload = fixture()
        device = FakeSerial(b"DualCsvLogger 2.0\r\n" + frame(payload))
        with tempfile.TemporaryDirectory() as folder:
            worker = SerialSession("TEST_ONLY", folder)
            events = []
            def event(kind, value):
                events.append((kind, value))
                if kind == "saved":
                    worker.stop()
            worker.event.connect(event)
            with patch("connection.serial.Serial", return_value=device) as constructor:
                worker.run()
                constructor.assert_called_once_with("TEST_ONLY", 115200, timeout=0.1, write_timeout=2)
            self.assertTrue(device.dtr)
            self.assertEqual(next(Path(folder).glob("*.csv")).read_bytes(), payload)
            self.assertEqual(len([w for w in device.writes if w.startswith(b"ACK_LOG ")]), 1)
            self.assertIn("saved", [k for k, _ in events])
            self.assertNotIn("error", [k for k, _ in events])

    def test_commands_are_bounded_to_supported_protocol(self):
        worker = SerialSession("TEST_ONLY", ".")
        with self.assertRaises(ValueError):
            worker.command("ARBITRARY")
        worker.command("NEW")
        self.assertEqual(worker.commands.get_nowait(), b"NEW\n")
