"""Self-test for sbf_geotag_exports: parsing, clustering, matching, EXIF roundtrip.

Run::
    python tools/test_sbf_geotag_exports.py
"""

from __future__ import annotations

import json
import math
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import tifffile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from sbf_geotag_exports import (  # noqa: E402
    CSV_FIELDS,
    Cluster,
    CorrelationResult,
    Event,
    Frame,
    LineGroup,
    Match,
    build_correlation_rows,
    cluster_events,
    discover_bay14line_frames,
    discover_frames,
    event_to_fix,
    frame_slot,
    group_by_line,
    match_all,
    match_line,
    parse_sbf_csv,
    write_correlation_csv,
    write_gps_into_tiff,
)

FPS = 13  # canonical frames per sweep used in these tests


def make_event(tow: float, wnc: int = 2420, lat: float = 32.1, lon: float = 34.8,
               h: float = 100.0) -> Event:
    return Event(
        tow_s=tow, wnc=wnc,
        lat_deg=lat, lon_deg=lon, height_m=h, undulation_m=17.3,
    )


def test_parse_sbf_csv_minimum() -> None:
    text = (
        "113587.062,2420,Standalone,sub,3D,No Error,0.561635431,0.608002240,54.088,17.308\n"
        "113587.271,2420,Standalone,sub,3D,No Error,0.561635431,0.608002240,54.087,17.308\n"
    )
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as fp:
        fp.write(text)
        fp.flush()
        path = Path(fp.name)
    try:
        events = parse_sbf_csv(path)
        assert len(events) == 2, events
        assert events[0].wnc == 2420
        assert abs(events[0].tow_s - 113587.062) < 1e-3
        assert events[0].lat_deg is not None
        assert abs(events[0].lat_deg - math.degrees(0.561635431)) < 1e-6
        # UTC computation: GPS epoch + 2420 weeks + 113587 sec - 18 leap = a real date.
        utc = events[0].utc()
        assert utc.tzinfo is timezone.utc
        assert utc.year == 2026, utc
    finally:
        path.unlink(missing_ok=True)


def test_parse_sbf_csv_skips_dnu_floats() -> None:
    text = (
        "100.0,2420,Standalone,sub,3D,No Error,-2.0e10,-2.0e10,-2.0e10,-2.0e10\n"
    )
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".csv") as fp:
        fp.write(text)
        fp.flush()
        path = Path(fp.name)
    try:
        events = parse_sbf_csv(path)
        assert len(events) == 1
        assert events[0].lat_deg is None
        assert events[0].lon_deg is None
        assert events[0].height_m is None
        assert events[0].undulation_m is None
    finally:
        path.unlink(missing_ok=True)


def test_cluster_events_splits_on_big_gap() -> None:
    e = [make_event(100.0 + i * 0.2) for i in range(5)]
    e += [make_event(200.0 + i * 0.2) for i in range(3)]
    clusters = cluster_events(e, gap_s=5.0)
    assert len(clusters) == 2
    assert clusters[0].size == 5
    assert clusters[1].size == 3
    assert clusters[0].index == 0
    assert clusters[1].index == 1
    assert math.isclose(clusters[0].span_s, 0.8, abs_tol=1e-9)


def test_cluster_events_no_gap_returns_one() -> None:
    e = [make_event(100.0 + i * 0.2) for i in range(5)]
    clusters = cluster_events(e, gap_s=5.0)
    assert len(clusters) == 1
    assert clusters[0].size == 5


def _make_line(
    line_num: int,
    ts0: datetime,
    slots_and_dts: list[tuple[int, int, float]],
) -> LineGroup:
    """Synthesise a LineGroup.

    *slots_and_dts* is ``[(sweep, frame_in_sweep, t_offset_s), ...]``;
    t_offset_s is seconds past ts0.
    """
    frames: list[Frame] = []
    for s, f_in, off in slots_and_dts:
        frames.append(Frame(
            path=Path(f"/tmp/Cam0_20260525_proj_120000_L{line_num}_S{s}_F{f_in}.tif"),
            cam="Cam0", date_yyyymmdd="20260525", project="proj",
            hhmmss="120000", line=line_num, sweep=s, frame_in_sweep=f_in,
            acquire_ts=ts0 + timedelta(seconds=off),
        ))
    return LineGroup(line=line_num, frames=frames)


def test_frame_slot_indexing() -> None:
    assert frame_slot(1, 1, 13) == 0
    assert frame_slot(1, 13, 13) == 12
    assert frame_slot(2, 1, 13) == 13
    assert frame_slot(6, 13, 13) == 77


