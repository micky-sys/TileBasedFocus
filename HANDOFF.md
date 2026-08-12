# Iron 661 Capture — Handoff Notes

> **CRITICAL — M4 vs M5 (do not get this backwards):**
> - **M4 is the FLIGHT focus mode** — the real, deployed autofocus. Its scene is
>   **moving** (the aircraft flies over ground). On the **bench** it is exercised with a
>   **static** scene only because we can't fly; that static bench case is a degenerate
>   test, NOT what M4 is for.
> - **M5 is a BENCH simulation of flight** — it moves the mirrors to fake the scene
>   changing, so the flight tracking behaviour can be validated on the bench without
>   flying. M5 is a proxy for M4-in-flight.
> - **Consequence:** both M4 and M5 run the SAME flight AF algorithm (continuous
>   tracking servo). The fast/slow servo profile must follow **detected scene motion**,
>   never the mode label — keying "M4 = static" is WRONG (M4 in flight is moving).

_Last updated: 2026-08-09 — **OrthoSim GT: the altitude readout was never a measurement**
(§29): the bench had been reporting **10,000 ft while actually simulating ~2,985 ft**, and
nothing in the decode could have noticed — speed/distance/GSD are *authored by the display*
and read back out of the optical bar, and the magnification `M` (camera-px per display-px)
is a hand-set `--M` defaulting to **1.0** that altitude scales as `1/M`. With stock settings
the chain lands on 3.505 cm/px = **9,999.8 ft by construction**. New `measure_scale()`
(`orthosim_decode.py`) measures `M` off the corner fiducials — they sit at *known display-pixel*
positions (3652 px apart), so panel pitch and focal length cancel — averaging opposite edges
so tilt cancels; logged once per session as the `--M` the display should have used. Measured
**M = 3.3553**, independently corroborated by tracker-flow-vs-encoded-speed (3.16–3.36) and
the geometric ceiling `13400/3840 = 3.49`. Relaunching with `--M 3.3553 --zoom 0.4251` gives a
**true 10,000 ft @ 100 kn**. Separately fixed the optical bar, which was failing CRC on
**100 %** of frames: `_sample_bits` used one global `(max+min)/2` while the imaged white level
**ramps >5:1 across the bar** (~205 → ~43, black flat at ~15), so the dim end's whites read as
black; now thresholded against a **local min/max envelope** (`_cell_thresholds`) — verified on
a real saved frame decoding across 38 parameter combinations with identical payload, and
**~75 %** of live frames now decode. Also: the no-decode diagnostic printed a *tautological*
`u8 min/max=0/255` (post-normalise) and only dumped debug when fiducials were **found** — the
opposite of the real failure — both fixed. **Velocity was never wrong** (`M` cancels out of
ground speed). Object lifetime at the new altitude **2.53 s → 4.26 s (10.1 → 20.4 frames)**,
and each 5 cm source pixel now spans 1.4 camera px instead of 4.5. Launch traps recorded:
`OPENCV_IO_MAX_IMAGE_PIXELS` (the ortho is 1.17 gigapixels) and **`--screen 1`** (Qt puts the
Dell 1920×1080 at index 0). New self-checks in `tools/test_orthosim.py` (19 pass).
**2026-08-09 addendum (§29.9): the bench models neither flight case.** In flight the 300 mm
shoots **long edge forward** (objects traverse 13400 px) and the 400 mm **short edge forward**
(9528 px); the bench scrolls vertically, so it traverses the short axis. At 10,000 ft / 100 kn
that makes it **1.41× pessimistic for the 300 mm** (flight = 28.6 frames) and **1.33× optimistic
for the 400 mm** (flight = 15.3 frames). The 400 mm loses twice over — short axis (0.711) **and**
faster ground flow from the longer focal length, since `GSD = h·p/f` (0.750) — for **0.533, i.e.
half the 300 mm's object lifetime**. So a dive-then-climb must fit **~15 frames**, not 20, and
the cross-object handover is **mandatory** for the 400 mm rather than an optimisation. Bench the
worst case by scaling ground speed by 400/300: **`--speed 68.59`** (133.33 kn) → 15.3 frames.
Orientation needs no code change (`leading_edge_from_motion` resolves the edge from measured
`dx`/`dy`).
Previous: 2026-08-02 — **AF climb SNR — lucky imaging + curve-fit peak (log-domain)**
(§27): the dive-then-climb peak search is now hardened against the noisier outdoor/in-flight
focus signal. Each climb position now **dwells `climb_lucky_frames=3` frames and keeps the
MAX** (lucky imaging — turbulence/vibration only ever *blur*, so the sharpest frame in a short
burst is the truest read of focus there); the peak is refined by a local **parabola fit**
(`_fit_peak`, vertex `-b/2a`) instead of a single noisy argmax; and that fit is done in the
**log domain** (`peak_fit_log`) because the variance-based metric's noise is **multiplicative**
(SD ∝ score) — `log` is the variance-stabilising transform and `log(Gaussian)==parabola`. All
three are flag-gated in `app.py` (revert: `climb_lucky_frames=1`, `peak_curve_fit=False`,
`peak_fit_log=False`). Also added a **"2-obj track"** preview button (green boxes on the coarse
preview, no AF) to eyeball leading-edge object flow, and **reverted** the M5 sweep-reversal
reset in `DualObjectTracker` (a bench-only artifact). New self-check `tools/test_af_peak.py`
(4/4). Previous: 2026-07-30 — **M6 "Timelapse" mode** (§26): a new recording mode for a
**side-by-side comparison against the Phase One camera** — mirrors held in the mid
(Focus/M4) position firing **one trigger per second**, with full-res frames written to
disk exactly like M0/M1. Added to firmware (`$M6*` → `setMode(6)`, `picsInPlaceDelay =
1000`) and the Iron661 app (`Mode.M6`, "Timelapse" button, added to `_RECORDING_MODES`).
Previous: 2026-07-30 — **encoder closed-loop autofocus (AF v2) + servo-BSOD root cause**
(§25): the AMT203S motor-shaft encoder is integrated (`EncoderTracker`/`EncoderAxis` in
`focus_axis.py`, `$ENC` parsed in `serial_client.py`; polarity is **negative** —
`encoder_counts_per_pulse=-24` — because a `-` pulse raises the count) and the M4/M5
autofocus was **rewritten** from the §24 dither servo into a paced open-loop
**dive-then-climb** engine (`autofocus_v2.py`) that only *reads* the encoder: dive below
focus (str3), climb up in fine str0 steps taking one settled `(encoder, score)` sample per
frame, peak-detect, then park by descending to the dive bottom and creeping back up onto the
peak **at the climb's own pace**. Making the climb score only the **settled** frame per step
(`climb_settle_frames`) removed the motion-lag that parked the lens a few counts below focus →
**focuses consistently** on the bench. **STOP now halts in place** (the old `park_upward`
re-park burst yanked the lens off focus and corrupted the next run). The repeated **BSODs were
the OLD flooding servo, not the hardware** — bisection: old build fine overnight, new code fine
idle, serial stress wedged the PCB but didn't BSOD, full AF cycle on the new engine completed
clean; the cure was the paced engine + **non-blocking serial TX thread** + **frame conflation**
(no GUI-thread serial blocking / 8 MB frame pileup). Still watch: BSODs were sporadic, and a
separate **firmware bug wedges the PCB after ~676 pulses** (power-cycle to recover). Committed
as "Add encoder closed-loop autofocus (AF v2)"; self-checks `tools/test_autofocus_v2.py` (9).
Previous: 2026-07-27 — **manual backlash telemetry (scrapped the automatic test)**:
the automatic on-rig backlash probe was removed after it kept mis-measuring lash (per-frame
sharpness noise + a 30 ms exposure faked an early "lens responded", giving implausible ~19-count
readings). Deleted `host/capture/focus_probe.py` + `tools/test_backlash_measure.py` and stripped
all backlash-test code from `app.py`/`focus_panel.py`/`focus_axis.py` + the `$SV`/`$FT` host
wrappers (`serial_comm.py`, `serial_client.py`); the shared AF/Lock-tile/full-res-crop infra
stays. Replaced it with a **manual workflow**: firmware `$S0*` now means the shortest reliable
move — **duty 7000 at a 3 ms pulse** (str0=7000, and `$S0*` also sets `focusTimeIncrement=3`;
`$S1*`–`$S3*` restore 10 ms) — so the operator locks a Fine tile, creeps focus with the Up/Down
hotkeys, and reads the backlash straight off an **always-on bottom-left scatter** of focus score
(Y) vs encoder count (X): the forward/back arms' horizontal gap IS the lash. A **"Log CSV"**
toggle writes `timestamp, frame, encoder, focus_score` per locked-tile frame to a
`Iron661_focus_<stamp>.csv` in the **Session logs** folder (`host/capture/focus_csv_log.py`).
Per-frame telemetry is emitted from both focus-frame handlers via `MainWindow._emit_focus_telemetry`
(Fine: `meta["best_score"]` when a tile is locked; Coarse: the pinned full-res crop score when AF
isn't running). New check `tools/test_focus_csv_log.py` (3/3). **Requires a ClearCore reflash**
for the new `$S0`/`focusTimeIncrement` behaviour. Previous: 2026-07-22 — **non-blocking focus pulses (timers, not delays) + AF servo/tile
overhaul for M4-flight & M5-bench** (§24): the ClearCore now fires focus-motor pulses off a
`millis()` timer serviced in `loop()` and inside the sweep busy-waits, so a `+`/`-` pulse no
longer **blocks** the trigger cadence — this is what made the camera frame rate collapse (2.9 Hz
vs the 4.8 Hz cap) and starved M5 during capture. Host AF was reworked to a **continuous dither
servo** with motion-adaptive fast/slow profiles, coarse sweep-to-max escalation, a servo leash,
a snap-back anchor, strong-peak lock and RProp adaptive step; tile ranking is now
saturation-aware (scores unclipped detail only); and AF now **seeds its target from the Fine
preview's held ROI** so it focuses the building/gantry you were looking at instead of re-picking
bushes on a blurry frame. Timing instrumentation (`af-time` lag/`frame_dt`) and a selection-frame
dump were added. `tools/test_autofocus.py` **27/27**. Previous: 2026-07-19 — **focus-encoder decision: motor-shaft AMT203S** (§23):
decided to add an AMT203S-V absolute encoder on the DC-motor shaft to assist AF. It
fixes the §22.1 no-move/stiction failure (verified motion + closed-loop motor
positioning + a power-persistent absolute datum for focus presets), does **not** need
to capture gear/spur lash (the contrast loop already compensates it via same-direction
dither + direction-matched landing), and is chosen over the month-long gear-output
remount and the draw-wire encoder. §23 also records the **flat-score disambiguation**
(peak vs lash vs stiction vs defocus floor). Previous: 2026-07-12 — **autofocus metric/landing fixes + bench-box BSOD analysis**
(§22): AF now uses a brightness-invariant, pre-blurred full-res metric (clean repeatable
focus peak, no more static-scene score swings) and a **direction-matched landing** that
approaches `best_pos` from the same side the search found it (closes the 13–35 %
final-vs-best gap from open-loop gear lash). SHM pool auto-scales to RAM (24 @ 64 GB
operational — unchanged; 8 @ 32 GB bench). **Three BSODs on the bench box (32 GB) are
hardware/driver, not the app** — three different stop codes in one afternoon plus a
year-long WDF_VIOLATION history predating all this work; run MemTest86. Operational 64 GB
machine unaffected. Previous: 2026-07-12 — **oblique-motor "bonkers" homing root-caused** (§21): the
capture-app-only homing failure was a **marginal 24 V supply** — under two-camera load
the oblique couldn't reach its hard-stop torque threshold so HLFB never asserted (8 s
timeout). Raising to nominal **28 V** fixed it (5/5 homing success, ~4.6 s). ClearCore
firmware reflashed with kept **safe-abort** (a motor that fails to home / a sweep that
keeps faulting now disables + drops to mode 0 instead of hunting forever) and the
`home wait N ms` diagnostic. Previous: 2026-07-09 — **OrthoSim ground-distance ruler +
fast-load** (§20):
new human-readable scale (toggle **G** / `--scale`) locked to the imagery for reading
distance/speed and effective GSD from the Iron661 image; new `--reduce {1,2,4,8}`
decodes the gigapixel ortho at 1/N res (GSD auto-scaled) so it loads in seconds; window
now fits the screen. Launch caveat: agent-spawned Qt apps inherit
`QT_QPA_PLATFORM=offscreen` (invisible) — override with `windows` when launching for
display. Previous: 2026-07-09 — first **autofocus ground-test session** (§18): ran the
hardware-free simulator path (§14.7 layer 1); new `tools/af_ground_test.py` metrics
sweep reports **54/54 runs converged** — the software baseline for the real rig.
ClearCore reflashed 2026-07-09 (unblocks on-hardware M5); exposure/gain left unlocked
in M5 by operator choice (§16.3 decision closed). Previous: 2026-06-30 — project is
now under Git (§13); Iron661Capture,
Parallax Export Studio, shared source, build specs, docs, ClearCore sketch, and the
KAYA runtime dependency copy are intended to be committed so the apps can be run
from the repository going forward. Previous: 2026-06-29 — GPS correlator
**latest-fix fallback** (§12), Parallax Export Studio GUI tweaks (§12), and both
apps rebuilt + redeployed to `C:\Parallax Apps`. Use this to pick up work on
another machine._

> **TL;DR of current state:** The GPS correlation pipeline is **fixed and validated
> end-to-end** (TOW-based sweep segmentation — see §9). A 1820-frame / 140-sweep /
> ~7-min live capture matched **100% on all 3 cameras with 0 stale / 0 misses** and
> passed every on-disk integrity check. GPS-parser log spam was reduced to
> errors-only (§10); GPS auto-detect now prefers "Septentrio Virtual USB COM Port 1"
> (§11). The app is **rebuilt (on local NTFS — see §4 PKG warning) and deployed** to
> the default folder `C:\Parallax Apps\Iron661 Capture`; the deployed EXE **launches
> cleanly** (smoke-tested, no PKG popup, window title shown).

This document summarizes the current state of the **Iron661Capture** app and the
work done in the most recent session, so you can continue seamlessly elsewhere.

---

## 1. What the app is

`Iron661Capture` is a PySide6 desktop app that runs a 3-camera Iron 661 rig:

- Live preview (one camera at a time), M0/M1 sweep capture to per-camera U.2
  drives, M3 manual line capture, and M4 tile-based focus mode.
- Talks to a **ClearCore PCB** over serial (mode/camera/focus/trigger control).
- Reads **GPS** (Septentrio SBF) off a COM port and correlates fixes to frames.
- Uses the **KAYA GenTL** SDK (`.cti`) for the frame grabbers.

### Entry point & layout

- Entry script: `host/main_capture.py` (keeps heavy imports inside
  `if __name__ == "__main__"` for the multiprocessing-spawn writer subprocesses).
- Main GUI window: `host/capture/app.py` → `class MainWindow(QMainWindow)`.
- Capture pipeline: `host/capture/pipeline.py`
  (`AcquisitionThread`, `CameraPipeline`).
- GPS: `host/capture/gps_receiver.py`, `gps_correlator.py`, `gps_fix.py`,
  `sbf_parser.py`.
- Session logging (added this session): `host/capture/session_log.py`.
- PyInstaller spec: `host/Iron661Capture.spec`.

> The project is now a **Git repository** on branch `main`. Use Git commits for
> restore points instead of manual `_backups/` snapshots where possible. The project
> still lives on Google Drive (`My Drive/...`), so avoid building PyInstaller output
> directly into the repo folder; see §4.

---

## 2. Environment / prerequisites

- **Windows**, Python **3.14** (`py -3` → 3.14.3 on the rig; the build interpreter
  is `C:\Users\USER\AppData\Local\Python\pythoncore-3.14-64`). Earlier sessions used
  3.12; all runtime + build deps now resolve cleanly under 3.14.
- Install deps from `host/`:
  ```
  py -3 -m pip install -r requirements.txt -r requirements-build.txt
  ```
- KAYA Instruments drivers + GenTL producer (`.cti`) for real hardware. The repo
  bundles a copy under `KAYA Instruments/Common/bin` and the app auto-detects via
  `GENICAM_GENTL64_PATH`. On a dev machine **without** a frame grabber you will
  see `KY_DeviceScan found no frame grabbers` and a "Camera open failed" dialog —
  this is expected; the GUI still launches.

---

## 3. How to run (from source)

From the repo root:

```
py -3 host/main_capture.py
py -3 host/main_capture.py --cti "C:\Program Files\KAYA Instruments\Common\bin\KYFGLibGenTL_vc141.cti"
py -3 host/main_capture.py --port COM7        # ClearCore PCB (else auto-detect)
py -3 host/main_capture.py --gps-port COM4    # Septentrio SBF (else auto-detect)
```

## 4. How to build & deploy the EXE

PyInstaller **onedir** build.

> ⛔ **CRITICAL — never build onto Google Drive.** The repo lives on `G:\My Drive\…`
> (a virtual/streamed filesystem). If PyInstaller's `COLLECT` writes the EXE there,
> the embedded PKG tail is corrupted and the EXE dies at launch with
> *"Could not load PyInstaller's embedded PKG archive"* (full write-up:
> `docs/pkg_issue.md`). The smoking gun is COLLECT taking ~160 s on `G:` vs <30 s on
> `C:`. **Always redirect `--distpath`/`--workpath` to real local NTFS.** Only the
> output bytes must be local; the `.spec` can stay on Drive.

Canonical flow, run from `host/`:

```
py -3 tools\make_exe_icon.py                                   # (re)build the .ico (data file; Drive OK)
$r = Join-Path $env:LOCALAPPDATA 'Iron661Build'
py -3 -m PyInstaller --noconfirm --clean --distpath "$r\dist" --workpath "$r\build" Iron661Capture.spec
.\install_parallax_desktop.ps1                                 # deploy + desktop shortcut
```

