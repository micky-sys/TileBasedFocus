# X-Parallax Teensy vibration logger — v1.1.0

Firmware for **Teensy 4.1**, four **ICM-42688-P** boards and the hub Gerber dated
2026-11-03. Built with PJRC Teensy core **1.62.0**, SPI 1.0 and bundled SdFat 2.1.2.
This is a compiled, bench-test candidate. It has **not been tested on the physical
hub, IMUs, cameras or SD card**. No hardware was programmed during development.

## Files and installation

- `VibrationLogger/VibrationLogger.ino` and its headers: full firmware.
- `SPI_TEST.md`: RS-232 cable test instructions, results and limitations.
- `release/VibrationLogger.ino.hex`: compiled Teensy 4.1 image, 600 MHz, USB Serial.
- `tools/decode.py`: standard-library Python decoder, integrity report and CSV export.
- `FORMAT.md`: binary data and time interpretation.
- `tests/`: host tests for queues, protocol parsing, capture windows and decoding.

Install the Teensy board package in Arduino IDE using PJRC's package URL:
`https://www.pjrc.com/teensy/package_teensy_index.json`.
Open `VibrationLogger/VibrationLogger.ino`, select **Teensy 4.1 / 600 MHz / USB Serial**
and upload normally. Libraries are provided by the Teensy package; an unrelated
Library Manager copy of SdFat is not needed. Alternatively load the supplied HEX
with Teensy Loader. Only program the intended Teensy.

For a repeatable command-line build, run `./build.ps1`. It uses an isolated local
toolchain directory. On another machine, run `./build.ps1 -InstallToolchain` once
(requires Arduino CLI and internet access). The script builds and copies the HEX;
it does not upload firmware. `./test.ps1` requires g++ and Python 3.10+.

## Actual hub pin mapping

Numbers below are **Teensy GPIO numbers**, not connector pad numbers.

- `Serial1`: RX **0**, TX **1**, **115200 8N1**. Connected through the hub MAX3232
  to FlightBridge. Incoming GPS NMEA and commands share this UART; replies use TX.
  USB Serial is optional diagnostics and is never required to start the program.
- `SPI`: SCLK **13**, MOSI **11**, MISO **12**.
  Sensor 1/J1: CS **10**, INT1 **2**, FSYNC **6**.
  Sensor 2/J2: CS **9**, INT1 **3**, FSYNC **7**.
- `SPI1`: SCLK **27**, MOSI **26**, **MISO 39** (explicit alternate-pin selection).
  Sensor 3/J3: CS **38**, INT1 **23**, FSYNC **21**.
  Sensor 4/J4: CS **37**, INT1 **22**, FSYNC **32**.
- Trigger/J8: GPIO **14**, from the 3.3 V ClearCore breakout.
- Camera 1 strobe/J7: GPIO **35**, after U2 level conversion.
- Camera 2 strobe/J11: GPIO **34**, after U4 level conversion.
- Camera 3 strobe/J12: GPIO **33**, after U5 level conversion.
- SD uses the Teensy's built-in **SDIO** slot, independent of both SPI buses.

**INT2 wiring is intentionally used as FSYNC output from the Teensy.** The firmware
first configures sensor pin 9 as FSYNC, then drives that wire. Do not attach another
output to these lines. INT1 is configured as a FIFO watermark output; this version
uses timed FIFO polling, so it does not need INT1 GPIO interrupts. GPIO 13 is the
SPI clock: the built-in LED is never used for status blinking.

## Acquisition and modes

Default rate is **16,000 samples/s per sensor**, recording all six axes, temperature
and sensor timestamp in 16-byte FIFO packets. Ranges are **±2000 dps and ±16 g**.
The UI filters are second order, nominal 4 kHz bandwidth. Factory notch/anti-alias
filter settings remain enabled and their actual register bytes are recorded.
These settings favor range headroom; they are not a promise that the entire
0–8 kHz Nyquist band is flat. No automatic gyro zeroing or gravity subtraction occurs.

