#!/usr/bin/env python3
"""bgm-compose スキルの横断点検（作曲側）。

環境の移設・復旧直後や、スキルを編集した後に実行する。
作曲に使う venv の有無、作曲側のファイルと全スクリプトの構文、
作曲スモーク（実例2曲の生成＋記譜確認）、主題選別の校正記録の
読み取りに加えて、以下を確認する:
  - 決定性: 同じ spec からバイト一致の MIDI が出ること
  - golden: 固定 spec のイベント列ハッシュが golden と一致すること
    （--update-golden で再生成）
  - 変異テスト7種: わざと壊した spec を対応チェックが落とすこと
  - パッド声部検査の単体テスト
  - 陰性フィクスチャ: 「旧チェック全通過でも試聴で却下された実例」の
    spec/MIDI が置き場（環境変数 BGM_FIXTURE_DIR、既定はスキル外の
    データ置き場）にあれば、新チェックで FAIL になること。
    置き場が無い環境では SKIP として報告する（FAIL にはしない）。
音源・レンダリング側の点検は bgm-sound の selfcheck.py が担う。

使い方: python3 scripts/selfcheck.py [--update-golden]
終了コード: FAIL が一つでもあれば 1、無ければ 0（SKIP は失敗に数えない）。
"""
import copy
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
VENV_PY = os.path.expanduser("~/workspace/midi-bgm/venv/bin/python")
GOLDEN_PATH = os.path.join(HERE, "golden_events.json")
FIXTURE_DIR = os.environ.get(
    "BGM_FIXTURE_DIR", os.path.expanduser("~/workspace/midi-bgm/fixtures"))

RESULTS = []
SKIPS = []


def report(name, ok, detail=""):
    RESULTS.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail else ""))


def report_skip(name, detail=""):
    SKIPS.append(name)
    print(f"SKIP  {name}" + (f" — {detail}" if detail else ""))


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _bootstrap_venv():
    """mido の無いインタプリタで起動されたら venv の python で再実行する。"""
    try:
        import mido  # noqa: F401
        return
    except ImportError:
        pass
    if os.path.exists(VENV_PY) \
            and os.path.abspath(sys.executable) != os.path.abspath(VENV_PY):
        os.execv(VENV_PY, [VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])


def _load_core():
    sys.path.insert(0, HERE)
    import check_midi
    import compose
    return compose, check_midi