def test_match_line_picks_best_rmse_cluster() -> None:
    # Real cluster: 5 events at 0.20s spacing -> matches a dense line of 5 frames
    target = [make_event(100.0 + i * 0.20) for i in range(5)]
    # Distractor: 5 events at 0.30s spacing (poor delta match)
    distractor = [make_event(500.0 + i * 0.30) for i in range(5)]
    clusters = [Cluster(target, 0), Cluster(distractor, 1)]
    line = _make_line(
        13, datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    m = match_line(line, clusters, frames_per_sweep=FPS)
    assert m.cluster is clusters[0]
    assert m.rmse_s < 1e-9, m.rmse_s


def test_match_line_rejects_too_small_clusters() -> None:
    small = [make_event(100.0 + i * 0.20) for i in range(3)]
    line = _make_line(
        13, datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    m = match_line(line, [Cluster(small, 0)], frames_per_sweep=FPS)
    assert m.cluster is None


def test_match_all_consumes_clusters_in_order() -> None:
    c0 = Cluster([make_event(100.0 + i * 0.20) for i in range(5)], 0)
    c1 = Cluster([make_event(200.0 + i * 0.20) for i in range(5)], 1)
    line_a = _make_line(
        1, datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    line_b = _make_line(
        2, datetime(2026, 5, 25, 12, 5, 0, tzinfo=timezone.utc),
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    ms = match_all([line_a, line_b], [c0, c1], frames_per_sweep=FPS)
    assert ms[0].cluster is c0
    assert ms[1].cluster is c1


def test_match_falls_back_to_size_without_timestamps() -> None:
    c = Cluster([make_event(100.0 + i * 0.20) for i in range(7)], 0)
    line = _make_line(
        13, datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    for f in line.frames:
        f.acquire_ts = None
    m = match_line(line, [c], frames_per_sweep=FPS)
    assert m.cluster is c
    assert math.isinf(m.rmse_s)
    assert m.flight_offset_s is None


def test_match_line_handles_sparse_sf_exports() -> None:
    """Sparse export: 4 frames at slots (0, 7, 13, 25). The right cluster is a
    big run of 30 events at 0.20s; an attractor of 30 events at 0.50s would
    win a *naive* head-deltas match but should lose with slot-based matching.
    """
    fps = 13
    right = [make_event(1000.0 + i * 0.20) for i in range(30)]
    wrong = [make_event(2000.0 + i * 0.50) for i in range(30)]
    clusters = [Cluster(right, 0), Cluster(wrong, 1)]

    ts0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    # Slots: (1,1)=0, (1,8)=7, (2,1)=13, (2,13)=25
    # Expected times from cluster `right`: 0.0, 1.4, 2.6, 5.0 seconds.
    line = _make_line(13, ts0, [
        (1, 1, 0.0),
        (1, 8, 0.20 * 7),
        (2, 1, 0.20 * 13),
        (2, 13, 0.20 * 25),
    ])
    m = match_line(line, clusters, frames_per_sweep=fps)
    assert m.cluster is clusters[0], m
    assert m.rmse_s < 1e-9, m.rmse_s
    assert m.slots == [0, 7, 13, 25]


def test_match_line_finds_offset_inside_cluster_with_pre_line_burst() -> None:
    """A "trigger test" burst of 4 events fires ~3 s before the real line and,
    with a generous ``cluster_gap_s``, lands in the SAME cluster. The line is
    13 events at 0.20 s spacing. The matcher must scan starting offsets and
    pick offset=4 (skip the burst) rather than offset=0 (which would treat
    the burst as the first sweep).

    This is the failure mode we hit on the May 2026 flight where L10's
    pre-line trigger sat in cluster #11/#12 and the matcher pinned the line
    to slot 0 — yielding rmse=13 s on the best surviving candidate.
    """
    fps = 13
    burst = [make_event(1000.0 + i * 0.05) for i in range(4)]
    line_events = [make_event(1003.0 + i * 0.20) for i in range(13)]
    cluster = Cluster(burst + line_events, 0)

    ts0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    line = _make_line(10, ts0, [(1, k + 1, k * 0.20) for k in range(13)])
    m = match_line(line, [cluster], frames_per_sweep=fps)
    assert m.cluster is cluster
    assert m.slot_offset == 4, m.slot_offset
    assert m.rmse_s < 1e-9, m.rmse_s
    # Event-for-slot helper resolves to the post-burst region:
    assert m.event_for_slot(0) is cluster.events[4]
    assert m.event_for_slot(12) is cluster.events[16]


def test_match_line_picks_slot_matching_cluster_over_size_match() -> None:
    """A cluster whose total *size* happens to equal the number of exported
    frames must NOT win over a larger cluster whose events at the exported
    slots line up with the frames' timing.
    """
    fps = 13
    # Decoy cluster of size 4 (= number of exported frames) but its first 4
    # events span only 0.6s -- nothing like the real 5s line.
    decoy = [make_event(500.0 + i * 0.20) for i in range(4)]
    right = [make_event(1000.0 + i * 0.20) for i in range(30)]
    clusters = [Cluster(decoy, 0), Cluster(right, 1)]

    ts0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    line = _make_line(13, ts0, [
        (1, 1, 0.0),
        (1, 8, 1.4),
        (2, 1, 2.6),
        (2, 13, 5.0),
    ])
    m = match_line(line, clusters, frames_per_sweep=fps)
    assert m.cluster is clusters[1], m


def test_event_to_fix_carries_position_and_utc() -> None:
    ev = make_event(113587.062, lat=32.1, lon=34.8, h=54.0)
    fix = event_to_fix(ev)
    assert fix.latitude_deg == 32.1
    assert fix.longitude_deg == 34.8
    assert fix.altitude_m == 54.0
    assert fix.timestamp_utc is not None
    assert fix.fix_quality == 1


def test_discover_frames_groups_by_line_and_sorts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        names = [
            "Cam2439007_20260525_proj_120000_L13_S2_F1.tif",
            "Cam2439007_20260525_proj_120000_L13_S1_F2.tif",
            "Cam2439007_20260525_proj_120000_L13_S1_F1.tif",
            "Cam2439007_20260525_proj_120000_L7_S1_F1.tif",
            "noise.txt",
        ]
        for n in names:
            (root / n).write_bytes(b"")  # not a real tiff -> acquire_ts will be None
        # Plain empty files won't be valid TIFFs, so discovery sets acquire_ts=None
        # but should still parse names and group correctly.
        frames = discover_frames(root)
        # Empty files won't have valid tiff page; tifffile raises -> our reader
        # returns None and the frame stays. So 4 frames total.
        assert len(frames) == 4, [f.path.name for f in frames]
        groups = group_by_line(frames)
        assert [g.line for g in groups] == [7, 13]
        line13 = next(g for g in groups if g.line == 13)
        # Sorted by (sweep, frame_in_sweep):
        keys = [(f.sweep, f.frame_in_sweep) for f in line13.frames]
        assert keys == [(1, 1), (1, 2), (2, 1)], keys


def test_write_gps_into_tiff_roundtrip() -> None:
    arr = np.arange(64 * 96, dtype=np.uint16).reshape(64, 96)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.tif"
        original_desc = json.dumps({
            "sweep": 1, "frame_in_sweep": 1, "camera_serial": "1234",
            "acquire_timestamp": "2026-05-25T14:46:43.258+03:00",
        })
        tifffile.imwrite(
            str(path), arr,
            photometric="minisblack",
            compression=None,
            bigtiff=False,
            description=original_desc,
        )

        ev = make_event(113587.062, lat=32.179340, lon=34.835962, h=54.088)
        fix = event_to_fix(ev)
        write_gps_into_tiff(path, fix)

        with tifffile.TiffFile(str(path)) as tf:
            page = tf.pages[0]
            out_arr = page.asarray()
            desc = json.loads(page.tags["ImageDescription"].value)
            # GPS IFD0 tags we wrote (lat=2, lon=4, lat_ref=1, ts=7)
            assert page.tags.get(1) is not None and page.tags.get(1).value == "N"
            assert page.tags.get(2) is not None
            assert page.tags.get(3) is not None and page.tags.get(3).value == "E"
            assert page.tags.get(4) is not None

        assert (out_arr == arr).all()
        assert desc["sweep"] == 1
        assert desc["acquire_timestamp"] == "2026-05-25T14:46:43.258+03:00"
        assert desc["gps"]["latitude_deg"] == 32.179340
        assert desc["gps"]["longitude_deg"] == 34.835962
        assert desc["gps"]["altitude_m"] == 54.088
        assert desc["gps"]["timestamp_utc"] is not None


def _make_frame(path: Path, line: int, sweep: int, fis: int) -> Frame:
    return Frame(
        path=path,
        cam="Cam0001",
        date_yyyymmdd="20260525",
        project="proj",
        hhmmss="120000",
        line=line,
        sweep=sweep,
        frame_in_sweep=fis,
    )


def test_write_correlation_csv_basic_and_unmatched_rows() -> None:
    fps = 13
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # Two matched frames + one matched-but-OOB + one whole-line unmatched.
        f_match_1 = _make_frame(root / "Cam0001_20260525_proj_120000_L13_S1_F1.tif", 13, 1, 1)
        f_match_2 = _make_frame(root / "Cam0001_20260525_proj_120000_L13_S1_F2.tif", 13, 1, 2)
        f_oob    = _make_frame(root / "Cam0001_20260525_proj_120000_L13_S1_F13.tif", 13, 1, 13)
        # frame on a line that did not match any cluster:
        f_orphan = _make_frame(root / "Cam0001_20260525_proj_120000_L7_S1_F1.tif", 7, 1, 1)
        for f in (f_match_1, f_match_2, f_oob, f_orphan):
            f.path.write_bytes(b"")
        cluster = Cluster(
            events=[
                make_event(1000.0, lat=32.10, lon=34.80, h=120.5),
                make_event(1000.2, lat=32.11, lon=34.81, h=120.6),
                # only 2 events -- slot 12 (F13) will be out-of-bounds
            ],
            index=0,
        )
        line13 = LineGroup(line=13, frames=[f_match_1, f_match_2, f_oob])
        line7 = LineGroup(line=7, frames=[f_orphan])
        slots_13 = [0, 1, 12]  # 12 = (S1, F13) under fps=13
        match_13 = Match(line13, cluster, slots=slots_13,
                         rmse_s=0.001, flight_offset_s=0.0)
        match_7 = Match(line7, None, slots=[0],
                        rmse_s=float("inf"), flight_offset_s=None)
        result = CorrelationResult(
            events=cluster.events,
            clusters=[cluster],
            lines=[line13, line7],
            matches=[match_13, match_7],
            incomplete_tiffs=[],
        )

        out = root / "out.csv"
        n = write_correlation_csv(result, out, root=root)
        assert n == 4
        text = out.read_text(encoding="utf-8-sig")
        lines = text.strip().splitlines()
        # header + 4 data rows:
        assert len(lines) == 5, lines
        header_csv = lines[0]
        assert header_csv.split(",") == list(CSV_FIELDS), header_csv
        # Row order is (line, sweep, frame_in_sweep):
        rows = build_correlation_rows(result, root=root)
        order = [(r["line"], r["sweep"], r["frame_in_sweep"]) for r in rows]
        assert order == [("7", "1", "1"), ("13", "1", "1"), ("13", "1", "2"), ("13", "1", "13")], order
        # Orphan row: assigned=0, empty lat/lon, note explains.
        orphan = next(r for r in rows if r["line"] == "7")
        assert orphan["assigned"] == "0"
        assert orphan["latitude_deg"] == ""
        assert orphan["longitude_deg"] == ""
        assert "no cluster" in orphan["note"]
        # OOB row: cluster_index set, lat empty, note mentions slot.
        oob = next(r for r in rows if r["frame_in_sweep"] == "13")
        assert oob["assigned"] == "0"
        assert oob["cluster_index"] == "0"
        assert oob["event_slot"] == "12"
        assert oob["latitude_deg"] == ""
        assert "slot 12" in oob["note"]
        # Matched rows: lat/lon populated, ISO timestamp present.
        first = next(r for r in rows if r["frame_in_sweep"] == "1" and r["line"] == "13")
        assert first["assigned"] == "1"
        assert first["latitude_deg"] == f"{32.10:.9f}"
        assert first["longitude_deg"] == f"{34.80:.9f}"
        assert first["altitude_m"] == f"{120.5:.3f}"
        assert first["timestamp_utc"].endswith("+00:00")
        assert first["cluster_index"] == "0"
        assert first["event_slot"] == "0"


def test_csv_uses_tiff_export_name_in_bay14line_mode() -> None:
    """In bay14line mode the CSV's ``filename`` column must carry the
    name Bay14Reader would give the exported TIFF, NOT the shared
    ``.bay14line`` container name (which is the same for every chunk
    inside it -- useless for joining downstream). The ``relative_path``
    must mirror Bay14Reader's per-camera output subfolder layout.
    """
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # A single .bay14line file shared by both chunks (this is the
        # real-world container layout).
        container = root / "Cam2439007_20260525_ParallaxTestRishon_L13_144545.bay14line"
        container.write_bytes(b"")  # only the path matters here
        f1 = Frame(
            path=container,
            cam="Cam2439007",
            date_yyyymmdd="20260525",
            project="ParallaxTestRishon",
            hhmmss="144545",
            line=13, sweep=1, frame_in_sweep=1,
            acquire_ts=None,
        )
        f2 = Frame(
            path=container,
            cam="Cam2439007",
            date_yyyymmdd="20260525",
            project="ParallaxTestRishon",
            hhmmss="144545",
            line=13, sweep=1, frame_in_sweep=2,
            acquire_ts=None,
        )
        cluster = Cluster(events=[
            make_event(1000.0, lat=32.10, lon=34.80, h=120.0),
            make_event(1000.2, lat=32.11, lon=34.81, h=120.1),
        ], index=0)
        line = LineGroup(line=13, frames=[f1, f2], cam_serial="Cam2439007",
                         hhmmss="144545")
        m = Match(line, cluster, slots=[0, 1],
                  rmse_s=0.001, flight_offset_s=-7.2)
        result = CorrelationResult(
            events=cluster.events, clusters=[cluster],
            lines=[line], matches=[m], incomplete_tiffs=[],
        )

        rows = build_correlation_rows(result, root=root)
        assert len(rows) == 2, rows
        # The container name MUST NOT leak into the filename column:
        for r in rows:
            assert ".bay14line" not in r["filename"], r
            assert ".bay14line" not in r["relative_path"], r
        # The synthesized TIFF names match Bay14Reader's exporter layout:
        names = sorted(r["filename"] for r in rows)
        assert names == [
            "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif",
            "Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F2.tif",
        ], names
        # relative_path is "<cam>/<tiff_name>" (per-camera subfolder):
        rels = sorted(r["relative_path"] for r in rows)
        assert rels == [
            "Cam2439007/Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F1.tif",
            "Cam2439007/Cam2439007_20260525_ParallaxTestRishon_144545_L13_S1_F2.tif",
        ], rels


def test_discover_bay14line_frames_round_trips_acquire_ts() -> None:
    """Write a tiny synthetic .bay14line with COMPRESSION_NONE so we don't
    need blosc2 at test time, then discover its chunks via the geotagger's
    raw-file path. Each chunk's ``acquire_timestamp`` must come back as a
    parsed ``datetime``, and the matcher must lock onto the synthetic
    cluster with sub-millisecond RMSE.
    """
    from capture.raw_bay_writer import (  # noqa: E402
        COMPRESSION_NONE,
        LINE_PIXEL_FORMAT_UINT16_2D,
        LineWriter,
    )
    h, w = 2, 2  # 8 bytes per chunk -- file stays trivially small
    n_frames = 5
    base = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        line_path = root / "Cam9999_20260525_DemoProj_L7_120000.bay14line"
        wlw = LineWriter(
            str(line_path),
            height=h, width=w,
            line_meta={
                "camera_serial": "9999",
                "project": "DemoProj",
                "line": 7,
                "capture_kind": "sweep",
            },
            codec=COMPRESSION_NONE,
            pixel_format=LINE_PIXEL_FORMAT_UINT16_2D,
        )
        wlw.open()
        payload = b"\x00" * (h * w * 2)
        ts_list = []
        for i in range(n_frames):
            ts = base + timedelta(seconds=0.2 * i)
            ts_list.append(ts)
            wlw.append_chunk(
                payload,
                sweep=1, frame_in_sweep=i + 1,
                frame_meta={
                    "acquire_timestamp": ts.isoformat(timespec="milliseconds"),
                    "capture_kind": "sweep",
                },
            )
        wlw.close_with_index(fsync=False)

        frames = discover_bay14line_frames(root, recursive=False)
        assert len(frames) == n_frames, frames
        for i, f in enumerate(frames):
            assert f.line == 7, f
            assert f.sweep == 1
            assert f.frame_in_sweep == i + 1
            assert f.path == line_path
            assert f.acquire_ts is not None
            # Equal modulo timezone normalisation by fromisoformat:
            assert f.acquire_ts.astimezone(timezone.utc) == ts_list[i]

        lines = group_by_line(frames)
        assert [g.line for g in lines] == [7]

        # Build a matching cluster: events at 0.20 s spacing, no offset.
        events = [Event(
            tow_s=100.0 + 0.20 * i, wnc=2420,
            lat_deg=32.10 + 1e-4 * i,
            lon_deg=34.80 + 1e-4 * i,
            height_m=120.0,
            undulation_m=17.3,
        ) for i in range(n_frames)]
        cluster = Cluster(events, 0)
        m = match_line(lines[0], [cluster], frames_per_sweep=13)
        assert m.cluster is cluster
        assert m.slot_offset == 0
        assert m.rmse_s < 1e-6, m.rmse_s


def test_write_gps_into_bay14line_round_trip_in_place() -> None:
    """End-to-end raw-mode write: synthesize a .bay14line, patch a GPS
    block into one of its chunk headers in place, and verify that
    Bay14Reader's reader (``parse_frame_chunk_header``) sees the block
    AND that:

    * the file's byte length is unchanged (no shifted chunk offsets),
    * the line index still resolves to the same chunk offsets,
    * subsequent (unpatched) chunks still parse cleanly with their
      original metadata intact,
    * Bay14Reader's :func:`_gps_fix_from_meta` reconstructs the same
      ``GpsFix`` that was written.

    This is the round-trip that backs the new "Start writing" path in
    bay14line mode -- a regression here would silently corrupt every
    raw container on the ingest SSD.
    """
    from capture.raw_bay_writer import (  # noqa: E402
        COMPRESSION_NONE,
        LINE_HEADER_FIXED_PREFIX,
        LINE_INDEX_ENTRY_SIZE,
        LINE_PIXEL_FORMAT_UINT16_2D,
        LineWriter,
        parse_frame_chunk_header,
        parse_line_header_prefix,
        parse_line_index,
    )
    import mmap
    from sbf_geotag_exports import write_gps_into_bay14line
    from capture.gps_fix import GpsFix  # noqa: E402

    h, w = 2, 2
    n_frames = 5
    base = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        line_path = root / "Cam9999_20260525_DemoProj_L7_120000.bay14line"
        wlw = LineWriter(
            str(line_path),
            height=h, width=w,
            line_meta={
                "camera_serial": "9999",
                "project": "DemoProj",
                "line": 7,
                "capture_kind": "sweep",
            },
            codec=COMPRESSION_NONE,
            pixel_format=LINE_PIXEL_FORMAT_UINT16_2D,
        )
        # Use direct-I/O chunk alignment (4 KiB) without actually
        # enabling direct-I/O writes -- gives us the production-style
        # chunk-header padding the in-place GPS patch relies on.
        wlw.chunk_alignment = 4096
        wlw.open()
        payload = b"\x00" * (h * w * 2)
        for i in range(n_frames):
            ts = base + timedelta(seconds=0.2 * i)
            wlw.append_chunk(
                payload,
                sweep=1, frame_in_sweep=i + 1,
                frame_meta={
                    "acquire_timestamp": ts.isoformat(timespec="milliseconds"),
                    "capture_kind": "sweep",
                },
            )
        wlw.close_with_index(fsync=False)
        size_before = line_path.stat().st_size

        # Patch GPS into frames (S1, F2) and (S1, F4) only.
        fix_f2 = GpsFix(
            latitude_deg=32.1234567,
            longitude_deg=34.8765432,
            altitude_m=120.5,
            timestamp_utc=base + timedelta(seconds=0.2 * 1),
            satellites=12,
            fix_quality=1,
        )
        fix_f4 = GpsFix(
            latitude_deg=32.2,
            longitude_deg=34.9,
            altitude_m=121.0,
            timestamp_utc=base + timedelta(seconds=0.2 * 3),
            satellites=11,
            fix_quality=1,
        )
        stats = write_gps_into_bay14line(
            line_path,
            {(1, 2): fix_f2, (1, 4): fix_f4},
            extra_by_frame={
                (1, 2): {"cluster_index": 3, "event_slot": 1},
                (1, 4): {"cluster_index": 3, "event_slot": 3},
            },
        )
        assert stats.written == 2, stats
        assert stats.failed == 0, stats.errors
        assert stats.skipped == 0, stats

        # File length unchanged -- no chunk offsets shifted.
        assert line_path.stat().st_size == size_before, (
            f"file grew/shrank: {size_before} -> {line_path.stat().st_size}"
        )

        # Walk every chunk via the index and re-parse its header.
        with open(line_path, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                prefix = mm[:LINE_HEADER_FIXED_PREFIX]
                _hl, n, idx_off, _ = parse_line_header_prefix(prefix)
                idx_blob = mm[idx_off: idx_off + n * LINE_INDEX_ENTRY_SIZE]
                chunks = parse_line_index(idx_blob, n)
                for sweep, f_in, offset, _p_len, _unc in chunks:
                    blob = bytes(mm[offset: offset + 4096])
                    meta, hdr_len, payload_len = parse_frame_chunk_header(blob)
                    # acquire_timestamp must remain present for every chunk
                    # (patch must NOT clobber unrelated keys).
                    assert "acquire_timestamp" in meta, (sweep, f_in, meta)
                    if (sweep, f_in) in {(1, 2), (1, 4)}:
                        assert "gps" in meta, (sweep, f_in, meta)
                        gps = meta["gps"]
                        # to_dict() drops Nones so missing keys are fine.
                        assert isinstance(gps, dict)
                        assert abs(float(gps["latitude_deg"]) -
                                   (32.1234567 if f_in == 2 else 32.2)) < 1e-9
                        assert abs(float(gps["longitude_deg"]) -
                                   (34.8765432 if f_in == 2 else 34.9)) < 1e-9
                        assert gps["cluster_index"] == 3
                    else:
                        assert "gps" not in meta, (sweep, f_in, meta)

        # Bay14Reader's reader path round-trips cleanly.
        from bay14_export_core import _gps_fix_from_meta  # noqa: E402
        with open(line_path, "rb") as fh:
            with mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                prefix = mm[:LINE_HEADER_FIXED_PREFIX]
                _hl, n, idx_off, _ = parse_line_header_prefix(prefix)
                idx_blob = mm[idx_off: idx_off + n * LINE_INDEX_ENTRY_SIZE]
                chunks = parse_line_index(idx_blob, n)
                _sweep, _fin, offset_f2, _p_len, _unc = chunks[1]
                blob = bytes(mm[offset_f2: offset_f2 + 4096])
                meta_f2, _hl2, _pl2 = parse_frame_chunk_header(blob)
        fix_back = _gps_fix_from_meta(meta_f2)
        assert fix_back is not None
        assert abs(fix_back.latitude_deg - 32.1234567) < 1e-9
        assert abs(fix_back.longitude_deg - 34.8765432) < 1e-9
        assert fix_back.satellites == 12
        assert fix_back.fix_quality == 1


def test_group_by_line_splits_retries_by_hhmmss() -> None:
    """Two captures of the same operator-set line number (different
    HHMMSS in the filename) must become two distinct LineGroups, even
    though they share ``line=1``. Bundling them together poisons the
    matcher because each capture restarts its (sweep, frame_in_sweep)
    counter from 1 and the merged slot space ends up with duplicates.
    """
    ts0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    # Two retries of L1: aborted attempt at 120000 (just one frame) and
    # the successful one at 140000 (five frames).
    aborted = [Frame(
        path=Path("Cam0001_20260525_proj_120000_L1_S1_F1.tif"),
        cam="Cam0001", date_yyyymmdd="20260525", project="proj",
        hhmmss="120000", line=1, sweep=1, frame_in_sweep=1,
        acquire_ts=ts0,
    )]
    ok = [Frame(
        path=Path(f"Cam0001_20260525_proj_140000_L1_S1_F{i+1}.tif"),
        cam="Cam0001", date_yyyymmdd="20260525", project="proj",
        hhmmss="140000", line=1, sweep=1, frame_in_sweep=i + 1,
        acquire_ts=ts0 + timedelta(hours=2, seconds=0.2 * i),
    ) for i in range(5)]
    groups = group_by_line(aborted + ok)
    assert len(groups) == 2, [g.label() for g in groups]
    labels = [g.label() for g in groups]
    assert labels == ["L1@120000", "L1@140000"], labels
    # Each retry kept its own frame count -- no contamination across.
    assert len(groups[0].frames) == 1
    assert len(groups[1].frames) == 5
    # Identity tuple is propagated:
    assert groups[0].hhmmss == "120000"
    assert groups[1].cam_serial == "Cam0001"


def test_match_line_rejects_clusters_outside_max_flight_offset() -> None:
    """The absolute-time prior. A cluster's intra-line timing fits the
    line perfectly (rmse ~ 0) but its anchor event sits ~3 hours away
    in wall-clock time from the frame's acquire timestamp -- a wrong
    match the matcher would otherwise happily pick when there are no
    competing in-window clusters. With ``max_flight_offset_s=600`` it
    must reject the candidate and return ``cluster=None``.
    """
    from sbf_geotag_exports import DEFAULT_LEAP_SECONDS as _LEAP

    base_utc = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    _GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
    weeks = (base_utc - _GPS_EPOCH).days // 7
    base_tow = ((base_utc - _GPS_EPOCH).total_seconds()
                - weeks * 7 * 86400 + _LEAP)

    # Cluster sits 3 hours after the line's frame acquire timestamps:
    # implied offset = +10 800 s, far outside any plausible flight-
    # clock drift window.
    distant = [Event(
        tow_s=base_tow + 3 * 3600 + 0.20 * i, wnc=weeks,
        lat_deg=32.10, lon_deg=34.80, height_m=120.0, undulation_m=17.3,
    ) for i in range(5)]
    clusters = [Cluster(distant, 0)]

    line = _make_line(
        7, base_utc,
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    # Without the cap, the distant cluster is the only candidate and
    # wins (its RMSE is ~0; nothing else to compare to).
    m_no_cap = match_line(line, clusters, frames_per_sweep=13)
    assert m_no_cap.cluster is clusters[0]
    assert abs(m_no_cap.flight_offset_s - 10_800.0) < 1.0

    # With a 10-minute cap the distant cluster is rejected.
    m_capped = match_line(
        line, clusters, frames_per_sweep=13,
        max_flight_offset_s=600.0,
    )
    assert m_capped.cluster is None, m_capped


def test_match_line_rejects_clusters_with_high_rmse() -> None:
    """The intra-line-fit prior. A cluster sits in the right wall-clock
    window but its events fire at a noticeably different cadence than
    the frames -- so the best achievable RMSE is several seconds, not
    the usual ~30 ms. With ``max_rmse_s=1.0`` the matcher refuses to
    pick it. This is what stops a 897-frame line from latching onto
    a neighboring 897-event cluster that has a similar but not
    identical firing pattern.
    """
    from sbf_geotag_exports import DEFAULT_LEAP_SECONDS as _LEAP

    base_utc = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    _GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
    weeks = (base_utc - _GPS_EPOCH).days // 7
    base_tow = ((base_utc - _GPS_EPOCH).total_seconds()
                - weeks * 7 * 86400 + _LEAP)

    # Events at 1.0 s spacing -- the line fires at 0.2 s spacing.
    # Best-case RMSE is ~couple of seconds (mismatched cadence).
    bad_cadence = [Event(
        tow_s=base_tow + 1.0 * i, wnc=weeks,
        lat_deg=32.10, lon_deg=34.80, height_m=120.0, undulation_m=17.3,
    ) for i in range(5)]
    clusters = [Cluster(bad_cadence, 0)]
    line = _make_line(
        7, base_utc,
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )

    # No cap -- the only candidate wins even with bad RMSE.
    m_no_cap = match_line(line, clusters, frames_per_sweep=13)
    assert m_no_cap.cluster is clusters[0]
    assert m_no_cap.rmse_s > 1.0, m_no_cap

    # With a 1 s RMSE cap the match is rejected.
    m_capped = match_line(
        line, clusters, frames_per_sweep=13,
        max_rmse_s=1.0,
    )
    assert m_capped.cluster is None, m_capped


def test_match_line_accepts_partial_fit_with_tail_missing_events() -> None:
    """Real-world rig case: 897 recorded frames but only 894 GPS events
    in the matching cluster (receiver dropped the last 3 triggers).
    With ``max_missing_events=0`` the cluster is too small and the line
    comes back NO-match; with ``max_missing_events=10`` the line claims
    the cluster, and the last 3 frames report None from
    ``event_for_slot``.
    """
    from sbf_geotag_exports import DEFAULT_LEAP_SECONDS as _LEAP

    base_utc = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    _GPS_EPOCH = datetime(1980, 1, 6, tzinfo=timezone.utc)
    weeks = (base_utc - _GPS_EPOCH).days // 7
    base_tow = ((base_utc - _GPS_EPOCH).total_seconds()
                - weeks * 7 * 86400 + _LEAP)

    # 5 frames in the line, 3 events in the cluster -- tail mismatch.
    events = [Event(
        tow_s=base_tow + 0.20 * i, wnc=weeks,
        lat_deg=32.10, lon_deg=34.80, height_m=120.0, undulation_m=17.3,
    ) for i in range(3)]
    clusters = [Cluster(events, 0)]
    line = _make_line(
        7, base_utc,
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )

    # Strict: 3 < 5, cluster too small, NO match.
    m_strict = match_line(line, clusters, frames_per_sweep=13)
    assert m_strict.cluster is None

    # Slack of 2 still rejects (3 short by 2 events == max_missing).
    # Slack of 3+ accepts.
    m_slack = match_line(
        line, clusters, frames_per_sweep=13,
        max_missing_events=3,
    )
    assert m_slack.cluster is clusters[0], m_slack
    assert m_slack.slot_offset == 0
    # First 3 frames mapped, last 2 fall off the end.
    assert m_slack.event_for_slot(0) is events[0]
    assert m_slack.event_for_slot(1) is events[1]
    assert m_slack.event_for_slot(2) is events[2]
    assert m_slack.event_for_slot(3) is None
    assert m_slack.event_for_slot(4) is None


def test_match_all_prefers_bigger_capture_within_same_line() -> None:
    """When a long successful capture (5 frames) and a 2-frame aborted
    retry share line=1 and both could match SOME cluster, the long one
    must consume its rightful cluster first. Otherwise a tiny retry's
    flat timing pattern can fit anywhere and steal the cluster that
    really belonged to the full capture.
    """
    fps = 13
    # Cluster A: 5 events at 0.20 s spacing -- exact match for the
    # successful capture's 5-frame timing.
    cA = Cluster(
        [make_event(1000.0 + 0.20 * i) for i in range(5)],
        0,
    )
    # Cluster B: 5 events too, but at 0.50 s spacing. Almost anything
    # 2-frame fits it (only one inter-frame delta to constrain), so the
    # aborted retry would be happy here.
    cB = Cluster(
        [make_event(2000.0 + 0.50 * i) for i in range(5)],
        1,
    )

    ts0 = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    successful = _make_line(
        1, ts0,
        [(1, 1, 0.0), (1, 2, 0.2), (1, 3, 0.4), (1, 4, 0.6), (1, 5, 0.8)],
    )
    successful.hhmmss = "140000"
    aborted = _make_line(
        1, ts0,
        [(1, 1, 0.0), (1, 2, 0.5)],
    )
    aborted.hhmmss = "120000"
    # Caller order is "aborted before successful" (chronological). The
    # matcher must reorder by frame count so the successful capture
    # grabs cluster A before the aborted retry can lock it out.
    ms = match_all([aborted, successful], [cA, cB], frames_per_sweep=fps)
    # Output order respects caller order:
    assert ms[0].line is aborted
    assert ms[1].line is successful
    # But the cluster assignment reflects the size-first greedy order:
    assert ms[1].cluster is cA, ms[1]
    assert ms[1].rmse_s < 1e-9, ms[1].rmse_s
    # The aborted retry got the leftover cluster B.
    assert ms[0].cluster is cB


def test_discover_bay14line_frames_skips_unparseable_filenames() -> None:
    """Files that don't match Cam*_L*_HHMMSS[_pN].bay14line are skipped
    with a warning -- they must not bleed into the line groups (otherwise
    a stray ``foo.bay14line.tmp`` from a crashed writer would corrupt the
    matcher's view of the flight)."""
    from capture.raw_bay_writer import (  # noqa: E402
        COMPRESSION_NONE,
        LINE_PIXEL_FORMAT_UINT16_2D,
        LineWriter,
    )
    h, w = 2, 2
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        good = root / "Cam1_20260525_proj_L1_120000.bay14line"
        # ".tmp" suffix violates the regex; treated as not a valid line file.
        bad = root / "not_a_line_file.bay14line"
        for p in (good, bad):
            wlw = LineWriter(
                str(p), height=h, width=w,
                line_meta={"camera_serial": "1", "project": "proj", "line": 1},
                codec=COMPRESSION_NONE,
                pixel_format=LINE_PIXEL_FORMAT_UINT16_2D,
            )
            wlw.open()
            wlw.append_chunk(
                b"\x00" * (h * w * 2),
                sweep=1, frame_in_sweep=1,
                frame_meta={"acquire_timestamp": "2026-05-25T12:00:00"},
            )
            wlw.close_with_index(fsync=False)
        frames = discover_bay14line_frames(root, recursive=False)
        assert {f.path for f in frames} == {good}


def test_write_correlation_csv_excel_friendly_newlines() -> None:
    """csv.writer + ``newline=''`` must not emit blank lines between rows
    (Excel-on-Windows would otherwise see a doubled record separator)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        frame = _make_frame(root / "Cam0001_20260525_proj_120000_L1_S1_F1.tif", 1, 1, 1)
        frame.path.write_bytes(b"")
        cluster = Cluster([make_event(0.0, lat=1.0, lon=2.0)], 0)
        line = LineGroup(1, [frame])
        match = Match(line, cluster, slots=[0],
                      rmse_s=0.0, flight_offset_s=0.0)
        result = CorrelationResult([cluster.events[0]], [cluster], [line], [match], [])
        out = root / "x.csv"
        write_correlation_csv(result, out, root=root)
        raw = out.read_bytes()
        # No "\r\n\r\n" anywhere (blank lines between records).
        assert b"\r\n\r\n" not in raw, raw[:300]


def main() -> int:
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
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
