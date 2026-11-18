#!/usr/bin/env python3
"""レンダリング→マスタリング→納品ゲートのパイプライン（標準ライブラリのみ）。

使い方:
  python3 render.py --spec SPEC.py --mid SONG.mid --out OUTDIR --name spring_noon \
      [--baseline REFERENCE.ogg] [--sf SOUNDFONT] [--gain 1.0] [--loop] [--xfade 2.0]

手順:
  0. 事前検査: fluidsynth / ffmpeg / ffprobe の実行ファイル、venv の Python、
     音源ファイル、仕様書・MIDI の存在を確認する。不足があれば、何が無いかと
     復旧手順を明示して描画に入らず即座に停止する（終了コード 2）。
  1. FluidSynth で WAV 描画（ピークが -1 dBFS を超えたら gain を下げて1回だけ描き直す）。
     描画直後に無音・ノイズフロアだけの出力を検出したら失敗として報告し停止する
     （音源が読み込まれていないのに正常終了するケースへの対策。終了コード 2）。
  2. 基準曲があれば計測して baseline.json を作る
  3. 低域比が基準の許容（±10pt）から外れていれば lowshelf EQ を ±3.5dB ずつ、
     測定→補正→再測定を最大2回（必ず生描画から当て直す）
  4. loudnorm 2パス（1パス目で測定、測定値で線形補正）: 目標 -14 LUFS / -1 dBTP
  5. --loop のとき末尾クロスフェード版を作り、継ぎ目段差を計測する
  6. 納品ゲート（measure.py --gate）と記譜確認（check_midi.py）を実行し、
     render_report.json に全記録（所要時間つき）を残す

計測は measure.py（システム python3 + numpy）、記譜確認は check_midi.py
（venv の python + mido）を子プロセスで呼ぶ。
"""
import argparse
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


