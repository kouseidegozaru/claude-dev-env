#!/usr/bin/env python3
"""作曲仕様書（spec）から MIDI を生成するテンプレート。

使い方:
  ~/workspace/midi-bgm/venv/bin/python compose.py SPEC.py OUT.mid

spec は Python ファイルで、辞書 SPEC を定義する（examples/ 参照）。
必須: title, seed, bpm, bars, progression, chords, tracks, melody
seed が無い仕様書は不完全としてエラーにする（再現性の担保）。

生成は seed で決定的: 同じ spec からは何度でもバイト一致の MIDI が出る。
"""
import importlib.util
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
    return spec


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


def build(spec):
    rng = random.Random(spec["seed"])
    bpm = float(spec["bpm"])
    bpb = int(spec.get("beats_per_bar", 4))
    bars = int(spec["bars"])
    prog = spec["progression"]
    if len(prog) != bars:
        raise ValueError(f"progression の長さ {len(prog)} が bars {bars} と一致しません")
    chords = spec["chords"]
    hum = spec.get("humanize", {})
    vel_jit = int(hum.get("vel_jitter", 4))
    time_jit = int(hum.get("time_jitter_ticks", 3))

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

    def add(tkey, note, start_beat, dur_beats, vel, humanize=True):
        v = vel
        s_tick = int(round(start_beat * TPB))
        if humanize:
            v = max(1, min(127, vel + rng.randint(-vel_jit, vel_jit)))
            s_tick += rng.randint(-time_jit, time_jit) if time_jit else 0
        e_tick = int(round((start_beat + dur_beats) * TPB))
        ev = tracks[tkey][2]
        ev.append((max(0, s_tick), 1, note, v))
        ev.append((max(e_tick, s_tick + 1), 0, note, 0))

    accomp = spec.get("accomp")
    bass = spec.get("bass")
    pad = spec.get("pad")

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
            vel = bass.get("vel", 64)
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
                for n in ch["pad"]:
                    add("pad", n, b0, bpb - 0.05, pad.get("vel", 34), humanize=False)

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
                add(tkey, note, b0 + off, dur, vel)

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
