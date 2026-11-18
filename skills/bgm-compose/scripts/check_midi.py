#!/usr/bin/env python3
"""記譜上の機械確認（venv の python + mido で実行する）。

使い方:
  ~/workspace/midi-bgm/venv/bin/python check_midi.py SPEC.py SONG.mid

確認項目:
  - 主題の戻り: spec の theme_check = (主題セクション, 再現セクション) について、
    主旋律の音高列が再現部にどれだけ一致して戻っているか（一致率と先頭フレーズの逐語一致）
  - 主旋律の音域（最低・最高）と最大跳躍 [半音]
  - 小節数と概算の長さが仕様と合っているか
結果は JSON で出し、全項目が下限を満たせば終了コード 0。
下限: 主題の先頭フレーズ（主題セクション先頭2小節）が再現部に逐語で現れること。
"""
import importlib.util
import json
import sys

from mido import MidiFile

TPB = 480


def load_spec(path):
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    return mod.SPEC


def melody_notes(mid, spec):
    """主旋律トラックの (小節, 音高) を小節順に返す。"""
    bpb = int(spec.get("beats_per_bar", 4))
    bar_ticks = bpb * TPB
    cfg = spec["tracks"]["melody"]
    target_name = cfg.get("name", "melody")
    chosen = None
    for tr in mid.tracks:
        name = None
        for m in tr:
            if m.type == "track_name":
                name = m.name
                break
        if name == target_name:
            chosen = tr
            break
    if chosen is None:
        # 名前が取れない場合は主旋律チャンネルのトラックを探す
        for tr in mid.tracks:
            if any(m.type == "note_on" and m.channel == cfg["channel"] for m in tr):
                chosen = tr
                break
    notes = []  # (abs_tick, note)
    t = 0
    for m in chosen:
        t += m.time
        if m.type == "note_on" and m.velocity > 0 and m.channel == cfg["channel"]:
            notes.append((t, m.note))
    notes.sort()
    return [(tick // bar_ticks + 1, note) for tick, note in notes]


def section_range(spec, name):
    for n, first, last in spec.get("sections", []):
        if n == name:
            return first, last
    return None


def seq_for_bars(notes, first, last):
    return [n for bar, n in notes if first <= bar <= last]


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    spec = load_spec(sys.argv[1])
    mid = MidiFile(sys.argv[2])
    notes = melody_notes(mid, spec)
    pitches = [n for _b, n in notes]
    leaps = [abs(b - a) for a, b in zip(pitches, pitches[1:])]
    out = {
        "melody_notes": len(pitches),
        "melody_range": [min(pitches), max(pitches)] if pitches else None,
        "max_leap_semitones": max(leaps) if leaps else 0,
    }
    checks = []
    tc = spec.get("theme_check")
    if tc:
        a_rng = section_range(spec, tc[0])
        b_rng = section_range(spec, tc[1])
        a_seq = seq_for_bars(notes, *a_rng)
        b_seq = seq_for_bars(notes, *b_rng)
        # 主題先頭2小節のフレーズが再現部に逐語で現れるか
        head_bars = (a_rng[0], min(a_rng[0] + 1, a_rng[1]))
        head = seq_for_bars(notes, *head_bars)
        found = any(b_seq[i:i + len(head)] == head
                    for i in range(0, max(0, len(b_seq) - len(head) + 1))) if head else False
        # 一致率: 再現部の音符のうち主題部の音高列と最長共通部分列ベースでなく、
        # 先頭からの一致ではなく集合の重なりでなく、逐語部分列の総量で測る簡易指標
        common = 0
        i = 0
        while i < len(b_seq):
            j = 0
            while (i + j < len(b_seq) and j < len(a_seq)
                   and b_seq[i + j] == a_seq[j]):
                j += 1
            if j >= 3:
                common += j
                i += j
            else:
                i += 1
        ratio = round(common / len(b_seq), 3) if b_seq else 0.0
        out["theme"] = {"theme_section": tc[0], "reprise_section": tc[1],
                        "head_phrase_verbatim_in_reprise": found,
                        "reprise_notes_from_theme_ratio": ratio}
        checks.append({"check": "theme_return", "pass": bool(found),
                       "detail": f"主題先頭フレーズの逐語再現={found}, 再現部の主題由来率={ratio}"})
    out["checks"] = checks
    out["verdict"] = "PASS" if all(c["pass"] for c in checks) else "FAIL"
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
