# ResetCsvLogger 1.2 — Arduino, Teensy 4.1, SEN1/J1, 16 kHz

Version 1.2 separates motion validity from temperature availability. The 16 kHz
hardware log returned temperature -128 in alternating packets. Version 1.1
incorrectly counted those as invalid motion samples too. All six axes in the
provided 16,629-row excerpt were free of invalid sentinels. The cause of the
alternating temperature availability has not been independently established;
the new flag preserves it explicitly without inventing replacement temperatures.
The new CSV has 14 columns: `temperature_valid` is appended to the old 13 columns.
Old files are exported verbatim, not reinterpreted or rewritten.

This is a separate, single-sensor SD recording test. HubVitality and the main
four-sensor flight firmware are unchanged. Version 1.1 raises both gyro and accel
to nominal 16,000 samples/second. SPI remains at 8 MHz. FIFO service runs every
250 microseconds instead of 500; timestamp screening is adjusted to 16 kHz.
The previously tested 8 kHz version is preserved in the release folder as
`ResetCsvLogger-8k-v1.0-Arduino.zip` and `ResetCsvLogger-8k-v1.0.ino.hex`.

**Every boot deletes ALL files and subdirectories on the microSD after exporting
the previous `/log.csv`. Unrelated files are deleted too. Nothing is formatted.**
This applies to the card in the Teensy's built-in socket, not to files on the PC.

## Load and run

1. Open `ResetCsvLogger.ino` in Arduino IDE. Keep `LoggerCore.h` and
   `VitalityCore.h` in the same `ResetCsvLogger` folder.
2. Select **Teensy 4.1**, **USB Type: Serial**, and the Teensy's USB port.
   This was compiled using Teensy core **1.62.0**, with its bundled SPI and SdFat.
   The USB-drain check is specifically based on that core's USB Serial driver.
3. Upload. Open Serial Monitor at **115200**, with **Newline** enabled.
   This is native USB: the baud selection does not limit export throughput.
4. Firmware waits indefinitely for a USB serial connection before touching the
   SD filesystem. After connection it mounts the card and exports any old log:

   ```text
   === LOG_CSV_BEGIN bytes=... ===
   ...complete previous log.csv...
   === LOG_CSV_END bytes=... crc32=... ===
   ERASING_ALL_SD_CONTENTS
   RECORDING log.csv; SEN1/J1 nominal 16000 samples/s; ...
   ```

   On the first run, `NO_PREVIOUS_LOG` replaces the export.
5. Move/rotate the sensor for a short test. Send **STOP** to stop and close the
   file. Send **RESET** to reboot: the saved CSV is printed and a new recording
   starts automatically. RESET during recording also closes first.
6. If the USB port disconnects during reset, reopen Serial Monitor. The firmware
   waits for it; it will not erase the card while waiting for that connection.

Start with a recording of a few seconds. At 16 kHz even a short recording produces
many thousands of lines. Arduino Serial Monitor may discard older text or become
slow; successful USB transmission cannot prove that it displayed or saved every
byte. For longer runs, use a terminal with capture-to-file enabled **before**
reset. The end marker contains the file byte count and CRC32 (IEEE) for verifying
an exact-byte capture. Markers are not part of the CRC or the file itself.
The monitor may normalize line endings when copying; this changes the CRC.

The Teensy's onboard pushbutton is a **Program** button, not an ordinary reset
button. Use the `RESET` command for this workflow, or an actual reset/power cycle.

## Commands

Commands are plain uppercase text with a newline, on USB or hub RS232
**Serial1, 115200 8N1**. They are not the FlightBridge XP command protocol.

- `STOP`: end acquisition, drain queued records to SD, sync, close. Wait for
  `FILE_CLOSED`. No further samples are recorded until reset.
- `RESET`: perform the same clean stop if needed, then reboot/export/erase/start.
- `STATUS`: recording state, acquired count, CSV row count, buffer occupancy,
  invalid samples, timestamp anomalies and error reason; also timing peaks.
- `HELP`: brief command reminder.

The old CSV is exported **only over USB**, never at RS232's limited throughput.
GPS lines and unrecognized commands are ignored. This sketch does not record GPS,
camera strobes, triggers, or the other three sensors.

## Capture and file format

The sensor FIFO is serviced by an interrupt every 250 microseconds. An 8,192-record
RAM queue separates acquisition from SD writes, buffering approximately half a second
of data. Main-loop writes use a 16 KiB buffer and sync approximately once a second.
No CSV formatting, SD calls or serial output occur in the acquisition interrupt.

