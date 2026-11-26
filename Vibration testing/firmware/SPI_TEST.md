# SPI cable diagnostic — firmware 1.1.0

This is an electrical communication screening test for the four ICM-42688-P
sensors on the existing hub, including the proposed 400 mm cables. It needs no
oscilloscope and no SD card. It does not record vibration or exposure timing.

## Running from FlightBridge

1. Flash `release/VibrationLogger.ino.hex` and use the updated
   `../FlightBridge/release/FlightBridge.exe`.
2. With power off, connect all four sensors and route the cables as they will be
   installed. Power up. Connect FlightBridge to the hub's RS-232 port at
   **115200, 8N1**, using the existing GPS/navigation port configuration. This is
   Serial1 through the MAX3232; the USB serial monitor is not the command input.
3. Wait for IDLE, then click **SPI cable test**. TESTING confirms the test
   started; it does not mean it passed. The run takes several minutes, reporting
   progress roughly every second. GPS continues forwarding; incoming NMEA at the
   Teensy is consumed but not saved during the diagnostic.
4. Wait for the final `SPI_SUMMARY` in the log. A complete run produces 16
   `SPI_ROW` entries: four sensors at each of 4, 8, 12 and 16 MHz, in that order.
   These are **requested SPI clocks**, not measured SCLK frequencies; the Teensy
   SPI peripheral uses the closest supported clock not exceeding the request.
5. **Stop SPI test** cancels the run and restores sensor configuration. Wait for
   the final summary before starting acquisition or removing power. Disconnecting
   the app does not cancel the test. Reconnect and click **SPI results**
   to recover the most recent results while the Teensy remains powered.

The log is saved to `%LOCALAPPDATA%\XParallax\FlightBridge\events.log`
(with rotating backups). Demo mode reports `SIMULATOR_NO_SPI`, never a cable PASS.
The device keeps only the latest run in RAM; a new run or power cycle removes it.

## What it actually tests

- Both SPI buses and all four existing CS/MISO/MOSI/SCLK paths, with normal pinout.
- 100,000 write/read rounds **per sensor per requested clock**: 900,000 readback
  bytes per row, 14.4 million readback bytes for a complete run.
- Deterministic patterns derived from complementary, alternating, walking-bit and
  pseudorandom values. Sensor/byte-specific salts distinguish chips and offsets.
- Every round writes all four chips before reading them back, helping expose
  aliased chip selects as well as read/write corruption.
- Exact byte and bit comparison, counts of failed register accesses and the first
  mismatched byte. A single mismatch fails that sensor/rate; nothing is retried
  away or averaged out.

The test uses **bank 4 OFFSET_USER0..8, addresses 0x77..0x7F**, with the gyro and
accelerometer OFF and FIFO disabled. These are volatile user offset registers;
the test does not write calibration OTP. Configuration and bank guards run at
1 MHz; test payload writes and nine-byte burst reads run at the selected clock.
Before every pattern write, the bank is checked at 1 MHz. Failed preparation or a
failed bank guard skips that sensor for that rate and is reported as an access
error. Other sensors/rates are still tested.

At each rate, sensors are reset/configured. After completion or cancellation,
all four are reset and configured for the normal logger again, with acquisition
still OFF. The firmware verifies zero user offsets, bank 0, and restores each
software SPI setting to 16 MHz. A failed cleanup latches `SPI_RESTORE` and blocks
recording until reset. Save the results and repair/check wiring before resetting.

