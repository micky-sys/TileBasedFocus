"""Decode X-Parallax XPL v1 logs using Python 3.10+ (standard library only).

Pass all segments of ONE session in order. --csv exports samples/events/GPS;
without it, scan and validate without creating multi-gigabyte CSV files.
Digital sample times are not corrected for sensor/filter latency.
"""
from __future__ import annotations
import argparse
import bisect
import csv
import glob
import json
from pathlib import Path
import statistics
import struct
import zlib

HEADER = struct.Struct("<IHBBIQI")
MAGIC = 0x31525058
TICK_US = 32 / 30


def frames(paths):
    expected = None
    for path in paths:
        with open(path, "rb") as f:
            while True:
                offset = f.tell()
                raw = f.read(24)
                if not raw:
                    break
                if len(raw) != 24:
                    raise ValueError(f"{path}:{offset}: truncated header")
                magic, size, kind, source, seq, time, crc = HEADER.unpack(raw)
                if magic != MAGIC or size > 400 or not 1 <= kind <= 8:
                    raise ValueError(f"{path}:{offset}: invalid header")
                if kind in (2,3,4) and source > 3:
                    raise ValueError(f"{path}:{offset}: invalid source")
                data = f.read(size)
                if len(data) != size:
                    raise ValueError(f"{path}:{offset}: truncated payload")
                if zlib.crc32(data, zlib.crc32(raw[:20])) != crc:
                    raise ValueError(f"{path}:{offset}: CRC mismatch")
                if expected is not None and seq != expected:
                    raise ValueError(f"{path}:{offset}: record sequence {seq}, expected {expected}")
                if expected is None and seq != 0:
                    raise ValueError("First record is not sequence zero; supply all session segments")
                expected = (seq + 1) & 0xffffffff
                yield kind, source, time, data


def packets(data):
    if len(data) < 8:
        raise ValueError("Short FIFO record")
    index, count = struct.unpack_from("<II", data)
    if count == 0 or len(data) != 8 + count * 16:
        raise ValueError("Invalid FIFO record length")
    for j in range(count):
        p = data[8 + j * 16:24 + j * 16]
        if p[0] & 0xf0 != 0x60 or p[0] & 8 == 0:
            raise ValueError("Invalid ICM FIFO header")
        axes = struct.unpack_from(">6h", p, 1)
        yield index + j, p[0], axes, struct.unpack_from("b", p, 13)[0], struct.unpack_from(">H", p, 14)[0]


def scan(paths):
    report = {"files": [str(p) for p in paths], "records": 0, "metadata": [],
              "errors": [], "warnings": [], "stats": {}, "clean_end": False,
              "timing_note": "FSYNC-derived digital sample times; analog/filter delay NOT calibrated"}
    sensors = [{"samples": 0, "excluded_or_missing": 0, "overlap_samples": 0,
                "invalid": 0, "saturated": 0, "timestamp_jumps": 0,
                "unmatched_fsync": 0, "anchors": []} for _ in range(4)]
    high = [-1] * 4
    prev = [None] * 4
    pending = [None] * 4
    marker_high = [0] * 4
    mode = None
    session = None
    last_kind = None
    expected_segment = 0
    try:
        for kind, source, time, data in frames(paths):
            report["records"] += 1
            last_kind = kind
            if kind == 1:
                meta = json.loads(data)
                report["metadata"].append(meta)
                if source == 255:
                    if session is not None and meta["session"] != session:
                        raise ValueError("Mixed sessions")
                    if meta["segment"] != expected_segment:
                        raise ValueError("Missing/out-of-order segment metadata")
                    expected_segment += 1
                    session = meta["session"]
                    mode = meta["mode"]
            elif kind == 3:
                if source > 3 or len(data) != 12:
                    raise ValueError("Invalid FSYNC record")
                marker, uncertainty, hint = struct.unpack("<III", data)
                if marker > marker_high[source]:
                    if pending[source] is not None:
                        report["warnings"].append(f"Sensor {source+1}: FSYNC marker without stored tagged sample")
                    pending[source] = (time + uncertainty / 2, hint)
                    marker_high[source] = marker
            elif kind == 2:
                if source > 3:
                    raise ValueError("Invalid sensor ID")
                s = sensors[source]
                for index, header, axes, temp, stamp in packets(data):
                    if index <= high[source]:
                        s["overlap_samples"] += 1
                        continue
                    if high[source] >= 0 and index != high[source] + 1:
                        s["excluded_or_missing"] += index - high[source] - 1
                        prev[source] = None
                    elif high[source] < 0 and mode == "continuous" and index:
                        s["excluded_or_missing"] += index
                    high[source] = index
                    s["samples"] += 1
                    s["invalid"] += any(v == -32768 for v in axes)
                    s["saturated"] += any(abs(v) >= 32700 for v in axes if v != -32768)
                    if header & 0x0c == 0x0c:
                        marker = pending[source]
                        if marker is not None and index >= marker[1] and stamp*TICK_US <= 200:
                            s["anchors"].append((index, marker[0]+stamp*TICK_US))
                            pending[source] = None
                        else:
                            s["unmatched_fsync"] += 1
                        prev[source] = None
                    else:
                        if prev[source] is not None:
                            dt = ((stamp-prev[source]) & 65535)*TICK_US
                            if not 45 <= dt <= 80:
                                s["timestamp_jumps"] += 1
                        prev[source] = stamp
            elif kind == 7:
                report["clean_end"] = json.loads(data).get("clean", False)
            elif kind == 8:
                report["stats"][str(source)] = json.loads(data)
    except (ValueError, KeyError, struct.error) as exc:
        report["errors"].append(str(exc))
    report["clean_end"] = report["clean_end"] and last_kind == 7
    if not report["clean_end"]:
        report["errors"].append("No clean final footer: power loss, missing segment, or acquisition/storage failure")
    for i, s in enumerate(sensors):
        anchors = s["anchors"]
        rates = [(b[0]-a[0])*1e6/(b[1]-a[1]) for a,b in zip(anchors, anchors[1:])
                 if 0 < b[1]-a[1] <= 30000 and b[0] > a[0]]
        s["anchor_count"] = len(anchors)
        s["measured_rate_hz"] = statistics.median(rates) if rates else None
        if mode == "continuous" and s["excluded_or_missing"]:
            report["errors"].append(f"Sensor {i+1}: missing continuous samples")
        final_count = report["stats"].get(str(i), {}).get("samples_read")
        if mode == "continuous" and final_count is not None and final_count != s["samples"]:
            report["errors"].append(f"Sensor {i+1}: final firmware count differs from stored samples")
        if s["timestamp_jumps"]:
            report["errors"].append(f"Sensor {i+1}: discontinuous sensor timestamps")
        if s["samples"] and len(anchors) < 2:
            report["warnings"].append(f"Sensor {i+1}: insufficient FSYNC anchors for measured sample timing")
        if s["invalid"] or s["saturated"]:
            report["warnings"].append(f"Sensor {i+1}: invalid or near-range-limit samples; inspect before analysis")
        if s["unmatched_fsync"]:
            report["warnings"].append(f"Sensor {i+1}: some FSYNC-tagged samples lack a matching stored marker (possible window boundary)")
    fault = report["stats"].get("255", {}).get("fault", "NONE")
    if fault != "NONE":
        report["errors"].append(f"Firmware fault: {fault}")
    report["mode"] = mode
    report["sensors"] = [{k:v for k,v in s.items() if k != "anchors"} for s in sensors]
    report["integrity_ok"] = not report["errors"]
    return report, [s["anchors"] for s in sensors]


