#!/usr/bin/env python3
"""bgm-compose スキルの横断点検。

環境の移設・復旧直後や、スキルを編集した後に実行する。
道具の有無、台帳（references/instruments.md）の全音源の存在と試し鳴らし、
全スクリプトの構文、作曲スモーク、主題選別の校正記録の読み取りを一括で確認する。

使い方: python3 scripts/selfcheck.py
終了コード: 全て PASS なら 0、一つでも FAIL があれば 1。
"""
import json
import os
import subprocess
import sys
import tempfile
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
VENV_PY = os.path.expanduser("~/workspace/midi-bgm/venv/bin/python")
SF_DIR = os.path.expanduser("~/workspace/soundfonts")

RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# 台帳（references/instruments.md）と対応させる点検対象。
# (名前, 種別, パス, テスト音高, モード)  種別: sfz / folder / guitar / sf2
INSTRUMENTS = [
    ("kalimba", "sfz", f"{SF_DIR}/freepats-kalimba/Kalimba-SFZ-20190723/Kalimba-20190723.sfz", [60, 67, 72], None),
    ("emily_guitar", "sfz", f"{SF_DIR}/emily/Emilyguitar/emily_basic.sfz", [57, 64, 69], None),
    ("shiny_guitar", "sfz", f"{SF_DIR}/sfz-extra/karoryfer.shinyguitar/Shinyguitar/Programs/acoustic.sfz", [57, 64, 69], None),
    ("cello", "sfz", f"{SF_DIR}/sfz-extra/karoryfer-bigcat.cello/Karoryfer_Bigcat_cello/Programs/01- Bowed (velocity layer).sfz", [48, 55, 60], "sustain"),
    ("double_bass", "sfz", f"{SF_DIR}/sfz-extra/dsmolken.double-bass/dsmolken_double_bass/d_smolken_rubner_bass_arco.sfz", [36, 43, 48], "sustain"),
    ("meatbass", "sfz", f"{SF_DIR}/sfz-extra/karoryfer.meatbass/Meatbass/Programs/04_pizz.sfz", [36, 43, 48], None),
    ("pasta_bass", "sfz", f"{SF_DIR}/sfz-extra/karoryfer.pastabass/Pastabass/combo_platter.sfz", [40, 47, 52], None),
    ("bear_sax", "sfz", f"{SF_DIR}/sfz-extra/karoryfer.bear-sax/Bear_Sax/Programs/2-solo-poly.sfz", [46, 53, 58], "sustain"),
    ("marimba", "folder", f"{SF_DIR}/vsco2-src/Percussion/Marimba", [60, 67, 72], None),
    ("xylophone", "folder", f"{SF_DIR}/vsco2-src/Percussion/Xylo", [72, 79, 84], None),
    ("glockenspiel", "folder", f"{SF_DIR}/vsco2-src/Percussion/Glock", [79, 84, 88], None),
    ("tubular_bells", "folder", f"{SF_DIR}/vsco-tb", [60, 65, 72], None),
    ("harp", "folder", f"{SF_DIR}/vsco2-src/Strings/Harp", [60, 64, 67], None),
    ("violins", "folder", f"{SF_DIR}/vsco2-src/Strings/Violin Section/susVib", [57, 64, 69], "sustain"),
    ("flute", "folder", f"{SF_DIR}/vsco2-src/Woodwinds/Flute/susNV", [72, 76, 79], "sustain"),
    ("guitar_black", "guitar", f"{SF_DIR}/bng-release/Samples/black/ord", [52, 59, 64], None),
    ("guitar_green", "guitar", f"{SF_DIR}/bng-release/Samples/green/ord", [52, 59, 64], None),
]
SF2_FILES = [
    ("FluidR3_GM", "/usr/share/sounds/sf2/FluidR3_GM.sf2", 100_000_000),
    ("MuseScore_General", "/usr/share/sounds/sf3/MuseScore_General_Full.sf3", 50_000_000),
    ("GeneralUser_GS", f"{SF_DIR}/GeneralUser-GS.sf2", 20_000_000),
]
# forge_piano の SF2 本体はスキル外に置く運用もあるため、候補のどれかで判定する
FORGE_CANDIDATES = [
    os.path.join(SKILL, "instruments/forge_piano.sf2"),
    os.path.expanduser("~/workspace/custom-skills/bgm-compose/instruments/forge_piano.sf2"),
]
EXPECTED_PRESETS = {"clean", "twang", "lofi", "crunch", "overdrive", "blues",
                    "distortion", "lead_drive", "fuzz", "metal", "chorus_clean",
                    "flanger_clean", "phaser_crunch", "trem_drive", "surf",
                    "ambient", "shoegaze"}
EXPECTED_ADDONS = {"chorus", "flanger", "phaser", "tremolo", "vibrato",
                   "delay_375", "delay_500", "slapback", "presence", "darken"}


def wav_peak(path):
    import numpy as np
    w = wave.open(path)
    n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        a = np.frombuffer(raw, dtype=np.float32)
    return (float(np.abs(a).max()) if a.size else 0.0), n / w.getframerate()


