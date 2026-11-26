import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import threading
import time
import unittest
from bridge import Bridge, BridgeConfig, PortConfig
from demo import MemoryPort
from protocol import LineFramer, command_frame, control_frame, parse_control, response_from


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("Timed out waiting for condition")


class CapturePort(MemoryPort):
    def __init__(self, partial=None):
        super().__init__()
        self.output = bytearray()
        self.partial = partial
        self.fail = False
        self.lock = threading.Lock()

    def write(self, data):
        if self.fail:
            raise OSError("Simulated unplug")
        data = data[:self.partial] if self.partial else data
        count = super().write(data)
        with self.lock:
            self.output.extend(data)
        return count

    def written(self):
        with self.lock:
            return bytes(self.output)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.ports = {name: CapturePort() for name in ("gps", "nav", "teensy")}
        self.config = BridgeConfig(PortConfig("gps", 9600), PortConfig("nav", 9600), PortConfig("teensy", 115200))
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.stop()
            self.assertFalse(any(t.is_alive() for t in bridge.threads))

    def start(self, **kwargs):
        bridge = Bridge(self.config, opener=lambda c: self.ports[c.port], **kwargs)
        self.bridges.append(bridge)
        bridge.start()
        return bridge

    def test_fragmented_raw_navigation_and_normalized_teensy(self):
        bridge = self.start()
        source = b"$GPGGA,test*00\r\n!AIVDM,test\n@XP,untrusted\r\nnoise\n"
        for part in (source[:3], source[3:12], source[12:]):
            self.ports["gps"].inject(part)
        wait_for(lambda: bridge.snapshot()["discarded_lines"] == 2)
        wait_for(lambda: self.ports["nav"].written() == source)
        expected = b"$GPGGA,test*00\r\n!AIVDM,test\r\n"
        wait_for(lambda: self.ports["teensy"].written() == expected)
        self.assertEqual(bridge.snapshot()["bad_checksums"], 1)
        self.assertEqual(bridge.snapshot()["missing_checksums"], 1)

    def test_partial_writes_and_commands_never_split_sentences(self):
        self.ports["teensy"].partial = 3
        bridge = self.start()
        self.ports["gps"].inject(b"$GPRMC,partial")
        wait_for(lambda: bridge.snapshot()["gps_bytes"] == 14)
        request = bridge.send_command("START")
        wait_for(lambda: command_frame(request, "START") in self.ports["teensy"].written())
        self.assertEqual(bridge.snapshot()["recording"], "UNKNOWN")
        self.ports["gps"].inject(b",end\r\n")
        self.ports["teensy"].inject(control_frame("R", request, "OK", "RECORDING", "STARTED"))
        wait_for(lambda: bridge.snapshot()["recording"] == "RECORDING")
        wait_for(lambda: b"$GPRMC,partial,end\r\n" in self.ports["teensy"].written())
        self.assertEqual(self.ports["teensy"].written(), command_frame(request, "START") + b"$GPRMC,partial,end\r\n")

    def test_lost_teensy_does_not_stop_navigation(self):
        bridge = self.start()
        self.ports["teensy"].fail = True
        self.ports["gps"].inject(b"$FIRST\n")
        wait_for(lambda: bridge.snapshot()["teensy"] == "FAULT")
        self.ports["gps"].inject(b"$SECOND\n")
        wait_for(lambda: self.ports["nav"].written() == b"$FIRST\n$SECOND\n")
        self.assertEqual(bridge.snapshot()["gps"], "Connected")

    def test_spi_test_progress_results_stop_and_gps(self):
        bridge = self.start()
        request = bridge.send_command("SPITEST")
        wait_for(lambda: command_frame(request, "SPITEST") in self.ports["teensy"].written())
        self.ports["teensy"].inject(control_frame("R", request, "OK", "TESTING", "SPI_TEST_STARTED"))
        wait_for(lambda: bridge.snapshot()["recording"] == "TESTING" and not bridge.snapshot()["pending"])
        self.ports["gps"].inject(b"$GPGGA,test*00\r\n")
        self.ports["teensy"].inject(control_frame("R", "0", "OK", "TESTING", "SPI_PROGRESS;mhz=4;round=100"))
        wait_for(lambda: b"$GPGGA,test*00\r\n" in self.ports["nav"].written())
        wait_for(lambda: b"$GPGGA,test*00\r\n" in self.ports["teensy"].written())
        stop = bridge.send_command("STOP")
        wait_for(lambda: command_frame(stop, "STOP") in self.ports["teensy"].written())
        self.ports["teensy"].inject(control_frame("R", stop, "OK", "TESTING", "SPI_TEST_STOPPING"))
        wait_for(lambda: not bridge.snapshot()["pending"])
        self.assertEqual(bridge.snapshot()["recording"], "TESTING")
        self.ports["teensy"].inject(control_frame("R", "0", "ERR", "IDLE", "SPI_SUMMARY;result=CANCELLED"))
        wait_for(lambda: bridge.snapshot()["recording"] == "IDLE")
        query = bridge.send_command("SPITESTRESULT")
        wait_for(lambda: command_frame(query, "SPITESTRESULT") in self.ports["teensy"].written())
        self.ports["teensy"].inject(control_frame("R", "0", "OK", "IDLE", "SPI_ROW;mhz=4;s=1;result=PARTIAL"))
        # Unsolicited rows never acknowledge the outstanding query.
        time.sleep(0.03)
        self.assertTrue(bridge.snapshot()["pending"])
        self.ports["teensy"].inject(control_frame("R", query, "OK", "IDLE", "SPI_RESULTS"))
        wait_for(lambda: not bridge.snapshot()["pending"])

    def test_lost_navigation_does_not_stop_teensy(self):
        bridge = self.start()
        self.ports["nav"].fail = True
        self.ports["gps"].inject(b"$FIRST\n$SECOND\n")
        wait_for(lambda: bridge.snapshot()["nav"] == "FAULT")
        wait_for(lambda: self.ports["teensy"].written() == b"$FIRST\r\n$SECOND\r\n")

    def test_timeout_and_late_ack_do_not_report_success(self):
        bridge = self.start(ack_timeout=0.10)
        request = bridge.send_command("START")
        wait_for(lambda: "timed out" in bridge.snapshot()["command"])
        self.ports["teensy"].inject(control_frame("R", request, "OK", "RECORDING", "STARTED"))
        wait_for(lambda: "stale" in bridge.snapshot()["notice"])
        self.assertEqual(bridge.snapshot()["recording"], "UNKNOWN")
        self.assertEqual(self.ports["teensy"].written().count(b"START"), 1)

    def test_bad_ack_checksum_is_ignored(self):
        bridge = self.start()
        request = bridge.send_command("STATUS")
        wait_for(lambda: b"STATUS" in self.ports["teensy"].written())
        response = control_frame("R", request, "OK", "IDLE", "READY")
        corrupt = response.replace(b"READY", b"READX")
        self.ports["teensy"].inject(corrupt)
        wait_for(lambda: "checksum" in bridge.snapshot()["notice"])
        self.assertTrue(bridge.snapshot()["pending"])
        self.ports["teensy"].inject(response)
        wait_for(lambda: bridge.snapshot()["recording"] == "IDLE")

    def test_device_boot_cancels_pending_command(self):
        bridge = self.start()
        bridge.send_command("START")
        self.ports["teensy"].inject(control_frame("R", "0", "OK", "IDLE", "BOOT"))
        wait_for(lambda: "rebooted" in bridge.snapshot()["command"])
        self.assertFalse(bridge.snapshot()["pending"])
        self.assertEqual(bridge.snapshot()["recording"], "IDLE")

    def test_one_pending_command(self):
        bridge = self.start()
        bridge.send_command("STATUS")
        with self.assertRaises(RuntimeError):
            bridge.send_command("START")

    def test_arm_ack_and_capture_event_keep_trigger_mode_armed(self):
        bridge = self.start()
        request = bridge.send_command("ARM")
        wait_for(lambda: b"ARM" in self.ports["teensy"].written())
        self.ports["teensy"].inject(control_frame("R", request, "OK", "ARMED", "READY"))
        wait_for(lambda: bridge.snapshot()["recording"] == "ARMED")
        self.ports["teensy"].inject(control_frame("R", "0", "OK", "ARMED", "CAPTURE_DONE"))
        time.sleep(0.05)
        self.assertEqual(bridge.snapshot()["recording"], "ARMED")
        request = bridge.send_command("STOP")
        wait_for(lambda: b"STOP" in self.ports["teensy"].written())
        self.ports["teensy"].inject(control_frame("R", request, "OK", "IDLE", "DISARMED"))
        wait_for(lambda: bridge.snapshot()["recording"] == "IDLE")

    def test_startup_failure_closes_previously_opened_ports(self):
        def opener(config):
            if config.port == "gps":
                raise OSError("Port in use")
            return self.ports[config.port]
        bridge = Bridge(self.config, opener=opener)
        with self.assertRaises(OSError):
            bridge.start()
        self.assertTrue(self.ports["nav"].closed)
        self.assertTrue(self.ports["teensy"].closed)

    def test_overflow_explicitly_faults_only_affected_output(self):
        gate = threading.Event()
        entered = threading.Event()
        old_write = self.ports["teensy"].write
        def blocked(data):
            entered.set()
            gate.wait(1)
            return old_write(data)
        self.ports["teensy"].write = blocked
        bridge = self.start(queue_size=2)
        try:
            self.ports["gps"].inject(b"$FIRST\n")
            self.assertTrue(entered.wait(1))
            # Give the independent nav writer time to consume its tiny queue.
            for i in range(5):
                self.ports["gps"].inject(f"$LINE{i}\n".encode())
                time.sleep(0.02)
            wait_for(lambda: bridge.snapshot()["teensy"] == "FAULT")
            self.assertIn("overflow", bridge.snapshot()["notice"])
            self.assertEqual(bridge.snapshot()["gps"], "Connected")
        finally:
            gate.set()