def sample_time(index, anchors, indices):
    if not anchors:
        return "", "unsynchronized"
    right = bisect.bisect_left(indices, index)
    if right < len(anchors) and anchors[right][0] == index:
        return round(anchors[right][1], 3), "fsync_anchor"
    if 0 < right < len(anchors):
        a,b = anchors[right-1:right+1]
        if 0 < b[1]-a[1] <= 30000:
            return round(a[1]+(index-a[0])*(b[1]-a[1])/(b[0]-a[0]), 3), "interpolated"
    candidates = anchors[max(0,right-1):min(len(anchors),right+1)]
    a = min(candidates, key=lambda item: abs(index-item[0]))
    return round(a[1]+(index-a[0])*62.5, 3), "nominal_extrapolation"


def export_csv(paths, out, anchors):
    indices = [[a[0] for a in sensor] for sensor in anchors]
    high = [-1]*4
    handles = [open(out/f"sensor_{i+1}.csv", "w", newline="", encoding="utf-8") for i in range(4)]
    writers = [csv.writer(h) for h in handles]
    for w in writers:
        w.writerow(["sample_index","mcu_us","time_quality","ax_g","ay_g","az_g",
                    "gx_dps","gy_dps","gz_dps","temperature_C","valid","near_saturation",
                    "fifo_header","raw_timestamp"])
    try:
        with open(out/"events.csv","w",newline="",encoding="utf-8") as ef, open(out/"gps.csv","w",newline="",encoding="utf-8") as gf:
            events, gps = csv.writer(ef), csv.writer(gf)
            events.writerow(["mcu_us","type","source","value"])
            gps.writerow(["receive_mcu_us","fragment_index","nmea_fragment"])
            try:
                for kind, source, time, data in frames(paths):
                    if kind == 2:
                        for index, header, axes, temp, stamp in packets(data):
                            if index <= high[source]:
                                continue
                            high[source]=index
                            t,q=sample_time(index,anchors[source],indices[source])
                            valid=all(v != -32768 for v in axes)
                            acc=[round(v/2048,7) if v != -32768 else "" for v in axes[:3]]
                            gyro=[round(v*2000/32768,7) if v != -32768 else "" for v in axes[3:]]
                            temperature=round(temp/2.07+25,4) if temp != -128 else ""
                            writers[source].writerow([index,t,q,*acc,*gyro,temperature,int(valid),
                                                      int(any(abs(v)>=32700 for v in axes if v != -32768)),header,stamp])
                    elif kind == 4:
                        events.writerow([time,"initial_level" if len(data)>1 else "edge",
                                         "trigger" if source==0 else f"cam{source}_strobe",data[0]])
                    elif kind == 6:
                        events.writerow([time,"burst","",data.decode("ascii")])
                    elif kind == 5:
                        gps.writerow([time,source,data.decode("ascii")])
            except ValueError:
                pass # scan() already reports the damaged tail; export valid prefix only.
    finally:
        for h in handles:
            h.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files",nargs="+",help="XPL files or wildcard for ONE complete session")
    parser.add_argument("--out",type=Path,default=Path("decoded"))
    parser.add_argument("--csv",action="store_true",help="Export four potentially large sample CSVs plus event/GPS CSVs")
    args=parser.parse_args()
    paths=sorted({Path(p) for pattern in args.files for p in glob.glob(pattern)})
    if not paths:
        parser.error("No matching XPL files")
    report,anchors=scan(paths)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/"summary.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    if args.csv:
        export_csv(paths,args.out,anchors)
    print(json.dumps(report,indent=2))
    return 0 if report["integrity_ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
