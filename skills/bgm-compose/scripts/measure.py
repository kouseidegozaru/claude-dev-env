#!/usr/bin/env python3
"""音響計測ハーネス（システム python3 + numpy で実行する）。

使い方:
  python3 measure.py AUDIO [AUDIO ...]            指標を JSON で出す
  python3 measure.py --gate AUDIO --baseline BASE.json [--spec SPEC.py]
  python3 measure.py --seam LOOP_AUDIO --ref MASTERED_AUDIO

指標の定義:
  duration_s     ffprobe の長さ
  lufs_i         統合ラウドネス（ffmpeg loudnorm 解析パス, EBU R128）
  true_peak_dbtp トゥルーピーク [dBTP]
  peak_dbfs      波形ピーク [dBFS]
  band_low_pct   帯域エネルギー比 20-250Hz [%]（STFT 2048/1024, Hann）
  centroid_hz    スペクトル重心の平均 [Hz]

納品ゲート（--gate）の判定:
  (a) LUFS が目標 ± tol（既定 -14.0 ± 0.5）
  (b) トゥルーピークが上限以下（既定 -1.0 dBTP）
  (c) 低域比が基準 ± low_tol_pt（既定 15pt）、重心が基準 × [0.5, 2.0]（基準がある場合）
  (d) 長さが仕様の概算 ± duration_tol_s（既定 4s、--spec 指定時）
基準 JSON は measure.py BASE_AUDIO --json-out base.json で作る。
"""
import argparse
import importlib.util
import json
import math
import re
import subprocess
import sys

import numpy as np

SR = 44100
TIMEOUT = 600


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)


def probe_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path])
    return float(json.loads(r.stdout)["format"]["duration"])


def loudness(path):
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vn",
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=TIMEOUT)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("loudnorm JSON が取れなかった: " + path)
    d = json.loads(m[-1])
    return {"lufs_i": round(float(d["input_i"]), 2),
            "true_peak_dbtp": round(float(d["input_tp"]), 2),
            "lra_lu": round(float(d["input_lra"]), 2)}


def decode(path, mono=False):
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1" if mono else "2",
         "-ar", str(SR), "-f", "f32le", "-"],
        capture_output=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError("デコード失敗: " + path)
    x = np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)
    if mono:
        return x
    return x[: len(x) // 2 * 2].reshape(-1, 2)


def spectral(mono):
    nfft, hop = 2048, 1024
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / SR)
    mags = []
    for s in range(0, len(mono) - nfft, hop):
        mags.append(np.abs(np.fft.rfft(mono[s:s + nfft] * win)))
    if not mags:
        return {"band_low_pct": 0.0, "centroid_hz": 0.0}
    power = np.array(mags).T ** 2
    total = power.sum(axis=0) + 1e-20
    centroid = (freqs[:, None] * power).sum(axis=0) / total
    grand = power.sum()
    sel = (freqs >= 20) & (freqs < 250)
    return {"band_low_pct": round(float(power[sel].sum() / grand * 100), 2),
            "centroid_hz": round(float(np.mean(centroid)), 1)}


def measure(path):
    out = {"file": path, "duration_s": round(probe_duration(path), 2)}
    out.update(loudness(path))
    x = decode(path)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    out["peak_dbfs"] = round(20 * math.log10(max(peak, 1e-12)), 2)
    out.update(spectral(x.mean(axis=1)))
    return out


def spec_gate_params(spec_path):
    if not spec_path:
        return {}
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", spec_path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    spec = mod.SPEC
    gate = dict(spec.get("gate", {}))
    # 仕様からの概算の長さ（テンポマップ積分）
    bpb = int(spec.get("beats_per_bar", 4))
    changes = sorted(spec.get("tempo_changes", []))
    secs, cur_beat, cur_bpm = 0.0, 0, float(spec["bpm"])
    for bar, tbpm in changes:
        beat = (bar - 1) * bpb
        secs += (beat - cur_beat) * 60.0 / cur_bpm
        cur_beat, cur_bpm = beat, float(tbpm)
    secs += (int(spec["bars"]) * bpb - cur_beat) * 60.0 / cur_bpm
    gate["duration_expected_s"] = round(secs, 2)
    return gate


