#!/usr/bin/env python3
"""bgm-compose スキルの横断点検（作曲側）。

環境の移設・復旧直後や、スキルを編集した後に実行する。
作曲に使う venv の有無、作曲側のファイルと全スクリプトの構文、
作曲スモーク（実例2曲の生成＋記譜確認）、主題選別の校正記録の
読み取りを一括で確認する。音源・レンダリング側の点検は
bgm-sound の selfcheck.py が担う。

使い方: python3 scripts/selfcheck.py
終了コード: 全て PASS なら 0、一つでも FAIL があれば 1。
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
VENV_PY = os.path.expanduser("~/workspace/midi-bgm/venv/bin/python")

RESULTS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    tmp = tempfile.mkdtemp(prefix="bgm_compose_selfcheck_")
    # 1. 道具（作曲と記譜確認は mido の入った venv で行う）
    ok = os.path.exists(VENV_PY) and sh([VENV_PY, "-c", "import mido"]).returncode == 0
    report("venv python + mido", ok)

    # 2. スキルのファイルとスクリプト構文
    for rel in ("SKILL.md", "references/patterns.md", "references/melody.md",
                "references/voice.md", "references/form.md", "references/scene.md",
                "examples/spec_spring_noon.py", "examples/spec_loop_lake.py"):
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

    # 3. 作曲スモーク（両実例＋記譜確認）
    for ex in ("spec_spring_noon", "spec_loop_lake"):
        spec = os.path.join(SKILL, "examples", f"{ex}.py")
        mid = os.path.join(tmp, f"{ex}.mid")
        r = sh([VENV_PY, os.path.join(HERE, "compose.py"), spec, mid])
        ok = r.returncode == 0 and os.path.exists(mid)
        if ok:
            r2 = sh([VENV_PY, os.path.join(HERE, "check_midi.py"), spec, mid])
            ok = '"verdict": "PASS"' in r2.stdout
        report(f"compose:{ex}", ok)

    # 4. 主題選別: 既定パスで校正記録が読まれ、権限セットが一致するか
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
