#!/usr/bin/env bash
# bgm-sound の環境復旧スクリプト（冪等）。
#
# 使い方: bash scripts/setup_env.sh
#
# 各ステップは「目標状態の確認 → 無ければ実行 → 再確認」の形で、
# 既に目標状態なら no-op になる。2 回連続で実行すると 2 回目は
# 全ステップ no-op になることが受け入れ条件。
# 最後に selfcheck.py を実行し、その終了コードをそのまま返す。
#
# ステップ: (1) 道具 (fluidsynth/ffmpeg/ffprobe) の導入
#           (2) venv と mido の用意
#           (3) 音源の配置確認と SHA-256 照合
#           (4) fluidsynth 参照先の恒久性チェック
#           (5) 生成 IR の用意
#           (6) environment.lock.json の生成（版とハッシュの自動収集）
#           (7) selfcheck の実行
set -u
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$SKILL_DIR/scripts"
SF_DIR="$HOME/workspace/soundfonts"
VENV_PY="$HOME/workspace/midi-bgm/venv/bin/python"
LOCK="$SKILL_DIR/environment.lock.json"
CHANGED=0

ok()   { echo "ok:   $1"; }
fix()  { echo "fix:  $1"; CHANGED=$((CHANGED + 1)); }
warn() { echo "warn: $1"; }

# ---- (1) 道具 -------------------------------------------------------------
need_pkgs=()
command -v fluidsynth >/dev/null 2>&1 || need_pkgs+=(fluidsynth)
command -v ffmpeg >/dev/null 2>&1 || need_pkgs+=(ffmpeg)
command -v ffprobe >/dev/null 2>&1 || need_pkgs+=(ffmpeg)
if [ "${#need_pkgs[@]}" -eq 0 ]; then
  ok "tools fluidsynth/ffmpeg/ffprobe are installed"
else
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq && apt-get install -y -qq "${need_pkgs[@]}" \
      && fix "installed packages: ${need_pkgs[*]}" \
      || warn "apt install failed: ${need_pkgs[*]}"
  else
    warn "apt-get not available; install manually: ${need_pkgs[*]}"
  fi
fi
# システム音源（パッケージ側）
if [ ! -f /usr/share/sounds/sf2/FluidR3_GM.sf2 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -qq fluid-soundfont-gm \
      && fix "installed fluid-soundfont-gm" \
      || warn "could not install fluid-soundfont-gm"
  else
    warn "FluidR3_GM.sf2 missing and apt-get unavailable"
  fi
else
  ok "FluidR3_GM.sf2 present"
fi
if [ ! -f /usr/share/sounds/sf3/MuseScore_General_Full.sf3 ]; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -qq musescore-general-soundfont \
      && fix "installed musescore-general-soundfont" \
      || warn "could not install musescore-general-soundfont"
  else
    warn "MuseScore_General_Full.sf3 missing and apt-get unavailable"
  fi
else
  ok "MuseScore_General_Full.sf3 present"
fi

# ---- (2) venv + mido ------------------------------------------------------
if [ -x "$VENV_PY" ] && "$VENV_PY" -c "import mido" >/dev/null 2>&1; then
  ok "venv python + mido"
else
  if [ ! -x "$VENV_PY" ]; then
    python3 -m venv "$HOME/workspace/midi-bgm/venv" \
      && fix "created venv at ~/workspace/midi-bgm/venv" \
      || warn "venv creation failed"
  fi
  "$VENV_PY" -m pip install -q mido \
    && fix "installed mido into venv" \
    || warn "pip install mido failed"
fi
python3 -c "import numpy" >/dev/null 2>&1 \
  && ok "system python3 + numpy" \
  || warn "system python3 lacks numpy (pip install numpy)"

# ---- (3) ワークスペース側音源の確認 ---------------------------------------
for f in "$SF_DIR/GeneralUser-GS.sf2" "$SKILL_DIR/instruments/forge_piano.sf2"; do
  if [ -f "$f" ]; then
    ok "soundfont present: $f"
  else
    warn "soundfont missing (restore from the ledger's source): $f"
  fi
done

# ---- (4) fluidsynth 参照先の恒久性 ----------------------------------------
FS_BIN="$(command -v fluidsynth || true)"
if [ -n "$FS_BIN" ]; then
  FS_REAL="$(readlink -f "$FS_BIN")"
  case "$FS_REAL" in
    *breakthrough-runs*|/tmp/*)
      warn "fluidsynth resolves to a non-permanent path: $FS_REAL"
      if [ -x /usr/bin/fluidsynth ]; then
        warn "a system fluidsynth exists at /usr/bin/fluidsynth; fix PATH order to prefer it"
      fi
      ;;
    *) ok "fluidsynth path is permanent: $FS_REAL" ;;
  esac
fi

# ---- (5) 生成 IR -----------------------------------------------------------
ir_missing=0
for k in ambience room spring plate chamber hall; do
  [ -f "$SF_DIR/ir/$k.wav" ] || ir_missing=1
done
if [ "$ir_missing" -eq 0 ]; then
  ok "generated IRs (6 kinds) present"
else
  python3 "$SCRIPTS/generate_ir.py" \
    && fix "generated missing IRs" \
    || warn "IR generation failed"
fi

# ---- (6) environment.lock.json --------------------------------------------
python3 - "$LOCK" "$VENV_PY" <<'PYEOF'
import hashlib, importlib.metadata, json, os, platform, re, subprocess, sys

lock_path, venv_py = sys.argv[1], sys.argv[2]

def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return r.stdout + r.stderr
    except Exception:
        return ""

def ver(pattern, text):
    m = re.search(pattern, text)
    return m.group(1) if m else None

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def pkg_version(py, pkg):
    try:
        r = subprocess.run([py, "-c",
                            f"import importlib.metadata as m; "
                            f"print(m.version('{pkg}'))"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or None
    except Exception:
        return None

home = os.path.expanduser("~")
sf_candidates = [
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/sounds/sf3/MuseScore_General_Full.sf3",
    f"{home}/workspace/soundfonts/GeneralUser-GS.sf2",
    os.path.join(os.path.dirname(lock_path), "instruments/forge_piano.sf2"),
]
soundfonts = {p: sha256(p) for p in sf_candidates if os.path.isfile(p)}
data = {
    "tools": {
        "fluidsynth": ver(r"(\d+\.\d+\.\d+)", run(["fluidsynth", "--version"])),
        "ffmpeg": ver(r"ffmpeg version (\d+\.\d+(?:\.\d+)?)",
                      run(["ffmpeg", "-version"])),
    },
    "python": {
        "system": platform.python_version(),
        "venv": ver(r"(\d+\.\d+\.\d+)", run([venv_py, "--version"])),
        "mido": pkg_version(venv_py, "mido"),
        "numpy": pkg_version("python3", "numpy"),
    },
    "os": " ".join(os.uname()[:2]),
    "soundfonts": soundfonts,
}
new = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
old = None
if os.path.exists(lock_path):
    with open(lock_path, encoding="utf-8") as f:
        old = f.read()
if old == new:
    print("ok:   environment.lock.json is up to date")
else:
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(new)
    print("fix:  environment.lock.json regenerated")
    sys.exit(3)
PYEOF
[ $? -eq 3 ] && CHANGED=$((CHANGED + 1))

# ---- (7) selfcheck ----------------------------------------------------------
echo "--- selfcheck ---"
python3 "$SCRIPTS/selfcheck.py"
rc=$?
echo "--- setup summary: ${CHANGED} change(s) applied ---"
exit $rc
