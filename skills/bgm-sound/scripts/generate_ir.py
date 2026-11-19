#!/usr/bin/env python3
"""生成 IR（インパルス応答）の生成スクリプト。

設計:
- 減衰包絡は RT60 でちょうど -60 dB になる指数関数 exp(-6.9078 * t / RT60)。
  以前の afade（線形に近いフェード）は名目 RT60 と実効の尾が
  一致しないため廃止した。
- 左右は別シードのノイズで分離する（左右同一ノイズはモノラル的な狭い尾になる）。
- 単位エネルギー正規化（チャンネルごとに sum(x^2) = 1）を行い、IR 種別を
  変えても wet 量の目安が再現するようにする。
- ダンピング（尾の暗さ）は生成時のローパスで焼き込む。場面対応:
  昼・朝 7kHz、夕 5.2kHz、夜 4-5kHz、雪 4kHz、plate 的主題曲 9kHz。
- 初期反射（ER）は疎なタップ（43/61/87/97 ms、振幅 0.5/0.35/0.25/0.18、
  約1.5 ms のノイズバースト）で近似する。生成 IR には実空間の ER が無い
  ことが弱点であり、このタップは近似に過ぎない点をここに明記する。
- プリディレイは IR 先頭の無音として焼き込む（--predelay-ms 指定時、
  ファイル名は <kind>_pd<ms>.wav）。

使い方:
  python3 generate_ir.py              # 不足分だけ生成（既存は残す）
  python3 generate_ir.py --force      # 全種を作り直す
  python3 generate_ir.py --kind plate --predelay-ms 22
出力先: ~/workspace/soundfonts/ir/
"""
import argparse
import os
import wave

import numpy as np

SR = 44100
IR_DIR = os.path.expanduser("~/workspace/soundfonts/ir")

# kind: (RT60 秒, ダンピング LP Hz, HP Hz, ER タップ振幅の倍率, シード)
# LP は空間の性格に対応する: room/ambience=明るめ 7kHz、hall=落ち着いた
# 5.2kHz、chamber=やや暗め 6kHz、plate=明るい 9kHz、spring=バネの帯域。
IR_SPECS = {
    "ambience": (0.45, 7000, 80, 1.0, 101),   # ごく短い空間。尾でなく ER 主体
    "room":     (1.4, 7000, 60, 0.8, 202),
    "spring":   (0.9, 5500, 250, 0.7, 303),
    "plate":    (1.9, 9000, 60, 0.6, 404),
    "chamber":  (2.1, 6000, 60, 0.8, 505),
    "hall":     (2.6, 5200, 50, 0.8, 606),
}
ER_TAPS = [(0.043, 0.5), (0.061, 0.35), (0.087, 0.25), (0.097, 0.18)]


def _one_pole_lp(x, fc, sr=SR):
    if fc >= sr / 2:
        return x
    a = 1.0 - np.exp(-2.0 * np.pi * fc / sr)
    y = np.empty_like(x)
    acc = 0.0
    # 1段では減衰が緩いので2段掛ける
    for _ in range(2):
        acc = 0.0
        for i, v in enumerate(x):
            acc += a * (v - acc)
            y[i] = acc
        x = y.copy()
    return y


def _one_pole_hp(x, fc, sr=SR):
    if fc <= 0:
        return x
    a = np.exp(-2.0 * np.pi * fc / sr)
    y = np.empty_like(x)
    prev_x, prev_y = 0.0, 0.0
    for i, v in enumerate(x):
        cur = a * (prev_y + v - prev_x)
        y[i] = cur
        prev_x, prev_y = v, cur
    return y


def build_ir(kind, predelay_ms=0.0):
    rt60, lp, hp, er_gain, seed = IR_SPECS[kind]
    n_tail = int(rt60 * SR)
    n_pre = int(predelay_ms * SR / 1000.0)
    n = n_pre + n_tail
    t = np.arange(n_tail) / SR
    env = np.exp(-6.9078 * t / rt60)  # t=RT60 で振幅 1/1000 = -60 dB
    chans = []
    for ch in range(2):
        rng = np.random.default_rng(seed * 10 + ch)  # 左右は別シード
        x = rng.standard_normal(n_tail) * env
        # 初期反射タップ（短いノイズバーストを包絡つきで埋め込む）
        burst = int(0.0015 * SR)
        for tap_s, amp in ER_TAPS:
            i0 = int(tap_s * SR)
            if i0 + burst < n_tail:
                x[i0:i0 + burst] += (rng.standard_normal(burst)
                                     * amp * er_gain * env[i0])
        x = _one_pole_lp(x, lp)
        x = _one_pole_hp(x, hp)
        if n_pre:
            x = np.concatenate([np.zeros(n_pre), x])
        # 単位エネルギー正規化（チャンネルごと）
        e = float(np.sum(x * x))
        if e > 0:
            x = x / np.sqrt(e)
        chans.append(x)
    return np.stack(chans, axis=1)  # (frames, 2)


def write_wav(path, x):
    pcm = np.clip(np.round(x * 32767), -32768, 32767).astype("<i2")
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def ir_path(kind, predelay_ms=0.0):
    if predelay_ms:
        return os.path.join(IR_DIR, f"{kind}_pd{int(round(predelay_ms))}.wav")
    return os.path.join(IR_DIR, f"{kind}.wav")


def generate(kind, predelay_ms=0.0, force=False):
    path = ir_path(kind, predelay_ms)
    if os.path.exists(path) and not force:
        return path, False
    os.makedirs(IR_DIR, exist_ok=True)
    write_wav(path, build_ir(kind, predelay_ms))
    return path, True


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="既存も作り直す")
    ap.add_argument("--kind", choices=sorted(IR_SPECS), help="1種だけ作る")
    ap.add_argument("--predelay-ms", type=float, default=0.0,
                    help="先頭に焼き込むプリディレイ (ms)")
    a = ap.parse_args()
    kinds = [a.kind] if a.kind else sorted(IR_SPECS)
    for kind in kinds:
        path, made = generate(kind, a.predelay_ms, a.force)
        rt60 = IR_SPECS[kind][0]
        print(f"{'generated' if made else 'exists   '} {path} "
              f"(RT60 {rt60}s, predelay {a.predelay_ms}ms)")


if __name__ == "__main__":
    main()
