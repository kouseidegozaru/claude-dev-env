# システム python3 (numpy) で実行: 実録ギターサンプルで DI ステムを描く。
# 使い方: render_guitar.py <samples_dir> <events.json> <out.wav> [rr_offset]
import json
import os
import re
import sys
import wave

import numpy as np

SR = 44100
NOTE_RE = re.compile(r"([a-g])([#sb]?)(-?\d+)_([a-z]+)_rr(\d+)\.wav$", re.I)
BASE = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}
DYN_ORDER = ["pp", "p", "mp", "mf", "f", "ff"]


def load_wav(path):
    w = wave.open(path)
    n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sw == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
        v = b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        a = v.astype(np.float32) / 8388608.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"unsupported sampwidth {sw}: {path}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a


def build_index(samples_dir):
    idx = {}  # (pitch, dyn) -> [paths sorted by rr]
    skipped = 0
    for fn in sorted(os.listdir(samples_dir)):
        m = NOTE_RE.search(fn)
        if not m:
            continue
        # リポジトリ側でヘッダが壊れているファイル（約5%）は除外する
        try:
            with open(os.path.join(samples_dir, fn), "rb") as fh:
                head = fh.read(12)
            if head[:4] != b"RIFF" or head[8:12] != b"WAVE":
                skipped += 1
                continue
        except OSError:
            skipped += 1
            continue
        letter, acc, octv, dyn, _rr = m.groups()
        pc = BASE[letter.lower()]
        if acc in ("#", "s"):
            pc += 1
        elif acc == "b":
            pc -= 1
        pitch = pc + (int(octv) + 1) * 12
        idx.setdefault((pitch, dyn.lower()), []).append(os.path.join(samples_dir, fn))
    if skipped:
        print(f"skipped {skipped} corrupt files")
    return idx


def pick_layer(dyns, vel):
    # ベロシティを層の並びへ線形に対応させる
    if len(dyns) == 1:
        return dyns[0]
    pos = vel / 127.0 * (len(dyns) - 1)
    return dyns[int(round(pos))]


def render(samples_dir, events, rr_offset=0):
    idx = build_index(samples_dir)
    dyns = sorted({d for _, d in idx}, key=lambda d: DYN_ORDER.index(d) if d in DYN_ORDER else 99)
    pitches = sorted({p for p, _ in idx})
    if not pitches:
        sys.exit("no samples indexed in " + samples_dir)
    print(f"layers={dyns} pitch range={pitches[0]}..{pitches[-1]} ({len(pitches)} pitches)")
    cache = {}
    rr_count = {}
    total = max(e["start"] + e["dur"] for e in events) + 0.5
    buf = np.zeros(int(total * SR) + SR, dtype=np.float32)
    for e in events:
        pitch = e["pitch"]
        dyn = pick_layer(dyns, e["vel"])
        # 同音のサンプルが無ければ最も近い音高から移調する
        src_pitch = pitch if (pitch, dyn) in idx else min(pitches, key=lambda p: abs(p - pitch))
        paths = idx[(src_pitch, dyn)]
        key = (src_pitch, dyn)
        n = rr_count.get(key, 0)
        rr_count[key] = n + 1
        path = paths[(n + rr_offset) % len(paths)]
        if path not in cache:
            cache[path] = load_wav(path)
        smp = cache[path]
        ratio = 2.0 ** ((pitch - src_pitch) / 12.0)
        dur = e["dur"] + 0.09  # リリース分
        need = int(dur * SR * ratio)
        seg = smp[:need]
        if ratio != 1.0 and len(seg) > 2:
            pos = np.arange(int(len(seg) / ratio)) * ratio
            seg = np.interp(pos, np.arange(len(seg)), seg).astype(np.float32)
        # 終端フェード（クリック防止とミュート感）
        fade = min(len(seg), int(0.055 * SR))
        if fade > 0:
            seg = seg.copy()
            seg[-fade:] *= np.linspace(1.0, 0.0, fade) ** 1.5
        gain = (e["vel"] / 127.0) ** 1.15
        start = int(e["start"] * SR)
        end = min(len(buf), start + len(seg))
        buf[start:end] += seg[:end - start] * gain
    peak = float(np.abs(buf).max())
    if peak > 0:
        buf *= 0.89 / peak
    return buf


if __name__ == "__main__":
    samples_dir, events_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    rr_offset = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    with open(events_path) as f:
        events = json.load(f)
    buf = render(samples_dir, events, rr_offset)
    pcm = (np.clip(buf, -1, 1) * 32767).astype("<i2")
    w = wave.open(out_path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes(pcm.tobytes())
    w.close()
    print("wrote", out_path, f"{len(buf)/SR:.1f}s")
