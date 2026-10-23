#!/usr/bin/env python3
"""kansei: 画像（スクリーンショット・作品画像・動画の代表フレーム）の画素を数えて、色の面積比を出す。

measure-web.mjs と同じ数え方（各色 6bit に丸め、距離 24 以内の色は面積の大きい色にまとめる）。
Playwright が無いときの代わり。Pillow があれば使い、無ければ標準ライブラリだけで PNG を読む
（8bit の RGB/RGBA/グレー、インターレースなしの PNG に対応。JPEG は Pillow が要る）。

使い方: python3 count-colors.py <画像> [--top 12] [--step 2]
  --step: 何画素おきに数えるか（大きい画像を速く数える。既定は自動）
"""
import struct
import sys
import zlib
from math import hypot


def read_png_stdlib(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("PNG ではない（JPEG 等は Pillow が要る）")
    pos, idat, w = 8, b"", None
    while pos < len(data):
        n, typ = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            if depth != 8 or interlace or ctype not in (0, 2, 4, 6):
                raise ValueError("8bit・インターレースなしの PNG だけ対応（Pillow があれば他も可）")
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + n
    bpp = {0: 1, 2: 3, 4: 2, 6: 4}[ctype]
    raw = zlib.decompress(idat)
    stride = w * bpp
    rows, prev, i = [], bytearray(stride), 0
    for _ in range(h):
        ft = raw[i]; line = bytearray(raw[i + 1:i + 1 + stride]); i += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b = prev[x]
            c = prev[x - bpp] if x >= bpp else 0
            if ft == 1: line[x] = (line[x] + a) & 255
            elif ft == 2: line[x] = (line[x] + b) & 255
            elif ft == 3: line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif ft == 4:
                p = a + b - c; pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line); prev = line

    def pixels(step):
        for y in range(0, h, step):
            r = rows[y]
            for x in range(0, w, step):
                o = x * bpp
                if ctype in (0, 4):
                    yield r[o], r[o], r[o]
                else:
                    yield r[o], r[o + 1], r[o + 2]
    return w, h, pixels


def read_any(path):
    try:
        from PIL import Image
        im = Image.open(path).convert("RGB")
        w, h = im.size
        px = im.load()

        def pixels(step):
            for y in range(0, h, step):
                for x in range(0, w, step):
                    yield px[x, y]
        return w, h, pixels
    except ImportError:
        return read_png_stdlib(path)


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(2)
    path = args[0]
    top = int(args[args.index("--top") + 1]) if "--top" in args else 12
    w, h, pixels = read_any(path)
    step = int(args[args.index("--step") + 1]) if "--step" in args else max(1, int((w * h / 1_500_000) ** 0.5))
    counts = {}
    for r, g, b in pixels(step):
        k = ((r >> 2) << 12) | ((g >> 2) << 6) | (b >> 2)
        counts[k] = counts.get(k, 0) + 1
    total = sum(counts.values())
    clusters = []
    for k, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        c = (((k >> 12) & 63) * 4 + 2, ((k >> 6) & 63) * 4 + 2, (k & 63) * 4 + 2)
        hit = next((cl for cl in clusters if hypot(cl[0][0] - c[0], cl[0][1] - c[1], cl[0][2] - c[2]) <= 24), None)
        if hit: hit[1] += n
        elif len(clusters) < 400: clusters.append([c, n])
        else: clusters[-1][1] += n
    clusters.sort(key=lambda cl: -cl[1])
    print(f"{path} {w}x{h}（{step} 画素おきに集計）")
    rows = []
    for c, n in clusters[:top]:
        hexv = "#" + "".join(f"{min(255, v):02X}" for v in c)
        chroma = max(c) - min(c)
        pct = round(n / total * 100, 1)
        rows.append((hexv, pct, chroma))
        print(f"{hexv} {pct}% 彩度差{chroma}")
    ground = rows[0]
    accents = [r for r in rows[1:] if r[2] >= 40 and r[1] >= 0.05]
    neutrals = [r for r in rows[1:] if r[2] < 40][:3]
    print(f"地色（画素）: {ground[0]} {ground[1]}%")
    print("無彩色の次点（画素）: " + (", ".join(f"{r[0]} {r[1]}%" for r in neutrals) or "なし"))
    print("差し色（画素・彩度のある色）: " + (", ".join(f"{r[0]} {r[1]}%" for r in accents[:4]) or "なし"))


if __name__ == "__main__":
    main()
