"""Self-checks for OBSERVE: the fixed-focus, many-patch stitch diagnostic.

OBSERVE exists to answer why a freshly acquired patch scores ~1.6x the patch it
replaced when both are measured on the same frame at the same focus. Three things have
to hold or the answer is worthless:

  * the lens really does not move (otherwise focus is back in the measurement);
  * several patches really are alive at once (otherwise there is nothing to compare);
  * every patch is scored from the SAME frame (otherwise the ratio folds in whatever
    changed between frames, which is the thing being measured).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "host"))

from capture.autofocus_v2 import (  # noqa: E402
    AFStateV2,
    AutoFocusConfigV2,
    AutoFocusEngineV2,
)
from capture.focus_axis import EncoderAxis  # noqa: E402
from capture.observe_log import COLUMNS, ObserveLog  # noqa: E402


class _Shaft:
    """Records every pulse so a test can assert the motor was never touched."""

    def __init__(self, counts_per_pulse: float = 24.0) -> None:
        self.cpp = float(counts_per_pulse)
        self.count = 0.0
        self.pulses: list = []

    def pulse(self, sym: str, n: int) -> None:
        self.pulses.append((sym, int(n)))
        self.count += (int(n) if sym == "+" else -int(n)) * self.cpp

    def read(self) -> float:
        return self.count


def _textured(seed: int = 0, h: int = 480, w: int = 640) -> np.ndarray:
    """A frame with enough structure that tile selection accepts a patch anywhere."""
    rng = np.random.default_rng(seed)
    f = rng.integers(20, 235, size=(h, w), dtype=np.uint8)
    # Hard edges so Laplacian variance is comfortably above min_texture_score.
    f[::7, :] = 255
    f[:, ::9] = 10
    return f


def _engine(rows: list, *, objects: int = 3, offset: float = 0.0, seconds: float = 1e9,
            full_res_multi=None):
    shaft = _Shaft()
    cfg = AutoFocusConfigV2()
    cfg.observe_objects = objects
    cfg.observe_offset_counts = offset
    cfg.observe_seconds = seconds
    cfg.dive_burst_interval_s = 0.0
    cfg.handover_stagger_frames = 1      # fill the slots quickly in a short test
    cfg.drive_cadence_to_min = False
    axis = EncoderAxis(shaft.pulse, shaft.read, counts_per_pulse=24.0)
    af = AutoFocusEngineV2(pulse=lambda *_a, **_k: None, axis=axis, config=cfg,
                           observe_row=rows.append, full_res_multi=full_res_multi)
    return af, axis, shaft


def _run(af, frames: int, seed0: int = 0) -> None:
    for i in range(frames):
        # Shift the content a little each frame so the tracker reports real motion;
        # without it, response stays low and no second patch is ever acquired.
        af.feed_frame(np.roll(_textured(0), -3 * i, axis=0))


def test_observe_never_commands_the_motor() -> None:
    """The whole premise. If the lens moves, focus is back in the measurement and the
    run cannot distinguish 'the patch moved' from 'the focus changed' -- which is the
    exact confound OBSERVE was built to remove."""
    rows: list = []
    af, axis, shaft = _engine(rows, offset=0.0)
    af.start_observe()
    assert af.state is AFStateV2.OBSERVE
    _run(af, 25)

    assert shaft.pulses == [], f"OBSERVE moved the motor: {shaft.pulses[:5]}"
    assert axis.position == 0.0, axis.position
    assert rows, "not moving is easy if you also do nothing -- it must still measure"


def test_observe_keeps_the_configured_number_of_patches_alive() -> None:
    """Two patches on one frame is the minimum unit of evidence here, and until
    2026-08-16 the only thing that ever added a second was the handover refill --
    gated on enable_handover, which is off. Observation must not depend on the
    correction it is being used to design."""
    for want in (1, 2, 4):
        rows: list = []
        af, _axis, _shaft = _engine(rows, objects=want)
        assert not af.cfg.enable_handover, "the point is this works with the stitch OFF"
        af.start_observe()
        _run(af, 40)
        alive = len(af._rs.objects)
        assert alive == want, f"wanted {want} patches alive, got {alive}"


def test_every_patch_is_scored_from_the_same_frame() -> None:
    """A ratio between patches is only meaningful if both scores came off one frame.
    Check it structurally: every row written for a given frame number carries a
    distinct oid, and there are as many as there are live patches."""
    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=3)
    af.start_observe()
    _run(af, 40)

    assert rows, "no rows emitted"
    by_frame: dict = {}
    for r in rows:
        by_frame.setdefault(r["frame"], []).append(r["oid"])
    full = [f for f, oids in by_frame.items() if len(oids) == 3]
    assert full, f"never scored 3 patches on one frame: {by_frame}"
    for f in full:
        assert len(set(by_frame[f])) == 3, f"frame {f} scored an oid twice: {by_frame[f]}"


def test_rows_carry_both_age_and_height_because_only_a_reversal_separates_them() -> None:
    """On a forward-scrolling scene a patch's age and its height in the frame are the
    same number in disguise -- it travels at a constant rate. The CSV has to carry both
    so that a reverse-scroll run (OrthoSim --dir down), where the correlation flips,
    can be differenced against a forward one."""
    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=2)
    af.start_observe()
    _run(af, 40)

    for key in ("age_frames", "y_centre", "born_frame", "pos_counts", "score"):
        assert key in rows[0], f"{key} missing from the row"
    old = [r for r in rows if r["age_frames"] > 5]
    assert old, "no patch survived long enough to age"
    assert all(r["age_frames"] == r["frame"] - r["born_frame"] for r in rows)


def test_the_offset_move_happens_once_and_is_walked_back() -> None:
    """The ratio's dependence on lens position is the discriminator between a field
    cause and a content cause, so observations are taken at a few points along the
    curve. Each one must hand the lens back, or every later measurement silently
    references a different origin."""
    rows: list = []
    af, axis, shaft = _engine(rows, objects=1, offset=-480.0)
    af.start_observe()
    _run(af, 40)
    assert abs(axis.position - (-480.0)) <= 12.0, f"never reached the offset: {axis.position}"
    moved_out = len(shaft.pulses)
    assert moved_out > 0, "an offset was configured but nothing moved"

    af.stop("test")
    assert abs(axis.position) <= 12.0, f"did not return home: {axis.position}"
    assert len(shaft.pulses) > moved_out, "returned without moving?"


def test_a_stale_full_res_batch_logs_nothing_rather_than_a_coarse_score() -> None:
    """Regression, 2026-08-16. OBSERVE fell back to the coarse scorer whenever the
    full-res batch was stale. Coarse runs ~16x larger on real scenery, so ~8% of rows in
    the first real log were samples of a different metric -- and because a stale batch is
    stale for EVERY patch at once, they read as frame-global 20x spikes and were
    misdiagnosed as a display artifact for most of a day. The saved crops settled it:
    every score baked into a filename was full-res and none exceeded 6236, while the CSV
    for the same 37 seconds held 35 rows between 6236 and 39226 -- two disjoint
    populations, so two metrics.

    A skipped frame is a hole in the series. A frame scored with the other ruler is a
    lie in it, and max() over the series finds every one."""
    fresh = 12345.0
    calls = {"n": 0}

    def flaky(rois, labels=None):
        calls["n"] += 1
        if calls["n"] % 3:          # stale two frames out of three
            return None
        return [fresh] * len(rois)

    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=3, full_res_multi=flaky)
    af.start_observe()
    _run(af, 60)

    assert calls["n"] > 10, "the full-res path was never consulted"
    assert rows, "every frame was dropped -- the fix must not silence the log entirely"
    odd = sorted({r["score"] for r in rows} - {fresh})
    assert not odd, f"rows logged on a stale batch, scored with the other metric: {odd}"


def test_the_score_callback_is_told_which_OBJECT_each_roi_belongs_to() -> None:
    """The app names each saved crop from this, and its own loop index is only a POSITION
    in the pinned ROI list (a preview pane): positions rebind every time an object retires
    and the list shifts up. Measured on the 2026-08-16 run, all 29 objects passed through
    all three positions, so position-numbered files cannot be grouped by object -- which
    is exactly how a 'p1 over time' filmstrip came to show a dozen unrelated patches. The
    oids must reach the callback, and must still identify the same patch on the frame
    after one retires."""
    seen: list = []

    def probe(rois, labels=None):
        seen.append((tuple(r[1] for r in rois), None if labels is None else tuple(labels)))
        return [1.0] * len(rois)

    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=3, full_res_multi=probe)
    af.start_observe()
    # Long enough that patches actually retire and the positions rebind -- with nothing
    # retiring, a position index and an oid are indistinguishable and this proves nothing.
    _run(af, 150)
    assert af._rs.next_oid > af.cfg.observe_objects, (
        f"no patch retired in the run (next_oid={af._rs.next_oid}), so position and oid "
        f"cannot be told apart -- feed more frames")

    assert seen, "the score callback was never called"
    labelled = [(r, l) for r, l in seen if l is not None]
    assert labelled, "labels never arrived -- crops would be named by list position"
    for rois, labels in labelled:
        assert len(labels) == len(rois), f"{len(labels)} labels for {len(rois)} rois"
        assert len(set(labels)) == len(labels), f"an oid was reused in one frame: {labels}"
    # An oid follows its patch, so once patches retire the labels advance past the number
    # of positions. Checked against the engine's own counter rather than a hardcoded
    # number: a position index can never exceed observe_objects-1, so this equality can
    # only hold for real oids.
    top = max(max(l) for _r, l in labelled)
    assert top == af._rs.next_oid - 1, (
        f"highest label {top} != newest oid {af._rs.next_oid - 1} -- "
        f"these are not object ids")
    assert top > af.cfg.observe_objects - 1, (
        f"label {top} is within position range 0..{af.cfg.observe_objects - 1}")


def test_open_loop_steps_every_frame_and_scores_every_frame() -> None:
    """Open loop must not throw frames away. Flight runs open loop.

    This used to step on alternate frames and skip the one in between, because the panel
    is mid-transition right after a step and a 250 us shutter landing there records the
    LCD physically partway between two scene positions -- a 50/50 blend of two pieces of
    the city. Live 2026-08-19: 6% of scored frames, score halved (object 0 at a clamped
    ROI: 344, 337, 185), indistinguishable from real defocus downstream.

    Lockstep replaced that dodge with a fix: the panel is provably static before the
    shutter opens. The alternation stayed on regardless and cost half the AF samples of
    every open-loop run -- including in flight, where ``_orthosim_step`` still runs (it
    is deliberately ungated), there is no panel, and nothing was ever unsettled. So an
    open-loop bench run can catch a transition again; lockstep is where clean
    measurements come from now.

    The interval is the half that is easy to get wrong: with the clock restarted every
    frame, each step must report ONE frame period, not two."""
    sys.path.insert(0, str(REPO / "host"))
    from capture.app import MainWindow          # noqa: E402  (heavy import)

    class _Sock:
        def __init__(self): self.sent = []
        def sendto(self, payload, _addr): self.sent.append(float(payload))

    sock = _Sock()

    class _Owner:
        _orthosim_step_sock = sock
        _orthosim_sock = MainWindow._orthosim_sock   # shared with the Align readout
        _orthosim_step_t = 1.0      # pre-seeded: frame 1 is not the clock-start case
        _af_crop_scores = [10.0]
        _af_crop_seq = 1
        _af_crop_scored_seq = 0
        _af_pair_stale = 0
        _af_pair_fresh = 0
        _af_crops_active = False
        _af_crop_labels_pinned = [7]
        _af_crop_answered = [7]     # what the pipeline says this batch was cut with
        # Real method, with saving switched off: this test is about which frames are SCORED,
        # and the accept path now also writes crops. A stub that swallowed the call would let
        # the two drift apart, which is exactly the bug of 2026-09-01.
        _save_af_crops = MainWindow._save_af_crops
        class _focus_panel:
            @staticmethod
            def is_save_focus_enabled(): return False
        def _active_pipeline(self):
            class _P:
                def set_preview_rois(self, _r, **_kw): pass
            return _P()
        def _log(self, _m): pass

    # Frame-paced fake clock. The real one is called once per STEPPING frame, so letting
    # it tick per call would hide whether the interval spans one frame or two.
    import capture.app as appmod                # noqa: E402
    FRAME_S, clock, real_time = 0.25, {"t": 100.0}, appmod.time

    class _T:
        @staticmethod
        def monotonic(): return clock["t"]

    o = _Owner()
    o._orthosim_step_t = clock["t"]             # last step: one frame ago
    pattern = []
    appmod.time = _T
    try:
        for _ in range(6):
            clock["t"] += FRAME_S
            before = len(sock.sent)
            MainWindow._orthosim_step(o)
            stepped = len(sock.sent) > before
            # Re-arm a fresh batch each frame so the refusal can only come from the flag.
            o._af_crop_seq += 1
            got = MainWindow._af_full_res_multi(o, [(0, 0, 8, 8)], [7])
            pattern.append((stepped, got is not None))
    finally:
        appmod.time = real_time

    assert [p[0] for p in pattern] == [True] * 6, (
        f"the scene must advance on EVERY frame: {[p[0] for p in pattern]}")
    assert [p[1] for p in pattern] == [True] * 6, (
        f"every frame must be scored in open loop: {[p[1] for p in pattern]}")
    # The counter is created lazily on the first skip, so it should not exist at all.
    skips = getattr(o, "_af_unsettled_skips", 0)
    assert skips == 0, (
        f"{skips} frames were discarded as unsettled; open loop must discard none -- "
        f"in flight there is no panel to be unsettled by")
    # ONE frame period per step, since the clock now restarts on every frame. The scene
    # still advances by real elapsed time either way, so the simulated aircraft's speed
    # is unchanged and only the jump size halves -- which is the direction that helps.
    assert sock.sent[1:] == [FRAME_S] * (len(sock.sent) - 1), (
        f"a step reported {sock.sent[1:]} s; expected {FRAME_S} s per step -- "
        f"the scene is advancing at the wrong speed")

    # A send that fails leaves the panel where it was, so the next frame is NOT at risk
    # -- treating it as unsettled would drop a clean frame for free.
    class _Dead(_Sock):
        def sendto(self, _p, _a): raise OSError("display app is down")
    o2 = _Owner(); o2._orthosim_step_sock = _Dead()
    MainWindow._orthosim_step(o2)
    assert o2._af_frame_unsettled is False
    MainWindow._orthosim_step(o2)
    assert o2._af_frame_unsettled is False, (
        "a failed send was treated as a step -- every other frame is discarded for "
        "nothing while the display is down")


def test_a_lagging_crop_batch_is_matched_per_object_not_zipped_by_position() -> None:
    """The crops answer the objects pinned one call ago, and the engine is about to read
    them against the objects it holds NOW. Zipping by position is wrong: a patch retiring
    while another is born on the same frame keeps the count at N while shifting every
    member, [1,2,3] -> [2,3,4]. That happened on 13% of consecutive frames in the 145717
    run -- about once per patch lifetime -- and handed every score to its neighbour, in
    the log AND in the cross-cal ratio the stitch multiplies.

    Dropping the whole batch on mismatch was the first fix, and it over-corrected: the
    objects in both lists have valid, correctly-labelled crops and only the newborn has
    none. It cost ~46% of the 234231 run's samples, against stale=1 in ~216 calls. So the
    invariant under test is per-object, and it is two-sided -- no score may reach the wrong
    object, AND no answerable object may be dropped because a neighbour turned over.

    Exercises the real method with a stub owner, because the lag lives in the app, not in
    the engine."""
    sys.path.insert(0, str(REPO / "host"))
    from capture.app import MainWindow          # noqa: E402  (heavy import, one test)

    class _Pipe:
        def set_preview_rois(self, _rois, **_kw): pass

    class _Owner:
        _af_crop_scores = [10.0, 20.0, 30.0]
        _af_crop_seq = 1
        _af_crop_scored_seq = 0
        _af_pair_stale = 0
        _af_pair_fresh = 0
        _af_crops_active = False
        _af_crop_labels_pinned = [1, 2, 3]
        _af_crop_answered = [1, 2, 3]           # what the pending batch answers
        # Real method with saving off -- see the note in the stepping test.
        _save_af_crops = MainWindow._save_af_crops
        class _focus_panel:
            @staticmethod
            def is_save_focus_enabled(): return False
        def _active_pipeline(self): return _Pipe()
        def _log(self, _m): pass

    rois = [(0, 0, 8, 8), (0, 9, 8, 17), (0, 18, 8, 26)]
    run = MainWindow._af_full_res_multi

    # Same objects as were pinned -> the batch is theirs, hand it over.
    o = _Owner()
    assert run(o, rois, [1, 2, 3]) == [10.0, 20.0, 30.0]

    # Same COUNT, object 1 retired and 4 born. 2 and 3 keep their OWN scores; only the
    # newborn is blank. Position zipping would return [10, 20, 30] here and file object
    # 2's curve under object 1's score, which is the whole point of the labels.
    o = _Owner()
    assert run(o, rois, [2, 3, 4]) == [20.0, 30.0, None], (
        "a shifted object list was zipped by position -- every score is one object off, "
        "or the answerable objects were thrown away with the newborn")
    assert o._af_crop_identity_partial == 1

    # Reordered without any membership change: every object is answerable, so all three
    # come back attached to the right one. The old guard dropped this outright.
    o = _Owner()
    assert run(o, rois, [1, 3, 2]) == [10.0, 30.0, 20.0]

    # Nothing in common -> indistinguishable from having no batch, so say so rather than
    # handing back a list of blanks the caller would have to special-case.
    o = _Owner()
    assert run(o, rois, [7, 8, 9]) is None

    # No labels (the N=2 cross-cal pair before it is wired) keeps the old count check.
    o = _Owner()
    assert run(o, rois, None) == [10.0, 20.0, 30.0]


def test_entering_a_recording_mode_leaves_the_full_res_feed_on() -> None:
    """M0/M1/M6 write nothing at all unless the backend is emitting full-res frames.

    ``_apply_mode_state_after_uart`` switches emission ON for those modes, then a few
    lines later calls ``_set_preview_full_res(False)`` to drop the two-object preview's
    ROIs -- and that used to switch emission straight back OFF, because it guarded only
    against stealing the feed from autofocus. Every recording line came out empty
    (2026-08-18: five lines, ``frames=0`` each) while the preview stayed perfect, since
    the small preview frame is built regardless. Worse, it was invisible in the counters:
    a frame with no full-res payload is the ``None`` sentinel, which is deliberately
    never counted as a drop, so the operator saw written=0 AND dropped=0.

    Exercises the real method with a stub owner -- the bug is in the app's ordering."""
    sys.path.insert(0, str(REPO / "host"))
    from capture.app import MainWindow          # noqa: E402  (heavy import)

    class _Modes:
        def __init__(self, recording): self.recording = recording
        def is_recording_mode(self): return self.recording

    class _Pipe:
        camera_number = 1
        def __init__(self): self.emission = True
        def set_preview_rois(self, _rois): pass
        def set_full_res_emission(self, on): self.emission = bool(on)

    def _run(recording, af_cams=()):
        pipe = _Pipe()

        class _Owner:
            _af_full_res_cams = set(af_cams)
            _modes = _Modes(recording)
            def _active_pipeline(self): return pipe
            def _log(self, _m): pass

        MainWindow._set_preview_full_res(_Owner(), False)
        return pipe.emission

    assert _run(recording=True) is True, (
        "entering a recording mode killed the full-res feed -- every line records "
        "zero frames while the preview still looks fine")
    # Not recording and AF isn't using it: nothing wants full-res, so stop paying the
    # ~255 MB per-frame copy. That saving is the whole reason this branch exists.
    assert _run(recording=False) is False
    # AF's claim still stands on its own, independent of the mode.
    assert _run(recording=False, af_cams=(1,)) is True


