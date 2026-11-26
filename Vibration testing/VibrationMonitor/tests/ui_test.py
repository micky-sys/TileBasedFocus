"""Offscreen UI workflow test: never opens a physical COM port."""
import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication
from app import MainWindow
from data import load_csv
from test_sync import fixture as sync_fixture
import tempfile

class Session:
    def __init__(self): self.sent=[]
    def isRunning(self): return True
    def command(self, text): self.sent.append(text)
    def stop(self): pass
    def wait(self, timeout): return True

app = QApplication([])
w = MainWindow()
w.show()
session = Session()
w.session = session
w.want_connection = True
w.on_event("line", "DualCsvLogger 2.0")
assert w.download_button.isEnabled() and w.new_button.isEnabled()
w.download_button.click()
assert session.sent == ["DUMP"]
assert not w.new_button.isEnabled()
w.on_event("line", "DOWNLOAD_CONFIRMED; SD log retained")
assert w.new_button.isEnabled()
w.new_button.click()
assert session.sent[-1] == "NEW"
w.on_event("line", "RECORDING log.csv; SEN1/J1 SPI0 + SEN3/J3 SPI1")
assert w.download_button.isEnabled()
w.on_event("line", "CSV_STATUS state=STOPPED samples=20640 fault=RAM_QUEUE_FULL fault_sensor=1")
assert "RAM_QUEUE_FULL" in w.state_label.text()
w.show_log(load_csv(Path(__file__).with_name("demo.csv")))
for index in range(3):
    w.tabs.setCurrentIndex(index)
    app.processEvents()
assert all(len(plot.listDataItems()) == 2 for plot in w.plots)
for item in w.plots[6].listDataItems():
    assert item.opts["symbol"] == "o"
    assert len(item.yData) == 3000
assert "SEN1" in w.sensor_summary.text() and "SEN3" in w.sensor_summary.text()
assert "0.375 s" in w.sensor_summary.text()
with tempfile.TemporaryDirectory() as folder:
    sync_path=Path(folder)/"sync.csv"
    sync_path.write_text(sync_fixture()[0])
    w.show_log(load_csv(sync_path))
    assert "FSYNC-aligned" in w.sensor_summary.text()
    assert w.plots[0].getPlotItem().getAxis("bottom").labelText == "FSYNC-aligned sample time"
    w.tabs.setCurrentIndex(1)
    w.plots[3].setXRange(0.05,0.052,padding=0)
    requested=[]
    w.open_log=lambda path,window=None:requested.append((path,window))
    w.detail_button.click()
    assert requested[-1][0] == sync_path
    assert abs(requested[-1][1][0]-0.05)<0.00001
    w.overview_button.click()
    assert requested[-1][1] is None
w.session = None
w.close()
print("PASS UI: command buttons, transfer gating, two-sensor graphs and all three tabs")
