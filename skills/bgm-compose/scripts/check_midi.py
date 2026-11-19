#!/usr/bin/env python3
"""記譜上の機械確認（イベント列基盤・二層ゲート版）。

使い方:
  ~/workspace/midi-bgm/venv/bin/python check_midi.py SPEC.py SONG.mid

設計:
  - 全チェックは MIDI から抽出したイベント列
    (onset, pitch, dur, rest, velocity) だけを見る。
  - 二層構造: ハード FAIL は「全チェック通過でも試聴で却下された実例」
    型の失敗に直結する少数に限る（音階断片、小節リズム型の種類/占有率、
    対比軸、モチーフ未定義、署名スロット空、B の再現部化、主題の戻り）。
    分布に依存する指標は WARN + 記録で開始し、裏付けが取れたものだけ
    後から FAIL へ昇格させる。
  - spec に宣言が無い項目は SKIP とする。納品条件は FAIL 0 件 + SKIP 0 件
    （verdict == "PASS"）。WARN は理由を曲のメモに残した場合のみ可。
  - 宣言→実物の照合（cadence_table / harmony_events）は次段階の担当で、
    ここでは宣言の有無だけを見て pending_verification に記録する。
  - 閾値は check_thresholds.json に分離する（版数と変更履歴つき）。
  - FAIL / WARN には処方（直し方）を必ず文字列で付ける。
"""
import importlib.util
import json
import math
import os
import sys
from collections import Counter

from mido import MidiFile

TPB = 480
GRID = TPB // 4  # 16分音符グリッド（ヒューマナイズの揺れを吸収する）

_DEFAULT_THRESHOLDS = {
    "rhythm_occupancy_warn": 0.40,
    "rhythm_occupancy_fail": 0.55,
    "rhythm_run_warn": 3,
    "rhythm_short_scope_bars": 12,
    "stepwise_theme_band": [0.55, 0.80],
    "stepwise_whole_band": [0.60, 0.85],
    "stepwise_warn": 0.85,
    "stepwise_composite": 0.90,
    "compression_warn": 0.75,
    "contrast_axes_min": 2,
    "density_ratio_axis": 1.40,
    "vel_axis": 16,
    "vel_axis_weak": 8,
    "pitch_axis_semitones": 3,
    "range_width_axis_semitones": 4,
    "harmonic_rhythm_axis": 2.0,
    "rest_ratio_axis": 0.15,
    "csim_band": [0.60, 0.95],
    "b_reprise_margin": 0.05,
    "b_reprise_fallback_jaccard": 0.35,
    "b_reprise_fallback_chroma": 0.95,
    "fragment_run_head_min": 4,
    "fragment_run_section_min": 5,
    "motif_recurrence_min": 3,
    "peak_position_band": [0.55, 0.80],
    "melody_range_max_semitones": 16,
    "melody_iqr_min_semitones": 4,
    "large_leap_semitones": 8,
    "leap_density_per_8bars": 3,
    "energy_density_tol": 0.20,
    "energy_pitch_tol": 2,
    "energy_vel_tol": 7,
    "bar_start_align_warn_ms": 15.0,
}


def load_thresholds():
    th = dict(_DEFAULT_THRESHOLDS)
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "check_thresholds.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        for k, v in data.items():
            if k not in ("version", "history"):
                th[k] = v
    except Exception:  # noqa: BLE001 - 閾値ファイルが無くても既定値で動く
        pass
    return th


TH = load_thresholds()


# ---------------------------------------------------------------- spec

def load_spec(path):
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    spec = getattr(mod, "SPEC", None)
    if spec is None:
        raise ValueError("spec に SPEC 辞書がありません")
    return spec


def section_range(spec, name):
    for n, first, last in spec.get("sections", []):
        if n == name:
            return first, last
    return None


def section_of_bar(spec, bar):
    for name, first, last in spec.get("sections", []):
        if first <= bar <= last:
            return name
    return None


def theme_sections(spec):
    """(主題セクション名, 再現セクション名 or None) を返す。"""
    tc = spec.get("theme_check")
    if tc:
        return tc[0], (tc[1] if len(tc) > 1 else None)
    for name, _f, _l in spec.get("sections", []):
        if name.lower() not in ("intro", "outro"):
            return name, None
    secs = spec.get("sections", [])
    return (secs[0][0], None) if secs else (None, None)


# ---------------------------------------------------------------- events

def _find_track(mid, cfg):
    target_name = cfg.get("name")
    if target_name:
        for tr in mid.tracks:
            for m in tr:
                if m.type == "track_name":
                    if m.name == target_name:
                        return tr
                    break
    for tr in mid.tracks:
        if any(m.type == "note_on" and m.velocity > 0
               and getattr(m, "channel", None) == cfg.get("channel")
               for m in tr):
            return tr
    return None


def _q(ticks):
    return int(round(ticks / GRID)) * GRID