def test_a_dead_log_sink_does_not_kill_the_run() -> None:
    """Same lesson as the OrthoSim ground-truth logger (HANDOFF error 6): an OSError
    from a CSV on a flaky mount escaped into the repaint loop and froze the display for
    12 hours while the simulation kept running. A diagnostic must not be able to take
    down the thing it is measuring."""
    with tempfile.TemporaryDirectory() as d:
        lg = ObserveLog(csv_dir=d)
        assert lg.open({"objects": 3})

        class _Boom:
            def writerow(self, _r):
                raise OSError(22, "Invalid argument")

        lg._w = _Boom()
        lg.write({"frame": 1, "t": 0.0, "pos_counts": 0.0, "raw_counts": 0.0,
                  "oid": 0, "born_frame": 0, "age_frames": 0, "roi": (0, 0, 10, 10),
                  "y_centre": 5.0, "score": 1.0, "track_response": 0.9})
        assert lg._w is None, "the sink must disable itself, not keep raising"


def test_the_csv_round_trips() -> None:
    import csv as _csv

    with tempfile.TemporaryDirectory() as d:
        lg = ObserveLog(csv_dir=d)
        path = lg.open({"objects": 2, "offset_counts": -1500.0})
        for i in range(5):
            lg.write({"frame": i, "t": i * 0.3, "pos_counts": -1500.0,
                      "raw_counts": -3000.0, "oid": i % 2, "born_frame": 0,
                      "age_frames": i, "roi": (10, 20 + i, 110, 120 + i),
                      "y_centre": 70.0 + i, "score": 100.0 + i,
                      "track_response": 0.87})
        lg.close()
        with open(path, newline="", encoding="utf-8") as fh:
            body = [r for r in _csv.reader(fh) if r and not r[0].startswith("# ")]
    assert body[0] == COLUMNS, body[0]
    assert len(body) == 6, f"header + 5 rows expected, got {len(body)}"
    assert body[1][COLUMNS.index("oid")] == "0"
    assert body[2][COLUMNS.index("age_frames")] == "1"


