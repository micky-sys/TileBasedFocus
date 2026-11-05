"""Self-test for the Parallax Export Studio's metadata-only CSV export.

Run::

    py -3 tools/test_gps_export_studio_core.py
"""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from bay14_reader_gui import build_export_path, export_file_name  # noqa: E402
from gps_export_studio_core import (  # noqa: E402
    CSV_FIELDS,
    FrameRecord,
    FrameStore,
    write_frame_csv,
)

LINE = Path("Cam2439007_20260525_Proj_L13_144545_p0.bay14line")


def _rec(sweep: int, fin: int, **kw) -> FrameRecord:
    return FrameRecord(
        cam_index=0, cam_serial="2439007", line_path=LINE, line=13,
        hhmmss="144545", sweep=sweep, frame_in_sweep=fin, **kw,
    )


def test_export_file_name_matches_image_export_path() -> None:
    meta = {"sweep": 2, "frame_in_sweep": 7}
    want = build_export_path(Path("out"), "2439007", LINE, meta, "tif").name
    assert export_file_name(LINE, 2, 7, "tif") == want, want
    assert want == "Cam2439007_20260525_Proj_144545_L13_S2_F7.tif"


def test_write_frame_csv_rows_sorted_with_empty_cells_for_missing() -> None:
    store = FrameStore()
    # Added out of slot order; a frame with no fix and no attitude first.
    store.add(_rec(2, 1))
    store.add(_rec(
        1, 1, lat_deg=32.0329522868, lon_deg=34.7461570430, altitude_m=3784.0641,
        timestamp_utc="2026-05-25T11:44:53.108+00:00",
        yaw_deg=88.5, pitch_deg=-89.0, roll_deg=1.25,
    ))
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "sub" / "frame_metadata.csv"
        n = write_frame_csv(
            store, out,
            lambda r: export_file_name(r.line_path, r.sweep, r.frame_in_sweep, "jpg"),
        )
        raw = out.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf"), "expected utf-8-sig BOM for Excel"
        assert b"\r\n\r\n" not in raw, "blank lines between rows"
        with out.open(encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))

    assert n == 2 and len(rows) == 2
    assert tuple(rows[0].keys()) == CSV_FIELDS
    first, second = rows
    assert first["image"] == "Cam2439007_20260525_Proj_144545_L13_S1_F1.jpg"
    assert first["timestamp_utc"] == "2026-05-25T11:44:53.108+00:00"
    assert first["latitude_deg"] == "32.03295229"
    assert first["longitude_deg"] == "34.74615704"
    assert first["altitude_m"] == "3784.064"
    assert (first["yaw_deg"], first["pitch_deg"], first["roll_deg"]) == (
        "88.500", "-89.000", "1.250",
    )
    assert second["image"] == "Cam2439007_20260525_Proj_144545_L13_S2_F1.jpg"
    assert all(second[k] == "" for k in CSV_FIELDS if k != "image"), second


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed} / {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
