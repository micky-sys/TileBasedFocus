"""Launch the real EXE with synthetic data; no visible window or hardware I/O."""
import os
from pathlib import Path
import subprocess
import sys
import numpy as np
from PySide6.QtGui import QImage

root = Path(__file__).resolve().parents[1]
environment = os.environ.copy()
environment["QT_QPA_PLATFORM"] = "offscreen"
preview = root / "tests/packaged-preview.png"
fixture_path = Path(sys.argv[2]).resolve() if len(sys.argv)>2 else root / "tests/demo.csv"
result = subprocess.run(
    [str(Path(sys.argv[1]).resolve() if len(sys.argv)>1 else root / "release/VibrationMonitor.exe"), "--smoke-test",
     str(fixture_path), "--screenshot", str(preview)],
    env=environment, timeout=45, creationflags=subprocess.CREATE_NO_WINDOW,
)
assert result.returncode == 0, f"Packaged app exit: {result.returncode}"
image = QImage(str(preview)).convertToFormat(QImage.Format_RGBA8888)
assert not image.isNull()
pixels = np.frombuffer(image.bits(), dtype=np.uint8).reshape(-1, 4)
assert len(np.unique(pixels[::101], axis=0)) > 100, "Screenshot is blank/unpainted"
print("PASS packaged EXE: load CSV, both sensors, seven plots, valid temperature points and rendered window")
