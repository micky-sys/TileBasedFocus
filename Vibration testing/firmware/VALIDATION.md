# Validation — 2026-11-16

## Release 1.1.0 — SPI cable diagnostic

- Compiled and linked the updated firmware for Teensy 4.1, official Teensy core
  1.62.0, 600 MHz / USB Serial. HEX copied to `release/VibrationLogger.ino.hex`.
- Native diagnostic tests passed: four sensors/four rates, known-pattern readback,
  injected bit errors, aliased chip selects, missing devices, read/write guard
  failure, cancellation at multiple execution points, and cleanup failure.
- The device adapter was tested with a fake banked device: pattern writes only
  target bank-4 offsets, invalid bank halts further writes, cleanup verifies zero
  offsets and returns bank/clock to normal even when reporting a failure.
- Existing native queue/parser/window/CRC tests and eight decoder tests passed.
  Parser coverage now includes SPITEST and SPITESTRESULT.
- All 16 FlightBridge protocol/transport tests passed, including SPI test progress,
  STOP, results retrieval, concurrent GPS fanout and unsolicited-result handling.
- Real Tk UI with simulated ports passed: new buttons, TESTING state, blocking
  incompatible commands, STOP and visible event log. Updated EXE built with Tk.

No physical hardware upload, 400 mm cable test, electrical-margin measurement,
acquisition-rate validation or SD endurance run was performed. The diagnostic
uses nine-byte register reads; it does not certify FIFO/SD throughput or detect
every possible corruption of live samples. See `SPI_TEST.md` for the bench steps.

## Original release — 2026-11-08

Release: X-Parallax VibrationLogger 1.0.0.

Completed:

- Compiled and linked for `teensy:avr:teensy41` using official Teensy 1.62.0,
  default 600 MHz and USB Serial configuration. Bundled SPI 1.0 and SdFat 2.1.2.
- Checked GPIO mapping against the 2026-11-03 hub Gerber connectivity extraction.
  SPI1 explicitly selects MISO=39, preserving Serial1 TX=1.
- Native C++ tests exercised valid/malformed command parsing, checksum rejection,
  queue-full behavior, 200,000 concurrent producer/consumer transfers, trigger
  window merging/extension and the standard CRC test vector.
- Eight Python decoder tests passed against a binary fixture emitted by the C++
  record encoder. They cover unit conversion and FSYNC interpolation, partial
  writes, CRC corruption, missing records, invalid lengths, missing sensor samples
  despite valid record CRCs, range/invalid flags and unsynchronized data handling.

Not performed:

- Hardware upload or physical sensor/SD/RS-232/camera integration.
- Real-time ISR execution/SD stall measurements on a Teensy.
- Oscilloscope verification of cable signal integrity or FSYNC capture.
- Calibration of GPIO timestamp latency, sensor/filter delay, axis alignment or bias.
- Optical blur validation or PSD estimation from actual flight data.

These tests establish a buildable implementation and test its portable logic and
file decoding. They do not establish electrical margins, real-world acquisition
throughput or exposure-time accuracy. Follow the README bench acceptance procedure.
