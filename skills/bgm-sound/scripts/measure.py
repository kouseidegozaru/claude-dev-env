#!/usr/bin/env python3
"""音響計測ハーネス（システム python3 + numpy で実行する）。

使い方:
  python3 measure.py AUDIO [AUDIO ...]            指標を JSON で出す
  python3 measure.py --gate AUDIO --baseline BASE.json [--spec SPEC.py]
  python3 measure.py --seam LOOP_AUDIO [--ref MASTERED_AUDIO]
  python3 measure.py --loop AUDIO                 ループ素材として計測する

指標の定義:
  duration_s        ffprobe の長さ
  lufs_i            統合ラウドネス（ffmpeg loudnorm 解析パス, EBU R128）
  true_peak_dbtp    トゥルーピーク [dBTP]
  lra_lu            ラウドネスレンジ [LU]（ループ素材は3周連結の2-3周目でも測る）
  peak_dbfs         波形ピーク [dBFS]
  plr               トゥルーピーク - 統合ラウドネス [dB]
  psr_min           PSR（最も大きい短時間ラウドネスの時点でのトゥルーピークとの差、
                    ebur128 フレームログから取得）
  max_shortterm_lufs 最大短時間ラウドネス（ebur128 フレームログ）
  bands_pct         7帯域エネルギー比 [%]（20-60/60-250/250-500/500-2k/2-5k/5-8k/8-16k）
  band_low_pct      20-250Hz の集約値（bands_pct からの派生、従来互換）
  band_high_pct     4-16kHz の集約値（bands_pct からの派生）
  centroid_hz       スペクトル重心の平均 [Hz]
  ltas_db           長期平均スペクトル（1/3 オクターブ平滑、最大帯域を 0dB とする相対値）
  tilt_db_per_oct   LTAS の傾斜（60Hz-12kHz の回帰）[dB/oct]。記録・処方の入力であり
                    ゲートの深刻度は持たない
  spectral_stats    spread / skewness / flatness / rolloff85 のフレーム分布（中央値と IQR）
  stereo            L/R 相関（全体と1秒窓中央値）と Side/Mid 比 [%]
  tail_s            -60dBFS を最後に超えた時点から末尾までの長さ [s]
  distance_report   基準との距離を見るための特徴量ブロック（合否は付けない）
  fatigue_flags     長時間ループ再生の疲労フラグ（high_freq / dynamic_stasis / short_cycle）

納品ゲート（--gate）の判定:
  ハード (FAIL): LUFS が目標 ± tol、トゥルーピークが上限以下、PLR 8未満、
    最小 PSR 7未満、LRA が基準 ±5 LU 超、低域比が基準 ± low_tol_pt、
    重心が基準 × [0.5, 2.0]、長さが仕様の概算 ± duration_tol_s
  WARN: PLR 10未満、最小 PSR 9未満、最大短時間ラウドネスが目標 +6 LU 超、
    LRA が基準 ±3 LU 超、帯域比の許容外れ（単一基準曲の間は全帯域 WARN 止まり。
    FAIL は場面コーパス（最低3曲）の帯ができた場合に「2帯域以上の外れ」だけ）
基準 JSON は measure.py BASE_AUDIO --json-out base.json で作る。
"""
import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile

import numpy as np

SR = 44100
TIMEOUT = 600

BANDS = [("20-60", 20, 60), ("60-250", 60, 250), ("250-500", 250, 500),
         ("500-2k", 500, 2000), ("2k-5k", 2000, 5000), ("5k-8k", 5000, 8000),
         ("8k-16k", 8000, 16000)]
# 帯域比の初期許容 [pt]（場面コーパスで後から較正する）
BAND_TOL_PT = {"60-250": 15.0, "250-500": 5.0, "2k-5k": 5.0}
BAND_TOL_DEFAULT_PT = 7.0


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


