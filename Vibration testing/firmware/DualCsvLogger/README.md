# DualCsvLogger 2.2 — Teensy 4.1, J1 + J3 with FSYNC

## New in 2.2: common time reference, existing wiring

Connect the same sensors to **J1 and J3**. No jumper or cable change is needed.
Sensor connector pin 4 (labelled INT2) already carries the IMU pin9 multifunction
signal: J1 to Teensy GPIO6, J3 to GPIO21. The firmware changes that pin to a
**FSYNC input** before driving it. It releases the MCU output before sensor reset.
INT1 stays available for data-ready diagnostics. J2/J4 FSYNC wires remain inputs.

Every approximately 10 ms, Teensy emits one pulse on each FSYNC wire. Pulses are
held high until the next 250 us service tick. Each rising edge has its own
before/after `micros()` bracket on a common 64-bit MCU clock; sequential pin
writes are not assumed simultaneous. Each sensor must return a matching FIFO tag.
Missing tags for 30 ms, unsolicited tags or full metadata queues stop the run.
`SYNC_STATUS sent=... received_J1=... received_J3=...` should increase about 100/s;
one pending tag per sensor can be normal at a status snapshot.

**This aligns timestamps, not ADC sample phases.** Internal oscillators remain
independent. Vibration Monitor 2.2 reconstructs sample times and clock drift
between FSYNC anchors. Do not subtract rows with equal sample indices.

Register configuration, from local TDK DS-000347 sections 6.3, 12.7, 14.42,
14.45, 14.48 and 15.18: TMST_CONFIG=0x23; FIFO_CONFIG1=0x0F;
FSYNC_CONFIG=0x10 (rising edge, UI temperature tag); bank1 INTF_CONFIG5=0x02.
FIFO temperature/axis LSBs are not modified by UI FSYNC tagging.

Marked FIFO packets replace the absolute ODR timestamp with FSYNC-to-ODR delta.
Their `sensor_ticks` column therefore retains the preceding ordinary counter;
the next ordinary packet unwraps a two-period interval. The Windows app recovers
the marked sample's tick as the midpoint of its two neighbours, subtracts the
raw FSYNC delta to locate the edge in sensor ticks, and maps between consecutive
edges and MCU times. This assumes regular ODR between neighbouring packets and
approximately linear clock drift over 10 ms. Missing samples/timestamp anomalies,
missing/duplicate markers or implausible anchor slopes disable aligned display.
Before the first/after the last anchor, samples remain in the file but are not
plotted in aligned mode; there is no extrapolated timing presented as measured.

Timestamp quantization, input synchronization and GPIO timing introduce error.
The edge bracket is NOT a measured total accuracy. Filter/group delay is not
removed. Physical validation remains necessary, ideally by mounting both sensors
rigidly side by side with matching axes and applying common gentle motion.
This checks gross alignment; it cannot independently certify microsecond latency.

**Only sensors are connected at present. This sketch does not record camera
trigger/strobe inputs.** Exposure alignment will additionally require verified
strobe capture and accounting for sensor filtering delay. A common time reference
alone does not identify which samples belong to a particular exposure.

Use the 2.2 app for aligned graphs; the old app ignores the new metadata and
continues to show FIFO-read time. In 2.2, zoom to a span of at most 3 seconds,
then click **Load visible range: individual samples** to inspect raw samples
without the long-recording min/max envelope. **Full recording overview** restores
the full view. Timing is reconstructed at load time; raw CSV values are retained.

Compilation and automated tests passed. The new FSYNC path and its extra CSV
load have not yet been physically tested. Start with a 10–20 second run and
look for `FSYNC-aligned sample time`, zero timestamp flags and no queue growth.

## Existing acquisition and transfer behavior

Version 2.1 replaces per-row snprintf with a bounded integer-only CSV formatter.
The output columns, sensor ODR, SPI speed and buffer capacity are unchanged.
The November 19 two-sensor runs stopped after about 0.64–0.73 seconds with
RAM_QUEUE_FULL: acquisition outpaced the foreground conversion/write path.
Version 2.1 subsequently recorded both sensors for 18.66 s without overflow in
the user's bench run. Version 2.2 retains that formatter and acquisition schedule.

An additional `# TIMING` footer records capture duration before draining buffered
data, maximum FIFO service time/gap, maximum SD write/sync duration and fault
sensor. These are wall-clock durations and can include interrupt preemption.
The older END elapsed_ms includes final draining and is not exact captured span.
The new formatter is tested byte-for-byte against snprintf on 10,000 randomized
records, 64-bit boundary values, negative sentinels and insufficient buffers.

Two ICM-42688-P sensors, each at nominal **16 kHz**, one per SPI peripheral.
This is a separate sketch; tested single-sensor ResetCsvLogger is retained.

## Connections (Teensy GPIO numbers, not connector pad numbers)

- **SEN1 / J1**: SPI0; SCK 13, MOSI 11, MISO 12, CS 10, INT1 2.
- **SEN3 / J3**: SPI1; SCK 27, MOSI 26, **MISO 39**, CS 38, INT1 23.
- J2 and J4 remain unused. Their chip selects 9/37 stay high.
- FSYNC GPIO6/21 are driven after sensor configuration; GPIO7/32 remain inputs.
- The built-in microSD socket uses native SDIO, independent of both SPI buses.

Mapping follows `VibrationLogger/Config.h`, the hub mapping already used in the
project. Do not attach the second sensor to J2 for this test. SPI1 explicitly
selects alternate MISO pin 39, not its default pin 1.

