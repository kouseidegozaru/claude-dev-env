#!/usr/bin/env python3
"""作曲仕様書（spec）から MIDI を生成するテンプレート。

使い方:
  ~/workspace/midi-bgm/venv/bin/python compose.py SPEC.py OUT.mid

spec は Python ファイルで、辞書 SPEC を定義する（examples/ 参照）。
必須: title, seed, bpm, bars, progression, chords, tracks, melody,
      motif（型付きレコード）, cadence_table
seed が無い仕様書は不完全としてエラーにする（再現性の担保）。
motif / cadence_table が無い仕様書も不完全としてエラーにする。

生成は seed で決定的: 同じ spec からは何度でもバイト一致の MIDI が出る。
"""
import importlib.util
import itertools
import math
import random
import sys

from mido import MidiFile, MidiTrack, Message, MetaMessage

TPB = 480  # ticks per beat


def load_spec(path):
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    spec = getattr(mod, "SPEC", None)
    if spec is None:
        raise ValueError("spec に SPEC 辞書がありません")
    if "seed" not in spec:
        raise ValueError("仕様書に seed 欄がありません（不完全な仕様書）")
    _validate_schema(spec)
    return spec


def _validate_schema(spec):
    """必須フィールドの検証（ロード時エラー）。

    必須: seed（再現性）、motif（型付きレコード）、cadence_table。
    motif は intervals（音程列）/ rhythm（リズム比、intervals より1つ多い）/
    contour（輪郭）/ anchor_start_degree・anchor_end_degree（アンカー）/
    signature（署名スロット、空は不可）を持つ辞書とする。
    """
    motif = spec.get("motif")
    if not isinstance(motif, dict):
        raise ValueError("仕様書に motif 欄がありません（不完全な仕様書）")
    intervals = motif.get("intervals")
    rhythm = motif.get("rhythm")
    signature = motif.get("signature")
    if not isinstance(intervals, list) or len(intervals) < 1:
        raise ValueError("motif.intervals が空です（モチーフの音程列が必須）")
    if not isinstance(rhythm, list) or len(rhythm) != len(intervals) + 1:
        raise ValueError("motif.rhythm は intervals より1つ多い長さで"
                         "リズム比を並べます")
    if not isinstance(signature, list) or len(signature) < 1:
        raise ValueError("motif.signature が空です（署名スロットは必須）")
    ct = spec.get("cadence_table")
    if not isinstance(ct, list) or not ct:
        raise ValueError("仕様書に cadence_table 欄がありません"
                         "（不完全な仕様書）")
    for row in ct:
        if not isinstance(row, dict) or "end_bar" not in row \
                or "type" not in row:
            raise ValueError("cadence_table の各行には end_bar と type が"
                             "必要です")


def section_of(spec, bar):
    for name, first, last in spec.get("sections", []):
        if first <= bar <= last:
            return name
    return None


def section_last_bar(spec, bar):
    for _name, _first, last in spec.get("sections", []):
        if bar == last:
            return True
    return False


def _scale_step(pitch, scale_pcs, direction):
    p = pitch + direction
    while p % 12 not in scale_pcs:
        p += direction
    return p


