#!/usr/bin/env python3
"""レンダリング→マスタリング→納品ゲートのパイプライン（標準ライブラリのみ）。

使い方:
  python3 render.py --spec SPEC.py --mid SONG.mid --out OUTDIR --name example_track \
      [--baseline REFERENCE.ogg] [--sf SOUNDFONT] [--gain 1.0] \
      [--preset streaming|game_asset|broadcast] [--lufs -14] [--tp -1.0] \
      [--loop [--xfade 2.0]] \
      [--space-ir IR.wav [--space-wet-db -20] [--space-rt60 1.9] \
       [--space-predelay-ms 20] [--space-type hall] [--space-sends "all:-20"]] \
      [--reverb-level 0.9] [--allow-dynamic-reason "理由"]

手順:
  0. 事前検査: fluidsynth / ffmpeg / ffprobe の実行ファイル、venv の Python、
     音源ファイル、仕様書・MIDI の存在を確認する。不足があれば、何が無いかと
     復旧手順を明示して描画に入らず即座に停止する（終了コード 2）。
  1. FluidSynth で WAV 描画（ピークが -1 dBFS を超えたら gain を下げて1回だけ描き直す）。
     シンセ設定（サンプルレート・補間相当・リバーブ・コーラス・ゲイン）は
     SYNTH_SETTINGS に集約して全量明示し、設定辞書のハッシュを report に残す。
     描画直後に成功判定を3点で行う: (1) 終了コード、(2) 標準エラーのパターン
     （エラー系と、プリセット代替の警告。代替は描画失敗と同格に扱う）、
     (3) 出力実測（長さが仕様 ±10% 以内・無音でないこと）。あわせて無音区間の
     区間検出（途中の2秒以上の無音、無音合計30%超は失敗）を行う。
  2. 空間: 主空間は1つだけ。--space-ir 指定時は afir センド方式（内蔵リバーブは
     明示的に切る）、未指定時は FluidSynth 内蔵リバーブ（設定は全量記録）。
     両方の併用は禁止。使用経路・IR種別・RT60・プリディレイ・送り量を
     render_report に必須記録とし、記録が無い成果物は未完成として扱う。
  3. 基準曲があれば計測して baseline.json を作る
  4. 低域比が基準の許容（±10pt）から外れていれば lowshelf EQ を ±3.5dB ずつ、
     測定→補正→再測定を最大2回（必ず生描画から当て直す）
  5. --loop のときループ化を loudnorm の前に行う。標準は定常状態レンダリング
     （末尾の尾を頭へ折り返す render-and-wrap）。--xfade 指定時はクロスフェード
     を代替手段とし、カーブは継ぎ目の位相相関で選び、長さは候補
     [50/250/500/1000/2000]ms から継ぎ目指標が最小のものを探索する。
  6. loudnorm 2パス（1パス目で測定、測定値で線形補正）。LRA 目標は固定値でなく
     測定した LRA そのもの（LRA を変えない）。第2パスの normalization_type を
     回収して report に記録し、線形を指定したのに dynamic へ落ちていたら
     失敗として止める（dynamic は --allow-dynamic-reason で意図した場合だけ許可）。
     目標ラウドネスは delivery プリセット（streaming / game_asset / broadcast）
     または --lufs / --tp で決める。使ったプリセット名は report に記録する。
  7. 納品ゲート（measure.py --gate）と記譜確認（check_midi.py）を実行し、
     render_report.json に全記録（所要時間つき）を残す

計測は measure.py（システム python3 + numpy）、記譜確認は check_midi.py
（venv の python + mido）を子プロセスで呼ぶ。
"""
import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MEASURE = os.path.join(HERE, "measure.py")


