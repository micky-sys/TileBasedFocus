"""Hardware-free packaged GUI verification; called only with explicit --self-test."""
import json
import time


def run(app, result_path):
    steps = ["connect", "idle", "armed", "stopped", "recording", "stopped_again", "health", "disconnected"]
    position = 0
    deadline = time.monotonic() + 15

    def finish(ok, reason):
        if app.bridge:
            app.bridge.stop()
        if app.simulator:
            app.simulator.stop()
        result_path.write_text(json.dumps({"ok": ok, "reason": reason, "steps": steps[:position]}, indent=2), encoding="utf-8")
        app.root.destroy()

    def advance():
        nonlocal position, deadline
        try:
            if time.monotonic() > deadline:
                finish(False, "Timeout at " + steps[position])
                return
            state = app.bridge.snapshot() if app.bridge else {}
            step = steps[position]
            ready = False
            if step == "connect":
                app.connect()
                ready = True
            elif step == "idle" and state.get("recording") == "IDLE" and state.get("nmea_lines", 0) >= 2:
                app.send("ARM")
                ready = True
            elif step == "armed" and state.get("recording") == "ARMED" and not state.get("pending"):
                app.send("STOP")
                ready = True
            elif step == "stopped" and state.get("recording") == "IDLE" and not state.get("pending"):
                app.mode_var.set("Continuous")
                app.send("START")
                ready = True
            elif step == "recording" and state.get("recording") == "RECORDING" and not state.get("pending"):
                app.send("STOP")
                ready = True
            elif step == "stopped_again" and state.get("recording") == "IDLE" and not state.get("pending"):
                app.send("HEALTH")
                ready = True
            elif step == "health" and "SIMULATED_HEALTH" in state.get("command", "") and not state.get("pending"):
                app.disconnect(force=True)
                ready = True
            elif step == "disconnected" and not app.bridge and not app.busy:
                position += 1
                finish(True, "Packaged GUI and simulated serial protocol passed")
                return
            if ready:
                position += 1
                deadline = time.monotonic() + 15
            app.root.after(50, advance)
        except Exception as error:
            finish(False, repr(error))
    app.root.after(100, advance)