def apply_ornaments(table, cfg, bpb):
    """装飾パス（調査 R6）: 主旋律の骨格に非和声音を後付けする。

    - 経過音: 3度跳躍の手前、跳躍元ロングトーンの末尾0.5拍に中間の音階音を挿入
    - 刺繍音: 同音反復の2音目冒頭0.5拍を上行の隣接音に置き換える
    protect_bars の小節（主題の頭など）は触らない。1小節あたり max_per_bar 個まで。
    戻り値: (装飾後の表, 挿入個数)
    """
    scale_pcs = set(cfg["scale_pcs"])
    protect = set(cfg.get("protect_bars", []))
    max_per_bar = int(cfg.get("max_per_bar", 1))
    ovel = int(cfg.get("vel", 80))
    new = {}
    for bar, notes in table.items():
        new[bar] = [[t[0], float(t[1]), float(t[2]),
                     (t[3] if len(t) >= 4 else None)] for t in notes]
    seq = []
    for bar in sorted(new):
        for item in new[bar]:
            seq.append((bar, item))
    seq.sort(key=lambda x: ((x[0] - 1) * bpb + x[1][1]))
    per_bar = {}
    count = 0
    for i in range(len(seq) - 1):
        bar1, n1 = seq[i]
        bar2, n2 = seq[i + 1]
        end1 = (bar1 - 1) * bpb + n1[1] + n1[2]
        start2 = (bar2 - 1) * bpb + n2[1]
        if abs(end1 - start2) > 1e-6:
            continue  # 連続していない音の間には装飾しない
        # ---- 経過音（3度跳躍） ----
        mid = None
        step1 = _scale_step(n1[0], scale_pcs, 1)
        if _scale_step(step1, scale_pcs, 1) == n2[0]:
            mid = step1
        else:
            step1d = _scale_step(n1[0], scale_pcs, -1)
            if _scale_step(step1d, scale_pcs, -1) == n2[0]:
                mid = step1d
        if (mid is not None and n1[2] >= 1.0 and bar1 not in protect
                and per_bar.get(bar1, 0) < max_per_bar):
            n1[2] -= 0.5
            new[bar1].append([mid, n1[1] + n1[2], 0.5, ovel])
            per_bar[bar1] = per_bar.get(bar1, 0) + 1
            count += 1
            continue
        # ---- 刺繍音（同音反復の2音目） ----
        if (n1[0] == n2[0] and n2[2] >= 1.0 and bar2 not in protect
                and per_bar.get(bar2, 0) < max_per_bar):
            nb = _scale_step(n2[0], scale_pcs, 1)
            new[bar2].append([nb, n2[1], 0.5, ovel])
            n2[1] += 0.5
            n2[2] -= 0.5
            per_bar[bar2] = per_bar.get(bar2, 0) + 1
            count += 1
    out = {}
    for bar, notes in new.items():
        notes.sort(key=lambda x: x[1])
        out[bar] = [tuple([n, o, d] + ([v] if v is not None else []))
                    for n, o, d, v in notes]
    return out, count


class _Humanizer:
    """パート別のヒューマナイズ（独立一様乱数ではなく相関成分で作る）。

    タイミング: AR(1) 相関ノイズ。σ は拍に対する割合で指定し、
    パート係数（主旋律 1.0 / 伴奏 0.7 / ベース 0.5 / パッド 0.5）を掛ける。
    最大偏差は min(2.5σ, 拍の5%) で頭打ちにする。
    小節頭の発音はパート間の縦の線を揃えるため揺らさない。
    ベロシティ: 主旋律は構造成分（フレーズ弧 + 拍アクセント）が主で、
    vel_jitter（±4 程度）は全パート共通の仕上げの微量成分として残す。
    """

    def __init__(self, rng, sigma_ticks):
        self.rng = rng
        self.sigma = float(sigma_ticks)
        self.state = 0.0

    def timing_ticks(self):
        if self.sigma <= 0:
            return 0
        rho = 0.7
        self.state = rho * self.state \
            + math.sqrt(1.0 - rho * rho) * self.rng.gauss(0.0, 1.0)
        cap = min(2.5 * self.sigma, 0.05 * TPB)
        return int(round(max(-cap, min(cap, self.state * self.sigma))))


def _structural_vel(start_beat, bpb):
    """ベロシティの構造成分: フレーズ弧（2小節周期の膨らみ、最大 +8）と
    拍アクセント（小節頭 +6、その他の拍頭 +3、拍裏 -6）。"""
    phrase = 2.0 * bpb
    phase = (start_beat % phrase) / phrase
    arc = 8.0 * math.sin(math.pi * phase)
    beat_in_bar = start_beat % bpb
    if abs(beat_in_bar - round(beat_in_bar)) < 1e-9:
        metric = 6.0 if int(round(beat_in_bar)) == 0 else 3.0
    else:
        metric = -6.0
    return arc + metric


