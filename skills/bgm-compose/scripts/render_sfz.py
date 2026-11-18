# 汎用 SFZ レンダラー: SFZ ファイル（または音名つきファイル名のフォルダ）から
# イベント JSON を鳴らす。bgm-compose の曲作りで実録サンプル楽器を使うための道具。
#
# 使い方:
#   render_sfz.py --sfz <file.sfz> <events.json> <out.wav> [--mode oneshot|sustain]
#                 [--tail 1.5] [--fade-ms 60] [--gain 1.0]
#   render_sfz.py --folder <dir>  <events.json> <out.wav> [同上]
#     フォルダ形式はファイル名中の音名（C4, F#3, Gb3 等）をキーセンターとして扱う。
#
# 対応する SFZ 記法の範囲: <global>/<group>/<region>、sample, key,
# lokey/hikey, pitch_keycenter, lovel/hivel, volume, tune, default_path,
# seq_position（複数 region はラウンドロビンとして巡回）。
# noises 等のパスを含む region は自動で除外する（演奏ノイズは鳴らさない）。
import json
import os
import re
import struct
import sys

import numpy as np

SR = 44100
NOTE_RE = re.compile(r"([A-Ga-g])([#b]?)(-?\d)")

_pc = {"c": 0, "d": 2, "e": 4, "f": 5, "g": 7, "a": 9, "b": 11}


def note_to_midi(name):
    m = NOTE_RE.search(name)
    if not m:
        return None
    pc = _pc[m.group(1).lower()]
    if m.group(2) == "#":
        pc += 1
    elif m.group(2) == "b":
        pc -= 1
    return pc + (int(m.group(3)) + 1) * 12


def load_wav(path):
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
            raise ValueError("bad header")
        f.seek(0)
        raw = f.read()
    pos = 12
    fmt = None
    data = None
    while pos + 8 <= len(raw):
        cid = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif cid == b"data":
            data = body
        pos += 8 + size + (size % 2)
    if fmt is None or data is None:
        raise ValueError("no fmt/data")
    audio_fmt, channels, rate, _br, _ba, bits = fmt
    if audio_fmt == 3 and bits == 32:
        v = np.frombuffer(data, dtype="<f4").astype(np.float32)
    elif bits == 24:
        b = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3)
        v = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8)
             | (b[:, 2].astype(np.int32) << 16))
        v = np.where(v >= 1 << 23, v - (1 << 24), v).astype(np.float32) / 8388608.0
    elif bits == 16:
        v = np.frombuffer(data, dtype="<i2").astype(np.float32) / 32768.0
    else:
        raise ValueError(f"unsupported bits {bits}")
    if channels == 2:
        v = v.reshape(-1, 2).mean(axis=1)
    if rate != SR:
        n = int(len(v) * SR / rate)
        v = np.interp(np.linspace(0, len(v) - 1, n), np.arange(len(v)), v)
    return v.astype(np.float32)


def _expand_includes(text, base, depth=0):
    if depth > 4:
        return text
    def rep(m):
        inc = m.group(1).replace("\\", "/")
        p = os.path.normpath(os.path.join(base, inc))
        if os.path.exists(p):
            inner = open(p, encoding="utf-8", errors="replace").read()
            return _expand_includes(inner, os.path.dirname(p), depth + 1)
        return ""
    return re.sub(r'#include\s+"([^"]+)"', rep, text)


def parse_sfz(path):
    base = os.path.dirname(os.path.abspath(path))
    text = open(path, encoding="utf-8", errors="replace").read()
    text = _expand_includes(text, base)
    text = re.sub(r"//[^\n]*", "", text)
    # <control> の default_path 等は全体既定として扱う
    ctrl_defaults = {}
    for sec in re.findall(r"<control>(.*?)(?=<\w+>|\Z)", text, re.S):
        for m in re.finditer(r"(\w+)=([^\s]+)", sec):
            ctrl_defaults[m.group(1)] = m.group(2)
    # パス解決に失敗したときのためのファイル名索引（2階層上まで）
    name_index = {}
    for up in (base, os.path.dirname(base), os.path.dirname(os.path.dirname(base))):
        if os.path.isdir(up):
            for root, _dirs, files in os.walk(up):
                for fn in files:
                    if fn.lower().endswith(".wav"):
                        name_index.setdefault(fn.lower(), os.path.join(root, fn))
        if len(name_index) > 8000:
            break
    regions = []
    glob, group, cur = {}, {}, None

    def flush():
        if cur is not None and "sample" in cur:
            regions.append(dict(cur))

    for token in re.split(r"(<global>|<group>|<region>)", text):
        if token == "<global>":
            flush(); cur = None; group = {}; glob = {}
            cur_dict = glob
            continue
        if token == "<group>":
            flush(); cur = None; group = {}
            cur_dict = group
            continue
        if token == "<region>":
            flush()
            cur = dict(glob); cur.update(group)
            cur_dict = cur
            continue
        if token.strip() == "":
            continue
        if cur is None and "cur_dict" not in dir():
            cur_dict = glob
        for m in re.finditer(r"(\w+)=([^\s]+(?: [^\s=]+)*?)(?=\s+\w+=|\s*$)", token):
            k, v = m.group(1), m.group(2).strip()
            try:
                cur_dict[k] = float(v) if ("." in v or k in
                    ("volume", "tune", "pan")) else int(v)
            except ValueError:
                cur_dict[k] = v
    flush()
    out = []
    for r in regions:
        sample = str(r["sample"]).replace("\\", "/")
        if "noise" in sample.lower():
            continue
        dp = str(r.get("default_path",
                       ctrl_defaults.get("default_path", ""))).replace("\\", "/")
        full = os.path.normpath(os.path.join(base, dp, sample))
        if not os.path.exists(full):
            full = name_index.get(os.path.basename(sample).lower(), full)
        if not os.path.exists(full):
            continue
        if "key" in r:
            lo = hi = kc = int(r["key"])
        else:
            lo = int(r.get("lokey", 0)); hi = int(r.get("hikey", 127))
            kc = int(r.get("pitch_keycenter", lo))
        out.append({"path": full, "lo": lo, "hi": hi, "kc": kc,
                    "lovel": int(r.get("lovel", 0)),
                    "hivel": int(r.get("hivel", 127)),
                    "vol_db": float(r.get("volume", 0)),
                    "tune": float(r.get("tune", 0))})
    return out