def loop_loudness(path, dur):
    """ループ素材を3周連結し、1周目を捨てて2-3周目でラウドネスを測る。

    1周だけでは継ぎ目を跨ぐ尾の定常状態が見えないため、LRA は定常部で測る。
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-stream_loop", "2", "-i", path,
         "-ss", f"{dur:.3f}", "-vn",
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, timeout=TIMEOUT)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("ループ LRA の loudnorm JSON が取れなかった: " + path)
    d = json.loads(m[-1])
    return {"lufs_i_loop": round(float(d["input_i"]), 2),
            "lra_loop_lu": round(float(d["input_lra"]), 2)}


def ebur128_stats(path):
    """ebur128 のフレームログから最大短時間ラウドネスを取る。

    PSR は「最も大きい短時間ラウドネスの時点での、トゥルーピークとの差」
    として定義する（静かな区間ではピークと短時間ラウドネスの差が無意味に
    開くため、フレーム単位の最小値は指標にならない）。
    astats はラウドネスを出さないため、必ず ebur128 のログ解析で取る。
    """
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path, "-vn",
         "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, timeout=TIMEOUT)
    pat = re.compile(r"\bS:\s*(-?[\d.]+)\s")
    sts = []
    for line in r.stderr.splitlines():
        m = pat.search(line)
        if m:
            s = float(m.group(1))
            if s > -120.0:
                sts.append(s)
    if not sts:
        return {"max_shortterm_lufs": None}
    return {"max_shortterm_lufs": round(max(sts), 2)}


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


def _dist(vals, nd=4):
    v = np.asarray(vals, dtype=np.float64)
    v = v[np.isfinite(v)]
    if len(v) == 0:
        return {"median": None, "iqr": None}
    q25, q50, q75 = np.percentile(v, [25, 50, 75])
    return {"median": round(float(q50), nd), "iqr": round(float(q75 - q25), nd)}


def analyze(x):
    """ステレオ波形 (n, 2) からスペクトル系・ステレオ系の指標を一括で出す。"""
    mono = x.mean(axis=1)
    nfft, hop = 2048, 1024
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / SR)
    mags = []
    for s in range(0, len(mono) - nfft, hop):
        mags.append(np.abs(np.fft.rfft(mono[s:s + nfft] * win)))
    out = {}
    if mags:
        mags = np.array(mags)                # (frames, bins)
        power = mags ** 2
        total = power.sum(axis=1) + 1e-20    # (frames,)
        grand = power.sum()
        # 帯域別エネルギー比
        bands = {}
        for name, lo, hi in BANDS:
            sel = (freqs >= lo) & (freqs < hi)
            bands[name] = round(float(power[:, sel].sum() / grand * 100), 2)
        out["bands_pct"] = bands
        out["band_low_pct"] = round(bands["20-60"] + bands["60-250"], 2)
        out["band_high_pct"] = round(bands["5k-8k"] + bands["8k-16k"], 2)
        # フレーム単位のスペクトル統計
        centroid = (freqs[None, :] * power).sum(axis=1) / total
        var = ((freqs[None, :] - centroid[:, None]) ** 2 * power).sum(axis=1) / total
        spread = np.sqrt(np.maximum(var, 0.0))
        with np.errstate(divide="ignore", invalid="ignore"):
            skew = (((freqs[None, :] - centroid[:, None]) ** 3 * power).sum(axis=1)
                    / total) / np.maximum(spread, 1e-9) ** 3
        band_sel = (freqs >= 20) & (freqs < 16000)
        pb = power[:, band_sel] + 1e-20
        flat = np.exp(np.log(pb).mean(axis=1)) / pb.mean(axis=1)
        cum = np.cumsum(power, axis=1) / total[:, None]
        roll_idx = (cum < 0.85).sum(axis=1)
        roll = freqs[np.minimum(roll_idx, len(freqs) - 1)]
        out["centroid_hz"] = round(float(np.mean(centroid)), 1)
        out["spectral_stats"] = {
            "spread_hz": _dist(spread), "skewness": _dist(skew),
            "flatness": _dist(flat, nd=6), "rolloff85_hz": _dist(roll)}
        # LTAS（時間平均パワー → 1/3 オクターブ平滑、最大帯域を 0dB とする）
        ltas_pow = power.mean(axis=0)
        centers, vals = [], []
        fc = 25.0
        while fc < 16000:
            lo, hi = fc * 2 ** (-1 / 6), fc * 2 ** (1 / 6)
            sel = (freqs >= lo) & (freqs < hi)
            if sel.any():
                centers.append(fc)
                vals.append(float(ltas_pow[sel].sum()))
            fc *= 2 ** (1 / 3)
        vals = np.array(vals)
        ltas_db = 10 * np.log10(np.maximum(vals, 1e-20) / max(vals.max(), 1e-20))
        out["ltas_db"] = {str(int(round(c))): round(float(v), 2)
                          for c, v in zip(centers, ltas_db)}
        sel = [(math.log2(c), v) for c, v in zip(centers, ltas_db)
               if 60 <= c <= 12000]
        if len(sel) >= 2:
            xs = np.array([a for a, _ in sel])
            ys = np.array([b for _, b in sel])
            slope = float(np.polyfit(xs, ys, 1)[0])
            out["tilt_db_per_oct"] = round(slope, 3)
        else:
            out["tilt_db_per_oct"] = None
    else:
        out.update({"bands_pct": {name: 0.0 for name, _, _ in BANDS},
                    "band_low_pct": 0.0, "band_high_pct": 0.0,
                    "centroid_hz": 0.0, "spectral_stats": {},
                    "ltas_db": {}, "tilt_db_per_oct": None})
    # ステレオ像
    L, R = x[:, 0], x[:, 1]
    if len(x) and L.std() > 1e-12 and R.std() > 1e-12:
        corr_all = float(np.corrcoef(L, R)[0, 1])
    else:
        corr_all = 1.0
    win_n = SR
    corrs = []
    for s in range(0, len(x) - win_n + 1, win_n):
        lw, rw = L[s:s + win_n], R[s:s + win_n]
        if lw.std() > 1e-9 and rw.std() > 1e-9:
            corrs.append(float(np.corrcoef(lw, rw)[0, 1]))
    mid = (L + R) / 2.0
    side = (L - R) / 2.0
    rms_mid = float(np.sqrt(np.mean(mid ** 2))) if len(x) else 0.0
    rms_side = float(np.sqrt(np.mean(side ** 2))) if len(x) else 0.0
    out["stereo"] = {
        "corr_overall": round(corr_all, 3),
        "corr_median_1s": round(float(np.median(corrs)), 3) if corrs else None,
        "side_mid_ratio_pct": round(rms_side / rms_mid * 100, 2) if rms_mid > 1e-12 else 0.0}
    # 尾の長さ（-60dBFS を最後に超えた時点から末尾まで）
    env = np.max(np.abs(x), axis=1) if len(x) else np.array([])
    above = np.nonzero(env > 1e-3)[0]
    out["tail_s"] = round(float((len(env) - 1 - above[-1]) / SR), 3) if len(above) else round(len(env) / SR, 3)
    return out


def window_loudness_sd(mono, win_s=30.0):
    """30秒窓ごとの RMS [dB] の標準偏差（ダイナミクスの静止の代理指標）。"""
    win = int(SR * win_s)
    n = len(mono) // win
    if n < 3:
        return None
    s = mono[: n * win].reshape(n, win)
    rms = np.sqrt(np.mean(s ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    return round(float(np.std(db)), 3)


def fatigue_flags(m, is_loop, baseline):
    """長時間ループ再生の疲労フラグ。直し先は SKILL / references 側の対応表で固定する。

    high_freq:      4-16kHz 帯域比が同場面基準 +5pt 超（基準が無い場合は判定しない）
    dynamic_stasis: LRA < 3 LU、または30秒窓ラウドネスの SD < 0.5 LU
    short_cycle:    ループ長が帯の下限（60秒）未満（ループ素材の場合のみ）
    """
    flags = {"high_freq": None, "dynamic_stasis": None, "short_cycle": None,
             "band_high_pct": m.get("band_high_pct"),
             "window_loudness_sd_30s": m.get("window_loudness_sd_30s")}
    if baseline and "bands_pct" in baseline:
        base_high = (baseline["bands_pct"].get("5k-8k", 0.0)
                     + baseline["bands_pct"].get("8k-16k", 0.0))
        flags["high_freq"] = bool(m["band_high_pct"] > base_high + 5.0)
        flags["baseline_band_high_pct"] = round(base_high, 2)
    lra = m.get("lra_loop_lu", m.get("lra_lu"))
    sd = m.get("window_loudness_sd_30s")
    flags["dynamic_stasis"] = bool(
        (lra is not None and lra < 3.0) or (sd is not None and sd < 0.5))
    if is_loop:
        flags["short_cycle"] = bool(m["duration_s"] < 60.0)
    return flags


def measure(path, is_loop=False):
    out = {"file": path, "duration_s": round(probe_duration(path), 2)}
    out.update(loudness(path))
    if is_loop:
        out.update(loop_loudness(path, out["duration_s"]))
    out.update(ebur128_stats(path))
    out["psr_min"] = (round(out["true_peak_dbtp"] - out["max_shortterm_lufs"], 2)
                      if out.get("max_shortterm_lufs") is not None else None)
    x = decode(path)
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    out["peak_dbfs"] = round(20 * math.log10(max(peak, 1e-12)), 2)
    out["plr"] = round(out["true_peak_dbtp"] - out["lufs_i"], 2)
    out.update(analyze(x))
    out["window_loudness_sd_30s"] = window_loudness_sd(x.mean(axis=1))
    out["distance_report"] = {
        "note": "このレポートは作曲の良し悪しを判定しない",
        "spectral_stats": out["spectral_stats"],
        "stereo": out["stereo"],
        "tail_s": out["tail_s"],
        "bands_pct": out["bands_pct"],
        "tilt_db_per_oct": out["tilt_db_per_oct"],
        "ltas_db": out["ltas_db"]}
    out["fatigue_flags"] = fatigue_flags(out, is_loop, None)
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
    if spec.get("loop"):
        gate.setdefault("loop", True)
    return gate


def gate(audio, baseline_path, spec_path, cli_targets=None):
    g = {"lufs_target": -14.0, "lufs_tol": 0.5, "tp_max": -1.0,
         "low_tol_pt": 15.0, "centroid_ratio": [0.5, 2.0], "duration_tol_s": 4.0}
    g.update(spec_gate_params(spec_path))
    if cli_targets:
        # CLI（レンダリング時に選んだ delivery プリセット等）は spec の gate 辞書より
        # 優先する。マスタリングの目標とゲートの目標が食い違うのを防ぐため。
        g.update({k: v for k, v in cli_targets.items() if v is not None})
    is_loop = bool(g.get("loop"))
    m = measure(audio, is_loop=is_loop)
    base = None
    if baseline_path:
        with open(baseline_path, encoding="utf-8") as f:
            base = json.load(f)
    m["fatigue_flags"] = fatigue_flags(m, is_loop, base)
    checks = []

    def chk(name, status, detail):
        # status: "pass" | "warn" | "fail"。pass フィールドは従来互換（fail 以外は True）
        checks.append({"check": name, "status": status,
                       "pass": status != "fail", "detail": detail})

    chk("lufs", "pass" if abs(m["lufs_i"] - g["lufs_target"]) <= g["lufs_tol"] else "fail",
        f"{m['lufs_i']} LUFS (目標 {g['lufs_target']} ± {g['lufs_tol']})")
    chk("true_peak", "pass" if m["true_peak_dbtp"] <= g["tp_max"] else "fail",
        f"{m['true_peak_dbtp']} dBTP (上限 {g['tp_max']})")
    # PLR（S8 の2層化に対する例外として、配信実務由来のためハード側に置く）
    if m["plr"] < 8.0:
        chk("plr", "fail", f"PLR {m['plr']} dB (8未満は FAIL)")
    elif m["plr"] < 10.0:
        chk("plr", "warn", f"PLR {m['plr']} dB (10未満は WARN)")
    else:
        chk("plr", "pass", f"PLR {m['plr']} dB")
    # 最小 PSR（ebur128 フレームログ由来）
    if m.get("psr_min") is not None:
        if m["psr_min"] < 7.0:
            chk("psr_min", "fail", f"最小 PSR {m['psr_min']} dB (7未満は FAIL)")
        elif m["psr_min"] < 9.0:
            chk("psr_min", "warn", f"最小 PSR {m['psr_min']} dB (9未満は WARN)")
        else:
            chk("psr_min", "pass", f"最小 PSR {m['psr_min']} dB")
    # 最大短時間ラウドネス（出典 EBU R 128 s1 は +5 LU、BGM 向けに +6 LU へ緩和した WARN）
    if m.get("max_shortterm_lufs") is not None:
        over = m["max_shortterm_lufs"] - g["lufs_target"]
        chk("max_shortterm", "warn" if over > 6.0 else "pass",
            f"最大短時間 {m['max_shortterm_lufs']} LUFS (目標 +{over:.1f} LU、+6 超で WARN)")
    # LRA（基準相対。ループ素材は3周連結の2-3周目の値で判定する）
    lra_val = m.get("lra_loop_lu", m["lra_lu"])
    lra_label = "LRA(ループ定常部)" if "lra_loop_lu" in m else "LRA"
    if m["duration_s"] < 60.0:
        chk("lra", "pass",
            f"{lra_label} {lra_val} LU (60秒未満の素材では合否に使わない・参考値)")
    elif base and "lra_lu" in base:
        base_lra = base.get("lra_loop_lu", base["lra_lu"])
        diff = lra_val - base_lra
        note = ""
        if base_lra - 5.0 < 0:
            note = "（基準 LRA が小さいため下側は到達不能で、実質片側ゲート）"
        if abs(diff) > 5.0:
            chk("lra", "fail", f"{lra_label} {lra_val} LU (基準 {base_lra} との差 {diff:+.1f} LU、±5 超は FAIL){note}")
        elif abs(diff) > 3.0:
            chk("lra", "warn", f"{lra_label} {lra_val} LU (基準 {base_lra} との差 {diff:+.1f} LU、±3 超は WARN){note}")
        else:
            chk("lra", "pass", f"{lra_label} {lra_val} LU (基準 {base_lra} との差 {diff:+.1f} LU){note}")
    else:
        chk("lra", "pass", f"{lra_label} {lra_val} LU (基準が無いため相対判定なし)")
    if lra_val < 2.0:
        chk("lra_flat", "warn", f"{lra_label} {lra_val} LU (2未満は「潰れ/単調」の注記)")
    if lra_val > 12.0:
        chk("lra_wide", "warn", f"{lra_label} {lra_val} LU (12超は試聴で確認する注記)")
    if base:
        lo, hi = base["band_low_pct"] - g["low_tol_pt"], base["band_low_pct"] + g["low_tol_pt"]
        chk("band_low", "pass" if lo <= m["band_low_pct"] <= hi else "fail",
            f"{m['band_low_pct']}% (基準 {base['band_low_pct']}% の許容 {lo:.1f}..{hi:.1f})")
        rlo, rhi = base["centroid_hz"] * g["centroid_ratio"][0], base["centroid_hz"] * g["centroid_ratio"][1]
        chk("centroid", "pass" if rlo <= m["centroid_hz"] <= rhi else "fail",
            f"{m['centroid_hz']} Hz (基準 {base['centroid_hz']} Hz の許容 {rlo:.0f}..{rhi:.0f})")
        # 7帯域の許容判定: 単一基準曲の間は全帯域 WARN 止まり。
        # FAIL は場面コーパス（最低3曲）の帯ができた場面だけに「2帯域以上の外れ」で適用する。
        if "bands_pct" in base:
            out_bands = []
            for name, _, _ in BANDS:
                tol = BAND_TOL_PT.get(name, BAND_TOL_DEFAULT_PT)
                d = m["bands_pct"][name] - base["bands_pct"][name]
                if abs(d) > tol:
                    out_bands.append(f"{name} ({d:+.1f}pt)")
            corpus = (base.get("corpus_size", 0) >= 3) or ("bands_band" in base)
            if len(out_bands) >= 2 and corpus:
                chk("bands", "fail", "許容外れ帯域: " + ", ".join(out_bands))
            elif out_bands:
                chk("bands", "warn", "許容外れ帯域: " + ", ".join(out_bands)
                    + ("（2帯域以上だが FAIL は場面コーパスの帯がある場合のみ）" if len(out_bands) >= 2 else ""))
            else:
                chk("bands", "pass", "全帯域が許容内")
    if "duration_expected_s" in g:
        # 音源のリリース/リバーブの尾が付くため、上側だけ追加で 3s 許す
        lo = g["duration_expected_s"] - g["duration_tol_s"]
        hi = g["duration_expected_s"] + g["duration_tol_s"] + 3.0
        chk("duration", "pass" if lo <= m["duration_s"] <= hi else "fail",
            f"{m['duration_s']}s (仕様概算 {g['duration_expected_s']}s の許容 {lo:.1f}..{hi:.1f}s)")
    # 距離レポート（基準がある場合は差分を併記する。合否は付けない）
    dist = dict(m["distance_report"])
    if base:
        diff = {}
        if "bands_pct" in base:
            diff["bands_diff_pt"] = {
                name: round(m["bands_pct"][name] - base["bands_pct"][name], 2)
                for name, _, _ in BANDS}
        if "centroid_hz" in base and base["centroid_hz"]:
            diff["centroid_ratio"] = round(m["centroid_hz"] / base["centroid_hz"], 3)
        if "stereo" in base:
            diff["stereo_corr_diff"] = round(
                m["stereo"]["corr_overall"] - base["stereo"]["corr_overall"], 3)
            diff["side_mid_diff_pt"] = round(
                m["stereo"]["side_mid_ratio_pct"] - base["stereo"]["side_mid_ratio_pct"], 2)
        if "tilt_db_per_oct" in base and base["tilt_db_per_oct"] is not None \
                and m["tilt_db_per_oct"] is not None:
            diff["tilt_diff_db_per_oct"] = round(
                m["tilt_db_per_oct"] - base["tilt_db_per_oct"], 3)
        if "tail_s" in base:
            diff["tail_diff_s"] = round(m["tail_s"] - base["tail_s"], 3)
        dist["diff_vs_baseline"] = diff
    ok = all(c["status"] != "fail" for c in checks)
    return {"audio": audio, "metrics": m, "baseline": base,
            "checks": checks,
            "warnings": [c["check"] for c in checks if c["status"] == "warn"],
            "distance_report": dist,
            "verdict": "PASS" if ok else "FAIL"}


def _rms_db_series(x, win):
    n = len(x) // win
    if n == 0:
        return np.array([])
    s = x[: n * win].reshape(n, win)
    r = np.sqrt(np.mean(s ** 2, axis=1) + 1e-12)
    return 20 * np.log10(r)


def _seam_metrics_at(c, p, internal_diff_p99, win_sizes):
    """連結波形 c の継ぎ目位置 p（サンプル）まわりの指標を出す。"""
    res = {}
    jump = abs(float(c[p]) - float(c[p - 1])) if 0 < p < len(c) else 0.0
    res["jump_ratio"] = round(jump / internal_diff_p99, 3) if internal_diff_p99 > 0 else 0.0
    steps = {}
    for ms in win_sizes:
        win = int(SR * ms / 1000)
        s = _rms_db_series(c, win)
        idx = p // win
        lo, hi = max(1, idx - 5), min(len(s) - 1, idx + 5)
        steps[f"rms_step_{ms}ms_db"] = round(
            float(np.max(np.abs(np.diff(s[lo:hi + 1])))) if hi > lo else 0.0, 2)
    res.update(steps)
    return res


def seam(loop_path, ref_path=None):
    """継ぎ目判定（合議制）: ループ素材を3周連結した波形の2か所の継ぎ目で測る。

    指標: 相対跳び比（内部差分の p99 との比）、多解像度 RMS 窓（10/25/100ms）、
    スペクトルフラックス比、帯域別段差、位相相関、DC 差・ノイズフロア差。
    L/R・Mid/Side の各系列で測り、最悪値を採用する。
    """
    x = decode(loop_path)                    # (n, 2)
    n = len(x)
    concat = np.tile(x, (3, 1))
    seams_at = [n, 2 * n]
    series = {"L": concat[:, 0], "R": concat[:, 1],
              "Mid": concat.mean(axis=1), "Side": (concat[:, 0] - concat[:, 1]) / 2.0}
    checks = []

    def chk(name, status, detail):
        checks.append({"check": name, "status": status,
                       "pass": status != "fail", "detail": detail})

    # 相対跳び比と RMS 段差（系列ごとの最悪値）
    worst = {}
    per_seam = []
    for p in seams_at:
        entry = {"at_s": round(p / SR, 3)}
        for sname, c in series.items():
            diffs = np.abs(np.diff(c))
            mask = np.ones(len(diffs), dtype=bool)
            for q in seams_at:
                lo = max(0, q - int(SR * 0.1))
                hi = min(len(diffs), q + int(SR * 0.1))
                mask[lo:hi] = False
            p99 = float(np.percentile(diffs[mask], 99)) if mask.any() else 0.0
            met = _seam_metrics_at(c, p, p99, (10, 25, 100))
            entry[sname] = met
            for k, v in met.items():
                worst[k] = max(worst.get(k, 0.0), v)
        per_seam.append(entry)
    jr = worst.get("jump_ratio", 0.0)
    if jr > 1.5:
        chk("jump_ratio", "fail", f"相対跳び比 {jr} (1.5超は FAIL)")
    elif jr > 1.0:
        chk("jump_ratio", "warn", f"相対跳び比 {jr} (1.0超は WARN)")
    else:
        chk("jump_ratio", "pass", f"相対跳び比 {jr}")
    # スペクトルフラックス比（Mid 系列、継ぎ目フレーム vs 内部 p95）
    mid = series["Mid"]
    nfft, hop = 2048, 1024
    win = np.hanning(nfft)
    mags = []
    for s in range(0, len(mid) - nfft, hop):
        mags.append(np.abs(np.fft.rfft(mid[s:s + nfft] * win)))
    flux_ratio = None
    if len(mags) > 4:
        mags = np.array(mags)
        num = np.abs(np.diff(mags, axis=0)).sum(axis=1)
        den = mags[:-1].sum(axis=1) + 1e-12
        flux = num / den
        seam_frames = set()
        for p in seams_at:
            seam_frames.add(p // hop)
        internal = np.array([f for i, f in enumerate(flux)
                             if all(abs(i - sf) > 2 for sf in seam_frames)])
        seam_flux = [flux[sf - 1] for sf in seam_frames if 0 < sf <= len(flux)]
        if len(internal) and seam_flux:
            flux_ratio = round(float(max(seam_flux) / np.percentile(internal, 95)), 3)
    if flux_ratio is None:
        chk("flux_ratio", "warn", "フラックス比を算出できなかった（素材が短い）")
    elif flux_ratio > 1.5:
        chk("flux_ratio", "fail", f"スペクトルフラックス比 {flux_ratio} (1.5超は FAIL)")
    elif flux_ratio > 1.2:
        chk("flux_ratio", "warn", f"スペクトルフラックス比 {flux_ratio} (1.2超は WARN)")
    else:
        chk("flux_ratio", "pass", f"スペクトルフラックス比 {flux_ratio}")
    # 帯域別段差と位相相関・DC（Mid 系列、各継ぎ目）
    band_centers = [125, 250, 500, 1000, 2000, 4000, 8000]
    band_step_max, rho_min, dc_diff_db = 0.0, 1.0, -120.0
    for p in seams_at:
        w = int(SR * 0.1)
        before = mid[p - w:p]
        after = mid[p:p + w]
        if len(before) < w or len(after) < w:
            continue
        fb = np.abs(np.fft.rfft(before * np.hanning(w)))
        fa = np.abs(np.fft.rfft(after * np.hanning(w)))
        freqs_w = np.fft.rfftfreq(w, 1.0 / SR)
        band_db = []
        for fc in band_centers:
            lo, hi = fc * 2 ** (-0.5), fc * 2 ** 0.5
            sel = (freqs_w >= lo) & (freqs_w < hi)
            if not sel.any():
                continue
            eb = 10 * np.log10(float((fb[sel] ** 2).sum()) + 1e-20)
            ea = 10 * np.log10(float((fa[sel] ** 2).sum()) + 1e-20)
            band_db.append((max(eb, ea), abs(ea - eb)))
        if band_db:
            # 最も強い帯域から 40dB 以上下の帯域はノイズフロア同然で
            # dB 差が無意味に開くため、段差判定から外す
            strongest = max(v for v, _ in band_db)
            band_step_max = max([d for v, d in band_db if v >= strongest - 40.0]
                                or [0.0])
        w50 = int(SR * 0.05)
        tail_w, head_w = mid[p - w50:p], mid[p:p + w50]
        if tail_w.std() > 1e-12 and head_w.std() > 1e-12:
            rho_min = min(rho_min, float(np.corrcoef(tail_w, head_w)[0, 1]))
        dc_diff_db = max(dc_diff_db, 20 * math.log10(
            abs(float(after.mean()) - float(before.mean())) + 1e-12))
    chk("band_step", "fail" if band_step_max > 3.0 else "pass",
        f"帯域別段差の最大 {round(band_step_max, 2)} dB (3dB 超は FAIL)")
    if rho_min < -0.3:
        chk("phase_corr", "fail", f"位相相関 rho {round(rho_min, 3)} (-0.3 未満は接続点の再探索が必須)")
    else:
        chk("phase_corr", "pass", f"位相相関 rho {round(rho_min, 3)}")
    chk("dc_offset", "warn" if dc_diff_db > -60.0 else "pass",
        f"継ぎ目前後の DC 差 {round(dc_diff_db, 1)} dBFS (-60 dBFS 超は WARN)")
    # ノイズフロア差（周回ごとの 100ms 窓 RMS の 5 パーセンタイルの差）
    floors = []
    for cyc in range(3):
        seg = mid[cyc * n:(cyc + 1) * n]
        s = _rms_db_series(seg, int(SR * 0.1))
        if len(s):
            floors.append(float(np.percentile(s, 5)))
    nf_diff = max(floors) - min(floors) if floors else 0.0
    chk("noise_floor", "warn" if nf_diff > 2.0 else "pass",
        f"ノイズフロアの周回差 {round(nf_diff, 2)} dB (2dB 超は WARN)")

    out = {"loop_file": loop_path, "ref_file": ref_path,
           "seams": per_seam, "worst": {k: round(v, 3) for k, v in worst.items()},
           "flux_ratio": flux_ratio,
           "band_step_max_db": round(band_step_max, 2),
           "phase_corr_rho": round(rho_min, 3),
           "checks": checks,
           "warnings": [c["check"] for c in checks if c["status"] == "warn"]}
    # 従来互換: 素つなぎとの 100ms RMS 段差比較（--ref 指定時）
    if ref_path:
        def legacy_step(path, join_self):
            lx = decode(path, mono=True)
            ext = np.concatenate([lx, lx[: SR * 2]]) if join_self else lx
            s = _rms_db_series(ext, int(SR * 0.1))
            idx = len(lx) // int(SR * 0.1)
            lo, hi = max(1, idx - 5), min(len(s) - 1, idx + 5)
            return float(np.max(np.abs(np.diff(s[lo:hi + 1]))))
        out["seam_step_db_crossfaded"] = round(legacy_step(loop_path, True), 2)
        out["seam_step_db_butt"] = round(legacy_step(ref_path, True), 2)
    out["verdict"] = "PASS" if all(c["status"] != "fail" for c in checks) else "FAIL"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("audio", nargs="*")
    ap.add_argument("--json-out")
    ap.add_argument("--gate", metavar="AUDIO")
    ap.add_argument("--baseline")
    ap.add_argument("--spec")
    ap.add_argument("--seam", metavar="LOOP_AUDIO")
    ap.add_argument("--ref")
    ap.add_argument("--loop", action="store_true",
                    help="ループ素材として計測する（LRA は3周連結の2-3周目、疲労フラグの short_cycle を有効化）")
    ap.add_argument("--lufs-target", type=float)
    ap.add_argument("--lufs-tol", type=float)
    ap.add_argument("--tp-max", type=float)
    args = ap.parse_args()

    if args.seam:
        print(json.dumps(seam(args.seam, args.ref), ensure_ascii=False, indent=2))
        return 0
    if args.gate:
        cli = {"lufs_target": args.lufs_target, "lufs_tol": args.lufs_tol,
               "tp_max": args.tp_max, "loop": True if args.loop else None}
        res = gate(args.gate, args.baseline, args.spec, cli)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump(res, f, ensure_ascii=False, indent=2)
        return 0 if res["verdict"] == "PASS" else 1
    rows = [measure(p, is_loop=args.loop) for p in args.audio]
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(rows[0] if len(rows) == 1 else rows, f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
