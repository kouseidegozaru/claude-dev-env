#!/usr/bin/env python3
"""kansei: 日本語の文章の「必須の軸」を数える（参考文章と制作物を同じ数え方で測るため）。

使い方: python3 measure-text.py <ファイル.md|.txt> [--whole]
  - Markdown の見出し（# で始まる行）と区切り線は数えない。
  - `## ` 見出しがあれば、見出しごとに測る（--whole で全体を1つとして測る）。
    最初の `## ` より前（出典・説明書きなど）は数えない。数えるなら --include-preamble。
  - 文は 。！？!? と改行で区切る。語尾の分類・三点列挙の検出は機械的な目安。最後は目で確かめる。
  - 固有名詞は自動で判定しない（カタカナ語を手がかりとして出すだけ）。数えて書き足す。
"""
import re
import sys
from statistics import mean

PROMO = ["革新", "シームレス", "次世代", "唯一無二", "寄り添", "紡ぐ", "紡ぎ", "彩る", "彩り", "可能性", "の世界へ", "最高", "極上",
         "至福", "こだわり", "上質", "洗練", "癒やし", "癒し", "特別な", "素敵な", "ぜひ", "是非", "大切な", "心を込め", "安心"]
FIRST = ["私", "わたし", "僕", "ぼく", "うち", "当店", "わたくし", "わたしたち", "私たち"]
SECOND = ["あなた", "みなさん", "皆さん", "皆様", "みなさま", "お客様", "お客さま"]
KNUM = "一二三四五六七八九十百千万〇"
UNIT = "日月時分円人本回年歳つ個段枚度半週号ミリセンチグラム"


def sentences_of(text):
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        for s in re.findall(r"[^。！？!?]+[。！？!?」』）)]*", line):
            s = s.strip()
            if s:
                out.append(s)
    return out


def ending(s):
    t = re.sub(r"[。！？!?」』）)\s]+$", "", s)
    if re.search(r"(ます|ました|ません|ましょう|です|でした|ください|でしょう)$", t): return "です・ます"
    if re.search(r"(ので|から|けど|けれど|が|て|で|に|のに|ば|し)$", t): return "言いさし"
    if re.search(r"[一-鿿゠-ヿー々]$", t): return "体言止め"
    if re.search(r"(だ|である|た|る|い|う|く|す|つ|ぬ|む|ぶ|ぐ|ず|ない|ん|よ|ね|な)$", t): return "常体"
    return "その他"


def measure(name, text):
    lines = [l for l in text.split("\n") if not l.lstrip().startswith("#") and not re.fullmatch(r"\s*[-*_]{3,}\s*", l)]
    body = "\n".join(lines).strip()
    paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
    sents = sentences_of(body)
    if not sents:
        return None
    lens = [len(re.sub(r"\s", "", s)) for s in sents]
    chars = re.sub(r"\s", "", body)
    kanji = len(re.findall(r"[一-鿿々]", chars))
    hira = len(re.findall(r"[぀-ゟ]", chars))
    kata = len(re.findall(r"[゠-ヿー]", chars))
    jp = kanji + hira + kata or 1
    ends = {}
    for s in sents:
        e = ending(s); ends[e] = ends.get(e, 0) + 1
    nums = re.findall(r"[0-9０-９][0-9０-９,，.:：〜~\-/]*|[" + KNUM + r"]+(?=[" + UNIT + r"])", body)
    contrast = len(re.findall(r"ではなく|じゃなく|ではない[、。]|だけでなく|だけじゃなく|よりも", body))
    triples = len(re.findall(r"(?:[^、。\n]{1,10}、){2}[^、。\n]{1,10}(?:など|や|と|も|。)", body))
    promo = [w for w in PROMO if w in body]
    first = sorted({w for w in FIRST if w in body}); second = sorted({w for w in SECOND if w in body})
    kata_words = sorted(set(re.findall(r"[゠-ヿ]{2,}ー?[゠-ヿ]*", body)))
    symbols = {"！": len(re.findall(r"[！!]", body)), "？": len(re.findall(r"[？?]", body)), "ダッシュ": len(re.findall(r"——|―|−−", body)),
               "絵文字": len(re.findall(r"[\U0001F300-\U0001FAFF☀-➿]", body)), "太字": body.count("**") // 2, "「」": body.count("「")}
    longest = sents[lens.index(max(lens))]; shortest = sents[lens.index(min(lens))]
    print(f"\n## {name}")
    print(f"文の長さ: 平均 {mean(lens):.1f} 字 / 最長 {max(lens)} 字「{longest[:24]}」 / 最短 {min(lens)} 字「{shortest}」 / 文数 {len(sents)}")
    print(f"段落: {len(paras)} 段落、1段落あたり {len(sents) / len(paras):.1f} 文 / 総字数 {len(chars)}")
    print("語尾: " + ", ".join(f"{k} {v}" for k, v in sorted(ends.items(), key=lambda kv: -kv[1])))
    print(f"漢字率: {kanji / jp * 100:.0f}%（カタカナ {kata / jp * 100:.0f}%、ひらがな {hira / jp * 100:.0f}%）")
    print(f"視点: 一人称 {', '.join(first) or 'なし'} / 二人称 {', '.join(second) or 'なし'}")
    print(f"結論の位置・締め（目で判断する材料）: 最初の文「{sents[0]}」 / 最後の文「{sents[-1]}」")
    print(f"具体物: 数字 {len(nums)}（{' '.join(nums[:8])}） / 固有名詞は手で数える（カタカナ語: {' '.join(kata_words[:8]) or 'なし'}）")
    print(f"対比構文: {contrast} / 三点列挙（目安）: {triples}")
    print(f"抽象語・宣伝語: {len(promo)}（{' '.join(promo) or 'なし'}）")
    print("記号: " + ", ".join(f"{k} {v}" for k, v in symbols.items()))
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__); sys.exit(2)
    text = open(sys.argv[1], encoding="utf-8").read()
    parts = re.split(r"^##\s+(.+)$", text, flags=re.M)
    if "--whole" in sys.argv or len(parts) == 1:
        measure("全体", text)
        return
    if parts[0].strip() and "--include-preamble" in sys.argv:
        measure("（最初の見出しより前）", parts[0])
    elif parts[0].strip():
        print("（最初の ## 見出しより前は数えていない。数えるなら --include-preamble）")
    for i in range(1, len(parts), 2):
        measure(parts[i].strip(), parts[i + 1])


if __name__ == "__main__":
    main()