- The bundle lands at `%LOCALAPPDATA%\Iron661Build\dist\Iron661Capture\` (local NTFS).
- **Default deploy folder is `C:\Parallax Apps\Iron661 Capture`** (note the spaces;
  matches the sibling tools `C:\Parallax Apps\Bay14Reader` / `…\GPS Correlator`),
  hard-coded as `$dst` in `host/install_parallax_desktop.ps1`. The installer
  **prefers the local build output** above, mirrors it with `robocopy /MIR`, and
  refreshes the desktop shortcut `Parallax Iron661 Capture.lnk`. It only falls back
  to the on-Drive `host\dist\` (and warns) if no local build exists.
- PyInstaller: **6.20.0**; bundle ≈ 903 MB, launcher exe ≈ 11 MB.
- **Smoke-test the EXE for real** before declaring success: the PKG popup leaves the
  process alive, so check `(Get-Process -Id <pid>).MainWindowTitle` is non-empty (or
  eyeball the window) — a bare "process didn't exit" check passes even when broken.

> **Gotchas:**
> - `host/build_windows_release.ps1` (the older one-shot script) sets
>   `$ErrorActionPreference='Stop'`, so it **aborts on step 1** when `pip` prints its
>   upgrade notice to *stderr*. Until patched, run the steps above directly (deps are
>   already installed). Build log: `host/build_iron661.log`.
> - The 2026-06-04 ~15:18 deploy was built **onto Drive** and hit the PKG popup; it
>   was rebuilt onto local NTFS afterwards. Don't repeat that mistake.

---

## 5. Changes made in the most recent session

All changes are in `host/capture/` and compile cleanly (`py_compile`, no lint
errors). They were verified by launching from source (no hardware, so camera/
preview/GPS data paths were not exercised live).

### 5.1 Human-readable session log — `session_log.py` (new) + `app.py`
- New `SessionLogger` class writes a plain-text `.txt` log per session.
- **Location:** next to the app's EXE (frozen) / `host/` (dev). Falls back to
  `%LOCALAPPDATA%\Iron661Capture\logs` → home → temp if not writable. Override
  with env var `IRON661_LOG_DIR`.
- **Filename:** `Iron661_session_YYYYMMDD_HHMMSS.txt`.
- **Header** includes **Build date** (EXE mtime, or `main_capture.py` mtime in
  dev) and **Session start** time, then the log file path.
- **Captures:** everything in the on-screen log window (via `_log`), every
  button press (`[BUTTON]`), error/confirmation dialogs (`[ERROR]`/`[DIALOG]`),
  a periodic GPS position line every 10 s (`[GPS]`, `GPS_LOG_INTERVAL_S`), and a
  per-line summary on line close (`[LINE]` acquired/written/dropped). Footer has
  session end + duration.
- Integration points in `app.py`: `MainWindow.__init__` (creates logger +
  `_line_count_baseline`), `_log`, `_install_button_press_logging` /
  `_log_button_press`, `_on_bringup_failed`, the reset-cameras dialog,
  `_log_gps_position`, `_capture_line_baseline` / `_log_ended_line_summary`
  (hooked into `_open_lines_for_active_pipelines`, `_close_all_lines`, and the
  drive-change line roll), and `closeEvent` (calls `_session_log.close()`).

### 5.2 Build date shown in GUI — `app.py` (matches Bay14Reader)
- Window title: `Iron 661 Capture + Focus  (build YYYY-MM-DD)`.
- Permanent gray `Build: YYYY-MM-DD` widget in the status bar.
- Source: `session_log.build_date("%Y-%m-%d")`.

### 5.3 "Reset counters" button — `app.py`
- New button in the Project header row (next to "Test trigger ($T)").
- `_on_reset_counters_clicked`: calls `CameraPipeline.reset_counters()` on every
  camera (zeroes acquired/written/dropped, labels flip to 0). Does **not** touch
  cameras/serial/open line files. Re-pins any open line's summary baseline to 0.
- `CameraRow` now tracks numeric counts (`frame_counts()` →
  `(acq, written, dropped)`), used by the per-line summary too.

### 5.4 Exposure/Gain now affect only the **previewed** camera — `app.py`
- `_on_preview_sensor_apply`: writes to `_active_pipeline()` (the camera whose
  preview radio is on) instead of all cameras. Warns if none is previewed.
- `_sync_preview_sensor_fields`: reads displayed values from the previewed camera
  (falls back to lowest cam number if none selected); auto-refreshes when the
  preview camera changes. Tooltips updated.

### 5.5 100% full-resolution preview zoom toggle — `app.py` + `pipeline.py`
- **Toggle button at the top-left corner of the preview window**
  (`_PreviewDisplayLabel.set_corner_button`). Checkable: "Zoom: Fit" ↔
  "Zoom: 100%". Disabled in M4.
- `app.py`: `_on_preview_zoom_toggled`, `_push_preview_zoom_size`,
  `_preview_zoom_on` state; `_scale_preview_to_label` shows the crop 1:1 in zoom
  mode; crop size re-pushed on resize / splitter move / preview switch.
- `pipeline.py`: `AcquisitionThread.set_preview_zoom` /
  `set_preview_zoom_size` (+ `CameraPipeline` pass-throughs); new
  `_full_res_center_crop_gray8()` builds a native-resolution center crop from the
  **full-res shared-memory slot** (read *before* the slot is submitted to the
  writer — important to avoid racing the writer subprocess). Falls back to the
  decimated whole-frame preview when no full-res payload is available for a frame.
  Full-res emission is only ON in M0/M1, so zoom is meaningful there.

---

## 6. Known state / testing notes

- All edits compile (`py -3 -m py_compile ...`) and pass `ReadLints`.
- Verified the app **launches** from source repeatedly; session log is written
  and contains the header, log lines, button presses, error dialog, periodic GPS
  status, and footer.
- **Hardware-tested on the rig (2026-06-04, 3 cameras + ClearCore + GPS,
  serials 2439006/7/8, drives D:/E:/F:):**
  - **Preview zoom toggle (§5.5) — PASS.** Toggled "Fit" ↔ "100% full-res crop"
    multiple times in M1 with live frames. The crop tracked the window on
    resize, and crucially the writer pipeline was unaffected during zoom-on
    (`dropped=0 failed=0 packed_drops=0`), confirming the full-res crop is read
    from the SHM slot *before* it is submitted to the writer.
  - **Reset counters button (§5.3) — PASS.** `Reset counters: zeroed
    acq/written/dropped on 3 camera(s).` with no per-camera failures. Cameras
    stayed armed, writer packed-totals kept advancing, open lines untouched.
  - **Per-camera exposure/gain (§5.4) — PASS.** Editing the previewed camera
    (e.g. `cam3 ExposureTime=1000 → OK (verified=1000.0)`, `Gain=3 → OK`) wrote
    only to that camera and verified back from the device.
  - **Throughput sanity:** sustained full M1 continuous at ~4.3 Hz/camera × 3
    (~2.7 GB/s aggregate, ~0.9 GB/s per U.2) with **zero backpressure drops**.
    The only drop observed was a single ≤1-frame/camera blip at an M0→M1 line
    roll (`no .bay14line container is open`) — the io_writer container-open
    handshake briefly lagging the host during the transition, which recovers
    on the next frame and does not accumulate in steady state.
  - On first launch the grabber enumerated only 2 of 3 cameras (transient
    CXP/enumeration hiccup); a relaunch cleanly saw all 3.
- **GPS correlation (TOW fix, §9) — PASS (2026-06-04 PM).** A long live capture
  (1820 frames, 140 sweeps, ~7 min, multiple container rolls) reported
  `matched=1820 (100%) stale=0 out_of_future=0 no_event=0` on all 3 cameras.
  On-disk `tools/bay14_gps_check.py` over the 162 GB result: 100% GPS coverage on
  every (camera, line, sweep), 0 cross-camera TOW mismatches, 0 monotonicity
  violations, and visible sweep structure (intra ~225 ms vs inter ~302 ms).
- The deployed EXE at `C:\Parallax Apps\Iron661 Capture` is **current and launches
  cleanly** — built on local NTFS (`%LOCALAPPDATA%\Iron661Build`) and smoke-tested
  2026-06-04 ~16:00 (window title `Iron 661 Capture + Focus (build 2026-06-04)`, no
  PKG popup). An interim build written onto Drive earlier that afternoon hit the PKG
  popup; see §4 for why and the mandatory local-NTFS build.

## 7. Suggested next steps

1. ~~On the rig: verify the zoom toggle in M0/M1, per-camera exposure/gain, and
   the `[LINE]` summary deltas.~~ **Done 2026-06-04 — all passed (see §6).**
2. ~~Rebuild the EXE and confirm the session log lands next to the EXE with the
   correct build date.~~ **Done 2026-06-04 PM — rebuilt & deployed to
   `C:\Parallax Apps\Iron661 Capture` (see §4).**
3. ~~Fix GPS correlation (low fix rate / monotonicity violations).~~ **Done &
   validated 2026-06-04 PM — TOW-based sweep segmentation, 100% on a 1820-frame
   live capture (see §9).**
4. **Watch for a recurrence of the "stale storm."** One earlier run hit 0% matches
   (all frames `stale`); it did **not** reproduce on the longer capture, so it is
   intermittent and currently un-reproduced. The `stale-age` diagnostic (§9) is in
   place to measure the exact frame-vs-event offset if it returns — if you see a
   `stale-age` line in a correlator summary, send it over to pick the targeted fix
   (widen `STALE_AHEAD_MS`, timestamp at true acquisition, or sequence alignment).
5. Patch `host/build_windows_release.ps1` so it survives `pip`'s stderr notice
   (e.g. relax `$ErrorActionPreference` around the pip call or `2>&1` it), so the
   one-shot build script works end-to-end again (see §4 gotcha).
6. Optional polish: surface the session-log path in the on-screen log at startup;
   add a `--log-dir` CLI flag; make the zoom crop demosaiced (currently raw Bayer
   shown as grayscale, so a faint checkerboard is expected at 100%).
7. Optional: suppress/relabel the single `no .bay14line container is open` drop
   at mode-transition line rolls so it isn't conflated with genuine backpressure
   drops (see §6 throughput note).

## 8. Quick file reference

| Area | File |
|------|------|
| Entry point | `host/main_capture.py` |
| Main window / all UI + handlers | `host/capture/app.py` |
| Acquisition + writer pipeline | `host/capture/pipeline.py` |
| Session log file | `host/capture/session_log.py` |
| GPS receive/correlate | `host/capture/gps_receiver.py`, `gps_correlator.py`, `gps_fix.py`, `sbf_parser.py` |
| PyInstaller spec / installer | `host/Iron661Capture.spec`, `host/install_parallax_desktop.ps1` |
| GPS diagnostics (offline) | `tools/bay14_gps_check.py`, `tools/sbf_gap_analysis.py`, `tools/validate_tow_fix.py`, `tools/frame_event_lag.py`, `tools/test_gps_correlator.py` |
| Raw SBF taps (when `GPS_SBF_TAP_DIR` set) | `tools/taps/*.sbf`, `tools/taps/*.timing.jsonl` |
| Manual backups | `_backups/` |

---

## 9. GPS correlation — TOW sweep-segmentation fix (2026-06-04 PM)

**Problem.** The correlator originally split the GPS event stream into "sweeps"
(one container roll = 13 trigger events) using **host-arrival time gaps**. USB
batching of SBF blocks makes those host gaps jittery, so sweeps were mis-segmented
("gen-counter drift"), producing a low GPS fix rate and TOW-monotonicity violations.

**Fix.** Segment sweeps by **SBF Time-of-Week (TOW) gaps** instead — TOW is the
GPS-clock timestamp on each `ExtEventPVTGeodetic` event and is jitter-free.
Implemented in `host/capture/gps_correlator.py`:
- New threshold `SWEEP_TOW_GAP_MS = 280.0` (intra-sweep ~225 ms, inter-sweep
  ~300 ms, so 280 ms cleanly separates them); `__init__` takes `sweep_tow_gap_ms`.
- `add_event` starts a new sweep when `tow_gap_ms > SWEEP_TOW_GAP_MS` or
  `tow_gap_ms <= 0` (week rollover / reset). Falls back to host-arrival gaps only
  when `event.tow_ms` is `DNU_U4` (Do-Not-Use).

**Diagnostics added.**
- `stale-age` instrumentation in `GpsCorrelator` (+ printed by `app.py`'s
  `_log_gps_correlator_stats`): on a `stale` miss it records the signed age
  (min/avg/max ms) of the closest candidate, i.e. the exact `frame_perf_ns` vs
  `event_host_perf_ns` offset. **Only prints when stale misses occur**, so a clean
  run shows no `stale-age` line.
- `tools/bay14_gps_check.py` — line/run-aware on-disk validator (per-camera/line/
  sweep coverage, cross-camera identity, TOW monotonicity, intra/inter-sweep
  deltas). Use `--root` (accepts multiple) and `meta_only` fast path.
- `tools/sbf_gap_analysis.py` — host-gap vs TOW-gap comparison + cumulative drift.
- `tools/validate_tow_fix.py` — time-ordered offline correlator sim against a raw
  SBF tap (de-batched host times); reports matched/wrong/missed.
- `tools/frame_event_lag.py` — wall-clock lag between frame acquire and event
  arrival across a capture.
- Regression unit tests in `tools/test_gps_correlator.py`
  (`TowSegmentationTests`): batched host timestamps must not split a sweep; a TOW
  gap must start a new sweep even at zero host gap.

**Result.** See §6 — 100% match on a 1820-frame live capture, all on-disk checks
pass.

**Optional raw tap.** Set env `GPS_SBF_TAP_DIR` before launch to record the raw SBF
byte stream + per-block arrival timings to `tools/taps/` for offline replay/debug.

---

## 10. GPS-parser log-noise reduction (2026-06-04 PM)

The receiver emits cumulative parser stats every few seconds; previously `app.py`
logged a `GPS parser: good=… events=… bad_crc=…` line **every interval**, flooding
the GUI/session log. `_on_gps_parser_stats` now:
- Stays **silent while healthy**.
- Logs `GPS parser: PROBLEM -- bad_crc +N bad_len +N skipped +N B (cumulative: …)`
  **only when an error counter actually increases** since the last sample (tracks
  deltas; treats a negative delta as a reconnect/reset and counts from 0).
- Emits a **single** `GPS parser: receiving events (good=N)` the first time events
  flow, as a one-time "link alive" confirmation.
- `events_skipped_unknown` is intentionally **not** flagged (it just means other
  SBF block types are on the wire — benign).
- `_on_gps_connection_changed` resets the watcher state on each (dis)connect.

Full cumulative parser/correlator totals are still dumped on disconnect, so nothing
is lost for post-run diagnostics.

---

## 11. GPS auto-detect — preferred SBF port (2026-06-04 PM)

A Septentrio receiver enumerates **several** virtual USB COM ports; only one
carries the SBF stream. `GpsReceiver.auto_detect()` (`host/capture/gps_receiver.py`)
used to return the *first* port matching any Septentrio hint, which could pick the
wrong one. It now:
1. **Prefers** a Septentrio port whose driver label contains
   `SEPTENTRIO_PREFERRED_LABEL = "virtual usb com port 1"` (i.e. "Septentrio
   Virtual USB COM Port 1").
2. Falls back to the previous first-hint match if that exact label isn't present.

The chosen path is logged (`GPS auto-detect (preferred SBF port): …` /
`… (fallback hint …)`). A CLI `--gps-port COMx` still overrides auto-detect.

---

## 12. GPS latest-fix fallback + Parallax Export Studio GUI (2026-06-29)

### 12.1 Why — a whole line lost embedded GPS (L14)

On the 2026-06-23 flight, line 14 had **0% embedded GPS** on all 3 cameras even
though the receiver *was* logging that line's events (they show up in the external
SBF export and were ~0.1 s fresh in the live preview). Root cause: the live
correlator's per-slot lookup (`pop_fix_for`) returned `None` for every L14 frame —
the camera's `frame_in_sweep` had drifted out of phase with the GPS sweep
segmentation (triggered by L14's turbulent start: abnormal 83 s / 16 s sweep pauses),
so no slot entry matched even though a correct, temporally-adjacent fix was sitting
in the table under a different slot key.

### 12.2 Fix — latest-fix fallback in `GpsCorrelator` (`host/capture/gps_correlator.py`)

When the per-slot lookup finds nothing for a frame, `pop_fix_for` now falls back to
the **most recent fix of any slot** if it's still fresh, instead of returning `None`:

- New tunable `LATEST_FALLBACK_AHEAD_MS` (default = `STALE_AHEAD_MS` = 1500 ms) and
  `__init__` arg `latest_fallback_ms`. The fallback accepts `self._latest` when
  `-FUTURE_MS <= (frame_perf_ns - latest_host_perf_ns) <= LATEST_FALLBACK_AHEAD_MS`.
- **A genuine GPS outage leaves `_latest` older than the bound**, so the fallback
  declines and the frame still goes out with no fix — it only rescues the out-of-phase
  case, never fabricates a position from minutes-old data.
- Set `latest_fallback_ms=0` to disable and restore the strict slot-only behaviour.
- `GpsFix` / `to_dict()` are **unchanged** (the schema flows into chunk headers +
  downstream parsers), so a fallback fix looks like any other embedded fix on disk.

**Traceability.** New per-camera `fallback_latest` counter in `_CameraState`,
surfaced in `stats()` and cleared by `reset_stats()`. `app.py`'s
`_log_gps_correlator_stats` now reports `with_fix=N (X%) [matched=… fallback_latest=…]`
so you can see how many frames were rescued vs. matched directly per line.

**Tests.** `tools/test_gps_correlator.py` `MissedEventTests` updated/extended:
missing-slot-with-fresh-latest now returns the fallback fix; missing-slot with a
stale latest still returns `None`; and `latest_fallback_ms=0` restores strict mode.
All 16 tests pass.

> **Related offline finding (Parallax Export Studio).** Lines 9/10 failed to
> correlate against the external SBF file because they contained long *intra-line*
> inter-frame pauses (63.8 s / 74.2 s) that, at the old `cluster_gap_s=60 s`, split a
> single line across multiple SBF clusters (the matcher needs one contiguous
> cluster). Raising the cluster gap to ~80–120 s merges them and they match cleanly —
> hence the new GUI default below.

### 12.3 Parallax Export Studio GUI (`tools/gps_export_studio_gui.py`)

- TIFF-mode radio labels now show the on-disk frame size (uncompressed,
  `13400×9528`, `compression=None`, binary MB as Windows Explorer reports):
  - "Raw Bayer uint16 (14→16 shift)" → **"Grayscale (~243 MB/frame)"** (1×u16)
  - "Demosaiced RGB uint16" → **"RGB (~731 MB/frame)"** (3×u16)
- **RGB is now the default** TIFF mode (was raw/grayscale).
- **Correlation parameters → Cluster gap** default changed `60 s` → **`80 s`**.

### 12.4 Build & deploy (both apps → `C:\Parallax Apps`)

Both apps were rebuilt from source on **Python 3.12.10 / PyInstaller 6.20.0** onto
local NTFS (`%LOCALAPPDATA%`) — never directly onto Google Drive (see §4 PKG
warning) — then mirrored with `robocopy /MIR`:

- `C:\Parallax Apps\Parallax Export Studio\Parallax Export Studio.exe`
  (built via `tools/ParallaxExportStudio.spec`).
- `C:\Parallax Apps\Iron661 Capture\Iron661Capture.exe`
  (built via `host/Iron661Capture.spec`).

> **Gotcha:** the `WScript.Shell` COM call that creates the desktop shortcut hangs
> under headless automation, which stalls `host/install_parallax_desktop.ps1`
> mid-run. The app bundle still copies fine — only the optional
> `Parallax Iron661 Capture.lnk` desktop shortcut may need to be (re)created by hand.
> Neither deployed EXE was smoke-launched this session (Iron661 needs the camera
> hardware; the dev machine self-exits without it).

---

## 13. Git workflow (2026-06-30)

The repository was initialized on branch `main` with a first source-only commit:

```
16c57b7 Initial project setup
```

The current Git baseline should include the runnable/rebuildable app source:

- `host/` — Iron661Capture app, including `host/Iron661Capture.spec`, build/deploy
  scripts, assets, requirements, and `host/capture/`.
- `tools/` — Parallax Export Studio (`tools/gps_export_studio_gui.py`,
  `tools/gps_export_studio_core.py`, `tools/ParallaxExportStudio.spec`) plus shared
  Bay14/GPS/export utilities used by the studio.
- `Parallax_CLRCR_V2_4/` — current ClearCore sketch used by the capture rig.
- `KAYA Instruments/` — local copy of the KAYA runtime/GenTL dependency used for
  hardware bring-up when the system install is unavailable.
- `docs/` and `HANDOFF.md` — operational notes and build/run documentation.

Ignored on purpose: generated `build/` and `dist/` folders, logs, session logs,
Python caches, raw captures, TIFF exports, archives, temporary folders, and manual
backup snapshots. Those can be regenerated or are too large/noisy for normal Git
history.

Useful commands from the repo root:

```
git status
git add <files-or-folders>
git commit -m "Describe the checkpoint"
git log --oneline --max-count=5
```

---

## 14. Autofocus — M4 (2026-06-30)

Contrast-detection autofocus (CDAF) for the M4 focus mode. It copes with the
in-flight scene (which changes rapidly but overlaps frame-to-frame) by tracking a
high-texture patch and hill-climbing the open-loop focus motor. Implemented in
`host/capture/autofocus.py` (`AutoFocusEngine` = hardware-free state machine;
`AutoFocusController(QObject)` = Qt wrapper + multi-camera sequencing), wired into
`host/capture/focus_panel.py` and `host/capture/app.py`, reusing the winning-tile
scorer (`sharpness.find_best_tile` / `score_tile_subsampled`) and `tile_geometry.TileGrid`.

### 14.1 Requirements (as specified)

- The aircraft is **in flight**: the scene changes rapidly, but consecutive frames
  overlap heavily, so it is **not** an independently pollable variable — a focus
  metric measured across focus positions is confounded by content motion unless we
  track the same physical patch.
- **Hardware limits:** brushed DC focus motors, **no encoder**, all **3 motors driven
  by one H-bridge, relay-selectable** (`$Cn*`). Therefore autofocus runs on **one
  camera at a time**. (Per-motor encoders are planned later — see 14.8.)
- Use the **winning-tile** strategy to pick an object to focus on and **keep it in
  the FOV as long as possible** — but pick it from the **leading edge of the frame**
  (the side facing flight direction: top/bottom/left/right) so the object traverses
  the whole frame before exiting (maximum dwell time), rather than from the central
  circle.
- Support an **operator-assisted** workflow: the operator does a coarse focus to get
  close, and AF fine-tunes from there (default). A **full-auto** bracket search is
  also available.
- The focus loop need **not be human-usable**, so the M4 cadence is pushed toward the
  camera's **~209 ms (~4.8 FPS)** trigger limit (faster convergence + smaller per-step
  scene shift). The old **250 ms** figure was **not** a hardware floor — it was an
  arbitrary `if (picsInPlaceDelay<250)` clamp in `Parallax_CLRCR_V2_4`. The firmware now
  (a) treats `picsInPlaceDelay` as the **trigger-to-trigger period**, gated on
  `lastFrameTime` exactly like the sweep path (so the trigger pulse `picDelayCurr` is
  absorbed *within* the period instead of added on top of a trailing delay), and (b)
  floors the `<` command at **`minTimeBetweenFrames` (209 ms)** — the camera's real
  ceiling — so sending `<` drives M4 straight to max FPS. Host `FOCUS_TAP_MIN_INTERVAL_S`
  was lowered 0.25→0.21 (~4.76 Hz) to match, so the focus tap isn't the bottleneck.

### 14.2 Approach

1. **Leading-edge winning tile.** `tile_geometry.TileGrid` gained a selection region
   (`region="circle"` default, or `region="edge"` with `lead_edge` ∈
   {top,bottom,left,right} and `edge_band` band-depth fraction). AF lays candidate
   tiles along the leading-edge band and scores them with the existing sharpness
   metric to pick a high-texture target. The M4 fine preview still uses
   `region="circle"`.
2. **Automatic leading-edge detection.** The leading edge = the edge **opposite the
   image-motion vector**, which the patch tracker measures via phase correlation
   (`cv2.phaseCorrelate`). So the band is derived automatically; an explicit
   `lead_edge` hint (e.g. from GPS track) can seed it. If motion is indeterminate
   (hover / no parallax), AF falls back to the central circle.
3. **Patch tracking.** Each frame the tracker estimates the global shift and moves the
   ROI to follow the same physical content, so the sharpness metric is comparable
   across focus positions. Re-acquire when tracking is lost or the patch reaches the
   trailing edge.
4. **Open-loop hill climb.** No encoder, so "position" = cumulative signed pulse count
   (1 pulse = 10 ms drive). Climb to the sharpness peak, remember the best, and return
   to it (approximate; backlash-compensated).

### 14.3 State machine

`IDLE → ACQUIRE (pick leading-edge target) → MEASURE (median of N frames) → MOVE
(pulse + settle) → … → RETURN_TO_BEST → DONE`, with `FAILED` on persistent low
texture / lost tracking / safety cap. Each focus frame advances the machine; pulses
are clocked by frame arrivals plus a settle count.

### 14.4 Config (`AutoFocusConfig`)

`assisted` (default True), `step_pulses` / `coarse_step_pulses`,
`assisted_bracket_pulses` (max excursion per side in assisted mode),
`max_total_pulses` (hard safety cap), `settle_frames`, `measure_frames`,
`patience` (flat/declining steps before reversing/stopping; `best` advances on any real gain),
`backlash_pulses` (extra pulses sent on every direction reversal — calibratable),
`strength` (`$Sn` level used during a run), `min_texture_score` /
`max_low_texture_frames`, `edge_band`, `drive_cadence_to_min`.

### 14.5 UI (`FocusPanel`)

Adds **Auto-focus** (current camera), **Auto-focus all** (cycles cam1→2→3 via the
`$Cn*` relay), **Stop**, an **assisted / full-auto** selector, and a status/progress
readout. Strength + camera radios are reused. Camera/strength/mode controls are
locked while a run is active.

### 14.6 Failure-mode & non-flight-motion handling

- **Intermittent** low texture (e.g. oblique sky phase) → **pause and resume**: those
  frames are not counted, focus is held, and the climb resumes when texture returns.
  **Persistent** low texture (> `max_low_texture_frames`) → abort.
- **Oscillating / reversing motion** → hysteresis: the same patch is tracked through
  reversals (long dwell); the leading edge is locked at acquisition, not re-picked per
  reversal.
- **Keep exposure/gain fixed during a run** (auto-exposure off) so the metric isn't
  confounded; the M4 sensor controls are already disabled in M4.
- A genuine **GPS/motion outage** never fabricates motion — AF just falls back to the
  central circle.

### 14.7 Ground testing (cameras stationary, mirrors move)

The mirror rig is not aircraft-like: the **vertical (nadir)** camera sees **left↔right**
travel; the **oblique** cameras trace a **circle whose top is sky-only**. The AF is
motion-agnostic (leading edge derived from measured motion; cyclic/oscillating cases
handled above), so the rig is a valid and *repeatable* exerciser. Layered tests:

1. **Hardware-free closed loop (CI):** `tools/test_autofocus.py` runs the full engine
   against a software **focus simulator** in `frame_grabber_sim.py`
   (`FocusSceneSimulator`) — a textured scene whose **blur is a function of a simulated
   focus position**, with selectable motion profiles: flight translation, nadir L-R
   oscillation, and oblique circle-with-sky. AF pulses drive the simulated motor →
   blur changes → loop closes entirely in software. Asserts convergence near the
   optimum, sky-phase pause/resume, and reversal/backlash handling.
2. **Optical bench (rig validation):** fixed textured **resolution target**
   (slanted-edge / Siemens star) at constant distance + moving mirror. Manually find
   true best focus, defocus by a known amount, run AF, and measure **accuracy**
   (vs manual best), **repeatability** (spread over N runs), **convergence time**
   (frames/pulses), and **sky-sector behavior**. Mirror motion is repeatable, so the
   numbers are comparable run-to-run. Keep the target far enough that mirror angle
   doesn't materially change subject distance.
3. **Offline replay (partial):** push recorded frames (captures, `Training pictures/`,
   `exp_123.tif`) through the tracker + leading-edge + sharpness metric to validate
   selection/tracking/metric on real imagery. Focus *actuation* can't be replayed.

### 14.8 Future: per-motor encoders

Encoders (planned) will allow closed-loop positioning, exact and repeatable
return-to-best, and faster convergence. The engine is structured so a future
`FocusAxis` abstraction (reporting real position instead of cumulative pulse counts)
can replace the open-loop pulse bookkeeping without touching the search logic.

## 15. Ground-test mode — M5 (2026-07-02)

`$M5*` is an **on-hardware** ground-test mode: it moves the mirrors like the M0/M1
sweep but drives autofocus **in the background** so the real AF loop can be exercised
against a *physically* moving scene on the bench (stationary cameras, moving mirrors —
see §14.7 for why the rig is a valid exerciser). It complements the hardware-free
simulator loop in `tools/test_autofocus.py`; here the actual focus motor, relay mux,
camera, and host AF engine all run together. **Preview-only — M5 writes no frames to
disk** (a "save during M5" toggle can be added later).

### 15.1 Why a new mode (non-blocking stepped sweep)

The normal `Sweep()` is a single blocking `for` loop over all `Positions`, so serial
(the AF `+`/`-` pulses) is only serviced *between whole sweeps* — useless for a
background focus loop. M5 instead advances **one mirror position + one frame per
`loop()` iteration** (`SweepStepM5()`), then returns so `CheckMessages()` can apply any
pending AF pulses **before the next move**. The mirror sweep (servos) and the focus
motor (H-bridge) are independent actuators, so there is no contention.

### 15.2 Firmware (`Parallax_CLRCR_V2_4.ino`)

- Globals: `basePositions = 13` (normal sweeps), `m5Positions = 40` (ground-test,
  user-adjustable), `m5Index` (current step). `verticalAngles[]` enlarged `20 → 64`.
- `SweepStepM5()`: computes `position0/position1` for `m5Index` exactly like `Sweep()`,
  `MoveAbsolutePosition(...)` (blocks on `StepsComplete()` + `HLFB_ASSERTED` so the
  mirror is *confirmed in place*), `delay(mechDelayCurr)` to settle, then the same
  `minTimeBetweenFrames` cadence gate + trigger block. `m5Index++` and wraps to 0
  (continuous), recomputing `calculateSweepTime()` each wrap.
- `loop()` `case 5: SweepStepM5();`.
- `setMode(5)`: `Positions = m5Positions; calculateMotorAngles(); setupMotorsFast(); m5Index = 0;`.
  The sweep cases 0/1/2/3 now set `Positions = basePositions; calculateMotorAngles();`
  so **leaving M5 restores the 13-position sweep**.
- `+`/`-`/`>`/`<` are gated on `mode==4 || mode==5`. New `$P<n>*` sets `m5Positions`
  (clamped `[2,64]`); if already in M5 it recomputes angles and restarts the step index.
  `>`/`<` in M5 also tune `mechDelayCurr`/cadence for per-frame settle.

### 15.3 Host

- `mode_controller.Mode.M5` ("Ground Test" button); `is_af_mode()` returns
  `mode in (M4, M5)` and now gates AF start, the raw-focus frame feed, focus-tap
  enable, the focus `_view_stack` page, coarse-forcing, and camera-select. M5 is **not**
  in `_RECORDING_MODES`. The AF engine is unchanged — the M5 sweep is just the
  moving-scene case its tracker/leading-edge logic already handles.
- `serial_client.send_mode`/`app._on_mode_clicked` map 5↔`Mode.M5`; a `Mode 5:` USB
  banner also syncs the host UI. `SerialClient.set_m5_positions(n)` →
  `serial_comm` → `$P<n>*`.
- `FocusPanel` adds an **M5 positions** spinbox (default 40, range 2–64) that sends
  `$P<n>*` on edit; the existing Auto-focus buttons work in M5.

### 15.4 Bench validation checklist

1. `$M5*` steps through ~40 positions continuously (watch mirror motion + `Swp`/frame logs).
2. AF `+`/`-` land **between** frames (watch `PicDelay`/focus logs while a run is active).
3. `$P<n>*` changes the number of positions live; leaving M5 restores 13-position sweeps.
4. Host offscreen import-check of `capture.app`; `tools/test_autofocus.py` still passes
   (engine unchanged).

## 16. Session log — M5 ground-test mode (2026-07-02)

This session implemented the **M5 ground-test mode** end-to-end (firmware + host +
docs). Full technical detail is in §15; this is the chronological summary and status.

### 16.1 What was done

- **Firmware (`Parallax_CLRCR_V2_4.ino`)** — added the non-blocking stepped sweep
  `SweepStepM5()` (one mirror position + one frame per `loop()` iteration, continuous
  wrap), enlarged `verticalAngles[] 20→64`, added `basePositions`/`m5Positions`/
  `m5Index`, `loop()` `case 5`, `setMode(5)` (and made sweep modes 0/1/2/3 restore
  `basePositions`), the `$M5*` handler, `$P<n>*` positions parser (clamp `[2,64]`), and
  gated `+`/`-`/`>`/`<` on `mode==4 || mode==5`.
- **Host** — `Mode.M5` ("Ground Test") in `mode_controller` with `is_af_mode()`
  (= M4|M5) driving every AF gate; `serial_comm`/`serial_client.set_m5_positions()` →
  `$P<n>*`; `app` mode button + mapping + `Mode 5:` USB-banner sync + AF gating via
  `is_af_mode()`; `FocusPanel` **M5 positions** spinbox (default 40, range 2–64);
  `autofocus._guard()` allows M4 or M5 (engine unchanged). M5 is **preview-only**
  (not in `_RECORDING_MODES`).
- **Docs** — `docs/clearcore_serial_protocol.md` (`$M5*`, `$P<n>*`, `+`/`-`/`>`/`<`
  now also mode 5) and this HANDOFF §15/§16.

### 16.2 Validation performed (software only)

- All edited host modules byte-compile; `capture.app` imports cleanly (offscreen Qt).
- `tools/test_autofocus.py` — **9/9 pass** (AF engine untouched).
- Firmware was **reviewed by eye only** — no Arduino toolchain on this machine, so it
  has **not been compiled**.

### 16.3 Current status / next actions

- Code is **complete and committed** (first M5 commit `0a56497`; see git log for the
  session-summary commit that also lands this §16).
- **Blocking before any window/bench test:** open the sketch in the Arduino IDE /
  arduino-cli, **Verify** (to catch anything the eyeball review missed) and **Upload**
  to the ClearCore.
- Then run the §15.4 bench checklist.
- **Open decision:** exposure/gain are auto-locked in M4 but currently left *enabled*
  in M5. AF assumes fixed exposure, so either avoid touching them during a run or ask
  to have them locked in M5 too (small change).

### 16.4 Git note (unchanged from §13)

Commit via `git.exe commit` (not `git commit`) from `cmd`: the harness appends a
`Co-authored-by: … <…>` trailer whose `<` breaks PowerShell parsing. `git.exe` avoids
the injected trailer. Stage only intended files — the working tree has pre-existing
untracked junk (`Training pictures/`, `RefDesign/`, etc.) that must not be committed.

## 17. Orthophoto screen bench rig — OrthoSim (2026-07-05)

A second, higher-flexibility ground-test rig: a large screen (driven by a second
PC running the new **OrthoSim** app) shows a scrolling high-resolution orthophoto
in front of the stationary Iron661 camera to simulate flight. It complements the
M5 mirror sweep (§15) and the hardware-free simulator (§14.7) — here the real
camera, focus motor, relay mux and host AF loop all run against realistic,
*calibrated* moving imagery.

### 17.1 Components

- **Display app** (`tools/orthosim_gui.py` + `tools/orthosim_core.py`): full-screen
  PySide6 app (GPU-accelerated `QOpenGLWidget` viewport) that tiles and scrolls the
  ortho, renders four ArUco corner fiducials, and renders a high-contrast optical
  ground-truth **bar**. Writes a `*.groundtruth.csv` cross-check log.
- **Shared codec** (`orthosim_codec.py`, repo root): the optical bar wire format —
  112 cells = 16 sync + 88 payload + 8 CRC, encoding absolute `distance_mm`,
  `tick`, `velocity_mmps`, and flags (seam/paused/calibration). CRC-8 rejects
  smeared reads. Imported by both the display app and the capture decoder so they
  cannot drift.
- **Capture decoder** (`host/capture/orthosim_decode.py`): detects the fiducials,
  homography-rectifies the imaged screen to display coordinates, samples the bar,
  decodes it, and (via `SpeedDistanceTracker`) derives **measured** speed/distance
  to compare against the simulated (CSV) values. `rectify_screen()` also yields a
  bezel-free screen crop. `cv2.aruco` is imported guarded (headless-safe).
- **AF masking** (`tile_geometry.py` + `host/capture/autofocus.py`): new
  `TileGrid(region="interior", interior_inset=…, exclude_edge=…, edge_band=…)`
  keeps focus tiles off the screen bezel/office (challenge 1) and off the optical
  strip band (challenge 2). Enabled per-run via `AutoFocusConfig.restrict_interior`
  (default **off**, so normal M4/M5 flight behaviour is unchanged).

### 17.2 Calibration model (cm/px)

With `ortho_gsd_cm` (cm per ortho pixel, KNOWN), `zoom` Z (screen-px per ortho-px)
and `M` (camera-px per screen-px, from the fiducials):

- screen GSD = `ortho_gsd_cm / Z` (always displayable),
- simulated camera GSD = `ortho_gsd_cm / (Z · M)` (= "flight height"),
- scroll speed (screen-px/s) = `ground_speed_mps · 100 / ortho_gsd_cm · Z`,
- distance integrates `ground_speed_mps` over wall-clock time.

`zoom` maps to simulated height, the Up/Down keys map to simulated ground speed.
The **C** calibration overlay draws a checkerboard + scale bar (labelled in mm if
`--panel-pitch` is given) to measure/verify M.

### 17.3 Running it

Display PC (from source):

```
py -3 tools/orthosim_gui.py PATH\ortho.tif --gsd 5.0
py -3 tools/orthosim_gui.py ortho.png --gsd 4 --panel-pitch 0.181 --M 1.0 --dir up
py -3 tools/orthosim_gui.py ortho.png --gsd 5 --windowed     # bring-up
```

Controls: Up/Down speed, +/- (or PageUp/Down) zoom, Space pause, H HUD, C
calibration, R reset distance, S save config, Esc/Q quit.

Package (never build onto Google Drive — see §4):

```
$r = Join-Path $env:LOCALAPPDATA 'OrthoSimBuild'
py -3 -m PyInstaller --noconfirm --clean --distpath "$r\dist" --workpath "$r\build" tools\OrthoSim.spec
robocopy "$r\dist\OrthoSim" "C:\Parallax Apps\OrthoSim" /MIR
```

Decode a saved camera image offline:

```
py -3 -m capture.orthosim_decode SHOT.png --screen-w 3840 --screen-h 2160
```

### 17.4 The three challenges (how they're handled)

1. **Static bezel/office** → ArUco homography crop (`rectify_screen`) + AF
   `region="interior"` inset. Physically: fill the FOV with the screen, matte
   surround, dark room.
2. **Moving optical strip** → it lives in a known reserved band; AF excludes that
   band via `exclude_edge`; the decoder samples it via the homography, so it is
   used for measurement but kept out of imagery/AF.
3. **Screen pixel structure / moire** → the display is the resolution bottleneck
   (13400 camera px vs ~3840 panel px oversamples the grid). Mitigate: highest-PPI
   panel, light diffuser/anti-glare film, max brightness (avoid PWM flicker vs the
   ~250 µs exposure), exposure ≥ one refresh period if banding. The optical bar is
   deliberately coarse/high-contrast so moire does not defeat the decoder. For a
   true full-sensor sharpness test, a physically translating printed target is the
   higher-fidelity complement.

### 17.5 Status / validation (software only)

- New tests `tools/test_orthosim.py` — **13/13 pass** (codec round-trip + CRC/sync
  rejection, calibration math, config JSON, `TileGrid` interior exclusion, and the
  full decode pipeline both fronto-parallel and perspective-warped).
- `tools/test_autofocus.py` still **9/9** (interior masking is opt-in).
- `capture.app` and `capture.orthosim_decode` import cleanly (offscreen Qt).
- Not yet exercised against the real camera/screen; `--M` must be calibrated from
  the fiducials on the actual bench geometry. All cameras are treated as nadir for
  now (flat scroll); oblique perspective pre-warp is a later option.

## 18. Autofocus ground testing — simulator baseline (2026-07-09)

First autofocus ground-test session (§14). The operator chose the **hardware-free
simulator path** (§14.7 layer 1) for this session — no rig, no camera; the focus
loop closes entirely in software.

- **Firmware:** operator confirmed the ClearCore was **(re)flashed 2026-07-09**
  before the session. This closes the §16.3 "M5 sketch never compiled/uploaded"
  blocker for future on-hardware M5 / OrthoSim tests.
- **Open decision resolved (§16.3):** exposure/gain stay **unlocked in M5**. The
  operator will not touch them during a run, so **no host change was made** — the
  M5-lock idea is dropped, not deferred.

### 18.1 New harness — `tools/af_ground_test.py`

Complements `tools/test_autofocus.py` (a binary pass/fail regression gate) with a
**metrics sweep** — the numbers a bench run would produce, so there is a software
baseline to compare the real rig against:

- Runs the AF engine across the three mirror-rig motion profiles
  (`translate` / `oscillate` / `circle`-with-sky) × assisted/full-auto × a spread of
  starting defocus offsets and scene seeds.
- Reports **accuracy** (pulses off true best focus), **repeatability** (landing-
  position std), **convergence** (frames + commanded pulses), and **sharpening**
  (start→final blur sigma).
- Reuses the existing `FocusSceneSimulator` and the already-tested `run_autofocus`
  driver; **no new dependencies**. `--quick` for a fast single-seed pass. It
  **asserts** every scenario converges (non-zero exit on failure), so it doubles as
  a CI-style gate. Convergence tolerance = 6 pulses (1 pulse = 10 ms open-loop
  drive; no encoder, so a few pulses residual is expected — §14.2/§14.8).

### 18.2 Results (2026-07-09) — 54/54 converged

- `tools/test_autofocus.py`: **9/9 pass**.
- `tools/af_ground_test.py` full sweep: **54/54 runs converged within 6 pulses.**
  - **Assisted** (the default in-flight workflow — operator coarse-focuses first):
    **0.00 pulse error, 0.00 spread** on all three profiles — lands dead-on.
  - **Full-auto:** ~1.33 avg / 2.0 max pulse error, <1 pulse spread; blur sigma
    **5.1 → 0.7** (genuinely sharpened every run).
  - **Convergence** ~18–29 frames (~a few seconds at the ~4.8 FPS M5 cadence). The
    `circle` profile takes a few extra frames, confirming the sky-sector
    **pause/resume** (§14.6) skips low-texture frames instead of chasing them.

### 18.3 Next

- On-hardware **M5 bench run (§15.4 checklist)** is now unblocked (firmware flashed
  today). Keep exposure/gain fixed manually during runs (per §18 decision).
- Re-run `tools/af_ground_test.py` after any AF-engine change to keep the baseline
  honest.

## 19. M5 focus-tap bug fix — first on-hardware M5 run (2026-07-09)

**Symptom.** First live M5 (Ground Test) run: mirrors stepped through the sweep but
**no frames appeared** in the focus preview / AF engine. M0/M1 streamed fine.

**Root cause.** The M5 work (§15/§16) added `ModeController.is_af_mode()` (= M4 **or**
M5) and switched the *app-side* focus gates to it, but the **acquisition thread's
focus-tap emission** in `host/capture/pipeline.py` was left on `is_focus_mode()`
(**M4-only**). So in M5 the app enabled the tap and showed the focus page, the PCB
triggered, and frames reached the backend — but the pipeline's `in_focus_mode` gate
was `False`, so `want_focus_tap` never fired and no `raw_focus_frame` was ever emitted.
The firmware `SweepStepM5()` trigger block was fine all along (byte-identical to
`Sweep()`); this was purely host-side.

**Fix.** `pipeline.py` (~line 785) now gates `in_focus_mode` on `is_af_mode()` (M4|M5).
The focus-crop **disk-writer** gates (`pipeline.py` ~1878/2513) are intentionally left
`is_focus_mode()` (M4-only) — M5 is preview-only (§15.1). The app-side M4-only gates
for exposure/gain lock and fine-focus are also correct as-is (exposure stays *unlocked*
in M5 per §18; M5 forces coarse per §15.3). One-line functional change; no reflash.

**Verified on hardware (2026-07-09).** After relaunch, entering M5 logged
`raw_focus_frame -> focus panel #1 … #16` and `focus-tap emit #N (coarse)` with **no
`no frame in 5s` warnings**; `Swp# 22` shows a full 40-position M5 wrap while streaming.

**Still open.** Camera 3 (`2439007`) failed to enumerate on every launch this session
(only 2/3 cameras) — persistent, not the transient §6 hiccup; needs a physical
CXP/power check on that camera.

## 20. OrthoSim: ground-distance ruler, fast load, launch caveat (2026-07-09)

First real drive of the OrthoSim screen rig with the `Bnei_brak.tif` ortho
(15021×77786 px, **GSD 5 cm/px**, ~1.17 Gpx GeoTIFF on Drive). Three changes plus one
environment gotcha that cost most of the session to diagnose.

### 20.1 Human-readable ground-distance ruler

A labeled scale along the scroll axis (yellow ticks on round distances — `100 m`,
`1 km`, … — with `m`/`km` labels), **locked to the imagery** (ticks scroll at exactly
the imagery velocity/direction). Purpose: from the Iron661 image you can read cumulative
distance / speed and measure camera-px per known ground distance — i.e. the **effective
GSD** that already folds in screen distance + crop, which is what you tune zoom/speed
against (complements the machine-readable coded bar; both can be on at once).

- Toggle live with **G**; start on with `--scale`; config `scale_enabled` /
  `scale_target_px` (desired ~px between ticks, default 200).
- Pure math lives in `tools/orthosim_core.py`: `nice_interval_m()` (1/2/5×10ⁿ interval)
  and `scale_ticks()` (direction-aware `(distance, screen_pos)` list). Rendering is
  `OrthoView._draw_scale()` in `tools/orthosim_gui.py` (left edge vertical scroll,
  bottom edge horizontal).
- Labels are **cumulative simulated distance** (matches the HUD/CSV), not ortho-tile
  position — the ortho loops, so cumulative is the meaningful quantity.
- Self-check: `tools/test_orthosim_scale.py` (Qt-free, no framework, `py -3`) — guards
  round-tick spacing + imagery-locking. **Passes.**

### 20.2 `--reduce {1,2,4,8}` fast load

Full-res decode of the ~1.17 Gpx ortho on the main thread (before the window shows)
took minutes and ~6–7 GB RAM. `--reduce N` decodes at 1/N via `cv2.IMREAD_REDUCED_*`
and **scales `ortho_gsd_cm` by N**, so calibration/scale/HUD are unchanged — only the
source pixels are coarser (a monitor can't show the full res anyway). `--reduce 4` loads
in ~30 s. Also: window now sizes to the available screen and pins top-left (a fixed
1280×800 overflowed small/virtual displays).

### 20.3 Launch caveat — `QT_QPA_PLATFORM=offscreen`

**Symptom.** OrthoSim launched from the agent shell was completely invisible (no window,
no taskbar entry) despite the process being healthy and logging `window shown,
visible=True`. Chased several wrong leads (OOM, off-screen placement, session mismatch).

**Root cause.** The agent/automation shell has **`QT_QPA_PLATFORM=offscreen`** in its
environment, so Qt renders to a virtual offscreen buffer (reports a phantom 800×800
screen, creates no real window). `.NET`/Windows correctly saw the real 1920×1080 desktop
in the same session — only Qt was redirected.

**Fix.** Prepend **`$env:QT_QPA_PLATFORM = "windows"`** when launching for display, e.g.:

```powershell
$env:QT_QPA_PLATFORM = "windows"; $env:OPENCV_IO_MAX_IMAGE_PIXELS = "10000000000"
py -3 tools/orthosim_gui.py "Running TIF\Bnei_brak.tif" --gsd 5.0 --scale --windowed
```

Launching from a **normal interactive terminal needs no override** (it doesn't set
`offscreen`). This affects any Qt GUI started from the agent shell (Iron661 app too).

## 21. Oblique-motor "bonkers" homing — marginal 24 V supply (2026-07-12)

**Symptom.** Commanding a mode from the **Iron661 capture app** made the oblique
(`motor1`) fail to home and "go bonkers" — hunting past its limits, then faulting. The
same `$M0*`/`$M1*` from the Arduino Serial Monitor, from `main.py`, and homing straight
from Teknic **MSP** all worked fine.

**Isolation ladder (what ruled out software).** Same ClearCore, same wire-level
command (`serial_comm.FocusController` sends an identical `$Mn*\n`; the app sends
nothing extra for M0):

| Vehicle | Cameras | Result |
|---|---|---|
| MSP (motor only, no ClearCore) | — | homes fine, repeatable |
| `main.py` sim | none | fine |
| `main.py --real` | **1** streaming | fine |
| Iron661 capture app | **2** streaming | **5/5 fail** |

Firmware diagnostics (temporarily added to `EnableAndHome`) proved the software was
never confusing the motor: on every failure the drive entered homing **clean**
(`alerts=0`), no interrupt activity (`trigISR+=0`, `DI6=0` — the `SweepOnTrig` ISR is
the only interrupt and it never fired), and `EnableAndHome` is a **blocking busy-wait**
that services no serial during homing — so no queued app command can reach the motor
mid-home. HLFB simply **never asserted** and it ran the full `home wait 8000 ms`
timeout.

**Root cause.** **Marginal supply voltage (24 V).** Hard-stop homing needs enough bus
voltage to drive the axis into its stop hard enough to trip the torque threshold. Under
the capture app's heavier electrical load (**two** cameras streaming over PoCXP + the
writer/SHM pipeline), the rail sagged just enough that the oblique couldn't reach that
threshold — so it kept hunting and HLFB never came up. `main.py` (one camera) and MSP
(no extra load) had the headroom, which is why only the full app tripped it.

**Fix (validated).** Raised the supply to **nominal 28 V**. Re-test in the capture app
with both cameras: **5/5 homing success**, oblique completes in a consistent **~4.6 s**
(vs the 8000 ms timeout at 24 V), including a run ~15 min into the session. Lesson:
**check supply-voltage margin under full system load** — a marginal rail shows up as
intermittent, app-correlated motor faults that masquerade as software bugs.

**Firmware changes kept as defense-in-depth** (`Parallax_CLRCR_V2_4.ino`, reflashed
2026-07-12):

- **Homing safe-abort** — any motor that doesn't assert HLFB within `timeout` is now
  **disabled** instead of left enabled (previously it kept running its homing move →
  the endless "bonkers" hunt). Applies to all three homing branches. `$Fault*` is sent
  to the host on a partial homing failure (one motor homes, the other doesn't).
- **Sweep safe-abort** — `maxMoveFaults` (5) consecutive faulted moves in
  `MoveAbsolutePosition` → `abortToSafeMode()` disables both motors and drops to mode 0
  (`sweepAborted` unwinds the in-flight `Sweep`/`SweepStepM5`), instead of the old
  fault → re-home → re-fault loop.
- **`home wait N ms`** diagnostic retained in `EnableAndHome` (cheap, and it's what
  localised this); the noisier `pre-home`/`trigISR`/`DI6` probes were removed once the
  cause was found.

**Operational note (KAYA grabber).** During this session the capture app repeatedly
failed camera bring-up with `KYPY_STATUS_INVALID_FGHANDLE`. Cause: **Vision Point was
open** (holds the frame grabber exclusively); a stale grabber handle can also linger
after a hard kill. Fix: close Vision Point (and any leftover Python), or reboot to
clear a stuck handle, then relaunch.

## 22. Autofocus metric/landing fixes + bench-box BSOD analysis (2026-07-12)

> **Scope note.** All of the crashes below are on the **bench test box** (32 GB RAM),
> not the operational 64 GB machine. The operational machine is unaffected by any of
> this.

### 22.1 Autofocus — two fixes that made the hill-climb usable

Live M4 static-focus runs (vertical camera at a window) with per-position `af-trace`
instrumentation showed the metric, not the search, was the problem, then exposed a
second, actuator problem:

1. **Brightness-invariant metric + light pre-blur** (`focus_measure_full_res` in
   `host/capture/autofocus.py`). The full-res green-subgrid Laplacian variance is now
   (a) normalised by mean² (kills auto-exposure/gain score swings — the earlier
   ~900↔1600 range on a *static* scene) and (b) **pre-blurred with a σ≈1 Gaussian**
   before the Laplacian. The Laplacian is a high-pass and was amplifying sensor noise,
   which put a **10–16 % frame-to-frame spread on the score at a fixed lens position**.
   A gentle smooth costs little real focus signal (defocus blurs over several pixels)
   and roughly doubled the SNR: the focus curve went from a noisy flat line to a
   **clean, repeatable peak** (peak-to-tail ≈ 2×, `best_pos` clustered −2/−1/0 across
   runs instead of the earlier `+2, −13, −2, +0` chaos). `measure_frames=4`,
   `step_pulses=1`.

2. **Direction-matched landing** (`_begin_return`). Even with the clean peak, the
   returned picture was **13–35 % softer than the best AF measured** (`final < best` in
   every run). Cause: open-loop DC gear lash is **direction-dependent** — AF found best
   travelling one way, then returned to `best_pos` from the *other* side, so the optics
   settled a whole lash-band off. The old lash-comp only fixed the *commanded* position,
   not the *approach direction*. Fix: record `best_dir` (travel direction when `best_pos`
   was set) and on return **overshoot to the far side, then approach `best_pos` in
   `best_dir`** — reproducing the exact lash state of the best measurement. Guarded by a
   `final_score ≥ 0.75·best_score` assertion in `test_backlash_does_not_prevent_convergence`
   (large-lash sim). All AF self-checks pass.

Still staged (needs ClearCore reflash before use): **`$S0*` "Strength 0"** (`str0=3500`)
for extra-fine focus increments; host `clearcore_protocol.set_strength(0)` supports it.
AF config stays at strength 1 until it's flashed and confirmed. **Caution:** one live
run read an *identical* score at every position — the focus motor didn't move at all at
strength 1 (stiction / dropped pulse). Sort out reliable single-pulse motion before
going finer, or Strength 0 will just produce more no-move runs.

**RAM-scaled SHM pool** (`pipeline.py`): `SHM_POOL_SLOTS` now scales with physical RAM
(~0.5 slots/GB, clamped [8, 24]) → **24 @ 64 GB (operational, unchanged)**, 8 @ 32 GB
(bench). Bench commit dropped from ~17.7 GB to ~5.7 GB. This does **not** degrade the
operational machine's recording depth.

### 22.2 BSODs on the bench box — hardware/driver, not the app

Three bug-checks on 2026-07-12 while running the capture app, **three different stop
codes**:

| Time | Bugcheck | Notes |
|---|---|---|
| 17:03 | `0x1A` MEMORY_MANAGEMENT | subcode `0x61941` |
| 17:21 | `0x1A` MEMORY_MANAGEMENT | subcode `0x41790` |
| 17:56 | `0x0A` IRQL_NOT_LESS_OR_EQUAL | read of a kernel addr at IRQL 2; hit at app launch / KAYA DMA bring-up |

Plus a **year-long history on this same box**: five `0x10D` WDF_VIOLATION bug-checks
across **July 2025** (7/5, 7/9, 7/11, 7/12/2025) — long before any of the AF/pool work
existed.

**Verdict: this is failing RAM or a buggy kernel driver, not the capture app.**
- A user-mode Python process **cannot** produce `0x0A`/`0x1A`/`0x10D` — those are
  kernel-mode faults. An app can *trigger* a latent fault under memory/DMA load; it
  cannot *be* the fault (you'd get a Python traceback, not a BSOD).
- The instability **predates our work by a year** (2025 WDF_VIOLATIONs).
- **Three different stop codes from one workload** is the textbook bad-RAM signature —
  corruption lands in a different kernel structure each run.
- Reducing the pool to 5.7 GB did **not** stop it → not simply "asking for too much RAM".
- The `0x0A` hit at camera bring-up (KAYA frame-grabber DMA into RAM) — a DMA-heavy
  driver writing a bad region is a plausible trigger for that specific code.

**Next step to close it out.** Run **MemTest86** (bootable USB, multiple passes;
per-DIMM if there are two) — stronger than Windows `mdsched`. Only the *last* crash is
saved (full dump at `C:\Windows\MEMORY.DMP`, overwritten each time; `C:\Windows\Minidump`
is empty). If MemTest is clean, analyse `MEMORY.DMP` to name the faulting driver (likely
KAYA grabber or GPU).

## 23. Focus encoder — motor-shaft AMT203S decision + flat-score logic (2026-07-19)

Evaluated adding an absolute encoder to assist AF (§14.8 "future per-motor encoders").
Candidate: **Same Sky AMT203S-V** — 12-bit (4096 count/rev) absolute, SPI — mounted on
the **DC-motor shaft** (`docs/amt20.pdf`; verify max-RPM spec, see §23.5).

### 23.1 Mechanical chain

NMB SE30R2NTCD brushed DC motor -> **encoder on motor shaft** -> StepperOnline EGS14-G45
planetary gearbox (**45:1**) -> 3D-printed spur gear (70 T) meshing with a spur gear glued
to the lens focus ring (108 T), both module 0.8. Total reduction motor->ring =
45 x (108/70) = **69.4:1**. Focus ring dia. 81 mm.

### 23.2 Why the motor shaft (not the gear output, not a draw-wire)

- **Resolution is a non-issue.** 4096 count/rev x 69.4 ~= 284 k counts per ring rev =
  **~0.0013 deg/count referred to the ring** — three orders of magnitude finer than
  needed. The only real question is backlash.
- **AF closes on the image, not on position.** Focus correctness never depends on knowing
  where the lens is; the encoder's value is in the **actuator**, not the focus criterion.
- **The dominant open-loop failure is actuation reliability, not lash.** Per §22.1 a live
  run "read an identical score at every position — the motor didn't move at all (stiction /
  dropped pulse)." Today `position` = a blind cumulative pulse count where **1 pulse != a
  repeatable amount of motion** (stiction, PWM/supply variation — cf. §21, dropped pulses).
  A motor-shaft encoder fixes exactly this class of bug: **verified motion** (know a pulse
  moved the shaft, or didn't), **closed-loop positioning** (move *to a count* instead of
  timing blind pulses -> faster, monotonic convergence), and an **absolute, power-cycle-
  persistent datum** (focus currently has *no* home reference at all) that enables **focus
  presets** (infinity / hyperfocal / last-good) the current architecture can't do.
- **What it does NOT capture:** gear/spur lash. Referred to the motor, ~1 deg of ring lash
  is ~69 deg of motor rotation, so the encoder sees the lash as its own motion but can't
  tell where in the band the lens sits. **This is acceptable** — the AF already neutralises
  lash *optically* (same-direction dither approach + `backlash_pulses` in `_begin_cycle` /
  `_go_to_arm`, and the direction-matched landing of §22.1). So motor-side buys ~90% of the
  practical benefit for ~10% of the effort and **zero schedule hit**; the gear-output
  remount (>= 1 month) and the SICK draw-wire are both rejected.

### 23.3 Flat-score disambiguation (peak vs lash vs stiction vs floor)

The encoder senses the **motor**, never the lens. "No lens motion" is inferred from the
**image score** staying flat, so *lash* = (encoder counts up) AND (image flat) — it takes
**both** signals; neither alone reveals it.

A naive "gradient == 0 => at peak" rule would false-lock in the lash band. The AF does not
use that rule: `_servo_update` in `host/capture/autofocus.py` samples a **3-point
mini-curve** (-d, centre, +d) and gates lock on **peakedness**, not a flat gradient:

- **True peak** — lash taken up, all arms move the lens: centre HIGH, both arms LOWER ->
  `peak_avg > 0` (concave) -> **lock**.
- **Lash band** — lens moves for no arm: all three read the same -> `peak_avg ~= 0`
  (not concave) -> "defocus floor / plateau, never lock, widen the probe."

So flat-**and**-concave = peak; flat-**and**-not-concave = lash/floor -> keep hunting. Lash
is never mistaken for a peak, and the image alone makes that call (the encoder is not what
prevents the false lock).

What the encoder **uniquely** adds is separating a third flat-score case the image can't:

| Case | Motor encoder | Image score |
|---|---|---|
| Stiction / dropped pulse (§22.1 no-move) | counts = 0 | flat |
| Lash band | counts != 0 | flat |
| Defocus floor (far from focus) | counts != 0 | flat |

Without an encoder all three look identical ("sent pulses, score didn't move"); with it the
**no-move** case is uniquely identified (encoder flat) — the failure the encoder fixes.
Honest limit: the encoder alone still **cannot** separate lash from the defocus floor (both
= motor motion + flat image); only the image peakedness does.

Same pairing **measures the lash band**: drive one way until the image responds, reverse,
count the motor-encoder counts elapsed before the image responds again = lash referred to
motor counts (encoder supplies the count, the image supplies the "lens re-engaged" event).

### 23.4 Optional later: true backlash-free ring position

If absolute presets or **printed-gear slip/wear detection** (a motor encoder can't see it)
ever justify measuring the ring directly, prefer a **magnetic arc/ring scale bonded to the
81 mm focus ring, read by a fixed head** (off-axis rotary magnetic encoder, or an AS5311-
class readhead over a magnetic strip) — you already glue a gear to that ring, so a thin
scale adds ~no mechanical complexity and has **zero coupling backlash**. Not the draw-wire.

### 23.5 Integration caveats

- **Max RPM (the one real risk of motor-shaft mounting).** The motor shaft is the *fastest*
  point (before the 45:1). Verify the AMT203's max operating speed + absolute-update rate
  against the SE30R2NTCD slew RPM under focus drive — the spec to confirm in `docs/amt20.pdf`.
- **SPI wiring on the ClearCore.** AMT203 is SPI absolute (with a "set zero" command); 3
  motors -> 3 encoders -> 3 CS lines or a mux, plus a firmware `FocusAxis` reading counts.
  Document in `docs/clearcore_serial_protocol.md` when implemented.
- **Lash-repeatable presets** only work if a preset is always approached from **one fixed
  direction** (same trick the dither already uses).
- Realises the §14.8 `FocusAxis` abstraction: replace the cumulative-pulse bookkeeping in
  `autofocus.py` (`_drive` / `_RunState.position`) with encoder-count moves + a "did it
  move?" guard, without touching the dither/servo search logic.

### 23.6 Measuring the lash — manual creep + score-vs-encoder scatter (2026-07-27)

The **automatic** backlash probe (the §23.3 drive-until-image-responds pairing, once
`BacklashProbeEngine`) was **removed** — on the real rig it kept mis-tripping: per-frame
Laplacian noise plus the long indoor exposure (30 ms vs a 3 ms pulse) faked an early "lens
responded", giving implausible ~19-count readings. It was replaced by a **manual, operator-
in-the-loop** method that reads the lash straight off a graph. This is now the way to get the
number; the automatic engine and its self-check are gone (see the top-of-file banner).

**Why two peaks = the lash.** The AMT203S sits on the **motor shaft**, *upstream* of the
69.4:1 train (§23.1), so its count leads the lens by the whole gear/spur dead-band. Creep the
focus *through* best-focus and the image-sharpness peak appears at one motor-encoder reading;
reverse and creep back through, and because the motor first has to eat the lash before the
lens turns, the **same optical peak now appears at a different encoder reading**. Plot focus
score (Y) vs encoder (X) and you get **two humps** — one per travel direction. Their
horizontal separation **is the backlash, referred to the motor shaft**.

**Workflow (M4 or M5, encoder streaming):**
1. Lock a well-textured **Fine tile** ("Lock tile") so the score tracks one fixed patch.
2. Optionally tick **"Log CSV"** (writes `timestamp, frame, encoder, focus_score` to
   `Session logs/Iron661_focus_<stamp>.csv`; see `host/capture/focus_csv_log.py`).
3. Creep with the **Up/Down** focus hotkeys (`$S0` = 3 ms @ 7000, or a coarser strength)
   from one side, *past* best focus, then reverse and creep back past it. Do **2-3 full
   passes each way** — the peak is shallow and noisy, so averaging sweeps matters.
4. Watch the always-on **top-right scatter**; the up-arm and down-arm humps show the lash live.

**Analysis (reproducible from the CSV):**
- Segment the rows into **monotonic runs** by encoder direction (ignore <30-count jitter);
  keep only runs whose span crosses the peak (>~1500 counts here).
- Per run, find the focus-peak encoder position two ways: **argmax** of the per-encoder mean
  score, and a **score-weighted centroid** over the sharp region (weight = `score − baseline`,
  baseline ~ the run's floor). They should agree to ~100 counts; disagreement = a lopsided/
  noisy peak.
- Average the up-run peaks and the down-run peaks separately; **backlash = |up_peak −
  down_peak|**.

**Measured (this rig, indoor static target, shallow peak so ±~150 counts):**
- str1 (10 ms @ 7000), 2 passes each way: **~1,210 (argmax) / ~1,295 (centroid)** counts.
- str0 (3 ms @ 7000), 1 pass each way: **~1,412 / ~1,461** counts.
- Backlash is mechanical (independent of drive strength), so take **~1,300 ± 150 motor
  counts** ≈ **~1.6° of focus-ring rotation** (1300 / (4096 × 69.4) rev) — consistent with the
  §23.2 "~1° ring ≈ 69° motor" estimate, and plausible for a 45:1 planetary + 3D-printed
  spur pair. Compensation on a reversal is therefore ~1,300 counts (~54 of the str0 3 ms pulses).

**Counts-per-pulse (same CSV, plateau-to-plateau step, one hotkey press per plateau):**
- str0 (3 ms): **median ~24-25 counts/pulse** (mean ~28, dragged up by bundled taps).
- str1 (10 ms): **~120-225 counts/pulse**.
- So a 3 ms pulse buys only ~**1/6** of a 10 ms pulse, not 3/10 — sub-linear because a fixed
  chunk of each short pulse is spent breaking stiction before the shaft turns. `$S0` = the
  finest reliable step (below 3 ms the motor only buzzes).

**Gotchas:** creep a **high-contrast, well-defocused** patch so the peak is sharp; the first
few frames right after a reversal and the odd single-frame score dropout are noisy — drop
short runs and outliers before peak-finding.

## 24. Non-blocking focus pulses + AF servo/tile overhaul (2026-07-22)

A long session driving live M4 (flight-focus, exercised static on the bench) and M5 (bench
flight-sim, moving mirrors) autofocus. Two families of change: a **firmware fix** that stops
focus pulses from stalling the trigger cadence, and a **host AF rework** so the servo actually
holds/finds focus and picks a sensible target. `tools/test_autofocus.py` is **27/27**.

### 24.1 Firmware — focus pulses now run off a timer, not `delay()` (`Parallax_CLRCR_V2_4.ino`)

**Problem.** A `+`/`-` focus command used to drive the H-bridge and then **busy-block** for the
pulse duration (`focusIncCount × focusTimeIncrement` ms of 10 ms/pulse drive) inside
`CheckMessages()`. That blocking time is stolen from the main `loop()`, so it lands *inside* the
trigger cadence and pushes trigger-to-trigger out — the measured focus frame rate collapsed to
**~2.9 Hz** against the **4.8 Hz** (`minTimeBetweenFrames` 209 ms) ceiling, and M5 "slowed down
very much during capture". Variable cadence also corrupts AF: a stale/late frame makes the servo
read a phantom gradient and drive the **wrong way**.

**Fix (non-blocking `millis()` timer).** The pulse is now *scheduled*, not waited on:
- Globals `bool focusPulseActive` / `uint32_t focusPulseEndTime` (§~119-123).
- The `+` / `-` handlers set `focusMotorPin.State(±focusStrength)`, then
  `focusPulseActive = true; focusPulseEndTime = millis() + focusIncCount·focusTimeIncrement;`
  and **return immediately** (no `delay`, §~423-441).
- `serviceFocusPulse()` (§~314) turns the motor **off** once `millis()` passes
  `focusPulseEndTime`, or immediately if the mode leaves 4/5 (safety). Gated on `focusPulseActive`
  so it's a cheap no-op otherwise.
- Called from `loop()` (§~298) **and** inside the cadence busy-waits of `TakePicsInPlace()`
  (§~748) and the M5 step's frame gate (§~725) — so a pulse that finishes mid-wait is stopped
  promptly instead of drifting to the next `loop()`.

Net: the motor still gets the same drive time, but it overlaps the trigger cadence instead of
blocking it. **The operator has flashed this build.** (Encoderless drive is still open-loop; 1
pulse ≠ a repeatable amount of motion — see §22.1/§23. This fix is about *cadence*, not motion
repeatability.)

### 24.2 Host AF — timing instrumentation to prove the lag (`host/capture/autofocus.py`)

To confirm the cadence story from the host side (and catch stale-frame servo errors):
- `af-time arm=… pos=… lag=…s settle=… frame_dt=…s score=…` logged at each arm measurement —
  wall-clock **lag** from issuing a lens move to scoring it, and **`frame_dt`** (feed_frame
  delivery interval). `_RunState` gained `arm_drive_t` / `last_frame_t` / `frame_dt`.
- `_dump_select_frame()` writes `af_select_frame.png` (+ `_roi.png`) at acquisition — the exact
  decimated frame the picker scored and the ROI it chose — for offline analysis. Guarded to skip
  tiny (<400 px) test frames so the suite doesn't litter the repo.
- Scratch `tools/_af_tile_probe.py` re-scores a dumped frame with the current σ-7 high-pass vs
  Difference-of-Gaussians band-passes (comparison showed the current metric was already the best
  of the candidates on a real hazy frame).

### 24.3 Host AF — continuous dither servo + acquisition robustness

The old hill-climb was replaced by a **continuous dither servo** suited to a moving scene, with:
- **Motion-adaptive profile** (`AutoFocusConfig.apply_scene_profile`, `adaptive_profile=True`):
  starts on the STATIC (averaged + anchored) profile and promotes to the fast per-patch tracking
  profile once sustained motion is measured. **Profile follows detected motion, never the mode
  label** (M4 in flight is *moving* — see the top-of-file M4/M5 warning).
- **Coarse sweep-to-max escalation** — if the dither can't find a slope or snap-back trips, it
  sweeps for the global peak, then hands back to the dither (leashed to the coarse best).
- **Snap-back anchor** — tracks the best absolute centre-arm score/position and refuses to lock
  far below it (kills the "walk away from good focus" and floor-lock failures).
- **Strong-peak lock / anchored lock** — a concave 3-point peak (§23.3) locks immediately on a
  static scene instead of dithering forever; RProp-style **adaptive step** grows while the
  gradient sign holds and shrinks when it flips (fixes the sawtooth crawl).
- **Upward landing** (`_rest_upward`) — the final park always approaches from below to seat gear
  lash the same way the arms measured it.

### 24.4 Host AF — saturation-aware tile ranking

`_unclipped_texture` median-fills clipped pixels before the Gaussian high-pass and **scores only
unclipped detail**, so a genuinely detailed but partly-overexposed tile isn't rejected for a flat
one. Tests `test_saturation_aware_prefers_unclipped_detail` /
`test_partly_clipped_busy_beats_featureless_unclipped`.

### 24.5 Host AF — seed the target from the Fine preview (fixes "AF picks bushes")

**Symptom (operator).** With the scene *somewhat defocused*, manual **Fine** mode shows the
**building/gantry** but **AF's Zoom tile picks bushes**.

**Root cause (not a metric/region/resolution bug).** Fine and AF use the *same* picker, frame,
config and metric. The difference is **when** the pick is made: the Fine tracker locks a tile
while the scene is **sharp** and then *deliberately holds it through blur* (it only re-picks on a
genuine pan-out, never when the image just softens). AF, on start, ran `_force_focus_coarse()`
which calls `_reset_fine_tracker()` — **discarding that held ROI** — then re-picked on the now
**blurry** acquisition frame, where coarse texture (bushes) out-scores fine detail (building).

**Fix.** `_on_autofocus_requested` grabs `self._fine_tracker.roi` **before** forcing coarse and
passes it as `seed_roi` → `AutoFocusController.start_single` → `AutoFocusEngine.start` →
`_RunState.seed_roi`. `_acquire` uses the seed verbatim (clamped + scored, logs
`af-acquire seeded-from-fine …`) instead of re-picking; it's **one-shot** (a later pan-out
reacquire re-picks normally). So: get the target sharp in Fine, then press **Auto-focus while
still on Fine** and AF focuses *that* tile. Starting AF straight from Coarse (or clicking Coarse
first, which resets the Fine tracker) leaves no seed and AF picks as before. Test
`test_seed_roi_is_used_instead_of_repicking`.

### 24.6 Freeze / responsiveness fixes

- **App "NOT RESPONDING" during M5** — PCB pulse-acknowledgement serial spam was flooding the log
  path; it's now filtered. Full-res emission was made **idempotent** so AF reliably has the
  full-res tap on start.

### 24.7 Status / open items

- Firmware non-blocking pulses: **flashed**; expect `frame_dt` to sit near 0.21 s. Re-check the
  `af-time` logs to confirm lag is now ~one frame and steady.
- Seed fix (§24.5): **not yet hardware-verified** — the last on-hardware run predated it (log
  showed `af-acquire target=full-frame`, no `seeded-from-fine`). Relaunch and test via the
  Fine→Auto-focus workflow above.
- Still worth doing (deferred): **SNR-gated lock** — require centre > arms by more than the
  measured cycle-to-cycle noise before counting toward "done", else widen the dither (a run
  declared "in focus" via `locked_cycles` on a noise-dominated hazy tile, not a real peak).
- Two log oddities to keep an eye on: snap-back repeatedly firing at `centre +0` (servo not
  moving off zero before giving up), and a late `no frame in 5s … armed but not streaming` before
  shutdown.

---

## 25. Encoder closed-loop autofocus (AF v2) + servo-BSOD root cause (2026-07-29/30)

The AMT203S encoder (§23) is now integrated and the whole M4/M5 autofocus was
**rewritten** from the continuous dither servo (§24) into a paced, open-loop
**dive-then-climb** engine that only *reads* the encoder. This section records the
encoder wiring, the new engine, the serial/memory hardening, and the BSOD
investigation that turned out to be the **old servo**, not the hardware.

Committed as `Add encoder closed-loop autofocus (AF v2): dive-then-climb engine`
(30 files). The engine sits **beside** the legacy servo (`autofocus.py`) —
`AutoFocusController` can drive either for A/B testing.

### 25.1 Encoder integration (host side)

- **Hardware:** AMT203S-V absolute encoder on the DC-motor shaft (SPI, 12-bit =
  4096 counts/rev). The ClearCore reads it and streams `$ENC=<count>` lines — fast
  (~100 ms) in M4/M5, slow (~2000 ms) elsewhere. `$ENC=-1` = SPI read failed.
- **`EncoderTracker`** (`host/capture/focus_axis.py`): unwraps the wrapping 0–4095
  absolute count into a **monotonic total** (handles the rev roll-over), so the host
  has a continuous position. `serial_client.py` parses `$ENC` off the serial reader
  thread and exposes `encoder_position` / `encoder_present`.
- **`FocusAxis` abstraction:** `OpenLoopAxis` (pulse-count only, legacy/tests) and
  `EncoderAxis` (closed-loop — issues `+`/`-` pulse bursts and reads the encoder back).
  A `raw_count` property gives the live pre-origin/pre-scale count for diagnostics
  ("is the tracker frozen?" vs "did origin/scale math cancel the move?").
- **Polarity (critical):** on this rig a `-` pulse *raises* the raw count, so
  `encoder_counts_per_pulse` is **NEGATIVE (-24.0)**. With that, `position =
  (raw-origin)/cpp` increases on a `+` drive (as the move math assumes). An earlier
  `+24` inverted this and made every "dive down" run *away upward* into the end stop
  ("how the fuck is this in focus?"). Lash ≈ **1290 counts**; one str3 pulse ≈ 34
  position-units (~800 counts), one str0 pulse ≈ ~1 unit.

### 25.2 AF v2 — the dive-then-climb engine (`host/capture/autofocus_v2.py`)

Why not the servo: the settle-and-verify servo re-commanded an **absolute** `move_to`
every frame against ~10 Hz encoder feedback and 15–22 ms discrete pulses. On stale
feedback it overshot (a −120 target ran to −1387) and fired 1000-char correction
bursts — violent oscillation. The fix is to **never** run an absolute-position servo:
take **fixed open-loop steps** and merely read where the encoder landed. A constant
feedback lag then only shifts the whole curve by a constant, which the upward park
cancels.

Phases (`ACQUIRE → DIVE → CLIMB → PARK → HOLD/DONE`):

1. **Dive** — str3, `dive_bursts=4` × 1 pulse down, seating the gear lash from below.
   Separate bursts (encoder re-read between) so a stalled motor / end stop is caught
   early (`dive_min_drop_counts`).
2. **Climb** — str0, `climb_step_pulses=2` up per frame, one **(encoder, score)** sample
   per frame; peak-detect on "score rose then fell" (`peak_fall_frac`,
   `peak_confirm_samples`). One-way, so the lash stays seated and every sample shares
   the same mechanical state → clean curve.
3. **Park** — descend back to the **climb start (dive bottom)** minus a margin, then
   **creep up** onto `best_pos`. Both legs now run at the **climb's pace** (str0, small
   steps, one per frame); descend and creep have **separate time budgets**.
4. **Hold/Done** — one-shot lands and holds (atmospheric lens is static for hours;
   `reverify_interval_s=0`).

Key debugging outcomes this session:

- **Motion lag was the "soft park".** The climb sampled the encoder **while moving**
  (reads a few counts behind), but the park **settled** before its final read (no lag)
  — so it parked a few counts *below* the true peak. `climb_settle_frames` (take N
  frames per step, score only the settled last one) reads an un-lagged position and
  makes focus **consistent**. Confirmed on-rig: the manual pauses (step-gate) were
  doing exactly this by hand.
- **Park pace / aliasing.** An earlier park descended with str3 (~34 counts/pulse) —
  coarse enough to alias the ~10 Hz encoder and seat the lash differently → soft park.
  Doing the whole park at the climb's fine str0 pace fixed it.
- **STOP must not move.** `stop()` used to re-park via the legacy `park_upward()` (a
  big absolute burst) — that yanked the lens off focus on STOP and left pulses draining
  that corrupted the next run's dive ("crazy rapid moves after STOP"). `stop()` now
  **halts in place**.
- **Whole-run scatter:** `_emit_sample()` pushes (encoder, score) every frame across
  dive/climb/park (not just the climb), so the graph traces the entire down-up-down-up
  trajectory — the creep-up limb re-peaking at the same encoder as the climb's
  `best_pos` is the visual proof there's no residual lag.
- **Debug step-gate** (`step_gate`): pauses at every phase boundary until the operator
  presses **SPACE** (app-wide `QShortcut` in `app.py` → `controller.step()` →
  `engine.step()`). Used to confirm the post-climb move was *not* flinging the motor to
  end-of-travel.
- **Do NOT slam trigger cadence to minimum** (`drive_cadence_to_min=False`): that
  legacy dither trick starved the AMT203S SPI read (`$ENC=-1` / frozen at 0 through the
  whole dive).

Config lives in `app.py` (`AutoFocusConfigV2`): `strength=0`, `dive_strength=3`,
`dive_bursts=4`×1, `climb_step_pulses=2`, `encoder_counts_per_pulse=-24`,
`backlash_pulses = round(1.7×1290/24)`. Self-checks: `tools/test_autofocus_v2.py`
(9 cases: axis, static converge/recover, encoder land-from-below, one-shot,
stuck-motor-fails, climb-settle, **stop-halts-in-place**, step-gate).

### 25.3 Serial + memory hardening (what stopped the freezes)

- **Non-blocking serial TX** (`serial_comm.py`): a bounded `queue.Queue` + background
  TX thread with `write_timeout=1.0`. `_send` only enqueues, so the **GUI thread never
  blocks** on serial — this removed the freeze under a pulse storm. `test_serial_tx_queue.py`.
- **Frame conflation** (`pipeline.py` + `app.py`): `_focus_frame_inflight` +
  `note_focus_frame_consumed()` keep **at most one** 8 MB focus frame in flight; stale
  frames are dropped instead of piling up in the Qt event queue (the ~40 MB/s growth
  that preceded the crashes).
- SHM pool still auto-scales to RAM (8 slots @ 32 GB bench, 24 @ 64 GB operational).

### 25.4 BSOD root cause — the OLD flooding servo, not the box

- **Symptom:** repeated BSODs during AF — `0x1A MEMORY_MANAGEMENT`, `0xBE`, `0xA`,
  `0x133 DPC_WATCHDOG`, plus a WHEA fatal hardware error. Looked like bad RAM.
- **Bisection:** old build left running overnight → **no BSOD**. New code idle in
  coarse/fine for an hour → stable. Serial-only stress → **wedged the PCB but did NOT
  BSOD Windows**. Full AF cycle on the rewritten engine → completed, no BSOD.
- **Conclusion:** the crashes tracked the **old flooding AF servo** — the
  settle-and-verify loop re-issuing `move_to` bursts on stale ~10 Hz feedback created a
  serial command storm that, combined with **blocking serial writes on the GUI thread**
  and the 8 MB frame pileup, wedged the driver/kernel. The paced dive-then-climb engine
  + non-blocking TX (§25.3) removed it; system stable 17.5 h+ across the tests. **Not
  ruled out for good** — the crashes were *sporadic*, so keep watching boot time /
  process life across runs (a handful of clean runs is not proof).
- **Separate firmware bug (still open):** the ClearCore **wedges** (stops answering
  serial) after ~676 sustained pulses / ~135 s. This explains AF flakiness (frozen
  encoder, oscillation) but does **not** BSOD Windows. Recover with a **PCB
  power-cycle**; real fix belongs in firmware. `tools/serial_af_stress.py` reproduces it
  without the camera.

### 25.5 Status / open items

- **Focuses consistently** on the static bench with `climb_settle_frames=3`. The
  no-pauses run (`step_gate=False`) is being validated to confirm the built-in per-phase
  settle is enough on its own.
- `climb_settle_frames=3` is a **DEBUG** (slow) value — tune down to the minimum settle
  once the encoder's true settle time is measured.
- `step_gate=False` for production one-shot; flip to `True` only to inspect phases.
- **Open:** the PCB pulse-wedge firmware bug (§25.4); still-sporadic BSOD watch.
- Flight (moving-scene) path is unchanged in spirit — single-object patch tracking
  keeps the scored ROI on target during a slow pan; the multi-object stitch for fast
  churn remains a future layer (not needed to find focus on a near-static scene).

### 25.6 Flight layer: two-object tracking + handover (design, not yet built)

The dive-then-climb sweep takes **seconds**, but in flight the ground **pans
continuously** — any single scored patch drifts across the FOV and exits before the
sweep finishes. The static bench hides this (the patch never moves). The plan for
flight is a **two-object tracking + handover** scheme that stitches focus samples from
successive ground patches into **one continuous score-vs-encoder curve**, so the sweep
can run to completion regardless of scene motion.

**Why it's valid.** The thing we correct is the **atmospheric lens** — defocus that is
**~uniform across the whole field of view** and drifts only over **hours** (§ engine
docstring / §23). So *best focus is the same encoder position no matter which ground
patch you look at.* That is the key enabler: two different patches, scored at the same
encoder position, should report the **same trend**, so their samples belong on the same
curve. (Absolute sharpness differs per patch — see normalisation below — but the
*location of the peak in encoder space* does not.)

**The handover loop:**

1. **Acquire** a high-texture patch at the **leading edge** of the optical flow (the
   side new ground enters from) — `leading_edge_from_motion()` +
   `select_leading_edge_roi()` already pick this.
2. **Track + score** it every frame as it traverses the FOV (`PatchTracker` gives the
   per-frame `dx,dy`; the ROI is advanced by the flow). Each frame contributes one
   `(encoder, score)` sample to the sweep curve.
3. **Before it exits** the trailing edge (`_roi_pinned_against_flow()` detects a patch
   pinned against the outflow edge), **acquire a second** leading-edge patch and run
   **both** for a few overlap frames.
4. **Cross-calibrate on the overlap:** while both patches are in-frame and being
   scored, compute a **scale factor** that maps the incoming patch's score onto the
   outgoing patch's (they have different absolute contrast). Apply it so the stitched
   curve has **no discontinuity** at the handover.
5. **Drop** the outgoing patch once it leaves; the incoming patch is now the primary.
   Repeat — an unbounded relay of patches carries one continuous curve across the whole
   dive-then-climb, however far the scene has panned.

**Normalisation options** (pick per rig noise): per-object divide-by-own-running-max,
or the overlap scale factor above (preferred — it's a direct measurement, no
assumption about each patch's absolute texture). The **peak encoder position** is what
we read off the stitched curve; absolute score magnitude only matters for the
contrast/lock check.

**Failure handling:** reject featureless / sky patches (`min_texture_score`,
`_frame_is_featureless`) so a blank stretch doesn't inject a false low; if no valid
leading-edge patch exists for several frames, pause the sweep (don't advance the
motor) until texture returns rather than sampling garbage.

**Scaffolding already present** (so this is an extension, not a rewrite): the engine
holds `rs.objects` as a **list** of `_TrackedObject(oid, roi)` with a `next_oid`
counter; `_advance_objects()` already moves every tracked ROI by the flow, drops ones
pinned against the outflow, and **re-acquires a leading-edge patch when the list goes
empty**. What's missing for true flight is: (a) running **two** objects concurrently
with a deliberate overlap window, (b) the **overlap scale-factor** cross-calibration,
and (c) feeding the *stitched* (normalised) samples — not just the primary object's —
into the climb's peak detector.

**ponytail:** deliberately deferred. Single-object tracking is correct for the static
bench and slow pans we can actually test now; the relay only earns its complexity at
fast flight churn, which we can't exercise until we fly (or drive M5 mirrors fast
enough to pan a patch fully across the FOV within one sweep). Build it against an M5
fast-pan bench case first, with the stitched curve drawn on the same score-vs-encoder
scatter so the handover seams are visible.

## 26. Timelapse mode — M6 (2026-07-30)

**Why.** A recording mode for a **side-by-side comparison against the Phase One
camera**: park the mirrors in the mid position and grab one full-res frame per second
so the Iron 661 output can be lined up against the Phase One's on the same static/slow
scene. It reuses M4's "fire in place" mechanics but, unlike M4 (preview-only), it
**writes to disk like M0/M1**.

**Behaviour.** Mirrors held at the **mid position** (`(0,0)`, same as Focus/M4), **one
trigger pulse per second**, host records **full-res** frames to the per-camera drives
via the same `.bay14` write path as M0/M1.

### 26.1 Firmware (`Parallax_CLRCR_V2_4.ino`)

- `loop()` `case 6: TakePicsInPlace();` — reuses the M4 in-place routine (mid position +
  periodic trigger; the trigger period is the global `picsInPlaceDelay`).
- `$M6*` handler → `setMode(6)`.
- `setMode(6)`: `setupMotorsFast()` then `picsInPlaceDelay = 1000` (one trigger/sec).
  Because M6 shares `TakePicsInPlace()` with M4, entering another mode and returning
  re-runs `setMode` so the 1000 ms is re-applied on each M6 entry.
- `+`/`-`/`>`/`<` remain gated to `mode==4||mode==5`, so M6 stays a fixed 1 Hz (no live
  rate tuning) — intentional for a controlled comparison.

### 26.2 Host (Iron661Capture)

- `mode_controller.py`: new `Mode.M6`, button label **"Timelapse"**, button widths, and
  **added to `_RECORDING_MODES`** — so `can_write()`/`is_recording_mode()` treat M6 as a
  recording mode and every existing writer/exposure gate picks it up automatically.
- `serial_client.py`: PCB int `6 → Mode.M6`.
- `app.py`: mode button + tooltip; `Mode.M6 → 6` for `$M6*`; M6 included in the
  recording-mode branches (pre-flight "nothing will be saved" warning, refuse-if-not-armed
  guard, full-res emission, `.bay14line` open, GPS-correlator reset). M6 stays on the
  **normal preview stack** (not the focus panel — `in_focus` is still M4/M5 only).
- Docs: `docs/clearcore_serial_protocol.md` `$M6*`; `serial_comm.py` docstring.

### 26.3 Status / notes

- Firmware **flashed** by the operator (2026-07-30) before this session's app launch.
- M6 reuses the M0/M1 file naming, so `SweepCounter` still rolls sweep/frame indices in
  groups of 13 even though the mirrors don't sweep. Acceptable for the comparison
  (frames are contiguous 1 Hz); a flat timelapse counter is a possible later tweak.

## 27. AF climb SNR — lucky imaging + curve-fit peak + two-object test tool (2026-08-02)

**Why.** Shooting outdoors through the window (representative of real flight) the focus
score is much noisier than indoors: peaks are flatter and the frame-to-frame scatter is
larger. The dive-then-climb search (§25) picked its focus position from the **argmax of a
single noisy sample per encoder step**, so a noise spike could define "focus" and a false
peak could latch. The changes below make the peak estimate robust **without** touching the
metric or the acquisition (exposure/gain), and every one is flag-gated so it can be A/B'd or
reverted from `app.py` alone. All live in `host/capture/autofocus_v2.py`.

### 27.1 Lucky-imaging aggregation — max over N frames per position

- `_climb_tick` now **dwells `climb_lucky_frames` frames at each encoder position** (the lens
  is held — no step during collection) and takes the **max** of those scores as that
  position's sample. Turbulence, heat-shimmer through the window, and platform vibration only
  ever *lower* sharpness, so the sharpest frame in a short burst is the least-corrupted read
  of true focus; the mean is biased low and wobbly. This is the classic long-range/astro
  "lucky imaging" trick.
- The scatter graph still receives **every raw frame** (`self._sample(pos, score, True)`);
  only the peak logic consumes the per-position max. `lucky_left` counts **frames** (not just
  valid scores) so a stray `None` (no fresh full-res crop that frame) can't stall the climb.
- `climb_lucky_frames=1` reproduces the old score-every-frame behaviour exactly. Wired to
  **3** in `app.py` — roughly 3x the climb duration at ~4.8 fps, fine for the static/bench
  scene, in exchange for a much cleaner curve. **Supersedes the dead `climb_settle_frames`
  knob** (it was set but never read; kept only for config parity).

### 27.2 Curve-fit peak — parabola vertex, not argmax

- New pure helper `_fit_peak(samples, window, log_domain=True)`: fits a quadratic to the
  `peak_fit_window` samples on each side of the argmax and returns the vertex `-b/2a`. Using
  the whole shoulder averages out the per-sample noise that makes a raw argmax jump around.
  Returns `None` (caller keeps the argmax) when there are `<3` points, the fit isn't concave
  (`a >= 0` — no real hump), or the vertex lands outside the fitted span (extrapolation is not
  a measurement).
- `_finish_climb` applies it when `peak_curve_fit` is on and logs both:
  `af2-climb peak refine argmax=%+d fit=%+d` — so the correction is visible live.

### 27.3 Log-domain fit — variance stabilisation for the metric's multiplicative noise

- The observation that "the signal gets noisier the higher the score" is **intrinsic**: the
  metric (`focus_measure_full_res` = `lap.var()/(mean²+1)`) is a **variance estimator**, and a
  variance estimator's sampling noise scales with its value (`SD ∝ score`). The *relative*
  scatter is roughly constant; the *absolute* scatter grows near the peak.
- Consequence: plain least-squares (`np.polyfit`) assumes constant-variance noise, so it
  **over-weights the noisy high points**. `_fit_peak` now fits **`log(score)`** first
  (`peak_fit_log=True`): `log` is the variance-stabilising transform for multiplicative noise
  (→ ~constant additive noise, which OLS wants), and it's a **better-shaped model** too — a
  near-focus sharpness peak is ~Gaussian and `log(Gaussian)` is exactly a parabola. Degenerate
  log fit → silent fall-through to the linear fit (also the `peak_fit_log=False` path).
- **Honest caveat:** at the current `peak_fit_window=3`, if the peak is *broad* the shoulder
  samples are all near the peak in magnitude, so log vs linear is nearly a tie — the log
  benefit only becomes material when the fit window spans real dynamic range. Widening
  `peak_fit_window` (to 4-5) is the lever to actually cash it in on a broad peak. Left at 3;
  the change is theoretically correct and never does worse.

### 27.4 Revert switches + self-check

- One place, `app.py` `AutoFocusConfigV2(...)`: `climb_lucky_frames=1` /
  `peak_curve_fit=False` / `peak_fit_log=False` each independently back out one layer.
- `tools/test_af_peak.py` (4/4): the fit lands near a known vertex, beats raw argmax on
  averaged noisy trials, returns `None` on degenerate inputs (ramp / too-few / flat), and the
  **log-domain fit beats the linear fit** on a Gaussian peak with multiplicative noise.

### 27.5 Two-object tracking test tool + M5 sweep-reversal revert

- New **"2-obj track"** button in `focus_panel.py` (`dual_track_toggled` signal): runs the
  `DualObjectTracker` (`host/capture/dual_tracker.py`) on the coarse frame and outlines each
  tracked object with a **green box + its object id** on the main preview — **no autofocus,
  no full-res** — purely to eyeball the leading-edge→trailing-edge object flow across the FOV.
  In `app.py`, `_feed_dual_track` (green-box overlay) and `_feed_dual_preview` (split-screen
  crops) are **mutually exclusive** so the shared tracker advances once per frame.
- A sweep-reversal reset had been added to `DualObjectTracker` so the tracker didn't get
  confused when the **M5 mirror sweep reverses** direction — but that reversal is a
  **bench-only artifact** (a real flight/rolling-orthophoto pan is unidirectional), so it was
  **reverted** to keep the tracker honest for the real use case. Test tweaks in
  `tools/test_dual_tracker.py` were reverted with it.

### 27.6 Status

- All three AF SNR layers are **on** and were confirmed on the bench ("looking good" — peak
  refine visibly correcting the argmax; consistent focus). Next: rolling-orthophoto motion
  test for the two-object tracking/handover path.

## 28. Camera optics — focal lengths + pixel pitch (GSD → altitude) (2026-08-06)

Constants needed to turn the OrthoSim **simulated camera GSD** (cm/cam-px) into a
physical **altitude**. The link is the imaging system's angular resolution:

```
altitude = GSD · f / p            (all consistent units)
altitude_ft = 10 · GSD_cm · f_mm / p_µm · 3.28084
```

(this is `orthosim_core.simulated_height_hint_m()`, ×3.28084 for feet). GSD is the
*ground* sample distance; smaller GSD = lower altitude. `f` = lens focal length,
`p` = sensor pixel pitch.

### 28.1 Sensor pixel pitch — Sony IMX661 (the "661")

- **Pixel pitch: 3.45 µm × 3.45 µm**, resolution 13400 (H) × 9528 (V), 56.7 mm diagonal
  (Type 3.6"), global-shutter CMOS. Source: KAYA **Iron 661 CoaXPress** datasheet /
  Sony **IMX661** flyer (the camera name's "661" is the IMX661 sensor).
- Same sensor on all three rig cameras, so `p = 3.45 µm` is common to all.

### 28.2 Per-camera lens focal lengths

| Camera serial | Focal length |
| --- | --- |
| **2439008** | **300 mm** |
| **2439007** | **400 mm** |
| **2439006** | **400 mm** |

Worked example (OrthoSim bench, active cam **2439008**, f = 300 mm, p = 3.45 µm):
`altitude_ft = 10 · GSD_cm · 300 / 3.45 · 3.28084 ≈ 2853 · GSD_cm` — i.e. a decoded
simulated GSD of 1.0 cm/cam-px ≈ 2853 ft; 0.5 cm/cam-px ≈ 1426 ft.

> The OrthoSim GT status line now shows feet directly — `_feed_orthosim` keys the focal
> length off the active camera serial (`_CAM_FOCAL_MM`) with the shared 3.45 µm pitch.

---

## 29. OrthoSim GT — the altitude readout was never a measurement (2026-08-06)

**The headline: the bench had been reporting 10,000 ft while actually simulating ~2,985 ft**,
and nothing in the decode chain could have noticed. Fixed by measuring the magnification
`M` off the fiducials instead of assuming it. Also fixed the optical-bar decode, which had
been failing 100 % of the time on the real rig.

### 29.1 Why the readout could not disagree with the display

Speed, distance and GSD are **authored by the display** and read back out of the optical
bar. The display integrates its own simulated speed into a distance, computes
`gsd_cm = ortho_gsd_cm / (Z · M)`, and encodes all of it. Even `vel meas=` is only the
decoded distance differentiated over wall-clock time — it verifies the **clock**, not the
optics. No wrong lens, screen distance or zoom can show up in any of it.

`M` (camera-px per display-px) is a **hand-set `--M` flag defaulting to 1.0**, and altitude
scales as `1/M`. With the stock config (`ortho_gsd 5.0`, `Z 1.4264`, `M 1.0`) the chain lands
on 3.505 cm/px = **9,999.8 ft by construction**. The display was being launched without
`--M`, so the 10,000 ft readout was the default config restating itself.

**Velocity was never affected** — `M` cancels out of ground speed entirely (verified: `M=1.0`
and `M=3.3553` both recover exactly 100.0 kn). Only altitude was wrong.

### 29.2 Fiducial metres vs. ground metres (the thing that confuses everyone)

Inverting `altitude = X·f/(N·p)` on the **fiducials** gives the *distance to the screen*
(~8 m of bench), not the simulated altitude — the markers are furniture on the bezel, not
features on the simulated ground. The formula is right; it needs a line whose length is known
in **ground** metres.

Two equivalent routes, both now confirmed to agree:

- **Pixel domain (what `measure_scale` does).** The imagery carries 5 cm of ground per ortho
  pixel painted at `Z` screen-px per ortho-px, so every screen pixel holds `5/Z` cm of ground
  *regardless of panel size*. Only `M` is unknown. The fiducials sit at known display-pixel
  positions (`screen_w − 2·margin − fiducial_px` = **3652 px** at 3840 wide), so their imaged
  separation gives `M` directly. Panel pitch and focal length **cancel**.
- **Ground-metre domain.** Any known ground length works. The scroll supplies one free: at
  100 kn, 51.44 m of ground per second. `GSD = ground_speed / camera_flow`. The `--scale`
  ruler (**G**) is the static equivalent — at 10,000 ft, 100 m of ground spans 2,853 px.

> Sanity phrasing that settles it in one line: *at 10,000 ft the ground drifts across the
> sensor at 783 camera px/s.* Measure the actual drift and divide.

### 29.3 `measure_scale()` — M from the fiducials (`orthosim_decode.py`)

New pure function taking the id 0..3 centres from `detect_fiducials`. Averages **opposite
edges** per axis so a modest perspective tilt cancels instead of biasing one side. Returns
camera-px per display-px at the detection resolution; `app.py` multiplies by
`KyfgCamera.SUBSAMPLE_FACTOR` (decode runs on the 4× coarse tap). Logged **once per session**
on the first good decode (`_measure_ortho_scale`), reporting the `--M` the display should
have been launched with. Tests: recovers deliberately anisotropic known scales; unbiased
under a keystone warp.

### 29.4 Optical bar — local-envelope thresholding (the 100 % CRC failure)

`_sample_bits` thresholded all 120 cells against one global `(max+min)/2`. On the real rig
the imaged white level **ramps better than 5:1 across the bar** (measured ~205 at one end vs
~43 at the other) while black stays flat near 15, so the dim end's whites fell under the
global midpoint and read as black. **Every frame failed CRC** with the screen fully visible
and in focus.

`_cell_thresholds()` now thresholds each cell against the midpoint of its local min/max
envelope (`_ENVELOPE_CELLS = 15`), which follows the ramp. Verified on the real saved frame:
decodes across 38 different parameter combinations (window fraction 0.2–0.4, vertical offset
±8, envelope 9–21) with **byte-identical payload** — an 8-bit CRC does not pass 38 times by
chance.

> ponytail ceiling: a run of ≥15 identical cells collapses the envelope, so those windows
> fall back to the global midpoint. A long run in a badly lit stretch can still misread; the
> CRC catches it and the next frame (1 Hz) is used. Upgrade = run-length-limited encoding,
> or fit a smooth illumination model over the whole bar.

Live result: **~75 % of frames decode** (52/69, then 35/47), up from **zero all session**.
The residual failures are likely the exposure straddling a display refresh and catching the
bar mid-update — untested.

### 29.5 Diagnostics that were lying

- `u8 min/max` in the no-decode log was **tautological**: `_to_gray_u8` min-max normalises,
  so it always printed `0/255`. Now reports the **sensor's** range (`raw min/max=1445/16383`
  — note 16383 is the 14-bit ceiling, i.e. some saturation).
- The one-shot debug dump only fired when fiducials were **found** and the bar failed — the
  opposite of the actual failure mode, so nothing was ever saved. `_dump_ortho_frame_debug`
  now saves the coarse frame on **fiducial failure** (`ortho_dbg_frame.png`), which is what
  finally showed the framing.

### 29.6 Bench calibration — measured values (2026-08-06)

| Quantity | Value |
| --- | --- |
| **M measured** | **3.3047 x / 3.4060 y → mean 3.3553** (anisotropy 0.970 = slight screen tilt) |
| 4K panel | Qt name `AAA`, 3840×2160, physical **1016 × 572 mm** |
| Panel pixel pitch | **0.2646 mm** (1016 / 3840) |
| Fiducial separation | 3652 screen px = **966 mm** |
| Before calibration | reported 10,014 ft / **actual 2,985 ft** (GSD 1.046 cm/px) |
| After (`--M 3.3553 --zoom 0.4251`) | GSD 3.5052 cm/px = **true 10,000 ft** |

Cross-check: `M` was independently estimated at **3.16–3.36** from tracker flow vs. encoded
speed *before* the fiducial measurement, and the geometric ceiling is `13400/3840 = 3.49`
(the screen must fit the sensor width). All three agree.

**`M` is a property of the rig** (camera distance, lens, panel pitch) — it does **not** change
with zoom, so measure once and it holds until the camera or lens moves.

### 29.7 Launch recipe (both apps)

```powershell
# OrthoSim display — note the pixel-limit env var and --screen 1
$env:QT_QPA_PLATFORM = "windows"; $env:OPENCV_IO_MAX_IMAGE_PIXELS = "10000000000"
py -3 tools/orthosim_gui.py "Running TIF\Bnei_brak.tif" `
    --gsd 5.0 --screen 1 --scale --M 3.3553 --zoom 0.4251 --speed 51.44

# Capture app
$env:QT_QPA_PLATFORM = "windows"; py -3 host/main_capture.py
```

Two launch traps cost time today:

- **`OPENCV_IO_MAX_IMAGE_PIXELS`** — `Bnei_brak.tif` is 15021×77786 ≈ **1.17 gigapixels**,
  over OpenCV's 2³⁰ default. Without it: `(-215:Assertion failed) pixels <= CV_IO_MAX_IMAGE_PIXELS`.
- **`--screen 1`, not 0.** Qt enumerates the **Dell U2417H (1920×1080) as screen 0** and the
  4K panel as **screen 1**; this order has changed at least once. Landing on the small screen
  silently renders at 1920×1080 and every layout-dependent decode fails. Check the
  `window shown, geometry=...` line — it must read `3840, 2160`.

To retarget the altitude: `zoom = 5.0 / (GSD_target_cm · M)`, where
`GSD_target_cm = altitude_ft / 2853` for the 300 mm cam. Or dial `-`/`+` live and watch
`sim camera GSD` on the HUD (**H**) reach **3.5052** for 10,000 ft. **S** saves to
`orthosim_config.json`.

### 29.8 Two-object tracking at 10,000 ft

Measured by **Little's Law** (mean live objects ÷ creation rate — assumes no tile geometry),
over a 181 s window with 74 transitions:

| | this morning (~3,000 ft, 53 kn) | now (10,000 ft, 100 kn) |
| --- | --- | --- |
| Object lifetime | 2.53 s | **4.26 s** |
| In frames | 10.1 | **20.4** |
| Flow | 617 coarse px/s | 378 coarse px/s |
| Delivered rate | 4.46 fps | 4.78 fps |
| Mean live objects | 1.97 / 2 | **1.98 / 2** |

Higher altitude means *less* angular motion for the same ground speed, so lifetime nearly
doubled despite the speed going 53 → 100 kn. The two-object relay holds both patches alive
essentially always, so a handover has continuous overlap (~40 frames of tracked texture
across one).

Resolution improved too: each 5 cm source pixel now spans **1.4 camera px** instead of 4.5,
so the imagery carries real high-frequency detail rather than interpolation — materially
better for a sharpness metric. **Natural ceiling: 5.00 cm/px (1 ortho px = 1 camera px) at
14,265 ft** — below that the bench is upsampling the source.

### 29.9 Sensor orientation vs. flight direction — the bench flatters the 300 mm

**The bench does not model either flight case, and errs in opposite directions for the two
lenses.** Which sensor axis an object traverses depends on how the sensor is clocked relative
to the direction of travel:

| Setup | forward axis | object traverses | leading edge |
| --- | --- | --- | --- |
| **Flight, 300 mm** | long edge forward | **13400 px** (long) | short edge |
| **Flight, 400 mm** | short edge forward | **9528 px** (short) | long edge |
| **Bench (today)** | — (vertical scroll) | **9528 px** (short) | long edge |

Scaling from the measured 4.26 s / 20.4 frames (§29.8), at 10,000 ft and 100 kn:

| | traverse | flow | lifetime |
| --- | --- | --- | --- |
| Bench now (300 mm, short axis) | 2382 coarse px | 378 px/s | 4.26 s — **20.4 frames** |
| Flight 300 mm (long edge fwd) | 3350 coarse px | 378 px/s | 5.99 s — **28.6 frames** |
| Flight 400 mm (short edge fwd) | 2382 coarse px | 504 px/s | 3.19 s — **15.3 frames** |

The 400 mm is worse than its narrower FOV alone implies, because **two factors multiply**: it
traverses the short axis (0.711) *and* its longer focal length speeds up ground flow at the
same altitude, since `GSD = h·p/f` (0.750). Together **0.533 — the 400 mm gets about half the
300 mm's object lifetime.**

> **Consequence for AF.** 15.3 frames is the binding constraint, and it is *below* what the
> bench shows. If a dive-then-climb needs more than ~15 frames (with `climb_lucky_frames=3`
> per position it plausibly does), the cross-object handover is **mandatory** for the 400 mm,
> not an optimisation. Validating a sweep on the 300 mm bench geometry flatters it by ~1.9×.

No code change is needed for orientation: `leading_edge_from_motion` resolves the leading edge
from measured `dx`/`dy` every frame (today's run reported `lead=bottom` without being told).

**Benching the 400 mm worst case (chosen approach).** The bench already traverses the short
axis, i.e. the 400 mm's flight orientation, so the *only* discrepancy is flow — and flow scales
with focal length. Multiply the ground speed by **400/300**:

```powershell
--speed 68.59      # 133.33 kn = 100 kn x 4/3 ; keep --zoom 0.4251 --M 3.3553
```

→ 3.19 s / **15.3 frames**, an exact match for the 400 mm's flight image motion. GSD stays at
the 300 mm's 3.505 cm/px, so scene *detail* is still the 300 mm's — only the motion is the
400 mm's.

*Alternative if scene detail matters too:* match the 400 mm's actual GSD at 10,000 ft
(2.629 cm/px) with `--zoom 0.5668` at a real 100 kn → 3.29 s / 15.7 frames, and both flow and
detail are right. Caveat: the app would label it **7,500 ft**, because `_feed_orthosim` derives
feet from the *physical* 300 mm lens. GSD is what the AF actually sees; the altitude label is
lens-dependent annotation.

**Benching the 300 mm flight case** would need the long axis traversed, i.e. horizontal scroll
(`--dir left|right`). Not practical with `Bnei_brak.tif` as-is: it is 15021 × 77786 (tall and
narrow), so horizontally it is only ~1.7 screen widths at `--zoom 0.4251` and would seam every
~15 s against ~6 s object lifetimes. Would need the ortho rotated 90° offline (→ 77786 × 15021,
seams ~75 s apart) or the camera rotated on the rig.

### 29.10 Status / open items

- ✅ M measured, altitude honest, 10,000 ft @ 100 kn verified end to end.
- ✅ Bar decode fixed (0 % → ~75 %).
- ⬜ **Residual ~25 % CRC failures** — suspect exposure straddling a display refresh. Untested.
- ⬜ **Whether an AF dive-then-climb completes in 15 frames** (the 400 mm worst case — see
  §29.9; 20 frames is the bench figure and 28 the 300 mm flight figure). The climb dwells
  `climb_lucky_frames=3` per position at `climb_step_pulses=2`, so it depends on how many
  positions it walks. Watch `af2-climb begin` and the handover lines on the next run.
- ⬜ **Run the 400 mm worst case** (`--speed 68.59`) before trusting any sweep result; the
  default bench geometry is ~1.9× optimistic for that lens.
- ⬜ AF's **first** target is picked with no motion history (`leading_edge_from_motion(0,0)`
  → `None`), so it starts mid-frame with roughly half the travel. The handover has to fire
  almost immediately.
- ⬜ The AF ROI overlay only draws the **primary** object; the on-deck handover patch is
  tracked and cross-calibrated but never rendered, so an AF run shows one box even when the
  handover is working correctly.

---

## 30. AF cross-object handover — three fixes, one failed redesign, and a way to measure truth (2026-08-10)

Fifteen AF runs at the 400 mm worst case (`--speed 68.59`, 133.3 kn, GT-verified at
10,014 ft). The headline is methodological: **freezing the scene gives an absolute focus
reference**, and it immediately overturned a redesign that a page of simulations had
justified. Read §30.4 before trusting any AF accuracy claim in this document.

### 30.1 The bug that made the parked position meaningless

The climb spans ~20 objects (an object lives ~8–15 frames; the climb needs ~170–240), and
each patch has its own unknown brightness/texture scale `k`. AF v2 recovered `k` by
measuring an A/B ratio at each handover and multiplying it into a running
`cumulative_scale`. Sorting six consecutive runs by final `cum_scale` exposed the problem:

| `cum_scale` | 7.8e−05 | 0.030 | 0.050 | 0.270 | 0.433 | 3.974 |
|---|---|---|---|---|---|---|
| **parked at** | −122 | −122 | −56 | −27 | +0 | +12 |

Rank correlation **1.00**, six for six. The parked position was a pure function of stitch
drift with no discernible dependence on focus. A decayed stitch tilts the reconstructed
curve downward, which pushes the parabola vertex off the left end and parks the lens at the
dive floor. Run 5 shows why tuning could never fix it: its per-link ratio averaged 0.922 —
only 8 % off unity, a genuinely good content ratio — and 0.922¹⁶ = 0.272, matching its
logged `cum_scale` of 0.2702 to three digits. **Error in a product compounds**; landing
within ~5 pulses would have needed every one of 16 links accurate to ~1.4 %.

### 30.2 Three fixes that survived

1. **Handover ordering** (`_advance_objects`, `autofocus_v2.py`). On the promotion frame the
   on-deck slot was refilled *before* `_handle_handover` ran, clobbering `ondeck_oid` and
   resetting `_CrossCal`. Every run logged "no stitchable cross-cal → re-anchor" and
   `handovers=0`. A `not promoted` guard defers the refill by one frame.
2. **Metric mismatch** (`_coarse_pair_score` + `focus_measure_normalized`). The ratio was
   measured as raw Laplacian variance on coarse frames while the curve used mean²-normalised
   full-res scores — so the ratio was dimensionally wrong and biased by brightness. Live
   effect: per-link geometric mean went **0.48–0.92 → ~1.07**, and `cum_scale` stopped
   collapsing (2.2–7.4 instead of 1e−05).
3. **Park diagnostic** (`best_oid` in `_RunState`). On a moving scene the object under the
   park is rarely the one the peak was measured on, so `parked/peak` compared different
   patches and cried "parked but soft" spuriously. It now reports
   `peak was oid=N, parked on oid=M -> NOT comparable` and withholds the warning.

### 30.3 The redesign that lost on hardware

`_fit_peak_scale_free()` locates the peak from **per-segment log-slopes**: `k` cancels
exactly, since `d/dp log(k·S) = d/dp log S`, and for a ~Gaussian response the log-slope is
`-(p-p0)/σ²` — linear, crossing zero at focus, for *any* σ. No ratio, no chain, nothing to
compound. Simulation strongly favoured it (median 4.0 vs 11.3 pulses against the measured
run-7 structure; flat at 4.0 as scene heterogeneity rose while the parabola degraded past
35). Two real defects were found and fixed along the way — `min_seg_points` had to drop to
2 because live objects only survive ~2.8 samples, and the tail noise floor levered the
crossing to the sweep edge (a 2 % floor reproduced the live +9-pulse bias exactly).

**It still lost, by 3×.** Against a frozen-scene reference it put focus 54 pulses out where
the raw argmax was ~13 (§30.4). Cause: real segments are 2–3 samples at 2-pulse spacing, so
per-segment slopes are mostly noise, and with only 1–2 segments past the peak the zero
crossing extrapolates instead of interpolating. The simulated noise model was too kind.

⛔ **The parabola is primary again.** `_fit_peak_scale_free` and `_segment` remain in
`autofocus_v2.py` **unused**, with the hardware numbers recorded at the call site, plus 7
self-checks in `tools/test_af_segment_peak.py`. The scale-invariance argument is still
sound and would matter if stitch drift ever returns — but it needs far more samples per
object to be usable. Do not re-enable it on the simulation argument alone.

`climb_samples` keeps its `(pos, score, oid)` shape from that work; the oid is a useful
segment marker and one self-check pins the 3-tuple plumbing.

### 30.4 Freeze the scene to get ground truth ⭐

Every AF quality metric we had was self-referential (`contrast`, `improvement`,
`parked/peak`, and the last is invalid across objects). **Pause OrthoSim with `SPACE` and
re-run AF**: one object for the whole climb, so `handovers=0`, a clean single curve, the
scale problem cannot arise, and `parked/peak` becomes a *valid* number. The difference
between the moving and frozen park positions is the moving-AF error, in pulses.

Rules for a valid comparison:

- **One session, no relaunch, stay in M4.** The axis homes at bring-up, so each launch has
  its own encoder zero and cross-session positions are not comparable. (This also explains
  an apparent 20-pulse "focus drift" between runs 7 and 8 — different zeros, not drift.)
- Both runs dive below focus and climb up, so backlash cancels. Preserve that.
- `SPACE` is safe: `step_gate` defaults `False`, so it cannot advance an AF phase.
- If a supposedly frozen run logs `handovers>0`, the scene wasn't frozen — discard it.

Measured (argmax, the estimator-independent quantity):

| session | static argmax | moving argmax | offset |
|---|---|---|---|
| 328754 | +8, +0 | −9 | **−13** |
| 328755 | −2 | +11, +7, +0 | **+8** |

**The offset flips sign, so there is no detectable moving-vs-static bias** — just a common
~±10-pulse random error, the same magnitude as the static reference's own repeatability
(+8, +0). An earlier "13-pulse bias" inferred from a single reference was scatter.

**±10 pulses is cheap.** Run 15 parked 7 pulses off its own argmax and still measured
`ratio=0.96` — 96 % of peak sharpness. That is a *measurement* of the tolerance, not a
guess, and it matches the operator's naked-eye view that recent runs looked sharp.

### 30.5 Status / open items

- ✅ Handover stitches (`handovers=13–21` per run, was 0). Ratios cluster near 1.
- ✅ Park diagnostic no longer lies about softness on moving scenes.
- ✅ Moving AF is within ~±10 pulses ⇒ ≥96 % of peak sharpness. Good enough as it stands.
- ✅ Frozen-scene reference is the tool for any future AF accuracy claim (~1 min per pair).
- ⬜ **Park undershoots its target by 3–4 pulses, 7/7 observations, always short, never
  over** (`target -6 → arrived -10`, `-7 → -11`, `+15 → +11`, `+41 → +37`, `-122 → -126`,
  `-50 → -53`). A consistent directional error in the creep-up approach — the only clean
  systematic left, and free to correct.
- ⬜ **The parabola may be *adding* scatter on moving scenes.** Argmax spanned 11 pulses
  (+11, +7, +0) but post-fit targets spanned 22 (+15, +6, −7), the fit moving +4, −1, −7 —
  inconsistent in direction and size. On frozen runs it barely moves anything (+8→+12,
  +0→+2), as expected for a clean local shape. If it holds up, skipping the refinement when
  `handovers>0` halves the spread for a one-line change. **3 runs only — hypothesis.**
- ⬜ 8 of 20 object changes were re-anchors in one run (2 of 24 in another). Re-anchors
  apply no scale correction at all, leaving uncorrected steps in the displayed curve.
- ⬜ `min_texture_score` is used for two unrelated metrics (tile sharpness and intensity
  SD). Latent hazard, not an active bug.
- ✅ **OrthoSim GT CSV can silently freeze the display** (fixed). The default log path sits
  next to the `.tif` — on the Google Drive stream — and `flush()` threw `Errno 22` on every
  write for 12.5 h, i.e. through every run above. `_on_tick()` calls `self.update()` *after*
  the logger, so each failure skipped a repaint while `self._scroll` kept advancing: at a
  higher failure rate the screen would stop animating and a bench run would read it as a
  **frozen scene**, with no visible signal — which would quietly invalidate the §30.4
  method. `GroundTruthLogger.maybe_log` now swallows `OSError`, prints the path once and
  disables itself; the picture keeps moving. Pass `--log <local path>` if you need the CSV.
  Only the CSV was lost — the capture-side GT decode reads the optical bar, not this file,
  so the altitude/velocity readouts in the logs above are unaffected.

### 30.6 Lesson

A simulation justified a redesign; one hardware measurement killed it in a single run. The
simulated noise model was optimistic in exactly the way that mattered, and a *smooth* drift
model earlier had also given the opposite answer to a *random-walk* one drawn from the
measured ratios. When a bench measurement is an option, take it before writing the code.

---

## 31. Counts, park bias, and the field map (2026-08-11)

Continuing §30. Chasing the last systematic errors out of AF closed two of them, then ran
into one that AF cannot measure about itself — **best focus is not in the same place
everywhere in the frame** — which is what the field-map tool exists to quantify.

### 31.1 Units: positions are COUNTS, drive amounts are PULSES

Positions used to be carried in "pulse-equivalent units" (`counts / 24`), rounded to `int`
on every read. That threw away 24× of the encoder's resolution — up to **±12 counts of
quantisation noise** fed straight into the parabola fit — and made thresholds read tighter
than they were: `park_tolerance_pulses = 4` looks small and is really 96 counts.

The rule now, stated at the top of `focus_axis.py`: **anything measured, compared, or
thresholded is in encoder counts; only actual motor commands are in pulses.** A pulse is a
fixed-duration H-bridge burst whose size depends on drive strength (~24 counts at str0, ~34×
that at str3), so it is not a unit of distance. A count is, and it means the same thing at
every strength.

`FocusAxis.position` is now a float in counts, `pulses_for()` converts a wanted move into
whole pulses, and the count-denominated config fields (`dive_min_drop_counts`,
`park_stable_counts`, `park_slow_band_counts`, `park_climb_band_counts`,
`park_descend_margin_counts`) are scaled in `app.py` by the measured `af_counts_per_pulse`
so the physical distances the rig was tuned with are preserved. `*_pulses` fields are drive
amounts and stayed as they were.

### 31.2 Park bias — supersedes the open item in §30.5

The 3–4 pulse undershoot, 7/7 observations, was an **asymmetric exit condition**. The creep
only ever moves up, and it stopped at `pos >= best_pos - park_tolerance_pulses`, i.e. at the
*bottom edge* of a 96-count band. A one-directional approach turns a tolerance band into a
systematic offset of its full width. The exit is now centred on half a pulse (12 counts, the
finest move that exists), and `af2-park arrived` logs `miss=` so the error is visible rather
than inferred. `tools/test_af_handover.py` gained a check that fails under the old rule.

Removing it uncovered a second, smaller bias it had been masking: **post-arrival drain
drift** of +16…+81 counts (mean ~+36, about 1.5 pulses) — the shaft keeps moving after
"arrived" is logged. Still open, but it is now the largest remaining mechanical systematic
rather than the second largest.

### 31.3 Cross-run comparison needs `raw` — `tools/af_rebase_runs.py`

`EncoderAxis.reset()` runs on **every AF press**, so `best_pos` and park positions are
relative to wherever the lens happened to be when the button was hit. Comparing them across
runs is meaningless, and doing so had already produced wrong conclusions once. The only
position that survives a reset is `raw_count`, now logged in both the dive and park lines.
`tools/af_rebase_runs.py` parses a session log, rebases every run onto that common frame,
and reports start/argmax/fit/landed, the park miss, the drain, and the gap between one run's
landing and the next run's start.

### 31.4 The finding that motivated the tool

Rebasing the frozen-scene runs split the scatter cleanly in two:

| comparison | spread |
|---|---|
| static AF, repeated on the **same** tile | **1.6 pulses** |
| static AF, across **different** tiles | **7.8 pulses** |

The estimator is fine. What moves is *where in the frame* it looked. Moving AF rides patches
near y≈2000 in the coarse frame while the static runs picked tiles near y≈1000, so the
moving-vs-static difference was substantially a comparison between two regions of a non-flat
field — screen tilt, lens field curvature, or both. **Until that is measured, no AF accuracy
number that compares runs targeting different regions means anything.**

AF cannot answer this about itself: it scores exactly one tile for the whole climb (the
multi-tile scan happens once, during acquire, only to *choose* that tile).

### 31.5 The field map — `host/capture/field_map.py`

**Field map** button, next to Stop AF in the M4 panel. One dive-and-climb; five tiles pinned
at fixed sensor coordinates — centre plus the four mid-edges — scored on **every** frame.

Why one sweep rather than one AF run per region: every AF run re-zeros the encoder, re-seats
the lash, parks, and lands minutes apart, and all of that is difference that is not optics.
Tiles scored from the same held frame at the same encoder position share their positions,
their frames, and their mechanical state exactly, so what is left between their vertices is
optics. It is also ~40 s instead of ~20 minutes.

- **Centre + mid-edges, not corners.** Tilt is linear (top and bottom fall on opposite sides
  of centre); curvature is symmetric (both move the same way). Corners confound them.
- **Full-res, not the coarse tap.** Coarse aliases away the high frequencies focus lives in
  and peaks in the wrong place. `set_preview_rois` already crops an arbitrary list of ROIs
  from one held slot (built for the two-object preview, never limited to two), so this
  needed **no pipeline changes** — and reuses `focus_measure_full_res` and `_fit_peak`.
- **Tiles are 600 px full-res**, ~3.6 MB/frame for five. AF-sized ROIs (~2820×2004) would
  copy 56 MB/frame.
- **It never parks.** Dive ~3400 counts, climb 260 str0 pulses (~6240 counts) sampling every
  ~48, then a **paced return to the start** so a field map costs nothing to run — a single
  absolute `move_to` of that size is what used to yank the lens around on AF stop, so the
  return steps down 6 pulses/frame like everything else. The whole park-bias question stays
  out of the measurement.
- **The climb does not stop at the first peak.** Tiles crest at different positions; halting
  on the earliest would truncate exactly the curves being compared.
- Reports each tile's vertex in counts, its offset from centre, and the spread; writes every
  sample to `Iron661_fieldmap_<stamp>.csv` in the session-log folder (one write at the end,
  not a flush per row — see the OrthoSim `Errno 22` lesson in §30.5). Stop AF aborts and
  keeps what it collected.

⚠️ **Freeze the scene first.** The tiles are pinned to sensor pixels, so the content under
them must hold still. No loss: field curvature belongs to the optics, not the picture.

`tools/test_field_map.py` gives five synthetic tiles their own true focus positions and
checks the recovery. A genuinely flat field reports a **0-count** spread — so any spread
seen on the rig is real and not the tool's own noise — and an 800-count tilt reads back as
789. Also checks Bayer alignment of the ROIs, that an abort keeps its samples, and that the
sweep ends where it started.

### 31.6 Next

Run one sweep on the frozen bench. Expect a spread near the ~7.8 pulses (~190 counts) the
across-tile AF scatter implies; substantially more would mean the moving-vs-static
discrepancy of §30.4 was mostly geometry rather than the estimator, and that AF's tile
choice — not its peak finder — is the thing worth constraining.

→ Answered in **§32**. The spread is real and geometric, but the early sweeps that measured
it were wrong in five different ways; §32.1 is the list.

## 32. Making the field map trustworthy — and what it measured (2026-08-12)

The tool of §31 worked on the first try and gave a wrong answer with a straight face, five
separate times, before it gave a right one. Every failure produced a *plausible, tight*
number. That is the theme worth carrying forward: on this rig, a diagnostic that cannot
refuse is worse than no diagnostic.

### 32.1 Five confident wrong answers, and what each needed

**1. Tiles off the screen.** Tiles were laid out by frame fraction. The screen fills 96% of
frame width but only ~76% of height, so the bottom tile sat on the wall behind the display —
metres away, focusing somewhere else entirely, and reported as "vertical tilt +223c". The
operator spotted it on the preview before the numbers did.

`five_tile_rois` now takes a `region` and lays the tiles inside it. `MainWindow._field_map_region`
finds that region from the ArUco fiducials (largest axis-aligned box inside the quad), which
on this rig reads **92% × 67% of the frame** — and the tiles are drawn on the coarse preview
before the sweep so the operator can see where they landed.

**2. …and then the fiducials were never consulted.** The first "bounded" sweep silently fell
back to the whole frame. `detect_fiducials` needs only the ArUco *dictionary name*, but the
code required the whole `ScreenLayout`, which only exists once the OrthoSim decode is armed.
Toggle off → search skipped → inset 0.00 → identical tiles to before. Now it falls back to
`ScreenLayout.aruco_dict` and always looks.

**3. Refusing was right, then wrong.** With no fiducials and no interior inset, the sweep now
**refuses**: an unbounded sweep measures the wall and calls it lens tilt. But ten seconds
after a successful sweep it refused again, because one marker had dropped out. A bare `None`
from `detect_fiducials` cannot tell a blank display from an obscured corner, so it now takes
an `ids_out` list, and the field map **reuses the last box measured this session** when
markers are momentarily lost, warning that this holds only if nothing has moved.

**4. An abort stranded the lens, and the next sweep clipped.** `stop()` halted the motor where
it stood. Each abort left the lens partway down the dive; the next sweep zeroed *there*. The
start position walked `raw=0 → 325 → 1465 → 4770`, and since dive and climb are fixed lengths
sized to start near focus, the third sweep ran out **158 counts short** of the peak. Every
tile's argmax landed on the last sample, four of them on the *same* last sample, reporting
`SPREAD 20c (0.8 pulses)` — the best-looking result of the day and pure artifact.

Two fixes. An abort now **walks the lens home before reporting** (bidirectionally — stopping
during the dive leaves it *below* the start, and the old descend-only return called that
"arrived"). And a peak sitting on either end of the swept range is **clipped**: flagged,
excluded, and the sweep prints `SWEEP DID NOT BRACKET FOCUS` instead of a spread. Clipping is
only tested when the tile has real contrast, so a blank tile still gets the honest "no hump".

**5. Test data in the production log folder.** `FieldMapSweep(csv_dir=None)` meant "the real
session-log folder", and a test that forgot the argument dropped six synthetic sweeps in among
the rig data — where `fieldmap_report.py` cheerfully averaged them in (one with a 2c spread,
one with 3577c). `csv_dir` is now a **required** argument; `app.py` passes `default_log_dir()`
explicitly. Synthetic files are identifiable by their 192 px test tiles versus the rig's 600 px.

### 32.2 The fit window was the precision limit — 3 → 20

`fit_window=3` fits seven points across the flattest part of the hump, which is exactly where
the curve says least about its own centre. The peak here is ~1400 counts wide at half max,
about 21 samples, and `log(Gaussian)` is a parabola over that whole span, so the flanks were
being thrown away for nothing.

Checked offline against two good sweeps: at window 3 their tilt/curvature terms disagreed by
**600c** in total and 3 of 10 fits failed outright; from window **15 to 30** the disagreement
plateaus near **135c** with no failures. A broad plateau rather than a tuned point, so 20 —
roughly the half-width — is the honest middle. This single change is what turned an incoherent
measurement into a coherent one: at window 3 the horizontal curvature term flipped sign
between runs, so that signal simply was not visible.

### 32.3 Trust checks, and why the obvious one was backwards

A tile is trusted only if it has a real hump (`contrast ≥ 5`), was bracketed (not clipped), and
its vertex sits near a **model-free** peak locator.

The first version compared the vertex to the **argmax** — reasonable at window 3, backwards at
window 20, where the fit is *more* reliable than a noisy argmax on a broad flat top. It now
compares against the **midpoint of the above-half-max span**, which averages both flanks.
Measured on real sweeps, the wide-window vertex sits within **9%** of the curve width of the
midpoint every time, while the argmax strays to 16%. Tolerance is 20% of the half-width.

The log also prints a second, fit-free **argmax spread** beside the fit spread. If the two
disagree badly, the model is doing the talking rather than the optics.

`tools/fieldmap_report.py` had its own copy of the peak fit that *clamped* an out-of-span
vertex where the engine *rejects* it, so the tool quietly contradicted the log it was
explaining — by 200c on one sweep. It now imports the engine's `_fit_peak`.

### 32.4 What the rig actually measures

Nine sweeps, three per frozen scene, plus four earlier ones (`--groups`, §32.5):

| term | value | meaning |
|---|---|---|
| horizontal tilt | **+165c** (~7 pulses) | screen yawed; right and left disagree by 330c |
| vertical tilt | +60c | small |
| horizontal curvature | +20…+110c | drifting, see §32.6 |
| vertical curvature | −70…0c | drifting, see §32.6 |
| total spread | ~350c | |

**The field is dominated by bench yaw, not by the lens.** Four clean scene groups spanning an
app restart *and* an OrthoSim restart give horizontal tilt of 175, 183, 168 and 146c — mean
**168c, scatter 16c**, well inside the instrument noise. For scale, the lens moves ~1400 counts
and still looks sharp, so the two sides of the screen disagree by about a quarter of that
window. This is an alignment error that does not exist in flight over flat ground.

⚠️ The 10:07/10:25 pair reads 267c and should **not** carry equal weight: those two sweeps were
17 minutes apart with aborts and manual focus jogging between them, so they were never a
controlled replicate pair.

`left` has the broadest curve in every single sweep (1512–2466c at half max, against 1100–1670
elsewhere) — genuinely softer there, consistent with being the far end of a yawed screen — and
it anchors the horizontal tilt, so that term carries the most uncertainty of the four.

### 32.5 Error budget — and the retraction of the "100c scene effect"

`py -3 tools/fieldmap_report.py --stats --groups=3,3,3` splits the scatter into a pooled
within-scene component (deviations from each scene's *own* mean, so a genuine scene shift
cannot masquerade as instrument noise) and a between-scene component with the instrument's
share `within²/n` subtracted off.

| term | within-scene (instrument) | scene effect |
|---|---|---|
| horizontal tilt | ±38c | not detectable |
| vertical tilt | ±25c | not detectable |
| horizontal curvature | ±35c | ~42c |
| vertical curvature | ±24c | ~27c |
| spread | ±57c | not detectable |

**The tool repeats to about ±40c — under two pulses.** Scene content does not measurably move
either tilt term and moves curvature by ~30c.

⚠️ **This retracts the estimate made earlier the same day.** From two runs per scene it looked
like content shifted the answer by ~100c. With n=2, noise alone manufactures apparent scene
differences of exactly that size — the two-run comparison cannot separate them, and did not.
The correction matters well beyond this tool: a 100c content floor would have capped what *any*
contrast-detection AF could achieve at ~4 pulses. The real figure is nearer 30c, one pulse, so
the ceiling on AF accuracy is much higher than §30/§31 assumed.

**Grouping must be explicit.** Sweeps here run 39–50 s apart and refreezing a scene takes about
as long as pausing between repeats, so a gap-based rule silently merges or splits scenes and
returns a confident wrong variance. `--groups=3,3,3` is given by the operator; `--gap` remains
only as a guess, and says so.

### 32.6 Open items

- **Scene and time are confounded.** Both curvature terms drift monotonically across the three
  scenes — horizontal +19 → +85 → +109, vertical −66 → −37 → −5 — over six minutes. Two
  independent terms moving in step and in order looks more like drift (thermal, or the mount
  settling) than scene content. The design ran scenes in sequential blocks, so the two cannot
  be separated. **Interleave A/B/A/B** to settle it. Until then the ~30c attributed to content
  in §32.5 may be neither content nor real.
- Curvature is the term that would follow the camera into flight, so it is the one worth
  pinning down; tilt is a bench artifact.
- **Shimming the yaw** is well justified: at 165c against ±38c noise, removing even a third of
  it will show clearly. Measure before and after **within one session and one frozen scene** —
  comparing across scenes costs three times the precision.
- AF's own `fit_window` was left alone. The field map's climb is long and evenly sampled; AF's
  is short and adaptive, so the window finding here does not transfer without its own check.

### 32.7 Lesson

Every one of the five failures in §32.1 produced a number that looked *better* than the truth —
tighter spreads, cleaner curves, fewer flagged tiles. A measurement that cannot say "I could
not measure this" will always be beaten by one that can, because the failure modes are quiet
and the wrong answers are the pretty ones.
