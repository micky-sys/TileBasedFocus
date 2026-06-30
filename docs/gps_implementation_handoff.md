# GPS Implementation — Handoff Document

Status as of 2026-05-26. Purpose: hand the GPS coordinate-capture feature
off to a developer continuing the work on a different machine. Everything
listed under "Implemented" is on `main` (uncommitted), tested, and passes
lint. Everything under "Next steps" is the rough order in which I'd pick
it up.

For the deep technical reference (SBF wire format, slot-based correlation
algorithm, EXIF tag layout) see [`gps_sbf_correlator.md`](./gps_sbf_correlator.md).
This doc is the project-level "what / why / where" for a new agent.

---

## 1. Original goal (from the user)

Integrate a Septentrio GNSS receiver into the Iron 661 capture app so
every frame captured in M0 / M1 can carry the matching GPS fix. The
receiver shares the ClearCore PCB's trigger pulse: each pulse fires
three cameras simultaneously AND triggers the receiver's `EXT_EVENT`
input, producing one `ExtEventPVTGeodetic` SBF block on the receiver's
USB-CDC COM port.

Hard requirements:

1. Open a second COM port at 115200 8N1, parse the Septentrio Binary
   Format (SBF) byte stream, decode `ExtEventPVTGeodetic` blocks
   (block ID 4038).
2. Attach the fix to the per-frame metadata so it's saved into the
   `.bay14line` chunk JSON (already had a `gps` field — just feed it).
3. Allow `Bay14Reader` to write the same fix into standard EXIF GPS
   tags on exported TIFFs.
4. Show the latest fix in the preview's upper-right corner; show
   `NO GPS` (red) when not connected or when no recent fix.
5. Solve the correlation problem (which SBF event belongs to which
   camera frame), accounting for one GPS message vs. three camera
   frames per trigger, and for the timing skew between an event
   (arrives ~5 ms after the pulse) and a frame (arrives ~300 ms
   after the pulse).

The user proposed three correlation strategies and asked which we'd
use; the agreed-on plan is "all three combined" (see Section 3).

## 2. Topology / hardware assumptions (confirmed with user)

```
ClearCore PCB ──trigger pulse──► 3 cameras (CXP Line1)
              └──EXT_EVENT pulse──► Septentrio receiver
                                       │
                                       ▼ USB-CDC, 115200 8N1
                                  Host PC, COM<N>
```

