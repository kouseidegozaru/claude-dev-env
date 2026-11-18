# 作曲仕様書の例（ループ型）: 湖畔の循環ループ
# 16小節をそのまま繰り返す設計。頭と尾が同じ進行・同じエネルギーで接続する。
# G major, 88 BPM, 4/4。ピアノ主旋律 + ギターのアルペジオ + ベース + 弦パッド。
# render.py --loop で末尾2秒クロスフェード版を作り、継ぎ目を計測する。

q = 1.0
h = 2.0
w = 4.0

SPEC = {
    "title": "Lakeside Loop",
    "seed": 7,
    "bpm": 88,
    "beats_per_bar": 4,
    "bars": 16,
    "loop": True,
    "sections": [("loop", 1, 16)],
    "progression": ["G", "C", "Em", "D"] * 3 + ["G", "C", "D", "G"],
    "chords": {
        "G":  {"arp": [55, 59, 62, 67], "bass_root": 43, "bass_fifth": 50, "pad": [71, 74, 79]},
        "C":  {"arp": [55, 60, 64, 67], "bass_root": 48, "bass_fifth": 55, "pad": [72, 76, 79]},
        "Em": {"arp": [52, 59, 64, 67], "bass_root": 40, "bass_fifth": 47, "pad": [71, 76, 79]},
        "D":  {"arp": [54, 57, 62, 66], "bass_root": 50, "bass_fifth": 57, "pad": [69, 74, 78]},
    },
    "tracks": {
        "melody": {"name": "Piano", "channel": 0, "program": 0},
        "accomp": {"name": "Guitar", "channel": 1, "program": 24},
        "bass":   {"name": "Bass", "channel": 2, "program": 32},
        "pad":    {"name": "Strings", "channel": 3, "program": 48},
    },
    "accomp": {"style": "arpeggio", "pattern": [0, 1, 2, 3, 2, 1, 2, 3],
               "note_len": 0.55, "vel": 58},
    "bass": {"style_by_section": {"default": "root_fifth"}, "skip_last_bar": False, "vel": 62},
    "pad": {"vel": 30},
    "melody_cfg": {"vel": 88, "dur_scale": 0.95},
    "melody": {
        1:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        2:  [(69, 0, q), (71, 1, q), (72, 2, h)],
        3:  [(71, 0, q), (67, 1, q), (64, 2, h)],
        4:  [(69, 0, q), (71, 1, q), (74, 2, h)],
        5:  [(76, 0, q), (74, 1, q), (71, 2, h)],
        6:  [(72, 0, q), (74, 1, q), (76, 2, h)],
        7:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        8:  [(69, 0, q), (71, 1, q), (69, 2, h)],
        9:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        10: [(69, 0, q), (71, 1, q), (72, 2, h)],
        11: [(76, 0, q), (74, 1, q), (71, 2, q), (72, 3, q)],
        12: [(74, 0, q), (71, 1, q), (69, 2, h)],
        13: [(67, 0, q), (69, 1, q), (71, 2, h)],
        14: [(72, 0, q), (71, 1, q), (69, 2, h)],
        15: [(71, 0, q), (69, 1, q), (67, 2, q), (66, 3, q)],
        16: [(67, 0, w)],
    },
    "humanize": {"vel_jitter": 4, "time_jitter_ticks": 0},
    "gate": {"lufs_target": -14.0, "lufs_tol": 0.5, "tp_max": -1.0,
             "low_tol_pt": 15.0, "centroid_ratio": [0.5, 2.0], "duration_tol_s": 4.0},
}
