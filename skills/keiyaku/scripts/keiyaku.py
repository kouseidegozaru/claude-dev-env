#!/usr/bin/env python3
"""keiyaku: 契約数の詰まりを計算する（ファネル・ストック・A/B テスト）。標準ライブラリのみ。

使い方:
  python3 keiyaku.py funnel <段階.csv>
      CSV: stage,count[,target][,note]。上から段階の順。target は前段階比の目標転換率（0〜1、空欄可）。
      各段階の転換率・離脱数と、その段階だけが target に届いたとき／相対 +10% したときの最終段階の増分を出す。
  python3 keiyaku.py stock --active N --new N --churn R [--months 12] [--new-x 1.1] [--churn-x 0.9]
      有効契約数の月次推移（翌月 = 今月 + 新規 − 今月 × 解約率）と定常状態（新規 ÷ 解約率）。
      --new-x / --churn-x を付けると、新規を何倍・解約率を何倍にした場合と並べる。
  python3 keiyaku.py ab-size --base P --mde M [--alpha 0.05] [--power 0.8]
      2群の比率の差を検出するのに必要な1群あたりのサンプル数。M は相対改善（0.2 = +20%）。
  python3 keiyaku.py ab-judge --a N X --b N X [--alpha 0.05]
      A/B の判定。N は母数、X は契約（転換）数。差・相対改善・信頼区間・p 値（両側、2標本比率の z 検定）。
  python3 keiyaku.py selftest
"""
import argparse
import csv
import math
import sys
from statistics import NormalDist

Z = NormalDist()


def read_funnel(path):
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
            if not r.get("stage"):
                continue
            t = r.get("target", "")
            rows.append({"stage": r["stage"], "count": float(r["count"]),
                         "target": float(t) if t else None, "note": r.get("note", "")})
    if len(rows) < 2:
        sys.exit("段階が2つ以上必要")
    return rows


def funnel_calc(rows):
    last = rows[-1]["count"]
    out = []
    for i, r in enumerate(rows):
        o = dict(r, rate=None, drop=None, gain_target=None, gain_10=None)
        if i > 0:
            prev = rows[i - 1]["count"]
            o["rate"] = r["count"] / prev if prev else 0.0
            o["drop"] = prev - r["count"]
            o["gain_10"] = last * 0.1
            if r["target"] is not None and o["rate"]:
                o["gain_target"] = max(0.0, last * (r["target"] / o["rate"] - 1))
        out.append(o)
    return out


def cmd_funnel(a):
    rows = funnel_calc(read_funnel(a.csv))
    pct = lambda v: "" if v is None else f"{v * 100:.1f}%"
    num = lambda v: "" if v is None else f"{v:,.0f}"
    print("| 段階 | 数 | rate | drop | target | gain@target | gain@+10% | note |")
    print("|---|---|---|---|---|---|---|---|")
    for o in rows:
        print(f"| {o['stage']} | {num(o['count'])} | {pct(o['rate'])} | {num(o['drop'])} | "
              f"{pct(o['target'])} | {num(o['gain_target'])} | {num(o['gain_10'])} | {o['note']} |")
    first, last = rows[0]["count"], rows[-1]["count"]
    print(f"\n全体の転換率（{rows[0]['stage']} → {rows[-1]['stage']}）: {last / first * 100:.2f}%")
    ranked = sorted([o for o in rows if o["gain_target"]], key=lambda o: -o["gain_target"])
    if ranked:
        print("目標到達時の増分が大きい順: " + " > ".join(f"{o['stage']}(+{o['gain_target']:,.0f})" for o in ranked))
    else:
        print("target 列が無い（または全段階が目標以上）。詰まりは改善の余地と手間で判断する。")


def simulate(active, new, churn, months):
    s = [active]
    for _ in range(months):
        active = active + new - active * churn
        s.append(active)
    return s


def cmd_stock(a):
    if not 0 < a.churn < 1:
        sys.exit("--churn は 0〜1 の月次解約率")
    cases = [("そのまま", a.new, a.churn)]
    if a.new_x:
        cases.append((f"新規×{a.new_x:g}", a.new * a.new_x, a.churn))
    if a.churn_x:
        cases.append((f"解約率×{a.churn_x:g}", a.new, a.churn * a.churn_x))
    if a.new_x and a.churn_x:
        cases.append(("両方", a.new * a.new_x, a.churn * a.churn_x))
    sims = [(n, simulate(a.active, nw, c, a.months), nw / c) for n, nw, c in cases]
    print("| 月 | " + " | ".join(n for n, _, _ in sims) + " |")
    print("|---|" + "---|" * len(sims))
    step = 1 if a.months <= 12 else max(1, a.months // 12)
    for m in range(0, a.months + 1):
        if m % step == 0 or m == a.months:
            print(f"| {m} | " + " | ".join(f"{s[m]:,.0f}" for _, s, _ in sims) + " |")
    print()
    for n, s, ss in sims:
        print(f"{n}: 定常状態 {ss:,.0f}（現在の {ss / a.active:.2f} 倍）、{a.months} か月後 {s[-1]:,.0f}"
              f"（{s[-1] - a.active:+,.0f}）")
    lost = a.active * a.churn
    print(f"\n今月の解約 {lost:,.0f} 件 vs 新規 {a.new:,.0f} 件"
          + ("。新規が解約を下回っており減少中" if lost > a.new else ""))


def sample_size(base, mde, alpha=0.05, power=0.8):
    p1, p2 = base, base * (1 + mde)
    if not (0 < p1 < 1 and 0 < p2 < 1):
        raise ValueError("率は 0〜1 の範囲に")
    za, zb = Z.inv_cdf(1 - alpha / 2), Z.inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p2 - p1) ** 2
    return math.ceil(n)


