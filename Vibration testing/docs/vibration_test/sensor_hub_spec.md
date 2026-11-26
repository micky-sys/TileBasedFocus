# X-Parallax vibration hub: verified configuration for Cursor

Codex, 2026-11-16. Answers to the eight hardware questions in vibration_test_brief.md.
This describes the current source and supplied Gerbers, not a completed physical acceptance test.

## 1. Sensors, ranges and useful sensitivity

Four ICM-42688-P six-axis IMUs. Current firmware: gyro +/-2000 degrees/s,
accelerometer +/-16 g, 16-bit FIFO axes. Gyro scale 2000/32768 degrees/s/count;
acceleration scale 1/2048 g/count. Gyro quantization step is 0.0610 degrees/s.
Keep range headroom for deliberate scanning initially; reduce the gyro range only
after measuring scan peaks, updating metadata/scaling and checking clipping.

TDK specifies typical gyro noise density 0.0028 degrees/s/sqrt(Hz), and 0.028
degrees/s RMS at 100 Hz bandwidth. A white-noise estimate at 1000 Hz equivalent
noise bandwidth is 0.0885 degrees/s RMS per gyro, NOT <=0.05. Two independent
gyros subtracted have about 0.125 degrees/s RMS at that bandwidth before other
errors. The 0.05 target would require about 319 Hz equivalent bandwidth under
that approximation. Bandwidth must follow the measured spectrum, not be reduced
solely to satisfy a noise target. These are estimates, not measured board noise.

