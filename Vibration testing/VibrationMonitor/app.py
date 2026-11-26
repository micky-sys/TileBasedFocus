from __future__ import annotations
import argparse
import os
from pathlib import Path
import sys
import time
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets as W
import pyqtgraph as pg
from serial.tools import list_ports
from connection import SerialSession
from data import load_csv

pg.setConfigOptions(antialias=False, background="#101b2c", foreground="#ccd8e9")
COLORS = {1: "#59d6bb", 2: "#f5cd72", 3: "#77acff", 4: "#ed96bf"}


class LoadJob(QtCore.QThread):
    loaded = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    progress = QtCore.Signal(int)
    def __init__(self, path, parent=None, window=None):
        super().__init__(parent)
        self.path = path
        self.window = window
    def run(self):
        try:
            result = load_csv(self.path, self.isInterruptionRequested, self.progress.emit, self.window)
            if not self.isInterruptionRequested():
                self.loaded.emit(result)
        except InterruptedError:
            pass
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("X-Parallax · Vibration Monitor")
        self.resize(1350, 900)
        self.setMinimumSize(1080, 760)
        self.settings = QtCore.QSettings("XParallax", "VibrationMonitor")
        default = Path(QtCore.QStandardPaths.writableLocation(QtCore.QStandardPaths.DocumentsLocation)) / "XParallaxLogs"
        self.folder = Path(self.settings.value("folder", str(default)))
        self.session = None
        self.load_job = None
        self.pending_load = None
        self.log_data = None
        self.last_file = None
        self.want_connection = False
        self.target_port = None
        self.target_serial = None
        self.firmware2 = False
        self.downloading = False
        self._build()
        self.refresh_ports()
        self.reconnect = QtCore.QTimer(self)
        self.reconnect.setInterval(2000)
        self.reconnect.timeout.connect(self.try_reconnect)
        self.reconnect.start()

    def _build(self):
        root = W.QWidget()
        self.setCentralWidget(root)
        outer = W.QVBoxLayout(root)
        outer.setContentsMargins(24, 20, 24, 16)
        title = W.QLabel("X-PARALLAX  /  VIBRATION MONITOR")
        title.setObjectName("title")
        outer.addWidget(title)
        subtitle = W.QLabel("Capture complete SD logs · Verify every transfer · Compare motion across sensors")
        subtitle.setObjectName("muted")
        outer.addWidget(subtitle)
        body = W.QHBoxLayout()
        outer.addLayout(body, 1)
        sidebar = W.QFrame()
        sidebar.setObjectName("panel")
        sidebar.setFixedWidth(292)
        side = W.QVBoxLayout(sidebar)
        side.setContentsMargins(18, 18, 18, 18)
        side.addWidget(W.QLabel("DEVICE"))
        self.port_combo = W.QComboBox()
        side.addWidget(self.port_combo)
        self.refresh_button = W.QPushButton("Refresh ports")
        self.refresh_button.clicked.connect(self.refresh_ports)
        side.addWidget(self.refresh_button)
        self.connect_button = W.QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.clicked.connect(self.toggle_connection)
        side.addWidget(self.connect_button)
        self.connection_label = W.QLabel("Disconnected · USB serial, 115200")
        self.connection_label.setWordWrap(True)
        side.addWidget(self.connection_label)
        hint = W.QLabel("Close Arduino Serial Monitor before connecting.\n\nSensor 1 → SEN1 / J1 / SPI0\nSensor 3 → SEN3 / J3 / SPI1\n16,000 samples/s per sensor")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        side.addWidget(hint)
        side.addSpacing(18)
        side.addWidget(W.QLabel("RECORDING"))
        self.download_button = W.QPushButton("Stop & download")
        self.download_button.clicked.connect(lambda: self.send("DUMP"))
        side.addWidget(self.download_button)
        self.new_button = W.QPushButton("New recording")
        self.new_button.clicked.connect(lambda: self.send("NEW"))
        side.addWidget(self.new_button)
        note = W.QLabel("New recording saves the old log first, then clears ALL SD contents and starts again.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        side.addWidget(note)
        row = W.QHBoxLayout()
        self.stop_button = W.QPushButton("Stop only")
        self.stop_button.clicked.connect(lambda: self.send("STOP"))
        self.status_button = W.QPushButton("Status")
        self.status_button.clicked.connect(lambda: self.send("STATUS"))
        row.addWidget(self.stop_button)
        row.addWidget(self.status_button)
        side.addLayout(row)
        side.addSpacing(18)
        side.addWidget(W.QLabel("SAVE LOCATION"))
        self.folder_label = W.QLabel(str(self.folder))
        self.folder_label.setWordWrap(True)
        self.folder_label.setObjectName("muted")
        side.addWidget(self.folder_label)
        self.folder_button = W.QPushButton("Choose folder…")
        self.folder_button.clicked.connect(self.choose_folder)
        side.addWidget(self.folder_button)
        open_folder = W.QPushButton("Show saved files")
        open_folder.clicked.connect(self.show_folder)
        side.addWidget(open_folder)
        open_file = W.QPushButton("Open existing CSV…")
        open_file.clicked.connect(self.open_file)
        side.addWidget(open_file)
        side.addStretch()
        body.addWidget(sidebar)
        content = W.QVBoxLayout()
        body.addLayout(content, 1)
        self.state_label = W.QLabel("Ready to connect")
        self.state_label.setObjectName("state")
        content.addWidget(self.state_label)
        self.transfer_label = W.QLabel("Downloads are saved without Serial Monitor truncation.")
        self.transfer_label.setWordWrap(True)
        content.addWidget(self.transfer_label)
        self.progress = W.QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        content.addWidget(self.progress)
        self.sensor_summary = W.QLabel("No log loaded. Graphs appear after a download or opening a CSV.")
        self.sensor_summary.setWordWrap(True)
        self.sensor_summary.setObjectName("summary")
        content.addWidget(self.sensor_summary)
        detail_row = W.QHBoxLayout()
        self.detail_button = W.QPushButton("Load visible range: individual samples")
        self.detail_button.clicked.connect(self.load_detail)
        self.overview_button = W.QPushButton("Full recording overview")
        self.overview_button.clicked.connect(lambda: self.open_log(self.log_data.path) if self.log_data else None)
        detail_row.addWidget(self.detail_button)
        detail_row.addWidget(self.overview_button)
        content.addLayout(detail_row)
        self.tabs = W.QTabWidget()
        self.plots = []
        for tab_name, axes, units in [("Acceleration", "XYZ", "g"), ("Angular velocity", "XYZ", "°/s"), ("Temperature", ["Die"], "°C")]:
            page = W.QWidget()
            layout = W.QVBoxLayout(page)
            previous = None
            for axis in axes:
                plot = pg.PlotWidget()
                plot.setLabel("left", f"{axis}", units=units)
                plot.setLabel("bottom", "Teensy batch-read time", units="s")
                plot.showGrid(x=True, y=True, alpha=0.15)
                plot.addLegend(offset=(8, 5))
                plot.setMinimumHeight(110)
                if previous is not None:
                    plot.setXLink(previous)
                previous = plot
                self.plots.append(plot)
                layout.addWidget(plot)
            self.tabs.addTab(page, tab_name)
        content.addWidget(self.tabs, 1)
        self.graph_note = W.QLabel("Graphs show the last loaded log, not a live stream. Sensors are not hardware-synchronized.")
        self.graph_note.setWordWrap(True)
        self.graph_note.setObjectName("muted")
        content.addWidget(self.graph_note)
        self.console = W.QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(400)
        self.console.setFixedHeight(105)
        self.console.setPlaceholderText("Device status and transfer verification")
        content.addWidget(self.console)
        self.setStyleSheet("""
            QMainWindow, QWidget { background: #0b1321; color: #dce6f3; font: 10pt 'Segoe UI'; }
            QLabel#title { font-size: 18pt; font-weight: 700; padding-bottom: 2px; }
            QLabel#muted { color: #93a7bf; font-size: 9pt; }
            QFrame#panel { background: #141f30; border: 1px solid #24344a; border-radius: 10px; }
            QFrame#panel QLabel { background: transparent; }
            QLabel#state { font-size: 16pt; font-weight: 600; padding-top: 6px; }
            QLabel#summary { background: #141f30; padding: 10px; border-radius: 6px; }
            QPushButton { background: #22364e; border: 1px solid #35516e; border-radius: 5px; padding: 7px 10px; }
            QPushButton:hover { background: #2d4865; }
            QPushButton:disabled { color: #617388; background: #182637; border-color: #253448; }
            QPushButton#primary { color: #08261e; background: #59d6bb; font-weight: 700; }
            QComboBox { background: #0b1321; border: 1px solid #35516e; padding: 7px; border-radius: 5px; }
            QTabWidget::pane { border: 1px solid #24344a; }
            QTabBar::tab { padding: 9px 18px; background: #141f30; color: #9aadc5; }
            QTabBar::tab:selected { background: #22364e; color: #75e5cd; }
            QPlainTextEdit { background: #0a101a; border: 1px solid #24344a; font: 9pt Consolas; }
            QProgressBar { border: none; background: #22364e; }
            QProgressBar::chunk { background: #59d6bb; }
        """)
        self.controls()

    def controls(self):
        connected = self.session is not None and self.session.isRunning()
        self.connect_button.setText("Disconnect" if self.want_connection else "Connect")
        self.port_combo.setEnabled(not self.want_connection)
        self.folder_button.setEnabled(not self.want_connection)
        self.refresh_button.setEnabled(not self.want_connection)
        for button in [self.download_button, self.new_button]:
            button.setEnabled(connected and self.firmware2 and not self.downloading)
        for button in [self.stop_button, self.status_button]:
            button.setEnabled(connected and not self.downloading)

    def refresh_ports(self):
        selected = self.port_combo.currentData()
        self.port_combo.clear()
        for port in sorted(list_ports.comports(), key=lambda p: p.device):
            self.port_combo.addItem(f"{port.device} · {port.description}", port.device)
            if port.device == selected:
                self.port_combo.setCurrentIndex(self.port_combo.count()-1)

    def choose_folder(self):
        folder = W.QFileDialog.getExistingDirectory(self, "Save vibration logs", str(self.folder))
        if folder:
            self.folder = Path(folder)
            self.folder_label.setText(folder)
            self.settings.setValue("folder", folder)

    def show_folder(self):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(self.folder)))
        except OSError as exc:
            self.show_error(str(exc))

    def toggle_connection(self):
        if self.want_connection:
            self.want_connection = False
            if self.session:
                self.session.stop()
            self.controls()
            return
        port = self.port_combo.currentData()
        if not port:
            self.show_error("Select a COM port first. Close Arduino Serial Monitor if it holds the port.")
            return
        self.target_port = port
        self.target_serial = next((p.serial_number for p in list_ports.comports() if p.device == port), None)
        self.want_connection = True
        self.start_connection(port)

    def start_connection(self, port):
        if self.session is not None:
            return
        self.firmware2 = False
        self.session = SerialSession(port, self.folder, self)
        self.session.event.connect(self.on_event)
        self.session.finished.connect(self.session_finished)
        self.session.start()
        self.state_label.setText("Connecting…")
        self.controls()

    def session_finished(self):
        if self.session:
            self.session.deleteLater()
        self.session = None
        self.downloading = False
        self.connection_label.setText("Waiting to reconnect…" if self.want_connection else "Disconnected")
        self.controls()

    def try_reconnect(self):
        if self.session is not None:
            # A first HELP may be ignored while firmware is waiting for an ACK
            # from a previous, interrupted connection. Retry identification.
            if self.want_connection and not self.firmware2 and not self.downloading:
                self.session.command("HELP")
            return
        if not self.want_connection:
            return
        ports = list(list_ports.comports())
        port = next((p.device for p in ports if self.target_serial and p.serial_number == self.target_serial), None)
        if not self.target_serial and any(p.device == self.target_port for p in ports):
            port = self.target_port
        if port:
            self.start_connection(port)

    def send(self, command):
        if self.session:
            self.session.command(command)
            self.console.appendPlainText("> " + command)
            if command in {"DUMP", "NEW"}:
                self.state_label.setText("Stopping / preparing download…")
                self.downloading = True
                self.controls()

    def on_event(self, kind, data):
        if kind == "connected":
            self.connection_label.setText(f"Connected · {data} · USB")
            self.state_label.setText("Connected — waiting for device")
            self.session.command("HELP")
        elif kind == "disconnected":
            self.console.appendPlainText("Disconnected; any incomplete download remains .part")
        elif kind == "line":
            self.console.appendPlainText(data)
            if "DualCsvLogger" in data or data.startswith("SENSOR_STATUS"):
                self.firmware2 = True
            device_fault = next((word.split("=", 1)[1] for word in data.split() if word.startswith("fault=")), "NONE")
            if data.startswith("CSV_STATUS") and device_fault != "NONE":
                self.state_label.setText("Recording stopped: " + device_fault)
                self.downloading = False
            elif data.startswith("RECORDING"):
                self.state_label.setText("Recording on SD · 2 sensors" if self.firmware2 else "Recording on SD")
                self.downloading = False
            elif "state=STOPPED" in data or data.startswith("DOWNLOAD_CONFIRMED"):
                self.state_label.setText("Stopped · log retained on SD")
                self.downloading = False
            elif data.startswith("CSV_STATUS") and "state=RECORDING" in data:
                self.state_label.setText("Recording on SD · 2 sensors" if self.firmware2 else "Recording on SD")
            elif data.startswith("FAIL") or data.startswith("DOWNLOAD_FAILED"):
                self.state_label.setText("Device reported an error — see status below")
                self.downloading = False
            elif data.startswith("ERASING"):
                self.state_label.setText("Preparing new recording · clearing SD")
            elif data.startswith("ResetCsvLogger"):
                self.console.appendPlainText("Legacy firmware: startup downloads supported; install DualCsvLogger 2.0 for Download/New controls.")
        elif kind == "begin":
            self.downloading = True
            self.state_label.setText("Receiving log…")
            self.transfer_label.setText(f"Receiving {data['bytes']/1e6:,.2f} MB · checking byte count and CRC32")
            self.progress.setValue(0)
        elif kind == "progress":
            self.progress.setValue(int(1000*data["received"]/max(data["bytes"], 1)))
        elif kind == "saved":
            self.downloading = False
            self.last_file = Path(data["path"])
            self.progress.setValue(1000)
            self.transfer_label.setText(f"Saved & verified · {self.last_file.name} · {data['bytes']/1e6:,.2f} MB · CRC {data['crc32']}")
            self.console.appendPlainText("VERIFIED " + str(self.last_file))
            self.open_log(self.last_file)
        elif kind == "error":
            self.downloading = False
            message = data["message"]
            if data.get("partial"):
                message += "\nPartial file: " + data["partial"]
            self.show_error(message)
        self.controls()

    def show_error(self, message):
        self.state_label.setText("Attention needed — see details below")
        self.console.appendPlainText("ERROR: " + message)

    def open_file(self):
        filename, _ = W.QFileDialog.getOpenFileName(self, "Open vibration log", str(self.folder), "CSV logs (*.csv)")
        if filename:
            self.open_log(Path(filename))

    def load_detail(self):
        if not self.log_data:
            return
        plot = self.plots[(0,3,6)[self.tabs.currentIndex()]]
        start, end = plot.viewRange()[0]
        if end-start > 3:
            self.show_error("Zoom to a span of 3 seconds or less, then load individual samples.")
            return
        self.open_log(self.log_data.path, (start,end))

    def open_log(self, path, window=None):
        if self.load_job:
            self.pending_load = (Path(path),window)
            self.load_job.requestInterruption()
            return
        self.sensor_summary.setText("Loading graphs… Full log stays on disk; plot memory is bounded.")
        self.load_job = LoadJob(path, self, window)
        self.load_job.loaded.connect(self.show_log)
        self.load_job.failed.connect(self.show_error)
        self.load_job.finished.connect(self.load_finished)
        self.load_job.start()

    def load_finished(self):
        self.load_job.deleteLater()
        self.load_job = None
        if self.pending_load:
            path, self.pending_load = self.pending_load, None
            self.open_log(*path)

    def show_log(self, result):
        self.log_data = result
        summary = []
        for sensor in result.sensors.values():
            duration = (sensor.last_us - sensor.first_us)/1e6 if sensor.first_us is not None else 0
            summary.append(f"SEN{sensor.id} / J{sensor.id}  •  {duration:.3f} s  •  {sensor.count:,} samples  •  {sensor.rate:,.0f} Hz  •  motion invalid {sensor.motion_invalid:,}  •  sequence breaks {sensor.sequence_errors:,}  •  timestamp flags {sensor.timestamp_anomalies:,}  •  temp missing {sensor.temp_missing:,}")
        summary.append(f"File: {result.path.name}  |  End: {result.end_reason}")
        summary.append(result.timing_note)
        if result.synchronized:
            summary.append("Samples outside the first/last FSYNC anchors are excluded from graphs: " +
                           ", ".join(f"SEN{s.id}: {s.unanchored}" for s in result.sensors.values()))
        self.sensor_summary.setText("\n".join(summary))
        self.sensor_summary.setStyleSheet("color: #ffc56e;" if not result.clean_stop else "")
        for channel, plot in enumerate(self.plots):
            plot.setLabel("bottom", "FSYNC-aligned sample time" if result.synchronized else "Teensy batch-read time", units="s")
            plot.clear()
            plot.plotItem.legend.clear()
            for sensor in result.sensors.values():
                x, y = sensor.plot.curve(channel, result.origin)
                style = dict(pen=pg.mkPen(COLORS[sensor.id], width=1))
                if channel == 6:
                    # Alternating missing slots leave no adjacent finite pair.
                    # Mark observed temperatures instead of interpolating gaps.
                    style = dict(pen=None, symbol="o", symbolSize=3, symbolPen=None, symbolBrush=COLORS[sensor.id])
                    observed = np.isfinite(x) & np.isfinite(y)
                    x, y = x[observed], y[observed]
                curve = plot.plot(x, y, name=f"SEN{sensor.id} / J{sensor.id}", connect="finite", **style)
                curve.setDownsampling(auto=True, method="peak")
                curve.setClipToView(True)
            plot.enableAutoRange()
            if result.window is not None:
                plot.setXRange(*result.window, padding=0)
        reduced = any(s.plot.stride > 1 for s in result.sensors.values())
        self.graph_note.setText(("FSYNC common time reference; camera exposure not recorded; filter delay not corrected. " if result.synchronized else "Batch time only; not suitable for exposure alignment. ") +
                               ("Overview uses min/max bins. Zoom, then load visible range for individual samples." if reduced else "Individual samples shown. Scroll to zoom; drag to pan. Temperature gaps remain missing."))

    def closeEvent(self, event):
        self.want_connection = False
        if self.session:
            self.session.stop()
            if not self.session.wait(4000):
                self.show_error("Waiting for serial I/O to close; try closing again shortly.")
                event.ignore()
                return
        if self.load_job:
            self.load_job.requestInterruption()
            if not self.load_job.wait(4000):
                event.ignore()
                return
        event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--open", type=Path)
    parser.add_argument("--smoke-test", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    app = W.QApplication(sys.argv[:1])
    # Qt's offscreen backend does not enumerate Windows system fonts. This also
    # makes automated visual checks use the same readable font as the real app.
    if not QtGui.QFontDatabase.families():
        for name in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
            font = Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts"/name
            if font.exists():
                QtGui.QFontDatabase.addApplicationFont(str(font))
    app.setFont(QtGui.QFont("Segoe UI", 10))
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    if args.open or args.smoke_test:
        window.open_log(args.open or args.smoke_test)
    if args.smoke_test:
        deadline = time.monotonic()+60
        def check():
            if window.log_data is not None:
                assert len(window.plots) == 7
                assert window.log_data.sensors
                for plot in window.plots:
                    assert len(plot.listDataItems()) == len(window.log_data.sensors)
                for sensor, item in zip(window.log_data.sensors.values(), window.plots[6].listDataItems()):
                    assert np.isfinite(item.yData).all()
                    if sensor.plot.stride == 1:
                        assert len(item.yData) == np.count_nonzero(np.isfinite(sensor.plot.low[:, 6]))
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(args.screenshot))
                app.exit(0)
            elif time.monotonic() > deadline:
                app.exit(2)
        timer = QtCore.QTimer()
        timer.timeout.connect(check)
        timer.start(200)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
