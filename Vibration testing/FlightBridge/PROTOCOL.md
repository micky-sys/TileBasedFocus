# X-Parallax serial protocol v1

Transport: full-duplex serial UART, default 115200 baud, 8N1, no flow control.
The PC sends NMEA and commands. The Teensy sends command replies and optional state
events. All control messages use ASCII and terminate with CRLF. Receiver limits:
1024 bytes per NMEA line, 240-byte control payload. Never embed CR/LF in a field.

## Framing

NMEA begins with `$` or `!`. Preserve its own checksum. No GPS messages beginning
with `@` are forwarded from the source to Teensy, preventing accidental command injection.

Control frame:

```text
@XP,1,<fields>*HH\r\n
```

Here `\r\n` means actual CR and LF bytes. `HH` is the two-digit hexadecimal XOR of
all ASCII bytes after `@` and before `*`, including commas. The checksum detects
accidental corruption; it is not an authentication mechanism. Field content is
printable ASCII without commas, `@`, `*`, or whitespace. Use underscores in detail text.

Command fields:

```text
C,<request_id>,<command>
```

The PC creates a fresh 12-character lowercase hexadecimal request ID. There is at
most one outstanding command. Commands:

- `ARM`: from IDLE, prepare repeated hardware-triggered captures. Reply `ARMED` only
  after buffers/files/acquisition are ready. Do not wait for a trigger to acknowledge.
  Trigger and strobe timing is handled by the MCU, not by the PC application.
- `START`: from IDLE, begin continuous acquisition. Reply `RECORDING` only after it
  has actually started. Reject a mode change while already ARMED or RECORDING.
- `STOP`: stop/disarm either mode, finish pending storage operations, then reply IDLE.
  STOP while IDLE is a successful no-op. Do not claim success if committing data fails.
- `STATUS`: return current actual recorder state without changing it.
- `HEALTH`: check IMU communication/identities, SD availability and logger errors.
  Return ERR with a reason if not ready. Checking UART communication alone is not a
  complete health test. HEALTH must not start, stop or disarm the recorder.
- `SPITEST` (firmware 1.1.0+): IDLE only; immediately reply
  `OK,TESTING,SPI_TEST_STARTED`, then run the asynchronous four-sensor diagnostic.
  Reject START, ARM and HEALTH while testing. STOP replies
  `OK,TESTING,SPI_TEST_STOPPING`, requests cancellation, and returns to IDLE only
  after cleanup. Cleanup failure latches ERROR/SPI_RESTORE until reset.
- `SPITESTRESULT` (firmware 1.1.0+): acknowledge `SPI_RESULTS`, then emit the latest
  16 result rows and summary with ID `0`. Before any test return `NO_SPI_RESULTS`.
  Rejected while ARMED/RECORDING. While testing, rows are a live, non-atomic view;
  use the final report for acceptance. Use a new request ID to retrieve again.

Reply fields:

```text
R,<request_id>,<OK_or_ERR>,<state>,<detail>
```

States: `IDLE`, `ARMED`, `RECORDING`, `TESTING`, `UNKNOWN`, `ERROR`. The same request ID must be
echoed exactly. `detail` is a short machine-readable code, e.g. `READY`, `STARTED`,
`STOPPED`, `SD_MISSING`, `IMU3_NO_RESPONSE`, `INVALID_STATE`.

Firmware must implement ARM/START/STOP idempotently and cache recent replies by
request ID. Repeating an ID returns the cached result without repeating an operation.
The PC does not automatically retry. IDs are different after reconnects; querying
actual status is the recovery mechanism for uncertain outcomes.

Optional unsolicited state reply uses ID `0`. At boot, send `R,0,OK,IDLE,BOOT` (or
the true startup state). The app cancels any pending command on BOOT. Other state
events update the displayed state and appear in the log. For example, after a
triggered burst is safely stored, `R,0,OK,ARMED,CAPTURE_DONE`. An acquisition/storage
failure should report `R,0,ERR,ERROR,<reason>`; never report success for lost samples.

Frames above are payload examples; always add the prefix, checksum, and terminator.
Python's `protocol.control_frame()` and the sketch's `reply()` implement that framing.
The host ignores corrupt, stale, wrong-ID and unsupported-version command replies.

## Acquisition responsibilities

Triggered mode should maintain a pre-trigger ring buffer and retain sufficient
post-trigger samples to cover every camera strobe and filter delay. Preserve the
hardware trigger and both edges of each strobe with sensor timing information.
Return to ARMED after a burst; record/report overlapping triggers or capture overrun
explicitly. A camera trigger is NOT a UART `START` command.

Continuous mode records uniformly timed samples and camera-event markers until STOP.
The firmware must service sensor FIFOs throughout; a short FIFO cannot hold a
multi-second continuous recording while waiting for later readout. Record the sample
rate, range, filter configuration and any data losses for later PSD analysis.

The PC currently selects these modes but does not configure sample rate or capture
window. Those settings must be defined with the actual acquisition firmware. Do not
silently interpret an unsupported command as another mode.

## Firmware implementation

The full implementation is `../firmware/VibrationLogger/VibrationLogger.ino`.
It supports this protocol, both capture modes, SD storage, camera edge timestamps
and NMEA recording. See `../firmware/README.md` for verified pin mapping, default
settings, timing limitations, error handling and the required hardware bench test.
Diagnostic message details and examples are in `../firmware/SPI_TEST.md`.

## Legacy protocol-only sketch limitations

The sketch parses the protocol and counts received NMEA. It does not save NMEA or
sensor data, check sensor/SD health, configure IMUs, or capture GPIO timing. Its
ARM/START/HEALTH hooks deliberately fail with `LOGGER_NOT_IMPLEMENTED`. Replace the
hooks, including `recorderState()`, as part of building the actual logger firmware.
The sketch has not been built with the Teensy toolchain or tested on hardware.
