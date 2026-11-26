# XPL binary format v1

All integers in record headers and payload bookkeeping are little endian.
Raw 16-byte ICM FIFO packets remain **big endian**. No padding occurs on disk.

Every record consists of a 24-byte header followed by 0–400 payload bytes:

```
offset 0:  uint32 magic = 0x31525058 (ASCII XPR1)
offset 4:  uint16 payload_length
offset 6:  uint8  type
offset 7:  uint8  source
offset 8:  uint32 record_sequence (increments across all segments in one session)
offset 12: uint64 mcu_boot_microseconds
offset 20: uint32 CRC32
offset 24: payload
```

CRC is standard reflected IEEE CRC-32, polynomial 0xEDB88320, initial state
0xffffffff, final inversion. It covers header bytes 0–19 then the payload;
the CRC field is excluded. Equivalent to Python `zlib.crc32(payload,
zlib.crc32(header[:20]))`.

Types:

- **1 META**: UTF-8 JSON. Source 255 is session configuration; 0–3 describe the
  corresponding sensor. Other source IDs may describe board/input configuration.
- **2 FIFO**: source 0–3 = sensor 1–4. Payload starts with `uint32 first_sample_index`
  and `uint32 packet_count`, followed by that many 16-byte FIFO packets. Header time
  is the start of the FIFO service, **not** the sample time. Each sensor's index
  advances for every acquired packet even while an armed window is not being saved.
- **3 SYNC**: source 0–3. Payload is `uint32 marker_id`, `uint32 edge_bracket_us`,
  `uint32 next_sample_index_hint`. Header time is just before asserting this sensor's
  FSYNC GPIO; the physical edge is bounded by that time and bracket duration.
  Hint is the number of samples already read, not the exact flagged sample index.
  The following FSYNC-tagged packet identifies the corresponding sample.
- **4 EDGE**: source 0 = trigger; 1, 2, 3 = camera strobes. Payload first byte is raw
  GPIO level. A second byte of 1 denotes an initial level snapshot, not a captured
  edge. Timestamp is GPIO ISR time (or session start for the initial snapshot).
- **5 GPS**: raw ASCII NMEA without CR/LF, up to 400 bytes per fragment. Source is
  fragment index from zero. Equal timestamps identify one sentence. Retain original
  NMEA checksum/fix-status fields; the firmware does not assert GPS validity.
- **6 BURST**: JSON markers for begin, end, merged trigger and stop_truncated.
  Retained batches can extend beyond requested boundaries. Filter by derived sample
  times during analysis; preserve pre/post margins for filter-delay studies.
- **7 END**: JSON `{"continuation":true}` at a segment boundary; `{"clean":true}`
  for acquisition ending without a known fault; `{"clean":false}` on a known fault.
  A successful STOP acknowledgment additionally confirms SD sync/close succeeded.
- **8 STATS**: JSON. Source 255 contains overall maxima/counters/fault; source 0–3
  contains each sensor's cumulative samples_read, invalid, saturated, FSYNC count
  and maximum FIFO occupancy. Periodic snapshots can be slightly non-simultaneous;
  final statistics are taken after acquisition stops.

Record order is storage order, not strict time order: delayed FIFO readout, camera
events and retained prehistory can have earlier timestamps than preceding records.
Sort by appropriate derived/event time for plotting. Duplicate sensor indices can
appear when saved prehistory overlaps a previous capture; the decoder removes those
duplicate CSV rows. Gaps in index are intentional between triggered windows.

FIFO packet bytes: 0 header; 1–6 accel XYZ int16; 7–12 gyro XYZ int16; 13 signed
temperature; 14–15 timestamp uint16. Header bits 3:2 = 3 indicate FSYNC elapsed time;
value 2 indicates ordinary sensor timestamp. Acceleration scale = counts/2048 g;
gyro scale = counts*2000/32768 dps; temperature = signed_count/2.07 + 25 °C.
Axis count -32768 is invalid, not a physical negative-full-scale reading.
The decoder marks absolute values >=32700 as near saturation (a diagnostic heuristic).

Timestamps scale by 32/30 µs per count with the configured internal clock. The
decoder anchors flagged samples to the preceding FSYNC record and interpolates
between anchors no more than 30 ms apart. Outside those brackets it labels nominal
62.5 µs/sample extrapolation explicitly. Do not interpret sensor ticks as MCU ticks
or apply FSYNC-to-sample elapsed timestamps as an absolute running counter.

Partial/corrupt tails are reported; the decoder can export the valid prefix. It
does not attempt to invent missing records or conceal a missing final footer.
