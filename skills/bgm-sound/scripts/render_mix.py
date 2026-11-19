#!/usr/bin/env python3
"""Stem-mix renderer, stage A: split a composed MIDI into per-channel
stems, render each stem, then trim / pan / fader-balance and merge.

Pipeline (stage A only; EQ / compression / sidechain / width processing
are later stages and are intentionally not implemented here):
  split   the input MIDI per channel (role names come from --spec
          tracks, --roles, or the forge/GM routing as a fallback)
  render  each stem with FluidSynth at a fixed gain (-g 0.8, 44.1 kHz);
          a stem that would clip is re-rendered at half gain and the
          adjustment is recorded (this is a render-gain correction,
          not mix peak normalisation)
  measure peak / RMS / solo loudness per stem
  trim    one static gain per stem towards RMS -20 dBFS
          (clamp +/-12 dB; if the peak would exceed -6 dBFS, a
          peak-based gain towards -8 dBFS takes priority)
  pan     equal-power coefficients by role (melody/bass/drums/pad
          centre, accompaniment -15 deg with its double at +15 deg,
          decoration +20 deg); content at 150 Hz and below is always
          kept centred via a crossover split
  merge   ffmpeg amix with normalize=0, dropout_transition=0 and the
          fader values as weights. Peak normalisation of the mix is
          NOT done anywhere: it would destroy the balance just built.
  export  loudness-normalised OGG (measured gain + limiter, iterated)

Fader initial values are relative to the melody stem's solo loudness:
melody 0 / bass -4 / accomp -7 / drums -8 / pad -11 dB. A spec's
`mix` dict (role name or track name -> dB) overrides them per song.

Failure guards: every soundfont must exist and carry a valid SF2
(RIFF/sfbk) header; any stem whose MIDI contains notes but peaks
below -60 dBFS aborts the export naming the stem (a missing soundfont
otherwise makes FluidSynth exit 0 while rendering only the noise
floor). All decisions are recorded in stem_mix_report.json next to
the exported file (and in the work dir).

Usage:
  python3 render_mix.py song.mid song.ogg \
      --forge-sf2 ../instruments/forge_piano.sf2 --forge-channels 0 \
      [--spec spec.py] [--roles 0:melody,1:accomp,2:bass,3:pad]
"""
import importlib.util
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/hatch/workspace/midi-bgm/venv/lib/python3.12/site-packages")
import mido  # noqa: E402

GM_SF2_DEFAULT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
RATE = 44100  # project-wide sample rate (do not mix rates in one pipeline)
RENDER_GAIN = 0.8
PEAK_FLOOR_DBFS = -60.0
TRIM_TARGET_RMS_DBFS = -20.0
TRIM_CLAMP_DB = 12.0
TRIM_PEAK_LIMIT_DBFS = -6.0
TRIM_PEAK_TARGET_DBFS = -8.0
FADER_DEFAULTS = {"melody": 0.0, "bass": -4.0, "accomp": -7.0,
                  "drums": -8.0, "pad": -11.0, "color": -9.0}
PAN_ANGLES = {"melody": 0.0, "bass": 0.0, "drums": 0.0, "pad": 0.0,
              "color": 20.0}  # accomp handled by stem order (-15/+15)
LOW_MONO_HZ = 150


def require_file(path, role):
    p = Path(path)
    if not p.is_file() or p.stat().st_size == 0:
        raise RuntimeError(f"{role} not found or empty: {path}")
    return p


def require_soundfont(path, role):
    p = require_file(path, role)
    with open(p, "rb") as f:
        head = f.read(12)
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"sfbk":
        raise RuntimeError(
            f"{role} is not a valid SF2 soundfont (missing RIFF/sfbk "
            f"header): {path}")
    return p


def run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"command failed ({cmd[0]}): {r.stderr[-400:]}")
    return r


def decode_f32(path):
    """Decode any audio file to float32 stereo at RATE via ffmpeg."""
    r = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le",
         "-ac", "2", "-ar", str(RATE), "-"],
        capture_output=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"decode failed for {path}: "
                           f"{r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.float32).reshape(-1, 2).astype(np.float64)


def peak_dbfs(x):
    if x.size == 0:
        return -120.0
    p = float(np.max(np.abs(x)))
    return 20.0 * math.log10(p) if p > 0 else -120.0


def rms_dbfs(x):
    if x.size == 0:
        return -120.0
    v = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    return 20.0 * math.log10(v) if v > 0 else -120.0