An interrupt services one sensor every 250 µs (each sensor every 1 ms), using
16 MHz SPI. The main loop writes binary records to SD. A 1024-entry RAM queue
provides roughly **230 ms** of normal backlog capacity, separate from each
sensor's short FIFO. Files are about **1.2 MB/s** in continuous mode. Actual
service time, maximum FIFO occupancy, queue occupancy, SD write duration, invalid
samples and near-saturation counts are recorded. Overflows and missed service
deadlines stop the recording with `ERROR` instead of silently discarding data.
The card must sustain recording with this hub and these cables in a bench test.

FlightBridge commands remain the existing `@XP,1,...*HH` protocol:

- **HEALTH**: when idle, mount the SD card and check all four WHO_AM_I values;
  while active, report monitored acquisition/storage health without disrupting SPI.
  This does not validate optical timing, mechanical attachment or the GPS fix.
- **START**: open a new session and continuously save every acquired FIFO packet.
- **ARM**: continuously sample into RAM history, but save IMU data only around
  hardware triggers. Default window is **20 ms before / 20 ms after the trigger**.
  Strobe edges extend an active window by another 20 ms; a currently active strobe
  prevents normal window closure. Batch boundaries add up to about 2 ms of margin.
  Overlapping triggers merge into one retained window and are explicitly marked.
  The logger stays `ARMED` after a capture; `CAPTURE_DONE` follows an SD sync.
- **STOP**: disarm, stop acquisition, drain buffered data, write final statistics,
  sync and close the file, then acknowledge. Stopping mid-burst marks it truncated.
- **STATUS**: report actual state. Disconnecting FlightBridge does not stop logging.
- **SPITEST**: from IDLE, run the four-sensor cable diagnostic at requested SPI
  clocks of 4, 8, 12 and 16 MHz. Immediate acknowledgement with state TESTING;
  progress and per-sensor results follow over Serial1. No SD card required.
- **SPITESTRESULT**: replay the latest diagnostic results, retained until reset or
  the next test. Available while idle/testing/error, not during acquisition.

While TESTING, START/ARM/HEALTH are rejected. STOP requests cancellation and replies
TESTING/SPI_TEST_STOPPING; wait for the final SPI_SUMMARY after sensor cleanup.
The test does not change the recording SPI clock or sample rate. Read
[SPI_TEST.md](SPI_TEST.md) before interpreting PASS or choosing a lower SPI clock.

Both modes save raw trigger/strobe edge timestamps and received NMEA. Recordings
begin only after sensor startup/filter settling. A trigger during the first 20 ms
after arming has incomplete history and is marked `warmup`.

Strobes are assumed **active high**, and trigger capture starts on a **rising edge**.
`Config.h` exposes polarity and a per-camera enable flag. For disconnected camera
inputs, disable their strobe handling or hold the upstream buffer input at a known
level; a pull-down at the Teensy cannot stabilize a floating buffer input.
Both edge levels are still logged. A strobe active over 250 ms raises `STROBE_STUCK`;
a merged burst over one second raises `BURST_LIMIT`. These are configurable limits.
If a camera's first strobe comes more than 20 ms after trigger, increase `POST_US`:
an edge arriving after the capture closed does not reopen it.

Errors latch until a Teensy reset. Save/query the error before resetting. `STOP`
cannot turn an unsuccessful recording into a successful one. A command may time
out in FlightBridge if a faulty/slow card stalls for over three seconds; query
STATUS before assuming the command failed. Repeated command IDs replay cached
responses; new recording sessions need new IDs, as FlightBridge already supplies.

## Timing and interpretation

Every sensor receives its own FSYNC pulse approximately every 10 ms, held high
until its next service (about 1 ms) without blocking acquisition, and timestamped
in the same extended `micros()` clock used for camera GPIO interrupts. The sensor
tags the first FIFO sample after that pulse and reports its FSYNC-to-sample delay.
The decoder uses those anchors to interpolate sample times and estimate each
sensor's actual rate. No common sensor oscillator is assumed. DS-000347 rev 1.6
section 12.7 requires the **32/30** timestamp scaling used here (no external CLKIN).