Open `DualCsvLogger.ino` with both `.h` files in the same folder. Select Teensy
4.1 and USB Type Serial in Arduino IDE, and upload. Close Serial Monitor and use
the **Vibration Monitor** Windows app. It implements the new save acknowledgement.

## Workflow and commands

At boot: wait for USB, mount SD, export an existing `/log.csv`, wait for a host
save acknowledgement, clear **ALL SD contents**, create a new log and start both
sensors. If there is no old log, no acknowledgement is needed. The old file's
format is irrelevant to export: it is copied verbatim.

- `DUMP`: stop/close if necessary, export, wait for acknowledgement; **retain** SD
  contents and stay stopped. The app labels this **Stop & download**.
- `NEW`: stop/close, export old file and require acknowledgement, clear **ALL SD
  contents**, create new file, reset counters, configure and start both sensors.
- `STOP`: stop acquisition, drain RAM queue, sync and close; no download or erase.
- `STATUS`: total counters, timing peaks, and one SENSOR_STATUS line per sensor.
- `HELP`: short protocol reminder.
- `RESET`: clean stop then reboot; boot workflow applies again.

Commands can arrive over USB or hub Serial1 RS232 at 115200. All exports and ACKs
use USB only. This test firmware does not use the FlightBridge XP protocol and
does not record GPS, trigger or strobe inputs.

An export is `LOG_CSV_BEGIN bytes=N`, exactly N file bytes, and
`LOG_CSV_END bytes=N crc32=XXXXXXXX`. After `WAIT_LOG_ACK`, the host must send
`ACK_LOG N XXXXXXXX` with a newline. Exact size and uppercase CRC must match.
Timeout is 30 seconds. Missing/bad ACK, detected USB failure or SD read failure
prevents erasure. If the directory purge itself fails, it may have partly deleted
contents; the firmware reports this and does not start recording.

Do not rely on Serial Monitor to acknowledge files automatically. When testing
without the app, the firmware will retain the previous log and stop after ACK
timeout. NEW/DUMP can be retried after the timeout. Details are in the app README.

## Acquisition and limitations

Both sensors are configured and allowed to settle with FIFO bypassed. Only after
both complete initialization are the FIFOs enabled and acquisition started. This
avoids filling the first sensor's FIFO while the second sensor settles.

Both use SPI 8 MHz, +/-16 g, +/-2000 degrees/s and 16-byte FIFO packets. One sensor
is serviced per 250 us timer interrupt, alternating buses: each sensor is serviced
every 500 us. The two peripherals do not imply simultaneous CPU SPI transfers.
An 8192-record shared ring buffers approximately **0.25 seconds** of combined
data, not 0.5 seconds per sensor. Main-loop CSV writes use 16 KiB blocks and sync
about once per second. Queue full, FIFO full, malformed packets or SD errors stop
the run. `fault_sensor` identifies a sensor-related failure by connector number.

The CSV appends `sensor_id` and `sync_id` to the ResetCsvLogger 1.2 columns:

```text
sample_index,mcu_read_us,sensor_ticks,timestamp_raw,ax_raw,ay_raw,az_raw,gx_raw,gy_raw,gz_raw,temp_raw,valid,timestamp_anomaly,temperature_valid,sensor_id,sync_id
```

`sensor_id` is **1 or 3**. `sample_index` starts at zero **independently for each
sensor**, as do unwrapped relative `sensor_ticks`. Do not interpret adjacent
interleaved rows as a single sensor stream. MCU batch-read times share a common
extended clock. The sensor clocks are independent, and their sampling phases and
rates may differ. FSYNC provides common time anchors as described above; camera
exposure timing is not recorded by this sketch.

`sync_id=0` is an ordinary timestamp; a nonzero ID identifies a marked sample.
Associated comment records can appear before or after their sample rows:

```text
# FSYNC sensor=1 id=7 before_us=1280000 bracket_us=1
```

ID increments per pulse pair; both wires share an ID, with individually measured
MCU edge brackets. Do not strip these comments from files intended for timing
analysis. RAM ring capacity remains 8192 samples (now 48 bytes each); a separate
128-entry queue stores pulse metadata, and CSV adds only one per-row column.

Conversions: acceleration = raw/2048 g, gyro = raw*2000/32768 degrees/s,
temperature = raw/2.07 + 25 C only when `temperature_valid=1`. Motion sentinel
-32768 makes `valid=0`; temperature sentinel -128 increments `temp_missing`
independently. Ordinary timestamp differences outside 40–80 sensor ticks are
flagged, or 80–160 ticks across a replaced FSYNC timestamp.
INT1 edge counters are diagnostic only; FIFO records determine sample counts.

For a first bench test, record 10–20 seconds and download. Expect roughly 16,000
samples/s **per sensor**, zero motion-invalid/timestamp-anomaly counts, no fault,
and total `samples=csv_rows` with `queued=0` after stop. Actual ODR differs from
nominal due to the internal oscillator. Missing temperatures may remain common.
Watch sustained queue growth with the new FSYNC metadata enabled. CSV recording
uses significantly more bandwidth than binary.

Hard resets/power loss can lose buffered or unsynced data or corrupt metadata;
use Stop/Download/New for ordinary operation. An interrupted log lacks `# END`.
The bounded parser/deletion/queue/ACK tests and Teensy compilation pass; no
physical hardware was programmed or exercised by the build.

Build from the project root with `firmware/build-dualcsvlogger.ps1`.
Output: `firmware/release/DualCsvLogger.ino.hex`. Tests: `firmware/test.ps1`.
