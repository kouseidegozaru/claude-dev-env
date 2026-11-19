# 作曲仕様書の例（ループ型）: 湖畔の循環ループ
# 16小節をそのまま繰り返す設計。頭と尾が同じ進行・同じエネルギーで接続する。
# G major, 88 BPM, 4/4。ピアノ主旋律 + ギターのアルペジオ + ベース + 弦パッド。
# render.py --loop で末尾2秒クロスフェード版を作り、継ぎ目を計測する。
# 一般的なサンプル spec: motif / cadence_table / energy_arc 等の新スキーマ宣言を含む。

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
    # 音階文脈（check が音階ステップ基準で数えるために使う）
    "scale_pcs": [0, 2, 4, 6, 7, 9, 11],  # G major
    "tonic_pc": 7,
    # 主題モチーフ（型付きレコード）: D-B-G の下降形、署名は先頭の -2
    "motif": {
        "intervals": [-2, -2],
        "rhythm": [1, 1, 2],
        "contour": "falling",
        "anchor_start_degree": 5,
        "anchor_end_degree": 1,
        "signature": [-2],
    },
    "cadence_table": [
        {"phrase": "loop-1", "end_bar": 4, "type": "half", "melody_degree": 5},
        {"phrase": "loop-2", "end_bar": 8, "type": "half", "melody_degree": 2},
        {"phrase": "loop-3", "end_bar": 12, "type": "half", "melody_degree": 2},
        {"phrase": "loop-4", "end_bar": 16, "type": "authentic", "melody_degree": 1},
    ],
    "harmony_events": [
        {"bar": 1, "type": "loop_start"},
        {"bar": 5, "type": "cycle_2"},
        {"bar": 9, "type": "cycle_3"},
        {"bar": 13, "type": "final_cycle"},
    ],
    # エネルギーの弧（計画値。check が実測と照合する。単一セクションのため対比判定は対象外）
    "energy_arc": {
        "loop": {"density": 15.9, "parts": 4, "mean_pitch": 70.5, "range_width": 12, "vel": 96.9, "rest_ratio": 0.01, "harmonic_rhythm": 1.0, "top_note_value": 1.0},
    },
    "progression": ["G", "C", "Em", "D"] * 3 + ["G", "C", "D", "G"],
    "chords": {
        "G":  {"arp": [55, 59, 62, 67], "bass_root": 43, "bass_fifth": 50, "pad": [71, 74, 79]},
        "C":  {"arp": [55, 60, 64, 67], "bass_root": 48, "bass_fifth": 55, "pad": [72, 76, 79]},
        "Em": {"arp": [52, 59, 64, 67], "bass_root": 40, "bass_fifth": 47, "pad": [71, 76, 79]},
        "D":  {"arp": [54, 57, 62, 66], "bass_root": 50, "bass_fifth": 57, "pad": [69, 74, 78]},
    },
    "tracks": {
        "melody": {"name": "Piano", "channel": 0, "program": 0,
                   "range_hard": [55, 84], "range_sweet": [62, 79],
                   "render_route": "recorded_samples"},
        "accomp": {"name": "Guitar", "channel": 1, "program": 24,
                   "range_hard": [40, 79]},
        "bass":   {"name": "Bass", "channel": 2, "program": 32,
                   "range_hard": [36, 60]},
        "pad":    {"name": "Strings", "channel": 3, "program": 48,
                   "range_hard": [57, 84]},
    },
    "accomp": {"style": "arpeggio", "pattern": [0, 1, 2, 3, 2, 1, 2, 3],
               "note_len": 0.55, "vel": 58, "vel_by_section": {"loop": 58}},
    "bass": {"style_by_section": {"default": "root_fifth"}, "skip_last_bar": False,
             "vel": 62, "vel_by_section": {"loop": 62}},
    "pad": {"vel": 30, "vel_by_section": {"loop": 30}, "voices": "auto"},
    "melody_cfg": {"vel": 88, "vel_by_section": {"loop": 88}, "dur_scale": 0.95},
    "melody": {
        1:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        2:  [(69, 0, h), (71, 2, q), (74, 3, q)],
        3:  [(71, 0, q), (67, 1, q), (64, 2, h)],
        4:  [(69, 0, h), (74, 2, h)],
        5:  [(74, 0, h), (71, 2, q), (67, 3, q)],
        6:  [(72, 0, q), (74, 1, h), (71, 3, q)],
        7:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        8:  [(69, 0, q), (71, 1, q), (69, 2, h)],
        9:  [(74, 0, q), (71, 1, q), (67, 2, h)],
        10: [(69, 0, h), (72, 2, h)],
        11: [(76, 0, h), (74, 2, q), (71, 3, q)],
        12: [(74, 0, q), (71, 1, q), (69, 2, h)],
        13: [(67, 0, h), (69, 2, q), (71, 3, q)],
        14: [(72, 0, q), (71, 1, h), (69, 3, q)],
        15: [(71, 0, q), (69, 1, q), (67, 2, q), (66, 3, q)],
        16: [(67, 0, w)],
    },
    # vel_jitter は仕上げの微量成分。タイミングは σ=拍の1.5% の相関ノイズ。
    "humanize": {"vel_jitter": 4, "timing_sigma_pct": 0.015},
    "gate": {"lufs_target": -14.0, "lufs_tol": 0.5, "tp_max": -1.0,
             "low_tol_pt": 15.0, "centroid_ratio": [0.5, 2.0], "duration_tol_s": 4.0},
}
