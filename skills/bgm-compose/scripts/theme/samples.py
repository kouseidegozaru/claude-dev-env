"""Sample composers for calibration and for the recovery test.

Everything here produces theme material as bars of (pitch, beats) in the
normalized key. Three families:

1. Good-theme generators and legitimate variations (used as known-good
   samples and as filler candidates).
2. Defect composers. Each defect class is composed INDEPENDENTLY from
   the defect's definition text - never by transforming a good theme -
   and each class has two construction methods (A = development,
   B = holdout) so calibration can test generalization. Classes:
   - no_motif:      pitch line with no recurring head cell
   - single_dur:    a single duration value throughout
   - no_cadence:    the reprise does not close on the tonic
   - wide_leaps:    leaps too wide / frequent, without stepwise recovery
   - no_breath:     breathing points systematically absent
   - sequence_only: a natural defect no checker claims to cover
     (recorded as a coverage gap, delegated to selection)
   Shuffle classes (pitch shuffle / rhythm shuffle) are transforms by
   nature; they are reported as coverage records, not authority classes.
3. External reference themes: five public-domain melodies and four
   well-formed-but-mediocre controls, encoded from the melodies
   themselves. Used to validate checker definitions (a definition that
   rejects a known-good external melody is not adopted) and as the
   known items of the blind recovery test. Verification use only -
   never incorporated into produced music.
"""
import random

from theme_sheet import degree_to_pitch as d2p

R = {  # bar rhythm templates (sum = 4 beats)
    "R1": [1, 1, 2], "R2": [1, 1, 1, 1], "R3": [2, 1, 1], "R4": [1, 2, 1],
    "R5": [0.5, 0.5, 1, 2],
}
MOTIFS = [[0, 2, 1, 2], [0, 1, 2, 1], [0, 2, 3, 2], [0, 1, 0, -1]]


def bar(degs, rhythm):
    assert len(degs) == len(rhythm) and abs(sum(rhythm) - 4) < 1e-9
    return [(d2p(d), dur) for d, dur in zip(degs, rhythm)]


# ---------------------------------------------------------------- goods
def gen_good(seed, motif=None, cadence_ok=True, start=None, shift=None):
    """Motif-first structured theme: a head cell stated, sequenced and
    answered, with a scalar middle and a reprise that closes on the tonic."""
    rng = random.Random(seed)
    m = motif if motif is not None else MOTIFS[rng.randrange(len(MOTIFS))]
    s = start if start is not None else rng.choice([1, 2, 3])
    sh = shift if shift is not None else rng.choice([1, 2])
    at = lambda base, offs: [base + o for o in offs]
    pres = [
        bar(at(s, m), R["R2"]),
        bar(at(s, m[:3]), R["R1"]),
        bar(at(s + sh, m), R["R2"]),
        bar([s + sh + m[0], s + sh + m[1], 4], R["R1"]),
        bar([s + 2, s + 3, s + 4, s + 5], R["R5"]),
        bar([s + 4, s + 2, s + 1], R["R1"]),
        bar(at(s, m), R["R2"]),
        bar([s + m[0], s + m[1], 7 if s >= 2 else 0], R["R1"]),
    ]
    final = [s + m[0], 1, 0] if cadence_ok else [s + m[0], 3, 5]
    repr_ = [
        bar(at(s, m), R["R2"]),
        bar(at(s, m[:3]), R["R1"]),
        bar(at(s + sh, m), R["R2"]),
        bar([s + sh + m[0], s + sh + m[1], s + 2], R["R1"]),
        bar([s + 5, s + 4, s + 3, s + 2], R["R5"]),
        bar([s + 2, s + 1, s], R["R1"]),
        bar(at(s, m), R["R2"]),
        bar(final, R["R1"]),
    ]
    return pres + repr_


