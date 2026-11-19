# ギター音色プリセット集（DI 波形 → アンプ/エフェクター処理）。
#
# 使い方:
#   python3 guitar_fx.py <preset> <in_di.wav> <out.wav> [--addon delay_375 ...]
#   python3 guitar_fx.py <preset> <in_di.wav> <out.wav> --cab cone
#   python3 guitar_fx.py --list
#
# プリセットは「素の DI 録音に後段で掛ける」前提。歪み段は必ず
# オーバーサンプリングしてからクリップし、エイリアシングを抑える。
# 倍率は歪みの硬さで変える: 柔らかい整形（tanh かつ閾値 0.4 以上）は
# 4 倍、閾値 0.34 以下・2 段歪み・ハードクリップは 8 倍。
# リバーブ系は生成 IR（afir）を使うため、この CLI 経由でのみ使える。
# ミックススクリプト内では PRESETS[name]["pre"] を [in]...[out] で挟んで使う。
#
# 注意: clip() 内のローパスは音色作りであると同時に、ダウンサンプリング
# 時の折り返し除去（アンチエイリアス）を兼ねている。明るさが欲しいから
# といって lp を外す・上げる改変は禁止。明るさ調整は後段の EQ
# （presence アドオン等）で行うこと。

import os
import subprocess
import sys

IR_DIR = os.path.expanduser("~/workspace/soundfonts/ir")
BASE_SR = 44100

# キャビネット相当の EQ（スピーカーの帯域に寄せる）。マイク位置の違いを
# 3 バリエーションで持つ: cap=中心（最も明るく攻撃的）、cap_edge=キャップ端
# （バランスの取れた標準、従来の CAB）、cone=コーン外側（暗く丸い）。
# 明るすぎる/フィズが気になる場合は後段 EQ でなくキャビ側で直すのが第一手。
CABS = {
    "cap": "highpass=f=78,lowpass=f=7000,"
           "equalizer=f=3200:width_type=o:width=1:g=2.5",
    "cap_edge": "highpass=f=78,lowpass=f=6200,"
                "equalizer=f=320:width_type=o:width=1:g=-2",
    "cone": "highpass=f=78,lowpass=f=4800,"
            "equalizer=f=3200:width_type=o:width=1:g=0.5,"
            "equalizer=f=250:width_type=o:width=1:g=1",
}
CAB = CABS["cap_edge"]  # 従来名の互換


def os_factor_for(threshold, type_="tanh", stages=1):
    """歪みの硬さからオーバーサンプリング倍率を決める。"""
    if type_ == "hard" or threshold <= 0.34 or stages >= 2:
        return 8
    return 4


