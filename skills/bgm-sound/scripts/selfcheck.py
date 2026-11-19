#!/usr/bin/env python3
"""bgm-sound スキルの横断点検（音色・レンダリング側）。

環境の移設・復旧直後や、スキルを編集した後に実行する。
道具の有無と実働、台帳（references/instruments.md）の全音源の存在と
試し鳴らし、ギター音色プリセット、全スクリプトの構文を一括で確認する。
作曲側の点検は bgm-compose の selfcheck.py が担う。

FAIL は3分類（+TIMEOUT）で集計する:
  ENV_MISSING  道具・音源・ロックファイルが無い
  ENV_DRIFT    あるが版・ハッシュ・参照先が environment.lock.json や
               恒久配置の前提と違う
  SKILL_BROKEN スキル側の破損（構文エラー、プリセット欠落、台帳不整合）
  TIMEOUT      外部コマンドが制限時間内に応答しない
FAIL の表示は「項目・状況・復旧手順」の3点セットに統一する。
外部コマンドにはタイムアウトを付け、自動リトライはしない（揺れを隠さない）。

使い方: python3 scripts/selfcheck.py
終了コード: 0=全 PASS、1=SKILL_BROKEN あり、2=ENV_MISSING あり、
           3=ENV_DRIFT のみ、4=TIMEOUT のみ。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
VENV_PY = os.path.expanduser("~/workspace/midi-bgm/venv/bin/python")
SF_DIR = os.path.expanduser("~/workspace/soundfonts")
IR_DIR = os.path.join(SF_DIR, "ir")
LOCK_PATH = os.path.join(SKILL, "environment.lock.json")
TIMEOUT_PROBE = 15     # 版確認などの軽い外部コマンド
TIMEOUT_RENDER = 60    # 試し鳴らし1件

RESULTS = []           # (name, ok, category)
SECTION_TIMES = []


def report(name, ok, detail="", category=None, remedy=""):
    if ok:
        RESULTS.append((name, True, None))
        print(f"PASS  {name}" + (f" — {detail}" if detail else ""))
    else:
        cat = category or "SKILL_BROKEN"
        RESULTS.append((name, False, cat))
        msg = f"FAIL  [{cat}] {name}"
        if detail:
            msg += f" — 状況: {detail}"
        if remedy:
            msg += f" / 復旧: {remedy}"
        print(msg)


class ShResult:
    def __init__(self, returncode, stdout="", stderr="", timed_out=False):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out


def sh(cmd, timeout=TIMEOUT_RENDER):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return ShResult(r.returncode, r.stdout, r.stderr)
    except subprocess.TimeoutExpired:
        return ShResult(None, timed_out=True)
    except OSError as e:
        return ShResult(127, stderr=str(e))


def section(title):
    def deco(fn):
        def wrap(*a, **kw):
            t0 = time.monotonic()
            fn(*a, **kw)
            SECTION_TIMES.append((title, time.monotonic() - t0))
        return wrap
    return deco


def load_lock():
    if not os.path.exists(LOCK_PATH):
        return None
    try:
        return json.load(open(LOCK_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def major_minor(v):
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


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
FORGE_CANDIDATES = [
    os.path.join(SKILL, "instruments/forge_piano.sf2"),
    os.path.expanduser("~/workspace/custom-skills/bgm-sound/instruments/forge_piano.sf2"),
]
EXPECTED_PRESETS = {"clean", "twang", "lofi", "crunch", "overdrive", "blues",
                    "distortion", "lead_drive", "lead_chord", "fuzz", "metal",
                    "chorus_clean", "flanger_clean", "phaser_crunch",
                    "trem_drive", "surf", "ambient", "shoegaze"}
EXPECTED_ADDONS = {"chorus", "flanger", "phaser", "tremolo", "vibrato",
                   "delay_375", "delay_500", "slapback", "presence", "darken",
                   "gate_tight", "bgm_backing"}
EXPECTED_CABS = {"cap", "cap_edge", "cone"}
IR_KINDS = ["ambience", "room", "spring", "plate", "chamber", "hall"]
NONPERMANENT_MARKERS = ("breakthrough-runs", "/tmp/")


def wav_peak(path):
    try:
        import numpy as np
    except ImportError:
        # selfcheck 自身を numpy の無い interpreter で実行された場合。
        # ピークは測れないので None を返し、呼び出し側で FAIL として報告する
        # （例外で落とさない）。
        w = wave.open(path)
        return None, w.getnframes() / w.getframerate()
    w = wave.open(path)
    n, ch, sw = w.getnframes(), w.getnchannels(), w.getsampwidth()
    raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    else:
        a = np.frombuffer(raw, dtype=np.float32)
    return (float(np.abs(a).max()) if a.size else 0.0), n / w.getframerate()


def check_fluidsynth(lock):
    name = "tool:fluidsynth"
    path = shutil.which("fluidsynth")
    if path is None:
        report(name, False, "PATH に無い", "ENV_MISSING",
               "scripts/setup_env.sh を実行する（apt で fluidsynth を導入）")
        return
    r = sh([path, "--version"], timeout=TIMEOUT_PROBE)
    if r.timed_out:
        report(name, False, f"--version が {TIMEOUT_PROBE} 秒以内に返らない",
               "TIMEOUT", "プロセスの状態を確認し、再実行は人の判断で行う")
        return
    m = re.search(r"(\d+\.\d+\.\d+)", r.stdout + r.stderr)
    if r.returncode != 0 or not m:
        report(name, False, f"--version が実働で返らない (rc={r.returncode})",
               "ENV_MISSING", "scripts/setup_env.sh を実行する")
        return
    version = m.group(1)
    # 参照先が恒久配置か（ラッパー経由でも実体が一時領域を指していないか）
    refs = [os.path.realpath(path)]
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        if head.startswith(b"#!"):  # スクリプト（ラッパー）なら参照先を読む
            text = head.decode("utf-8", "replace")
            refs += re.findall(r"/[^\s\"']+", text)
    except OSError:
        pass
    bad = [p for p in refs if any(mk in p for mk in NONPERMANENT_MARKERS)]
    if bad:
        report(name, False,
               f"バージョン {version} は返るが参照先が恒久配置でない: {bad[0]}",
               "ENV_DRIFT",
               "実体を ~/workspace/tools/ 配下かシステムへ移し setup_env.sh で固定する")
        return
    detail = f"{version} at {path}"
    if lock and lock.get("tools", {}).get("fluidsynth"):
        lv = lock["tools"]["fluidsynth"]
        if major_minor(lv) != major_minor(version):
            report(name, False,
                   f"版がロックと違う: 実測 {version} / ロック {lv}",
                   "ENV_DRIFT",
                   "版上げは golden 再承認イベント。golden_check.py を回してから "
                   "setup_env.sh でロックを更新する")
            return
        detail += " (ロック一致)"
    report(name, True, detail)


@section("tools")
def check_tools(lock):
    check_fluidsynth(lock)
    for tool in ("ffmpeg", "ffprobe"):
        path = shutil.which(tool)
        if path is None:
            report(f"tool:{tool}", False, "PATH に無い", "ENV_MISSING",
                   "scripts/setup_env.sh を実行する")
            continue
        r = sh([path, "-version"], timeout=TIMEOUT_PROBE)
        if r.timed_out:
            report(f"tool:{tool}", False, "応答が無い", "TIMEOUT",
                   "再実行は人の判断で行う")
            continue
        m = re.search(r"version (\d+\.\d+)", r.stdout)
        ver = m.group(1) if m else "?"
        ok = r.returncode == 0
        cat = None
        detail = ver
        if ok and tool == "ffmpeg" and lock and lock.get("tools", {}).get("ffmpeg"):
            lv = lock["tools"]["ffmpeg"]
            if major_minor(lv) != major_minor(ver):
                ok, cat = False, "ENV_DRIFT"
                detail = f"実測 {ver} / ロック {lv}"
        report(f"tool:{tool}", ok, detail, cat,
               "setup_env.sh でロックを更新する" if cat else "")
    ok = os.path.exists(VENV_PY) and \
        sh([VENV_PY, "-c", "import mido"], timeout=TIMEOUT_PROBE).returncode == 0
    report("venv python + mido", ok,
           "" if ok else f"{VENV_PY} が無いか mido が読めない",
           "ENV_MISSING", "scripts/setup_env.sh を実行する")
    try:
        import numpy  # noqa: F401
        report("system python3 + numpy", True)
    except ImportError:
        report("system python3 + numpy", False, "numpy が import できない",
               "ENV_MISSING", "scripts/setup_env.sh を実行する")


@section("files")
def check_files_and_syntax():
    for rel in ("SKILL.md", "references/instruments.md",
                "references/guitar.md", "assets/forge_piano.sfz",
                "scripts/generate_ir.py", "scripts/golden_check.py",
                "scripts/setup_env.sh"):
        report(f"file:{rel}", os.path.exists(os.path.join(SKILL, rel)),
               "" if os.path.exists(os.path.join(SKILL, rel)) else "ファイルが無い",
               "SKILL_BROKEN", "スキルの配布物を復元する")
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
    report("scripts py_compile", not bad, "; ".join(bad), "SKILL_BROKEN",
           "構文エラーを直す")


@section("ledger")
def check_ledger():
    ledger = open(os.path.join(SKILL, "references/instruments.md"),
                  encoding="utf-8").read()
    for name, _kind, path, _p, _m in INSTRUMENTS:
        # 台帳との対応はパス末尾の断片（ファイル名/フォルダ名）で取る。
        # 台帳の楽器名は日本語・製品名表記で selfcheck の内部キーと
        # 1対1の文字列一致はしないため、名前そのものの照合はしない。
        frag = path.split("/")[-1]
        report(f"ledger:{name}", frag in ledger,
               frag, "SKILL_BROKEN", "台帳と selfcheck の楽器集合を一致させる")


@section("renders")
def check_renders(tmp):
    for name, kind, path, pitches, mode in INSTRUMENTS:
        if not os.path.exists(path):
            report(f"render:{name}", False, f"パスが無い: {path}",
                   "ENV_MISSING",
                   "台帳の出所から公式配布物を取り直し setup_env.sh で照合する")
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
        r = sh(cmd, timeout=TIMEOUT_RENDER)
        if r.timed_out:
            report(f"render:{name}", False, f"{TIMEOUT_RENDER} 秒で終わらない",
                   "TIMEOUT", "再実行は人の判断で行う")
            continue
        if r.returncode != 0 or not os.path.exists(out):
            report(f"render:{name}", False, r.stderr.strip()[-100:],
                   "ENV_DRIFT",
                   "音源データの破損を疑い、台帳の出所から取り直す")
            continue
        peak, dur = wav_peak(out)
        if peak is None:
            report(f"render:{name}", False,
                   "selfcheck の実行 interpreter に numpy が無くピーク未計測",
                   "ENV_MISSING",
                   "numpy のある system python3 で selfcheck を実行する")
            continue
        report(f"render:{name}", peak > 0.05 and dur > 1.0,
               f"peak {peak:.2f}, {dur:.1f}s", "ENV_DRIFT",
               "音源データの破損を疑い、台帳の出所から取り直す")


@section("soundfonts")
def check_soundfonts(lock):
    lock_sf = (lock or {}).get("soundfonts", {})
    targets = list(SF2_FILES)
    forge = next((p for p in FORGE_CANDIDATES
                  if os.path.exists(p) and os.path.getsize(p) >= 30_000_000), None)
    report("sf2:forge_piano", forge is not None,
           forge or "候補の場所に無い", "ENV_MISSING",
           "台帳の手順で forge_piano.sf2 を配置する")
    if forge:
        targets.append(("forge_piano", forge, 30_000_000))
    for name, path, min_size in targets:
        if not os.path.exists(path):
            report(f"sf2:{name}", False, f"パスが無い: {path}", "ENV_MISSING",
                   "scripts/setup_env.sh を実行する（再導入コマンドは台帳参照）")
            continue
        with open(path, "rb") as f:
            head = f.read(12)
        if head[0:4] != b"RIFF" or head[8:12] != b"sfbk":
            report(f"sf2:{name}", False, "RIFF/sfbk ヘッダが無い（別物か破損）",
                   "ENV_DRIFT", "台帳の出所から取り直す")
            continue
        size = os.path.getsize(path)
        if size < min_size:
            report(f"sf2:{name}", False, f"サイズ不足 {size} < {min_size}",
                   "ENV_DRIFT", "台帳の出所から取り直す")
            continue
        digest = sha256_of(path)
        expected = lock_sf.get(path)
        if expected and expected != digest:
            report(f"sf2:{name}", False,
                   f"SHA-256 がロックと違う: {digest[:12]}… != {expected[:12]}…",
                   "ENV_DRIFT",
                   "差し替えが意図したものなら setup_env.sh でロックを更新する")
            continue
        report(f"sf2:{name}", True,
               f"{path} (sha256 {digest[:12]}…)")


@section("ir")
def check_ir():
    for kind in IR_KINDS:
        p = os.path.join(IR_DIR, f"{kind}.wav")
        report(f"ir:{kind}", os.path.exists(p),
               "" if os.path.exists(p) else f"パスが無い: {p}",
               "ENV_MISSING", "python3 scripts/generate_ir.py を実行する")


@section("guitar_fx")
def check_guitar_fx():
    sys.path.insert(0, HERE)
    import guitar_fx
    report("guitar_fx presets", EXPECTED_PRESETS <= set(guitar_fx.PRESETS),
           f"{len(guitar_fx.PRESETS)} presets", "SKILL_BROKEN",
           "不足プリセットを guitar_fx.py に戻す")
    report("guitar_fx addons", EXPECTED_ADDONS <= set(guitar_fx.ADDONS),
           f"{len(guitar_fx.ADDONS)} addons", "SKILL_BROKEN",
           "不足アドオンを guitar_fx.py に戻す")
    report("guitar_fx cabs", EXPECTED_CABS <= set(guitar_fx.CABS),
           ", ".join(sorted(guitar_fx.CABS)), "SKILL_BROKEN",
           "キャビ3バリエーションを guitar_fx.py に戻す")


def main():
    t_all = time.monotonic()
    tmp = tempfile.mkdtemp(prefix="bgm_sound_selfcheck_")
    lock = load_lock()
    report("env:lock", lock is not None,
           LOCK_PATH if lock else "environment.lock.json が無い",
           "ENV_MISSING", "scripts/setup_env.sh を実行して生成する")
    check_tools(lock)
    check_files_and_syntax()
    check_ledger()
    check_renders(tmp)
    check_soundfonts(lock)
    check_ir()
    check_guitar_fx()

    counts = {}
    for _n, ok, cat in RESULTS:
        if not ok:
            counts[cat] = counts.get(cat, 0) + 1
    n_pass = sum(1 for _n, ok, _c in RESULTS if ok)
    print(f"\n{n_pass}/{len(RESULTS)} PASS")
    if counts:
        print("FAIL 内訳: " + ", ".join(f"{k} {v} 件" for k, v in
                                       sorted(counts.items())))
    else:
        print("FAIL 0 件")
    print("所要時間: " + ", ".join(f"{t} {s:.1f}s" for t, s in SECTION_TIMES)
          + f" / 合計 {time.monotonic() - t_all:.1f}s")
    if "SKILL_BROKEN" in counts:
        return 1
    if "ENV_MISSING" in counts:
        return 2
    if "ENV_DRIFT" in counts:
        return 3
    if "TIMEOUT" in counts:
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
