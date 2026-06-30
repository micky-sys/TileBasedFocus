"""Unit-ish checks for the new naming scheme and the renamer tool.

Run::
    python tools/test_rename_exports.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rename_exports_to_new_scheme import (  # noqa: E402
    iter_candidates,
    rename_one,
    rewrite_basename,
)
from bay14_reader_gui import rewrite_export_stem  # noqa: E402


def test_rewrite_basename_with_p_token() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0_S1_F1.tif"
    want = "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif"
    got = rewrite_basename(src)
    assert got == want, (got, want)


def test_rewrite_basename_without_p_token() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_L13_144545_S1_F1.tif"
    want = "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif"
    got = rewrite_basename(src)
    assert got == want, (got, want)


def test_rewrite_basename_already_new() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif"
    assert rewrite_basename(src) is None


def test_rewrite_basename_jpg_extension() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p1_S2_F7.jpg"
    want = "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S2_F7.jpg"
    assert rewrite_basename(src) == want


def test_rewrite_basename_ignores_unrelated() -> None:
    assert rewrite_basename("notes.txt") is None
    assert rewrite_basename("Cam2439007.bay14line") is None


def test_rewrite_export_stem() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0"
    want = "Cam2439007_20260525_ParallaxTestRishon_144545_L13"
    got = rewrite_export_stem(src)
    assert got == want, (got, want)


def test_rewrite_export_stem_passthrough() -> None:
    src = "Cam2439007_20260525_ParallaxTestRishon_144545_L13"
    assert rewrite_export_stem(src) == src


def test_rename_walks_recursively() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cam = root / "2439007"
        cam.mkdir()
        names = [
            "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0_S1_F1.tif",
            "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0_S1_F2.tif",
            "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p1_S2_F1.jpg",
            "unrelated.txt",
        ]
        for n in names:
            (cam / n).write_bytes(b"x")

        cands = iter_candidates([root], recursive=True, extensions=None)
        assert len(cands) == 3, [c.name for c in cands]

        new_names = set()
        for src in cands:
            status, dest = rename_one(src, dry_run=False)
            assert status == "renamed", (status, src)
            assert dest is not None and dest.exists(), dest
            new_names.add(dest.name)

        assert new_names == {
            "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif",
            "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F2.tif",
            "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S2_F1.jpg",
        }, new_names

        cands2 = iter_candidates([root], recursive=True, extensions=None)
        assert cands2 == [], [c.name for c in cands2]


def test_rename_skips_existing_target() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        old = root / "Cam2439007_20260525_ParallaxTestRishon_L13_144545_p0_S1_F1.tif"
        new = root / "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif"
        old.write_bytes(b"a")
        new.write_bytes(b"b")
        status, dest = rename_one(old, dry_run=False)
        assert status == "skipped_exists", (status, dest)
        assert old.exists() and new.exists()


def main() -> int:
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {fn.__name__}: {exc!r}")
    print(f"\n{len(tests) - failed} / {len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