def cmd_ab_size(a):
    n = sample_size(a.base, a.mde, a.alpha, a.power)
    print(f"基準 {a.base * 100:.2f}% → {a.base * (1 + a.mde) * 100:.2f}%（相対 {a.mde * 100:+.0f}%）を "
          f"有意水準 {a.alpha}（両側）・検出力 {a.power} で検出するには")
    print(f"1群あたり {n:,} 、合計 {2 * n:,}")
    if a.daily:
        days = math.ceil(2 * n / a.daily)
        print(f"1日 {a.daily:,.0f} の流入で約 {days} 日" + ("（最低7日は回す）" if days < 7 else ""))


def judge(na, xa, nb, xb, alpha=0.05):
    pa, pb = xa / na, xb / nb
    p = (xa + xb) / (na + nb)
    se0 = math.sqrt(p * (1 - p) * (1 / na + 1 / nb))
    z = (pb - pa) / se0 if se0 else 0.0
    pval = 2 * (1 - Z.cdf(abs(z)))
    se = math.sqrt(pa * (1 - pa) / na + pb * (1 - pb) / nb)
    zc = Z.inv_cdf(1 - alpha / 2)
    return {"pa": pa, "pb": pb, "diff": pb - pa, "lift": (pb / pa - 1) if pa else float("nan"),
            "lo": pb - pa - zc * se, "hi": pb - pa + zc * se, "z": z, "p": pval}


def cmd_ab_judge(a):
    na, xa = a.a
    nb, xb = a.b
    r = judge(na, xa, nb, xb, a.alpha)
    ci = int(round((1 - a.alpha) * 100))
    print(f"A: {xa:,.0f}/{na:,.0f} = {r['pa'] * 100:.2f}%   B: {xb:,.0f}/{nb:,.0f} = {r['pb'] * 100:.2f}%")
    print(f"差 {r['diff'] * 100:+.2f}pt（相対 {r['lift'] * 100:+.1f}%）、{ci}% 信頼区間 "
          f"[{r['lo'] * 100:+.2f}pt, {r['hi'] * 100:+.2f}pt]、z = {r['z']:.2f}、p = {r['p']:.4f}")
    if r["p"] < a.alpha and r["lo"] > 0:
        print("判定: B が有意に高い（事前に決めた基準・サンプル数を満たしているかも確かめる）")
    elif r["p"] < a.alpha and r["hi"] < 0:
        print("判定: B が有意に低い")
    else:
        need = sample_size(r["pa"], abs(r["lift"])) if 0 < r["pa"] < 1 and r["lift"] and 0 < r["pb"] < 1 else None
        msg = "判定: 有意差なし（差が無いか、サンプル不足）"
        if need:
            msg += f"。今の差を検出するには1群あたり約 {need:,} 必要"
        print(msg)


def selftest():
    ok = True

    def check(name, got, want, tol):
        nonlocal ok
        good = abs(got - want) <= tol
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} {name}: {got:.4f}（期待 {want} ± {tol}）")

    rows = funnel_calc([
        {"stage": "訪問", "count": 10000, "target": None, "note": ""},
        {"stage": "申込開始", "count": 500, "target": 0.1, "note": ""},
        {"stage": "申込完了", "count": 250, "target": 0.5, "note": ""},
    ])
    check("funnel rate", rows[1]["rate"], 0.05, 1e-9)
    check("funnel gain@target（5%→10% で倍）", rows[1]["gain_target"], 250, 1e-6)
    check("funnel gain@target（目標達成済みは 0）", rows[2]["gain_target"], 0, 1e-9)
    s = simulate(1000, 100, 0.05, 600)
    check("stock 定常状態へ収束", s[-1], 2000, 0.5)
    check("stock 1か月後", s[1], 1050, 1e-9)
    check("ab-size base3% mde20%", sample_size(0.03, 0.2), 13914, 5)
    r = judge(1000, 100, 1000, 130)
    check("ab-judge z", r["z"], 2.1027, 0.001)
    check("ab-judge p", r["p"], 0.0355, 0.0005)
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="契約数の詰まりを計算する")
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("funnel")
    p.add_argument("csv")
    p = sp.add_parser("stock")
    p.add_argument("--active", type=float, required=True)
    p.add_argument("--new", type=float, required=True)
    p.add_argument("--churn", type=float, required=True)
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--new-x", type=float)
    p.add_argument("--churn-x", type=float)
    p = sp.add_parser("ab-size")
    p.add_argument("--base", type=float, required=True)
    p.add_argument("--mde", type=float, required=True)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--daily", type=float, help="1日の流入（期間の目安を出す）")
    p = sp.add_parser("ab-judge")
    p.add_argument("--a", type=float, nargs=2, required=True, metavar=("N", "X"))
    p.add_argument("--b", type=float, nargs=2, required=True, metavar=("N", "X"))
    p.add_argument("--alpha", type=float, default=0.05)
    sp.add_parser("selftest")
    a = ap.parse_args()
    if a.cmd == "selftest":
        sys.exit(selftest())
    {"funnel": cmd_funnel, "stock": cmd_stock, "ab-size": cmd_ab_size, "ab-judge": cmd_ab_judge}[a.cmd](a)


if __name__ == "__main__":
    main()
