# 作曲仕様書の例（曲型）: 春の昼の湖畔
# ピアノ主旋律 + ナイロンギターのアルペジオ + アコースティックベース + 弦パッド（展開と再現のみ）
# G major, 96 BPM, 4/4, 32小節: イントロ4 - 主題A8 - 展開B8 - 主題A'8 - アウトロ4

q = 1.0
h = 2.0
w = 4.0

SPEC = {
    "title": "Spring Noon Lakeside",
    "seed": 42,
    "bpm": 96,
    "beats_per_bar": 4,
    "bars": 32,
    "tempo_changes": [(31, 88), (32, 76)],  # 終盤のリタルダンド
    "sections": [
        ("intro", 1, 4), ("A", 5, 12), ("B", 13, 20), ("A'", 21, 28), ("outro", 29, 32),
    ],
    "theme_check": ("A", "A'"),  # check_midi.py が主題の戻りを照合する組
    "progression": (
        ["G", "C", "G", "D"]
        + ["G", "C", "Em", "D", "G", "C", "Am", "D"]
        + ["Em", "C", "G", "D", "Am", "Em", "C", "D"]
        + ["G", "C", "Em", "D", "G", "C", "G", "G"]
        + ["C", "G", "C", "G"]
    ),
    "chords": {
        "G":  {"arp": [55, 59, 62, 67], "bass_root": 43, "bass_fifth": 50, "pad": [71, 74, 79]},
        "C":  {"arp": [55, 60, 64, 67], "bass_root": 48, "bass_fifth": 55, "pad": [72, 76, 79]},
        "Em": {"arp": [52, 59, 64, 67], "bass_root": 40, "bass_fifth": 47, "pad": [71, 76, 79]},
        "D":  {"arp": [54, 57, 62, 66], "bass_root": 50, "bass_fifth": 57, "pad": [69, 74, 78]},
        "Am": {"arp": [52, 57, 60, 64], "bass_root": 45, "bass_fifth": 52, "pad": [69, 72, 76]},
    },
    "tracks": {
        "melody": {"name": "Piano", "channel": 0, "program": 0},    # GM 0 Acoustic Grand
        "accomp": {"name": "Guitar", "channel": 1, "program": 24},  # GM 24 Nylon Guitar
        "bass":   {"name": "Bass", "channel": 2, "program": 32},    # GM 32 Acoustic Bass
        "pad":    {"name": "Strings", "channel": 3, "program": 48}, # GM 48 String Ensemble
    },
    "accomp": {
        "style": "arpeggio",
        "pattern": [0, 1, 2, 3, 2, 1, 2, 3],
        "note_len": 0.55,
        "vel": 62,
        "vel_by_section": {"intro": 52, "outro": 52},
        "vel_by_bar": {31: 52, 32: 58},
        "final_bar_strum": True,
    },
    "bass": {
        "style_by_section": {"default": "whole", "B": "root_fifth", "A'": "root_fifth"},
        "skip_last_bar": True,
        "vel": 64,
    },
    "pad": {"sections": ["B", "A'"], "skip_last_bar": True, "vel": 34},
    "melody_cfg": {"vel": 92, "vel_by_section": {"intro": 78, "outro": 78}, "dur_scale": 0.95},
    "melody": {
        3:  [(71, 0, h)],
        4:  [(69, 0, h)],
        5:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        6:  [(69, 0, q), (71, 1, q), (72, 2, h)],
        7:  [(71, 0, q), (67, 1, q), (64, 2, h)],
        8:  [(69, 0, q), (71, 1, q), (74, 2, h)],
        9:  [(74, 0, q), (71, 1, q), (67, 2, q), (71, 3, q)],
        10: [(72, 0, q), (71, 1, q), (69, 2, h)],
        11: [(71, 0, q), (72, 1, q), (69, 2, h)],
        12: [(69, 0, q), (71, 1, q), (74, 2, h)],
        13: [(76, 0, q), (74, 1, q), (71, 2, h)],
        14: [(72, 0, q), (74, 1, q), (76, 2, h)],
        15: [(74, 0, q), (71, 1, q), (67, 2, h)],
        16: [(69, 0, q), (71, 1, q), (74, 2, h)],
        17: [(76, 0, q), (72, 1, q), (69, 2, h)],
        18: [(71, 0, q), (72, 1, q), (74, 2, h)],
        19: [(76, 0, q), (74, 1, q), (72, 2, q), (71, 3, q)],
        20: [(74, 0, w)],
        21: [(74, 0, q), (71, 1, q), (67, 2, h)],
        22: [(69, 0, q), (71, 1, q), (72, 2, h)],
        23: [(71, 0, q), (67, 1, q), (76, 2, q), (74, 3, q)],
        24: [(69, 0, q), (71, 1, q), (74, 2, h)],
        25: [(79, 0, q), (74, 1, q), (71, 2, h)],
        26: [(72, 0, q), (71, 1, q), (69, 2, h)],
        27: [(71, 0, q), (69, 1, q), (67, 2, q), (66, 3, q)],
        28: [(67, 0, w)],
        29: [(72, 0, q), (71, 1, q), (69, 2, h)],
        30: [(74, 0, q), (71, 1, q), (67, 2, h)],
        31: [(72, 0, h)],
        32: [(67, 0, w)],
    },
    # ピアノの導入和音（1-2小節）は melody トラックへの追加音符として書く
    "extra_notes": {
        "melody": {
            1: [(59, 0, 1.9, 52), (62, 0, 1.9, 52), (67, 0, 1.9, 52),
                (62, 2, 1.9, 52), (67, 2, 1.9, 52), (71, 2, 1.9, 52)],
            2: [(60, 0, 1.9, 52), (64, 0, 1.9, 52), (67, 0, 1.9, 52),
                (64, 2, 1.9, 52), (67, 2, 1.9, 52), (72, 2, 1.9, 52)],
        },
    },
    "humanize": {"vel_jitter": 4, "time_jitter_ticks": 0},
    # 納品ゲート用の目標（render.py / measure.py が読む）
    "gate": {"lufs_target": -14.0, "lufs_tol": 0.5, "tp_max": -1.0,
             "low_tol_pt": 15.0, "centroid_ratio": [0.5, 2.0], "duration_tol_s": 4.0},
}