def _pad_active_bars(spec, pad):
    bars = int(spec["bars"])
    active = []
    for bar in range(1, bars + 1):
        sec = section_of(spec, bar)
        secs = pad.get("sections")
        in_range = (sec in secs) if secs else True
        if pad.get("bar_ranges"):
            in_range = any(a <= bar <= b for a, b in pad["bar_ranges"])
        if pad.get("skip_last_bar", False) and section_last_bar(spec, bar):
            in_range = False
        if in_range:
            active.append(bar)
    return active


def _pad_candidates(ch, cfg):
    """コード毎のパッド候補列（最大24）。ハード制約で濾過する:
    音域内 / 声部間隔 <= 12半音 / 短9度（13半音）の回避 / 構成音の重複なし。"""
    pcs = sorted({n % 12 for n in ch.get("arp", [])} | {ch["bass_root"] % 12})
    lo, hi = cfg.get("range", [57, 84])
    n_voices = int(cfg.get("voices_n", 3))
    default = list(ch.get("pad", []))
    pool = [p for p in range(int(lo), int(hi) + 1) if p % 12 in pcs]
    out = []
    for combo in itertools.combinations(pool, n_voices):
        if len({n % 12 for n in combo}) < n_voices:
            continue
        if any(combo[i + 1] - combo[i] > 12 for i in range(len(combo) - 1)):
            continue
        if any(combo[j] - combo[i] == 13
               for i in range(len(combo)) for j in range(i + 1, len(combo))):
            continue
        out.append(combo)
    if default and len(default) == n_voices:
        out.sort(key=lambda c: sum(abs(c[i] - default[i])
                                   for i in range(n_voices)))
    else:
        out.sort(key=lambda c: sum(abs(n - 72) for n in c))
    return out[:24]


def _parallel_perfect(prev_low, cur_low, prev_high, cur_high):
    """2声部間の並行完全5度・完全8度（同じ方向への移動で完全音程→完全音程）。"""
    d_low = cur_low - prev_low
    d_high = cur_high - prev_high
    if d_low == 0 or d_high == 0 or (d_low > 0) != (d_high > 0):
        return False
    i0 = abs(prev_high - prev_low) % 12
    i1 = abs(cur_high - cur_low) % 12
    return i0 in (0, 7) and i1 == i0


def inspect_pad_voice_leading(voicings, bass_roots):
    """パッドの声部連結検査（独自仕様）。

    規則の定義は、声部連結の判定項目（並行完全5度・完全8度、声部の交差、
    過大な間隔）を扱う music21 の VoiceLeadingQuartet を出典として、
    本スクリプト用に独自に実装したもの（移植ではない）。
    voicings: 小節ごとのパッド音高タプルの列。bass_roots: 同じ小節列の
    ベース根音の列。戻り値: (列内の小節番号, 説明) のリスト。
    """
    violations = []
    for i, v in enumerate(voicings):
        bar = i + 1
        if list(v) != sorted(v):
            violations.append((bar, "声部の交差"))
        for a in range(len(v)):
            for b in range(a + 1, len(v)):
                if v[b] - v[a] > 12:
                    violations.append(
                        (bar, f"声部間隔 {v[b] - v[a]} 半音 (>12)"))
        if i == 0:
            continue
        prev = voicings[i - 1]
        pairs = [(bass_roots[i - 1], bass_roots[i], prev[k], v[k],
                  f"対ベース声部{k + 1}") for k in range(len(v))]
        pairs += [(prev[a], v[a], prev[b], v[b], f"声部{a + 1}-声部{b + 1}")
                  for a in range(len(v)) for b in range(a + 1, len(v))]
        for p0, p1, q0, q1, label in pairs:
            if _parallel_perfect(p0, p1, q0, q1):
                kind = "完全8度" if abs(q0 - p0) % 12 == 0 else "完全5度"
                violations.append((bar, f"並行{kind}（{label}）"))
    return violations