def _find_check_midi():
    # 記譜確認（check_midi.py）は作曲スキル bgm-compose 側の資産。
    # 同一フォルダに無ければ、隣の bgm-compose スキルから探す。
    candidates = [
        os.path.join(HERE, "check_midi.py"),
        os.path.normpath(os.path.join(
            HERE, "..", "..", "bgm-compose", "scripts", "check_midi.py")),
        os.path.expanduser(
            "~/workspace/custom-skills/bgm-compose/scripts/check_midi.py"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


CHECK_MIDI = _find_check_midi()
DEFAULT_SF = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
VENV_PY = os.path.expanduser("~/workspace/midi-bgm/venv/bin/python")
TIMEOUT = 900
SR = 44100

# FluidSynth のシンセ設定は既定任せにせず、ここに集約して全量明示する。
# 値は FluidSynth の文書化された既定値に合わせてあり、従来の暗黙の既定と
# 同じ音になる（版やビルドで既定が変わっても、ここが正になる）。
SYNTH_SETTINGS = {
    "sample_rate": SR,
    "polyphony": 256,
    "reverb": {"active": 1, "room_size": 0.2, "damp": 0.0, "width": 0.5,
               "level": 0.9},
    "chorus": {"active": 0, "nr": 3, "level": 2.0, "speed": 0.3,
               "depth": 8.0, "type": 0},
}

# delivery プリセット（納品種別ごとの目標値）。
# game_asset は未検証の作業値であり、実機確認後に確定する前提で report に残す。
DELIVERY_PRESETS = {
    "streaming": {"lufs": -14.0, "lufs_tol": 0.5, "tp": -1.0, "unverified": False},
    "game_asset": {"lufs": -20.0, "lufs_tol": 1.0, "tp": -1.0, "unverified": True},
    "broadcast": {"lufs": -23.0, "lufs_tol": 0.5, "tp": -1.0, "unverified": False},
}


def synth_settings(gain, reverb_level=None, afir_space=False):
    """実効シンセ設定の辞書・そのハッシュ・fluidsynth の -o オプション列を返す。"""
    s = json.loads(json.dumps(SYNTH_SETTINGS))
    s["gain"] = round(float(gain), 4)
    if reverb_level is not None:
        s["reverb"]["level"] = float(reverb_level)
    if afir_space:
        # afir センド方式が主空間のとき、内蔵リバーブは二重掛けになるため切る
        s["reverb"]["active"] = 0
    h = hashlib.sha256(json.dumps(s, sort_keys=True).encode()).hexdigest()[:12]
    rv, ch = s["reverb"], s["chorus"]
    opts = [
        "-o", f"synth.polyphony={s['polyphony']}",
        "-o", f"synth.reverb.active={rv['active']}",
        "-o", f"synth.reverb.room-size={rv['room_size']}",
        "-o", f"synth.reverb.damp={rv['damp']}",
        "-o", f"synth.reverb.width={rv['width']}",
        "-o", f"synth.reverb.level={rv['level']}",
        "-o", f"synth.chorus.active={ch['active']}",
        "-o", f"synth.chorus.nr={ch['nr']}",
        "-o", f"synth.chorus.level={ch['level']}",
        "-o", f"synth.chorus.speed={ch['speed']}",
        "-o", f"synth.chorus.depth={ch['depth']}",
        # synth.chorus.type は FluidSynth 2.3.4 に設定項目が存在しない
        # （-o で渡すとエラーで止まる）。コーラス無効時は無影響のため
        # 辞書への記録のみとし、オプションでは渡さない。
    ]
    return s, h, opts


def run(cmd, **kw):
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, **kw)
    return r, time.time() - t0


def measure_json(path):
    r, _ = run(["python3", MEASURE, path])
    if r.returncode != 0:
        raise RuntimeError("measure 失敗: " + r.stderr[-500:])
    return json.loads(r.stdout)[0]


def loudnorm_stats(path, lufs, tp, lra=11):
    """目標値つきで loudnorm の測定パスを回し、測定値と target_offset を得る。"""
    af = f"loudnorm=I={lufs}:TP={tp}:LRA={lra}:print_format=json"
    r, _ = run(["ffmpeg", "-hide_banner", "-i", path, "-vn",
                "-af", af, "-f", "null", "-"])
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("loudnorm 測定失敗: " + path)
    return json.loads(m[-1])


class DynamicFallbackError(RuntimeError):
    """線形を指定したのに loudnorm が dynamic へ落ちた（意図しない動的圧縮）。"""


def master(cur, mastered, lufs, tp, dynamic_reason=None):
    """loudnorm 2パス（測定→測定値で線形補正）で mastered.wav を作る。

    LRA 目標は測定した LRA そのもの（LRA を変えない）。第2パスの
    normalization_type を回収して返し、線形指定で dynamic だった場合は
    DynamicFallbackError を送出する（dynamic_reason がある場合だけ意図的として許可）。
    """
    st = loudnorm_stats(cur, lufs, tp)
    measured_lra = float(st["input_lra"])
    target_lra = max(measured_lra, 1.0)
    linear = dynamic_reason is None
    af = (f"loudnorm=I={lufs}:TP={tp}:LRA={target_lra}:"
          f"measured_I={st['input_i']}:measured_LRA={st['input_lra']}:"
          f"measured_TP={st['input_tp']}:measured_thresh={st['input_thresh']}:"
          f"offset={st['target_offset']}:linear={str(linear).lower()}:"
          f"print_format=json")
    r, _ = run(["ffmpeg", "-hide_banner", "-y", "-i", cur, "-af", af,
                "-ar", str(SR), mastered])
    if r.returncode != 0:
        raise RuntimeError("loudnorm 適用失敗: " + r.stderr[-500:])
    m = re.findall(r"\{[^{}]*\"normalization_type\"[^{}]*\}", r.stderr)
    applied = json.loads(m[-1]) if m else {}
    info = {"measured": {k: st[k] for k in
                         ("input_i", "input_tp", "input_lra", "target_offset")},
            "target_lra": target_lra,
            "linear_requested": linear,
            "normalization_type": applied.get("normalization_type"),
            "dynamic_intentional": dynamic_reason is not None,
            "dynamic_reason": dynamic_reason}
    if info["normalization_type"] == "dynamic" and linear:
        raise DynamicFallbackError(
            "loudnorm が線形補正できず dynamic（動的圧縮）へ落ちました。"
            "マスターで潰す前に、素材側のピーク/レベルを直してください。"
            "意図的に dynamic を使う場合だけ --allow-dynamic-reason で理由を指定します。")
    return info


def encode(wav, ogg):
    r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", wav,
                "-c:a", "libvorbis", "-q:a", "6", ogg])
    if r.returncode != 0:
        raise RuntimeError("ogg 変換失敗: " + r.stderr[-500:])
    return measure_json(ogg)


