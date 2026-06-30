# GPS (Septentrio SBF) capture path

Companion to `clearcore_serial_protocol.md`. The ClearCore PCB drives the
camera triggers; this document explains how the same trigger pulse is also
seen by a Septentrio receiver's EXT_EVENT input and how the host stitches
that event back to the matching camera frame.

## Wiring

```
ClearCore PCB ──trigger──► 3 cameras (CXP Line1)
              └─trigger──► Septentrio EXT_EVENT input
Receiver ── USB CDC (SBF) ──► Host PC, COM<N>, 115200 8N1
```

One pulse → three frames + one `ExtEventPVTGeodetic` SBF block. Every
camera-frame on the host has exactly one matching GPS event.

## Host modules

| File | Role |
| --- | --- |
| `host/capture/sbf_parser.py` | Stream parser for the SBF wire format. Hand-rolled CRC-CCITT + block framing. Only decodes `ExtEventPVTGeodetic` (block 4038); other blocks are counted and discarded. |
| `host/capture/gps_receiver.py` | Qt wrapper. Owns `pyserial` + a background reader thread. Pumps raw bytes into the parser and dispatches each decoded event into the correlator (synchronous) and the GUI (signal). |
| `host/capture/gps_correlator.py` | Tags events with `(gps_sweep_gen, gps_event_in_sweep)` based on inter-event gap, then answers `pop_fix_for(camera, frame_perf_ns, sweep, frame_in_sweep)` per-frame. |
| `host/capture/pipeline.py` | `AcquisitionThread._submit_slot` calls the correlator and attaches the fix's JSON-serialisable dict to the `WriteJob`. |
| `host/capture/tiff_writer.py` | Reused by the export path for `_build_gps_extratags`. |
| `tools/bay14_export_core.py` | Reconstructs `GpsFix` from `.bay14line` chunk metadata and writes the same EXIF GPS tags on TIFF export. |

## SBF wire format (subset)

Per the Septentrio SBF Reference Guide, section 4.1.1:

* Sync: `0x24 0x40` (`$@`).
* CRC: 16-bit CRC-CCITT (poly `0x1021`, seed `0`, no reflection, no
  final XOR) over the bytes from the ID field through the end of the
  block.
* ID: little-endian `u2`. Low 13 bits = block number; high 3 bits =
  revision. We mask the ID before comparing to `4038`
  (`ExtEventPVTGeodetic`).
* Length: little-endian `u2`. Total block size including the 8-byte
  header; always a multiple of 4.

The `ExtEventPVTGeodetic` body shares its layout with `PVTGeodetic`
(block 4007); we only unpack the leading 67 bytes (through `NrSV`).
Newer firmware revisions append more fields after that — SBF is
forward-compatible so we just ignore the tail.

`Do-Not-Use` markers: `0xFFFFFFFF` / `0xFFFF` / `0xFF` for unsigned
ints; `-2.0e10` exactly for `f4` and `f8`. The parser surfaces these
as Python `None` on the resulting `SbfExtEvent`.

## Correlation algorithm (hybrid)

The PCB schedules triggers like this:

```
...|trig 1|...|trig 2|...|trig 13|        sweep N
                              ...|trig 1|...   sweep N+1
   ^-- ~200 ms --^                ^-- ~300 ms gap (100 ms inter-sweep pause)
```

The GPS receiver sees the same pulse train, so the inter-event spacing
matches. The correlator exploits the structural gap to *segment* the
GPS event stream into "sweep generations" without depending on the
camera side at all:

1. **Counting (primary)**: each GPS event is tagged on arrival with
   `(gps_sweep_gen, gps_event_in_sweep)`. `gps_event_in_sweep`
   increments per event; it reaches 1 again the first time a > 250 ms
   inter-event gap is seen. The camera-side `frame_in_sweep` (from
   `SweepCounter`) is the lookup key into the slot table.
2. **Sweep-boundary resync** (via gap detection): the `SWEEP_GAP_MS =
   250 ms` threshold is the inter-sweep pause your strategy #3 was
   pointing at. Without it the slot counter would just wrap; with it
   the table is segmented and stale entries from the previous sweep
   never bleed into the next.