The file contains comment lines beginning with `#`, a CSV header, and one row per
FIFO packet, including flagged invalid packets. For pandas use `comment='#'`.

- `sample_index`: zero-based record number.
- `mcu_read_us`: extended Teensy clock at the start of that FIFO service. Records
  in the same FIFO batch share a timestamp; this is **not** their precise individual
  sampling time.
- `sensor_ticks`: relative, unwrapped sensor timestamp. Nominal tick duration is
  1.066666667 microseconds; the internal oscillator is not calibrated. Do not assume
  actual sampling is exactly 16,000 Hz. The previous 8 kHz run measured about
  8,116 Hz; approximately 16,232 Hz would be consistent with that clock offset,
  but must be measured again at this setting.
- `timestamp_raw`: original 16-bit FIFO timestamp, retained for checking rollover.
- `ax_raw,ay_raw,az_raw`: signed accelerometer counts; divide by 2048 for g.
- `gx_raw,gy_raw,gz_raw`: signed gyro counts; multiply by 2000/32768 for degrees/s.
- `temp_raw`: signed 8-bit FIFO temperature; degrees C = raw/2.07 + 25.
- `valid`: 0 when any of the six motion axes contains the invalid sentinel -32768.
  Since version 1.2 this flag and the `invalid` status counter exclude temperature.
- `temperature_valid`: 0 when `temp_raw` is -128. Do not convert that sentinel to
  degrees C. `temp_missing` counts these slots separately in status and the footer.
- `timestamp_anomaly`: 1 when successive timestamp separation is outside
  40–80 ticks (normally about 58–59). This screens for duplicates/gaps but is not
  proof of SPI integrity.

The sensor axes and ranges match HubVitality. No calibration or gravity removal
is applied. INT1 edges are diagnostic only; FIFO packets determine record counts.

A clean stop adds `# END` with counts and the stop/fault reason. The recording
window ends at the last serviced FIFO batch; samples still in the sensor FIFO at
STOP are outside that window. All records already in the RAM queue are drained
unless an SD error prevents it. `csv_rows` during recording includes rows buffered
in RAM and is not a count of durably synced rows yet.

## Failures and limitations

- A detected disconnect, USB stall, SD read failure or incomplete export prevents
  erasure. USB transfer completion is checked, **not acknowledged by the terminal
  application**. After a failed dump, reconnect and send RESET to retry.
- Erasure is recursive, including hidden files. If a file cannot be deleted
  (for example a read-only file), the directory is corrupt, or nesting exceeds
  16 levels, the firmware reports `ERASE_FAILED_MAY_BE_PARTIAL` and does not start
  recording. Earlier deletions cannot be undone. It does not bypass read-only
  attributes or format/recover a damaged filesystem.
- Sensor identity/configuration errors, FIFO full, malformed packets, RAM queue
  full, and SD write/sync errors stop the run and report a fault. An SD failure can
  prevent writing the final footer; inspect both serial status and the exported
  file. `# END reason=STOP` plus zero invalid/anomaly counts is the desired result.
- Hard reset or power removal can lose the RAM queue, partial write buffer and
  unsynced card data, and can damage filesystem metadata. Sync once per second is
  not a guarantee against card-internal caching or power failure. Use STOP/RESET
  commands for the normal test cycle. Without `# END`, treat the file as interrupted.
- Startup checks do not establish sustainable SD write throughput. The first
  hardware run must confirm no faults and bounded queue occupancy while writing.
  A queue near 8192 or growing continuously indicates inadequate write throughput.
- Keep the corrected cable orientation and confirmed 3.3 V supply. Pin 13 is SPI
  SCLK, so this sketch never blinks the onboard LED.

## Pinout

SEN1/J1: SPI0 SCK 13, MOSI 11, MISO 12, CS 10, INT1 2. FSYNC wire 6 stays an
input. Other chip selects 9/38/37 are held high; FSYNC 7/21/32 remain inputs.
The built-in SD socket uses native SDIO and does not share the sensor SPI bus.

## Build and validation

From the project root, run `firmware/build-csvlogger.ps1`. The output is
`firmware/release/ResetCsvLogger.ino.hex`; the script does not program hardware.
Run `firmware/test.ps1` for native tests, including export failures, destructive
operation ordering, nested deletion on a mock filesystem, CRC, timestamp rollover,
CSV formatting, and concurrent queue operation. Arduino compilation validates
the actual SdFat/Teensy API use. No physical SD recording test has been performed
by the assistant.
