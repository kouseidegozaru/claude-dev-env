# リードの各音を「メロディ音 + 直下のコードトーン最大2音」の和音に変換する。
import json
import sys

sys.path.insert(0, "/home/hatch/workspace/midi-bgm/pistols")
from spec import SPEC

BPM = SPEC["bpm"]
BAR_SEC = SPEC["beats_per_bar"] * 60.0 / BPM
prog = SPEC["progression"]
chords = SPEC["chords"]

with open(sys.argv[1]) as f:
    events = json.load(f)

out = []
for e in events:
    bar = int(e["start"] // BAR_SEC)
    bar = max(0, min(bar, len(prog) - 1))
    pcs = {n % 12 for n in chords[prog[bar]]["arp"]}
    p = e["pitch"]
    cands = [q for q in range(p - 13, p - 3) if q % 12 in pcs]
    cands = sorted(set(cands))[-2:]
    out.append(e)
    for q in cands:
        out.append({"start": e["start"], "dur": e["dur"] * 0.95,
                    "pitch": q, "vel": max(40, e["vel"] - 14)})
out.sort(key=lambda e: e["start"])
with open(sys.argv[2], "w") as f:
    json.dump(out, f)
print(f"{len(events)} notes -> {len(out)} notes (chord lead)")