def spec_duration(spec_path):
    """仕様書のテンポマップを積分した長さ [s]。"""
    import importlib.util
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", spec_path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    spec = mod.SPEC
    bpb = int(spec.get("beats_per_bar", 4))
    secs, cur_beat, cur_bpm = 0.0, 0, float(spec["bpm"])
    for bar, tbpm in sorted(spec.get("tempo_changes", [])):
        beat = (bar - 1) * bpb
        secs += (beat - cur_beat) * 60.0 / cur_bpm
        cur_beat, cur_bpm = beat, float(tbpm)
    secs += (int(spec["bars"]) * bpb - cur_beat) * 60.0 / cur_bpm
    return secs


def preflight(args):
    """入口の事前検査。不足の一覧 [(項目, 状況, 復旧手順)] を返す（空なら全て揃っている）。

    環境から道具や音源が消えた状態で描画に入ると、無音の WAV が正常終了で
    出力され、失敗が loudnorm の数値エラーとして現れて原因が分からなくなる。
    それを防ぐため、描画の前に前提をすべて確認し、不足は復旧手順つきで即座に止める。
    """
    problems = []
    for exe, pkg in (("fluidsynth", "fluidsynth"),
                     ("ffmpeg", "ffmpeg"),
                     ("ffprobe", "ffmpeg")):
        if shutil.which(exe) is None:
            problems.append((
                f"実行ファイル {exe}",
                "PATH 上に見つかりません",
                f"sudo apt-get install -y {pkg} で導入してください"
                "（導入済みのはずなら PATH を確認してください）。"))
    if not os.path.isfile(VENV_PY):
        problems.append((
            "作曲・記譜確認用の venv の Python",
            f"{VENV_PY} がありません",
            "~/workspace/midi-bgm/venv を作り直し、mido を入れてください"
            "（python3 -m venv ~/workspace/midi-bgm/venv のあと "
            "~/workspace/midi-bgm/venv/bin/pip install mido）。"))
    if not os.path.isfile(args.sf):
        problems.append((
            "音源ファイル",
            f"{args.sf} がありません",
            "sudo apt-get install -y fluid-soundfont-gm で再導入するか、"
            "手元にある .sf2 / .sf3 を --sf で指定してください"
            "（使える音源は references/instruments.md の台帳にあるものだけです）。"))
    elif os.path.getsize(args.sf) == 0:
        problems.append((
            "音源ファイル",
            f"{args.sf} が 0 バイトです",
            "音源が壊れています。再導入するか、別の音源を --sf で指定してください"
            "（使える音源は references/instruments.md の台帳にあるものだけです）。"))
    for label, path in (("仕様書", args.spec), ("MIDI ファイル", args.mid)):
        if not os.path.isfile(path):
            problems.append((
                label,
                f"{path} がありません",
                "パスの指定を確認してください"
                "（MIDI は先に compose.py で生成します）。"))
    if args.baseline and not os.path.isfile(args.baseline):
        problems.append((
            "基準曲ファイル",
            f"{args.baseline} がありません",
            "パスの指定を確認してください（基準曲なしで進めるなら --baseline を外します）。"))
    if args.space_ir and not os.path.isfile(args.space_ir):
        problems.append((
            "空間用 IR ファイル",
            f"{args.space_ir} がありません",
            "パスの指定を確認してください（内蔵リバーブで進めるなら --space-ir を外します）。"))
    return problems


def silence_problem(m, sf_path, fs_stderr):
    """描画直後の無音・ノイズフロア検出。問題があれば説明文を、なければ None を返す。

    FluidSynth は音源を読み込めなくても無音の WAV を正常終了で出力することがある。
    そのまま進むと EQ が空転し loudnorm が -inf の数値エラーで落ちて原因が
    分からなくなるため、原音の計測直後にここで止める。
    """
    lufs, peak = m["lufs_i"], m["peak_dbfs"]
    if (isinstance(lufs, (int, float)) and math.isfinite(lufs)
            and lufs > -60.0 and peak > -70.0):
        return None
    msg = (f"描画結果が無音またはノイズフロアだけです（LUFS {lufs}, ピーク {peak} dBFS）。"
           f"音源ファイル {sf_path} が FluidSynth に読み込まれていない可能性が高いです。"
           "音源ファイルの存在と中身（0 バイトでないこと）を確認し、"
           "--sf の指定を見直してから再実行してください。")
    tail = (fs_stderr or "").strip()
    if tail:
        msg += " FluidSynth の出力末尾: " + tail[-300:]
    return msg


