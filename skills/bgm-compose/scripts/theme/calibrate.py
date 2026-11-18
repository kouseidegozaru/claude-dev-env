"""Gate calibration.

Proves (or refutes) each checker's discrimination before it is allowed
to reject candidates. Rules of the calibration:

- Checker definitions and thresholds are fixed from musical rules and
  the scene specification BEFORE any sample is seen.
- Defect samples are composed independently from the defect definition
  text only (samples.py), never by transforming good themes. Every
  defect class has two construction methods: A (development) and
  B (holdout, a different construction).
- The good side always includes external well-known melodies
  (public domain). A checker definition that rejects an external good
  melody is not adopted.
- Authority condition, per checker: development class n >= 29 with
  one-sided 95% Clopper-Pearson lower bound of the detection rate
  >= 0.90; false-positive rate on good samples with one-sided 95%
  upper bound <= 0.10; holdout class detection rate >= 0.80;
  holdout-good false positives = 0; external-melody false
  positives = 0. A checker that misses any condition is warning-only.

Run at first use of the skill, and again whenever a checker or a
generation system changes. Writes the calibration record JSON.

Usage:
  python3 calibrate.py [--out calibration/calibration_record.json]
"""
import argparse
import json
import math
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import samples as S
from theme_check import CHECKERS, check_bars, DEFAULT_AUTHORITY


def binom_cdf(k, n, p):
    return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
               for i in range(0, k + 1))


def cp_lower(k, n, alpha=0.05):
    """One-sided Clopper-Pearson lower bound for a rate with k/n events."""
    if k == 0:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if 1 - binom_cdf(k - 1, n, mid) > alpha:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def cp_upper(k, n, alpha=0.05):
    """One-sided Clopper-Pearson upper bound for a rate with k/n events."""
    if k == n:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# checker -> (development class, holdout class)
AUTH_CLASSES = {
    "motif": ("no_motif_a", "no_motif_b"),
    "rhythm": ("single_dur_a", "single_dur_b"),
    "cadence": ("no_cadence_a", "no_cadence_b"),
    "range": ("wide_leaps_a", "wide_leaps_b"),
    "breath": ("no_breath_a", "no_breath_b"),
}


def build_classes():
    good_src2 = [S.gen_good(2000 + i) for i in range(40)]
    return {
        "no_motif_a": S.d_no_motif_a(64, 5000),
        "no_motif_b": S.d_no_motif_b(64, 11000),
        "single_dur_a": S.d_single_dur_a(40, 22000),
        "single_dur_b": S.d_single_dur_b(32, 23000),
        "no_cadence_a": S.d_no_cadence_a(40, 14000),
        "no_cadence_b": S.d_no_cadence_b(32, 15000),
        "wide_leaps_a": S.d_wide_leaps_a(40, 16000),
        "wide_leaps_b": S.d_wide_leaps_b(32, 17000),
        "no_breath_a": S.d_no_breath_a(40, 18000),
        "no_breath_b": S.d_no_breath_b(32, 19000),
        "pitch_shuffle": S.h_pitch_shuffle(good_src2[:30], 8000),
        "rhythm_shuffle": S.h_rhythm_shuffle(good_src2[:30], 9000),
        "sequence_only": S.d_sequence_only(40, 20000),
    }


