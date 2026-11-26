"""FlightBridge desktop application. Run with --demo to test without serial hardware."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from serial.tools import list_ports
from bridge import Bridge, BridgeConfig, PortConfig
from demo import Demo

APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "XParallax" / "FlightBridge"
BAUDS = ("4800", "9600", "19200", "38400", "57600", "115200", "230400", "460800", "921600")


class FlightBridgeApp:
    def __init__(self, root, demo=False):
        self.root = root
        self.bridge = None
        self.simulator = None
        self.busy = False
        self.closing = False
        self.jobs = queue.Queue()
        self.inputs = []
        self.port_vars = {}
        self.baud_vars = {}
        self.command_buttons = []
        self.demo_var = tk.BooleanVar(value=demo)
        self.mode_var = tk.StringVar(value="Triggered")
        self.status_var = tk.StringVar(value="Disconnected — select ports, then connect.")
        self.notice_var = tk.StringVar()
        self.recording_var = tk.StringVar(value="UNKNOWN")
        self.command_var = tk.StringVar(value="No command sent")
        self.counter_vars = {name: tk.StringVar(value="—") for name in ("gps", "nav", "teensy")}
        self.line_var = tk.StringVar(value="Waiting for GPS data…")
        self.quality_var = tk.StringVar()
        self.logger = logging.getLogger("flightbridge")
        self.logger.setLevel(logging.INFO)
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(APP_DIR / "events.log", maxBytes=2_000_000,
                                          backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            self.logger.addHandler(handler)
        except OSError:
            self.notice_var.set("Event log is unavailable: cannot write the application data folder.")
        self.preferences = self.load_preferences()
        self.build_ui()
        self.refresh_ports()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.tick)

    def build_ui(self):
        root = self.root
        root.title("X-Parallax · FlightBridge")
        width = min(1080, root.winfo_screenwidth() - 60)
        height = min(820, root.winfo_screenheight() - 90)
        root.geometry(f"{max(950, width)}x{max(650, height)}")
        root.minsize(950, 650)
        root.configure(bg="#eef2f6")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background="#eef2f6")
        style.configure("TLabel", background="#eef2f6", foreground="#172b42", font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel", foreground="#526477")
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 7))
        style.configure("Accent.TButton", background="#126b73", foreground="white")
        style.map("Accent.TButton", background=[("active", "#0b5960"), ("disabled", "#c4d1d8")])
        style.configure("TCheckbutton", background="#eef2f6", font=("Segoe UI", 10))
        style.configure("TRadiobutton", background="#eef2f6", font=("Segoe UI", 10))
        style.configure("TLabelframe", background="#eef2f6", bordercolor="#c9d3de")
        style.configure("TLabelframe.Label", background="#eef2f6", font=("Segoe UI", 10, "bold"))
        style.configure("State.TLabel", font=("Segoe UI", 15, "bold"), foreground="#126b73")
        style.configure("Notice.TLabel", foreground="#ad3b1c")

        main = ttk.Frame(root, padding=16)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(7, weight=1)
        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(header, text="FlightBridge", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="X-PARALLAX  /  GPS + RECORDER CONTROL", style="Sub.TLabel").pack(side="right")

        portbox = ttk.LabelFrame(main, text="Serial connections", padding=12)
        portbox.grid(row=1, column=0, sticky="ew")
        portbox.columnconfigure(1, weight=1)
        for column, text in enumerate(("Route", "COM port", "Baud rate", "Link / bytes sent or received")):
            ttk.Label(portbox, text=text, style="Sub.TLabel").grid(row=0, column=column, sticky="w", padx=6)
        for row, (name, title, default) in enumerate([
            ("gps", "GPS source →", "115200"), ("nav", "→ Navigation (virtual)", "115200"),
            ("teensy", "↔ Teensy (physical)", "115200")], 1):
            self.port_vars[name] = tk.StringVar(value=self.preferences.get(name + "_port", ""))
            self.baud_vars[name] = tk.StringVar(value=self.preferences.get(name + "_baud", default))
            ttk.Label(portbox, text=title).grid(row=row, column=0, sticky="w", padx=6, pady=8)
            port = ttk.Combobox(portbox, textvariable=self.port_vars[name], width=38)
            port.grid(row=row, column=1, sticky="ew", padx=6)
            baud = ttk.Combobox(portbox, textvariable=self.baud_vars[name], values=BAUDS, width=11)
            baud.grid(row=row, column=2, padx=6)
            self.inputs.extend([port, baud])
            ttk.Label(portbox, textvariable=self.counter_vars[name], width=30).grid(row=row, column=3, sticky="w", padx=6)
        ttk.Label(portbox, text="Virtual pair example: this app opens COM10; navigation software opens COM11. "
                  "Install/configure the pair separately.", style="Sub.TLabel", wraplength=940).grid(
                      row=4, column=0, columnspan=4, sticky="w", padx=6, pady=(8, 0))
        ttk.Label(portbox, text="All links: 8N1, no flow control. Only this app should open the GPS source port.",
                  style="Sub.TLabel").grid(row=5, column=0, columnspan=4, sticky="w", padx=6, pady=(4, 0))

        toolbar = ttk.Frame(main)
        toolbar.grid(row=2, column=0, sticky="ew", pady=12)
        self.connect_button = ttk.Button(toolbar, text="Connect", style="Accent.TButton", command=self.connect)
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(toolbar, text="Disconnect", command=self.disconnect, state="disabled")
        self.disconnect_button.pack(side="left", padx=8)
        self.refresh_button = ttk.Button(toolbar, text="Refresh ports", command=self.refresh_ports)
        self.refresh_button.pack(side="left")
        self.demo_check = ttk.Checkbutton(toolbar, text="Demo — no hardware", variable=self.demo_var)
        self.demo_check.pack(side="right")

        recorder = ttk.LabelFrame(main, text="Teensy recorder", padding=12)
        recorder.grid(row=3, column=0, sticky="ew")
        top = ttk.Frame(recorder)
        top.pack(fill="x")
        self.recording_label = ttk.Label(top, textvariable=self.recording_var, style="State.TLabel")
        self.recording_label.pack(side="left")
        ttk.Label(top, textvariable=self.command_var, wraplength=700).pack(side="right")
        modes = ttk.Frame(recorder)
        modes.pack(fill="x", pady=(8, 0))
        self.mode_buttons = []
        for mode, label in [("Triggered", "Triggered — capture each exposure event"),
                            ("Continuous", "Continuous — vibration / PSD")]:
            radio = ttk.Radiobutton(modes, text=label, variable=self.mode_var, value=mode,
                                    command=self.update_controls)
            radio.pack(side="left", padx=(0, 20))
            self.mode_buttons.append(radio)
        buttons = ttk.Frame(recorder)
        buttons.pack(fill="x", pady=(10, 0))
        for command, title in [("START", "Start recording"), ("STOP", "Stop recording"),
                               ("STATUS", "Get status"), ("HEALTH", "Check health"),
                               ("SPITEST", "SPI cable test"), ("SPITESTRESULT", "SPI results")]:
            button = ttk.Button(buttons, text=title, command=lambda c=command: self.send(
                "ARM" if c == "START" and self.mode_var.get() == "Triggered" else c), state="disabled")
            button.pack(side="left", padx=(0, 8))
            self.command_buttons.append((command, button))
        ttk.Label(recorder, text="State changes require a valid reply from the Teensy. "
                  "Triggered capture uses the Teensy's hardware trigger input.", style="Sub.TLabel").pack(anchor="w", pady=(8, 0))

        gpsbox = ttk.LabelFrame(main, text="Latest GPS sentence", padding=10)
        gpsbox.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(gpsbox, textvariable=self.line_var, font=("Consolas", 10), wraplength=950).pack(anchor="w")
        ttk.Label(gpsbox, textvariable=self.quality_var, style="Sub.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(main, textvariable=self.status_var, wraplength=980).grid(row=5, column=0, sticky="w", pady=(10, 2))
        ttk.Label(main, textvariable=self.notice_var, style="Notice.TLabel", wraplength=980).grid(row=6, column=0, sticky="w", pady=(0, 6))

        logs = ttk.Frame(main)
        logs.grid(row=7, column=0, sticky="nsew")
        self.log = tk.Text(logs, height=4, wrap="word", bg="#13273b", fg="#e6eef5", relief="flat",
                           font=("Consolas", 10), padx=10, pady=8, state="disabled")
        scroll = ttk.Scrollbar(logs, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(fill="both", expand=True)
        self.log.tag_configure("ERROR", foreground="#ffad9d")
        self.log.tag_configure("WARNING", foreground="#f8d18d")
        self.log.tag_configure("ACK", foreground="#80ddc4")
        footer = ttk.Frame(main)
        footer.grid(row=8, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(footer, text="v1.1  •  GPS is forwarded one way; recorder replies stay here.", style="Sub.TLabel").pack(side="left")
        ttk.Button(footer, text="Open event logs", command=lambda: os.startfile(str(APP_DIR))).pack(side="right")

    def load_preferences(self):
        try:
            value = json.loads((APP_DIR / "settings.json").read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_preferences(self):
        values = {}
        for name in self.port_vars:
            values[name + "_port"] = self.port_vars[name].get()
            values[name + "_baud"] = self.baud_vars[name].get()
        try:
            temp = APP_DIR / "settings.tmp"
            temp.write_text(json.dumps(values, indent=2), encoding="utf-8")
            temp.replace(APP_DIR / "settings.json")
        except OSError as error:
            self.append_log(time.time(), "WARNING", f"Could not save settings: {error}")

    def refresh_ports(self):
        try:
            choices = [f"{p.device} — {p.description}" for p in sorted(list_ports.comports())]
            for combo in self.inputs[::2]:
                combo.configure(values=choices)
            if not choices:
                self.status_var.set("No COM ports detected. Connect an adapter, configure a virtual pair, or use Demo.")
        except Exception as error:
            self.status_var.set(f"Port discovery failed: {error}")

    def configuration(self):
        if self.demo_var.get():
            return BridgeConfig(PortConfig("DEMO_GPS", 115200), PortConfig("DEMO_NAV", 115200),
                                PortConfig("DEMO_TEENSY", 115200))
        values = []
        for name in ("gps", "nav", "teensy"):
            port = self.port_vars[name].get().split(" — ")[0].strip()
            values.append(PortConfig(port, int(self.baud_vars[name].get())))
        config = BridgeConfig(*values)
        config.validate()
        return config

    def connect(self):
        if self.busy or self.bridge:
            return
        try:
            config = self.configuration()
        except ValueError as error:
            messagebox.showerror("Serial configuration", str(error), parent=self.root)
            return
        self.save_preferences()
        self.busy = True
        demo = self.demo_var.get()
        self.status_var.set("Opening serial connections…")
        self.notice_var.set("")
        self.update_controls()
        def work():
            simulator = Demo() if demo else None
            bridge = Bridge(config, opener=simulator.open) if simulator else Bridge(config)
            try:
                bridge.start()
                if simulator:
                    simulator.start()
                self.jobs.put(("connected", bridge, simulator))
            except Exception as error:
                self.jobs.put(("error", str(error), None))
        threading.Thread(target=work, daemon=True).start()

    def disconnect(self, force=False):
        if self.busy or not self.bridge:
            return
        state = self.bridge.snapshot()["recording"]
        if not force and state in {"ARMED", "RECORDING", "TESTING", "UNKNOWN"}:
            if not messagebox.askyesno("Disconnect", "The Teensy may still be armed, recording or testing.\n"
                                      "Disconnect without sending Stop?", parent=self.root):
                return
        self.busy = True
        bridge, simulator = self.bridge, self.simulator
        self.update_controls()
        def work():
            bridge.stop()
            if simulator:
                simulator.stop()
            self.jobs.put(("disconnected", None, None))
        threading.Thread(target=work, daemon=True).start()

    def send(self, command):
        if self.bridge:
            try:
                self.bridge.send_command(command)
            except (ValueError, RuntimeError) as error:
                self.append_log(time.time(), "ERROR", str(error))
            self.update_controls()

    def update_controls(self):
        idle = not self.bridge and not self.busy
        for widget in self.inputs + [self.refresh_button, self.demo_check, self.connect_button]:
            widget.configure(state="normal" if idle else "disabled")
        self.disconnect_button.configure(state="normal" if self.bridge and not self.busy else "disabled")
        status = self.bridge.snapshot() if self.bridge else {}
        for radio in self.mode_buttons:
            radio.configure(state="normal" if not self.busy and status.get("recording", "IDLE") == "IDLE"
                            and not status.get("pending") else "disabled")
        for command, button in self.command_buttons:
            enabled = not self.busy and status.get("teensy") == "Connected" and not status.get("pending")
            if command == "START":
                enabled = enabled and status.get("recording") == "IDLE"
                button.configure(text="Arm trigger capture" if self.mode_var.get() == "Triggered" else "Start continuous recording")
            if command == "STOP":
                button.configure(text="Stop SPI test" if status.get("recording") == "TESTING" else "Stop / disarm")
            if command == "SPITEST":
                enabled = enabled and status.get("recording") == "IDLE"
            if command == "SPITESTRESULT":
                enabled = enabled and status.get("recording") in {"IDLE", "TESTING", "ERROR"}
            if command == "HEALTH":
                enabled = enabled and status.get("recording") != "TESTING"
            button.configure(state="normal" if enabled else "disabled")

    def append_log(self, when, level, message):
        self.logger.info("%s %s", level, message)
        stamp = time.strftime("%H:%M:%S", time.localtime(when))
        self.log.configure(state="normal")
        self.log.insert("end", f"{stamp}  {level:<7} {message}\n", level)
        if int(self.log.index("end-1c").split(".")[0]) > 1000:
            self.log.delete("1.0", "101.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def tick(self):
        try:
            while True:
                result, value, simulator = self.jobs.get_nowait()
                self.busy = False
                if result == "connected":
                    self.bridge, self.simulator = value, simulator
                    self.append_log(time.time(), "INFO", "DEMO SESSION — simulated GPS and recorder" if simulator else "Hardware session connected")
                    if not self.closing:
                        self.send("STATUS")
                elif result == "error":
                    self.status_var.set("Connection failed. No ports were left open.")
                    self.notice_var.set(value)
                    self.append_log(time.time(), "ERROR", value)
                else:
                    self.bridge, self.simulator = None, None
                    self.status_var.set("Disconnected. Recorder state is not known.")
                    self.recording_var.set("UNKNOWN")
                    self.command_var.set("Disconnected")
                    for text in self.counter_vars.values():
                        text.set("Disconnected")
                    self.append_log(time.time(), "INFO", "Disconnected; no automatic Stop command sent")
        except queue.Empty:
            pass
        if self.bridge:
            for _ in range(100):
                try:
                    self.append_log(*self.bridge.events.get_nowait())
                except queue.Empty:
                    break
            s = self.bridge.snapshot()
            for name in self.counter_vars:
                self.counter_vars[name].set(f"{s[name]}  ·  {s[name + '_bytes']:,} B")
            self.recording_var.set(("DEMO / " if self.simulator else "") + s["recording"])
            self.recording_label.configure(foreground={"UNKNOWN": "#6b7583", "ERROR": "#ad3b1c",
                "ARMED": "#946400", "TESTING": "#946400", "RECORDING": "#126b73", "IDLE": "#172b42"}.get(s["recording"], "#172b42"))
            self.command_var.set(s["command"])
            self.line_var.set(s["last_line"] or "Waiting for a complete NMEA sentence…")
            self.quality_var.set(f"NMEA: {s['nmea_lines']:,}   |   Bad checksums: {s['bad_checksums']}   |   "
                                 f"No checksum: {s['missing_checksums']}   |   Omitted lines: {s['discarded_lines']}")
            age = f"{time.monotonic() - s['last_gps']:.1f} s ago" if s["last_gps"] else "not yet received"
            mode = "DEMO — no physical ports are open. " if self.simulator else "Live ports. "
            reply_age = f"{time.monotonic() - s['last_response']:.1f} s ago" if s["last_response"] else "not yet received"
            self.status_var.set(mode + f"Last GPS data: {age}. Last confirmed device reply: {reply_age}.")
            if s["notice"]:
                self.notice_var.set(s["notice"])
        self.update_controls()
        if self.closing and not self.busy:
            if self.bridge:
                self.disconnect(force=True)
            else:
                self.root.destroy()
                return
        self.root.after(100, self.tick)

    def close(self):
        if self.closing:
            return
        if self.bridge and self.bridge.snapshot()["recording"] in {"ARMED", "RECORDING", "TESTING", "UNKNOWN"}:
            if not messagebox.askyesno("Close FlightBridge", "The recorder may still be armed or running.\n"
                                      "Close without sending Stop?", parent=self.root):
                return
        self.save_preferences()
        self.closing = True
        self.status_var.set("Closing connections…")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo", action="store_true", help="Select hardware-free demonstration mode")
    parser.add_argument("--self-test", metavar="RESULT_JSON", help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = tk.Tk()
    app = FlightBridgeApp(root, demo=args.demo or bool(args.self_test))
    if args.self_test:
        from selftest import run
        root.withdraw()
        run(app, Path(args.self_test))
    root.mainloop()


if __name__ == "__main__":
    main()