def silence_segments(path):
    """silencedetect で無音区間を列挙する（区間検出）。

    失敗条件: 曲の途中（先頭・末尾の想定内無音を除く）にある2秒以上の無音、
    または無音区間の合計が全体の30%超。
    """
    r, _ = run(["ffmpeg", "-hide_banner", "-i", path, "-vn",
                "-af", "silencedetect=noise=-50dB:d=1.0", "-f", "null", "-"])
    starts = [float(v) for v in re.findall(r"silence_start: ([\d.]+)", r.stderr)]
    ends = [float(v) for v in re.findall(r"silence_end: ([\d.]+)", r.stderr)]
    dur = measure_json(path)["duration_s"]
    segs = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else dur
        segs.append({"start": round(s, 2), "end": round(e, 2),
                     "duration": round(e - s, 2)})
    total = sum(s["duration"] for s in segs)
    mid_long = [s for s in segs if s["duration"] >= 2.0
                and s["start"] >= 0.5 and s["end"] <= dur - 0.5]
    failed = bool(mid_long) or (dur > 0 and total > 0.30 * dur)
    return {"segments": segs, "total_silence_s": round(total, 2),
            "mid_silence_over_2s": mid_long, "failed": failed}


# 描画の標準エラーで検出するパターン（終了コードの意味は版依存のため単独では使わない）
STDERR_HARD_PATTERNS = [r"error", r"failed", r"could not", r"cannot", r"no such file"]
STDERR_SUBST_PATTERNS = [r"instrument not found", r"not found on channel",
                         r"substitut", r"no preset"]


def render_check(r, raw_path, expected_dur, m_raw, sf_path):
    """描画成功の3点判定: 終了コード / 標準エラーのパターン / 出力実測。

    プリセット代替の警告（見つからない音色が黙って別の音色に置き換わる）は
    数値ゲートでは捕まえられないため、描画失敗と同格に扱う。
    """
    err_lines = (r.stderr or "").splitlines()
    hard, subst = [], []
    for line in err_lines:
        low = line.lower()
        if any(re.search(p, low) for p in STDERR_SUBST_PATTERNS):
            subst.append(line.strip()[:200])
        elif any(re.search(p, low) for p in STDERR_HARD_PATTERNS):
            hard.append(line.strip()[:200])
    actual = m_raw["duration_s"]
    # FluidSynth は MIDI 終端のあとにリリース/リバーブの尾（実測で約3秒）を
    # 付けて描くため、上側は割合許容に尾の絶対許容を足す
    dur_ok = (expected_dur * 0.9 - 0.5) <= actual <= (expected_dur * 1.1 + 3.5)
    sil = silence_problem(m_raw, sf_path, r.stderr)
    segs = silence_segments(raw_path)
    ok = (r.returncode == 0 and not hard and not subst and dur_ok
          and sil is None and not segs["failed"])
    return {"ok": ok,
            "exit_code": r.returncode, "exit_code_ok": r.returncode == 0,
            "stderr_hard_matches": hard[:5],
            "stderr_substitution_matches": subst[:5],
            "duration_expected_s": round(expected_dur, 2),
            "duration_actual_s": actual, "duration_ok": dur_ok,
            "not_silent": sil is None, "silence_detail": sil,
            "silence_segments": segs}


def apply_space_afir(raw, ir_path, wet_db, predelay_ms, rt60, out_path):
    """afir センド方式で主空間を付ける（送り分岐だけを畳み込み、ドライは外で足す）。"""
    dur_raw = measure_json(raw)["duration_s"]
    out_dur = dur_raw + (rt60 if rt60 else 1.0)
    w = 10 ** (wet_db / 20.0)
    p = int(round(predelay_ms or 0))
    delay = f"[wet]adelay={p}|{p}[wetd];" if p > 0 else "[wet]anull[wetd];"
    fc = (f"[0:a]asplit=2[dry][send];"
          f"[send][1:a]afir=dry=0:wet=1:irnorm=1[wet];"
          + delay +
          f"[wetd]atrim=0:{out_dur:.3f}[wett];"
          f"[dry]apad=whole_dur={out_dur:.3f}[dryp];"
          f"[dryp][wett]amix=inputs=2:normalize=0:duration=first:"
          f"weights='1 {w:.4f}'[out]")
    r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-i", ir_path,
                "-filter_complex", fc, "-map", "[out]", "-ar", str(SR), out_path])
    if r.returncode != 0:
        raise RuntimeError("afir 適用失敗: " + r.stderr[-500:])