def loudnorm_measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("loudnorm measurement failed")
    return json.loads(m[-1])


def split_midi_per_channel(mid_path):
    """Return {channel: (MidiFile, note_count)} preserving meta events."""
    mid = mido.MidiFile(mid_path)
    channels = {}
    for track in mid.tracks:
        for msg in track:
            if hasattr(msg, "channel"):
                channels.setdefault(msg.channel, 0)
                if msg.type == "note_on" and msg.velocity > 0:
                    channels[msg.channel] += 1
    out = {}
    for ch in channels:
        smid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
        for track in mid.tracks:
            st = mido.MidiTrack()
            smid.tracks.append(st)
            for msg in track:
                if msg.is_meta or getattr(msg, "channel", None) == ch:
                    st.append(msg.copy())
        out[ch] = (smid, channels[ch])
    return out


def classify_role(name):
    n = (name or "").lower()
    if "melody" in n or "lead" in n:
        return "melody"
    if "bass" in n:
        return "bass"
    if "drum" in n or "perc" in n:
        return "drums"
    if "pad" in n or "string" in n:
        return "pad"
    if any(k in n for k in ("bell", "color", "deco", "glock", "celesta")):
        return "color"
    return "accomp"


def load_spec(spec_path):
    mod_spec = importlib.util.spec_from_file_location("bgm_spec", spec_path)
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    return mod.SPEC


def resolve_roles(spec, explicit, forge_channels, channels):
    """channel -> (track_name, role)."""
    roles = {}
    if spec:
        for name, cfg in spec.get("tracks", {}).items():
            ch = cfg.get("channel")
            if ch is not None:
                roles[ch] = (name, classify_role(name))
    for ch in channels:
        if ch not in roles:
            if ch == 9:  # GM drum channel
                roles[ch] = (f"ch{ch}", "drums")
            else:
                roles[ch] = (f"ch{ch}",
                             "melody" if ch in forge_channels else "accomp")
    if explicit:
        for part in explicit.split(","):
            ch_s, role = part.split(":", 1)
            ch = int(ch_s)
            name = roles.get(ch, (f"ch{ch}", role))[0]
            roles[ch] = (name, role.strip())
    return roles


def render_stem(sf2, mid_path, out_wav):
    """Render one stem at fixed gain; halve the gain if it would clip."""
    gain = RENDER_GAIN
    for _ in range(4):
        cmd = ["fluidsynth", "-ni", "-F", str(out_wav), "-r", str(RATE),
               "-O", "s16", "-g", str(gain)]
        if REVERB_LEVEL is not None:
            cmd += ["-o", f"synth.reverb.level={REVERB_LEVEL}"]
        cmd += [str(sf2), str(mid_path)]
        run(cmd)
        require_file(out_wav, "rendered stem WAV")
        if peak_dbfs(decode_f32(out_wav)) <= -0.5:
            return gain
        gain /= 2.0
    return gain


def pan_coeffs(angle_deg):
    p = max(-90.0, min(90.0, angle_deg)) / 90.0
    phi = (p + 1.0) * math.pi / 4.0
    return math.cos(phi), math.sin(phi)


def process_stem(in_wav, out_wav, trim_db, angle_deg):
    """Apply trim gain and role pan; <=150 Hz content stays centred."""
    left, right = pan_coeffs(angle_deg)
    fc = (f"[0:a]volume={trim_db:.2f}dB,asplit=2[a][b];"
          f"[a]lowpass=f={LOW_MONO_HZ},"
          f"pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1[lo];"
          f"[b]highpass=f={LOW_MONO_HZ},"
          f"pan=stereo|c0={left:.4f}*c0|c1={right:.4f}*c1[hi];"
          f"[lo][hi]amix=inputs=2:normalize=0:duration=first[out]")
    run(["ffmpeg", "-y", "-v", "error", "-i", str(in_wav),
         "-filter_complex", fc, "-map", "[out]",
         "-c:a", "pcm_f32le", "-ar", str(RATE), str(out_wav)])
    return fc


