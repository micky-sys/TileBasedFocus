"""Self-check for ``.bay14line`` container naming.

The one thing here that would break quietly: an M4 focus bracket's filename has
to stay matchable by ``sbf_geotag_exports._BAY14LINE_NAME_RE``, or the offline
geotag walk silently *skips the file* (it logs and continues). So that regex is
imported rather than restated -- a copy would drift and the test would pass
while the tool stopped seeing brackets.

Run::

    py -3 tools/test_line_naming.py
    py -3 -m pytest tools/test_line_naming.py -q
"""

from __future__ import annotations

import datetime as _dt
import os
import sys


def _bootstrap_paths() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(here)
    for p in (repo_root, os.path.join(repo_root, "host"), here):
        if p and p not in sys.path:
            sys.path.insert(0, p)


_bootstrap_paths()

from capture.file_naming import build_line_name  # noqa: E402
from sbf_geotag_exports import _BAY14LINE_NAME_RE  # noqa: E402

WHEN = _dt.datetime(2026, 9, 17, 14, 30, 5)


def _name(**kw) -> str:
    return build_line_name(
        drive_root="D:\\",
        project_raw="Kaya1",
        camera_serial="2439007",
        line=3,
        when=WHEN,
        **kw,
    ).basename()


def test_mapping_line_name_unchanged() -> None:
    """A normal sweep line must be byte-identical to the old scheme."""
    assert _name() == "Cam2439007_20260917_Kaya1_L3_143005.bay14line", _name()


def test_focus_name_carries_marker_and_camera() -> None:
    got = _name(focus=True, camera_number=1)
    assert got == "Cam2439007_20260917_Kaya1_FOCUS_C1_L3_143005.bay14line", got


def test_focus_without_camera_number_still_marked() -> None:
    got = _name(focus=True)
    assert got == "Cam2439007_20260917_Kaya1_FOCUS_L3_143005.bay14line", got


def test_focus_name_still_matches_geotag_regex() -> None:
    """The regression that would lose brackets from the offline walk."""
    for cam in (1, 2, 3):
        base = build_line_name(
            drive_root="D:\\", project_raw="Kaya1", camera_serial="2439007",
            line=12, when=WHEN, focus=True, camera_number=cam,
        )
        for part in (None, 0, 1):
            name = base.basename(part_idx=part)
            m = _BAY14LINE_NAME_RE.match(name)
            assert m, f"geotag regex would SKIP {name}"
            # Line number and timestamp must still be read correctly -- those
            # are what group chunks into lines downstream.
            assert m.group("line") == "12", (name, m.group("line"))
            assert m.group("hhmmss") == "143005", (name, m.group("hhmmss"))
            assert m.group("cam") == "Cam2439007", (name, m.group("cam"))


def test_part_suffix_goes_last() -> None:
    """``_pN`` must stay immediately before the extension, after the marker."""
    base = build_line_name(
        drive_root="D:\\", project_raw="Kaya1", camera_serial="2439007",
        line=3, when=WHEN, focus=True, camera_number=2,
    )
    assert base.basename(part_idx=1).endswith("_143005_p1.bay14line"), \
        base.basename(part_idx=1)


def test_focus_marker_is_greppable() -> None:
    """Sorting/filtering a drive for brackets is the point of the marker."""
    names = [
        _name(),
        _name(focus=True, camera_number=1),
        _name(focus=True, camera_number=2),
    ]
    assert [n for n in names if "_FOCUS_" in n] == names[1:], names


def main() -> int:
    tests = [
        test_mapping_line_name_unchanged,
        test_focus_name_carries_marker_and_camera,
        test_focus_without_camera_number_still_marked,
        test_focus_name_still_matches_geotag_regex,
        test_part_suffix_goes_last,
        test_focus_marker_is_greppable,
    ]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"line-naming self-check OK ({len(tests)} tests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