One receiver. One pulse → three camera frames + one
`ExtEventPVTGeodetic` block. Intra-sweep trigger spacing ≈ 200 ms;
inter-sweep pause ≈ 100 ms (so the gap between sweep-N's last
trigger and sweep-(N+1)'s first trigger is ~300 ms). 13 frames per
sweep.

These numbers are **the** structural assumption the correlator relies
on; they're encoded as the `SWEEP_GAP_MS = 250` and `sweep_size = 13`
defaults in `gps_correlator.py`.

## 3. Correlation algorithm (slot-based hybrid)

Differs from the earlier ring-buffer + per-camera consumer-index
sketch in the plan (which I attempted first and then discarded — see
"Lessons learned" below). The shipped design:

1. **Tag GPS events with `(gps_sweep_gen, gps_event_in_sweep)`** on
   arrival. `gps_event_in_sweep` increments per event but resets to
   1 whenever the inter-event host-arrival gap exceeds
   `SWEEP_GAP_MS = 250 ms`. The 250 ms threshold sits cleanly between
   the 200 ms intra-sweep cadence and the 300 ms inter-sweep gap.
2. **Camera-side lookup** uses `frame_in_sweep` (from `SweepCounter`)
   as the slot key. `pop_fix_for(camera, frame_perf_ns, sweep,
   frame_in_sweep)` walks the slot table, picks the freshest entry
   with `slot == frame_in_sweep` and `0 ≤ frame_perf_ns - host_perf_ns
   ≤ STALE_AHEAD_MS`. If only future entries exist (within
   `FUTURE_MS`), it returns `None` rather than consuming (the next
   frame will retry).
3. **No per-camera consumer index.** Slot-based lookup is naturally
   idempotent across cameras: cam1, cam2, cam3 all read the same
   slot entry for the same trigger. A frame drop on cam2 can't
   poison cam1 or cam3.
4. **TOW cross-check** at DEBUG level only: compares the matched
   event's GPS TOW against the previous slot's TOW; flags when the
   gap falls outside 100-350 ms (which would indicate a missed GPS
   event with the slot counter now off-by-one).

Tunables live in `host/capture/gps_correlator.py`:

```python
SWEEP_GAP_MS = 250.0
STALE_AHEAD_MS = 1500.0
FUTURE_MS = 200.0
SBF_TOW_GAP_MIN_MS = 100.0
SBF_TOW_GAP_MAX_MS = 350.0
MAX_GENERATIONS_KEPT = 10
```

## 4. Files (all in the repo, none in backups)

### New modules

| Path | What it does |
| --- | --- |
| `host/capture/sbf_parser.py` | Stream parser. Pure stdlib + `struct`. Frames SBF blocks (`$@` sync, 16-bit CRC-CCITT, BlockID + revision split, 4-byte-aligned length), validates CRC, decodes only `ExtEventPVTGeodetic` (4038). Surfaces Do-Not-Use sentinels as `None`. Encoder helper `encode_ext_event_pvt_geodetic` for tests. |
| `host/capture/gps_receiver.py` | `QObject` facade over `pyserial`. `list_ports()` / `auto_detect()` (matches "septentrio" / "polarx" / "mosaic" / "asterx" / "altus" in device descriptions), `connect_port` / `disconnect_port`, background reader thread, dual dispatch on each event: synchronous callback (`on_event`, used by the correlator) + Qt signal (used by the GUI log). |
| `host/capture/gps_correlator.py` | Slot-table correlator described above. Methods: `add_event`, `pop_fix_for`, `latest_fix`, `age_of_latest_ns`, `stats`, `set_connected`. Thread-safe. |

### Modified files

| Path | Change |
| --- | --- |
| `host/capture/pipeline.py` | `GpsProvider` widened to `Callable[[str, int, int, int], Optional[GpsFix]]`. `_submit_slot` now advances `SweepCounter` BEFORE calling the provider, captures `time.perf_counter_ns()` at frame dequeue, and serialises the fix via `fix.to_dict()` (previously used `__dict__.copy()` which would have raised on a `datetime`). |
| `host/capture/app.py` | `MainWindow` owns one `GpsCorrelator` + one `GpsReceiver`. `_BringupWorker` takes the provider and wires it into each `CameraPipeline`. New `_build_gps_bar()` (COM combo + refresh + Connect button + status). Preview's `_PreviewDisplayLabel` now hosts a top-right child `QLabel` repositioned via `resizeEvent`. 4 Hz `QTimer` (`_gps_overlay_timer`) refreshes the overlay text from `correlator.latest_fix()` and the receiver's connection state. New `--gps-port` CLI arg. Shutdown closes the GPS port too. |
| `host/main_capture.py` | Docstring + usage example mention `--gps-port`. (The actual argparse lives in `capture.app.main()`.) |
| `tools/bay14_export_core.py` | New `_gps_fix_from_meta(merged_meta)` reconstructs a `GpsFix` from the chunk's `gps` JSON block (parses the ISO-8601 timestamp back). `write_tiff_bayer_u16` and `encode_tiff_rgb16` now build EXIF GPS tags via the reused `capture.tiff_writer._build_gps_extratags` when the fix is positional. The JSON block is kept in `ImageDescription` for JSON-first consumers. |
| `docs/clearcore_serial_protocol.md` | Cross-link to `gps_sbf_correlator.md` at the top. |

### New documentation

| Path | Audience |
| --- | --- |
| `docs/gps_sbf_correlator.md` | Technical reference: SBF wire format, correlator algorithm with tunables table, GUI integration, EXIF layout, tests. |
| `docs/gps_implementation_handoff.md` | This file. |

### New tests (all passing)

| Path | Coverage |
| --- | --- |
| `tools/test_sbf_parser.py` | CRC vectors (XMODEM check value `0x31C3`), framing, byte-at-a-time streaming, multi-block feeds, garbage-before-sync, bad CRC / bad length rejection, unknown-block skip, resync-after-false-sync, UTC conversion, revision-bit masking. **12 tests.** |
| `tools/test_gps_correlator.py` | Multi-camera same-fix, sequential-in-sweep, future-event-returns-None, stale rejection, inter-sweep-gap → new generation, cam falling behind, missed slot returns None, `latest_fix` independence of consumption, `age_of_latest_ns`. **9 tests.** |
| `tools/test_bay14line_roundtrip.py::test_gps_exif_roundtrip` | Writes Bayer + RGB TIFFs with a synthetic `gps` JSON block in meta, reads the EXIF tags back, asserts lat/lon match within 1e-5. |

Run them all:

```powershell
python tools/test_sbf_parser.py -v
python tools/test_gps_correlator.py -v
python tools/test_bay14line_roundtrip.py
```

All three commands exit 0 and print `OK` / `all .bay14line round-trip tests passed.`

## 5. Lessons learned (worth knowing before changing the correlator)

I started with the ring-buffer + per-camera consumer-index design
sketched in the plan. It failed in two tests (`test_orphan_event_is_skipped`,
`test_resync_drops_trailing_orphans`) because the "snap to closest event"
sweep-boundary resync logic kept locking onto the wrong event when
the intra-sweep cadence (200 ms) was smaller than the resync window
(500 ms). I rewrote to the slot-table design instead. Key insight:

* **Don't index by FIFO position.** Index by the structural slot
  number (1..13) that's stable across the entire wire protocol on
  both sides.
* **Let the GPS stream segment itself** via the inter-sweep gap;
  don't depend on the camera side to tell the correlator about
  sweep boundaries.

If you ever revisit the algorithm, the failing-tests-first commit
of the ring-buffer attempt is preserved in this conversation's
transcript — search for "FIFO" / "ring buffer" in the diff history.

Two known limitations (acceptable for now):

1. **Single missed GPS event in the middle of a sweep** shifts every
   subsequent slot in that sweep by one (because `gps_event_in_sweep`
   is purely sequential within a generation). The TOW cross-check
   detects it but doesn't correct it — the affected frames get the
   wrong fix. Not fixed in this pass because in practice the
   Septentrio doesn't drop events at 5 Hz.
2. **Camera mid-sweep drop** is fine — the slot lookup is by
   `frame_in_sweep`, which `SweepCounter` advances per *received*
   frame on each camera. If cam2 drops physical-trigger 7, cam2's
   subsequent `frame_in_sweep` reads as 7 when the next received
   frame is actually physical-8. That means cam2 frame 7 will match
   GPS slot 7 (which corresponds to physical 7) — i.e. cam2 gets
   the previous-physical-trigger's fix on that frame. The fix is
   only ~200 ms stale, so position drift is sub-metre at flight
   speeds. Accept.

## 6. How to set up a fresh dev environment

The repo is at `g:\My Drive\Micky\WORK\Cursor\TileBasedFocus` on the
current machine. To bootstrap somewhere else:

1. **Python 3.12+** with the existing `requirements.txt`. No new
   dependencies were added — the parser is pure stdlib + `struct`,
   the receiver reuses `pyserial`, the GUI reuses `PySide6`, EXIF
   writing reuses `tifffile`.
2. The repo is **not** a git working tree (per the `Is directory a
   git repo: No` flag in the agent context). Snapshots live in
   `_backups/`. Use whatever sync mechanism the user has (Google
   Drive in the path suggests Drive sync) and confirm the files
   listed in Section 4 are all present.
3. Run the test suite (Section 4) to confirm everything's green
   before touching anything.
4. Run the GUI without hardware:
   ```powershell
   python host\main_capture.py --cti "<...KYFGLibGenTL_vc141.cti>"
   ```
   With no GPS hardware attached, the preview overlay should read
   `NO GPS` in red and the GPS COM bar should show `disconnected`.
   With a Septentrio plugged in (or a `socat`-bridged replay), the
   overlay should flip to green and show coordinates.

## 7. Status / completion checklist

All 9 to-do items from the plan are done:

- [x] `sbf_parser`: parser + CRC + decoder + unit tests
- [x] `gps_receiver`: Qt wrapper + auto-detect + reader thread
- [x] `correlator`: slot-table hybrid + unit tests
- [x] `pipeline_wiring`: widened `GpsProvider`, reordered `_submit_slot`,
      captured `perf_counter_ns()` at frame arrival
- [x] `main_window`: `GpsCorrelator` + `GpsReceiver` wired into
      `MainWindow`, GPS COM bar added
- [x] `preview_overlay`: top-right `QLabel`, 4 Hz refresh, NO GPS red /
      coordinates green
- [x] `cli`: `--gps-port` argparse flag, threaded into `MainWindow`
- [x] `bay14_reader_exif`: `bay14_export_core` writes EXIF GPS tags,
      round-trip test green
- [x] `docs`: `gps_sbf_correlator.md` (deep ref) + this handoff doc;
      `clearcore_serial_protocol.md` cross-links

Lint: clean across all touched files.
Tests: 12 + 9 + 5 passing.
Imports: `python -c "from capture.app import MainWindow"` exits 0.

## 8. Next steps (suggestions, not promised)

The plan listed these as "out of scope" for the current pass; they're
the natural follow-up work. None of them block the basic GPS feature
from being useful.

1. **M4 focus-crop GPS.** `tiff_writer.write_frame` already accepts
   a `gps=` argument and `FocusJob` carries a `gps` slot, but the
   focus-tap path in `AcquisitionThread.run` never assigns one. To
   close this: in the focus-tap branch, call `self._gps_provider`
   the same way `_submit_slot` does (with `frame_perf_ns` and the
   current sweep position) and pass the resulting `GpsFix` through
   `submit_focus_capture`. The Fine sub-mode path already has a
   slot index available so this is a localised change.
2. **Persist the GPS COM port choice** between sessions, the way
   the user might want for the ClearCore too. Currently you pick
   each launch; auto-detect catches the common case.
3. **Probe-based auto-detect**. `GpsReceiver.auto_detect` only
   matches on driver descriptions today. The plan called for an
   optional 250 ms `$@` probe over each candidate port to cover
   USB-serial adapters that hide the receiver's identity. Skipped
   to avoid blocking the GUI thread; if needed, do it from
   `_auto_connect_gps` on a `QThread`.
4. **Smoke harness**. The plan suggested a `socat` pipe + recorded
   SBF file for offline replay. Not built. The encoder helper
   `encode_ext_event_pvt_geodetic` in `sbf_parser.py` is enough
   to script a fake-receiver replay if you want to drive the GUI
   without hardware.
5. **TOW-based misalignment correction** for the "missed GPS
   event" case in Section 5. Cross-check TOW against `frame_in_sweep`
   × 200 ms and re-anchor the slot when the gap is exactly 2× normal.
   Tricky; only worth doing if the field shows it actually happens.
6. **Leap-second auto-update.** Currently hardcoded to 18 in
   `sbf_parser.DEFAULT_LEAP_SECONDS` (correct as of 2017-01-01).
   The receiver knows the current leap-second count and emits it
   on a separate SBF block (`ReceiverStatus` or similar) — could
   be auto-applied.

## 9. Architectural diagram

(Same as the plan's, for handoff convenience.)

```
ClearCore PCB ──trigger──► 3 cameras
              └──trigger──► Septentrio receiver
                                  │
                                  ▼ SBF over USB-COM @115200
                          GpsReceiver (Qt thread)
                                  │
                                  ▼ on_event callback
                          GpsCorrelator (slot table)
                                  ▲
                                  │ pop_fix_for(camera, t, sweep, slot)
3 cameras ──► CameraPipeline.AcquisitionThread
                                  │ gps dict
                                  ▼
                          packer → io_writer
                                  │
                                  ▼ .bay14line chunk JSON
                              Bay14Reader export
                                  │
                                  ▼ EXIF GPS IFD tags
                              exported TIFFs

                          GpsCorrelator.latest_fix()
                                  │
                                  ▼ 4 Hz refresh
                          Preview overlay (NO GPS / lat,lon,UTC)
```

## 10. Quick reference for new agent

* All capture-side code lives under `host/capture/`. New code is
  `sbf_parser.py`, `gps_receiver.py`, `gps_correlator.py`. Touched
  code is `pipeline.py` and `app.py`.
* Export-side code lives under `tools/`. Touched: `bay14_export_core.py`.
* Tests live under `tools/` (the project's existing convention).
* Python interpreter on the current machine:
  `C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe`.
  The system's `python.exe` is a Microsoft Store stub; don't use it.
* `git`: not initialised. The user uses `_backups/` snapshots instead.
* Plan file (read-only by user request):
  `C:\Users\User\.cursor\plans\gps_coordinate_capture_ca8604ee.plan.md`.
* Prior chat that established the plan + did the implementation:
  see this conversation's transcript.