def clip(boost_db, threshold, type_="tanh", lp=6500, os_factor=None):
    """ブースト→オーバーサンプリング→クリップ→折り返し除去 LP→戻す。

    lp はアンチエイリアス兼用（モジュール冒頭の注意を参照）。外さないこと。
    """
    f = os_factor if os_factor is not None else os_factor_for(threshold, type_)
    return (f"volume={boost_db}dB,aresample={BASE_SR * f},"
            f"asoftclip=type={type_}:threshold={threshold},"
            f"lowpass=f={lp},aresample={BASE_SR}")


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
    # ---- 歪み系統（前段ハイパスで低域がクリップ段を叩くのを防ぎ締める） ----
    "crunch": ("クランチ（軽い歪み）",
               "highpass=f=100," + clip(5, 0.55) + "," + CAB, None, 0, ""),
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
                   "highpass=f=120," + clip(5, 0.34, lp=6000) + "," +
                   CAB.replace("lowpass=f=6200", "lowpass=f=6000"),
                   None, 0, ""),
    "lead_drive": ("リード用2段歪み（ペダル→アンプの重ね掛け、8倍OS）",
                   "highpass=f=150,volume=13dB,aresample=352800,"
                   "asoftclip=type=tanh:threshold=0.30,volume=-3dB,"
                   "asoftclip=type=tanh:threshold=0.20,lowpass=f=7200,"
                   "aresample=44100,"
                   "equalizer=f=900:width_type=o:width=1:g=1,"
                   "equalizer=f=3200:width_type=o:width=1:g=2",
                   None, 0, ""),
    "lead_chord": ("和音リード専用2段歪み（相互変調対策に歪みを一段引き、"
                   "総ブースト +10dB、2段目閾値 0.28、前段HP 150Hz、8倍OS）",
                   "highpass=f=150,volume=10dB,aresample=352800,"
                   "asoftclip=type=tanh:threshold=0.34,volume=-2dB,"
                   "asoftclip=type=tanh:threshold=0.28,lowpass=f=6800,"
                   "aresample=44100," + CAB,
                   None, 0, ""),
    "fuzz": ("ファズ（ハードクリップ＋暗め、60年代系）",
             "highpass=f=120," + clip(9, 0.25, type_="hard", lp=3800) + ","
             "highpass=f=70,lowpass=f=3800,"
             "equalizer=f=200:width_type=o:width=1:g=2",
             None, 0, ""),
    "metal": ("メタル（中域を削ったスコープ＋重い歪み）",
              "highpass=f=120," + clip(8, 0.28, lp=6800) + ","
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
                      "highpass=f=100," + clip(5, 0.55) + "," + CAB + ","
                      "aphaser=in_gain=0.6:out_gain=0.9:delay=3.5:decay=0.45:"
                      "speed=0.55:type=t",
                      None, 0, ""),
    "trem_drive": ("ディストーション＋トレモロ",
                   "highpass=f=120," + clip(5, 0.34, lp=6000) + "," + CAB +
                   ",tremolo=f=5:d=0.55",
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
                 "highpass=f=120," + clip(9, 0.25, type_="hard", lp=4200) + ","
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
    # ハイゲイン時のノイズゲート。threshold は無音部ノイズのピークより
    # +3〜6 dB 上を目安に曲ごとに調整する。range は全閉でなく -20 dB
    # 程度に留め、サステインの尾を切らない。歪み段の後・空間系の前に置く。
    "gate_tight": "agate=threshold=0.02:ratio=2:range=0.1:attack=2:release=120",
    # 伴奏ギターを主旋律の後ろに座らせる後処理（clean/crunch 等の後段に付ける）。
    # レベル -4.5 dB、HPF 110 Hz で低域を譲り、250-500 Hz の泥を -2.5 dB、
    # 高域を丸めて距離を作る。コンプは座らせる用途で GR 3-5 dB を上限の目安。
    "bgm_backing": ("volume=-4.5dB,highpass=f=110,"
                    "equalizer=f=375:width_type=o:width=1:g=-2.5,"
                    "lowpass=f=4200,"
                    "acompressor=threshold=0.18:ratio=3:attack=15:release=80"),
}


def ensure_ir(kind):
    """生成 IR を用意する。生成方式は generate_ir.py が正本。"""
    path = os.path.join(IR_DIR, f"{kind}.wav")
    if os.path.exists(path):
        return path
    gen = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "generate_ir.py")
    r = subprocess.run(["python3", gen, "--kind", kind],
                       capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(path):
        raise RuntimeError(f"IR generation failed for {kind}: "
                           f"{(r.stderr or r.stdout)[-800:]}")
    return path


def apply(preset, in_wav, out_wav, addons=(), cab=None):
    desc, pre, ir, wet_db, post = PRESETS[preset]
    if cab is not None:
        if cab not in CABS:
            raise ValueError(f"unknown cab variant: {cab}")
        pre = pre.replace(CABS["cap_edge"], CABS[cab])
        post = post.replace(CABS["cap_edge"], CABS[cab])
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
        print("-- cabs:", ", ".join(CABS))
        sys.exit(0)
    addons = []
    if "--addon" in args:
        i = args.index("--addon")
        addons = args[i + 1:]
        args = args[:i]
    cab = None
    if "--cab" in args:
        i = args.index("--cab")
        cab = args[i + 1]
        args = args[:i] + args[i + 2:]
    preset, in_wav, out_wav = args[0], args[1], args[2]
    apply(preset, in_wav, out_wav, addons, cab=cab)