def make_xfade_variant(src, dur, xfade_s, curve, out_path):
    fc = (f"[0:a]atrim=0:{dur - xfade_s},asetpts=N/SR/TB[m];"
          f"[0:a]atrim={dur - xfade_s}:{dur},asetpts=N/SR/TB[t];"
          f"[0:a]atrim=0:{xfade_s},asetpts=N/SR/TB[h];"
          f"[t][h]acrossfade=d={xfade_s}:c1={curve}:c2={curve}[xf];"
          f"[m][xf]concat=n=2:v=0:a=1[out]")
    r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", src,
                "-filter_complex", fc, "-map", "[out]", "-ar", str(SR), out_path])
    if r.returncode != 0:
        raise RuntimeError("クロスフェード生成失敗: " + r.stderr[-500:])


def seam_of(path):
    r, _ = run(["python3", MEASURE, "--seam", path])
    if r.returncode != 0:
        raise RuntimeError("seam 計測失敗: " + r.stderr[-500:])
    return json.loads(r.stdout)


def build_loop_source(cur, dur, out_dir, xfade_arg):
    """loudnorm の前にループ化した素材を作る。(パス, 情報) を返す。

    標準は定常状態レンダリング（末尾の尾を頭へ折り返す）。--xfade 指定時は
    クロスフェード代替: カーブは位相相関で選び、長さは候補から継ぎ目指標が
    最小のものを探索する。
    """
    src_dur = measure_json(cur)["duration_s"]
    if xfade_arg is None:
        out = os.path.join(out_dir, "loop_src.wav")
        if src_dur <= dur + 0.01:
            r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", cur,
                        "-t", f"{dur:.3f}", "-ar", str(SR), out])
            if r.returncode != 0:
                raise RuntimeError("ループ切り詰め失敗: " + r.stderr[-500:])
            return out, {"method": "steady_state", "wrapped_tail": False,
                         "note": "素材に仕様長を超える尾が無いため折り返しなし"}
        fc = (f"[0:a]atrim=0:{dur},asetpts=N/SR/TB[body];"
              f"[0:a]atrim=start={dur}:end={2 * dur},asetpts=N/SR/TB,"
              f"apad=whole_dur={dur}[tail];"
              f"[body][tail]amix=inputs=2:normalize=0:duration=first[out]")
        r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", cur,
                    "-filter_complex", fc, "-map", "[out]", "-ar", str(SR), out])
        if r.returncode != 0:
            raise RuntimeError("定常状態レンダリング失敗: " + r.stderr[-500:])
        return out, {"method": "steady_state", "wrapped_tail": True,
                     "tail_s": round(src_dur - dur, 2)}
    # クロスフェード代替（探索型）
    cand_ms = sorted({ms for ms in (50, 250, 500, 1000, 2000)
                      if ms <= xfade_arg * 1000} | {int(round(xfade_arg * 1000))})
    probe = os.path.join(out_dir, "xfade_probe.wav")
    make_xfade_variant(cur, dur, min(500, cand_ms[-1]) / 1000.0, "qsin", probe)
    rho = seam_of(probe).get("phase_corr_rho")
    if rho is not None and rho >= 0.5:
        curves = ["tri"]          # 相関が高い素材は等電圧系
    elif rho is not None and rho <= 0.2:
        curves = ["qsin"]         # 無相関な素材は等パワー
    else:
        curves = ["qsin", "tri"]  # 中間は両方を描いて指標の小さい方
    best = None
    for ms in cand_ms:
        for curve in curves:
            path = os.path.join(out_dir, f"xfade_{ms}_{curve}.wav")
            make_xfade_variant(cur, dur, ms / 1000.0, curve, path)
            s = seam_of(path)
            jump = s["worst"].get("jump_ratio", 9.9)
            flux = s.get("flux_ratio") or 9.9
            score = (0 if s["verdict"] == "PASS" else 1, round(jump + flux, 3))
            if best is None or score < best[0]:
                best = (score, path, {"method": "crossfade", "xfade_ms": ms,
                                      "curve": curve, "probe_rho": rho,
                                      "seam": s})
    return best[1], best[2]