def export_ogg(wav_path, ogg_path, target_lufs, tp):
    """Normalise with loudnorm (dynamic mode) and verify.

    Linear loudnorm cannot always reach the LUFS target: when the
    mix's crest factor (TP - LUFS) exceeds the target window, its gain
    is capped by the true-peak ceiling and the result lands quiet
    (measured: -16.4 LUFS for a -14 target). Dynamic loudnorm limits
    true peaks internally and reaches the target; it overshoots the
    TP ceiling slightly (~0.2 dB), so the filter is given a 0.5 dB
    margin and the result is re-measured. If the TP is still over,
    one corrective gain-down pass is applied.
    """
    def encode(src, dst, tp_arg, extra_af=None):
        af = f"loudnorm=I={target_lufs}:TP={tp_arg}:LRA=11"
        if extra_af:
            af = extra_af + "," + af
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(src),
                            "-af", af, "-c:a", "libvorbis", "-q:a", "6", str(dst)],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg export failed: {r.stderr[-400:]}")

    encode(wav_path, ogg_path, tp - 0.5)
    meas = loudnorm_measure(ogg_path)
    if float(meas["input_tp"]) > tp:
        down = float(meas["input_tp"]) - tp + 0.1
        tmp = Path(str(ogg_path) + ".tmp.ogg")
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(ogg_path),
                            "-af", f"volume=-{down:.2f}dB",
                            "-c:a", "libvorbis", "-q:a", "6", str(tmp)],
                           capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg correction failed: {r.stderr[-400:]}")
        tmp.replace(ogg_path)
        meas = loudnorm_measure(ogg_path)
        print(f"note: TP correction of -{down:.2f} dB applied")
    print(f"export: LUFS={float(meas['input_i']):.2f} TP={float(meas['input_tp']):.2f} "
          f"(target {target_lufs} LUFS / {tp} dBTP)")
    return {"lufs": float(meas["input_i"]), "true_peak": float(meas["input_tp"])}


def apply_loop_xfade(x, seconds):
    nf = int(RATE * seconds)
    keep = x[:-nf].copy()
    t = np.linspace(0.0, 1.0, nf)[:, None]
    keep[:nf] = x[:nf] * t + x[-nf:] * (1 - t)
    return keep


def write_f32_wav(path, x):
    raw = Path(str(path) + ".raw")
    x.astype(np.float32).tofile(raw)
    try:
        run(["ffmpeg", "-y", "-v", "error", "-f", "f32le", "-ar", str(RATE),
             "-ac", "2", "-i", str(raw), "-c:a", "pcm_f32le", str(path)])
    finally:
        raw.unlink(missing_ok=True)