def _events_hash(compose, check_midi, spec):
    """固定 spec のイベント列（量子化済み）の決定的ハッシュ。"""
    with redirect_stdout(io.StringIO()):
        mid = compose.build(spec)
    all_events = check_midi.extract_events(mid, spec)
    melody, _n = check_midi.clean_melody_events(
        spec, all_events.get("melody", []))
    all_events = dict(all_events)
    all_events["melody"] = melody
    canon = {}
    for key in sorted(all_events):
        canon[key] = [[round(e["onset_beat"], 4), e["pitch"],
                       round(e["dur_beats"], 4), e["vel"]]
                      for e in all_events[key]]
    blob = json.dumps(canon, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _check_statuses(compose, check_midi, spec):
    with redirect_stdout(io.StringIO()):
        mid = compose.build(spec)
    out = check_midi.run_checks(spec, mid)
    return {c["check"]: c["status"] for c in out["checks"]}, out["verdict"]


def _mutation_tests(compose, check_midi, base_spec):
    """変異テスト7種: 壊し方ごとに対応チェックが WARN 以上/FAIL を出すこと。"""

    def fresh():
        return copy.deepcopy(base_spec)

    def expect(name, mut, check_name, statuses):
        spec = fresh()
        mut(spec)
        st, _v = _check_statuses(compose, check_midi, spec)
        got = st.get(check_name)
        report(f"mutation:{name}", got in statuses,
               f"{check_name}={got}（期待: {'/'.join(statuses)}）")

    def m_rhythm(spec):
        for bar, notes in list(spec["melody"].items()):
            ps = [t[0] for t in notes]
            spec["melody"][bar] = [
                (ps[0], 0, 1.0), (ps[min(1, len(ps) - 1)], 1, 1.0),
                (ps[-1], 2, 2.0)]

    def m_fragment(spec):
        scale = [67, 69, 71, 72, 74, 76, 79, 81]
        spec["melody"][5] = [(p, i, 1.0) for i, p in enumerate(scale[:4])]
        spec["melody"][6] = [(p, i, 1.0) for i, p in enumerate(scale[4:])]

    def m_b_copy(spec):
        for bar in range(5, 13):
            spec["melody"][bar + 8] = copy.deepcopy(spec["melody"][bar])
        spec["melody_cfg"]["vel_by_section"]["B"] = \
            spec["melody_cfg"].get("vel", 92)

    def m_peak(spec):
        spec["melody"][7] = list(spec["melody"][7]) + [(79, 3, 1.0)]
        spec["melody"][15] = list(spec["melody"][15]) + [(79, 3, 1.0)]
        b23 = list(spec["melody"][23])
        b23[-1] = (79, b23[-1][1], b23[-1][2])
        spec["melody"][23] = b23

    def m_uniform_end(spec):
        # フレーズ末（各セクションの2小節目）の末音だけを一律の位置・長さにする
        for _name, first, last in spec["sections"]:
            bar = first + 1
            while bar <= last:
                if bar in spec["melody"]:
                    notes = list(spec["melody"][bar])
                    last_note = notes[-1]
                    notes[-1] = (last_note[0], 2, 2.0)
                    spec["melody"][bar] = notes
                bar += 2

    def m_outlier(spec):
        b14 = list(spec["melody"][14])
        b14[0] = (70, b14[0][1], b14[0][2])  # 音階外音（後続は近接回収にならない）
        spec["melody"][14] = b14

    def m_stepwise(spec):
        for bar in range(5, 13):
            spec["melody"][bar] = [(71, 0, 1.0), (72, 1, 1.0),
                                   (74, 2, 1.0), (76, 3, 1.0)]

    expect("rhythm_flatten", m_rhythm, "rhythm_pattern", ("FAIL",))
    expect("scale_fragment", m_fragment, "scale_fragment", ("FAIL",))
    spec = fresh()
    m_b_copy(spec)
    st, _v = _check_statuses(compose, check_midi, spec)
    report("mutation:b_as_reprise",
           st.get("b_reprise") == "FAIL" or st.get("contrast_axes") == "FAIL",
           f"b_reprise={st.get('b_reprise')}, contrast_axes={st.get('contrast_axes')}")
    expect("peak_spread", m_peak, "peak_note", ("WARN", "FAIL"))
    expect("uniform_phrase_end", m_uniform_end, "phrase_endings",
           ("WARN", "FAIL"))
    expect("outlier_unrecovered", m_outlier, "outlier_recovery",
           ("WARN", "FAIL"))
    spec = fresh()
    m_stepwise(spec)
    st, _v = _check_statuses(compose, check_midi, spec)
    report("mutation:leap_to_stepwise",
           st.get("stepwise_rate") in ("WARN", "FAIL")
           or st.get("scale_fragment") == "FAIL",
           f"stepwise_rate={st.get('stepwise_rate')}, "
           f"scale_fragment={st.get('scale_fragment')}")


def main():
    _bootstrap_venv()
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

    # 5. 決定性・golden・変異テスト・パッド検査（ライブラリとして直接使う）
    compose, check_midi = _load_core()
    spring_path = os.path.join(SKILL, "examples", "spec_spring_noon.py")
    loop_path = os.path.join(SKILL, "examples", "spec_loop_lake.py")
    spring = compose.load_spec(spring_path)
    loop = compose.load_spec(loop_path)

    with redirect_stdout(io.StringIO()):
        m1 = compose.build(copy.deepcopy(spring))
        m2 = compose.build(copy.deepcopy(spring))
    p1, p2 = os.path.join(tmp, "det1.mid"), os.path.join(tmp, "det2.mid")
    m1.save(p1)
    m2.save(p2)
    with open(p1, "rb") as f:
        b1 = f.read()
    with open(p2, "rb") as f:
        b2 = f.read()
    report("determinism:spec_spring_noon", b1 == b2,
           f"{len(b1)} bytes x2")

    update_golden = "--update-golden" in sys.argv \
        or os.environ.get("BGM_UPDATE_GOLDEN")
    hashes = {"spec_spring_noon": _events_hash(compose, check_midi, spring),
              "spec_loop_lake": _events_hash(compose, check_midi, loop)}
    if update_golden:
        with open(GOLDEN_PATH, "w", encoding="utf-8") as f:
            json.dump({"note": "固定 spec のイベント列ハッシュ。"
                               "spec か生成器を変えたら --update-golden で再生成する。",
                       **hashes}, f, ensure_ascii=False, indent=2)
        for name, h in hashes.items():
            report(f"golden:{name}", True, f"updated {h[:12]}")
    else:
        golden = {}
        if os.path.exists(GOLDEN_PATH):
            with open(GOLDEN_PATH, encoding="utf-8") as f:
                golden = json.load(f)
        for name, h in hashes.items():
            report(f"golden:{name}", golden.get(name) == h,
                   f"current {h[:12]} vs golden "
                   f"{str(golden.get(name))[:12]}")

    _mutation_tests(compose, check_midi, spring)

    viol = compose.inspect_pad_voice_leading(
        [(64, 67, 71), (65, 69, 72)], [48, 50])
    viol_cross = compose.inspect_pad_voice_leading(
        [(71, 67, 64)], [43])
    report("pad_voice_leading_inspector",
           any("並行" in msg for _b, msg in viol) and bool(viol_cross),
           f"parallel={viol}, crossing={viol_cross}")

    # 6. 陰性フィクスチャ（却下実例が新チェックで FAIL になること）
    fixtures = []
    if os.path.isdir(FIXTURE_DIR):
        fixtures = sorted(
            d for d in os.listdir(FIXTURE_DIR)
            if d.startswith("rejected_")
            and os.path.exists(os.path.join(FIXTURE_DIR, d, "spec.py"))
            and os.path.exists(os.path.join(FIXTURE_DIR, d, "song.mid")))
    if not fixtures:
        report_skip("negative_fixtures",
                    f"{FIXTURE_DIR} に rejected_* フィクスチャが無い")
    for name in fixtures:
        d = os.path.join(FIXTURE_DIR, name)
        r = sh([VENV_PY, os.path.join(HERE, "check_midi.py"),
                os.path.join(d, "spec.py"), os.path.join(d, "song.mid")])
        try:
            verdict = json.loads(r.stdout).get("verdict")
        except Exception:  # noqa: BLE001
            verdict = None
        report(f"fixture:{name}", verdict == "FAIL",
               f"verdict={verdict}")

    n_fail = sum(1 for _n, ok in RESULTS if not ok)
    n_pass = len(RESULTS) - n_fail
    print(f"\n{n_pass}/{len(RESULTS)} PASS"
          + (f", {n_fail} FAIL" if n_fail else "")
          + (f", {len(SKIPS)} SKIP" if SKIPS else ""))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
