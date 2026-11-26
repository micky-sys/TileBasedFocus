# HubVitality — one-sensor board bring-up

## Version 1.2: SEN1 at 8,000 samples/s (2026-11-17)

The default sketch now samples **both gyro and accel at 8 kHz**, half the main
logger's planned 16 kHz. Keep the working sensor at **J1**. Reopen the INO in
Arduino IDE and upload; keep `VitalityCore.h` in the same sketch folder. Confirm
the startup banner says **HubVitality 1.2**. USB Serial Monitor remains 115200.
No additional libraries are needed. The separately built HEX is also updated.

Initialization/identity checks use 1 MHz SPI; acquisition uses a requested
**8 MHz SPI clock**. This is distinct from the 8 kHz sampling rate. Identity
scans still run at 100 kHz and 1 MHz. A new `IDENTITY_AT_8MHZ` error means the
slow bring-up succeeded but the first identity read at the acquisition clock did
not. It is a diagnostic lead, not proof of a particular cable fault.

The 200 Hz direct-register polling was replaced with **16-byte FIFO packets**,
read in batches in the main loop about every 500 us. FIFO service reads up to
32 packets per call. Both serial outputs use bounded queues and nonblocking
writes, so a slow monitor does not deliberately stall acquisition. Only summaries
and the latest sample are transmitted once per second, not all 8,000 samples.
Nothing is saved to SD. Factory anti-alias/notch settings are retained; UI filters
are second order with ODR/4 bandwidth, and measurement ranges are unchanged.

For a first run, leave the board still for 30–60 seconds, then gently move it.
Expected observations:

- `rate_hz` near **8000**; some offset is expected from the independent clocks.
  The interval PASS screen uses +/-5%, not a precision clock qualification.
- `invalid=0`, `full=0`, `bad_headers=0`, `timestamp_anomalies=0`.
- INT1 edges near 8,000 per second (8 us active-high data-ready pulses).
- FIFO peak well below its roughly 2 kB capacity; observe `service_max_us` and
  `gap_max_us` for long service delays. These maxima are since the last RETEST.
- `tx_drop_usb=0`, `tx_drop_uart=0`. These count discarded output *lines* when a
  text queue fills, not discarded sensor samples. Disconnected USB output is
  intentionally discarded without counting it as a queue overflow.

The FIFO is stop-on-full. A full flag, invalid count/alignment, malformed packet
or loss of identity stops the test with a visible fault. Use RETEST after resolving
the cause. Invalid samples and timestamp anomalies are counted; a reporting
interval containing them fails. FIFO timestamps are checked for duplicates and
large steps (80–160 ticks accepted at 8 kHz), including 16-bit rollover. This is
screening, not proof of error-free samples: the sensor SPI stream has no data CRC.

`SD` and `SCAN` now explicitly pause a running acquisition, perform their test,
then restart SEN1 with fresh counters. DIAG and RETEST likewise restart the run.
Use STATUS for observation without interrupting acquisition. Check uninterrupted
operation without issuing these reset/test commands during the 30–60 s interval.

The FIFO temperature field is eight-bit, so its displayed temperature is coarser
than the old direct-register temperature. Host age refers to the last FIFO read,
not the exact sampling instant. The test does **not** yet establish four-sensor
capacity, SD writing throughput or camera timing.

Validation: compiled for Teensy 4.1/core 1.62.0; native tests passed for output
queue wrap/full behavior, FIFO header/axis/temperature decoding and timestamp
wrap/duplicate/gap detection. This 8 kHz version has not yet been run on the board.

## Version 1.1 diagnostics (2026-11-17)

When initial bring-up fails, flash the updated `../release/HubVitality.ino.hex`,
leave the one sensor in J1, open Serial Monitor with newline enabled and send
`DIAG`. Copy the complete output from `TEST SD` through the final `DETAIL` line.
This repeats SD initialization, scans all four sensor connector identities,
and retries the normal J1 bring-up. No card files are written or formatted.

