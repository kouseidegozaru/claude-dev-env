"""Candidate pool generation (step 1) + gate filtering (step 2).

Generates 30-60 theme candidates from three mixed systems, so the pool
does not lean on a single generation method:
  (a) constrained automatic generation - a degree walk whose range,
      leap and rhythm-density parameters are varied systematically;
  (b) motif-first composition - a head cell is stated, sequenced and
      answered (the structured generator in samples.py; in live use,
      Claude also hand-writes candidates of this kind into the pool);
  (c) variation of library themes - motif-cell transposition by scale
      degrees WITH cadence re-derivation (see samples.vary_theme).
Default mix for 40 candidates is a:b:c = 5:3:2. All candidates share
the key declared in step 0 (sheets are key-relative, so the key is a
label here). No intent or self-evaluation is attached to a candidate.

Outputs (in --out-dir):
  pool.json           full library record: provenance, sheet, per-checker
                      results and the authority-gate verdict per candidate
  selection_sheet.txt ONLY the authority-gate passers, anonymized and
                      shuffled, notation only - this is what the
                      selection pass reads
  selection_key.json  sealed mapping from anonymous ids to pool ids.
                      Do not open it until the selection ranking is fixed.

Usage:
  python3 generate_pool.py --out-dir work/pool1 [--total 40] [--seed 1]
                           [--seeds library.json]
--seeds: optional JSON list of theme sheets used as variation sources
for system (c); without it, this run's system-(b) output is used.
"""
import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import samples as S
from theme_sheet import bars_to_sheet, format_bars_text, degree_to_pitch as d2p
from theme_check import check_bars, load_authority

GOOD_BLOCKS = [(S.R["R2"], S.R["R1"]), (S.R["R5"], S.R["R1"]),
               (S.R["R3"], S.R["R1"]), (S.R["R2"], S.R["R1"])]


