"""Line-oriented GPS/control protocol. No dependencies on the GUI or serial driver."""
from dataclasses import dataclass

COMMANDS = {"ARM", "START", "STOP", "STATUS", "HEALTH", "SPITEST", "SPITESTRESULT"}
STATES = {"IDLE", "ARMED", "RECORDING", "TESTING", "UNKNOWN", "ERROR"}


def checksum(payload: bytes) -> int:
    result = 0
    for value in payload:
        result ^= value
    return result


def control_frame(*fields: str) -> bytes:
    if any(not field or any(ord(c) < 33 or ord(c) > 126 or c in ",*@" for c in field)
           for field in fields):
        raise ValueError("Control fields must be printable ASCII without separators")
    payload = ("XP,1," + ",".join(fields)).encode("ascii")
    if len(payload) > 240:
        raise ValueError("Control message too long")
    return b"@" + payload + f"*{checksum(payload):02X}\r\n".encode("ascii")


def parse_control(line: bytes) -> list[str]:
    line = line.rstrip(b"\r\n")
    if not line.startswith(b"@") or len(line) > 246:
        raise ValueError("Invalid control frame")
    try:
        payload, supplied = line[1:].rsplit(b"*", 1)
        if len(supplied) != 2 or int(supplied, 16) != checksum(payload):
            raise ValueError("Invalid checksum")
        fields = payload.decode("ascii").split(",")
    except (UnicodeError, ValueError) as error:
        raise ValueError("Invalid control frame/checksum") from error
    if fields[:2] != ["XP", "1"] or any(not f for f in fields):
        raise ValueError("Unsupported protocol")
    return fields[2:]


def command_frame(request_id: str, command: str) -> bytes:
    if command not in COMMANDS:
        raise ValueError("Unsupported command")
    return control_frame("C", request_id, command)


@dataclass(frozen=True)
class Response:
    request_id: str
    result: str
    state: str
    detail: str


def response_from(line: bytes) -> Response:
    fields = parse_control(line)
    if (len(fields) != 5 or fields[0] != "R" or fields[2] not in {"OK", "ERR"}
            or fields[3] not in STATES):
        raise ValueError("Malformed response")
    return Response(*fields[1:])


class LineFramer:
    """Bounded CR/LF parser. Oversize/binary frames are discarded through the delimiter."""
    def __init__(self, limit: int = 1024):
        self.limit = limit
        self.buffer = bytearray()
        self.discarding = False
        self.discarded = 0

    def feed(self, chunk: bytes) -> list[bytes]:
        result = []
        for value in chunk:
            if value in (10, 13):
                if self.buffer and not self.discarding:
                    result.append(bytes(self.buffer))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                if value < 32 or value > 126 or len(self.buffer) >= self.limit:
                    self.buffer.clear()
                    self.discarding = True
                    self.discarded += 1
                else:
                    self.buffer.append(value)
        return result


def nmea_checksum_status(line: bytes) -> str:
    if b"*" not in line:
        return "missing"
    try:
        payload, supplied = line[1:].rsplit(b"*", 1)
        return "valid" if len(supplied) == 2 and int(supplied, 16) == checksum(payload) else "bad"
    except ValueError:
        return "bad"
