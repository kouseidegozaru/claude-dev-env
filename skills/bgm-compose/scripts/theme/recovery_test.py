"""Blind selection recovery test.

Verifies that the selection pass (a human-style ranking of anonymized
notation) can actually discriminate theme quality, using items whose
quality is known from OUTSIDE the production generators:

- 5 external well-known melodies (public domain) - the known goods;
- 4 well-formed-but-mediocre controls (scale practice, sequence only);
- 12 ordinary generated candidates as filler.

Every item must pass the authority gate (the gate is not what is being
tested here - discrimination above the gate is). Items are anonymized
and shuffled; the label key is sealed in a separate file. The selector
reads ONLY the pool text, ranks all items from the notation, and only
then is the key opened and the ranking scored.

Success criteria (fixed in this file BEFORE any ranking is made):
  A: every famous melody ranks in the top ~43% (rank <= 9 of 21,
     scaled as max(5, round(0.43 * N)) for other pool sizes);
  B: full separation - every mediocre control ranks below every
     famous melody;
  C (reference only): at least one famous melody in the top 3.
Pass = A and B. While the latest recovery test is not passing, the
selection must not be treated as a verified filter: the final 2-3
candidates go to a listening check and the user picks the main theme.

Run the test at first use of the skill, and again when a generation
system or the scene genre changes substantially.

Usage:
  python3 recovery_test.py build --out-dir work/recovery1
  # ... selector ranks the ids in work/recovery1/blind_pool.txt into
  #     work/recovery1/ranking.txt (one id per line, best first) ...
  python3 recovery_test.py score --out-dir work/recovery1
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import samples as S
from theme_sheet import format_bars_text
from theme_check import check_bars, load_authority


def build(out_dir, seed=20261117):
    authority, _ = load_authority(None)
    items = []
    for name, f in S.FAMOUS.items():
        items.append((f"famous:{name}", f()))
    for name, f in S.MEDIOCRE.items():
        items.append((f"mediocre:{name}", f()))
    for i in range(6):
        items.append((f"filler:gen_good:{i}", S.gen_good(50000 + i)))
    rng = random.Random(777)
    for i in range(2):
        mot = [rng.choice([-1, 0, 1, 2, 3]) for _ in range(4)]
        items.append((f"filler:struct:{i}",
                      S.gen_good(51000 + i, motif=mot, cadence_ok=True)))
    walks = []
    wseed = 52000
    while len(walks) < 4:
        t = S.gen_walk(wseed)
        wseed += 1
        if check_bars(t, authority)[1]:
            walks.append(t)
    for i, t in enumerate(walks):
        items.append((f"filler:walk:{i}", t))

    bad = [(lab, check_bars(t)[0]) for lab, t in items
           if not check_bars(t, authority)[1]]
    if bad:
        raise SystemExit(f"pool items failing the authority gate: {bad}")

    random.Random(seed).shuffle(items)
    os.makedirs(out_dir, exist_ok=True)
    lines = ["Blind selection pool. All themes: 8 bars presentation + 8 bars "
             "reprise, in scale degrees relative to the tonic.",
             "Notation: degree(duration in beats). Degrees: 0 = tonic, "
             "1 = 2nd, 2 = 3rd, 3 = 4th, 4 = 5th, 5 = 6th, 6 = 7th, "
             "7 = tonic above, negative = below the tonic.", ""]
    key = {}
    for idx, (lab, t) in enumerate(items):
        tid = f"T{idx + 1:02d}"
        key[tid] = lab
        lines.append(f"[{tid}]")
        lines.append(format_bars_text(t))
        lines.append("")
    with open(os.path.join(out_dir, "blind_pool.txt"), "w") as f:
        f.write("\n".join(lines))
    with open(os.path.join(out_dir, "blind_key.json"), "w") as f:
        json.dump(key, f, indent=1)
    print(f"pool written: {len(items)} themes, all pass the authority gate; "
          f"key sealed in blind_key.json")


def score(out_dir):
    with open(os.path.join(out_dir, "blind_key.json")) as f:
        key = json.load(f)
    ranking = []
    with open(os.path.join(out_dir, "ranking.txt")) as f:
        for line in f:
            line = line.strip()
            if line.startswith("T") and len(line) <= 4:
                ranking.append(line)
    if len(ranking) != len(key):
        raise SystemExit(f"ranking has {len(ranking)} ids, pool has {len(key)}")
    rank_of = {tid: i + 1 for i, tid in enumerate(ranking)}
    famous = {tid: rank_of[tid] for tid, lab in key.items()
              if lab.startswith("famous:")}
    mediocre = {tid: rank_of[tid] for tid, lab in key.items()
                if lab.startswith("mediocre:")}
    filler = {tid: rank_of[tid] for tid, lab in key.items()
              if lab.startswith("filler:")}
    print("famous ranks:", {key[t]: r for t, r in
                            sorted(famous.items(), key=lambda x: x[1])})
    print("mediocre ranks:", {key[t]: r for t, r in
                              sorted(mediocre.items(), key=lambda x: x[1])})
    print("filler ranks:", {key[t]: r for t, r in
                            sorted(filler.items(), key=lambda x: x[1])})
    n = len(key)
    a_limit = max(5, round(0.43 * n))
    A = all(r <= a_limit for r in famous.values())
    B = max(famous.values()) < min(mediocre.values())
    C = min(famous.values()) <= 3
    print(f"criterion A (all famous <= {a_limit} of {n}): {A}")
    print(f"criterion B (separation famous/mediocre): {B}")
    print(f"criterion C (a famous in top 3, reference only): {C}")
    print(f"RECOVERY TEST: {'PASS' if A and B else 'FAIL'}")
    return 0 if (A and B) else 1


def main():
    ap = argparse.ArgumentParser(description="Blind selection recovery test.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--out-dir", required=True)
    b.add_argument("--seed", type=int, default=20261117)
    s = sub.add_parser("score")
    s.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.out_dir, args.seed)
        return 0
    return score(args.out_dir)


if __name__ == "__main__":
    sys.exit(main())