def regions_from_folder(folder):
    out = []
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".wav"):
            continue
        midi = note_to_midi(fn)
        if midi is None:
            continue
        out.append({"path": os.path.join(folder, fn), "lo": midi, "hi": midi,
                    "kc": midi, "lovel": 0, "hivel": 127, "vol_db": 0,
                    "tune": 0})
    return out


def main():
    args = sys.argv[1:]
    opts = {"mode": "oneshot", "tail": 1.5, "fade_ms": 60, "gain": 1.0}
    pos = []
    i = 0
    sfz = folder = None
    while i < len(args):
        a = args[i]
        if a == "--sfz":
            sfz = args[i + 1]; i += 2
        elif a == "--folder":
            folder = args[i + 1]; i += 2
        elif a == "--mode":
            opts["mode"] = args[i + 1]; i += 2
        elif a == "--tail":
            opts["tail"] = float(args[i + 1]); i += 2
        elif a == "--fade-ms":
            opts["fade_ms"] = float(args[i + 1]); i += 2
        elif a == "--gain":
            opts["gain"] = float(args[i + 1]); i += 2
        else:
            pos.append(a); i += 1
    events_path, out_path = pos[0], pos[1]
    regions = parse_sfz(sfz) if sfz else regions_from_folder(folder)
    if not regions:
        print("no regions"); sys.exit(1)
    print(f"regions: {len(regions)} "
          f"pitch {min(r['kc'] for r in regions)}..{max(r['kc'] for r in regions)}")

    events = json.load(open(events_path))
    total = max((e["start"] + e["dur"] for e in events), default=0) + opts["tail"] + 0.3
    buf = np.zeros(int(total * SR), dtype=np.float32)
    cache = {}
    rr_counter = {}
    fade_n = int(opts["fade_ms"] / 1000 * SR)

    def get(path):
        if path not in cache:
            try:
                cache[path] = load_wav(path)
            except Exception:
                cache[path] = None
        return cache[path]

    for e in events:
        pitch, vel = int(e["pitch"]), int(e["vel"])
        cand = [r for r in regions if r["lo"] <= pitch <= r["hi"]
                and r["lovel"] <= vel <= r["hivel"]]
        if not cand:
            near = min(regions, key=lambda r: abs(r["kc"] - pitch))
            cand = [r for r in regions if r["kc"] == near["kc"]
                    and r["lovel"] <= vel <= r["hivel"]] or [near]
        key = (cand[0]["kc"], cand[0]["lo"], cand[0]["hi"])
        idx = rr_counter.get(key, 0)
        rr_counter[key] = idx + 1
        r = cand[idx % len(cand)]
        src = get(r["path"])
        if src is None:
            continue
        ratio = 2.0 ** ((pitch - r["kc"] + r["tune"] / 100.0) / 12.0)
        n_out = int(len(src) / ratio)
        seg = np.interp(np.arange(n_out) * ratio, np.arange(len(src)), src)
        if opts["mode"] == "sustain":
            want = int((e["dur"] + 0.25) * SR)
            if len(seg) > want:
                seg = seg[:want]
        else:
            want = int((e["dur"] + opts["tail"]) * SR)
            if len(seg) > want:
                seg = seg[:want]
        if len(seg) > fade_n:
            seg[-fade_n:] *= np.linspace(1.0, 0.0, fade_n) ** 1.5
        g = (vel / 127.0) ** 1.5 * (10 ** (r["vol_db"] / 20.0)) * opts["gain"]
        start = int(e["start"] * SR)
        end = min(len(buf), start + len(seg))
        if end > start:
            buf[start:end] += seg[:end - start] * g
    peak = float(np.abs(buf).max()) if len(buf) else 0.0
    if peak > 0:
        buf *= 0.89 / peak
    import wave as wavmod
    w = wavmod.open(out_path, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(buf, -1, 1) * 32767).astype("<i2").tobytes())
    w.close()
    print(f"wrote {out_path} {len(buf)/SR:.1f}s")


if __name__ == "__main__":
    main()