`SCAN` now checks J1/J2 on SPI0 and J3/J4 on SPI1 at 100 kHz and 1 MHz, 32 reads
at each speed. Each row includes the first eight raw WHO_AM_I bytes and counts
for 0x47, 0x00, 0xFF and other values. Only populated connectors are expected to
respond. A stable 0x00 or 0xFF alone does not distinguish missing power, wiring,
wrong selection, shorts or other hardware faults. Live measurements and RETEST
still target J1 only; moving the sensor does not select a different live channel.

SD reporting now separates `CARD_INIT_FAILED` (before mounting the filesystem)
from `VOLUME_MOUNT_FAILED` (card initialized, mount did not succeed), with SdFat
error code/data in hexadecimal. Filesystem mount failure may also involve read
errors, so it does not by itself prove a formatting problem. Successful card
initialization prints capacity. Error details remain in the repeating heartbeat,
so they are available even if the startup output was missed. Use a PC card reader
or another known-good card for comparison; do not format the existing card merely
because the mount failed.

USB output confirms MCU execution; it does not prove power reaches the remote
sensor. If a multimeter is available, measure 3V3 relative to GND at the Teensy
and at the sensor board while powered. Move connectors only with power off.

Standalone firmware for **Teensy 4.1** and **one ICM-42688-P on SEN1/J1**.
It does not require the other three sensors, a camera, GPS, or an SD card.
It replaces the recording firmware on the Teensy when flashed; the original
logger source and HEX remain separate and can be flashed again later.

## Run