def _synth_csv(path, *, down: bool, pos_gain: float, age_gain: float,
               frames: int = 60, life: int = 20) -> None:
    """A fake observation with a KNOWN cause mix, to check the report can read it.

    ``pos_gain`` scales score by height in frame; ``age_gain`` scales it by how long
    the patch has been tracked. Reversing the scroll flips which of the two moves."""
    lg = ObserveLog(csv_dir=os.path.dirname(path))
    lg.open({"synthetic": 1})
    for f in range(frames):
        # The outgoing patch (born a life ago, not yet culled) overlaps the incoming
        # one -- the same stagger the engine runs, and the only time a ratio exists.
        for oid in (f // life - 1, f // life):
            if oid < 0:
                continue
            born = oid * life
            age = f - born
            if not 0 <= age < life + life // 2:
                continue
            travel = age / float(life)               # 0 -> 1 across its life
            y = travel * 1800 if down else (1.0 - travel) * 1800
            score = 100.0 * (1.0 + pos_gain * (y / 1800.0)) * (1.0 - age_gain * travel)
            lg.write({"frame": f, "t": f * 0.3, "pos_counts": 0.0, "raw_counts": 0.0,
                      "oid": oid, "born_frame": born, "age_frames": age,
                      "roi": (100, int(y) - 50, 700, int(y) + 50), "y_centre": y,
                      "score": score, "track_response": 0.9})
    lg.close()
    os.replace(lg.path, path)


def test_the_report_tells_a_position_cause_from_an_age_cause() -> None:
    """The report's entire job. If it cannot separate these two on data where the
    answer is known by construction, it cannot be trusted on the rig."""
    sys.path.insert(0, str(REPO / "tools"))
    import observe_report as rep

    with tempfile.TemporaryDirectory() as d:
        # Pure position: score depends only on height, not on age.
        fwd = os.path.join(d, "pos_fwd.csv")
        rev = os.path.join(d, "pos_rev.csv")
        _synth_csv(fwd, down=False, pos_gain=0.8, age_gain=0.0)
        _synth_csv(rev, down=True, pos_gain=0.8, age_gain=0.0)
        r_f = [p["ratio"] for p in rep.pairs(rep.read(fwd)[0])]
        r_r = [p["ratio"] for p in rep.pairs(rep.read(rev)[0])]
        import statistics
        m_f, m_r = statistics.median(r_f), statistics.median(r_r)
        assert (m_f - 1.0) * (m_r - 1.0) < 0, (
            f"a pure-position cause must flip the ratio about 1.0, got {m_f:.3f}/{m_r:.3f}")

        # Pure age: the newer patch always wins, whichever way the scene runs.
        afw = os.path.join(d, "age_fwd.csv")
        arv = os.path.join(d, "age_rev.csv")
        _synth_csv(afw, down=False, pos_gain=0.0, age_gain=0.5)
        _synth_csv(arv, down=True, pos_gain=0.0, age_gain=0.5)
        a_f = statistics.median([p["ratio"] for p in rep.pairs(rep.read(afw)[0])])
        a_r = statistics.median([p["ratio"] for p in rep.pairs(rep.read(arv)[0])])
        assert a_f > 1.0 and a_r > 1.0, (
            f"an age cause must survive reversal, got {a_f:.3f}/{a_r:.3f}")
        assert abs(a_f - a_r) < 0.05, f"and barely move: {a_f:.3f} vs {a_r:.3f}"

        # And the direction it prints is read off the data, not a flag.
        assert rep.direction(rep.read(fwd)[0]).startswith("up")
        assert rep.direction(rep.read(rev)[0]).startswith("down")

        rep.report_one(fwd, None)  # must not raise on a real read


def test_saved_patches_are_named_so_the_outliers_can_be_found() -> None:
    """Several patches are saved from ONE frame, so the timestamp alone no longer
    identifies a file, and the folder has to answer two questions by filename alone:
    "is every crop of object 7 really the same object?" and "is a 43000-scoring patch
    actually sharp?".

    That makes the tag a PREFIX, not a suffix. With it trailing, a name sort is a time
    sort, so the objects interleave and following one through its life means picking its
    files out of the listing by hand. Leading, each object's life is contiguous."""
    import datetime as dt

    from capture.file_naming import build_focus_frame_name

    w = dt.datetime(2026, 8, 16, 14, 30, 5, 123000)

    def name(tag, when=None):
        return build_focus_frame_name(drive_root="D:\\", project_raw="Project",
                                      camera_serial="2439008", when=when or w,
                                      tag=tag).basename()

    assert name("") == "Cam2439008_20260816_Project_focus_143005_123.tif", (
        "an untagged crop must keep its old name -- existing runs still write these")
    a, b = name("OID000_y0400_s1500"), name("OID001_y1408_s43254")
    assert a != b, "two patches from one frame must not collide"
    assert a.startswith("OID000_y0400_s1500_") and "s43254" in b
    assert "/" not in name("bad/name:*?") and "*" not in name("bad/name:*?")

    # The property that matters: sorting groups by object, and within an object it runs in
    # capture order. Two objects alive across two frames, interleaved as they are written.
    later = dt.datetime(2026, 8, 16, 14, 30, 5, 456000)
    listing = sorted([
        name("OID009_y0400_s1500"), name("OID010_y0600_s900"),
        name("OID009_y1200_s1400", later), name("OID010_y1400_s800", later),
    ])
    got = [f.split("_Cam")[0] for f in listing]
    assert got == ["OID009_y0400_s1500", "OID009_y1200_s1400",
                   "OID010_y0600_s900", "OID010_y1400_s800"], got
    # Unpadded, "OID10" sorts between "OID1" and "OID2", and y762 lands after y1093.
    assert sorted(["OID009", "OID010", "OID002"]) == ["OID002", "OID009", "OID010"]
    assert sorted(["y0762", "y1093"]) == ["y0762", "y1093"]


def test_a_frame_that_did_not_move_must_not_forget_which_way_the_scene_flows() -> None:
    """Regression, 2026-09-01. The leading edge was recomputed from the CURRENT frame's
    motion, and ``leading_edge_from_motion`` reads |dy| < 0.5 px as static -- which makes
    the picker search the whole frame instead of the band content enters from.

    On the stepped rig the scene only advances on every other frame, so half of all
    frames looked static and half of all patches were born wherever they landed --
    including at the trailing edge, where they retire after one frame. Measured on the
    12:37 run: births spanned y238..y1832 of a 2382 px frame and the median patch lived
    3 scored frames out of a possible 5.6. A patch born at the exit is not a shorter
    measurement, it is no measurement: OBSERVE compares a patch against ITSELF over
    time, and a run of 1 has nothing to compare.

    Flow direction is a property of the scene, not of one frame, so it latches."""
    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=3)
    af.start_observe()
    resolved, moved = [], []
    shift = 0
    for i in range(40):
        if i % 2 == 0:      # stepped rig: OrthoSim advances only on the frames we nudge
            shift += 48
        af.feed_frame(np.roll(_textured(0), shift, axis=0))   # content flows DOWN -> lead=top
        resolved.append(af._resolve_lead())
        moved.append(abs(af._rs.motion_dy) > 0.5)

    assert not all(moved), "the feed never held still -- this tests the held frames"
    assert any(moved), "the feed never advanced -- there is no flow to remember"
    still = [lead for lead, m in zip(resolved[2:], moved[2:]) if not m]
    assert still and set(still) == {"top"}, (
        f"a held frame forgot the flow direction and sent the picker over the whole "
        f"frame: {sorted(set(still))}")


