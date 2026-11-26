# Vibration Monitor — Windows desktop app

## Version 2.2: shared sensor time reference

Use `release/2.2/VibrationMonitor.exe` with **DualCsvLogger 2.2**. The app remains
compatible with old files, but old logs cannot retroactively acquire FSYNC timing.
On valid new logs, the axis and summary say **FSYNC-aligned sample time**. The
parser uses FIFO tags and individually timestamped GPIO6/GPIO21 pulses to correct
sensor clock offset/drift. It does not use FIFO-read time as individual sample
time. There must be at least three complete, plausible anchors for each sensor;
sequence/timestamp/marker errors cause an explicit fallback to batch-time display.

The sensors continue to sample at independent phases. Aligning their time axes
does not create simultaneous samples or correct sensor/filter group delay.
No camera strobe/trigger capture is included in this version: exposure timing is
still unknown. Samples outside the first/last anchors are retained in CSV but
excluded from aligned graphs, rather than extrapolated.

Long-recording overview graphs retain peaks in min/max bins. For detailed timing,
zoom to <=3 seconds in the current tab, then click **Load visible range: individual
samples**. The app reloads that interval from raw CSV, applying FSYNC timing and
retaining individual sample coordinates. **Full recording overview** restores
the whole log. Reported counts/duration remain for the full recording.

Timing reconstruction is covered by synthetic tests with different sensor rates,
phases, FIFO delays, timestamp wraps, missing anchors and chunk boundaries.
Physical timing accuracy and new firmware throughput still need the bench run.

The November 19 update displays the actual recorded duration per sensor and
highlights fault termination (for example RAM_QUEUE_FULL) rather than reporting
an ordinary stopped state. A requested ten-second run may contain much less data
if the firmware stopped early; inspect End and the per-sensor duration.

Run `release/2.2/VibrationMonitor.exe`. It is a standalone Windows x64 application;
Python, Arduino IDE and a card reader are not needed to run the app.
The UI is English to avoid mixing right-to-left labels with hardware identifiers.

## Connect and record

1. Upload **DualCsvLogger 2.2** to a Teensy 4.1. Attach sensors to **SEN1/J1** and
   **SEN3/J3**. These are the two different SPI buses; J2 is on the same bus as J1.
2. Close Arduino Serial Monitor. Open Vibration Monitor, choose the Teensy's COM
   port and save folder, then click **Connect**. USB baud is set to 115200; native
   USB throughput is not limited to that UART baud rate.
3. At boot the firmware automatically exports an existing SD `log.csv`. The app
   writes its exact bytes to a unique `.csv.part` file, checks the byte count and
   CRC32, flushes/syncs/closes it, renames it to `.csv`, then sends the required
   acknowledgement. Only then does the firmware clear the SD and start a new run.
   The first downloaded log might be an old one-sensor run; that is expected.
4. After 10–20 seconds click **Stop & download**. Both sensors stop, pending data
   is written, and the completed CSV is downloaded. This action **keeps** the SD
   copy. The app shows Acceleration, Angular velocity and Temperature tabs.
5. Click **New recording** for another run. It downloads the prior log again,
   confirms it is saved, then **deletes ALL contents of the SD card** and starts
   both sensors. Downloads have unique names and never overwrite earlier captures.

**Stop only** closes the SD log without transferring it. **Status** requests
current counters. **Open existing CSV** works without a connected Teensy and also
accepts the complete 13/14-column CSV files from ResetCsvLogger 1.x.
**Show saved files** opens the selected folder. The default is
`Documents/XParallaxLogs`. Change the folder while disconnected.

The app reconnects after a USB reset using the device serial number, when
available, or otherwise the originally selected COM port. It never connects to
an arbitrary replacement port. It does not reset/flush the serial input buffer,
so startup log bytes are not deliberately discarded.

## Graphs and validity

- Each of the X/Y/Z acceleration plots overlays SEN1 and SEN3, in g (axes may
  display the standard milli prefix at small amplitudes).