For a 250 µs exposure, 16 kHz gives approximately **four samples**; for 400 µs,
approximately six or seven. This is useful for vibration correlation, but does
not resolve arbitrarily fast intra-exposure motion. FSYNC aligns **digital output
samples**, not the original physical motion before the sensor's filters. GPIO
interrupt latency and filter group delay still need measurement/calibration before
claiming accurate blur prediction. `mcu_us` is boot-relative, not GPS UTC. NMEA
timestamps record line completion at the UART parser; they are not exposure times.

## SD and decoding

Use FAT32 or exFAT. Session filenames are `XP00001_000.XPL`, etc.; existing names
are never overwritten. Segments roll at approximately 512 MiB and share sequence
numbers. Supply **all segments of one session** to the decoder. A one-second sync
reduces exposure to power loss but is not a substitute for STOP. Wait for a
successful STOP acknowledgment before removing the card or power.

```powershell
python tools/decode.py 'E:/XP00001_*.XPL' --out decoded/session1
python tools/decode.py 'E:/XP00001_*.XPL' --out decoded/session1 --csv
```

The first command scans without generating large CSVs. `summary.json` reports
frame CRC/sequence problems, missing footer, sensor timestamp discontinuities,
firmware faults, saturation, invalid samples and measured rates. The second also
writes four sensor CSVs, `events.csv`, and `gps.csv`. Long NMEA sentences are stored
in fragments; concatenate equal-timestamp fragments by `fragment_index`.

Rows include `time_quality`: directly FSYNC anchored, interpolated between nearby
anchors, nominal extrapolation, or unsynchronized. The decoder never labels
unsynchronized data as precisely timed. Intentional gaps between triggered
captures are counted separately from continuous-mode missing samples; overlapping
capture history is deduplicated by sample index. All original raw data remain in XPL.
CRC protects storage records; SPI has no end-to-end CRC, so plausible on-wire bit
errors can still escape detection. `integrity_ok` is not proof of signal integrity
or correct exposure calibration.

## First bench acceptance test

1. Verify wiring/power, insert SD and flash. Connect FlightBridge over the physical
   RS-232 port at 115200. Confirm IDLE and run HEALTH. All four sensors are required.
2. Set known strobe polarity/idle levels. Run continuous recording for 60 seconds
   with GPS forwarding active, then STOP. Decode and require a clean footer, no
   firmware fault, no missing continuous samples/timestamp jumps and four sets of
   FSYNC anchors. Check measured rates near 16 kHz and queue/FIFO/service peaks.
3. Check SPI clock and data at the sensor ends of the actual cables. No load test
   in software can establish adequate electrical margins. `ACQ_LATE` or `FIFO_FULL`
   means the configured acquisition budget was not met; do not ignore that error.
4. Use a known 3.3 V pulse source to test the trigger input and suitable levels at
   the hub's strobe connectors. Test 250 and 400 µs pulses, compare `events.csv`
   widths with an oscilloscope/logic analyzer, then test all three cameras. Do not
   route RS-232 electrical levels into the GPIO/strobe paths.
5. ARM, wait at least 20 ms, trigger several separated captures, then STOP. Verify
   prehistory and all three exposure windows. Test one overlapping trigger and a
   STOP during capture; confirm both conditions are marked.
6. Run the intended maximum recording duration using the intended SD card, with
   GPS and all camera events. A short passing test does not bound rare SD stalls.

PSD and optical blur computation are deliberately downstream analysis, not firmware
work performed during acquisition. Axis orientation, bias and optical geometry
must be supplied when analyzing the recorded motion.

## References

- TDK [ICM-42688-P DS-000347 rev 1.6](https://product.tdk.com/system/files/dam/doc/product/sensor/mortion-inertial/imu/data_sheet/ds-000347-icm-42688-p-v1.6.pdf),
  FIFO sections 6, 12.7 and register descriptions in sections 14–16.
- [PJRC Teensy 4.1](https://www.pjrc.com/store/teensy41.html) and
  [pinout card](https://www.pjrc.com/teensy/card11a_rev4_web.pdf).
- [PJRC SdFat SDIO example](https://github.com/PaulStoffregen/SdFat/blob/master/examples/TeensySdioDemo/TeensySdioDemo.ino).

SPI/SDIO/core dependencies retain their upstream licenses. This package does not
redistribute the downloaded toolchain or datasheet.
