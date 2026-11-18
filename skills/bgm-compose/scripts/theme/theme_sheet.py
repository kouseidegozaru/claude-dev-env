"""Theme sheet data model.

A theme sheet is a monophonic theme of 16 bars (8 bars presentation +
8 bars reprise), written in scale degrees relative to the tonic, so the
same sheet can be checked in a normalized key and arranged in any key.

JSON format (theme-sheet/1):
{
  "format": "theme-sheet/1",
  "mode": "major",
  "bars": [[[degree, beats], ...], ...]   // exactly 16 bars, each sums to 4 beats
}

degree: diatonic scale degree, 0 = tonic (one octave below 7), may be
negative or >= 7 for other octaves. Pitch conversion for the normalized
key (tonic = MIDI 60): pitch = 60 + 12 * (degree // 7) + SCALE[degree % 7].
Only major mode is calibrated; see SKILL.md for scope.
"""
import json

SCALE = [0, 2, 4, 5, 7, 9, 11]  # major scale semitone offsets
BASE_PITCH = 60  # normalized tonic (C4)


def degree_to_pitch(degree, base=BASE_PITCH):
    return base + 12 * (degree // 7) + SCALE[degree % 7]


def pitch_to_degree(pitch, base=BASE_PITCH):
    inv = {v: k for k, v in enumerate(SCALE)}
    rel = pitch - base
    return (rel // 12) * 7 + inv[rel % 12]


def sheet_to_bars(sheet):
    """JSON sheet -> list of 16 bars of (pitch, beats) in the normalized key."""
    bars = []
    for bar in sheet["bars"]:
        bars.append([(degree_to_pitch(int(d)), float(du)) for d, du in bar])
    return bars


def bars_to_sheet(bars, mode="major"):
    """List of bars of (pitch, beats) -> JSON sheet (degrees)."""
    return {
        "format": "theme-sheet/1",
        "mode": mode,
        "bars": [[[pitch_to_degree(p), du] for (p, du) in bar] for bar in bars],
    }


def validate_sheet(sheet):
    """Structural validation. Returns a list of problems (empty = valid)."""
    problems = []
    if sheet.get("format") != "theme-sheet/1":
        problems.append("format is not theme-sheet/1")
    if sheet.get("mode", "major") != "major":
        problems.append("only major mode is supported by the calibrated checkers")
    bars = sheet.get("bars")
    if not isinstance(bars, list) or len(bars) != 16:
        problems.append("bars must be a list of exactly 16 bars")
        return problems
    for i, bar in enumerate(bars):
        if not bar:
            problems.append(f"bar {i + 1} is empty")
            continue
        total = sum(float(du) for _, du in bar)
        if abs(total - 4.0) > 1e-6:
            problems.append(f"bar {i + 1} durations sum to {total}, not 4 beats")
        for d, du in bar:
            if float(du) <= 0:
                problems.append(f"bar {i + 1} has a non-positive duration")
    return problems


def load_sheet(path):
    with open(path) as f:
        sheet = json.load(f)
    problems = validate_sheet(sheet)
    if problems:
        raise ValueError(f"invalid theme sheet {path}: " + "; ".join(problems))
    return sheet


def save_sheet(sheet, path):
    problems = validate_sheet(sheet)
    if problems:
        raise ValueError("invalid theme sheet: " + "; ".join(problems))
    with open(path, "w") as f:
        json.dump(sheet, f, indent=1)


def pitch_seq(bars):
    return [p for b in bars for (p, _) in b]


def dur_seq(bars):
    return [du for b in bars for (_, du) in b]


def format_bars_text(bars):
    """Human/selector-readable notation: degree(duration) per bar."""
    lines = []
    for i, b in enumerate(bars):
        cells = " ".join(f"{pitch_to_degree(p)}({du:g})" for (p, du) in b)
        lines.append(f"  bar{i + 1:02d}: {cells}")
    return "\n".join(lines)