def test_the_display_covering_a_missed_repaint_in_one_jump_is_not_scored() -> None:
    """Regression, 2026-09-07. ``_orthosim_step`` nudges the display once per two frames
    and the display advances by the MEASURED interval, so a healthy run alternates hold,
    ~150 rows, hold, ~150 rows. A datagram that lands after the repaint is not lost --
    ``step_dt`` sums the backlog -- so the scene covers two intervals in ONE jump.

    The 10:28 run had 8 such pairs in 117 intervals: travel 0 rows, then 242-304. A
    shutter open across that jump records the panel partway between two scene positions
    ~250 rows apart, and it is frame-global -- OID001/002/003 all came back with an
    identical dy=-247 dx=+7 ghost, every score down 6-18%, track_response 0.860 against
    0.908-0.916 either side. A blend halves the score and nothing downstream can tell a
    halved score from real defocus, so the frame must not become a sample.

    The reference is the UPPER QUARTILE of recent travel, not the median: on a stepped
    rig the median sits between the held and moving levels, so a median test would refuse
    every genuine advance. That is the second assertion here."""
    rows: list = []
    af, _axis, _shaft = _engine(rows, objects=3)
    af.start_observe()

    lurch_at = 30                      # iteration index, not frame number
    shift = 0
    for i in range(48):
        if i % 2 == 0:
            shift += 48                # the designed alternation: hold, advance, ...
        if i == lurch_at:
            shift += 96                # backlog paid off in one repaint, on top of it
        af.feed_frame(np.roll(_textured(0), shift, axis=0))

    logged = {r["frame"] for r in rows}
    assert len(logged) > 10, f"almost nothing was scored at all: {sorted(logged)}"
    # rs.frames counts from 1, so iteration i is frame i + 1.
    assert (lurch_at + 1) not in logged, (
        "the lurch frame became a sample -- a two-position blend is now in the curve")
    assert af._rs.lurch_frames == 1, (
        f"expected exactly the one injected lurch, got {af._rs.lurch_frames}: a median"
        " reference flags every advancing frame on a stepped rig, which is why this"
        " uses the upper quartile")
    # Surgical: the ordinary advance on the very next frame still scores, which also
    # shows the ROIs were advanced through the lurch rather than left behind.
    assert (lurch_at + 2) in logged, (
        f"the guard took the following frame out too: logged={sorted(logged)[-8:]}")


