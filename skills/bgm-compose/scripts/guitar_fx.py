# ギター音色プリセット集（DI 波形 → アンプ/エフェクター処理）。
#
# 使い方:
#   python3 guitar_fx.py <preset> <in_di.wav> <out.wav> [--addon delay_375 ...]
#   python3 guitar_fx.py --list
#
# プリセットは「素の DI 録音に後段で掛ける」前提。歪み段は 4 倍
# オーバーサンプリングしてからクリップし、エイリアシングを抑える。
# リバーブ系は生成 IR（afir）を使うため、この CLI 経由でのみ使える。
# ミックススクリプト内では PRESETS[name]["pre"] を [in]...[out] で挟んで使う。

import os
import subprocess
import sys

IR_DIR = "/home/hatch/workspace/soundfonts/ir"

# キャビネット相当の共通 EQ（スピーカーの帯域に寄せる）
CAB = "highpass=f=78,lowpass=f=6200,equalizer=f=320:width_type=o:width=1:g=-2"

def clip(boost_db, threshold, type_="tanh", lp=6500):
    return (f"volume={boost_db}dB,aresample=176400,"
            f"asoftclip=type={type_}:threshold={threshold},"
            f"lowpass=f={lp},aresample=44100")

# name: (説明, pre チェーン, IR種別 or None, リバーブ wet dB, post チェーン)
PRESETS = {
    # ---- 素直な系統 ----
    "clean": ("クリーン（軽いコンプのみ）",
              "highpass=f=75,lowpass=f=7500,"
              "acompressor=threshold=0.25:ratio=2:attack=8:release=150",
              None, 0, ""),
    "twang": ("トワング（明るく締まったカントリー/ロカビリー系）",
              "highpass=f=90,lowpass=f=8000,"
              "equalizer=f=2800:width_type=o:width=1:g=3,"
              "acompressor=threshold=0.18:ratio=3:attack=5:release=120",
              None, 0, ""),
    "lofi": ("ローファイ（帯域を絞ってビブラート）",
             "highpass=f=350,lowpass=f=2800,"
             "bandpass=f=900:width_type=o:width=1.2,"
             "vibrato=f=5:d=0.4",
             None, 0, ""),
    # ---- 歪み系統 ----
    "crunch": ("クランチ（軽い歪み）",
               clip(5, 0.55) + "," + CAB, None, 0, ""),
    "overdrive": ("オーバードライブ（中域を押し出すチューブスクリーマー系）",
                  "highpass=f=650," + clip(6, 0.42, lp=5200) + ","
                  "highpass=f=78,lowpass=f=6200,"
                  "equalizer=f=900:width_type=o:width=1:g=2",
                  None, 0, ""),
    "blues": ("ブルース（温かみのある軽いオーバードライブ）",
              "highpass=f=400," + clip(5, 0.5, lp=5000) + ","
              "highpass=f=70,lowpass=f=5800,"
              "equalizer=f=250:width_type=o:width=1:g=2,"
              "equalizer=f=800:width_type=o:width=1:g=1.5",
              None, 0, ""),
    "distortion": ("ディストーション（標準的な1段歪み、リズム向け）",
                   clip(5, 0.34, lp=6000) + "," + CAB.replace("lowpass=f=6200", "lowpass=f=6000"),
                   None, 0, ""),
    "lead_drive": ("リード用2段歪み（ペダル→アンプの重ね掛け）",
                   "volume=13dB,aresample=176400,"
                   "asoftclip=type=tanh:threshold=0.30,volume=-3dB,"
                   "asoftclip=type=tanh:threshold=0.20,lowpass=f=7200,"
                   "aresample=44100,highpass=f=90,"
                   "equalizer=f=900:width_type=o:width=1:g=1,"
                   "equalizer=f=3200:width_type=o:width=1:g=2",
                   None, 0, ""),
    "fuzz": ("ファズ（ハードクリップ＋暗め、60年代系）",
             clip(9, 0.25, type_="hard", lp=3800) + ","
             "highpass=f=70,lowpass=f=3800,"
             "equalizer=f=200:width_type=o:width=1:g=2",
             None, 0, ""),
    "metal": ("メタル（中域を削ったスコープ＋重い歪み）",
              clip(8, 0.28, lp=6800) + ","
              "highpass=f=70,lowpass=f=6800,"
              "equalizer=f=120:width_type=o:width=1:g=3,"
              "equalizer=f=450:width_type=o:width=1.2:g=-6,"
              "equalizer=f=4500:width_type=o:width=1:g=3",
              None, 0, ""),
    # ---- モジュレーション系統 ----
    "chorus_clean": ("クリーン＋コーラス（80年代クリーントーン）",
                     PRESETS_CLEAN := "highpass=f=75,lowpass=f=7500,"
                     "acompressor=threshold=0.25:ratio=2:attack=8:release=150,"
                     "chorus=0.7:0.9:45:0.4:0.25:2",
                     None, 0, ""),
    "flanger_clean": ("クリーン＋フランジャー",
                      "highpass=f=75,lowpass=f=7500,"
                      "flanger=delay=8:depth=5:regen=40:width=75:speed=0.5:"
                      "shape=sin:phase=25:interp=linear",
                      None, 0, ""),
    "phaser_crunch": ("クランチ＋フェイザー（ファンク/70年代系）",
                      clip(5, 0.55) + "," + CAB + ","
                      "aphaser=in_gain=0.6:out_gain=0.9:delay=3.5:decay=0.45:"
                      "speed=0.55:type=t",
                      None, 0, ""),
    "trem_drive": ("ディストーション＋トレモロ",
                   clip(5, 0.34, lp=6000) + "," + CAB + ",tremolo=f=5:d=0.55",
                   None, 0, ""),
    # ---- 空間系統（IR リバーブ使用） ----
    "surf": ("サーフ（スプリングリバーブ強め＋軽いトレモロ）",
             "highpass=f=85,lowpass=f=7800,"
             "acompressor=threshold=0.2:ratio=2.5:attack=6:release=120",
             "spring", -10, ",tremolo=f=4.5:d=0.3"),
    "ambient": ("アンビエント（ディレイ＋ホールリバーブ＋薄いコーラス）",
                "highpass=f=75,lowpass=f=7000,"
                "aecho=0.8:0.85:375:0.35,chorus=0.6:0.8:50:0.3:0.2:1.5",
                "hall", -16, ""),
    "shoegaze": ("シューゲイザー（ファズ＋コーラス＋ホール）",
                 clip(9, 0.25, type_="hard", lp=4200) + ","
                 "highpass=f=70,lowpass=f=4200,"
                 "chorus=0.7:0.9:55:0.35:0.3:2",
                 "hall", -13, ""),
}

