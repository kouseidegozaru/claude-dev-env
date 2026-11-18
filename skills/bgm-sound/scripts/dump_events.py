# venv python (mido) で実行: ステム MIDI → ノートイベント JSON
import json
import sys

from mido import MidiFile, tick2second

mid_path, out_path = sys.argv[1], sys.argv[2]
mid = MidiFile(mid_path)
events = []
for tr in mid.tracks:
    tick = 0
    on = {}
    for m in tr:
        tick += m.time
        if m.type == "note_on" and m.velocity > 0:
            on.setdefault((m.channel, m.note), []).append((tick, m.velocity))
        elif m.type in ("note_off", "note_on"):
            key = (m.channel, m.note)
            if key in on and on[key]:
                t0, vel = on[key].pop(0)
                events.append({
                    "start": tick2second(t0, mid.ticks_per_beat, 300000),
                    "dur": tick2second(tick - t0, mid.ticks_per_beat, 300000),
                    "pitch": m.note, "vel": vel,
                })
# テンポは compose.py の既定 (200 BPM = 300000 us) で固定のはずだが、
# set_tempo があればそちらを優先する
tempo = 300000
for tr in mid.tracks:
    for m in tr:
        if m.type == "set_tempo":
            tempo = m.tempo
if tempo != 300000:
    events = []
    for tr in mid.tracks:
        tick = 0
        on = {}
        for m in tr:
            tick += m.time
            if m.type == "note_on" and m.velocity > 0:
                on.setdefault((m.channel, m.note), []).append((tick, m.velocity))
            elif m.type in ("note_off", "note_on"):
                key = (m.channel, m.note)
                if key in on and on[key]:
                    t0, vel = on[key].pop(0)
                    events.append({
                        "start": tick2second(t0, mid.ticks_per_beat, tempo),
                        "dur": tick2second(tick - t0, mid.ticks_per_beat, tempo),
                        "pitch": m.note, "vel": vel,
                    })
events.sort(key=lambda e: e["start"])
with open(out_path, "w") as f:
    json.dump(events, f)
print(f"{len(events)} events -> {out_path}")