def test_only_the_crops_that_became_samples_reach_the_disk() -> None:
    """The folder must hold the same frames as the observe CSV, because it is read as if it
    does. Saving every cropped frame instead wrote step-blended patches carrying a score
    computed at save time and reported to nobody: on 2026-09-01 six of ten OID029 crops were
    files the engine had refused, and the 50/50 blend among them read as proof the AF was
    scoring blends when it had rejected every one.

    Drives the real two-step flow -- crops arrive, THEN the engine accepts or refuses them on
    the same frame -- because the bug was entirely in doing those two things in one step."""
    from capture.app import MainWindow

    written, pinned = [], []

    class _Pipe:
        def note_preview_crops_consumed(self): pass
        def set_preview_rois(self, _r, **kw): pinned.append(kw)
        def submit_focus_capture(self, _img, tag): written.append(tag)

    class _Panel:
        def is_save_focus_enabled(self): return True

    class _Owner:
        _focus_panel = _Panel()
        _field_map = None
        _af_crops_active = True
        _af_crop_seq = 0
        _af_crop_scored_seq = 0
        _af_pair_stale = 0
        _af_pair_fresh = 0
        _af_frame_unsettled = False
        _focus_frame_ts = "frame-these-rois-came-off"
        _save_af_crops = MainWindow._save_af_crops
        # Real pool, not a serial stub: the scores now come back through it, and a stub
        # would let the ordering guarantee this whole test rests on go untested.
        _af_score_pool = MainWindow._af_score_pool
        _active_pipeline = lambda self: _Pipe()
        _log = lambda self, _m: None
        _af_crop_rois = property(lambda self: self._af_crop_rois_pinned)

    rng = np.random.default_rng(7)
    OLD_PIN = [(10, 1147, 90, 1200), (10, 1582, 90, 1640)]
    # Two live patches; the pipeline says the batch was cut with the pin named "pin-A",
    # which is objects 1 and 2.
    def arrive(o, pin_ts="pin-A"):
        o._af_pin_by_ts = {"pin-A": ([1, 2], OLD_PIN)}
        o._af_crop_rois_pinned = list(OLD_PIN)
        o._af_crop_labels_pinned = [1, 2]
        crops = [rng.integers(0, 4096, (8, 8), dtype=np.uint16) for _ in range(2)]
        MainWindow._on_raw_focus_preview_crops(o, crops, "t", pin_ts)

    # A settled frame where the engine still holds both objects: both are samples, both
    # saved -- and named with the y the TRACKER holds now (1500/1900), not the stale pin
    # they were requested under (1147/1582). The pipeline shifts the pin by the travel it
    # measures, so the pixels are at the tracker's position and the pin would misreport it.
    now = [(10, 1500, 90, 1560), (10, 1900, 90, 1960)]
    o = _Owner(); arrive(o)
    MainWindow._af_full_res_multi(o, now, [1, 2])
    assert [t.split("_s")[0] for t in written] == ["OID001_y1500", "OID002_y1900"], written

    # Object 1 retired and 3 was born. Object 1's crop is valid and scored, but the engine
    # never receives it, so it must not appear on disk either.
    written.clear()
    o = _Owner(); arrive(o)
    MainWindow._af_full_res_multi(o, now, [2, 3])
    assert [t.split("_s")[0] for t in written] == ["OID002_y1500"], written

    # The frame after a scene step: refused wholesale, so nothing is written. This is the
    # half of every stepped run that used to fill the folder with blends.
    written.clear()
    o = _Owner(); arrive(o); o._af_frame_unsettled = True
    MainWindow._af_full_res_multi(o, now, [1, 2])
    assert written == [], written

    # A stale batch (engine asks twice, no new crops) must not write the same patch again
    # under the next pin's name.
    written.clear()
    o = _Owner(); arrive(o)
    MainWindow._af_full_res_multi(o, now, [1, 2])
    n = len(written)
    MainWindow._af_full_res_multi(o, now, [1, 2])
    assert len(written) == n, written

    # A batch may answer the pin BEFORE last. The pipeline picks a pin up whenever it next
    # reads one, so the GUI cannot infer which pin a batch was cut with; labelling by "the
    # last thing we pinned" hands every crop to its neighbour. Measured on the 14:47 run:
    # OID003_y1039 correlates 0.93 with OID002_y1731 and 0.10 with its own object, and its
    # score (325 among neighbours of 179) is oid 2's too, because the SCORES are matched
    # through the same list.
    written.clear()
    o = _Owner()
    o._af_pin_by_ts = {"older": ([1, 2], OLD_PIN),
                       "newer": ([2, 3], [(10, 1300, 90, 1360), (10, 1700, 90, 1760)])}
    o._af_crop_rois_pinned = [(10, 1300, 90, 1360), (10, 1700, 90, 1760)]
    o._af_crop_labels_pinned = [2, 3]            # what the GUI pinned most recently
    crops = [rng.integers(0, 4096, (8, 8), dtype=np.uint16) for _ in range(2)]
    MainWindow._on_raw_focus_preview_crops(o, crops, "t", "older")   # ...but cut with THIS
    MainWindow._af_full_res_multi(o, now, [1, 2])
    assert [t.split("_s")[0] for t in written] == ["OID001_y1500", "OID002_y1900"], (
        f"a batch that answered the older pin was labelled with the newer one: {written}")

    # Scoring ROIs must be pinned to FOLLOW the scene (the field map's must not), and must
    # name the frame they were derived from -- without it the pipeline cannot tell a pin
    # that is one frame stale from one that is three, and under-shifts the crop.
    want = {"follow": True, "src_ts": "frame-these-rois-came-off"}
    assert pinned == [want] * 6, pinned


