"""Independent bounded serial pipelines; one writer owns the Teensy UART."""
import queue
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from protocol import LineFramer, command_frame, nmea_checksum_status, response_from


@dataclass(frozen=True)
class PortConfig:
    port: str
    baud: int


@dataclass(frozen=True)
class BridgeConfig:
    gps: PortConfig
    nav: PortConfig
    teensy: PortConfig

    def validate(self):
        names = [p.port.strip().upper() for p in (self.gps, self.nav, self.teensy)]
        if any(not name for name in names) or len(set(names)) != 3:
            raise ValueError("Choose three different ports: GPS, navigation and Teensy.")
        if any(not 300 <= p.baud <= 4000000 for p in (self.gps, self.nav, self.teensy)):
            raise ValueError("Baud rates must be between 300 and 4,000,000.")


def open_serial(config: PortConfig):
    import serial
    port = serial.Serial(port=None, baudrate=config.baud, timeout=0.05, write_timeout=0.5,
                         xonxoff=False, rtscts=False, dsrdtr=False)
    # Set requested levels before opening (some USB drivers can still briefly toggle them).
    port.dtr = False
    port.rts = False
    port.port = config.port.strip()
    port.open()
    return port


class Bridge:
    def __init__(self, config: BridgeConfig, opener: Callable = open_serial,
                 queue_size: int = 512, ack_timeout: float = 3.0):
        config.validate()
        self.config, self.opener = config, opener
        self.ack_timeout = ack_timeout
        self.ports = {}
        self.stop_event = threading.Event()
        self.lock = threading.RLock()
        self.threads = []
        self.events = queue.Queue(maxsize=500)
        self.nav_queue = queue.Queue(maxsize=queue_size)
        self.gps_queue = queue.Queue(maxsize=queue_size)
        self.command_queue = queue.Queue(maxsize=4)
        self.pending = None
        self.started = False
        self.metrics = dict(gps_bytes=0, nav_bytes=0, teensy_bytes=0, nmea_lines=0,
                            bad_checksums=0, missing_checksums=0, discarded_lines=0,
                            last_gps=0.0, last_response=0.0, last_line="",
                            recording="UNKNOWN", command="None", notice="", notices=0,
                            gps="Disconnected", nav="Disconnected", teensy="Disconnected")

    def snapshot(self):
        with self.lock:
            value = dict(self.metrics)
            value["pending"] = self.pending is not None
            return value

    def event(self, level, message):
        if level in {"ERROR", "WARNING"}:
            with self.lock:
                self.metrics["notice"] = message
                self.metrics["notices"] += 1
        try:
            self.events.put_nowait((time.time(), level, message))
        except queue.Full:
            # Status/counters retain faults even if the display is slow.
            pass

    def start(self):
        if self.started:
            raise RuntimeError("Create a new bridge to reconnect")
        self.started = True
        try:
            for name in ("nav", "teensy", "gps"):
                self.ports[name] = self.opener(getattr(self.config, name))
                self.metrics[name] = "Connected"
        except Exception:
            for port in self.ports.values():
                port.close()
            self.ports.clear()
            raise
        for name, worker in [("navigation-writer", self._nav_writer),
                             ("teensy-writer", self._teensy_writer),
                             ("teensy-reader", self._teensy_reader),
                             ("gps-reader", self._gps_reader),
                             ("command-watchdog", self._watchdog)]:
            thread = threading.Thread(target=worker, name=name, daemon=True)
            self.threads.append(thread)
            thread.start()
        self.event("INFO", "Ports opened: 8 data bits, no parity, 1 stop bit; no flow control.")
        if self.config.teensy.baud <= self.config.gps.baud:
            self.event("WARNING", "Teensy baud rate has no guaranteed headroom for commands; "
                       "prefer a faster output than GPS input.")

    def stop(self):
        self.stop_event.set()
        for port in self.ports.values():
            try:
                port.close()
            except Exception:
                pass
        for thread in self.threads:
            thread.join(timeout=1)
        with self.lock:
            self.pending = None
            for name in ("gps", "nav", "teensy"):
                self.metrics[name] = "Disconnected"
            self.metrics["recording"] = "UNKNOWN"

    def _alive(self, name):
        return not self.stop_event.is_set() and self.metrics[name] == "Connected"

    def _fail(self, name, error):
        with self.lock:
            if not self._alive(name):
                return
            self.metrics[name] = "FAULT"
            if name == "teensy":
                self.pending = None
                self.metrics["recording"] = "UNKNOWN"
                self.metrics["command"] = "Failed: Teensy link lost"
        self.event("ERROR", f"{name.upper()} stopped: {error}. Other links remain active; reconnect to retry.")
        try:
            self.ports[name].close()
        except Exception:
            pass

    def _enqueue(self, name, target, data):
        if not self._alive(name):
            return
        try:
            target.put_nowait(data)
        except queue.Full:
            self._fail(name, "output queue overflow (data cannot be delivered without loss)")

    def _write(self, name, data):
        # Complete one frame before another writer can send anything.
        offset = 0
        while offset < len(data):
            if not self._alive(name):
                raise OSError("Port closed")
            count = self.ports[name].write(data[offset:])
            if not count:
                raise OSError("Serial write made no progress")
            offset += count
            with self.lock:
                self.metrics[f"{name}_bytes"] += count

    def _gps_reader(self):
        framer = LineFramer()
        try:
            while self._alive("gps"):
                port = self.ports["gps"]
                chunk = port.read(min(max(port.in_waiting, 1), 4096))
                if not chunk:
                    continue
                with self.lock:
                    self.metrics["gps_bytes"] += len(chunk)
                    self.metrics["last_gps"] = time.monotonic()
                # Navigation receives EXACT original bytes, including proprietary lines/terminators.
                self._enqueue("nav", self.nav_queue, chunk)
                for line in framer.feed(chunk):
                    if not line.startswith((b"$", b"!")):
                        with self.lock:
                            self.metrics["discarded_lines"] += 1
                        continue
                    status = nmea_checksum_status(line)
                    with self.lock:
                        self.metrics["nmea_lines"] += 1
                        self.metrics["last_line"] = line.decode("ascii")
                        self.metrics["bad_checksums"] += status == "bad"
                        self.metrics["missing_checksums"] += status == "missing"
                    # Preserve sentence content; canonical terminators permit safe command insertion.
                    self._enqueue("teensy", self.gps_queue, line + b"\r\n")
                if framer.discarded:
                    with self.lock:
                        self.metrics["discarded_lines"] += framer.discarded
                    self.event("WARNING", "Malformed/oversize GPS line omitted from Teensy output.")
                    framer.discarded = 0
        except Exception as error:
            self._fail("gps", error)

    def _nav_writer(self):
        try:
            while self._alive("nav"):
                try:
                    data = self.nav_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                self._write("nav", data)
        except Exception as error:
            self._fail("nav", error)

    def send_command(self, command):
        request_id = uuid.uuid4().hex[:12]
        frame = command_frame(request_id, command)
        with self.lock:
            if not self._alive("teensy"):
                raise RuntimeError("Teensy port is not connected")
            if self.pending:
                raise RuntimeError("Wait for the current command to finish")
            self.pending = dict(id=request_id, command=command, deadline=time.monotonic() + 5,
                                sent=False)
            self.metrics["command"] = f"{command}: queued"
            self.command_queue.put_nowait((request_id, frame))
        self.event("TX", f"{command} [{request_id}]")
        return request_id

    def _teensy_writer(self):
        try:
            while self._alive("teensy"):
                try:
                    request_id, data = self.command_queue.get_nowait()
                except queue.Empty:
                    try:
                        self._write("teensy", self.gps_queue.get(timeout=0.02))
                    except queue.Empty:
                        pass
                    continue
                with self.lock:
                    if not self.pending or self.pending["id"] != request_id:
                        continue
                    # Set before write: a fast peer may reply before write() returns.
                    self.pending["sent"] = True
                    self.pending["deadline"] = time.monotonic() + self.ack_timeout
                    self.metrics["command"] = f"{self.pending['command']}: awaiting acknowledgement"
                self._write("teensy", data)
        except Exception as error:
            self._fail("teensy", error)

    def _teensy_reader(self):
        framer = LineFramer()
        try:
            while self._alive("teensy"):
                port = self.ports["teensy"]
                chunk = port.read(min(max(port.in_waiting, 1), 4096))
                for line in framer.feed(chunk):
                    if not line.startswith(b"@"):
                        self.event("RX", line.decode("ascii"))
                        continue
                    try:
                        response = response_from(line)
                    except ValueError:
                        self.event("WARNING", "Ignored malformed Teensy reply/checksum")
                        continue
                    with self.lock:
                        pending = self.pending
                        if response.request_id == "0":
                            # A boot announcement invalidates any in-flight request.
                            if response.detail == "BOOT":
                                self.pending = None
                                self.metrics["recording"] = response.state
                                self.metrics["command"] = "Device rebooted; previous request cancelled"
                                self.event("WARNING", "Teensy rebooted; verify recorder state")
                            else:
                                self.metrics["recording"] = response.state
                                self.metrics["last_response"] = time.monotonic()
                                self.event("RX", f"Device event: {response.state} / {response.detail}")
                            continue
                        if not pending or response.request_id != pending["id"] or not pending["sent"]:
                            self.event("WARNING", f"Ignored stale/unmatched reply [{response.request_id}]")
                            continue
                        self.pending = None
                        self.metrics["last_response"] = time.monotonic()
                        self.metrics["recording"] = response.state
                        self.metrics["command"] = f"{pending['command']}: {response.result} — {response.detail}"
                    level = "ACK" if response.result == "OK" else "ERROR"
                    self.event(level, f"{pending['command']}: {response.result}; "
                               f"state={response.state}; {response.detail}")
        except Exception as error:
            self._fail("teensy", error)

    def _watchdog(self):
        while not self.stop_event.wait(0.05):
            with self.lock:
                if self.pending and time.monotonic() > self.pending["deadline"]:
                    command = self.pending["command"]
                    self.pending = None
                    self.metrics["recording"] = "UNKNOWN"
                    self.metrics["command"] = f"{command}: timed out — outcome unknown"
                    self.event("ERROR", f"{command}: no acknowledgement; outcome unknown. "
                               "Use Status before retrying. No automatic retry was sent.")