def extract_events(mid, spec):
    """全トラックのイベント列を抽出する。

    戻り値: {track_key: [ {onset_tick, onset_beat, pitch, dur_beats,
             vel, bar, beat_in_bar, rest_after_beats} ]}
    onset / dur は16分グリッドへ量子化する（生成時の微小な揺れを吸収）。
    """
    bpb = int(spec.get("beats_per_bar", 4))
    out = {}
    for key, cfg in spec.get("tracks", {}).items():
        tr = _find_track(mid, cfg)
        if tr is None:
            out[key] = []
            continue
        channel = cfg.get("channel")
        ons = {}  # pitch -> [(tick, vel)]
        notes = []
        t = 0
        for m in tr:
            t += m.time
            if getattr(m, "channel", None) != channel:
                continue
            if m.type == "note_on" and m.velocity > 0:
                ons.setdefault(m.note, []).append((t, m.velocity))
            elif m.type == "note_off" or (m.type == "note_on"
                                          and m.velocity == 0):
                if ons.get(m.note):
                    st, vel = ons[m.note].pop(0)
                    notes.append((st, m.note, max(1, t - st), vel))
        for pitch, lst in ons.items():
            for st, vel in lst:
                notes.append((st, pitch, GRID, vel))
        evs = []
        for st, pitch, dur, vel in sorted(notes):
            o_beat = _q(st) / TPB
            evs.append({
                "onset_tick": st,
                "onset_beat": o_beat,
                "pitch": pitch,
                "dur_beats": max(0.25, _q(dur) / TPB),
                "vel": vel,
                "bar": int(o_beat // bpb) + 1,
                "beat_in_bar": o_beat % bpb,
            })
        evs.sort(key=lambda e: (e["onset_beat"], e["pitch"]))
        for i, ev in enumerate(evs):
            if i + 1 < len(evs):
                gap = evs[i + 1]["onset_beat"] - (ev["onset_beat"]
                                                  + ev["dur_beats"])
                ev["rest_after_beats"] = max(0.0, gap)
            else:
                ev["rest_after_beats"] = 0.0
        out[key] = evs
    return out


def clean_melody_events(spec, melody_evs):
    """spec の extra_notes 宣言に一致するイベントを主旋律から分離する。

    戻り値: (主旋律イベント列, 分離した件数)
    """
    extra = spec.get("extra_notes", {}).get("melody", {})
    if not extra:
        return melody_evs, 0
    bpb = int(spec.get("beats_per_bar", 4))
    keys = set()
    for bar, notes in extra.items():
        for tup in notes:
            keys.add((int(bar), int(tup[0]), int(round(float(tup[1]) * 4))))
    kept, removed = [], 0
    for ev in melody_evs:
        k = (ev["bar"], ev["pitch"],
             int(round((ev["onset_beat"] - (ev["bar"] - 1) * bpb) * 4)))
        if k in keys:
            removed += 1
        else:
            kept.append(ev)
    return kept, removed


# ---------------------------------------------------------------- scale

class Scale:
    """音階ステップ差で数えるための文脈（半音基準は使わない）。"""

    def __init__(self, spec):
        pcs = spec.get("scale_pcs")
        tonic = spec.get("tonic_pc")
        self.ok = bool(pcs) and tonic is not None
        self.pcs = set(pcs) if pcs else set()
        self.tonic = tonic
        if self.ok:
            ordered = sorted(self.pcs, key=lambda pc: (pc - tonic) % 12)
            self.deg_pc = {pc: i + 1 for i, pc in enumerate(ordered)}

    def step_diff(self, a, b):
        """a -> b の音階ステップ差。どちらかが音階外なら None。"""
        if not self.ok or a % 12 not in self.pcs or b % 12 not in self.pcs:
            return None
        if a == b:
            return 0
        lo, hi = (a, b) if a < b else (b, a)
        cnt = sum(1 for x in range(lo + 1, hi + 1) if x % 12 in self.pcs)
        return cnt if b > a else -cnt

    def degree(self, pitch):
        if not self.ok or pitch % 12 not in self.pcs:
            return None
        return self.deg_pc[pitch % 12]


def steps_of(evs, scale):
    """連続音の音階ステップ差列（音階外が絡む区間は None）。"""
    return [scale.step_diff(a["pitch"], b["pitch"])
            for a, b in zip(evs, evs[1:])]


def stepwise_rate(evs, scale):
    sts = [s for s in steps_of(evs, scale) if s is not None]
    if not sts:
        return None
    return sum(1 for s in sts if abs(s) == 1) / len(sts)


# ---------------------------------------------------------------- helpers

def item(check, status, detail, remedy=""):
    return {"check": check, "status": status, "detail": detail,
            "remedy": remedy}


def evs_in_bars(evs, first, last):
    return [e for e in evs if first <= e["bar"] <= last]


def rhythm_signature(evs_one_bar, bpb):
    sig = []
    for ev in evs_one_bar:
        off = round((ev["onset_beat"] - (ev["bar"] - 1) * bpb) * 4) / 4
        sig.append((off, round(ev["dur_beats"] * 4) / 4))
    return tuple(sorted(sig))


def section_stats(spec, all_events, melody, name):
    """セクションの実測統計（対比軸と energy_arc 照合の共通材料）。"""
    rng = section_range(spec, name)
    if rng is None:
        return None
    first, last = rng
    nbars = last - first + 1
    total = sum(1 for evs in all_events.values()
                for e in evs if first <= e["bar"] <= last)
    parts = sum(1 for evs in all_events.values()
                if any(first <= e["bar"] <= last for e in evs))
    mev = evs_in_bars(melody, first, last)
    prog = spec.get("progression", [])
    seg = 1
    for bar in range(first + 1, last + 1):
        if 0 <= bar - 1 < len(prog) and 0 <= bar - 2 < len(prog) \
                and prog[bar - 1] != prog[bar - 2]:
            seg += 1
    if mev:
        pitches = [e["pitch"] for e in mev]
        durs = Counter(round(e["dur_beats"] * 4) / 4 for e in mev)
        top_val = min(durs.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        sounding = sum(e["dur_beats"] for e in mev)
        mean_pitch = sum(pitches) / len(pitches)
        mean_vel = sum(e["vel"] for e in mev) / len(mev)
        width = max(pitches) - min(pitches)
        rest = max(0.0, 1.0 - sounding / (nbars * int(
            spec.get("beats_per_bar", 4))))
    else:
        top_val, mean_pitch, mean_vel, width, rest = None, 0.0, 0.0, 0, 1.0
    return {"bars": nbars, "density": total / nbars if nbars else 0.0,
            "parts": parts, "mean_pitch": mean_pitch,
            "range_width": width, "vel": mean_vel, "rest_ratio": rest,
            "harmonic_rhythm": seg / nbars if nbars else 0.0,
            "top_note_value": top_val, "melody_notes": len(mev)}


def contrast_axes(sx, sy):
    """隣接セクション間の対比軸を数える。戻り値: (軸の名前列, 弱い変化列)"""
    axes, weak = [], []
    if sx["density"] > 0 and sy["density"] > 0:
        r = max(sx["density"], sy["density"]) / min(sx["density"],
                                                    sy["density"])
        if r >= TH["density_ratio_axis"]:
            axes.append("密度")
    if abs(sx["parts"] - sy["parts"]) >= 1:
        axes.append("パート数")
    if abs(sx["mean_pitch"] - sy["mean_pitch"]) >= TH["pitch_axis_semitones"] \
            or abs(sx["range_width"] - sy["range_width"]) \
            >= TH["range_width_axis_semitones"]:
        axes.append("音高")
    dv = abs(sx["vel"] - sy["vel"])
    if dv >= TH["vel_axis"]:
        axes.append("vel")
    elif dv >= TH["vel_axis_weak"]:
        weak.append("vel(弱)")
    hx, hy = sx["harmonic_rhythm"], sy["harmonic_rhythm"]
    if hx > 0 and hy > 0:
        r = max(hx, hy) / min(hx, hy)
        if r >= TH["harmonic_rhythm_axis"]:
            axes.append("和声リズム")
    if sx.get("top_note_value") is not None \
            and sy.get("top_note_value") is not None \
            and sx["top_note_value"] != sy["top_note_value"]:
        axes.append("音価")
    if abs(sx["rest_ratio"] - sy["rest_ratio"]) >= TH["rest_ratio_axis"]:
        axes.append("休符率")
    return axes, weak


# ---------------------------------------------------------------- similarity

def _cosine(ha, hb):
    keys = set(ha) | set(hb)
    dot = sum(ha.get(k, 0) * hb.get(k, 0) for k in keys)
    na = math.sqrt(sum(v * v for v in ha.values()))
    nb = math.sqrt(sum(v * v for v in hb.values()))
    return dot / (na * nb) if na and nb else 0.0


def similarity(evs_a, evs_b):
    """材料類似度 CS = クロマcosine + 音程3gram Jaccard + 音価cosine の複合。"""
    ca = Counter(e["pitch"] % 12 for e in evs_a)
    cb = Counter(e["pitch"] % 12 for e in evs_b)
    chroma = _cosine(ca, cb)

    def trigrams(evs):
        iv = [b["pitch"] - a["pitch"] for a, b in zip(evs, evs[1:])]
        return {(iv[i], iv[i + 1]) for i in range(len(iv) - 1)}

    ta, tb = trigrams(evs_a), trigrams(evs_b)
    jacc = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    da = Counter(round(e["dur_beats"] * 4) / 4 for e in evs_a)
    db = Counter(round(e["dur_beats"] * 4) / 4 for e in evs_b)
    durc = _cosine(da, db)
    return {"cs": (chroma + jacc + durc) / 3, "chroma": chroma,
            "jaccard": jacc, "duration_cos": durc}


def motif_occurrences(evs, scale, motif):
    """主題セクション内のモチーフ出現回数（音程列+リズム比の近似マッチ）。"""
    intervals = motif.get("intervals", [])
    rhythm = motif.get("rhythm", [])
    if not intervals or len(rhythm) != len(intervals) + 1:
        return 0
    n = len(intervals) + 1
    count = 0
    i = 0
    while i + n <= len(evs):
        seg = evs[i:i + n]
        sts = [scale.step_diff(a["pitch"], b["pitch"])
               for a, b in zip(seg, seg[1:])]
        if sts == list(intervals):
            base = seg[0]["dur_beats"]
            rbase = rhythm[0]
            if base > 0 and rbase > 0 and all(
                    abs(seg[j]["dur_beats"] / base
                        - rhythm[j] / rbase) < 0.26
                    for j in range(n)):
                count += 1
                i += n
                continue
        i += 1
    return count


def find_intervals_pattern(evs, scale, pattern):
    """音程列パターン（署名）が連続区間として現れるか。"""
    if not pattern:
        return False
    sts = steps_of(evs, scale)
    m = len(pattern)
    return any(sts[i:i + m] == list(pattern)
               for i in range(len(sts) - m + 1))


# ---------------------------------------------------------------- checks

def check_spec_fields(spec):
    """必須フィールド（motif / 署名）と宣言の有無（SKIP 方式）。"""
    out = []
    motif = spec.get("motif")
    if not isinstance(motif, dict) or not motif.get("intervals"):
        out.append(item("motif_undefined", "FAIL",
                        "spec に motif（型付きレコード）が無い",
                        "主題モチーフを音程列/リズム比/輪郭/アンカー/署名で宣言する"))
        out.append(item("signature_slot", "SKIP",
                        "motif が無いため署名スロットを判定できない"))
    else:
        out.append(item("motif_undefined", "OK",
                        f"motif 宣言あり（音程列 {motif.get('intervals')}）"))
        sig = motif.get("signature")
        if not sig:
            out.append(item("signature_slot", "FAIL",
                            "motif.signature が空（署名スロット必須）",
                            "モチーフの中で署名を担う音程列を signature に書く"))
        else:
            iv = list(motif.get("intervals", []))
            is_sub = any(iv[i:i + len(sig)] == list(sig)
                         for i in range(len(iv) - len(sig) + 1))
            if is_sub:
                out.append(item("signature_slot", "OK",
                                f"署名スロット {sig} はモチーフ音程列の一部"))
            else:
                out.append(item("signature_slot", "WARN",
                                f"署名 {sig} がモチーフ音程列の連続部分列でない",
                                "署名はモチーフ音程列の一部として宣言する"))
    for field, label in (("cadence_table", "cadence_table"),
                         ("harmony_events", "harmony_events")):
        if spec.get(field):
            out.append(item(label, "OK",
                            "宣言あり（宣言→実物の照合は次段階で実施）"))
        else:
            out.append(item(label, "SKIP", f"{field} が未宣言"))
    return out


def check_scale_fragment(spec, melody, scale, theme_name):
    """音階断片検出: モチーフ自体、または主題部での順次単調ラン。"""
    motif = spec.get("motif") or {}
    iv = motif.get("intervals") or []
    reasons = []
    if iv and all(abs(s) == 1 for s in iv) \
            and (all(s > 0 for s in iv) or all(s < 0 for s in iv)):
        reasons.append("宣言モチーフ自体が全順次+単調の音階断片")
    if scale.ok and theme_name:
        rng = section_range(spec, theme_name)
        if rng:
            evs = evs_in_bars(melody, rng[0], rng[1])
            sts = steps_of(evs, scale)
            head_end_bar = min(rng[0] + 1, rng[1])
            run = 1
            for idx, s in enumerate(sts):
                prev = sts[idx - 1] if idx > 0 else None
                if s is not None and prev is not None \
                        and abs(s) == 1 and abs(prev) == 1 \
                        and (s > 0) == (prev > 0):
                    run += 1
                elif s is not None and abs(s) == 1:
                    run = 2
                else:
                    run = 1
                if run >= TH["fragment_run_section_min"]:
                    reasons.append(
                        f"主題部に{run}音の順次単調ラン（音階断片）")
                    break
                note_bar = evs[idx + 1]["bar"] if idx + 1 < len(evs) else None
                if note_bar is not None and note_bar <= head_end_bar \
                        and run >= TH["fragment_run_head_min"]:
                    reasons.append(
                        f"主題の頭フレーズに{run}音の順次単調ラン")
                    break
    if reasons:
        return item("scale_fragment", "FAIL", "; ".join(reasons),
                    "モチーフに跳躍や方向転換を入れ、音階の断片でない顔を作る")
    if not iv and not scale.ok:
        return item("scale_fragment", "SKIP",
                    "motif 宣言と scale_pcs の両方が無く判定できない")
    return item("scale_fragment", "OK", "音階断片は検出されなかった")


def check_rhythm(spec, melody):
    """小節リズム型の種類・占有率・最長連続（主題部と曲全体の両方）。"""
    bpb = int(spec.get("beats_per_bar", 4))
    theme_name, _ = theme_sections(spec)
    scopes = []
    if theme_name and section_range(spec, theme_name):
        f, l = section_range(spec, theme_name)
        scopes.append(("主題部", evs_in_bars(melody, f, l), l - f + 1))
    scopes.append(("曲全体", melody, int(spec.get("bars", 0))))
    out = []
    for label, evs, _nbars in scopes:
        by_bar = {}
        for e in evs:
            by_bar.setdefault(e["bar"], []).append(e)
        sigs = [rhythm_signature(by_bar[b], bpb) for b in sorted(by_bar)]
        if len(sigs) < 4:
            out.append((label, "SKIP", len(sigs), None, 0, 0))
            continue
        kinds = len(set(sigs))
        top_sig, top_cnt = Counter(sigs).most_common(1)[0]
        run = 1
        max_run = 1
        for a, b in zip(sigs, sigs[1:]):
            run = run + 1 if a == b else 1
            max_run = max(max_run, run)
        out.append((label, "OK", len(sigs), kinds, top_cnt, max_run))
    details = []
    fail = warn = skip = False
    for label, status, nbars, kinds, top_cnt, max_run in out:
        if status == "SKIP":
            details.append(f"{label}: 小節数不足で判定対象外")
            continue
        share = top_cnt / nbars if nbars else 0.0
        details.append(
            f"{label}: 種類{kinds}/小節{nbars}, 最頻占有{share:.0%}"
            f"({top_cnt}小節), 最長連続{max_run}小節")
        if kinds is not None and kinds <= 2:
            fail = True
            details.append(f"{label}: リズム型の種類が{kinds}種しかない")
        if nbars >= TH["rhythm_short_scope_bars"]:
            if share > TH["rhythm_occupancy_fail"]:
                fail = True
            elif share > TH["rhythm_occupancy_warn"]:
                warn = True
        else:
            if top_cnt > nbars / 2 and top_cnt >= 4:
                fail = True
            elif share > TH["rhythm_occupancy_warn"]:
                warn = True
        if max_run > TH["rhythm_run_warn"]:
            warn = True
    if fail:
        st = "FAIL"
    elif warn:
        st = "WARN"
    elif all(s[1] == "SKIP" for s in out):
        st = "SKIP"
    else:
        st = "OK"
    return item("rhythm_pattern", st, "; ".join(details),
                "小節リズム型を増やし、最頻型の占有を下げる"
                "（音高でなくリズムで変化を作る）")


def check_contrast(spec, all_events, melody):
    """隣接セクションの対比軸。新素材セクションへの遷移は FAIL 判定。"""
    secs = spec.get("sections", [])
    if len(secs) < 2:
        return item("contrast_axes", "OK", "セクションが1つで対象ペアなし")
    deriv = spec.get("section_derivations", {})
    results, fails, warns = [], [], []
    stats = {n: section_stats(spec, all_events, melody, n) for n, _f, _l in secs}
    for (n1, _f1, _l1), (n2, _f2, _l2) in zip(secs, secs[1:]):
        sx, sy = stats.get(n1), stats.get(n2)
        if not sx or not sy:
            continue
        axes, weak = contrast_axes(sx, sy)
        new_material = n2.lower() not in ("intro", "outro") \
            and n2 not in deriv
        tag = f"{n1}→{n2}: 軸{len(axes)} {axes}" + \
            (f" 弱い変化{weak}" if weak else "")
        results.append(tag)
        if len(axes) < TH["contrast_axes_min"]:
            (fails if new_material else warns).append(tag)
    if fails:
        return item("contrast_axes", "FAIL",
                    "; ".join(results) + f" / 対比不足: {fails}",
                    "処方は安い順: パート増減→vel→音高→密度→和声リズム→材料")
    if warns:
        return item("contrast_axes", "WARN", "; ".join(results),
                    "再現部・導入・終結以外の遷移では対比軸2つ以上を確保する")
    return item("contrast_axes", "OK", "; ".join(results))


def check_b_reprise(spec, melody, all_events):
    """B の再現部化: B が A' 以上に A と似ていて対比も無い場合は FAIL。"""
    theme_name, reprise_name = theme_sections(spec)
    secs = [n for n, _f, _l in spec.get("sections", [])]
    if not theme_name or theme_name not in secs:
        return item("b_reprise", "SKIP", "主題セクションが特定できない")
    a_rng = section_range(spec, theme_name)
    a_evs = evs_in_bars(melody, *a_rng)
    a_stats = section_stats(spec, all_events, melody, theme_name)
    idx_a = secs.index(theme_name)
    idx_r = secs.index(reprise_name) if reprise_name in secs else len(secs)
    middles = secs[idx_a + 1:idx_r] if idx_r > idx_a else []
    middles = [m for m in middles if m.lower() not in ("intro", "outro")]
    if reprise_name and reprise_name in secs:
        r_evs = evs_in_bars(melody, *section_range(spec, reprise_name))
        cs_ra = similarity(r_evs, a_evs)["cs"]
        bad = []
        for m in middles:
            m_evs = evs_in_bars(melody, *section_range(spec, m))
            cs_ma = similarity(m_evs, a_evs)["cs"]
            m_stats = section_stats(spec, all_events, melody, m)
            axes, _w = contrast_axes(a_stats, m_stats)
            if cs_ma >= cs_ra - TH["b_reprise_margin"] \
                    and len(axes) < TH["contrast_axes_min"]:
                bad.append(f"{m}: CS({m},A)={cs_ma:.3f} >= "
                           f"CS(A',A)-0.05={cs_ra - TH['b_reprise_margin']:.3f}"
                           f" かつ対比軸{len(axes)}")
        if bad:
            return item("b_reprise", "FAIL", "; ".join(bad),
                        "B には A と違う材料か対比軸を持たせ、再現部にしない")
        return item("b_reprise", "OK",
                    f"CS(A',A)={cs_ra:.3f}; 中間セクションの再現部化なし")
    # 再現部が無い形式へのフォールバック
    warns = []
    for m in middles:
        m_evs = evs_in_bars(melody, *section_range(spec, m))
        sim = similarity(m_evs, a_evs)
        if sim["jaccard"] >= TH["b_reprise_fallback_jaccard"] \
                and sim["chroma"] >= TH["b_reprise_fallback_chroma"]:
            warns.append(f"{m}: Jaccard={sim['jaccard']:.2f}, "
                         f"chroma={sim['chroma']:.2f}")
    if warns:
        return item("b_reprise", "WARN",
                    "再現部が無い形式で A と酷似するセクション: "
                    + "; ".join(warns),
                    "中間セクションには対比を持たせる")
    return item("b_reprise", "OK", "対象なし、または酷似セクションなし")


def check_stepwise(spec, melody, scale, compression):
    if not scale.ok:
        return item("stepwise_rate", "SKIP", "scale_pcs が未宣言")
    theme_name, _ = theme_sections(spec)
    parts = []
    status = "OK"
    theme_rate = None
    if theme_name and section_range(spec, theme_name):
        theme_rate = stepwise_rate(
            evs_in_bars(melody, *section_range(spec, theme_name)), scale)
    whole_rate = stepwise_rate(melody, scale)
    if theme_rate is not None:
        lo, hi = TH["stepwise_theme_band"]
        parts.append(f"主題部 順次率 {theme_rate:.0%}（帯 {lo:.0%}-{hi:.0%}・仮置き）")
        if theme_rate < lo or theme_rate > hi:
            status = "WARN"
        if theme_rate > TH["stepwise_warn"]:
            status = "WARN"
            parts.append("85%超のため WARN")
        if theme_rate >= TH["stepwise_composite"] and compression is not None \
                and compression >= TH["compression_warn"]:
            status = "FAIL"
            parts.append("90%以上かつ圧縮率も上限側のため複合 FAIL")
    if whole_rate is not None:
        lo, hi = TH["stepwise_whole_band"]
        parts.append(f"曲全体 順次率 {whole_rate:.0%}（帯 {lo:.0%}-{hi:.0%}・仮置き）")
        if whole_rate < lo or whole_rate > hi:
            status = "WARN" if status == "OK" else status
    return item("stepwise_rate", status, "; ".join(parts),
                "順次過多は音高でなくリズムの変化で外す")


def check_motif_recurrence(spec, melody, scale):
    motif = spec.get("motif")
    if not isinstance(motif, dict) or not motif.get("intervals"):
        return item("motif_recurrence", "SKIP", "motif が未宣言")
    if not scale.ok:
        return item("motif_recurrence", "SKIP", "scale_pcs が未宣言")
    theme_name, _ = theme_sections(spec)
    if not theme_name or not section_range(spec, theme_name):
        return item("motif_recurrence", "SKIP", "主題セクションが無い")
    evs = evs_in_bars(melody, *section_range(spec, theme_name))
    cnt = motif_occurrences(evs, scale, motif)
    if cnt < TH["motif_recurrence_min"]:
        return item("motif_recurrence", "WARN",
                    f"主題部でのモチーフ出現 {cnt} 回（最低 "
                    f"{TH['motif_recurrence_min']} 回）",
                    "主題セクション内でモチーフを3回以上、形を変えながら出す")
    return item("motif_recurrence", "OK", f"主題部でのモチーフ出現 {cnt} 回")


def check_signature_reprise(spec, melody, scale):
    motif = spec.get("motif")
    if not isinstance(motif, dict) or not motif.get("signature"):
        return item("signature_in_reprise", "SKIP", "署名が未宣言")
    if not scale.ok:
        return item("signature_in_reprise", "SKIP", "scale_pcs が未宣言")
    _t, reprise_name = theme_sections(spec)
    if not reprise_name or not section_range(spec, reprise_name):
        return item("signature_in_reprise", "OK", "再現部が無いため対象外")
    evs = evs_in_bars(melody, *section_range(spec, reprise_name))
    if find_intervals_pattern(evs, scale, list(motif["signature"])):
        return item("signature_in_reprise", "OK", "署名が再現部にも現れる")
    return item("signature_in_reprise", "WARN",
                "署名が再現部に現れない",
                "再現部には署名スロットの音程列を必ず含める")


def check_csim(spec, melody):
    theme_name, reprise_name = theme_sections(spec)
    secs = [n for n, _f, _l in spec.get("sections", [])]
    if not theme_name or theme_name not in secs:
        return item("csim_band", "SKIP", "主題セクションが特定できない")
    a_evs = evs_in_bars(melody, *section_range(spec, theme_name))
    lo, hi = TH["csim_band"]
    msgs, bad = [], []
    for name in secs:
        if name == theme_name:
            continue
        evs = evs_in_bars(melody, *section_range(spec, name))
        if not evs:
            continue
        cs = similarity(evs, a_evs)["cs"]
        msgs.append(f"CS({name},A)={cs:.3f}")
        if name == reprise_name and not (lo <= cs <= hi):
            bad.append(f"{name} の CS が帯 {lo}-{hi} の外")
    if bad:
        return item("csim_band", "WARN", "; ".join(msgs) + " / " + "; ".join(bad),
                    "再現部の変形同一性は CS 0.6-0.95 の帯に収める")
    return item("csim_band", "OK", "; ".join(msgs) if msgs else "比較対象なし")


def check_peak(spec, melody):
    if not melody:
        return item("peak_note", "SKIP", "主旋律が空")
    peak = max(e["pitch"] for e in melody)
    idxs = [i for i, e in enumerate(melody) if e["pitch"] == peak]
    total_beats = int(spec.get("bars", 0)) * int(spec.get("beats_per_bar", 4))
    durs = sorted(e["dur_beats"] for e in melody)
    median = durs[len(durs) // 2] if durs else 0
    bpb = int(spec.get("beats_per_bar", 4))
    lo, hi = TH["peak_position_band"]
    fails = []
    if len(idxs) != 1:
        fails.append(f"最高音の回数 {len(idxs)} 回（1回のみが条件）")
    i0 = idxs[0]
    ev = melody[i0]
    pos_n = i0 / max(1, len(melody) - 1)
    pos_t = ev["onset_beat"] / total_beats if total_beats else 0
    if not (lo <= pos_n <= hi and lo <= pos_t <= hi):
        fails.append(f"最高音の位置 音符基準{pos_n:.0%}/時刻基準{pos_t:.0%}"
                     f"（帯 {lo:.0%}-{hi:.0%}）")
    strong = abs(ev["beat_in_bar"] - round(ev["beat_in_bar"])) < 1e-6 \
        and int(round(ev["beat_in_bar"])) in (0, bpb // 2)
    if not strong:
        fails.append("最高音が強拍に無い")
    if ev["dur_beats"] < 1.5 * median:
        fails.append(f"最高音の音価 {ev['dur_beats']} が中央値の1.5倍未満")
    if fails:
        return item("peak_note", "WARN",
                    f"最高音 {peak}: " + "; ".join(fails),
                    "最高音は1回だけ、後半の山場で、強拍に長めに置く")
    return item("peak_note", "OK",
                f"最高音 {peak} は1回・位置{pos_t:.0%}・強拍・長音価を満たす")


def check_leaps(spec, melody):
    if len(melody) < 2:
        return item("leap_density", "SKIP", "主旋律が短い")
    leaps = [abs(b["pitch"] - a["pitch"])
             for a, b in zip(melody, melody[1:])]
    big = [x for x in leaps if x >= TH["large_leap_semitones"]]
    bars_with = len({e["bar"] for e in melody})
    per8 = len(big) / max(1, bars_with) * 8
    consec = any(a >= TH["large_leap_semitones"]
                 and b >= TH["large_leap_semitones"]
                 for a, b in zip(leaps, leaps[1:]))
    msgs = [f"大跳躍（{TH['large_leap_semitones']}半音以上）{len(big)}回"
            f"、8小節あたり {per8:.1f} 回"]
    bad = per8 > TH["leap_density_per_8bars"] or consec
    if consec:
        msgs.append("大跳躍が連続している")
    if bad:
        return item("leap_density", "WARN", "; ".join(msgs),
                    "大跳躍は8小節に3回まで、連続させない")
    return item("leap_density", "OK", "; ".join(msgs))


def check_phrase_endings(spec, melody, scale):
    bpb = int(spec.get("beats_per_bar", 4))
    secs = spec.get("sections", [])
    if not secs or len(melody) < 4:
        return item("phrase_endings", "SKIP", "材料不足")
    phrases = []  # (末音イベント, 実効末長)
    for _name, first, last in secs:
        bar = first
        while bar <= last:
            pend = min(bar + 1, last)
            evs = evs_in_bars(melody, bar, pend)
            if evs:
                last_ev = evs[-1]
                nxt = None
                for e in melody:
                    if e["onset_beat"] > last_ev["onset_beat"]:
                        nxt = e
                        break
                end_beat = (nxt["onset_beat"] if nxt
                            else int(spec.get("bars", 0)) * bpb)
                eff = end_beat - last_ev["onset_beat"]
                phrases.append((last_ev, eff))
            bar += 2
    if len(phrases) < 2:
        return item("phrase_endings", "OK", "フレーズ数が少なく対象外")
    warns = []
    degs = [scale.degree(ev["pitch"]) for ev, _eff in phrases]
    if all(d is not None for d in degs) and len(set(degs)) == 1:
        warns.append("全フレーズが同じ度数で終わる")
    effs = [round(eff, 2) for _ev, eff in phrases]
    if len(phrases) >= 3 and len(set(effs)) == 1:
        warns.append("フレーズ末の実効長が全て均一")
    last_ev, _eff = phrases[-1]
    if scale.ok and scale.tonic is not None:
        if last_ev["pitch"] % 12 != scale.tonic:
            warns.append("最終フレーズの末音が主音でない")
    if len(melody) >= 2:
        a, b = melody[-2], melody[-1]
        st = scale.step_diff(a["pitch"], b["pitch"]) if scale.ok else None
        if st is not None:
            if abs(st) > 1:
                warns.append("最終フレーズ末音への到達がステップでない")
        elif abs(b["pitch"] - a["pitch"]) > 2:
            warns.append("最終フレーズ末音への到達が跳躍")
    prog = spec.get("progression", [])
    chords = spec.get("chords", {})
    if prog and scale.ok and scale.tonic is not None:
        root = chords.get(prog[-1], {}).get("bass_root")
        if root is not None and root % 12 != scale.tonic:
            warns.append("最終コードの根音が主音でない")
    if warns:
        return item("phrase_endings", "WARN", "; ".join(warns),
                    "フレーズ末は度数と実効長に変化を付け、最終フレーズは"
                    "主音+ステップ到達+主和音で閉じる")
    return item("phrase_endings", "OK",
                f"フレーズ {len(phrases)} 件の末尾に問題なし")


def check_range(spec, melody):
    if len(melody) < 4:
        return item("melody_range", "SKIP", "主旋律が短い")
    pitches = sorted(e["pitch"] for e in melody)
    width = pitches[-1] - pitches[0]
    q1 = pitches[len(pitches) // 4]
    q3 = pitches[(3 * len(pitches)) // 4]
    iqr = q3 - q1
    msgs = [f"音域 {pitches[0]}-{pitches[-1]}（{width}半音）, IQR {iqr}半音"]
    bad = False
    if width > TH["melody_range_max_semitones"]:
        bad = True
        msgs.append("音域が上限超")
    if iqr < TH["melody_iqr_min_semitones"]:
        bad = True
        msgs.append("IQR が狭く滞在帯が単調")
    lo_band = [p for p in pitches if p <= pitches[0] + width * 0.25]
    hi_band = [p for p in pitches if p >= pitches[-1] - width * 0.25]
    if pitches and (not lo_band or not hi_band):
        bad = True
        msgs.append("上下25%帯のどちらかに滞在が無い")
    if bad:
        return item("melody_range", "WARN", "; ".join(msgs),
                    "音域は16半音以内に収め、滞在帯に広がりを持たせる")
    return item("melody_range", "OK", "; ".join(msgs))


def check_outliers(spec, melody, scale):
    if not scale.ok:
        return item("outlier_recovery", "SKIP", "scale_pcs が未宣言")
    bad = []
    for i, ev in enumerate(melody):
        if ev["pitch"] % 12 in scale.pcs:
            continue
        recovered = False
        for nxt in melody[i + 1:i + 3]:
            if nxt["pitch"] % 12 in scale.pcs \
                    and abs(nxt["pitch"] - ev["pitch"]) <= 2:
                recovered = True
                break
        if not recovered:
            bad.append(f"bar{ev['bar']} 音高{ev['pitch']}")
    if bad:
        return item("outlier_recovery", "WARN",
                    "未回収の音階外音: " + ", ".join(bad),
                    "音階外音は近接する音階音へステップで回収する")
    return item("outlier_recovery", "OK", "音階外音は全て回収されている")


def check_energy_arc(spec, all_events, melody):
    plan = spec.get("energy_arc")
    if not isinstance(plan, dict) or not plan:
        return item("energy_arc", "SKIP", "energy_arc が未宣言")
    warns = []
    stats = {}
    for name, _f, _l in spec.get("sections", []):
        stats[name] = section_stats(spec, all_events, melody, name)
    for name, p in plan.items():
        m = stats.get(name)
        if not m:
            continue
        if p.get("density") and m["density"]:
            if abs(m["density"] - p["density"]) / p["density"] \
                    > TH["energy_density_tol"]:
                warns.append(f"{name}: 密度 計画{p['density']} 実測"
                             f"{m['density']:.1f}")
        if p.get("mean_pitch") is not None:
            if abs(m["mean_pitch"] - p["mean_pitch"]) > TH["energy_pitch_tol"]:
                warns.append(f"{name}: 平均音高 計画{p['mean_pitch']} 実測"
                             f"{m['mean_pitch']:.1f}")
        if p.get("vel") is not None:
            if abs(m["vel"] - p["vel"]) > TH["energy_vel_tol"]:
                warns.append(f"{name}: vel 計画{p['vel']} 実測{m['vel']:.0f}")
    order = [n for n, _f, _l in spec.get("sections", []) if n in plan]
    for n1, n2 in zip(order, order[1:]):
        p1 = dict(plan[n1])
        p2 = dict(plan[n2])
        for p in (p1, p2):
            p.setdefault("parts", 0)
            p.setdefault("range_width", 0)
            p.setdefault("rest_ratio", 0.0)
            p.setdefault("harmonic_rhythm", 1.0)
            p.setdefault("top_note_value", None)
        axes, _w = contrast_axes(p1, p2)
        if len(axes) < TH["contrast_axes_min"]:
            warns.append(f"計画自体の対比不足 {n1}→{n2}: 軸{len(axes)} {axes}")
    if warns:
        return item("energy_arc", "WARN", "; ".join(warns),
                    "energy_arc は生成前に書き、計画自体が対比軸を満たすようにする")
    return item("energy_arc", "OK", "計画と実測の差は許容内、計画の対比も確保")


def check_track_ranges(spec, all_events):
    declared = False
    warns = []
    for key, cfg in spec.get("tracks", {}).items():
        hard = cfg.get("range_hard")
        if not hard:
            continue
        declared = True
        evs = all_events.get(key, [])
        if not evs:
            continue
        lo = min(e["pitch"] for e in evs)
        hi = max(e["pitch"] for e in evs)
        if lo < hard[0] or hi > hard[1]:
            warns.append(f"{key}: 実域 {lo}-{hi} が hard {hard} を逸脱")
        sweet = cfg.get("range_sweet")
        if sweet and (lo < sweet[0] or hi > sweet[1]):
            warns.append(f"{key}: 実域 {lo}-{hi} が sweet {sweet} の外"
                         f"（記録のみ）")
    if not declared:
        return item("track_ranges", "SKIP",
                    "range_hard を宣言したトラックが無い")
    if warns:
        return item("track_ranges", "WARN", "; ".join(warns),
                    "各パートの音域を range_hard 内、主旋律は sweet 帯中心に収める")
    return item("track_ranges", "OK", "全トラックが宣言音域内")


def check_vel_sections(spec):
    parts = {"melody": spec.get("melody_cfg", {}),
             "accomp": spec.get("accomp", {}),
             "bass": spec.get("bass", {}),
             "pad": spec.get("pad", {})}
    missing = []
    for k, cfg in parts.items():
        if k != "melody" and k not in spec:
            continue
        if not (isinstance(cfg, dict) and cfg.get("vel_by_section")):
            missing.append(k)
    if missing:
        return item("vel_by_section", "SKIP",
                    f"vel_by_section 未宣言のパート: {', '.join(missing)}")
    return item("vel_by_section", "OK", "全パートに vel_by_section 宣言あり")


def check_render_route(spec):
    cfg = spec.get("tracks", {}).get("melody", {})
    route = cfg.get("render_route")
    if not route:
        return item("render_route", "SKIP", "主旋律の描画経路が未宣言")
    if str(route).lower() in ("gm", "sf2", "soundfont") \
            and not cfg.get("render_route_reason"):
        return item("render_route", "WARN",
                    f"主旋律の描画経路が {route} だが理由が無い",
                    "主旋律は実録系を既定とし、一括描画系を使う場合は理由を書く")
    return item("render_route", "OK", f"主旋律の描画経路: {route}")


def check_theme_return(spec, melody):
    tc = spec.get("theme_check")
    if not tc:
        return item("theme_return", "OK", "theme_check 未宣言のため対象外")
    a_rng = section_range(spec, tc[0])
    b_rng = section_range(spec, tc[1]) if len(tc) > 1 else None
    if not a_rng or not b_rng:
        return item("theme_return", "SKIP", "theme_check のセクションが無い")
    a_seq = [e["pitch"] for e in evs_in_bars(melody, *a_rng)]
    b_seq = [e["pitch"] for e in evs_in_bars(melody, *b_rng)]
    head = [e["pitch"] for e in evs_in_bars(
        melody, a_rng[0], min(a_rng[0] + 1, a_rng[1]))]
    found = any(b_seq[i:i + len(head)] == head
                for i in range(0, max(0, len(b_seq) - len(head) + 1))) if head \
        else False
    common = 0
    i = 0
    while i < len(b_seq):
        j = 0
        while i + j < len(b_seq) and j < len(a_seq) \
                and b_seq[i + j] == a_seq[j]:
            j += 1
        if j >= 3:
            common += j
            i += j
        else:
            i += 1
    ratio = round(common / len(b_seq), 3) if b_seq else 0.0
    if not found:
        return item("theme_return", "FAIL",
                    f"主題先頭フレーズの逐語再現={found}, 再現部の主題由来率={ratio}",
                    "再現部の冒頭に主題の頭フレーズを逐語で戻す")
    return item("theme_return", "OK",
                f"主題先頭フレーズの逐語再現={found}, 再現部の主題由来率={ratio}")


def check_bar_alignment(spec, all_events):
    bpm = float(spec.get("bpm", 0) or 0)
    if bpm <= 0:
        return item("bar_start_alignment", "SKIP", "bpm が無い")
    ms_per_tick = (60000.0 / bpm) / TPB
    worst = 0.0
    bars = sorted({e["bar"] for evs in all_events.values() for e in evs})
    for bar in bars:
        starts = []
        for evs in all_events.values():
            in_bar = [e["onset_tick"] for e in evs if e["bar"] == bar]
            if in_bar:
                starts.append(min(in_bar))
        if len(starts) >= 2:
            worst = max(worst, (max(starts) - min(starts)) * ms_per_tick)
    if worst > TH["bar_start_align_warn_ms"]:
        return item("bar_start_alignment", "WARN",
                    f"パート間の小節頭ずれ最大 {worst:.1f} ms",
                    "パート間の小節頭ずれは15ms以内に収める")
    return item("bar_start_alignment", "OK",
                f"パート間の小節頭ずれ最大 {worst:.1f} ms")


# ---------------------------------------------------------------- driver

def run_checks(spec, mid):
    """spec 辞書と MIDI（パスまたは MidiFile）から全チェックを実行する。"""
    if isinstance(mid, (str, os.PathLike)):
        mid = MidiFile(str(mid))
    all_events = extract_events(mid, spec)
    melody_raw = all_events.get("melody", [])
    melody, n_extra = clean_melody_events(spec, melody_raw)
    scale = Scale(spec)
    theme_name, _reprise = theme_sections(spec)

    pitches = [e["pitch"] for e in melody]
    leaps = [abs(b - a) for a, b in zip(pitches, pitches[1:])]
    if len(melody) >= 3:
        tris = list(zip(pitches, pitches[1:], pitches[2:]))
        compression = 1.0 - len(set(tris)) / len(tris) if tris else None
    else:
        compression = None

    checks = []
    checks += check_spec_fields(spec)
    checks.append(check_scale_fragment(spec, melody, scale, theme_name))
    checks.append(check_rhythm(spec, melody))
    checks.append(check_contrast(spec, all_events, melody))
    checks.append(check_b_reprise(spec, melody, all_events))
    checks.append(check_stepwise(spec, melody, scale, compression))
    checks.append(check_motif_recurrence(spec, melody, scale))
    checks.append(check_signature_reprise(spec, melody, scale))
    checks.append(check_csim(spec, melody))
    checks.append(check_peak(spec, melody))
    checks.append(check_leaps(spec, melody))
    checks.append(check_phrase_endings(spec, melody, scale))
    checks.append(check_range(spec, melody))
    checks.append(check_outliers(spec, melody, scale))
    checks.append(check_energy_arc(spec, all_events, melody))
    checks.append(check_track_ranges(spec, all_events))
    checks.append(check_vel_sections(spec))
    checks.append(check_render_route(spec))
    checks.append(check_theme_return(spec, melody))
    checks.append(check_bar_alignment(spec, all_events))
    if compression is not None and compression >= TH["compression_warn"]:
        checks.append(item("compression_rate", "WARN",
                           f"圧縮率 {compression:.0%}（上限側・仮置き）",
                           "材料の使い回しを減らし、新規の音型を足す"))
    elif compression is not None:
        checks.append(item("compression_rate", "OK",
                           f"圧縮率 {compression:.0%}"))

    counts = Counter(c["status"] for c in checks)
    if counts.get("FAIL"):
        verdict = "FAIL"
    elif counts.get("SKIP"):
        verdict = "PASS_WITH_SKIPS"
    else:
        verdict = "PASS"
    pending = [c["check"] for c in checks
               if c["check"] in ("cadence_table", "harmony_events")
               and c["status"] == "OK"]
    out = {
        "melody_notes": len(pitches),
        "melody_range": [min(pitches), max(pitches)] if pitches else None,
        "max_leap_semitones": max(leaps) if leaps else 0,
        "extra_notes_separated": n_extra,
        "metrics": {
            "stepwise_theme": None,
            "stepwise_whole": stepwise_rate(melody, scale) if scale.ok
                else None,
            "compression_rate": compression,
            "sections": {n: section_stats(spec, all_events, melody, n)
                         for n, _f, _l in spec.get("sections", [])},
        },
        "checks": checks,
        "counts": {"FAIL": counts.get("FAIL", 0),
                   "WARN": counts.get("WARN", 0),
                   "SKIP": counts.get("SKIP", 0)},
        "pending_verification": pending,
        "verdict": verdict,
    }
    if theme_name and scale.ok and section_range(spec, theme_name):
        out["metrics"]["stepwise_theme"] = stepwise_rate(
            evs_in_bars(melody, *section_range(spec, theme_name)), scale)
    tc = spec.get("theme_check")
    if tc:
        out["theme"] = {"theme_section": tc[0],
                        "reprise_section": tc[1] if len(tc) > 1 else None}
    return out


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    try:
        spec = load_spec(sys.argv[1])
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"verdict": "FAIL",
                          "checks": [item("spec_load", "FAIL", str(e),
                                          "spec の読み込みエラーを直す")]},
                         ensure_ascii=False, indent=2))
        return 1
    out = run_checks(spec, sys.argv[2])
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
