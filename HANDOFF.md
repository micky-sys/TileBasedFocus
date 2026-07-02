# Iron 661 Capture — Handoff Notes

_Last updated: 2026-06-30 — project is now under Git (§13); Iron661Capture,
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
`improve_frac` (min relative improvement to count as better), `patience`,
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