3. **Staleness skip**: a slot match older than `STALE_AHEAD_MS =
   1500 ms` relative to the camera frame is rejected. Strategy #1 from
   your original list — if no event landed close to the frame in time,
   we write the frame without a fix instead of pairing it with the
   previous sweep's leftover.
4. **Future tolerance**: a slot match more than `FUTURE_MS = 200 ms`
   ahead of the frame is also rejected. SBF latency over USB is ~5 ms
   so anything beyond 200 ms means we haven't seen the matching block
   yet; return `None` and retry on the next frame.
5. **SBF-TOW cross-check (diagnostic)**: if the matched event's GPS
   TOW differs from the previous slot's by less than 100 ms or more
   than 350 ms, a `[gps_corr] TOW gap out of expected window` line is
   logged at DEBUG level. Doesn't fail the match — it just flags that
   a single GPS event was probably missed and the slot table is now
   off-by-one.

Tunables (all in `gps_correlator.py`):

| Constant | Default | Meaning |
| --- | --- | --- |
| `SWEEP_GAP_MS` | 250 | Inter-event gap that flips `gps_sweep_gen` |
| `STALE_AHEAD_MS` | 1500 | Reject slot matches older than this vs frame |
| `FUTURE_MS` | 200 | Reject slot matches further ahead than this |
| `MAX_GENERATIONS_KEPT` | 10 | Slot-table generations kept in memory (~26 s) |

## GUI

`MainWindow` adds a second COM bar (matching the ClearCore one) for the
GPS receiver: COM combo + refresh + Connect button + status label.
Auto-detect favours device descriptions containing "Septentrio";
override with `--gps-port` on the command line.

The preview area carries a top-right overlay refreshed at 4 Hz from
`GpsCorrelator.latest_fix()`:

* No connection / no fresh fix → red `NO GPS`.
* Fresh fix → two-line green readout:

  ```
  lat 32.085300   lon 34.781800
  12:34:56.789 UTC  11 sats  alt    41.2 m
  ```

A fix is considered "fresh" when its host-arrival is less than 2 s old
(more than one full sweep cycle of headroom at 5 Hz).

## On-disk format

The `WriteJob` already had a `gps` slot; the parser/correlator simply
fill it now. The `.bay14line` chunk header JSON gains a `gps` block:

```json
{
  "...": "...",
  "gps": {
    "latitude_deg": 32.0853,
    "longitude_deg": 34.7818,
    "altitude_m": 41.2,
    "timestamp_utc": "2026-05-26T12:34:56.789+00:00",
    "satellites": 11,
    "hdop": null,
    "fix_quality": 1
  }
}
```

`Bay14Reader` (via `tools/bay14_export_core.py`) writes the same fix
into standard EXIF GPS IFD tags (`GPSLatitude`/`GPSLongitude`/
`GPSAltitude` etc.) so `exiftool`, image viewers, and GIS tools pick
up the fix without parsing `ImageDescription` JSON. The JSON `gps`
block is also kept in `ImageDescription` for tools that prefer it.

## Tests

* `tools/test_sbf_parser.py` — synthetic SBF frames (CRC, framing,
  resync, bad-CRC + bad-length rejection, future-revision blocks).
* `tools/test_gps_correlator.py` — multi-camera matching, future /
  stale rejection, inter-sweep gap → new generation, sat counters.
* `tools/test_bay14line_roundtrip.py::test_gps_exif_roundtrip` —
  round-trip GPS through `write_tiff_bayer_u16` and `encode_tiff_rgb16`,
  verifying the EXIF tags decode back to the original lat/lon.

A smoke run on the rig can use `socat` (or a Python serial bridge)
to replay a recorded SBF file into a loopback COM pair while the
GUI is connected to the other end of the pair.

## Out of scope (today)

* M4 focus-crop GPS: `tiff_writer` already accepts `gps=` but the
  focus path doesn't currently get a fix — wired separately when
  needed.
* Real antenna calibration / lever-arm corrections.
* Persisting the GPS COM port choice in user settings (mirrors the
  ClearCore COM bar; user picks each launch).
