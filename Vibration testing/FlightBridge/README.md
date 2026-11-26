# FlightBridge — X-Parallax

Small Windows application for GPS forwarding and Teensy recorder control.
The interface is in English to keep port names, numbers and controls readable.

## Run

Open `FlightBridge.exe` from the release folder. The EXE includes Python, Tk and
pySerial; Python is not required on the flight computer. It is an unsigned local
build, not an installer. Serial/USB drivers and a virtual COM pair are separate.

For a hardware-free test, select **Demo — no hardware**, then **Connect**.
The app generates simulated NMEA, and a simulated Teensy acknowledges the controls.
Demo never opens a real COM port and never records sensor data.

## Connect to the flight computer

1. Install a virtual null-modem driver supported by your Windows installation,
   such as [com0com](https://com0com.sourceforge.net/), and create a COM pair.
   Example only: **COM10 ↔ COM11**. Both ends must be distinct, unused ports.
   FlightBridge does not install drivers, change driver-signing settings, or create
   the pair itself. Do not disable Windows security to accommodate an old driver.
2. Close any program currently holding the physical GPS source port.
3. In FlightBridge select the GPS source COM port and its actual baud rate.
   All three ports default to **115200 baud**; subsequently saved selections are restored.
4. Select **COM10** as the navigation output. In the navigation application select
   the other end, **COM11**, with matching serial settings. Do not select COM10 in both.
5. Select the physical RS-232 port connected to the Teensy hub; default **115200 baud**.
   The Teensy `Serial1` UART must use that same baud rate. This is the hub's MAX3232
   connection, not a direct RS-232 voltage connection to a GPIO.
6. All ports use **8N1**, with no software or hardware flow control. Click **Connect**.
   GPS and navigation baud rates are independent; each must match its corresponding
   peer. Prefer a Teensy baud rate higher than the GPS source baud rate to leave
   capacity for command messages.
7. FlightBridge requests recorder status. Start/Arm becomes available only after
   the device confirms `IDLE`. Use **Check health** before a recording session.

The source port is read only. Navigation output is one way: navigation software
commands are not routed back to GPS. If your navigation software requires GPS
configuration/query traffic in both directions, that requires a further extension.

## Two recording modes

The **SPI cable test** button is a separate idle-only diagnostic (firmware
1.1.0+). It tests all four sensors at requested clocks of 4/8/12/16 MHz and logs
per-sensor error counts. **SPI results** retrieves the latest run;
**Stop SPI test** cancels and waits for sensor cleanup. No SD card is required.
See [the diagnostic guide](../firmware/SPI_TEST.md) for acceptance criteria and
the subsequent full-rate recording check. The diagnostic does not automatically
change the recording clock. Demo mode cannot run this hardware test.

**Triggered**: click **Arm trigger capture**. After the Teensy replies `ARMED`, it
waits for the electrical trigger on its own input and captures each exposure event.
The PC does not issue a timing-critical Start command for each photograph. Keep a
pre-trigger ring buffer and a post-exposure margin in the logger firmware. The
recording window must include the last camera's exposure and account for sensor
filter delay. A completed burst returns to/stays `ARMED`, ready for the next event.

**Continuous**: click **Start continuous recording**. The Teensy records uninterrupted
sensor samples to its SD card until **Stop / disarm**. This is the data source for
later vibration spectra/PSD analysis. Preserve sample timestamps, configured sample
rate, filter settings, axis mapping, ranges, and any FIFO/dropout indicators.
Capture the trigger and strobe timestamps in this mode too, so camera events can be
correlated with the continuous signal.

**Stop / disarm** terminates either mode. To change modes, stop first. Disconnecting
or closing the PC app does **not** automatically stop/disarm the Teensy. The UI asks
before closing a session whose recorder is armed, recording, or in an unknown state.

The Windows application sends the mode commands and displays confirmed states.
Actual IMU acquisition, pre-trigger buffering and SD recording are implemented in
the companion `../firmware/VibrationLogger` project; see `../firmware/README.md`.
PSD computation remains downstream analysis. Sampling/window settings are currently
firmware configuration, not PC controls.

## Data handling and faults

- Navigation receives the original GPS bytes unchanged, including original line endings.
- Teensy receives complete ASCII `$...` and `!...` sentences, with CRLF terminators.
  Sentence content is unchanged. Bad/missing checksums are counted but forwarded,
  allowing the device to decide its own GPS validity policy. Binary, non-NMEA and
  oversize lines (over 1024 characters) are omitted only from the Teensy stream.
- Control frames start with `@XP,1,` and carry a checksum and request ID. They are
  inserted only between whole NMEA lines. One writer owns the Teensy output, preventing
  command/GPS byte interleaving.
- Each output has an independent bounded queue. A slow or disconnected Teensy does
  not block navigation. A slow or disconnected navigation output does not block Teensy.
- An output timeout/overflow is a visible **FAULT**, not silent dropping. The failed
  output stays stopped; the other links continue. Use Disconnect/Connect to recover.
  Reconnection is manual and does not replay old GPS sentences or commands.
- Byte counters measure bytes accepted by the OS serial driver, not end-to-end
  consumption by the navigation app. Only the Teensy control protocol has acknowledgements.
- Command timeout is 3 seconds after transmission is initiated (5 seconds if never
  dequeued). A timeout means **outcome unknown**, not necessarily command failure.
  No automatic retry is sent. Query status before deciding what to do next.
- The app does not stream high-rate IMU samples over RS-232. GPS/control share this
  UART; sensor samples are intended to remain in local SD logging on the Teensy.
- Windows receive timestamps and serial GPS arrival times must not be used as camera
  exposure timestamps. Capture trigger/strobe edges on the Teensy using its local clock.

Settings and rolling event logs are stored in `%LOCALAPPDATA%\XParallax\FlightBridge`.
The logs contain commands, replies and faults; they are not a raw GPS/sensor recording.
Settings are saved, but the app never connects or arms automatically on startup.

## Teensy integration

See `PROTOCOL.md` and the full companion firmware in
`../firmware/VibrationLogger/VibrationLogger.ino`. It uses Serial1 RX=0, TX=1 at
115200 baud and implements ARM/START/STOP/STATUS/HEALTH with local SD acquisition.
The older `teensy/FlightBridgeProtocol` sketch remains a protocol-only example;
use VibrationLogger for the experiment. Read its bench acceptance procedure first.

## Development and validation

Python 3.12 or later:

```powershell
python -m pip install -r requirements.txt
python app.py
python app.py --demo
python -m unittest discover -s tests -v
python tests/ui_smoke.py
```

Build with `build.ps1` after installing `PyInstaller==6.20.0`. Output is
`release/FlightBridge.exe`. The tests use in-memory serial endpoints and the actual
Tk GUI. Physical RS-232 adapters, com0com driver behavior, GPS hardware, navigation
software compatibility and the actual Teensy recorder require a bench integration test.

References: [pySerial API](https://pyserial.readthedocs.io/en/stable/pyserial_api.html),
[com0com project](https://com0com.sourceforge.net/),
[Welch PSD estimation](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html).