1. Connect SEN1 with power off. Observe pin-1 orientation at both ends of the cable.
2. Power the hub normally. If using external hub power and USB simultaneously,
   ensure Teensy VUSB is isolated from VIN: they are joined on a stock Teensy.
   [PJRC's power instructions](https://www.pjrc.com/store/teensy41.html#power)
   explain separating the pads. Do not connect two 5 V supplies together.
3. Flash `../release/HubVitality.ino.hex` with Teensy Loader, or open
   `HubVitality.ino` in Arduino IDE and select Teensy 4.1 / USB Type: Serial.
   Libraries are SPI and SdFat from the Teensy board package; no additional IMU
   library is needed. The tested build uses Teensy core 1.62.0 / 600 MHz.
4. Open the Teensy's USB Serial Monitor at 115200. It starts automatically;
   select Newline or Both NL & CR for commands. Opening USB late prints a banner
   and status; type `RETEST` to repeat the startup checks.
5. Leave the sensor still, then gently tilt and rotate it. Read the repeating
   measurements. There is no recording to SD.

Power/USB isolation is a hardware property; the program cannot verify it or
measure the hub's 12 V, 5 V or 3.3 V supply rails.

## What is checked

- MCU/USB: readable heartbeat/status and PING/PONG.
- SEN1 SPI: 100 WHO_AM_I reads at a conservative **1 MHz**, all expected to be
  `0x47`; sensor soft reset and RESET_DONE; configuration write/readback.
- Sensor data: gyro and accelerometer in low-noise mode, **8 kHz**, ranges
  **+/-16 g / +/-2000 degrees/s**. FIFO batches contain temperature, six axes and
  sensor timestamps. Invalid `-32768` axis readings are counted.
- INT1 wiring: data-ready is routed to SEN1 INT1, Teensy GPIO 2. The program counts
  rising edges independently from SPI polling. `OBSERVED` means edges arrived,
  not that the exact waveform or every sample was verified.
- Optional SD: mount card/filesystem in the Teensy's built-in SDIO socket. This
  checks basic access only; it does not create, modify, delete or format files,
  or certify write speed.
- RS-232: all output is also sent through Serial1, 115200 8N1. A received `PING`
  returns `PONG source=RS232`, checking the path in both directions **if that
  reply is received back on the PC's physical RS-232 port**.

SPI0 pinout: SCK 13, MOSI 11, MISO 12, CS 10. Unused sensors stay deselected.
SEN1 INT2/FSYNC (GPIO 6), and the other FSYNC wires, stay inputs. No camera lines
are driven. GPIO 13 is not blinked because it is SCLK.

## Expected output

These are illustrative values, not a measurement from your board:

```text
PASS WHO_AM_I expected=0x47 matches=100/100 first_bad=0x00
PASS SOFT_RESET
PASS CONFIG_READBACK; FIFO RUN odr_hz=8000 spi_hz=8000000 SEN1 only; summaries once per second
PASS DATA samples=8000 rate_hz=8000.0 invalid=0 interval_ms=1000; OBSERVED INT1 edges=8000 target_hz=8000
FIFO peak_bytes=80 full=0 bad_headers=0 timestamp_anomalies=0 service_max_us=150 gap_max_us=650 tx_drop_usb=0 tx_drop_uart=0
age_ms=0 accel_g=[0.010 -0.020 1.000] norm_g=1.000 gyro_dps=[0.12 -0.18 0.06] temp_C=28.38
```

`first_bad` is meaningful only if fewer than 100 identity reads matched.
At rest the acceleration vector magnitude (`norm_g`) should be approximately
1 g regardless of orientation, and gyro values should be near zero with some
bias/noise. Tilting redistributes gravity between axes; rotating produces gyro
readings. Temperature is the internal sensor temperature, not ambient temperature.

`PASS DATA` screens the interval's rate, invalid samples, timestamp steps and
recent valid data. It does not verify scale accuracy, correct physical axes,
every possible SPI bit error or the manufacturer's MEMS self-test. Diagnostic
commands explicitly interrupt/restart acquisition as described above; printing
uses nonblocking queues. `age_ms` labels the age since the most recent valid
FIFO read, so stale numbers are not mistaken for current measurements.

If WHO_AM_I fails (often 0x00 or 0xFF), check supply, cable orientation/continuity,
CS and solder joints. If data works but `WARN_NO_EDGES` appears, investigate the
INT1 route between sensor and Teensy GPIO 2. If `FAIL DATA` persists, inspect the
invalid count and rerun `RETEST`. SD not mounted does not fail the SEN1 check.
There is intentionally no blanket "entire hub PASS" result.

## Commands

Plain uppercase text, terminated by Enter, on USB or a serial terminal connected
to the hub's physical RS-232 interface:

- `PING`: replies PONG and identifies USB versus RS232 command source.
- `STATUS`: MCU uptime, sensor configuration/fault, sample/error/INT1 counts, SD
  mount state and the number of received RS232 lines.
- `RETEST`: reset and retest SEN1, then resume live readings.
- `SCAN`: identity-only scan of J1 through J4, at 100 kHz and 1 MHz.
- `DIAG`: SD check, full identity scan, then SEN1 reset/configuration/data test.
- `SD`: retry the optional SD mount check.
- `HELP`: repeat the instructions.

This sketch **does not implement FlightBridge's XP command protocol**. Close
FlightBridge before opening the same physical COM port in a serial terminal.
USB Serial and hub RS-232 are separate ports. Receiving automatic output alone
tests only the device-to-PC direction; use PING for a round-trip check. NMEA lines
are ignored. Do not connect RS-232 voltages directly to Teensy GPIOs; use the hub.

## Scope and validation

Source: [TDK ICM-42688-P datasheet](https://product.tdk.com.cn/system/files/dam/doc/product/sensor/mortion-inertial/imu/data_sheet/ds-000347-icm-42688-p-v1.6.pdf),
register descriptions for DEVICE_CONFIG, INT_CONFIG, INT_STATUS, sensor data,
INTF_CONFIG0, PWR_MGMT0, GYRO/ACCEL_CONFIG0, INT_CONFIG0/1 and INT_SOURCE0.

Not tested by this program: SEN2–4 live measurements/interrupts, FSYNC/INT2, camera trigger/strobes,
power-rail voltages, SD writing, high-rate FIFO acquisition or cable timing margin.
After this basic bring-up, use the separate logger/SPI diagnostic for those next
steps. Compilation does not substitute for the first run on your hardware.

Build from the project root with `./firmware/build-vitality.ps1`. It produces a
separate `firmware/release/HubVitality.ino.hex` and never uploads it automatically.
