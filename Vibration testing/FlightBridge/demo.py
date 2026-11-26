"""In-memory demonstration: never opens a physical COM port."""
import queue
import threading
import time
from protocol import LineFramer, checksum, control_frame, parse_control


class MemoryPort:
    def __init__(self):
        self.input = queue.Queue()
        self.closed = False
        self.on_write = None

    @property
    def in_waiting(self):
        return self.input.qsize()

    def inject(self, data):
        for value in data:
            self.input.put(value)

    def read(self, count=1):
        if self.closed:
            raise OSError("Closed")
        try:
            return bytes([self.input.get(timeout=0.03)])
        except queue.Empty:
            return b""

    def write(self, data):
        if self.closed:
            raise OSError("Closed")
        if self.on_write:
            self.on_write(bytes(data))
        return len(data)

    def close(self):
        self.closed = True


class Demo:
    def __init__(self):
        self.ports = {name: MemoryPort() for name in ("DEMO_GPS", "DEMO_NAV", "DEMO_TEENSY")}
        self.stop_event = threading.Event()
        self.state = "IDLE"
        self.framer = LineFramer()
        self.ports["DEMO_TEENSY"].on_write = self.receive
        self.thread = threading.Thread(target=self.feed, daemon=True)

    def open(self, config):
        return self.ports[config.port]

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)

    def receive(self, data):
        for line in self.framer.feed(data):
            if not line.startswith(b"@"):
                continue
            fields = parse_control(line)
            if len(fields) != 3 or fields[0] != "C":
                continue
            _, request_id, command = fields
            if command in {"SPITEST", "SPITESTRESULT"}:
                # A PC simulator cannot validate a physical SPI cable.
                self.ports["DEMO_TEENSY"].inject(
                    control_frame("R", request_id, "ERR", self.state, "SIMULATOR_NO_SPI"))
                continue
            if command == "ARM":
                self.state = "ARMED"
            if command == "START":
                self.state = "RECORDING"
            if command == "STOP":
                self.state = "IDLE"
            self.ports["DEMO_TEENSY"].inject(
                control_frame("R", request_id, "OK", self.state, "SIMULATED_" + command))

    def feed(self):
        while not self.stop_event.is_set():
            utc = time.strftime("%H%M%S", time.gmtime())
            date = time.strftime("%d%m%y", time.gmtime())
            for payload in [f"GPGGA,{utc}.00,3200.0000,N,03448.0000,E,1,10,0.9,100.0,M,0.0,M,,",
                            f"GPRMC,{utc}.00,A,3200.0000,N,03448.0000,E,0.0,0.0,{date},,,A"]:
                data = payload.encode("ascii")
                self.ports["DEMO_GPS"].inject(b"$" + data + f"*{checksum(data):02X}\r\n".encode())
            self.stop_event.wait(0.5)
