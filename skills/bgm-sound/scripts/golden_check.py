#!/usr/bin/env python3
"""golden 回帰テスト（納品ゲートとは別層の点検）。

経路別の短い固定入力（GM / SFZ 実録 / ギター / ループ継ぎ目 / 異常系）を
描き直し、保存済みの golden と比べる。保存するのは音声ではなく
計測 JSON + framehash + 環境フィンガープリント + 承認理由だけ。

比較は2層:
  同一環境では framehash のビット一致を要求する。
  環境が変わっていたら計測値の許容差（LUFS ±0.5、TP ±0.3 dB、
  長さ ±0.1 s、低域比 ±2 pt、重心 ±5%）で比べ、ENV_DRIFT として報告する。
実行の最初に決定性自己監査（同じ入力を2回描いて framehash が一致するか）
を行い、一致しない場合は framehash を合否に使わず計測値だけで運用する。

更新は --update-golden --reason "理由" の明示操作のみ。検証モードは
絶対に golden を書き換えない。

使い方:
  python3 scripts/golden_check.py
  python3 scripts/golden_check.py --update-golden --reason "初回登録"
終了コード: 0=一致、1=不一致、2=golden 未登録、3=ENV_DRIFT（許容差内）
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/hatch/workspace/midi-bgm/venv/lib/python3.12/site-packages")
import mido  # noqa: E402

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
SKILL = HERE.parent
GOLDEN_DIR = SKILL / "tests" / "golden"
EXPECTED_PATH = GOLDEN_DIR / "expected.json"
LOCK_PATH = SKILL / "environment.lock.json"
SF_DIR = os.path.expanduser("~/workspace/soundfonts")
GM_SF2 = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
FORGE_SF2 = str(SKILL / "instruments" / "forge_piano.sf2")
KALIMBA_SFZ = f"{SF_DIR}/freepats-kalimba/Kalimba-SFZ-20190723/Kalimba-20190723.sfz"
GUITAR_SAMPLES = f"{SF_DIR}/bng-release/Samples/black/ord"
RATE = 44100
TIMEOUT = 300
TOLERANCES = {"lufs": 0.5, "true_peak": 0.3, "duration_s": 0.1,
              "band_low_pct": 2.0, "centroid_rel": 0.05}

CASES = ["gm", "sfz", "guitar", "loop", "broken"]


def run(cmd, timeout=TIMEOUT):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


# ---------------------------------------------------------------- fixtures
def ensure_fixtures():
    """固定入力（MIDI / イベント JSON）を決定的に生成する。"""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    gm = GOLDEN_DIR / "gm.mid"
    if not gm.exists():
        mid = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack()
        mid.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        tr.append(mido.Message("program_change", channel=0, program=0, time=0))
        for i, n in enumerate([60, 62, 64, 65, 67, 69, 71, 72]):
            tr.append(mido.Message("note_on", channel=0, note=n, velocity=96,
                                   time=0 if i == 0 else 240))
            tr.append(mido.Message("note_off", channel=0, note=n, velocity=0,
                                   time=240))
        tr2 = mido.MidiTrack()
        mid.tracks.append(tr2)
        tr2.append(mido.Message("program_change", channel=1, program=48, time=0))
        for n in (48, 52, 55):
            tr2.append(mido.Message("note_on", channel=1, note=n, velocity=64,
                                    time=0))
        for j, n in enumerate((48, 52, 55)):
            tr2.append(mido.Message("note_off", channel=1, note=n, velocity=0,
                                    time=3840 if j == 0 else 0))
        mid.save(gm)
    loop = GOLDEN_DIR / "loop.mid"
    if not loop.exists():
        mid = mido.MidiFile(ticks_per_beat=480)
        tr = mido.MidiTrack()
        mid.tracks.append(tr)
        tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        tr.append(mido.Message("program_change", channel=0, program=0, time=0))
        for bar in range(2):
            for n in [60, 64, 67, 64]:
                tr.append(mido.Message("note_on", channel=0, note=n,
                                       velocity=96, time=0))
                tr.append(mido.Message("note_off", channel=0, note=n,
                                       velocity=0, time=480))
        mid.save(loop)
    for name, pitches in (("sfz_events", [60, 64, 67, 72]),
                          ("guitar_events", [52, 57, 59, 64])):
        p = GOLDEN_DIR / f"{name}.json"
        if not p.exists():
            ev = [{"start": i * 0.6, "dur": 0.55, "pitch": n, "vel": 100}
                  for i, n in enumerate(pitches)]
            p.write_text(json.dumps(ev))


# ---------------------------------------------------------------- rendering
def render_case(case, work):
    """ケースを描き、(成果物パス or None, 期待どおり失敗したか) を返す。"""
    if case == "gm":
        out = work / "gm.wav"
        r = run(["fluidsynth", "-ni", "-F", str(out), "-r", str(RATE),
                 "-O", "s16", "-g", "0.8", GM_SF2, str(GOLDEN_DIR / "gm.mid")])
        if r.returncode != 0:
            raise RuntimeError(f"gm render failed: {r.stderr[-300:]}")
        return out, False
    if case == "sfz":
        out = work / "sfz.wav"
        r = run(["python3", str(HERE / "render_sfz.py"), "--sfz", KALIMBA_SFZ,
                 str(GOLDEN_DIR / "sfz_events.json"), str(out)])
        if r.returncode != 0:
            raise RuntimeError(f"sfz render failed: {r.stderr[-300:]}")
        return out, False
    if case == "guitar":
        di = work / "guitar_di.wav"
        out = work / "guitar.wav"
        r = run(["python3", str(HERE / "render_guitar.py"), GUITAR_SAMPLES,
                 str(GOLDEN_DIR / "guitar_events.json"), str(di)])
        if r.returncode != 0:
            raise RuntimeError(f"guitar DI failed: {r.stderr[-300:]}")
        r = run(["python3", str(HERE / "guitar_fx.py"), "distortion",
                 str(di), str(out)])
        if r.returncode != 0:
            raise RuntimeError(f"guitar fx failed: {r.stderr[-300:]}")
        return out, False
    if case == "loop":
        out = work / "loop.ogg"
        r = run(["python3", str(HERE / "render_mix.py"),
                 str(GOLDEN_DIR / "loop.mid"), str(out),
                 "--forge-sf2", FORGE_SF2, "--forge-channels", "0",
                 "--loop-xfade", "0.5"])
        if r.returncode != 0:
            raise RuntimeError(f"loop render failed: {r.stderr[-300:]}")
        return out, False
    if case == "broken":
        out = work / "broken.ogg"
        r = run(["python3", str(HERE / "render_mix.py"),
                 str(GOLDEN_DIR / "gm.mid"), str(out),
                 "--forge-sf2", FORGE_SF2, "--forge-channels", "0",
                 "--gm-sf2", str(work / "nonexistent.sf2")])
        return None, r.returncode != 0
    raise ValueError(case)


# ---------------------------------------------------------------- metrics
def framehash_of(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                        "-f", "framehash", "-hash", "sha256", "-"],
                       capture_output=True, text=True, timeout=TIMEOUT)
    if r.returncode != 0:
        raise RuntimeError(f"framehash failed for {path}")
    lines = [ln for ln in r.stdout.splitlines()
             if ln and not ln.startswith("#")]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def loudnorm_measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=TIMEOUT)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("loudnorm measurement failed")
    return json.loads(m[-1])


def duration_of(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=TIMEOUT)
    return float(r.stdout.strip())


def spectral_metrics(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
                        "-ac", "1", "-ar", str(RATE), "-"],
                       capture_output=True, timeout=TIMEOUT)
    x = np.frombuffer(r.stdout, dtype=np.float32).astype(np.float64)
    win, hop = 2048, 1024
    if len(x) < win:
        x = np.pad(x, (0, win - len(x)))
    frames = [x[i:i + win] * np.hanning(win)
              for i in range(0, len(x) - win + 1, hop)]
    power = np.mean([np.abs(np.fft.rfft(f)) ** 2 for f in frames], axis=0)
    freqs = np.fft.rfftfreq(win, 1.0 / RATE)
    band = (freqs >= 20) & (freqs <= 16000)
    total = power[band].sum()
    low = power[band & (freqs <= 250)].sum()
    centroid = float((freqs[band] * power[band]).sum() / total) if total else 0.0
    return {"band_low_pct": round(100.0 * low / total, 2) if total else 0.0,
            "centroid_hz": round(centroid, 1)}


def metrics_of(path):
    meas = loudnorm_measure(path)
    m = {"lufs": float(meas["input_i"]),
         "true_peak": float(meas["input_tp"]),
         "duration_s": round(duration_of(path), 3)}
    m.update(spectral_metrics(path))
    return m


def env_fingerprint():
    if LOCK_PATH.exists():
        return {"lock_sha256": hashlib.sha256(
            LOCK_PATH.read_bytes()).hexdigest()}
    out = {}
    for tool in ("fluidsynth", "ffmpeg"):
        r = run([tool, "--version"], timeout=30)
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", r.stdout + r.stderr)
        out[tool] = m.group(1) if m else None
    return out


def metrics_within_tol(cur, exp):
    diffs = []
    for k in ("lufs", "true_peak", "duration_s", "band_low_pct"):
        d = abs(cur[k] - exp[k])
        if d > TOLERANCES[k]:
            diffs.append(f"{k}: {cur[k]} vs golden {exp[k]} (diff {d:.2f})")
    d = abs(cur["centroid_hz"] - exp["centroid_hz"])
    if d > TOLERANCES["centroid_rel"] * max(exp["centroid_hz"], 1.0):
        diffs.append(f"centroid_hz: {cur['centroid_hz']} vs golden "
                     f"{exp['centroid_hz']} (diff {d:.1f})")
    return diffs


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update-golden", action="store_true",
                    help="golden を更新する（--reason 必須）")
    ap.add_argument("--reason", default=None, help="更新理由（必須）")
    a = ap.parse_args()
    if a.update_golden and not a.reason:
        ap.error("--update-golden には --reason が必須です")

    ensure_fixtures()
    env = env_fingerprint()

    # 決定性自己監査: 同じ入力を2回描いて framehash が一致するか
    deterministic = True
    with tempfile.TemporaryDirectory(prefix="golden_audit_") as td:
        w = Path(td)
        hashes = []
        for sub in ("a", "b"):
            d = w / sub
            d.mkdir()
            p, _ = render_case("gm", d)
            hashes.append(framehash_of(p))
        if hashes[0] != hashes[1]:
            deterministic = False
    print(f"決定性自己監査: {'一致（決定的）' if deterministic else '不一致'}"
          + ("" if deterministic else
             " — framehash を合否に使わず計測値だけで運用する"))

    results = {}
    with tempfile.TemporaryDirectory(prefix="golden_run_") as td:
        work = Path(td)
        for case in CASES:
            cdir = work / case
            cdir.mkdir()
            path, failed_as_expected = render_case(case, cdir)
            if case == "broken":
                results[case] = {"expect": "failure",
                                 "failed_as_expected": failed_as_expected}
            else:
                results[case] = {"expect": "success",
                                 "framehash": framehash_of(path),
                                 "metrics": metrics_of(path)}

    if a.update_golden:
        data = {"version": 1, "env": env,
                "deterministic": deterministic,
                "cases": {}}
        for case, res in results.items():
            entry = dict(res)
            entry["approved"] = {"date": date.today().isoformat(),
                                 "reason": a.reason}
            data["cases"][case] = entry
        EXPECTED_PATH.write_text(json.dumps(data, ensure_ascii=False,
                                            indent=2))
        print(f"golden を更新しました: {EXPECTED_PATH}（理由: {a.reason}）")
        return 0

    if not EXPECTED_PATH.exists():
        print(f"golden が未登録です: {EXPECTED_PATH}\n"
              f"復旧: --update-golden --reason \"理由\" で登録する")
        return 2
    golden = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    env_changed = golden.get("env") != env
    problems = []
    drift_notes = []
    for case in CASES:
        exp = golden["cases"].get(case)
        if exp is None:
            problems.append(f"{case}: golden にケースが無い")
            continue
        cur = results[case]
        if exp["expect"] == "failure":
            if not cur.get("failed_as_expected"):
                problems.append(f"{case}: 異常系が失敗せず通ってしまった")
            continue
        diffs = metrics_within_tol(cur["metrics"], exp["metrics"])
        if env_changed:
            if diffs:
                problems.append(f"{case}: " + "; ".join(diffs))
            else:
                drift_notes.append(case)
        else:
            if deterministic and cur["framehash"] != exp["framehash"]:
                problems.append(f"{case}: framehash が一致しない")
            elif diffs:
                problems.append(f"{case}: " + "; ".join(diffs))
    for case in drift_notes:
        print(f"ENV_DRIFT  {case}: 環境が変わったが計測値は許容差内")
    if problems:
        for p in problems:
            print(f"FAIL  {p}")
        return 1
    if env_changed:
        print("全ケース許容差内（ENV_DRIFT）。"
              "意図した環境変更なら --update-golden --reason で再承認する")
        return 3
    print("全ケース golden 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main())