def main():
    tmp = tempfile.mkdtemp(prefix="bgm_selfcheck_")
    # 1. 道具
    for tool in ("fluidsynth", "ffmpeg", "ffprobe"):
        r = sh(["which", tool])
        report(f"tool:{tool}", r.returncode == 0)
    ok = os.path.exists(VENV_PY) and sh([VENV_PY, "-c", "import mido"]).returncode == 0
    report("venv python + mido", ok)
    try:
        import numpy  # noqa: F401
        report("system python3 + numpy", True)
    except ImportError:
        report("system python3 + numpy", False)

    # 2. スキルのファイルとスクリプト構文
    for rel in ("SKILL.md", "references/patterns.md", "references/instruments.md",
                "references/guitar.md", "examples/spec_spring_noon.py",
                "examples/spec_loop_lake.py", "assets/forge_piano.sfz"):
        report(f"file:{rel}", os.path.exists(os.path.join(SKILL, rel)))
    import py_compile
    bad = []
    for root, _dirs, files in os.walk(HERE):
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                try:
                    py_compile.compile(p, doraise=True)
                except Exception as e:  # noqa: BLE001
                    bad.append(f"{f}: {e}")
    report("scripts py_compile", not bad, "; ".join(bad))

    # 3. 台帳との整合（台帳にパスが載っているか）
    ledger = open(os.path.join(SKILL, "references/instruments.md"),
                  encoding="utf-8").read()
    for name, _kind, path, _p, _m in INSTRUMENTS:
        frag = path.replace(os.path.expanduser("~"), "~", 1)
        frag = frag.split("/")[-1]
        report(f"ledger:{name}", frag in ledger, frag)

    # 4. 実録サンプル楽器の試し鳴らし
    for name, kind, path, pitches, mode in INSTRUMENTS:
        if not os.path.exists(path):
            report(f"render:{name}", False, "path missing")
            continue
        ev = [{"start": i * 0.6, "dur": 0.55, "pitch": p, "vel": 100}
              for i, p in enumerate(pitches)]
        evf = os.path.join(tmp, f"{name}.json")
        json.dump(ev, open(evf, "w"))
        out = os.path.join(tmp, f"{name}.wav")
        if kind == "guitar":
            cmd = ["python3", os.path.join(HERE, "render_guitar.py"), path, evf, out]
        else:
            cmd = ["python3", os.path.join(HERE, "render_sfz.py"),
                   "--sfz" if kind == "sfz" else "--folder", path]
            if mode:
                cmd += ["--mode", mode]
            cmd += [evf, out]
        r = sh(cmd)
        if r.returncode != 0 or not os.path.exists(out):
            report(f"render:{name}", False, r.stderr.strip()[-100:])
            continue
        peak, dur = wav_peak(out)
        report(f"render:{name}", peak > 0.05 and dur > 1.0,
               f"peak {peak:.2f}, {dur:.1f}s")

    # 5. SF2 音源の存在とサイズ
    for name, path, min_size in SF2_FILES:
        ok = os.path.exists(path) and os.path.getsize(path) >= min_size
        report(f"sf2:{name}", ok, path)
    forge = next((p for p in FORGE_CANDIDATES
                  if os.path.exists(p) and os.path.getsize(p) >= 30_000_000), None)
    report("sf2:forge_piano", forge is not None,
           forge or "not found in candidate locations")

    # 6. 作曲スモーク（両実例＋記譜確認）
    for ex in ("spec_spring_noon", "spec_loop_lake"):
        spec = os.path.join(SKILL, "examples", f"{ex}.py")
        mid = os.path.join(tmp, f"{ex}.mid")
        r = sh([VENV_PY, os.path.join(HERE, "compose.py"), spec, mid])
        ok = r.returncode == 0 and os.path.exists(mid)
        if ok:
            r2 = sh([VENV_PY, os.path.join(HERE, "check_midi.py"), spec, mid])
            ok = '"verdict": "PASS"' in r2.stdout
        report(f"compose:{ex}", ok)

    # 7. ギター音色プリセット
    sys.path.insert(0, HERE)
    import guitar_fx
    report("guitar_fx presets", EXPECTED_PRESETS <= set(guitar_fx.PRESETS),
           f"{len(guitar_fx.PRESETS)} presets")
    report("guitar_fx addons", EXPECTED_ADDONS <= set(guitar_fx.ADDONS),
           f"{len(guitar_fx.ADDONS)} addons")

    # 8. 主題選別: 既定パスで校正記録が読まれ、権限セットが一致するか
    sys.path.insert(0, os.path.join(HERE, "theme"))
    from theme_check import load_authority
    rec_path = os.path.join(HERE, "theme", "calibration",
                            "calibration_record.json")
    auth, rec = load_authority(rec_path)
    rec_auth = sorted(k for k, v in rec.get("authority", {}).items()
                      if v.get("authority")) if rec else []
    report("theme calibration authority", sorted(auth) == rec_auth and bool(auth),
           ", ".join(auth))

    n_fail = sum(1 for _n, ok in RESULTS if not ok)
    print(f"\n{len(RESULTS) - n_fail}/{len(RESULTS)} PASS"
          + (f", {n_fail} FAIL" if n_fail else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
