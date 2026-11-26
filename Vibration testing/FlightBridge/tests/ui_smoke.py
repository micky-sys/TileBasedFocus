"""Real Tk UI + simulated transports; no physical COM ports are opened."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import time
import tkinter as tk
from app import FlightBridgeApp
from protocol import control_frame

root = tk.Tk()
app = FlightBridgeApp(root, demo=True)

def until(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("UI smoke test timed out")

try:
    app.connect_button.invoke()
    until(lambda: app.bridge and app.bridge.snapshot()["recording"] == "IDLE")
    until(lambda: app.bridge.snapshot()["nmea_lines"] >= 2)
    until(lambda: not app.command_buttons[0][1].instate(["disabled"]))
    app.command_buttons[0][1].invoke()
    until(lambda: app.bridge.snapshot()["recording"] == "ARMED")
    until(lambda: not app.command_buttons[1][1].instate(["disabled"]))
    app.command_buttons[1][1].invoke()
    until(lambda: app.bridge.snapshot()["recording"] == "IDLE")
    app.mode_var.set("Continuous")
    app.update_controls()
    app.command_buttons[0][1].invoke()
    until(lambda: app.bridge.snapshot()["recording"] == "RECORDING")
    until(lambda: not app.command_buttons[1][1].instate(["disabled"]))
    app.command_buttons[1][1].invoke()
    until(lambda: app.bridge.snapshot()["recording"] == "IDLE")
    app.send("HEALTH")
    until(lambda: "SIMULATED_HEALTH" in app.bridge.snapshot()["command"])
    until(lambda: not app.bridge.snapshot()["pending"])
    controls = dict(app.command_buttons)
    app.update_controls()
    assert not controls["SPITEST"].instate(["disabled"])
    controls["SPITEST"].invoke()
    until(lambda: "SIMULATOR_NO_SPI" in app.bridge.snapshot()["command"])
    # Exercise real UI state handling without pretending the simulator tested hardware.
    app.simulator.ports["DEMO_TEENSY"].inject(control_frame("R", "0", "OK", "TESTING", "SPI_PROGRESS;mhz=4"))
    until(lambda: app.bridge.snapshot()["recording"] == "TESTING")
    app.update_controls()
    for command in ("START", "HEALTH", "SPITEST"):
        assert controls[command].instate(["disabled"])
    assert not controls["STOP"].instate(["disabled"])
    assert not controls["SPITESTRESULT"].instate(["disabled"])
    assert controls["STOP"].cget("text") == "Stop SPI test"
    controls["STOP"].invoke()
    until(lambda: app.bridge.snapshot()["recording"] == "IDLE")
    assert app.log.winfo_height() > 40, "Log panel clipped"
    assert app.root.winfo_width() >= 950
    app.disconnect(force=True)
    until(lambda: app.bridge is None and not app.busy)
    print("PASS: UI connect, GPS fanout, recording commands, SPI controls/state/stop, disconnect")
finally:
    if app.bridge:
        app.bridge.stop()
    if app.simulator:
        app.simulator.stop()
    root.destroy()
