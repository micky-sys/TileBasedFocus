"""Score frames with many candidate statistics (sec.54.11): percentiles 2..50 of the pooled widths and
k-sharpest means.  py -3 tools/w10_pct_score.py out.csv list.txt [step]"""
import csv, os, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "host"))
from flight_edges import frame_edges

PCT = (2, 5, 10, 15, 20, 30, 50)
KS = (30, 100, 300)
out_csv, list_file = sys.argv[1], sys.argv[2]
step = int(sys.argv[3]) if len(sys.argv) > 3 else 1
paths = [l.strip() for l in open(list_file) if l.strip()][::step]
keys = ["file", "ts", "encoder", "enc_stale", "n_edges", "n_roi", "med_mean"] + [f"w{p}" for p in PCT] + [f"k{n}" for n in KS]
t0 = time.time()
with open(out_csv, "w", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore"); wr.writeheader()
    for i, p in enumerate(paths):
        try:
            st = frame_edges(p, pct=PCT, ksharp=KS)
        except Exception as e:  # noqa: BLE001
            print("skip", os.path.basename(p), e); continue
        st["file"] = os.path.basename(p); wr.writerow(st)
        if i % 50 == 0:
            print(i, len(paths), f"{time.time() - t0:.0f}s", flush=True)
print("wrote", out_csv)