class ProtocolTests(unittest.TestCase):
    def test_command_checksum_vector(self):
        self.assertEqual(parse_control(command_frame("abc123", "HEALTH")), ["C", "abc123", "HEALTH"])
        self.assertEqual(response_from(control_frame("R", "abc123", "ERR", "IDLE", "NO_SD")).result, "ERR")

    def test_framer_handles_cr_lf_binary_and_oversize(self):
        f = LineFramer(limit=5)
        self.assertEqual(f.feed(b"$A\r\n$B\n$C\r"), [b"$A", b"$B", b"$C"])
        self.assertEqual(f.feed(b"123456789\nX\x00Y\n$OK\n"), [b"$OK"])
        self.assertEqual(f.discarded, 2)

    def test_duplicate_ports_rejected(self):
        with self.assertRaises(ValueError):
            BridgeConfig(PortConfig("COM1", 9600), PortConfig("com1", 9600), PortConfig("COM3", 9600)).validate()

    def test_invalid_commands_and_fields(self):
        with self.assertRaises(ValueError):
            command_frame("id", "ERASE")
        with self.assertRaises(ValueError):
            control_frame("C", "id\n", "START")
        with self.assertRaises(ValueError):
            response_from(control_frame("R", "id", "OK", "WRONG", "TEST"))


if __name__ == "__main__":
    unittest.main()