def master(cur, mastered, lufs, tp):
    """loudnorm 2パス（測定→測定値で線形補正）で mastered.wav を作る。"""
    st = loudnorm_stats(cur, lufs, tp)
    af = (f"loudnorm=I={lufs}:TP={tp}:LRA=11:"
          f"measured_I={st['input_i']}:measured_LRA={st['input_lra']}:"
          f"measured_TP={st['input_tp']}:measured_thresh={st['input_thresh']}:"
          f"offset={st['target_offset']}:linear=true")
    r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", cur, "-af", af,
                "-ar", "44100", mastered])
    if r.returncode != 0:
        raise RuntimeError("loudnorm 適用失敗: " + r.stderr[-500:])


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
    ap.add_argument("--xfade", type=float, default=2.0)
    ap.add_argument("--reverb-level", type=float, default=None,
                    help="FluidSynth 内蔵リバーブのレベル (0.0-1.0)。未指定なら既定のまま")
    ap.add_argument("--lufs", type=float, default=-14.0)
    ap.add_argument("--tp", type=float, default=-1.0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    report = {"name": args.name, "spec": args.spec, "mid": args.mid,
              "soundfont": args.sf, "steps": []}

    def step(name, detail, secs):
        report["steps"].append({"step": name, "detail": detail,
                                "seconds": round(secs, 2)})
        print(f"[{name}] {detail} ({secs:.1f}s)")

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
        with open(os.path.join(args.out, "render_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 2
    report["preflight"] = {"ok": True}
    step("preflight", "fluidsynth / ffmpeg / ffprobe / venv Python / 音源 / 入力ファイルを確認", 0.0)

    raw = os.path.join(args.out, "raw.wav")
    # 1. render
    gain = args.gain
    fs_opts = []
    if args.reverb_level is not None:
        fs_opts = ["-o", f"synth.reverb.level={args.reverb_level}"]
    r, dt = run(["fluidsynth", "-ni", "-g", str(gain)] + fs_opts + ["-F", raw,
                 "-r", "44100", args.sf, args.mid])
    if r.returncode != 0:
        print(r.stderr[-1000:])
        return 1
    m_raw = measure_json(raw)
    if m_raw["peak_dbfs"] > -1.0:
        gain = round(gain * 0.7, 3)
        r, dt2 = run(["fluidsynth", "-ni", "-g", str(gain)] + fs_opts + ["-F", raw,
                      "-r", "44100", args.sf, args.mid])
        dt += dt2
        m_raw = measure_json(raw)
        step("render", f"ピーク超過のため gain を {gain} に下げて描き直し。"
                       f"raw: {m_raw['lufs_i']} LUFS, low {m_raw['band_low_pct']}%", dt)
    else:
        step("render", f"gain {gain}: {m_raw['lufs_i']} LUFS, "
                       f"peak {m_raw['peak_dbfs']} dBFS, low {m_raw['band_low_pct']}%", dt)
    report["raw_metrics"] = m_raw

    # 1b. silence check: 無音・ノイズフロアだけの描画は、後段の不可解なエラーにせずここで止める
    sil = silence_problem(m_raw, args.sf, r.stderr)
    if sil:
        report["silence_check"] = {"ok": False, "detail": sil}
        print("描画後の無音チェックで停止しました: " + sil, file=sys.stderr)
        with open(os.path.join(args.out, "render_report.json"), "w",
                  encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return 2
    report["silence_check"] = {"ok": True}

    # 2. baseline
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

    # 3. EQ iteration (max 2)
    cur = raw
    eq_gain = 0.0
    eq_hist = []
    if baseline_json:
        with open(baseline_json, encoding="utf-8") as f:
            base_low = json.load(f)["band_low_pct"]
        for i in range(2):
            m_cur = m_raw if cur == raw else measure_json(cur)
            low = m_cur["band_low_pct"]
            if base_low - 10 <= low <= base_low + 10:
                break
            eq_gain += 3.5 if low < base_low - 10 else -3.5
            eq_out = os.path.join(args.out, f"eq{i + 1}.wav")
            r, dt = run(["ffmpeg", "-v", "error", "-y", "-i", raw,
                         "-af", f"lowshelf=f=180:g={eq_gain}", eq_out])
            if r.returncode != 0:
                print(r.stderr[-500:])
                return 1
            cur = eq_out
            m_new = measure_json(cur)
            eq_hist.append({"iter": i + 1, "lowshelf_db_at_180hz": eq_gain,
                            "band_low_pct": m_new["band_low_pct"]})
            step("eq", f"lowshelf {eq_gain:+.1f} dB @180Hz -> low {m_new['band_low_pct']}%"
                       f" (基準 {base_low}%)", dt)
        report["eq_history"] = eq_hist

    # 4. loudnorm 2-pass + TP 調整（符号化後のトゥルーピーク超過を offset で下げ直す）
    #    ループ指定時は仕様の長さで切り詰めてから符号化する（尾は次の周回に重なる前提）
    t0 = time.time()
    mastered = os.path.join(args.out, "mastered.wav")
    ogg = os.path.join(args.out, args.name + ".ogg")
    tp_target = args.tp
    master(cur, mastered, args.lufs, tp_target)
    src_wav = mastered
    if args.loop:
        dur_spec = spec_duration(args.spec)
        trimmed = os.path.join(args.out, "mastered_trim.wav")
        r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", mastered,
                    "-t", f"{dur_spec:.3f}", trimmed])
        if r.returncode != 0:
            print(r.stderr[-500:])
            return 1
        src_wav = trimmed
        step("trim", f"ループ用に仕様の長さ {dur_spec:.2f}s で切り詰め", 0.0)
    m_fin = encode(src_wav, ogg)
    tp_hist = [{"tp_target": tp_target, "measured_tp": m_fin["true_peak_dbtp"]}]
    for _ in range(2):
        if m_fin["true_peak_dbtp"] <= args.tp:
            break
        tp_target = round(tp_target - (m_fin["true_peak_dbtp"] - args.tp) - 0.3, 2)
        master(cur, mastered, args.lufs, tp_target)
        src_wav = mastered
        if args.loop:
            dur_spec = spec_duration(args.spec)
            trimmed = os.path.join(args.out, "mastered_trim.wav")
            run(["ffmpeg", "-v", "error", "-y", "-i", mastered,
                 "-t", f"{dur_spec:.3f}", trimmed])
            src_wav = trimmed
        m_fin = encode(src_wav, ogg)
        tp_hist.append({"tp_target": tp_target, "measured_tp": m_fin["true_peak_dbtp"]})
    report["tp_adjustments"] = tp_hist
    step("loudnorm", f"2パス線形: {m_fin['lufs_i']} LUFS, TP {m_fin['true_peak_dbtp']} dBTP, "
                     f"low {m_fin['band_low_pct']}%, centroid {m_fin['centroid_hz']} Hz"
                     f" (TP目標 {tp_target})",
         time.time() - t0)
    report["final_metrics"] = m_fin
    report["final_file"] = ogg

    # 5. loop
    if args.loop:
        t0 = time.time()
        dur = m_fin["duration_s"]
        x = args.xfade
        fc = (f"[0:a]atrim=0:{dur - x},asetpts=N/SR/TB[m];"
              f"[0:a]atrim={dur - x}:{dur},asetpts=N/SR/TB[t];"
              f"[0:a]atrim=0:{x},asetpts=N/SR/TB[h];"
              f"[t][h]acrossfade=d={x}:c1=tri:c2=tri[xf];"
              f"[m][xf]concat=n=2:v=0:a=1[out]")
        loop_wav = os.path.join(args.out, "loop.wav")
        r, _ = run(["ffmpeg", "-v", "error", "-y", "-i", src_wav,
                    "-filter_complex", fc, "-map", "[out]", "-ar", "44100", loop_wav])
        if r.returncode != 0:
            print(r.stderr[-500:])
            return 1
        loop_ogg = os.path.join(args.out, args.name + "_loop.ogg")
        run(["ffmpeg", "-v", "error", "-y", "-i", loop_wav,
             "-c:a", "libvorbis", "-q:a", "6", loop_ogg])
        r, _ = run(["python3", MEASURE, "--seam", loop_ogg, "--ref", ogg])
        seam = json.loads(r.stdout)
        report["loop"] = {"file": loop_ogg, "xfade_s": x, "seam": seam}
        step("loop", f"クロスフェード {x}s: 継ぎ目段差 {seam['seam_step_db_crossfaded']} dB"
                     f" (素つなぎ {seam['seam_step_db_butt']} dB) -> {seam['verdict']}",
             time.time() - t0)

    # 6. gate + midi check
    t0 = time.time()
    gate_cmd = ["python3", MEASURE, "--gate", ogg, "--spec", args.spec,
                "--json-out", os.path.join(args.out, "gate.json")]
    if baseline_json:
        gate_cmd += ["--baseline", baseline_json]
    r, _ = run(gate_cmd)
    gate_res = json.loads(r.stdout)
    report["gate"] = gate_res
    step("gate", f"音響ゲート: {gate_res['verdict']} "
                 f"({', '.join(c['check'] + '=' + ('OK' if c['pass'] else 'NG') for c in gate_res['checks'])})",
         time.time() - t0)

    r, dt = run([VENV_PY, CHECK_MIDI, args.spec, args.mid])
    if r.returncode in (0, 1) and r.stdout.strip():
        midi_res = json.loads(r.stdout)
        report["midi_check"] = midi_res
        step("midi_check", f"記譜確認: {midi_res['verdict']}", dt)
    else:
        step("midi_check", "実行失敗: " + (r.stderr[-300:] if r.stderr else ""), dt)

    report["total_seconds"] = round(sum(s["seconds"] for s in report["steps"]), 2)
    with open(os.path.join(args.out, "render_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"done: {ogg} (total {report['total_seconds']}s) -> "
          f"{os.path.join(args.out, 'render_report.json')}")
    return 0 if gate_res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