- Each of the X/Y/Z angular velocity plots overlays both sensors, in degrees/s.
- Temperature is shown separately in degrees C; missing temperature slots are
  NaN, never converted from the -128 sentinel or filled with fabricated values.
- Graphs display the **last downloaded/opened file**, not live sensor telemetry.
  The bottom console shows live recording/transfer status.
- Scroll to zoom; drag to pan. X axes within each tab are linked. Plot legends
  identify the physical connector, not a renumbered sensor.
- For legacy files or rejected FSYNC metadata, time is the shared Teensy **FIFO batch-read clock**, relative to the first
  available batch in the file. It is not the exact instant of each individual
  sample. The sensors are not hardware-synchronized, so these graphs do not
  establish precise phase/latency between sensors or exposure timing.
- All rows are analyzed for counts, sequence discontinuities, motion invalid
  sentinels, temperature availability, and firmware timestamp-anomaly flags.
  Reported Hz is estimated from sample count and MCU time span.
- Long logs use bounded min/max envelopes for plotting. Peaks are retained within
  display bins, but their exact within-bin time is not represented. Every raw
  sample remains in the CSV. This is not a PSD/spectral analysis tool.
- Legacy v1.1 `valid=0` temperature-only rows do not discard valid motion data:
  the app checks the actual channel sentinels independently.

## Transfer failures

The `.csv` extension means size/CRC verification and local save completed for
downloads made by this app. CRC detects transfer changes; it does **not** prove
correct SPI measurements. Manually opened files have no transport verification.

Failed/disconnected/truncated transfers remain `.csv.part`; no acknowledgement
is sent for them. The new firmware retains the SD log when an acknowledgement is
missing. It times out after 30 seconds and reports an error. Reconnect if needed,
wait for that error, then retry **Stop & download**. A timeout does not authorize
erasure. Disk write/sync failures also prevent acknowledgement.

Old ResetCsvLogger 1.x exports can be received, but that old firmware does not
wait for an application acknowledgement and does not implement DUMP/NEW. Install
DualCsvLogger 2.2 for those controls and for the retention-on-failure behavior.

## Protocol and source

`transfer.py` implements byte-exact framing and disk persistence. `connection.py`
runs USB serial on a worker thread. `data.py` loads CSV on another worker and
bounds plot memory. `app.py` contains the desktop UI.

Export format:

```text
=== LOG_CSV_BEGIN bytes=N ===\r\n
<exactly N raw file bytes>
\r\n=== LOG_CSV_END bytes=N crc32=XXXXXXXX ===\r\n
WAIT_LOG_ACK\r\n
```

Only after verification and persistence, the host sends:

```text
ACK_LOG N XXXXXXXX\n
```

CRC32 is IEEE/zlib, uppercase 8-digit hexadecimal in the acknowledgement. The
CRC covers the raw file only, including original line endings, not markers.
Commands are uppercase ASCII with a newline: `STOP`, `DUMP`, `NEW`, `STATUS`,
`HELP`. Firmware also supports `RESET`, but the app uses NEW without USB reboot.

## Development and checks

Install `requirements.txt` in Python 3.12; run `python app.py`.
`build.ps1` runs unit tests and creates the standalone EXE with PyInstaller.

Checks performed:

- Fragmented and multi-megabyte transfers, exact-byte preservation, CRC/length
  mismatch, failed disk sync, disconnect/retry and unique file naming.
- Serial worker receiving a simulated device stream and sending the ACK after
  a complete saved file. Tests never open a real COM port.
- Two-sensor CSV scaling, legacy format handling, missing-temperature masking,
  bounded peak-preserving display reduction, cancellation and malformed headers.
- GUI command buttons, all plot tabs, and an offscreen packaged-EXE startup test
  with synthetic data. `tests/demo.csv` is synthetic, not flight measurements.
- Arduino compile and native tests for the matching firmware.

Physical two-sensor SPI throughput, SD performance and real USB operation still
need the hardware bench test. No device was flashed or connected by this build.
