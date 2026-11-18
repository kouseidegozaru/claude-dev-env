"""Theme checkers (the gate).

Five checkers inspect a theme sheet. Definitions were fixed from musical
rules before any samples were seen, then corrected only where external
well-known melodies refuted them (each correction is documented in
SKILL.md and in the calibration record). Four checkers hold rejection
authority (see calibration/calibration_record.json); the motif checker
is warning-only: its detection lower confidence bound did not reach the
authority threshold, so it is reported as a selection feature and never
rejects a candidate on its own.

Checker definitions (bars = 16 bars of (pitch, beats), normalized key,
pitch classes are relative to the tonic):
- rhythm:  the presentation (bars 1-8) contains a repeated bar-rhythm
  tuple or a repeated 2-bar block rhythm tuple, AND uses at least 2
  distinct duration values. The defect it rejects is a theme written in
  a single duration value only.
- cadence: the reprise ends on the tonic pitch class, and the
  presentation ends on the tonic or the dominant pitch class. A
  dominant->tonic leap close is a normal close and passes.
- range:   total range <= 15 semitones; leaps of >= 9 semitones are
  counted within bars only (a leap across a bar line is a section
  restart); at most 2 such leaps; every such leap must be followed by
  stepwise motion (<= 2 semitones) in the opposite direction.
- breath:  fewer than half of the eight 2-bar blocks lack a note of
  >= 2 beats. One short block is normal phrasing, not a defect.
- motif (warning-only): the head interval cell of bar 1 (its first up
  to 3 intervals) recurs as the head cell of at least one other bar
  among all 16 bars.

Usage:
  python3 theme_check.py THEME.json [--calibration CALIBRATION_RECORD.json]
Prints per-checker results and the authority-gate verdict as JSON.
Exit code 0 = authority gate passed, 1 = rejected, 2 = invalid input.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from theme_sheet import load_sheet, sheet_to_bars, pitch_seq, dur_seq
from collections import Counter

DEFAULT_AUTHORITY = ["rhythm", "cadence", "range", "breath"]


def chk_rhythm(bars):
    pres = bars[:8]
    bar_tuples = [tuple(du for (_, du) in b) for b in pres]
    block_tuples = [bar_tuples[i] + bar_tuples[i + 1] for i in range(0, 8, 2)]
    rep = any(c >= 2 for c in Counter(bar_tuples).values()) or \
        any(c >= 2 for c in Counter(block_tuples).values())
    return rep and len(set(dur_seq(pres))) >= 2


def chk_cadence(bars):
    pres_last = bars[7][-1][0] % 12
    rp = pitch_seq(bars[8:])
    return rp[-1] % 12 == 0 and pres_last in (0, 7)


def chk_range(bars):
    ps = pitch_seq(bars)
    if max(ps) - min(ps) > 15:
        return False
    flat = []  # (is_within_bar, interval or None at bar boundaries)
    for b in bars:
        bps = [p for (p, _) in b]
        for i in range(len(bps) - 1):
            flat.append((True, bps[i + 1] - bps[i]))
        flat.append((False, None))
    big = [i for i, (within, iv) in enumerate(flat)
           if within and iv is not None and abs(iv) >= 9]
    if len(big) > 2:
        return False
    seq = [iv for (_, iv) in flat if iv is not None]
    pos = -1
    for (within, iv) in flat:
        if iv is None:
            continue
        pos += 1
        if within and abs(iv) >= 9:
            if pos + 1 < len(seq):
                nxt = seq[pos + 1]
                if not (nxt * iv < 0 and abs(nxt) <= 2):
                    return False
            else:
                return False
    return True


def chk_breath(bars):
    short = 0
    for i in range(0, 16, 2):
        ev = bars[i] + bars[i + 1]
        if not any(du >= 2 for (_, du) in ev):
            short += 1
    return short < 4


def _head_cell(bar):
    ps = [p for (p, _) in bar]
    k = min(3, len(ps) - 1)
    if k < 2:
        return None
    return tuple(ps[i + 1] - ps[i] for i in range(k))


def chk_motif(bars):
    h0 = _head_cell(bars[0])
    if h0 is None:
        return False
    return any(_head_cell(b) == h0 for b in bars[1:])


CHECKERS = {
    "rhythm": chk_rhythm,
    "cadence": chk_cadence,
    "range": chk_range,
    "breath": chk_breath,
    "motif": chk_motif,
}


def check_bars(bars, authority=None):
    """Returns (detail dict, authority-gate verdict)."""
    if authority is None:
        authority = DEFAULT_AUTHORITY
    detail = {name: bool(fn(bars)) for name, fn in CHECKERS.items()}
    verdict = all(detail[name] for name in authority)
    return detail, verdict


def load_authority(calibration_path):
    """Authority set from a calibration record; falls back to defaults."""
    if calibration_path and os.path.exists(calibration_path):
        with open(calibration_path) as f:
            record = json.load(f)
        auth = [name for name, info in record.get("authority", {}).items()
                if info.get("authority")]
        if auth:
            return auth, record
    return list(DEFAULT_AUTHORITY), None


def main():
    ap = argparse.ArgumentParser(description="Check a theme sheet against the gate.")
    ap.add_argument("theme", help="theme sheet JSON (theme-sheet/1)")
    ap.add_argument("--calibration", default=None,
                    help="calibration record JSON (default: ../calibration/calibration_record.json)")
    args = ap.parse_args()
    cal_path = args.calibration
    if cal_path is None:
        cand = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "..", "calibration", "calibration_record.json")
        cal_path = cand if os.path.exists(cand) else None
    authority, _ = load_authority(cal_path)
    try:
        sheet = load_sheet(args.theme)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(json.dumps({"error": str(e)}))
        return 2
    bars = sheet_to_bars(sheet)
    detail, verdict = check_bars(bars, authority)
    print(json.dumps({
        "theme": args.theme,
        "checks": detail,
        "authority_checkers": authority,
        "warning_only": [n for n in CHECKERS if n not in authority],
        "authority_gate": "PASS" if verdict else "FAIL",
    }, indent=1))
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