def test_patches_scored_in_parallel_come_back_in_the_order_they_were_pinned() -> None:
    """The scores are matched to objects BY POSITION, so order is the whole contract.

    Scoring moved onto a thread pool (5 live-sized patches: 122 ms serial, 50 ms pooled,
    off the GUI thread whose busy time is the conflation). `pool.map` is ordered and
    `list` waits for all of it, so the handler still hands back exactly the serial list
    -- and if that ever stops being true the scores silently swap objects, which reads
    downstream as two patches trading focus curves, not as an error.

    Distinct sharpness per patch, so a shuffle cannot pass by coincidence, and a None
    hole in the middle because an empty pane must keep its slot rather than close it up.
    """
    sys.path.insert(0, str(REPO / "host"))
    from capture.app import MainWindow, _af_score_crop   # noqa: E402

    rng = np.random.default_rng(11)
    crops = []
    for blur in (0, 3, 7, 0, 5):                        # different focus per patch
        c = rng.integers(0, 4096, (64, 64), dtype=np.uint16).astype(np.float32)
        if blur:
            c = cv2.GaussianBlur(c, (0, 0), blur)
        crops.append(c.astype(np.uint16))
    crops.insert(2, None)                               # an empty pane, mid-list

    class _Owner:
        _field_map = None
        _af_crops_active = True
        _af_crop_seq = 0
        _af_score_pool = MainWindow._af_score_pool
        def _active_pipeline(self): return None
        def _log(self, _m): pass

    o = _Owner()
    MainWindow._on_raw_focus_preview_crops(o, crops, "t", "pin")
    want = [_af_score_crop(c) for c in crops]           # the serial answer
    assert o._af_crop_scores == want, (
        f"pooled scoring changed the result:\n  got  {o._af_crop_scores}\n  want {want}")
    assert o._af_crop_scores[2] is None, "the empty pane lost its slot"
    live = [s for s in o._af_crop_scores if s is not None]
    assert len(set(live)) == len(live), \
        "the patches are not distinguishable, so this test cannot detect a shuffle"
    o._af_score_pool().shutdown(wait=True)