def gate(audio, baseline_path, spec_path):
    m = measure(audio)
    g = {"lufs_target": -14.0, "lufs_tol": 0.5, "tp_max": -1.0,
         "low_tol_pt": 15.0, "centroid_ratio": [0.5, 2.0], "duration_tol_s": 4.0}
    g.update(spec_gate_params(spec_path))
    base = None
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as f:
            base = json.load(f)
    checks = []

    def chk(name, ok, detail):
        checks.append({"check": name, "pass": bool(ok), "detail": detail})

    chk("lufs", abs(m["lufs_i"] - g["lufs_target"]) <= g["lufs_tol"],
        f"{m['lufs_i']} LUFS (目標 {g['lufs_target']} ± {g['lufs_tol']})")
    chk("true_peak", m["true_peak_dbtp"] <= g["tp_max"],
        f"{m['true_peak_dbtp']} dBTP (上限 {g['tp_max']})")
    if base:
        lo, hi = base["band_low_pct"] - g["low_tol_pt"], base["band_low_pct"] + g["low_tol_pt"]
        chk("band_low", lo <= m["band_low_pct"] <= hi,
            f"{m['band_low_pct']}% (基準 {base['band_low_pct']}% の許容 {lo:.1f}..{hi:.1f})")
        rlo, rhi = base["centroid_hz"] * g["centroid_ratio"][0], base["centroid_hz"] * g["centroid_ratio"][1]
        chk("centroid", rlo <= m["centroid_hz"] <= rhi,
            f"{m['centroid_hz']} Hz (基準 {base['centroid_hz']} Hz の許容 {rlo:.0f}..{rhi:.0f})")
    if "duration_expected_s" in g:
        # 音源のリリース/リバーブの尾が付くため、上側だけ追加で 3s 許す
        lo = g["duration_expected_s"] - g["duration_tol_s"]
        hi = g["duration_expected_s"] + g["duration_tol_s"] + 3.0
        chk("duration", lo <= m["duration_s"] <= hi,
            f"{m['duration_s']}s (仕様概算 {g['duration_expected_s']}s の許容 {lo:.1f}..{hi:.1f}s)")
    ok = all(c["pass"] for c in checks)
    return {"audio": audio, "metrics": m, "baseline": base,
            "checks": checks, "verdict": "PASS" if ok else "FAIL"}


def seam(loop_path, ref_path):
    """継ぎ目判定: 100ms 窓 RMS 系列の、継ぎ目付近の最大段差 [dB]。
    loop はクロスフェード済みループ素材、ref はクロスフェード前の素材。
    ref を頭尾で素つなぎした系列と比べて段差が減っているかを見る。"""
    def series(x):
        win = int(SR * 0.1)
        n = len(x) // win
        s = x[: n * win].reshape(n, win)
        r = np.sqrt(np.mean(s ** 2, axis=1) + 1e-12)
        return 20 * np.log10(r)

    def max_step_near(s, idx, half=5):
        lo, hi = max(1, idx - half), min(len(s) - 1, idx + half)
        return float(np.max(np.abs(np.diff(s[lo:hi + 1]))))

    lx = decode(loop_path, mono=True)
    loop_ext = np.concatenate([lx, lx[: SR * 2]])
    s_loop = series(loop_ext)
    step_loop = max_step_near(s_loop, len(lx) // int(SR * 0.1))

    rx = decode(ref_path, mono=True)
    butt = np.concatenate([rx, rx[: SR * 2]])
    s_butt = series(butt)
    step_butt = max_step_near(s_butt, len(rx) // int(SR * 0.1))

    return {"loop_file": loop_path, "ref_file": ref_path,
            "seam_step_db_crossfaded": round(step_loop, 2),
            "seam_step_db_butt": round(step_butt, 2),
            "verdict": "PASS" if step_loop < step_butt else "FAIL"}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audio", nargs="*")
    ap.add_argument("--json-out")
    ap.add_argument("--gate", metavar="AUDIO")
    ap.add_argument("--baseline")
    ap.add_argument("--spec")
    ap.add_argument("--seam", metavar="LOOP_AUDIO")
    ap.add_argument("--ref")
    args = ap.parse_args()

    if args.seam:
        if not args.ref:
            ap.error("--seam には --ref が必要です")
        print(json.dumps(seam(args.seam, args.ref), ensure_ascii=False, indent=2))
        return 0
    if args.gate:
        res = gate(args.gate, args.baseline, args.spec)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        return 0 if res["verdict"] == "PASS" else 1
    rows = [measure(p) for p in args.audio]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rows[0] if len(rows) == 1 else rows, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