Primary source: [TDK DS-000347 rev 1.6](https://product.tdk.com/system/files/dam/doc/product/sensor/mortion-inertial/imu/data_sheet/ds-000347-icm-42688-p-v1.6.pdf).

## 2. Acquisition rate and filtering

Configured 16,000 samples/s PER SENSOR, simultaneous gyro+accelerometer operation
inside each IMU, 16-byte FIFO packets. Two SPI buses, two sensors on each, 16 MHz
SPI. Each sensor's FIFO is serviced every ~1 ms; this is not its sampling period.
UI filter is second order, nominal ODR/4 = 4 kHz. Factory anti-alias/notch settings
are preserved and register values logged. Effective response and physical-motion
to-output group delay need characterization. Do not equate 4 kHz UI setting with
a guaranteed flat 4 kHz measurement bandwidth.

Chip supports up to 32 ksps, but the implemented four-sensor logger is 16 ksps;
neither a four-sensor maximum nor sustained operation has been bench-verified.
Native Teensy SDIO is separate from both sensor SPI buses. Approximate continuous
file rate 1.2 MB/s; RAM buffering ~230 ms at normal load. Logs record overruns,
invalid/saturated samples, service timing, FIFO and queue peaks. Compile/tests
passed during firmware development; that does not certify cables, SD or hardware.

## 3. Synchronization

Sensors run independent internal clocks. INT2 wires are configured as FSYNC INPUTS
to the sensors, driven by Teensy outputs. Each sensor gets a timestamped FSYNC
marker approximately every 10 ms. FIFO tags and elapsed timestamps anchor samples
to MCU time; decoder interpolates between anchors and labels timing quality.
This does not force simultaneous sample phases or implement a common sample clock.
Use reconstructed sample time, never FIFO-read time, and resample into a common
time frame before comparing channels. Do not claim a verified 62.5 us timing
accuracy merely because that is the nominal sample period.

## 4. Camera timing and MCU clock

Trigger GPIO14 from 3.3 V ClearCore breakout. Camera strobes: GPIO35,34,33 via
three SN74LVC1G17 buffers supplied at 3.3 V; the designated strobe inputs accept
5 V logic. The direct trigger GPIO must remain 3.3 V compatible, not RS-232 levels.
Both rising/falling levels are recorded; default active-high windows are configurable.
GPIO interrupt timestamps have 1 us representation in an extended boot-relative
micros() counter. This is not a measured 1 us accuracy, GPS PPS lock, or ISR-latency
guarantee. NMEA arrival timestamps are not precise exposure timing.

Calibrate strobe semantics/width against actual camera exposure, measure timestamp
latency under recording load, and account for gyro filtering delay. No physical
camera-serial-to-strobe-port mapping has yet been confirmed. Label it on installation.
16 ksps gives ~4 samples in 250 us, ~8 in 500 us. Reconstruct the trajectory during
the window; endpoint displacement alone can miss blur from reversals.

## 5. Files, units and axes

Native SD .XPL files contain CRC32/sequence-protected binary records, raw FIFO,
FSYNC anchors, trigger/strobe edges, GPS, metadata, capture-window markers and
health statistics. Decoder exports four sensor CSVs, events.csv, gps.csv and
summary.json. Preserve all segments. File metadata includes ranges/filter setup.
No automatic bias removal or gravity subtraction. Axes are IMU package axes,
NOT flight axes. Record each board's orientation and mounting transform separately.
Confirm signs using gravity and known positive rotations on the bench; do not
infer axes from the cable exit alone.

Source and decoder: C:\Users\User\Documents\ChatGPT\Parallax\firmware
Detailed authoritative format: firmware/FORMAT.md; behavior: firmware/README.md.
Channel mapping (Teensy GPIO numbers, not header-pad numbers):

- J1/S1: SPI0 clock13/MOSI11/MISO12, CS10, INT1=2, FSYNC6.
- J2/S2: same bus, CS9, INT1=3, FSYNC7.
- J3/S3: SPI1 clock27/MOSI26/MISO39, CS38, INT1=23, FSYNC21.
- J4/S4: same bus, CS37, INT1=22, FSYNC32.
- UART Serial1 RX0/TX1 through MAX3232, 115200 8N1.
- Camera1/J7 ->35; Camera2/J11 ->34; Camera3/J12 ->33.

## 6. Sensor cables and supply

Ten-pin 2x5 IDC ribbon, approximately 20 cm. Previously selected assembly:
Samtec IDSD-05-D-08.00-G (8 inches = 203.2 mm); verify installed length and part
against purchase. Sensor connector pins: 1=3.3V,2=GND,3=MISO,4=INT2/FSYNC,
5=INT1,6=GND,7=SCLK,8=GND,9=CS,10=MOSI. GPIO uses 3.3 V levels.
The IMU alone is ~0.88 mA typical in six-axis low-noise operation; board LED and
other loads add current. No measured assembled-board maximum or cable margin yet.
Measure actual route including the moving service loop before freezing hub position.
Do not tension a ribbon across the pivot, bypass isolators with a taut harness,
or lengthen cables without rechecking SPI signals at the sensor.

## 7. Sensor mechanical interface

Re-read the actual supplied Gerber_Vibration-sensor_PCB_Vibration-sensor_2026-10-29.zip.
Outline 21 x 21 mm; THREE 2.2 mm non-plated mounting holes for M2 clearance.
Using the Gerber origin and looking from the top, x right and y down, hole centers
in mm: (2.2,18.5), (10.5,2.2), (18.8,18.5). This is the PCB pattern, NOT a drilling
instruction for an optical assembly. Gerber internal timestamp is Oct28 despite
the Oct29 archive name. Assembled mass and board thickness are not verified.
Weigh PCB + connector + fasteners + moving cable portion; evaluate added inertia,
not just mass. A short rigid adapter must support the board without bending it,
clear solder joints and use existing approved mechanical attachment points.
Do not put hot glue/foam tape in the vibration measurement load path. Fastener
locking does not replace the bearing area or electrical clearance of a washer.

Two anchor points with a free, gentle cable loop are needed: one on the moving
carrier after the connector, one on stationary structure. Verify full sweep both
ways. Merely placing a fixed anchor within a few cm does NOT guarantee low torque.
Compare response with/without the sensor harness in a repeatable ground condition.

## 8. Actual mirror drive and optical structure

Recovered from user-confirmed X-Parallax Project Summary (Oct6-8; focal lengths
reconfirmed Nov16): TWO back-to-back oblique mirrors on one carrier, one per
400 mm camera. Direct-driven by a Teknic ClearPath servo about flight Z. Nadir
uses a 300 mm lens and a separate mirror driven through motor arm/conrod about
flight X. Do not describe the oblique drive as a geared stepper. Focus drives
are separate geared brushed motors; their backlash is not scan-drive backlash.
Both mirror axes are commanded to stop for exposure. Installed motor settings
were confirmed in the summary; actual holding torque, servo residual dynamics,
bearing compliance and exposure settling have not been measured.

Flight frame: X forward, Y left, Z up. Serial2439006=tail;2439007=nose;2439008=nadir.
Two oblique mirrors are Edmund84-418; nadir moving mirror70-937. The common
oblique camera support was named A15 / XPL-P-102047 in the earlier review.
The CAD dummy curtain is not installed. Mechanical views available locally:
C:\Users\User\Downloads\Parallax general view.jpg and Parallax isolator concept.pdf.

## Placement status

Round-1 agreement is on four bodies: moving oblique carrier; stationary oblique
bearing support; tail camera/lens assembly; isolated upper plate at the oblique
support root. This isolates relative motions but does not cover every failure
mode. It cannot rule out individual glass flex or internal lens motion. Exact
attachment faces and optical clearances remain under model inspection. A second
run can move the upper-plate sensor below an isolator, or to the nose camera,
depending on the first-run evidence. Do not claim simultaneous input/output
isolator transmissibility without measuring both sides together.