def gen_walk(seed, cadence_ok=True, rhythm_plan="good"):
    """Constrained random walk. rhythm_plan='good' reuses textbook block
    rhythms so breath/rhythm stay plausible and defects isolate to pitch."""
    rng = random.Random(seed)
    good_blocks = [(R["R2"], R["R1"]), (R["R5"], R["R1"]),
                   (R["R3"], R["R1"]), (R["R2"], R["R1"])]
    bars, d = [], rng.choice([2, 3, 4])
    for b in range(16):
        if rhythm_plan == "good":
            rhy = good_blocks[(b // 2) % 4][b % 2]
        else:
            rhy = rng.choice(list(R.values()))
        degs = []
        for _ in rhy:
            d = max(0, min(9, d + rng.choice([-2, -1, 0, 1, 2])))
            degs.append(d)
        bars.append(bar(degs, rhy))
    if cadence_ok:
        bars[15] = [(bars[14][-1][0], 1), (d2p(1), 1), (d2p(0), 2)]
    return bars


def holdout_good_var(src):
    """Legitimate variations of good themes (ornament, rhythm swap) made
    by a different method than the good generator itself."""
    out = []
    for t in src:
        t2 = [list(b) for b in t]
        b = t2[13]
        for j, (p, du) in enumerate(b):
            if du == 2:
                t2[13] = b[:j] + [(p, 1), (p + 2, 1)] + b[j + 1:]
                break
        b5 = t2[4]
        if len(b5) >= 2:
            t2[4] = [(b5[0][0], b5[1][1]), (b5[1][0], b5[0][1])] + b5[2:]
        out.append(t2)
    return out


# ------------------------------------------------- variation (system c)
def _register_reps(deg):
    """All pitches with the pitch class of scale degree `deg`, across
    registers (degree + 7 per octave)."""
    return [d2p(deg + 7 * m) for m in (-2, -1, 0, 1, 2)]


def _excursion(p, lo, hi):
    return max(lo - p, 0) + max(p - hi, 0)


def _cadence_pitch(deg, lo, hi, near):
    """Pitch for a re-derived cadence degree, placed in the theme's own
    register. The cadence rule constrains pitch classes only, so any
    register representative preserves the cadence; among them prefer
    one inside the body's pitch window [lo, hi], closest to the
    preceding note `near`. If none is inside, take the one straying
    least outside the window."""
    reps = _register_reps(deg)
    inside = [p for p in reps if lo <= p <= hi]
    if inside:
        return min(inside, key=lambda p: abs(p - near))
    return min(reps, key=lambda p: (_excursion(p, lo, hi), abs(p - near)))


def _cadence_close(lo, hi, near):
    """The reprise-final descent (degree 1, degree 0) as one register
    pair, so the stepwise close is preserved in whichever register the
    pair lands. Scored like _cadence_pitch: inside the window first,
    then least excursion, then smoothest approach from `near`."""
    best = None
    for m in (-2, -1, 0, 1, 2):
        pair = (d2p(1 + 7 * m), d2p(0 + 7 * m))
        score = (_excursion(pair[0], lo, hi) + _excursion(pair[1], lo, hi),
                 abs(pair[0] - near))
        if best is None or score < best[0]:
            best = (score, pair)
    return best[1]


def vary_theme(t, k):
    """Transpose a theme by k scale degrees (motif-cell operation) and
    re-derive the cadence bars: the presentation-final bar is re-closed
    on the dominant and the reprise-final bar is re-derived as
    (previous note, degree 1, degree 0). A raw transposition without
    re-derivation shifts every phrase ending and is not used.

    The re-derived notes are placed in the transposed theme's own
    register, not fixed in the base octave. Fixed base-octave placement
    collided with upward transposition: the closing degrees 1 and 0
    dragged the pitch minimum back to the base octave while the body
    sat a shift higher, stretching the total span past the range
    limit and rejecting every upward variant of a wide-range theme.
    Register placement preserves the cadence exactly (the cadence
    rule sees pitch classes) and keeps the variant's span equal to
    the transposed body's span whenever a representative fits."""
    from theme_sheet import pitch_to_degree
    t2 = [[(d2p(pitch_to_degree(p) + k), du) for (p, du) in b] for b in t]
    body = []
    for bi, b in enumerate(t2):
        if bi == 15:
            continue  # fully re-derived below; its first note is bar 14's last
        body.extend(p for (p, _) in (b[:-1] if bi == 7 else b))
    lo, hi = min(body), max(body)
    near7 = t2[7][-2][0] if len(t2[7]) >= 2 else t2[6][-1][0]
    t2[7] = t2[7][:-1] + [(_cadence_pitch(4, lo, hi, near7), t2[7][-1][1])]
    prev = t2[14][-1][0]
    d1, d0 = _cadence_close(lo, hi, prev)
    t2[15] = [(prev, 1), (d1, 1), (d0, 2)]
    return t2


# ------------------------------------------------------- defect classes
def _cadence_fix(bars, pres_end_deg=4):
    bars[7] = bars[7][:-1] + [(d2p(pres_end_deg), bars[7][-1][1])]
    bars[15] = [(bars[14][-1][0], 1), (d2p(1), 1), (d2p(0), 2)]
    return bars


def _has_pitch_exact_cell(bars, n=3):
    from collections import Counter
    from theme_sheet import pitch_seq
    ps = pitch_seq(bars[:8])
    cells = [tuple(ps[i:i + n]) for i in range(len(ps) - n + 1)]
    return any(c >= 2 for c in Counter(cells).values())


def _gen_walk_isolated(seed):
    """Random walk that satisfies range/cadence/rhythm/breath by
    construction, so the only defect it can carry is lack of motif."""
    rng = random.Random(seed)
    good_blocks = [(R["R2"], R["R1"]), (R["R5"], R["R1"]),
                   (R["R3"], R["R1"]), (R["R2"], R["R1"])]
    bars, d = [], rng.choice([3, 4, 5])
    for b in range(16):
        rhy = good_blocks[(b // 2) % 4][b % 2]
        degs = []
        for _ in rhy:
            d = max(2, min(7, d + rng.choice([-2, -1, 0, 1, 2])))
            degs.append(d)
        bars.append(bar(degs, rhy))
    last = bars[7][-1]
    bars[7] = bars[7][:-1] + [(d2p(4), last[1])]
    bars[15] = [(bars[14][-1][0], 1), (d2p(1), 1), (d2p(0), 2)]
    return bars


def d_no_motif_a(n, seed0):
    out, seed = [], seed0
    while len(out) < n:
        t = _gen_walk_isolated(seed)
        seed += 1
        if not _has_pitch_exact_cell(t):  # definition-level filter (!= checker)
            out.append(t)
    return out


def d_no_motif_b(n, seed0):
    """Second method for 'no pitch motif': rhythm is one fixed 2-bar
    block throughout while the pitch line is an isolated walk."""
    rng = random.Random(seed0)
    out = []
    block = (R["R5"], R["R1"])
    for i in range(n):
        bars, d = [], rng.choice([3, 4, 5])
        for b in range(16):
            rhy = block[b % 2]
            degs = []
            for _ in rhy:
                d = max(2, min(7, d + rng.choice([-2, -1, 0, 1, 2])))
                degs.append(d)
            bars.append(bar(degs, rhy))
        out.append(_cadence_fix(bars))
    return out


def d_single_dur_a(n, seed0):
    """All quarter notes (single duration value), motif cell at bar
    heads, cadence conformed - composed from the definition only."""
    rng = random.Random(seed0)
    out = []
    cell = [0, 2, 1, 2]
    for i in range(n):
        s = rng.choice([1, 2, 3])
        bars = []
        for b in range(16):
            base = s + (0 if b % 4 == 0 else rng.choice([0, 1]))
            degs = [base + cell[j % 4] for j in range(4)]
            bars.append(bar(degs, [1, 1, 1, 1]))
        bars[7] = bars[7][:-1] + [(d2p(4), 1)]
        bars[15] = [(bars[14][-1][0], 1), (d2p(2), 1), (d2p(1), 1), (d2p(0), 1)]
        out.append(bars)
    return out


def d_single_dur_b(n, seed0):
    """Method B: all eighth notes over an ostinato pitch pattern."""
    rng = random.Random(seed0)
    out = []
    for i in range(n):
        s = rng.choice([2, 3])
        pat = [s, s + 2, s + 1, s + 2, s, s + 1, s + 2, s + 3]
        bars = []
        for b in range(16):
            degs = [pat[(b * 3 + j) % 8] for j in range(8)]
            bars.append(bar(degs, [0.5] * 8))
        bars[7] = bars[7][:-1] + [(d2p(4), 0.5)]
        bars[15] = [(bars[14][-1][0], .5), (d2p(3), .5), (d2p(2), .5),
                    (d2p(1), .5), (d2p(0), .5), (d2p(0), .5),
                    (d2p(0), .5), (d2p(0), .5)]
        out.append(bars)
    return out


def d_no_cadence_a(n, seed0):
    """Well-formed theme except the final bar is composed to end on
    scale degree 2 approached by a leap."""
    out = []
    for i in range(n):
        t = gen_good(seed0 + 1000 + i)
        t[15] = [(d2p(5), 1), (d2p(2), 1), (d2p(2), 2)]
        out.append(t)
    return out


def d_no_cadence_b(n, seed0):
    """Method B: final bar ends on the dominant after a stepwise line
    that never resolves."""
    out = []
    for i in range(n):
        t = gen_good(seed0 + 2000 + i)
        t[15] = [(d2p(2), 1), (d2p(3), 1), (d2p(4), 2)]
        out.append(t)
    return out


def d_wide_leaps_a(n, seed0):
    """Composed from leap cells [0,+9,-7,+5] semitone shapes in degrees."""
    rng = random.Random(seed0)
    out = []
    cell = [0, 5, 1, 4]  # degrees: C A D G -> leaps +9, -7, +5 semitones
    for i in range(n):
        s = rng.choice([0, 1])
        bars = []
        rhys = [R["R2"], R["R1"], R["R5"], R["R1"]]
        for b in range(16):
            rhy = rhys[b % 4]
            base = s + (b % 3)
            degs = [base + cell[j % 4] + (1 if j >= len(cell) else 0)
                    for j in range(len(rhy))]
            bars.append(bar(degs[:len(rhy)], rhy))
        out.append(_cadence_fix(bars))
    return out


def d_wide_leaps_b(n, seed0):
    """Method B: random-walk themes whose step distribution is
    leap-heavy (steps of +-5..7 degrees)."""
    rng = random.Random(seed0)
    out = []
    blocks = [(R["R2"], R["R1"]), (R["R5"], R["R1"]),
              (R["R3"], R["R1"]), (R["R2"], R["R1"])]
    for i in range(n):
        bars, d = [], rng.choice([3, 4])
        for b in range(16):
            rhy = blocks[(b // 2) % 4][b % 2]
            degs = []
            for _ in rhy:
                d = max(-4, min(14, d + rng.choice([-7, -5, 5, 7, -2, 2])))
                degs.append(d)
            bars.append(bar(degs, rhy))
        out.append(_cadence_fix(bars))
    return out


def d_no_breath_a(n, seed0):
    """Well-formed otherwise (motif cell, duration variety, repeated
    rhythm tuples); exactly the even 2-bar blocks are kept all-short."""
    rng = random.Random(seed0)
    out = []
    cell = [0, 2, 1, 2]
    for i in range(n):
        s = rng.choice([1, 2, 3])
        bars = []
        for b in range(16):
            base = s + (0 if b % 4 in (0, 1) else 1)
            if b % 4 in (0, 1):
                degs = [base + cell[j % 4] for j in range(5)]
                bars.append(bar(degs, [0.5, 0.5, 1, 1, 1]))
            else:
                degs = [base + cell[j] for j in range(3)]
                bars.append(bar(degs, [1, 1, 2]))
        out.append(_cadence_fix(bars))
    return out


def d_no_breath_b(n, seed0):
    """Method B: continuous eighth/quarter motion for the first 4 bars
    of each half, motif present."""
    rng = random.Random(seed0)
    out = []
    cell = [0, 1, 2, 1]
    for i in range(n):
        s = rng.choice([2, 3])
        bars = []
        for b in range(16):
            base = s + (b % 2)
            if b % 8 < 4:
                degs = [base + cell[j % 4] for j in range(6)]
                bars.append(bar(degs, [0.5, 0.5, 1, 0.5, 0.5, 1]))
            else:
                degs = [base + cell[j] for j in range(3)]
                bars.append(bar(degs, [1, 1, 2]))
        out.append(_cadence_fix(bars))
    return out


def d_sequence_only(n, seed0):
    """Natural defect: a one-bar cell sequenced up by step every bar.
    No checker claims to cover this; measured to record the gap."""
    rng = random.Random(seed0)
    out = []
    cell = [0, 2, 1, 2]
    for i in range(n):
        s = rng.choice([0, 1])
        bars = []
        for b in range(16):
            base = s + (b % 5)
            rhy = R["R2"] if b % 2 == 0 else R["R1"]
            degs = [base + cell[j] for j in range(len(rhy))]
            bars.append(bar(degs, rhy))
        out.append(_cadence_fix(bars))
    return out


def h_pitch_shuffle(src, seed0):
    rng = random.Random(seed0)
    out = []
    for t in src:
        t2 = [list(b) for b in t]
        from theme_sheet import pitch_seq
        ps = pitch_seq(t2[:8])
        rng.shuffle(ps)
        k = 0
        for bi in range(8):
            t2[bi] = [(ps[k + j], t2[bi][j][1]) for j in range(len(t2[bi]))]
            k += len(t2[bi])
        out.append(t2)
    return out


def h_rhythm_shuffle(src, seed0):
    rng = random.Random(seed0)
    out = []
    from theme_sheet import dur_seq
    for t in src:
        ds = dur_seq(t)
        rng.shuffle(ds)
        t2, k = [], 0
        for b in t:
            nb = []
            for (p, _) in b:
                nb.append((p, ds[k]))
                k += 1
            t2.append(nb)
        out.append(t2)
    return out


# ------------------------------------------------ external references
def _t(bars_deg):
    return [bar([d for (d, _) in b], [du for (_, du) in b]) for b in bars_deg]


def _twinkle():
    pres = [
        [(0, 1), (0, 1), (4, 1), (4, 1)],
        [(5, 1), (5, 1), (4, 2)],
        [(3, 1), (3, 1), (2, 1), (2, 1)],
        [(1, 1), (1, 1), (0, 2)],
        [(4, 1), (4, 1), (3, 1), (3, 1)],
        [(2, 1), (2, 1), (1, 2)],
        [(4, 1), (4, 1), (3, 1), (3, 1)],
        [(2, 1), (1, 1), (0, 2)],  # presentation close conformed to tonic
    ]
    rep = [list(b) for b in pres]
    rep[7] = [(1, 1), (1, 1), (0, 2)]
    return _t(pres + rep)


def _mary():
    half = [
        [(2, 1), (1, 1), (0, 1), (1, 1)],
        [(2, 1), (2, 1), (2, 2)],
        [(1, 1), (1, 1), (1, 2)],
        [(2, 1), (4, 1), (4, 2)],
        [(2, 1), (1, 1), (0, 1), (1, 1)],
        [(2, 1), (2, 1), (2, 2)],
        [(1, 1), (1, 1), (2, 1), (1, 1)],
        [(0, 4)],
    ]
    return _t(half + [list(b) for b in half])


def _frere():
    half = [
        [(0, 1), (1, 1), (2, 1), (0, 1)],
        [(0, 1), (1, 1), (2, 2)],
        [(2, 1), (3, 1), (4, 2)],
        [(2, 1), (3, 1), (4, 2)],
        [(4, .5), (5, .5), (4, .5), (3, .5), (2, 1), (0, 1)],
        [(4, .5), (5, .5), (4, .5), (3, .5), (2, 1), (0, 1)],
        [(0, 1), (-3, 1), (0, 2)],
        [(0, 1), (-3, 1), (0, 2)],
    ]
    return _t(half + [list(b) for b in half])


def _ode():
    pres = [
        [(2, 1), (2, 1), (3, 1), (4, 1)],
        [(4, 1), (3, 1), (2, 2)],
        [(0, 1), (0, 1), (1, 1), (2, 1)],
        [(2, 1), (1, 1), (1, 2)],
        [(2, 1), (2, 1), (3, 1), (4, 1)],
        [(4, 1), (3, 1), (2, 2)],
        [(0, 1), (0, 1), (1, 1), (2, 1)],
        [(1, 1), (0, 1), (0, 2)],
    ]
    rep = [
        [(1, 2), (2, 1), (0, 1)],
        [(1, 1), (2, .5), (3, .5), (2, 1), (0, 1)],
        [(1, 1), (2, .5), (3, .5), (2, 2)],
        [(0, 1), (1, 1), (-3, 2)],
        [(2, 1), (2, 1), (3, 1), (4, 1)],
        [(4, 1), (3, 1), (2, 2)],
        [(0, 1), (0, 1), (1, 1), (2, 1)],
        [(1, 1), (0, 1), (0, 2)],
    ]
    return _t(pres + rep)


def _jingle():
    pres = [
        [(2, 1), (2, 1), (2, 2)],
        [(2, 1), (2, 1), (2, 2)],
        [(2, 1), (4, 1), (0, 1), (1, 1)],
        [(2, 4)],
        [(3, 1), (3, 1), (3, 1), (3, 1)],
        [(3, 1), (2, 1), (2, 2)],
        [(2, 1), (1, 1), (1, 1), (2, 1)],
        [(1, 2), (4, 2)],
    ]
    rep = [
        [(2, 1), (2, 1), (2, 2)],
        [(2, 1), (2, 1), (2, 2)],
        [(2, 1), (4, 1), (0, 1), (1, 1)],
        [(2, 4)],
        [(3, 1), (3, 1), (3, 1), (3, 1)],
        [(3, 1), (2, 1), (2, 2)],
        [(1, 1), (1, 1), (2, 1), (1, 1)],
        [(0, 4)],
    ]
    return _t(pres + rep)


FAMOUS = {"twinkle": _twinkle, "mary": _mary, "frere": _frere,
          "ode": _ode, "jingle": _jingle}


def _scale_practice_1():
    half = [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(4, 1), (5, 1), (6, 2)],
        [(6, 1), (5, 1), (4, 1), (3, 1)],
        [(2, 1), (1, 1), (0, 2)],
        [(0, .5), (1, .5), (2, 1), (3, 2)],
        [(4, 1), (3, 1), (2, 2)],
        [(1, 1), (2, 1), (3, 1), (4, 1)],
        [(4, 1), (2, 1), (4, 2)],  # presentation ends on dominant degree 4
    ]
    rep = [list(b) for b in half]
    rep[7] = [(1, 1), (1, 1), (0, 2)]
    return _t(half + rep)


def _scale_practice_2():
    half = [
        [(7, 1), (6, 1), (5, 1), (4, 1)],
        [(3, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(4, 2), (4, 2)],
        [(5, .5), (4, .5), (3, 1), (2, 2)],
        [(2, 1), (3, 1), (4, 2)],
        [(5, 1), (4, 1), (3, 1), (2, 1)],
        [(2, 1), (4, 1), (4, 2)],
    ]
    rep = [list(b) for b in half]
    rep[7] = [(1, 1), (1, 1), (0, 2)]
    return _t(half + rep)


def _sequence_only_1():
    cell = [0, 2, 1, 2]
    rhys = [[1, 1, 1, 1], [1, 1, 2], [.5, .5, 1, 2], [1, 1, 2]]
    half = []
    for b in range(8):
        base = b % 4
        rhy = rhys[b % 4]
        degs = [base + cell[j % 4] for j in range(len(rhy))]
        half.append([(degs[j], rhy[j]) for j in range(len(rhy))])
    half[7] = [(3, 1), (4, 1), (4, 2)]
    rep = [list(b) for b in half]
    rep[7] = [(1, 1), (1, 1), (0, 2)]
    return _t(half + rep)


def _sequence_only_2():
    cell = [0, 1, 3, 1]
    rhys = [[1, 1, 2], [1, 1, 1, 1], [1, 1, 2], [.5, .5, 1, 2]]
    half = []
    for b in range(8):
        base = (b * 2) % 5
        rhy = rhys[b % 4]
        degs = [base + cell[j % 4] for j in range(len(rhy))]
        half.append([(degs[j], rhy[j]) for j in range(len(rhy))])
    half[7] = [(2, 1), (4, 1), (4, 2)]
    rep = [list(b) for b in half]
    rep[7] = [(1, 1), (1, 1), (0, 2)]
    return _t(half + rep)


MEDIOCRE = {"scale_practice_1": _scale_practice_1,
            "scale_practice_2": _scale_practice_2,
            "sequence_only_1": _sequence_only_1,
            "sequence_only_2": _sequence_only_2}