def main(mid_path, ogg_path, forge_sf2, forge_channels, gm_sf2,
         spec_path, roles_opt, target_lufs, tp, loop_xfade):
    global REVERB_LEVEL
    REVERB_LEVEL = _reverb_level_opt
    if shutil.which("fluidsynth") is None:
        raise RuntimeError("fluidsynth not found on PATH; cannot render")
    require_file(mid_path, "input MIDI")
    require_soundfont(forge_sf2, "built-instrument soundfont (--forge-sf2)")
    require_soundfont(gm_sf2, "GM soundfont")
    spec = load_spec(spec_path) if spec_path else None
    mix_overrides = (spec or {}).get("mix", {}) if spec else {}

    work = Path(ogg_path).parent / (Path(ogg_path).stem + "_render")
    work.mkdir(parents=True, exist_ok=True)

    per_channel = split_midi_per_channel(mid_path)
    roles = resolve_roles(spec, roles_opt, forge_channels, per_channel.keys())

    report = {"stage": "A", "sample_rate": RATE,
              "input_midi": str(mid_path), "stems": []}
    processed = []  # (path, fader_db, stem_report)
    accomp_seen = 0
    for ch in sorted(per_channel):
        smid, notes = per_channel[ch]
        name, role = roles[ch]
        sf2 = forge_sf2 if ch in forge_channels else gm_sf2
        stem_mid = work / f"stem_ch{ch}.mid"
        smid.save(stem_mid)
        stem_wav = work / f"stem_ch{ch}.wav"
        gain = render_stem(sf2, stem_mid, stem_wav)
        x = decode_f32(stem_wav)
        pk, rm = peak_dbfs(x), rms_dbfs(x)
        if notes > 0 and pk < PEAK_FLOOR_DBFS:
            raise RuntimeError(
                f"stem ch{ch} ({name}/{role}) rendered silent: peak "
                f"{pk:.1f} dBFS although its MIDI contains {notes} notes. "
                f"Soundfont {sf2} produced no audio (missing, corrupt, or "
                f"not loadable). Refusing to export.")
        solo = loudnorm_measure(stem_wav)
        # trim: one static correction towards RMS -20 dBFS
        trim = max(-TRIM_CLAMP_DB, min(TRIM_CLAMP_DB, TRIM_TARGET_RMS_DBFS - rm))
        trim_mode = "rms"
        if pk + trim > TRIM_PEAK_LIMIT_DBFS:
            trim = TRIM_PEAK_TARGET_DBFS - pk
            trim_mode = "peak"
        # pan angle by role (accompaniment: main -15, double +15, then alternate)
        if role == "accomp":
            angle = -15.0 if accomp_seen % 2 == 0 else 15.0
            accomp_seen += 1
        else:
            angle = PAN_ANGLES.get(role, 0.0)
        left, right = pan_coeffs(angle)
        fader = float(mix_overrides.get(role, mix_overrides.get(
            name, FADER_DEFAULTS.get(role, FADER_DEFAULTS["accomp"]))))
        proc_wav = work / f"stem_ch{ch}_proc.wav"
        fc = process_stem(stem_wav, proc_wav, trim, angle)
        srep = {"channel": ch, "track": name, "role": role,
                "soundfont": str(sf2), "notes": notes,
                "render_gain": gain, "peak_dbfs": round(pk, 2),
                "rms_dbfs": round(rm, 2),
                "solo_lufs": float(solo["input_i"]),
                "trim_db": round(trim, 2), "trim_mode": trim_mode,
                "fader_db": fader, "pan_deg": angle,
                "pan_coeffs": [round(left, 4), round(right, 4)],
                "filtergraph": fc}
        report["stems"].append(srep)
        processed.append((proc_wav, fader))
        print(f"stem ch{ch} {name}/{role}: peak {pk:.1f} dBFS, "
              f"trim {trim:+.1f} dB ({trim_mode}), fader {fader:+.1f} dB, "
              f"pan {angle:+.0f} deg")

    if not processed:
        raise RuntimeError("no stems found in input MIDI")

    weights = " ".join(f"{10 ** (f / 20.0):.4f}" for _p, f in processed)
    inputs = []
    for p, _f in processed:
        inputs += ["-i", str(p)]
    merge_fc = (f"amix=inputs={len(processed)}:duration=longest:"
                f"dropout_transition=0:normalize=0:weights='{weights}'")
    premaster = work / "premaster.wav"
    run(["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", merge_fc, "-c:a", "pcm_f32le",
         "-ar", str(RATE), str(premaster)])
    pm = decode_f32(premaster)
    pm_meas = loudnorm_measure(premaster)
    report["merge"] = {"filtergraph": merge_fc, "weights": weights}
    report["pre_master"] = {"peak_dbfs": round(peak_dbfs(pm), 2),
                            "lufs": float(pm_meas["input_i"])}
    print(f"pre-master: peak {peak_dbfs(pm):.1f} dBFS, "
          f"{float(pm_meas['input_i']):.2f} LUFS")

    export_src = premaster
    if loop_xfade > 0:
        x = apply_loop_xfade(pm, loop_xfade)
        export_src = work / "premaster_loop.wav"
        write_f32_wav(export_src, x)
        report["loop_xfade_seconds"] = loop_xfade
        print(f"loop crossfade applied: {loop_xfade:.1f}s, "
              f"duration now {len(x) / RATE:.1f}s")

    report["export"] = export_ogg(export_src, ogg_path, target_lufs, tp)
    report_path = work / "stem_mix_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    shutil.copy(report_path, Path(ogg_path).with_name("stem_mix_report.json"))
    print(f"wrote {ogg_path} (work dir: {work}, report: {report_path})")


if __name__ == "__main__":
    argv = sys.argv[1:]
    pos = [a for a in argv if not a.startswith("--")]
    def opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default
    _reverb_level_opt = (float(opt("--reverb-level", ""))
                         if "--reverb-level" in argv else None)
    REVERB_LEVEL = None
    main(pos[0], pos[1],
         opt("--forge-sf2", "../instruments/forge_piano.sf2"),
         {int(v) for v in opt("--forge-channels", "0").split(",")},
         opt("--gm-sf2", GM_SF2_DEFAULT),
         opt("--spec", None),
         opt("--roles", None),
         float(opt("--target-lufs", "-14")),
         float(opt("--tp", "-1")),
         float(opt("--loop-xfade", "0")))