def test_the_crop_window_is_shifted_by_the_travel_measured_before_cropping() -> None:
    """The pin can only ever reach a LATER frame than the one it was derived from, so the
    acq thread shifts it by the scene travel it measures on the frame it is about to crop.
    Measured, not predicted -- so it must hold for the bench's stop-go motion too, where a
    predictor built on 'next frame moves like this one' is exactly anti-correlated.

    Also pins the accumulation rule: a conflated frame never reaches the GUI, so its travel
    is absent from the next pin and has to be carried, or the correction under-shoots by
    however many frames the GUI dropped -- 28% of them on this rig."""
    sys.path.insert(0, str(REPO / "host"))
    from capture.pipeline import AcquisitionThread          # noqa: E402

    class _Acq:
        label = "test"
        _roi_follow = True
        _roi_tracker = None
        _roi_debt_x = _roi_debt_y = 0.0
        _roi_shift_max = 0.0
        _roi_pin: tuple = ([(0, 0, 8, 8)], True, None)
        _roi_travel_x = _roi_travel_y = 0.0
        _preview_rois = [(0, 0, 8, 8)]
        def __init__(self):
            self._roi_travel_at = {}      # per instance, or a, b and c share an odometer
        set_preview_rois = AcquisitionThread.set_preview_rois
        _accrue_roi_travel = AcquisitionThread._accrue_roi_travel

    # A scene panning a known amount per frame. Structured, because phase correlation on
    # white noise has nothing to lock onto.
    rng = np.random.default_rng(3)
    tall = cv2.GaussianBlur(
        rng.integers(0, 16383, (900, 600), dtype=np.uint16).astype(np.float32), (0, 0), 2)
    STEP = 40
    frames = [tall[i * STEP:i * STEP + 400].astype(np.uint16) for i in range(6)]

    a = _Acq()
    a.set_preview_rois([(0, 0, 8, 8)], follow=True, src_ts="f0")
    a._accrue_roi_travel(frames[0], "f0")            # first frame only primes the tracker
    assert (a._roi_debt_x, a._roi_debt_y) == (0.0, 0.0)

    # Three frames with no new pin: the debt keeps growing, because that is exactly the
    # case of the GUI conflating frames it never saw.
    for i, f in enumerate(frames[1:4], 1):
        a._accrue_roi_travel(f, f"f{i}")
    assert abs(abs(a._roi_debt_y) - 3 * STEP) < 6, a._roi_debt_y

    # A pin is charged the travel since ITS OWN frame, not since it happened to arrive.
    # The GUI conflates under load, so a pin can be 3 frames old by the time it lands; the
    # old reset-on-arrival rule assumed 1 and left the window lagging by the difference.
    # Measured on the 14:18 run, oid 1: 278 px of window motion against 564 of scene.
    a.set_preview_rois([(0, 0, 8, 8)], follow=True, src_ts="f1")   # derived 3 frames ago
    a._accrue_roi_travel(frames[4], "f4")
    assert abs(abs(a._roi_debt_y) - 3 * STEP) < 8, (
        f"a stale pin was charged for one frame instead of three: {a._roi_debt_y}")

    # ...and a pin that IS one frame old is charged for exactly one.
    a.set_preview_rois([(0, 0, 8, 8)], follow=True, src_ts="f4")
    a._accrue_roi_travel(frames[5], "f5")
    assert abs(abs(a._roi_debt_y) - STEP) < 6, a._roi_debt_y

    # Fixed tiles (the field map) must never be shifted, whatever the scene does.
    b = _Acq()
    b.set_preview_rois([(0, 0, 8, 8)], follow=False)
    for i, f in enumerate(frames):
        b._accrue_roi_travel(f, f"f{i}")
    assert (b._roi_debt_x, b._roi_debt_y) == (0.0, 0.0), (b._roi_debt_x, b._roi_debt_y)

    # The pin and its debt are ONE snapshot. The full-res slot fetch sits between the
    # measurement and the cut, so a pin can land in that window; the cut has to use what
    # accrue HANDS BACK, not a later re-read of the attribute.
    c = _Acq()
    c.set_preview_rois([(0, 0, 8, 8)], follow=True, src_ts="g0")
    c._accrue_roi_travel(frames[0], "g0")
    snapshot, _pin, _dx, _dy = c._accrue_roi_travel(frames[1], "g1")
    c.set_preview_rois([(0, 500, 8, 508)], follow=True, src_ts="g1")   # GUI posts mid-cut
    assert snapshot == [(0, 0, 8, 8)], f"crop would use the new pin, old debt: {snapshot}"

    nxt, _pin, _dx, _dy = c._accrue_roi_travel(frames[2], "g2")
    assert nxt == [(0, 500, 8, 508)], f"the new pin never took effect: {nxt}"
    assert abs(abs(c._roi_debt_y) - STEP) < 6, c._roi_debt_y


def _lockstep_owner(clock):
    """A stub MainWindow carrying just what the lockstep driver touches."""
    sys.path.insert(0, str(REPO / "host"))
    from capture.app import MainWindow          # noqa: E402  (heavy import)

    import queue

    class _Serial:
        def __init__(self): self.triggers = 0; self.external = []
        def send_test_trigger(self): self.triggers += 1
        def set_external_trigger(self, on): self.external.append(on)

    class _Sock:
        def __init__(self): self.sent = []
        def sendto(self, payload, _addr): self.sent.append(payload)

    class _Bar:
        def __init__(self): self.messages = []
        def showMessage(self, m): self.messages.append(m)

    class _Timer:
        def __init__(self): self.running = False
        def start(self, _ms): self.running = True
        def stop(self): self.running = False

    class _Panel:
        def __init__(self): self.reset_calls = 0
        def reset_lockstep(self): self.reset_calls += 1

    class _Owner:
        LS_READY_TIMEOUT_S = MainWindow.LS_READY_TIMEOUT_S
        LS_FRAME_TIMEOUT_S = MainWindow.LS_FRAME_TIMEOUT_S
        LS_MAX_RETRIES = MainWindow.LS_MAX_RETRIES
        _ls_send_step = MainWindow._ls_send_step
        _ls_frame_arrived = MainWindow._ls_frame_arrived
        _ls_poll = MainWindow._ls_poll
        _ls_stop = MainWindow._ls_stop
        _orthosim_sock = MainWindow._orthosim_sock

        def __init__(self):
            self._orthosim_step_sock = _Sock()
            self._serial = _Serial()
            self._bar = _Bar()
            self._panel = _Panel()
            self._focus_panel = self._panel
            self._ls_timer = _Timer(); self._ls_timer.running = True
            self._ls_ready_q = queue.SimpleQueue()
            self._ls_on = True
            self._ls_seq = 0
            self._ls_awaiting = None
            self._ls_sent_at = clock["t"]
            self._ls_triggered_at = None
            self._ls_retries = self._ls_frames = self._ls_timeouts = 0

        def statusBar(self): return self._bar
        def _log(self, _m): pass

    return MainWindow, _Owner


def _fake_clock():
    """Patch app.py's ``time`` so the timeout branches are reachable without sleeping."""
    import capture.app as appmod
    clock = {"t": 100.0}
    real = appmod.time
    appmod.time = type("T", (), {"monotonic": staticmethod(lambda: clock["t"])})()
    return clock, appmod, real