Register references: TDK ICM-42688-P DS-000347 rev 1.6, sections 5.4, 12.9 and
17.18–17.26:
[official datasheet](https://product.tdk.com.cn/system/files/dam/doc/product/sensor/mortion-inertial/imu/data_sheet/ds-000347-icm-42688-p-v1.6.pdf).

## Reading results

Example **detail field**, inside the normal checksummed XP response:

```text
SPI_ROW;test=0123456789ab;mhz=16;s=3;n=100000;bad_bytes=0;bad_bits=0;access=0;result=PASS;first=0:0:00:00
SPI_SUMMARY;test=0123456789ab;result=PASS;restore_mask=0;elapsed_ms=350000;target=100000
```

`test` is the start-command request ID. `s` is sensor 1–4/J1–J4, and `n` is the
number of successful readback rounds (including rounds with mismatched data).
`bad_bytes` and `bad_bits` count mismatches, not estimated link BER. `access`
counts failed preparation/bank checks; it is not a SPI peripheral error flag.
`first` is zero-based round:byte:expected-hex:actual-hex; meaningful only when
`bad_bytes > 0`. `elapsed_ms` above is illustrative, not a runtime guarantee.

Rows are PASS only after all 100,000 rounds with zero mismatch/access errors.
They can also be FAIL, PARTIAL or NOT_RUN. Overall PASS requires all 16 rows to
pass and cleanup to succeed. Other summary results are FAIL, CANCELLED,
RESTORE_FAILED, or RUNNING for a query during a test. `restore_mask` uses bits
0–3 for failed cleanup on sensors 1–4. All results use the current recorder state;
an ordinary failed cable test returns IDLE after successful cleanup, not ERROR.
Do not interpret IDLE or the command acknowledgement's OK as a passing result.

## Bench acceptance and clock reduction

Repeat the test several times with all four cables in their final positions,
including gentle cable movement and the usual nearby powered equipment. Any
mismatch or access failure is a failure to investigate. Compare with the original
short cable and swap one cable/sensor at a time to isolate the fault.

A PASS is useful evidence, **not a measurement of ringing, voltage margin, or a
guarantee against rare errors**. Nine-byte register readback does not validate long
FIFO bursts, sensor timing, SD stalls, or the acquisition scheduler. Sensor SPI
samples have no end-to-end data CRC; plausible-looking corrupted FIFO values can
evade normal logging checks. The file CRC protects storage, not the sensor link.

After a clean diagnostic, run at least 10 minutes of normal **Continuous** capture
at the existing 16,000 samples/s per sensor, with GPS forwarding, SD writing and
representative trigger/strobe traffic. STOP cleanly, decode the resulting file(s),
and inspect the acquisition errors, sample gaps and recorded service timing. This
is a separate throughput test; the diagnostic does not certify recording capacity.

The sweep **does not automatically lower the recording SPI clock**. The current
logger services the buses sequentially. With 16-byte packets and 16 ksample/s on
four sensors, payload alone is 8.192 Mbit/s before register/transaction overhead.
Thus setting both buses to 8 MHz cannot sustain the current implementation/rate.
At 12 MHz a maximum 24-packet service plus 8 protocol bytes takes about 261 us on
the wire, already exceeding its 250 us service budget, before software overhead.
If 16 MHz fails but slower rates pass, change cable/termination or revise the
acquisition schedule/rate and revalidate; do not just reduce `SPI_HZ` and fly.

## Direct RS-232 commands

The existing XP protocol is unchanged in version. New command verbs are
`SPITEST` and `SPITESTRESULT` without parameters. Use a fresh 12-character
lowercase hex ID, XOR checksum, and CRLF, as with START/STOP. Generate frames with
`FlightBridge/protocol.py`'s `command_frame(id, verb)`; a bare `SPITEST` line is
not accepted. Duplicate IDs replay the cached acknowledgement without restarting
the test. For a new results report, send SPITESTRESULT with a new ID.

SPITEST is accepted only from IDLE. START, ARM and HEALTH are rejected during
TESTING. STATUS remains available. STOP acknowledges `SPI_TEST_STOPPING` before
asynchronous cleanup finishes; the final report identifies cancellation. If STOP
arrives after all test work has completed, the already-computed result stands.
SPITESTRESULT is rejected during recording/armed capture to avoid diagnostic
reporting load while writing data. Mid-test reports are live views, not atomic
snapshots; use the final report for acceptance.

Unsolicited reports use request ID `0`, with details `SPI_PROGRESS`, `SPI_ROW` and
`SPI_SUMMARY`. A row/report error does not acknowledge another pending command.