def main():
    ap = argparse.ArgumentParser(description="Calibrate the theme gate.")
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "calibration", "calibration_record.json"))
    args = ap.parse_args()

    t_start = time.time()
    good = [S.gen_good(1000 + i) for i in range(64)]
    hold_good = S.holdout_good_var([S.gen_good(3000 + i) for i in range(30)])
    famous = {k: f() for k, f in S.FAMOUS.items()}
    mediocre = {k: f() for k, f in S.MEDIOCRE.items()}
    classes = build_classes()

    record = {
        "format": "calibration-record/1",
        "authority_rule": {
            "dev_min_n": 29,
            "detection_lower95_min": 0.90,
            "fp_upper95_max": 0.10,
            "holdout_detection_min": 0.80,
            "holdout_good_fp_must_be": 0,
            "external_fp_must_be": 0,
        },
        "external_goods": {},
        "external_controls": {},
        "classes": {},
        "authority": {},
        "coverage": {},
        "operational": {},
    }

    print("== external themes under the gate ==")
    for name, t in list(famous.items()):
        detail, verdict = check_bars(t, DEFAULT_AUTHORITY)
        print(f"famous {name}: authority_gate={'PASS' if verdict else 'FAIL'} {detail}")
        record["external_goods"][name] = {"checks": detail, "authority_gate": verdict}
    for name, t in list(mediocre.items()):
        detail, verdict = check_bars(t, DEFAULT_AUTHORITY)
        print(f"mediocre {name}: authority_gate={'PASS' if verdict else 'FAIL'} {detail}")
        record["external_controls"][name] = {"checks": detail, "authority_gate": verdict}

    print("== per-class detection with attribution ==")
    for name, cls in classes.items():
        fails = Counter()
        for t in cls:
            detail, _ = check_bars(t)
            for kk, v in detail.items():
                if not v:
                    fails[kk] += 1
        full_det = sum(1 for t in cls
                       if not all(check_bars(t)[0].values()))
        print(f"{name} n={len(cls)} detected={full_det} "
              f"rate={full_det / len(cls):.2f} by={dict(fails)}")
        record["classes"][name] = {
            "n": len(cls), "detected_full_gate": full_det,
            "attribution": dict(fails),
        }

    print("== authority per checker ==")
    full_fp = sum(1 for t in good if not all(check_bars(t)[0].values()))
    hold_full_fp = sum(1 for t in hold_good
                       if not all(check_bars(t)[0].values()))
    record["operational"]["full_gate_fp_good"] = [full_fp, len(good)]
    record["operational"]["full_gate_fp_holdout_good"] = [hold_full_fp,
                                                          len(hold_good)]
    for item, (a_name, b_name) in AUTH_CLASSES.items():
        chk = CHECKERS[item]
        a, b = classes[a_name], classes[b_name]
        det_a = sum(1 for t in a if not chk(t))
        det_b = sum(1 for t in b if not chk(t))
        fp_i = sum(1 for t in good if not chk(t))
        fp_h = sum(1 for t in hold_good if not chk(t))
        fp_ext = sum(1 for t in famous.values() if not chk(t))
        lower = cp_lower(det_a, len(a))
        upper = cp_upper(fp_i, len(good))
        ok = (len(a) >= 29 and lower >= 0.90 and upper <= 0.10
              and det_b / len(b) >= 0.80 and fp_h == 0 and fp_ext == 0)
        print(f"{item}: dev {det_a}/{len(a)} lower95={lower:.3f} | "
              f"holdout {det_b}/{len(b)} rate={det_b / len(b):.2f} | "
              f"FP good {fp_i}/{len(good)} upper95={upper:.3f} | "
              f"FP holdout-good {fp_h}/{len(hold_good)} | FP external "
              f"{fp_ext}/{len(famous)} -> {'AUTHORITY' if ok else 'warning-only'}")
        record["authority"][item] = {
            "authority": bool(ok),
            "dev_detected": det_a, "dev_n": len(a),
            "dev_lower95": round(lower, 3),
            "holdout_detected": det_b, "holdout_n": len(b),
            "holdout_rate": round(det_b / len(b), 3),
            "fp_good": fp_i, "fp_good_n": len(good),
            "fp_upper95": round(upper, 3),
            "fp_holdout_good": fp_h, "fp_external": fp_ext,
        }

    print("== coverage records (not authority classes) ==")
    for name in ("pitch_shuffle", "rhythm_shuffle"):
        cls = classes[name]
        det = sum(1 for t in cls if not all(check_bars(t)[0].values()))
        print(f"{name}: detected {det}/{len(cls)} "
              f"({det / len(cls):.2f}) by the full checker set")
        record["coverage"][name] = {"detected": det, "n": len(cls)}
    # The sequence-only defect in its realistic shape (the hand-written
    # controls) PASSES the gate: it is a coverage gap, delegated to
    # selection. The programmatic sequence class climbs out of range and
    # is caught incidentally by the range checker; that is a different
    # shape and must not be read as coverage of the defect.
    seq_controls = {name: check_bars(t, DEFAULT_AUTHORITY)[1]
                    for name, t in mediocre.items()
                    if name.startswith("sequence_only")}
    print(f"sequence-only controls passing the authority gate: "
          f"{sum(seq_controls.values())}/{len(seq_controls)} "
          f"-> coverage gap, delegated to selection")
    record["coverage"]["sequence_only_controls"] = {
        "authority_gate_pass": seq_controls,
        "note": "realistic sequence-only themes pass the gate; "
                "the gate does not cover this defect",
    }
    cls = classes["sequence_only"]
    det = sum(1 for t in cls if not all(check_bars(t)[0].values()))
    record["coverage"]["sequence_only_programmatic"] = {
        "detected": det, "n": len(cls),
        "note": "caught incidentally by the range checker when the "
                "sequence climbs out of range; not coverage of the "
                "sequence-only defect itself",
    }

    print("== operational pass rates ==")
    walks_ind = [S.gen_walk(20000 + i) for i in range(100)]
    p_ind = sum(1 for t in walks_ind if check_bars(t, DEFAULT_AUTHORITY)[1])

    def walk_copy(seed):
        t = S.gen_walk(seed, rhythm_plan="good")
        pres = [list(b) for b in t[:8]]
        rep = [list(b) for b in pres]
        from theme_sheet import degree_to_pitch as d2p
        rep[7] = [(rep[6][-1][0], 1), (d2p(1), 1), (d2p(0), 2)]
        return pres + rep

    walks_copy = [walk_copy(21000 + i) for i in range(100)]
    p_copy = sum(1 for t in walks_copy if check_bars(t, DEFAULT_AUTHORITY)[1])
    print(f"random walks: independent reprise passed {p_ind}/100; "
          f"reprise=copy passed {p_copy}/100")
    record["operational"]["walk_pass_independent_reprise"] = [p_ind, 100]
    record["operational"]["walk_pass_copy_reprise"] = [p_copy, 100]

    base = [S.gen_good(60000 + i) for i in range(20)]
    for k in (1, -1, 2, -2):
        okc = sum(1 for t in base
                  if check_bars(S.vary_theme(t, k), DEFAULT_AUTHORITY)[1])
        print(f"variation shift {k:+d}: authority-gate pass {okc}/20")
        record["operational"][f"variation_pass_{k:+d}"] = [okc, 20]

    t0 = time.perf_counter()
    for _ in range(20):
        pool = [S.gen_good(40000 + i) for i in range(22)] + \
               [S.gen_walk(41000 + i) for i in range(22)]
        for t in pool:
            check_bars(t)
    dt = (time.perf_counter() - t0) / 20
    print(f"44 candidates generated + fully checked: {dt * 1000:.0f} ms per pool")
    record["operational"]["pool44_ms"] = round(dt * 1000)
    record["operational"]["calibration_wall_seconds"] = round(time.time() - t_start, 1)

    with open(args.out, "w") as f:
        json.dump(record, f, indent=1)
    print(f"calibration record written: {args.out}")


if __name__ == "__main__":
    main()