# 後段に足せる単体エフェクト（-af 断片）
ADDONS = {
    "chorus": "chorus=0.7:0.9:45:0.4:0.25:2",
    "flanger": ("flanger=delay=8:depth=5:regen=40:width=75:speed=0.5:"
                "shape=sin:phase=25:interp=linear"),
    "phaser": ("aphaser=in_gain=0.6:out_gain=0.9:delay=3.5:decay=0.45:"
               "speed=0.55:type=t"),
    "tremolo": "tremolo=f=5:d=0.55",
    "vibrato": "vibrato=f=6:d=0.5",
    "delay_375": "aecho=0.8:0.85:375:0.35",
    "delay_500": "aecho=0.8:0.85:500:0.38",
    "slapback": "aecho=0.85:0.9:110:0.3",
    "presence": "equalizer=f=3200:width_type=o:width=1:g=3",
    "darken": "lowpass=f=4200",
}


def ensure_ir(kind):
    os.makedirs(IR_DIR, exist_ok=True)
    path = f"{IR_DIR}/{kind}.wav"
    if os.path.exists(path):
        return path
    spec = {"spring": ("0.9", "afade=t=out:st=0.03:d=0.87,highpass=f=900"),
            "room": ("1.4", "afade=t=out:st=0.05:d=1.35"),
            "hall": ("2.6", "afade=t=out:st=0.08:d=2.5,lowpass=f=5200")}[kind]
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
                        "-i", f"anoisesrc=d={spec[0]}:c=pink:r=44100:a=0.6",
                        "-af", spec[1], path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-800:])
    return path


def apply(preset, in_wav, out_wav, addons=()):
    desc, pre, ir, wet_db, post = PRESETS[preset]
    tail = "".join("," + ADDONS[a] for a in addons)
    if ir is None:
        af = pre + post + tail
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", in_wav, "-af", af,
               "-ar", "44100", out_wav]
    else:
        ir_path = ensure_ir(ir)
        fc = (f"[0:a]{pre}[a];[a]asplit=2[d][s];"
              f"[s][1:a]afir=dry=1:wet=1,volume={wet_db}dB[w];"
              f"[d][w]amix=inputs=2:normalize=0{post}{tail}[out]")
        cmd = ["ffmpeg", "-y", "-v", "error", "-i", in_wav, "-i", ir_path,
               "-filter_complex", fc, "-map", "[out]", "-ar", "44100", out_wav]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1500:])
        sys.exit(1)
    print(f"{preset}: {desc} -> {out_wav}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "--list":
        for name, (desc, *_rest) in PRESETS.items():
            print(f"{name:16s} {desc}")
        print("-- addons:", ", ".join(ADDONS))
        sys.exit(0)
    addons = []
    if "--addon" in args:
        i = args.index("--addon")
        addons = args[i + 1:]
        args = args[:i]
    preset, in_wav, out_wav = args[0], args[1], args[2]
    apply(preset, in_wav, out_wav, addons)