def test_one_lockstep_cycle_is_one_step_one_trigger_and_one_frame() -> None:
    """The handshake's whole job: the shutter opens ONLY between a READY and the next
    STEP, i.e. into a panel the display has said is painted and settled and which it
    will not move again until we report the frame."""
    clock, appmod, real = _fake_clock()
    try:
        import orthosim_codec as codec
        MainWindow, _Owner = _lockstep_owner(clock)
        o = _Owner()

        MainWindow._ls_send_step(o)
        assert codec.decode_step(o._orthosim_step_sock.sent[-1])[0] == 1
        assert o._serial.triggers == 0, "triggered before the display said it was ready"

        # The display answers, so now -- and only now -- the shutter may open.
        o._ls_ready_q.put(1)
        MainWindow._ls_poll(o)
        assert o._serial.triggers == 1, "a READY did not produce a trigger"
        assert len(o._orthosim_step_sock.sent) == 1, "asked for a new picture too early"

        # The frame lands: that is "done acquiring", so ask for the next picture.
        MainWindow._ls_frame_arrived(o)
        assert codec.decode_step(o._orthosim_step_sock.sent[-1])[0] == 2
        assert o._ls_frames == 1

        # Every step is the SAME size. Uniform travel is what removes the variable-jump
        # artefacts outright, rather than detecting them after the fact.
        dts = {codec.decode_step(p)[1] for p in o._orthosim_step_sock.sent}
        assert len(dts) == 1, f"steps were not uniform: {dts}"
    finally:
        appmod.time = real


def test_a_stale_READY_does_not_open_the_shutter() -> None:
    """A late answer to a step we already gave up on would fire the trigger at a moment
    the display never vouched for -- exactly the uncontrolled phase lockstep removes."""
    clock, appmod, real = _fake_clock()
    try:
        MainWindow, _Owner = _lockstep_owner(clock)
        o = _Owner()
        MainWindow._ls_send_step(o)          # seq 1 outstanding
        o._ls_ready_q.put(7)                 # an answer to something else entirely
        MainWindow._ls_poll(o)
        assert o._serial.triggers == 0, "fired on a READY that did not match the step"
        assert o._ls_awaiting == 1, "a stale READY cleared the real wait"
    finally:
        appmod.time = real


def test_a_lost_READY_retries_the_same_step_and_then_hands_the_trigger_back() -> None:
    """Lockstep inverts the property the open-loop channel was built on: there a lost
    datagram cost one step, here it stops the rig dead, and a 90 s run is ~430 round
    trips. So the app must notice, re-ask for the SAME seq -- asking for the next would
    double-step the scene, which is the very artefact being removed -- and finally give
    the trigger back rather than leaving the operator with a frozen preview and a
    camera that has been told not to fire."""
    clock, appmod, real = _fake_clock()
    try:
        import orthosim_codec as codec
        MainWindow, _Owner = _lockstep_owner(clock)
        o = _Owner()
        MainWindow._ls_send_step(o)
        assert codec.decode_step(o._orthosim_step_sock.sent[-1])[0] == 1

        for expected_try in range(1, o.LS_MAX_RETRIES + 1):
            clock["t"] += o.LS_READY_TIMEOUT_S + 0.01
            MainWindow._ls_poll(o)
            assert o._ls_retries == expected_try, o._ls_retries
            seq = codec.decode_step(o._orthosim_step_sock.sent[-1])[0]
            assert seq == 1, f"retry asked for step {seq}; the display would double-step"
            assert o._ls_on is True, "gave up while it still had retries left"

        clock["t"] += o.LS_READY_TIMEOUT_S + 0.01
        MainWindow._ls_poll(o)
        assert o._ls_on is False, "kept waiting forever on a display that never answers"
        assert o._serial.external == [False], (
            f"did not hand the trigger back, so the camera stays disarmed: "
            f"{o._serial.external}")
        assert o._ls_timer.running is False
        assert any("Lockstep" in m for m in o._bar.messages), (
            f"degraded silently, with nothing on screen to explain it: {o._bar.messages}")
    finally:
        appmod.time = real


def test_a_missing_frame_costs_a_frame_and_not_the_whole_run() -> None:
    """Regression, 2026-09-07 12:46. ONE frame went missing in 311 -- a hiccup as the
    operator switched to Fine -- and the loop tore itself down and handed the trigger
    back. Two complete observe runs were then recorded open loop, with the box still
    ticked, by an operator with every reason to think the rig was in lockstep.

    Re-triggering is always safe here and that is the point: the display holds its scene
    position until we report a frame, so the second exposure is of exactly the picture
    the first one was meant to catch."""
    clock, appmod, real = _fake_clock()
    try:
        MainWindow, _Owner = _lockstep_owner(clock)
        o = _Owner()
        MainWindow._ls_send_step(o)
        o._ls_ready_q.put(1)
        MainWindow._ls_poll(o)
        assert o._serial.triggers == 1 and o._ls_awaiting is None

        # One lost frame: re-trigger, stay in lockstep, do NOT advance the scene.
        clock["t"] += o.LS_FRAME_TIMEOUT_S + 0.01
        MainWindow._ls_poll(o)
        assert o._ls_on is True, "one missing frame killed the run again"
        assert o._serial.triggers == 2, "did not re-trigger"
        assert len(o._orthosim_step_sock.sent) == 1, (
            "asked the display to advance while still waiting for the frame")

        # The frame turns up on the retry: back to normal, and the retry count clears so
        # a later isolated hiccup gets the same full allowance.
        MainWindow._ls_frame_arrived(o)
        assert (o._ls_frames, o._ls_retries) == (1, 0)
        assert len(o._orthosim_step_sock.sent) == 2
    finally:
        appmod.time = real


def test_a_camera_that_never_answers_does_eventually_hand_the_trigger_back() -> None:
    """The other half of the loop can break for real too: $T* goes out and nothing ever
    comes back (camera wedged, or the PCB is not running $X1* firmware). Retries must be
    bounded, and the box must not be left ticked over a loop that has stopped."""
    clock, appmod, real = _fake_clock()
    try:
        MainWindow, _Owner = _lockstep_owner(clock)
        o = _Owner()
        MainWindow._ls_send_step(o)
        o._ls_ready_q.put(1)
        MainWindow._ls_poll(o)

        for _ in range(o.LS_MAX_RETRIES + 1):
            clock["t"] += o.LS_FRAME_TIMEOUT_S + 0.01
            MainWindow._ls_poll(o)
        assert o._ls_on is False, "retried forever at a camera that is not coming back"
        assert o._serial.external == [False]
        assert o._panel.reset_calls == 1, (
            "left the Lockstep box ticked over a loop that had stopped -- the exact way "
            "two observe runs got silently recorded open loop")
    finally:
        appmod.time = real


def main() -> int:
    # Collected, not listed: a hand-kept list silently skipped the stale-batch
    # regression on the day it was written. dict preserves definition order.
    tests = [v for k, v in globals().items()
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"  ok: {t.__name__}")
    print("observe tests OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
