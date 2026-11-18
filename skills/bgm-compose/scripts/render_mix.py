#!/usr/bin/env python3
"""Render a composed MIDI with built instruments + GM side by side
(procedure 6), normalise, and export OGG.

Routing is by MIDI channel: channels listed in --forge-channels are
rendered with the built instrument SF2, all other channels with the GM
soundfont. The two renders are summed, loudness-normalised to
--target-lufs / --tp (measured gain + peak limiter, iterated), and
encoded to OGG (Vorbis).

Options:
  --loop-xfade SECONDS  Make a seamless loop: crossfade the last
                        SECONDS into the head and trim them, so the
                        exported file loops without a seam.

Failure guards: before rendering, every soundfont path (built
instrument and GM) must exist and carry a valid SF2 (RIFF/sfbk)
header, and the input MIDI must exist; after rendering,
any part whose MIDI contains notes must peak above -60 dBFS. A
missing soundfont otherwise makes FluidSynth exit 0 while rendering
only the noise floor, which peak normalisation would then amplify
into a full-scale "song". Either guard failing aborts the export
with an error naming what is missing or silent; nothing is written
as a success in that case.

Usage:
  python3 render_mix.py song.mid song.ogg \
      --forge-sf2 ../instruments/forge_piano.sf2 --forge-channels 0
"""
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, "/home/hatch/workspace/midi-bgm/venv/lib/python3.12/site-packages")
import mido  # noqa: E402

GM_SF2 = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
RATE = 48000
# A part that contains notes but peaks below this is not music: in the
# missing-soundfont failure the rendered "audio" was the -90 dB noise
# floor. -60 dBFS leaves ample room for genuinely quiet parts while
# staying far above that floor.
PEAK_FLOOR_DBFS = -60.0


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


def count_notes(mid_path):
    mid = mido.MidiFile(mid_path)
    return sum(1 for track in mid.tracks for msg in track
               if msg.type == "note_on" and msg.velocity > 0)


def peak_dbfs(wav_path):
    _, x = load_wav(wav_path)
    if x.size == 0:
        return -120.0
    peak = float(np.max(np.abs(x)))
    return 20.0 * np.log10(peak) if peak > 0 else -120.0


def check_audible(wav_path, label, sf2, notes):
    db = peak_dbfs(wav_path)
    if db < PEAK_FLOOR_DBFS:
        raise RuntimeError(
            f"{label} rendered silent: peak {db:.1f} dBFS although its MIDI "
            f"contains {notes} notes. Soundfont {sf2} produced no audio "
            f"(missing, corrupt, or not loadable). Refusing to export: "
            f"peak normalisation would amplify this noise floor into a "
            f"full-scale file.")
    print(f"{label}: peak {db:.1f} dBFS ({notes} notes, soundfont {sf2})")


def split_midi(mid_path, forge_channels, out_forge, out_gm):
    mid = mido.MidiFile(mid_path)
    fmid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
    gmid = mido.MidiFile(ticks_per_beat=mid.ticks_per_beat)
    for track in mid.tracks:
        ft, gt = mido.MidiTrack(), mido.MidiTrack()
        fmid.tracks.append(ft)
        gmid.tracks.append(gt)
        for msg in track:
            if msg.is_meta:
                ft.append(msg.copy())
                gt.append(msg.copy())
            elif hasattr(msg, "channel"):
                (ft if msg.channel in forge_channels else gt).append(msg.copy())
            else:
                gt.append(msg.copy())
    fmid.save(out_forge)
    gmid.save(out_gm)


def render(sf2, mid, out):
    require_soundfont(sf2, "soundfont")
    require_file(mid, "part MIDI")
    r = subprocess.run(["fluidsynth", "-ni", "-F", str(out), "-r", str(RATE),
                        "-O", "s16", str(sf2), str(mid)],
                       capture_output=True, text=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"fluidsynth failed: {r.stderr[-400:]}")
    require_file(out, "rendered WAV")


def load_wav(path):
    with wave.open(str(path), "rb") as w:
        rate, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    return rate, np.frombuffer(raw, dtype=np.int16).astype(np.float64).reshape(-1, ch) / 32768.0


def write_wav(path, x, rate):
    # Peak-normalise to -3 dBFS (up or down) so the loudness stage
    # works in a sane gain range.
    peak = np.max(np.abs(x))
    if peak > 0:
        x = x * (0.7 / peak)
    data = np.clip(np.round(x * 32767), -32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.tobytes())


def loudnorm_measure(path):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn",
                        "-af", "loudnorm=print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900)
    m = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", r.stderr)
    if not m:
        raise RuntimeError("loudnorm measurement failed")
    return json.loads(m[-1])


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


def main(mid_path, ogg_path, forge_sf2, forge_channels, target_lufs, tp, loop_xfade):
    if shutil.which("fluidsynth") is None:
        raise RuntimeError("fluidsynth not found on PATH; cannot render")
    require_file(mid_path, "input MIDI")
    require_soundfont(forge_sf2, "built-instrument soundfont (--forge-sf2)")
    require_soundfont(GM_SF2, "GM soundfont")
    work = Path(ogg_path).parent / (Path(ogg_path).stem + "_render")
    work.mkdir(parents=True, exist_ok=True)
    fmid, gmid = work / "forge_part.mid", work / "gm_part.mid"
    split_midi(mid_path, forge_channels, fmid, gmid)
    fnotes, gnotes = count_notes(fmid), count_notes(gmid)
    fwav, gwav = work / "forge_part.wav", work / "gm_part.wav"
    render(forge_sf2, fmid, fwav)
    render(GM_SF2, gmid, gwav)
    if fnotes > 0:
        check_audible(fwav, "built-instrument part", forge_sf2, fnotes)
    if gnotes > 0:
        check_audible(gwav, "GM part", GM_SF2, gnotes)
    _, a = load_wav(fwav)
    _, b = load_wav(gwav)
    n = max(len(a), len(b))
    a = np.pad(a, ((0, n - len(a)), (0, 0)))
    b = np.pad(b, ((0, n - len(b)), (0, 0)))
    x = a + b
    mix_peak = float(np.max(np.abs(x))) if x.size else 0.0
    mix_db = 20.0 * np.log10(mix_peak) if mix_peak > 0 else -120.0
    if mix_db < PEAK_FLOOR_DBFS:
        raise RuntimeError(
            f"mixed audio is silent: peak {mix_db:.1f} dBFS. Refusing to "
            f"peak-normalise what may be only the noise floor into a "
            f"full-scale export.")
    if loop_xfade > 0:
        nf = int(RATE * loop_xfade)
        keep = x[:-nf].copy()
        t = np.linspace(0.0, 1.0, nf)[:, None]
        keep[:nf] = x[:nf] * t + x[-nf:] * (1 - t)
        x = keep
        print(f"loop crossfade applied: {loop_xfade:.1f}s, duration now {len(x) / RATE:.1f}s")
    mix = work / "mix.wav"
    write_wav(mix, x, RATE)
    export_ogg(mix, ogg_path, target_lufs, tp)
    print(f"wrote {ogg_path} (work dir: {work})")


if __name__ == "__main__":
    argv = sys.argv[1:]
    pos = [a for a in argv if not a.startswith("--")]
    def opt(name, default):
        return argv[argv.index(name) + 1] if name in argv else default
    main(pos[0], pos[1],
         opt("--forge-sf2", "../instruments/forge_piano.sf2"),
         {int(v) for v in opt("--forge-channels", "0").split(",")},
         float(opt("--target-lufs", "-14")),
         float(opt("--tp", "-1")),
         float(opt("--loop-xfade", "0")))