def _plan_pad_voicings(spec, prog, chords):
    """pad の voices: "auto" 用ボイシング列（縮小形 Viterbi）。

    前小節のパッド配置とその小節の bass_root だけを参照する。
    主旋律（外声枠）との連動は行わない（次段階の担当）。
    候補列はコード毎に最大24、遷移コストは声部の総移動量とし、
    並行完全5度・8度（対ベース・声部間）は遷移禁止とする。
    生成後に検査→局所修復ループ（総ペナルティ厳密減少のみ採用、
    上限20回）を回し、残違反は標準出力へ明示する（黙って通さない）。
    """
    pad = spec["pad"]
    active = _pad_active_bars(spec, pad)
    if not active:
        return {}
    segments = [[active[0]]]
    for bar in active[1:]:
        if bar == segments[-1][-1] + 1:
            segments[-1].append(bar)
        else:
            segments.append([bar])
    all_bass = [chords[prog[b - 1]]["bass_root"]
                for b in range(1, int(spec["bars"]) + 1)]
    result = {}
    INF = float("inf")

    def trans_cost(prev_v, cur_v, pb, cb):
        for k in range(len(cur_v)):
            if _parallel_perfect(pb, cb, prev_v[k], cur_v[k]):
                return INF
        for a in range(len(cur_v)):
            for b in range(a + 1, len(cur_v)):
                if _parallel_perfect(prev_v[a], cur_v[a],
                                     prev_v[b], cur_v[b]):
                    return INF
        return sum(abs(cur_v[k] - prev_v[k]) for k in range(len(cur_v)))

    for seg in segments:
        cands = [_pad_candidates(chords[prog[b - 1]], pad) for b in seg]
        if any(not c for c in cands):
            for b in seg:
                result[b] = tuple(chords[prog[b - 1]].get("pad", []))
            continue
        bass = [all_bass[b - 1] for b in seg]
        default = list(chords[prog[seg[0] - 1]].get("pad", []))
        first = cands[0]
        if default and len(default) == len(first[0]):
            dp = [sum(abs(c[k] - default[k]) for k in range(len(c)))
                  for c in first]
        else:
            dp = [0.0] * len(first)
        backs = []
        for i in range(1, len(seg)):
            ndp, nback = [], []
            for c in cands[i]:
                best, arg = INF, -1
                for j, p in enumerate(cands[i - 1]):
                    tc = trans_cost(p, c, bass[i - 1], bass[i])
                    if tc == INF:
                        continue
                    val = dp[j] + tc
                    if val < best:
                        best, arg = val, j
                ndp.append(best)
                nback.append(arg)
            backs.append(nback)
            dp = ndp
        idx = min(range(len(dp)), key=lambda j: dp[j])
        chosen = [None] * len(seg)
        for i in range(len(seg) - 1, -1, -1):
            chosen[i] = cands[i][idx]
            if i > 0:
                idx = backs[i - 1][idx] if backs[i - 1][idx] >= 0 else 0
        voicings = list(chosen)

        def penalty(vs):
            viol = inspect_pad_voice_leading(vs, bass)
            move = sum(abs(vs[i][k] - vs[i - 1][k])
                       for i in range(1, len(vs))
                       for k in range(len(vs[i])))
            return len(viol) * 10000 + move, viol

        pen, viol = penalty(voicings)
        tries = 0
        while viol and tries < 20:
            tries += 1
            pos = viol[0][0] - 1
            positions = [pos] + ([pos - 1] if pos > 0 else [])
            improved = False
            for at in positions:
                for cand in cands[at]:
                    if cand == voicings[at]:
                        continue
                    trial = list(voicings)
                    trial[at] = cand
                    tpen, _v = penalty(trial)
                    if tpen < pen:
                        voicings, pen = trial, tpen
                        _p, viol = penalty(voicings)
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        if viol:
            for bar_no, msg in viol:
                print(f"pad voice-leading violation (未修復): "
                      f"bar {seg[bar_no - 1]}: {msg}")
        for b, v in zip(seg, voicings):
            result[b] = tuple(v)
    return result


