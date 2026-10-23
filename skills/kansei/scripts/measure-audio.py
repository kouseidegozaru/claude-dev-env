#!/usr/bin/env python3
"""kansei: 音声ファイルの「必須の軸」のうち、測れるものを ffmpeg で測る（聴けない分を数値で補う）。

使い方: python3 measure-audio.py <音声または動画ファイル> [--spectrum <出力.png>]
必要: ffmpeg / ffprobe（無ければ止まる。そのときは music.md の「ツールが無いとき」に従う）

出すもの:
  長さ、統合ラウドネスとラウドネスレンジ（ebur128）、0.5 秒ごとの音量の推移、最も大きい区間、
  無音区間（silencedetect）、立ち上がりと終わりの長さ、発音（音の出だし）の密度、拍の周期の推定。
--spectrum を付けると、スペクトログラム画像も書き出す（Read で開いて、音の高さの分布や区切りを目で見る）。
拍の周期は音量の変化から推した「推定」。合成音・拍の弱い音・テンポが揺れる曲では外れる。
楽器名・音色・ジャンルはこのスクリプトでは分からない。書くなら「未確認」か、ユーザーの言葉を根拠にする。
"""
import array
import math
import re
import subprocess
import sys

SR = 8000


def run(cmd):
    return subprocess.run(cmd, capture_output=True, timeout=300)


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    f = sys.argv[1]
    try:
        dur = float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", f]).stdout.decode().strip())
    except (FileNotFoundError, ValueError):
        print("ffprobe が使えない、または長さが取れない。数値は出せないので、music.md の「ツールが無いとき」に従う。"); sys.exit(3)

    log = run(["ffmpeg", "-hide_banner", "-nostats", "-i", f, "-af", "ebur128=framelog=quiet", "-f", "null", "-"]).stderr.decode()
    I = re.findall(r"I:\s+(-?[\d.]+) LUFS", log); LRA = re.findall(r"LRA:\s+([\d.]+) LU", log)
    sil = run(["ffmpeg", "-hide_banner", "-nostats", "-i", f, "-af", "silencedetect=n=-40dB:d=0.3", "-f", "null", "-"]).stderr.decode()
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", sil)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", sil)]

    pcm = run(["ffmpeg", "-v", "error", "-i", f, "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]).stdout
    a = array.array("h"); a.frombytes(pcm[: len(pcm) // 2 * 2])

    def rms_db(seg):
        if not seg: return -120.0
        return 10 * math.log10(max(sum(v * v for v in seg) / len(seg), 1e-9) / (32768 ** 2))

    half = SR // 2
    curve = [rms_db(a[i:i + half]) for i in range(0, len(a), half)]
    loud = [c for c in curve if c > -60]
    med = sorted(loud)[len(loud) // 2] if loud else -60
    peak_i = max(range(len(curve)), key=lambda i: curve[i])
    rise = next((i * 0.5 for i, c in enumerate(curve) if c >= med - 6), 0.0)
    last = max((i for i, c in enumerate(curve) if c >= med - 6), default=len(curve) - 1)
    fall = dur - (last + 1) * 0.5

    # 発音の密度と拍の周期（20ms ごとのエネルギーの増え方から推定）
    hop = SR // 50
    env = [math.sqrt(sum(v * v for v in a[i:i + hop]) / hop) if a[i:i + hop] else 0 for i in range(0, len(a) - hop, hop)]
    flux = [max(0.0, env[i] - env[i - 1]) for i in range(1, len(env))]
    mf = sum(flux) / len(flux) if flux else 0
    sd = math.sqrt(sum((x - mf) ** 2 for x in flux) / len(flux)) if flux else 0
    thr = mf + 1.5 * sd
    onsets = [i for i in range(1, len(flux) - 1) if flux[i] > thr and flux[i] >= flux[i - 1] and flux[i] >= flux[i + 1]]
    onsets = [o for k, o in enumerate(onsets) if k == 0 or o - onsets[k - 1] > 5]  # 100ms 以内の重複を除く
    best, score, ratio = None, 0, 0
    if len(flux) > 200:
        c = [x - mf for x in flux]
        ac = {}
        for lag in range(int(0.3 * 50), int(1.5 * 50) + 1):  # 40〜200 BPM
            ac[lag] = sum(c[i] * c[i + lag] for i in range(len(c) - lag)) / (len(c) - lag)
        base = sum(abs(v) for v in ac.values()) / len(ac) or 1
        best = max(ac, key=ac.get); score = ac[best]; ratio = score / base

    print(f"ファイル: {f}")
    print(f"長さ: {dur:.2f} 秒")
    print(f"統合ラウドネス: {I[-1] if I else '取得できず'} LUFS / ラウドネスレンジ(LRA): {LRA[-1] if LRA else '取得できず'} LU（小さいほど音量の起伏が少ない）")
    print("音量の推移（0.5秒ごと RMS dBFS）: " + " ".join(f"{c:.0f}" for c in curve))
    print(f"最も大きい区間: {peak_i * 0.5:.1f}〜{peak_i * 0.5 + 0.5:.1f} 秒（{curve[peak_i]:.1f} dBFS） / 中央値 {med:.1f} dBFS")
    print(f"立ち上がり: 中央値-6dB に達するまで {rise:.1f} 秒 / 終わり: 中央値-6dB を下回ってから終端まで {fall:.1f} 秒")
    if starts:
        pairs = [f"{s:.2f}〜{(ends[k] if k < len(ends) else dur):.2f}秒" for k, s in enumerate(starts)]
        print("無音（-40dB 以下 0.3 秒以上）: " + ", ".join(pairs))
    else:
        print("無音（-40dB 以下 0.3 秒以上）: なし")
    print(f"発音（音の出だし）の数: {len(onsets)}（1秒あたり {len(onsets) / dur:.2f}）。時刻: " + " ".join(f"{o * 0.02:.1f}" for o in onsets[:24]))
    if best and ratio >= 2.0:
        print(f"拍の周期（推定）: {best * 0.02:.2f} 秒 ≒ {60 / (best * 0.02):.0f} BPM（強さ {ratio:.1f}。倍・半分の取り違えがありうる）")
    else:
        print(f"拍の周期（推定）: はっきりしない（強さ {ratio:.1f}）。拍の弱い音か、テンポが一定でない。BPM は「未確認」とする")
    print("測れないこと: 楽器名・音色・ジャンル・和声・感情。書くならユーザーの言葉を引用するか「未確認」と書く。")

    if "--spectrum" in sys.argv:
        out = sys.argv[sys.argv.index("--spectrum") + 1]
        r = run(["ffmpeg", "-v", "error", "-y", "-i", f, "-lavfi", "showspectrumpic=s=1280x480:legend=1", out])
        print(f"スペクトログラム: {out}" if r.returncode == 0 else f"スペクトログラム作成に失敗: {r.stderr.decode()[:200]}")


if __name__ == "__main__":
    main()