def gen_constrained(seed, start, steps, lo, hi, block_offset):
    """System (a): parameterized degree walk with cadence conformance
    (presentation closes on the dominant, reprise closes on the tonic)."""
    rng = random.Random(seed)
    bars, d = [], start
    for b in range(16):
        rhy = GOOD_BLOCKS[(b // 2 + block_offset) % 4][b % 2]
        degs = []
        for _ in rhy:
            d = max(lo, min(hi, d + rng.choice(steps)))
            degs.append(d)
        bars.append(S.bar(degs, rhy))
    bars[7] = bars[7][:-1] + [(d2p(4), bars[7][-1][1])]
    bars[15] = [(bars[14][-1][0], 1), (d2p(1), 1), (d2p(0), 2)]
    return bars


A_PARAM_GRID = [
    # (start, steps, lo, hi, block_offset)
    (2, [-1, 0, 1], 0, 7, 0), (3, [-1, 0, 1], 0, 7, 1),
    (2, [-2, -1, 0, 1, 2], 0, 9, 2), (4, [-2, -1, 0, 1, 2], 0, 9, 3),
    (1, [-1, 0, 1, 2], 0, 8, 0), (3, [-2, -1, 0, 1], 1, 9, 1),
    (2, [-2, -1, 1, 2], 0, 9, 2), (4, [-1, 0, 1], 2, 9, 3),
    (3, [-2, -1, 0, 1, 2], 0, 8, 0), (2, [-1, 0, 1, 2], 1, 8, 1),
    (4, [-2, -1, 0, 1, 2], 0, 9, 2), (1, [-2, -1, 0, 1], 0, 7, 3),
    (3, [-1, 0, 1], 0, 9, 0), (2, [-2, 0, 2], 0, 9, 1),
    (4, [-2, -1, 0, 1, 2], 1, 9, 2), (3, [-1, 0, 1, 2], 0, 9, 3),
    (2, [-2, -1, 0, 1, 2], 0, 7, 0), (1, [-1, 0, 1], 0, 8, 1),
    (3, [-2, -1, 1, 2], 1, 9, 2), (2, [-1, 0, 1], 0, 9, 3),
]


def main():
    ap = argparse.ArgumentParser(description="Generate and gate a candidate pool.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--total", type=int, default=40)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seeds", default=None,
                    help="JSON list of theme sheets: variation sources for system (c)")
    ap.add_argument("--calibration", default=None)
    args = ap.parse_args()

    if args.calibration is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "calibration", "calibration_record.json")
        if os.path.exists(cand):
            args.calibration = cand
    authority, _ = load_authority(args.calibration)
    n_a = round(args.total * 0.5)
    n_b = round(args.total * 0.3)
    n_c = args.total - n_a - n_b
    base = args.seed * 100000
    candidates = []  # (system, params, bars)

    for i in range(n_a):
        params = A_PARAM_GRID[i % len(A_PARAM_GRID)]
        bars = gen_constrained(base + 1000 + i, *params)
        candidates.append(("a", {"grid": list(params)}, bars))

    b_themes = []
    for i in range(n_b):
        motif = S.MOTIFS[i % len(S.MOTIFS)] if i < 8 else \
            [random.Random(base + 2000 + i).choice([-1, 0, 1, 2, 3])
             for _ in range(4)]
        bars = S.gen_good(base + 2000 + i, motif=motif)
        b_themes.append(bars)
        candidates.append(("b", {"motif": motif}, bars))

    seed_themes = b_themes
    if args.seeds:
        from theme_sheet import sheet_to_bars
        with open(args.seeds) as f:
            seed_themes = [sheet_to_bars(s) for s in json.load(f)]
    shifts = [1, -1, 2, -2]
    for i in range(n_c):
        src = seed_themes[i % len(seed_themes)]
        k = shifts[i % len(shifts)]
        bars = S.vary_theme(src, k)
        candidates.append(("c", {"shift": k, "source_index": i % len(seed_themes)}, bars))

    os.makedirs(args.out_dir, exist_ok=True)
    pool = {"format": "theme-pool/1", "seed": args.seed,
            "authority_checkers": authority, "candidates": []}
    passed = []
    for i, (system, params, bars) in enumerate(candidates):
        cid = f"C{i + 1:02d}"
        detail, verdict = check_bars(bars, authority)
        pool["candidates"].append({
            "id": cid, "system": system, "params": params,
            "sheet": bars_to_sheet(bars),
            "checks": detail, "authority_gate": verdict,
        })
        if verdict:
            passed.append((cid, bars))

    with open(os.path.join(args.out_dir, "pool.json"), "w") as f:
        json.dump(pool, f, indent=1)

    rng = random.Random(args.seed * 7919 + 13)
    rng.shuffle(passed)
    key = {}
    lines = [
        "Selection sheet. All themes: 8 bars presentation + 8 bars reprise,",
        "in scale degrees relative to the tonic (0 = tonic, 1 = 2nd, ...;",
        "negative = below the tonic). Notation: degree(duration in beats).",
        "Every theme on this sheet passed the calibrated gate. Rank ALL of",
        "them from the notation alone; provenance is not shown.",
        "",
    ]
    for i, (cid, bars) in enumerate(passed):
        sid = f"S{i + 1:02d}"
        key[sid] = cid
        lines.append(f"[{sid}]")
        lines.append(format_bars_text(bars))
        lines.append("")
    with open(os.path.join(args.out_dir, "selection_sheet.txt"), "w") as f:
        f.write("\n".join(lines))
    with open(os.path.join(args.out_dir, "selection_key.json"), "w") as f:
        json.dump(key, f, indent=1)

    by_system = {}
    for c in pool["candidates"]:
        by_system.setdefault(c["system"], [0, 0])
        by_system[c["system"]][0] += 1
        by_system[c["system"]][1] += 1 if c["authority_gate"] else 0
    print(f"pool: {len(candidates)} candidates "
          f"(a={n_a}, b={n_b}, c={n_c}); authority-gate passers: {len(passed)}")
    for s in sorted(by_system):
        print(f"  system {s}: {by_system[s][1]}/{by_system[s][0]} passed")
    print(f"wrote pool.json, selection_sheet.txt, selection_key.json to {args.out_dir}")


if __name__ == "__main__":
    main()