def parse_sends(text, default):
    if not text:
        return default
    out = {}
    for part in text.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = float(v)
    return out or default


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--mid", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--sf", default=DEFAULT_SF)
    ap.add_argument("--gain", type=float, default=1.0)
    ap.add_argument("--baseline", help="基準曲の音声ファイル、または baseline.json")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--xfade", type=float, default=None,
                    help="クロスフェード代替を使う場合の最大長 [s]（未指定なら定常状態レンダリング）")
    ap.add_argument("--reverb-level", type=float, default=None,
                    help="FluidSynth 内蔵リバーブのレベル (0.0-1.0)。未指定なら SYNTH_SETTINGS の既定")
    ap.add_argument("--preset", choices=sorted(DELIVERY_PRESETS), default=None,
                    help="delivery プリセット（納品種別）。--lufs/--tp より優先度は下")
    ap.add_argument("--lufs", type=float, default=None)
    ap.add_argument("--tp", type=float, default=None)
    ap.add_argument("--allow-dynamic-reason", default=None,
                    help="loudnorm の dynamic モードを意図的に許可する理由（記録される）")
    ap.add_argument("--space-ir", default=None, help="主空間の IR ファイル（afir センド方式）")
    ap.add_argument("--space-wet-db", type=float, default=-20.0)
    ap.add_argument("--space-rt60", type=float, default=None)
    ap.add_argument("--space-predelay-ms", type=float, default=0.0)
    ap.add_argument("--space-type", default=None, help="IR 種別の名前（記録用）")
    ap.add_argument("--space-sends", default=None,
                    help='送り量表 "パート:dB,..."（記録用、afir 時は全体量の目安）')
    args = ap.parse_args()

    if args.space_ir and args.reverb_level is not None:
        print("指定が矛盾しています: --space-ir（afir が主空間）と --reverb-level"
              "（内蔵リバーブが主空間）は併用できません。二重掛け禁止のため停止します。",
              file=sys.stderr)
        return 2

    # delivery プリセット解決（明示の --lufs/--tp があればそちらを優先する）
    preset_name = args.preset or "custom"
    preset = DELIVERY_PRESETS.get(args.preset, {})
    lufs_target = args.lufs if args.lufs is not None else preset.get("lufs", -14.0)
    lufs_tol = preset.get("lufs_tol", 0.5)
    tp_target0 = args.tp if args.tp is not None else preset.get("tp", -1.0)

    os.makedirs(args.out, exist_ok=True)
    report = {"name": args.name, "spec": args.spec, "mid": args.mid,
              "soundfont": args.sf, "steps": []}
    report["preset"] = {
        "name": preset_name, "lufs": lufs_target, "lufs_tol": lufs_tol,
        "tp": tp_target0, "unverified": bool(preset.get("unverified")),
        "note": ("未検証の作業値（実機で確認して確定する）"
                 if preset.get("unverified") else None)}

    def step(name, detail, secs):
        report["steps"].append({"step": name, "detail": detail,
                                "seconds": round(secs, 2)})
        print(f"[{name}] {detail} ({secs:.1f}s)")

    def save_report():
        report["total_seconds"] = round(
            sum(s["seconds"] for s in report["steps"]), 2)
        with open(os.path.join(args.out, "render_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

    # 0. preflight: 前提が欠けたまま描画に入らない。不足は復旧手順つきで即停止する。
    problems = preflight(args)
    if problems:
        report["preflight"] = {"ok": False,
                                "problems": [{"item": i, "status": s, "recovery": r}
                                             for i, s, r in problems]}
        print("事前検査で停止しました。不足している前提:", file=sys.stderr)
        for item, status, recovery in problems:
            print(f"  - {item}: {status}", file=sys.stderr)
            print(f"      復旧: {recovery}", file=sys.stderr)
        save_report()
        return 2
    report["preflight"] = {"ok": True}
    step("preflight", "fluidsynth / ffmpeg / ffprobe / venv Python / 音源 / 入力ファイルを確認", 0.0)

    # シンセ設定（全量明示）と空間経路の決定
    afir_space = args.space_ir is not None
    settings, settings_hash, fs_opts = synth_settings(
        args.gain, args.reverb_level, afir_space)
    report["synth_settings"] = settings
    report["synth_settings_hash"] = settings_hash
    if afir_space:
        report["space"] = {
            "path": "afir", "ir": os.path.basename(args.space_ir),
            "ir_type": args.space_type, "rt60_s": args.space_rt60,
            "predelay_ms": args.space_predelay_ms,
            "sends": parse_sends(args.space_sends, {"all": args.space_wet_db})}
    else:
        report["space"] = {
            "path": "fluidsynth", "ir": None, "ir_type": "内蔵リバーブ",
            "rt60_s": None, "predelay_ms": 0,
            "sends": {"all": f"内蔵リバーブ level {settings['reverb']['level']}"},
            "reverb": settings["reverb"]}
    report["complete"] = report["space"] is not None
    if not report["complete"]:
        report["unfinished_reasons"] = ["空間経路の記録が無い"]

    raw = os.path.join(args.out, "raw.wav")
    # 1. render
    gain = args.gain
    r, dt = run(["fluidsynth", "-ni", "-g", str(gain)] + fs_opts + ["-F", raw,
                 "-r", str(SR), args.sf, args.mid])
    if r.returncode != 0:
        print(r.stderr[-1000:])
        save_report()
        return 1
    m_raw = measure_json(raw)
    if m_raw["peak_dbfs"] > -1.0:
        gain = round(gain * 0.7, 3)
        settings, settings_hash, fs_opts = synth_settings(
            gain, args.reverb_level, afir_space)
        report["synth_settings"] = settings
        report["synth_settings_hash"] = settings_hash
        r, dt2 = run(["fluidsynth", "-ni", "-g", str(gain)] + fs_opts + ["-F", raw,
                      "-r", str(SR), args.sf, args.mid])
        dt += dt2
        if r.returncode != 0:
            print(r.stderr[-1000:])
            save_report()
            return 1
        m_raw = measure_json(raw)
        step("render", f"ピーク超過のため gain を {gain} に下げて描き直し。"
                       f"raw: {m_raw['lufs_i']} LUFS, low {m_raw['band_low_pct']}%", dt)
    else:
        step("render", f"gain {gain}: {m_raw['lufs_i']} LUFS, "
                       f"peak {m_raw['peak_dbfs']} dBFS, low {m_raw['band_low_pct']}%", dt)
    report["raw_metrics"] = m_raw

    # 1b. 描画成功の3点判定 + 無音区間の区間検出。後段の不可解なエラーにせずここで止める
    chk = render_check(r, raw, spec_duration(args.spec), m_raw, args.sf)
    report["render_check"] = chk
    if not chk["ok"]:
        print("描画の成功判定で停止しました: "
              f"終了コード ok={chk['exit_code_ok']}, stderr ハード一致={chk['stderr_hard_matches']}, "
              f"プリセット代替={chk['stderr_substitution_matches']}, "
              f"長さ ok={chk['duration_ok']} ({chk['duration_actual_s']}s / 仕様 {chk['duration_expected_s']}s), "
              f"無音でない={chk['not_silent']}, 無音区間の失敗={chk['silence_segments']['failed']}",
              file=sys.stderr)
        if chk["silence_detail"]:
            print("  " + chk["silence_detail"], file=sys.stderr)
        save_report()
        return 2
    step("render_check", "終了コード・標準エラー・出力実測の3点と無音区間を確認", 0.0)

    # 2. 空間（afir 指定時のみ後段で付ける。内蔵リバーブは描画時に設定済み）
    cur = raw
    m_cur = m_raw
    if afir_space:
        t0 = time.time()
        spaced = os.path.join(args.out, "spaced.wav")
        apply_space_afir(raw, args.space_ir, args.space_wet_db,
                         args.space_predelay_ms, args.space_rt60, spaced)
        cur = spaced
        m_cur = measure_json(cur)
        step("space", f"afir センド: wet {args.space_wet_db} dB, "
                      f"IR {os.path.basename(args.space_ir)}, "
                      f"プリディレイ {args.space_predelay_ms} ms", time.time() - t0)
        report["spaced_metrics"] = m_cur

    # 3. baseline
    baseline_json = None
    if args.baseline:
        if args.baseline.endswith(".json"):
            baseline_json = args.baseline
        else:
            t0 = time.time()
            bm = measure_json(args.baseline)
            baseline_json = os.path.join(args.out, "baseline.json")
            with open(baseline_json, "w", encoding="utf-8") as f:
                json.dump(bm, f, ensure_ascii=False, indent=2)
            step("baseline", f"{args.baseline}: {bm['lufs_i']} LUFS, "
                             f"low {bm['band_low_pct']}%, centroid {bm['centroid_hz']} Hz",
                 time.time() - t0)
            report["baseline_metrics"] = bm

    # 4. EQ iteration (max 2)
    eq_gain = 0.0
    eq_hist = []
    if baseline_json:
        with open(baseline_json, encoding="utf-8") as f:
            base_low = json.load(f)["band_low_pct"]
        for i in range(2):
            low = m_cur["band_low_pct"]
            if base_low - 10 <= low <= base_low + 10:
                break
            eq_gain += 3.5 if low < base_low - 10 else -3.5
            eq_out = os.path.join(args.out, f"eq{i + 1}.wav")
            r, dt = run(["ffmpeg", "-v", "error", "-y", "-i", cur,
                         "-af", f"lowshelf=f=180:g={eq_gain}", eq_out])
            if r.returncode != 0:
                print(r.stderr[-500:])
                save_report()
                return 1
            cur = eq_out
            m_cur = measure_json(cur)
            eq_hist.append({"iter": i + 1, "lowshelf_db_at_180hz": eq_gain,
                            "band_low_pct": m_cur["band_low_pct"]})
            step("eq", f"lowshelf {eq_gain:+.1f} dB @180Hz -> low {m_cur['band_low_pct']}%"
                       f" (基準 {base_low}%)", dt)
        report["eq_history"] = eq_hist

    # 5. ループ化（loudnorm の前）
    master_src = cur
    if args.loop:
        t0 = time.time()
        dur_spec = spec_duration(args.spec)
        master_src, loop_info = build_loop_source(
            cur, dur_spec, args.out, args.xfade)
        report["loop"] = loop_info
        step("loop", f"ループ化 ({loop_info['method']}): 仕様の長さ {dur_spec:.2f}s",
             time.time() - t0)

    # 6. loudnorm 2パス + TP 調整（符号化後のトゥルーピーク超過を目標側で下げ直す）
    t0 = time.time()
    mastered = os.path.join(args.out, "mastered.wav")
    ogg = os.path.join(args.out, args.name + ".ogg")
    tp_target = tp_target0
    loudnorm_hist = []
    try:
        info = master(master_src, mastered, lufs_target, tp_target,
                      args.allow_dynamic_reason)
        loudnorm_hist.append(info)
        m_fin = encode(mastered, ogg)
        tp_hist = [{"tp_target": tp_target, "measured_tp": m_fin["true_peak_dbtp"],
                    "normalization_type": info["normalization_type"]}]
        for _ in range(2):
            if m_fin["true_peak_dbtp"] <= tp_target0:
                break
            tp_target = round(tp_target - (m_fin["true_peak_dbtp"] - tp_target0) - 0.3, 2)
            info = master(master_src, mastered, lufs_target, tp_target,
                          args.allow_dynamic_reason)
            loudnorm_hist.append(info)
            m_fin = encode(mastered, ogg)
            tp_hist.append({"tp_target": tp_target,
                            "measured_tp": m_fin["true_peak_dbtp"],
                            "normalization_type": info["normalization_type"]})
    except DynamicFallbackError as e:
        report["loudnorm"] = {"ok": False, "error": str(e),
                              "history": loudnorm_hist}
        print("loudnorm で停止しました: " + str(e), file=sys.stderr)
        save_report()
        return 1
    report["loudnorm"] = {"ok": True, "history": loudnorm_hist}
    report["tp_adjustments"] = tp_hist
    step("loudnorm", f"2パス: {m_fin['lufs_i']} LUFS, TP {m_fin['true_peak_dbtp']} dBTP, "
                     f"low {m_fin['band_low_pct']}%, centroid {m_fin['centroid_hz']} Hz "
                     f"(プリセット {preset_name}, normalization_type "
                     f"{loudnorm_hist[-1]['normalization_type']})",
         time.time() - t0)
    report["final_metrics"] = m_fin
    report["final_file"] = ogg

    # 7. ループの継ぎ目計測（最終符号化ファイルに対して行う）
    if args.loop:
        t0 = time.time()
        loop_ogg = os.path.join(args.out, args.name + "_loop.ogg")
        shutil.copyfile(ogg, loop_ogg)
        seam = seam_of(ogg)
        report["loop"]["file"] = loop_ogg
        report["loop"]["seam"] = seam
        step("seam", f"継ぎ目合議判定: {seam['verdict']} "
                     f"(跳び比 {seam['worst'].get('jump_ratio')}, "
                     f"フラックス比 {seam.get('flux_ratio')}, "
                     f"帯域段差 {seam.get('band_step_max_db')} dB)",
             time.time() - t0)

    # 8. gate + midi check
    t0 = time.time()
    gate_cmd = ["python3", MEASURE, "--gate", ogg, "--spec", args.spec,
                "--lufs-target", str(lufs_target), "--lufs-tol", str(lufs_tol),
                "--tp-max", str(tp_target0),
                "--json-out", os.path.join(args.out, "gate.json")]
    if args.loop:
        gate_cmd += ["--loop"]
    if baseline_json:
        gate_cmd += ["--baseline", baseline_json]
    r, _ = run(gate_cmd)
    gate_res = json.loads(r.stdout)
    report["gate"] = gate_res
    step("gate", f"音響ゲート: {gate_res['verdict']} "
                 f"({', '.join(c['check'] + '=' + ('OK' if c['pass'] else 'NG') for c in gate_res['checks'])})"
                 + (f" WARN: {', '.join(gate_res.get('warnings', []))}"
                    if gate_res.get("warnings") else ""),
         time.time() - t0)

    r, dt = run([VENV_PY, CHECK_MIDI, args.spec, args.mid])
    if r.returncode in (0, 1) and r.stdout.strip():
        midi_res = json.loads(r.stdout)
        report["midi_check"] = midi_res
        step("midi_check", f"記譜確認: {midi_res['verdict']}", dt)
    else:
        step("midi_check", "実行失敗: " + (r.stderr[-300:] if r.stderr else ""), dt)

    save_report()
    print(f"done: {ogg} (total {report['total_seconds']}s) -> "
          f"{os.path.join(args.out, 'render_report.json')}"
          + ("" if report["complete"] else " [未完成: 空間経路の記録なし]"))
    return 0 if gate_res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