def build(spec):
    bpm = float(spec["bpm"])
    bpb = int(spec.get("beats_per_bar", 4))
    bars = int(spec["bars"])
    prog = spec["progression"]
    if len(prog) != bars:
        raise ValueError(f"progression の長さ {len(prog)} が bars {bars} と一致しません")
    chords = spec["chords"]
    hum = spec.get("humanize", {})
    vel_jit = int(hum.get("vel_jitter", 4))
    # タイミングの σ: 新形式は timing_sigma_pct（拍に対する割合）。
    # 旧形式 time_jitter_ticks があれば σ[ticks] として互換解釈する。
    if hum.get("timing_sigma_pct") is not None:
        sigma_base = TPB * float(hum["timing_sigma_pct"])
    elif hum.get("time_jitter_ticks") is not None:
        sigma_base = float(hum["time_jitter_ticks"])
    else:
        sigma_base = TPB * 0.015

    mid = MidiFile(ticks_per_beat=TPB)
    cond = MidiTrack()
    mid.tracks.append(cond)
    cond.append(MetaMessage("track_name", name=spec.get("title", "bgm"), time=0))
    cond.append(MetaMessage("set_tempo", tempo=int(60_000_000 / bpm), time=0))
    last_tick = 0
    for bar, tbpm in spec.get("tempo_changes", []):
        tick = int((bar - 1) * bpb * TPB)
        cond.append(MetaMessage("set_tempo", tempo=int(60_000_000 / float(tbpm)),
                                time=tick - last_tick))
        last_tick = tick

    tracks = {}
    for key, cfg in spec["tracks"].items():
        tr = MidiTrack()
        mid.tracks.append(tr)
        tr.append(MetaMessage("track_name", name=cfg.get("name", key), time=0))
        tr.append(Message("program_change", channel=cfg["channel"],
                          program=cfg["program"], time=0))
        tracks[key] = (tr, cfg["channel"], [])

    # パート別ヒューマナイザ（パートごとに独立した乱数ストリームと
    # 相関ノイズ状態を持つ。同じ spec からは決定的に同じ出力になる）
    _TIMING_FACTOR = {"melody": 1.0, "accomp": 0.7, "bass": 0.5, "pad": 0.5}
    humanizers = {}
    for idx, key in enumerate(spec["tracks"]):
        part_rng = random.Random(spec["seed"] * 1000003 + idx * 7919 + 17)
        humanizers[key] = _Humanizer(
            part_rng, sigma_base * _TIMING_FACTOR.get(key, 0.7))

    def add(tkey, note, start_beat, dur_beats, vel, humanize=True,
            structural=False):
        v = vel
        s_tick = int(round(start_beat * TPB))
        if humanize:
            hz = humanizers[tkey]
            if structural:
                v += int(round(_structural_vel(start_beat, bpb)))
            if vel_jit:
                v += hz.rng.randint(-vel_jit, vel_jit)
            v = max(1, min(127, v))
            shift = hz.timing_ticks()
            # 小節頭はパート間で揃えるため揺らさない（ノイズ状態だけ進める）
            if start_beat % bpb > 1e-9:
                s_tick += shift
        e_tick = int(round((start_beat + dur_beats) * TPB))
        ev = tracks[tkey][2]
        ev.append((max(0, s_tick), 1, note, v))
        ev.append((max(e_tick, s_tick + 1), 0, note, 0))

    accomp = spec.get("accomp")
    bass = spec.get("bass")
    pad = spec.get("pad")
    pad_voicings = None
    if pad and pad.get("voices") == "auto" and "pad" in tracks:
        pad_voicings = _plan_pad_voicings(spec, prog, chords)

    for bar_idx, chord_name in enumerate(prog):
        bar = bar_idx + 1
        b0 = bar_idx * bpb
        ch = chords[chord_name]
        sec = section_of(spec, bar)
        last_of_sec = section_last_bar(spec, bar)

        # ---- accompaniment ----
        if accomp and "accomp" in tracks:
            style = accomp.get("style", "arpeggio")
            vel = accomp.get("vel_by_bar", {}).get(
                bar, accomp.get("vel_by_section", {}).get(sec, accomp.get("vel", 62)))
            tones = ch["arp"]
            if accomp.get("final_bar_strum") and bar == bars:
                for n in tones:
                    add("accomp", n, b0, bpb - 0.1, vel)
            elif style == "arpeggio":
                cycle = accomp.get("pattern_cycle")
                if cycle:
                    # パターン・ローテーション（調査 R5）: 小節ごとにパターンを
                    # 切り替え、同一パターンの連続を避ける（習慣化の回避）。
                    pattern = cycle[bar_idx % len(cycle)]
                else:
                    pattern = accomp.get("pattern", [0, 1, 2, 3, 2, 1, 2, 3])
                step = bpb / len(pattern)
                nlen = accomp.get("note_len", step * 1.1)
                accent = accomp.get("accent", 6)
                for i, idx in enumerate(pattern):
                    add("accomp", tones[idx], b0 + i * step, nlen,
                        vel + (accent if i % 2 == 0 else 0))
            elif style == "drive8":
                # 8分音符の刻み（全音を同時に鳴らす）。パンク等の推進力用。
                accent = accomp.get("accent", 6)
                for i in range(int(bpb * 2)):
                    v = vel + (accent if i % 2 == 0 else 0)
                    for n in tones:
                        add("accomp", n, b0 + i * 0.5, 0.42, v)
            elif style == "strum":
                # 刻み: 拍ごとに和音を短く
                for beat in range(bpb):
                    for n in tones:
                        add("accomp", n, b0 + beat, 0.45, vel)
            elif style == "hold":
                for n in tones:
                    add("accomp", n, b0, bpb - 0.05, vel, humanize=False)

        # ---- bass ----
        if bass and "bass" in tracks:
            style_map = bass.get("style_by_section", {})
            style = style_map.get(sec, style_map.get("default", "whole"))
            vel = bass.get("vel_by_section", {}).get(
                sec, bass.get("vel", 64))
            skip = bass.get("skip_last_bar", True) and last_of_sec
            if style == "root_fifth" and not skip:
                half = bpb / 2
                add("bass", ch["bass_root"], b0, half - 0.1, vel)
                add("bass", ch["bass_fifth"], b0 + half, half - 0.1, vel - 2)
            elif style == "root_5_8" and not skip:
                # ルート→5度→オクターブ上ルート（調査 R5 のパターンライブラリ）
                add("bass", ch["bass_root"], b0, 1.9, vel)
                add("bass", ch["bass_fifth"], b0 + 2, 0.9, vel - 2)
                add("bass", ch["bass_root"] + 12, b0 + 3, 0.9, vel - 4)
            elif style == "punk8" and not skip:
                # 8分音符で根音を刻み続ける（パンクベース）。
                for i in range(int(bpb * 2)):
                    add("bass", ch["bass_root"], b0 + i * 0.5, 0.42,
                        vel + (6 if i % 2 == 0 else 0))
            elif style == "drive8mel" and not skip:
                # 駆動感のあるメロディックな8分ベース（レゲエパンク風）。
                # 根音を刻みながらオクターブと5度を差し込み、最後は次ルートの
                # 全音下から寄せる。
                nxt = chords[prog[bar_idx + 1]]["bass_root"] \
                    if bar_idx + 1 < bars else ch["bass_root"]
                r = ch["bass_root"]
                offs = [0, 0, 12, 0, 0, 7, 12, None]
                for i, o in enumerate(offs):
                    p = (nxt - 2) if o is None else r + o
                    add("bass", p, b0 + i * 0.5, 0.42,
                        vel + (6 if i == 0 else 3 if i == 4 else 0))
            elif style == "walk" and not skip:
                # ウォーキングベース（ジャズ）: 4分音符で根→5→オクターブ→
                # 次コード根音への半音アプローチ。
                nxt = chords[prog[bar_idx + 1]]["bass_root"] \
                    if bar_idx + 1 < bars else ch["bass_root"]
                ap = nxt - 1 if nxt >= ch["bass_root"] else nxt + 1
                add("bass", ch["bass_root"], b0, 0.9, vel)
                add("bass", ch["bass_fifth"], b0 + 1, 0.9, vel - 2)
                add("bass", ch["bass_root"] + 12, b0 + 2, 0.9, vel - 2)
                add("bass", ap, b0 + 3, 0.9, vel - 4)
            elif style == "approach" and not skip:
                # 最終拍に次コードのルートへの半音アプローチ（順次進行を作る）
                nxt = chords[prog[bar_idx + 1]]["bass_root"] \
                    if bar_idx + 1 < bars else ch["bass_root"]
                ap = nxt - 1 if nxt >= ch["bass_root"] else nxt + 1
                add("bass", ch["bass_root"], b0, 2.9, vel)
                add("bass", ap, b0 + 3, 0.9, vel - 4)
            else:
                add("bass", ch["bass_root"], b0, bpb - 0.1, vel)

        # ---- pad ----
        if pad and "pad" in tracks:
            secs = pad.get("sections")
            in_range = (sec in secs) if secs else True
            if pad.get("bar_ranges"):
                in_range = any(a <= bar <= b for a, b in pad["bar_ranges"])
            if in_range and not (pad.get("skip_last_bar", False) and last_of_sec):
                vel = pad.get("vel_by_section", {}).get(
                    sec, pad.get("vel", 34))
                if pad_voicings is not None:
                    notes = pad_voicings.get(bar, tuple(ch["pad"]))
                else:
                    notes = ch["pad"]
                for n in notes:
                    add("pad", n, b0, bpb - 0.05, vel, humanize=False)

    # ---- note tables (melody + extra) ----
    # (track, table, is_main_melody) の順に処理する。dur_scale は主旋律だけに掛ける。
    tables = []
    if "melody" in spec and "melody" in tracks:
        tables.append(("melody", spec["melody"], True))
    for tkey, table in spec.get("extra_notes", {}).items():
        if tkey in tracks:
            tables.append((tkey, table, False))

    mel_cfg = spec.get("melody_cfg", {})
    orn = spec.get("ornament")
    if orn and "melody" in spec:
        new_melody, n_orn = apply_ornaments(spec["melody"], orn, bpb)
        tables = [(k, (new_melody if k == "melody" else t), m)
                  for k, t, m in tables]
        print(f"ornament pass: +{n_orn} notes (passing/neighbor)")
    for tkey, table, is_main in tables:
        for bar, notes in table.items():
            b0 = (bar - 1) * bpb
            sec = section_of(spec, bar)
            for tup in notes:
                note, off, dur = tup[0], tup[1], tup[2]
                if len(tup) >= 4:
                    vel = tup[3]
                elif is_main:
                    vel = mel_cfg.get("vel_by_section", {}).get(
                        sec, mel_cfg.get("vel", 92))
                else:
                    vel = 64
                if is_main:
                    dur = dur * mel_cfg.get("dur_scale", 0.95)
                    if off == 0:
                        # 小節頭のアクセント（調査 R10 ヒューマナイズ）
                        vel += int(mel_cfg.get("accent_first", 0))
                add(tkey, note, b0 + off, dur, vel, structural=is_main)

    for _key, (tr, channel, events) in tracks.items():
        events.sort(key=lambda x: (x[0], x[1]))
        last = 0
        for tick, kind, note, vel in events:
            dt = tick - last
            last = tick
            if kind == 1:
                tr.append(Message("note_on", channel=channel, note=note,
                                  velocity=vel, time=dt))
            else:
                tr.append(Message("note_off", channel=channel, note=note,
                                  velocity=0, time=dt))
    return mid


def duration_s(spec):
    """テンポマップを積分した概算の長さ [s]。"""
    bpb = int(spec.get("beats_per_bar", 4))
    changes = sorted(spec.get("tempo_changes", []))
    total_beats = int(spec["bars"]) * bpb
    secs = 0.0
    cur_beat = 0
    cur_bpm = float(spec["bpm"])
    for bar, tbpm in changes:
        beat = (bar - 1) * bpb
        secs += (beat - cur_beat) * 60.0 / cur_bpm
        cur_beat, cur_bpm = beat, float(tbpm)
    secs += (total_beats - cur_beat) * 60.0 / cur_bpm
    return secs


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    spec = load_spec(sys.argv[1])
    mid = build(spec)
    mid.save(sys.argv[2])
    n_notes = sum(1 for tr in mid.tracks for m in tr
                  if m.type == "note_on" and m.velocity > 0)
    print(f"saved {sys.argv[2]}: bars={spec['bars']} notes={n_notes} "
          f"duration~{duration_s(spec):.1f}s seed={spec['seed']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
