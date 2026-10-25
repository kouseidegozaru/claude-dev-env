#!/usr/bin/env python3
"""keiyaku: 契約数の詰まりと効果を計算する。Python 3.8+ 標準ライブラリのみ。

使い方:
  python3 keiyaku.py funnel <段階.csv> [--contract 段名] [--churn R --months M] [--pooled K] [--set 段名=率 ...]
      CSV: stage,count[,target][,target_src][,cap][,wait][,note]。上から段階の順。
      target は前段比の目標転換率、cap はその段の上限（体験枠・商談枠・定員）、wait は待ち・断りの件数。
      各段の率と、target に届いたとき／+10% のとき／枠を待ち・断りの分だけ増やしたときの契約の増分。
      --churn と --months を付けると、増分を「期限時点の有効契約数」に換算して並べる。
      --set は仮説の率（例 有料=0.377）。その段だけをその率にしたときの増分を出す（暗算しない）。
  python3 keiyaku.py stock --active N --new N --churn R --months M [--new-x K] [--churn-x K] [--target T] [--arpu Y]
                          [--exit 月=件数 ...] [--cap N]
      期限時点（M か月後）の有効契約数、定常状態、定常に届くまでの遅れ、新規×K と解約率×K の比較。
      --exit は卒業・満期など予定された終了、--cap は定員。--arpu で期限時点の月商と期間の売上。
  python3 keiyaku.py ab-size --base P --mde M [--daily D] [--alpha 0.05] [--power 0.8]
      1群あたりの必要標本数と、1日 D の流入（2群合計）で何日かかるか。8週を超えれば A/B テスト不適。
  python3 keiyaku.py ab-judge --a N X --b N X [--planned N] [--ratio 0.5] [--observational] [--alpha 0.05]
      A/B の判定。期待度数が5未満なら Fisher の正確検定、それ以外は両側 z 検定。区間は Newcombe。SRM 点検つき。
  python3 keiyaku.py before-after --before [N] X --after [N] X [--ly-before [N] X --ly-after [N] X]
      施策前後（同じ長さの期間）の比較。値1つは件数、2つは母数と件数（率）。--ly-* はそれぞれの1年前（基準線）。
      既定の結論は「判断できない」。
  python3 keiyaku.py selftest

数値は「1,200」「１２００」「30%」を受け付ける。率の欄の 30 や 30% は 0.30 に直して警告する。
空欄・「ー」「不明」は欠損として扱い、0 とは見なさない。
"""
import argparse
import csv
import math
import sys
import unicodedata
from statistics import NormalDist

Z = NormalDist()
MISSING = {"", "-", "ー", "―", "—", "－", "不明", "na", "n/a", "nan", "null", "none", "なし", "?", "？"}
WARNINGS = []


class InputError(Exception):
    """入力が計算に使えない。理由を出して止める。"""


def warn(msg):
    WARNINGS.append(msg)


def flush_warnings():
    for w in WARNINGS:
        print(f"警告: {w}")
    if WARNINGS:
        print()
    del WARNINGS[:]


# ---------- 入力検証の層 ----------

def norm(s):
    return unicodedata.normalize("NFKC", str(s)).strip()


def parse_num(s, field="値", allow_missing=False):
    """桁区切り・全角・空欄を解釈して数にする。% 付きは受け付けない（人数の欄に率が入った取り違え）。"""
    t = norm(s)
    if t.lower() in MISSING:
        if allow_missing:
            return None
        raise InputError(f"{field} が空欄・欠損（{s!r}）。0 とは見なさない。値を確かめるか、推定値を入れて note に推定と書く")
    if t.endswith("%"):
        raise InputError(f"{field} に率（{s!r}）が入っている。人数・件数を入れる（列の取り違えを確かめる）")
    t = t.replace(",", "").replace(" ", "").replace("円", "").replace("人", "").replace("件", "")
    try:
        v = float(t)
    except ValueError:
        raise InputError(f"{field} を数として読めない（{s!r}）")
    if v < 0:
        raise InputError(f"{field} が負（{s!r}）")
    return v


def parse_rate(s, field="率", allow_missing=False, allow_over_one=False):
    """0〜1 の率にする。30 や 30% は 0.30 に直して警告する。"""
    t = norm(s)
    if t.lower() in MISSING:
        if allow_missing:
            return None
        raise InputError(f"{field} が空欄")
    pct = t.endswith("%")
    t = t.rstrip("%").replace(",", "").strip()
    try:
        v = float(t)
    except ValueError:
        raise InputError(f"{field} を数として読めない（{s!r}）")
    if pct:
        v /= 100.0
        warn(f"{field} の {s!r} を {v:g} に直した（% 書き）")
    elif v > 1 and not allow_over_one:
        if v <= 100:
            warn(f"{field} の {s!r} を % 書きとみなして {v / 100:g} に直した。違うなら 0〜1 で入れ直す")
            v /= 100.0
        else:
            raise InputError(f"{field} が 0〜1 の範囲外（{s!r}）")
    if v < 0:
        raise InputError(f"{field} が負（{s!r}）")
    if v > 1 and not allow_over_one:
        raise InputError(f"{field} が 1 を超える（{s!r}）")
    return v


# ---------- funnel ----------

def read_funnel(path):
    text = None
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, newline="", encoding=enc) as f:
                text = f.read()
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise InputError(f"{path} の文字コードを読めない（UTF-8 か Shift_JIS で保存し直す）")
    rows = []
    reader = csv.DictReader(text.splitlines())
    if not reader.fieldnames or "stage" not in [norm(h) for h in reader.fieldnames]:
        raise InputError("1行目は見出し stage,count[,target][,target_src][,cap][,wait][,note] にする")
    for r in reader:
        r = {norm(k or ""): (v or "") for k, v in r.items()}
        stage = norm(r.get("stage", ""))
        if not stage:
            continue
        rows.append({
            "stage": stage,
            "count": parse_num(r.get("count", ""), f"{stage} の count"),
            "target": parse_rate(r.get("target", ""), f"{stage} の target", allow_missing=True),
            "target_src": norm(r.get("target_src", "")),
            "cap": parse_num(r.get("cap", ""), f"{stage} の cap", allow_missing=True),
            "wait": parse_num(r.get("wait", ""), f"{stage} の wait", allow_missing=True),
            "note": norm(r.get("note", "")),
        })
    return rows


def deadline_gain(g, churn, months):
    """毎月 g 件の新規増を今月から続けたときの、months か月後の有効契約数の増分。"""
    if churn is None or months is None:
        return None
    if churn == 0:
        return g * months
    return g * (1 - (1 - churn) ** months) / churn


def propagate(c0, rates, caps):
    f = [c0]
    for i in range(1, len(rates)):
        v = f[-1] * rates[i]
        if caps[i] is not None:
            v = min(v, caps[i])
        f.append(v)
    return f


def funnel_calc(rows, contract=None, churn=None, months=None, pooled=1, what_if=None):
    if len(rows) < 2:
        raise InputError("段階が2つ以上必要")
    n = len(rows)
    for i, r in enumerate(rows):
        if r["count"] == 0:
            where = "先頭" if i == 0 else ("最終" if i == n - 1 else "途中")
            raise InputError(f"{where}の段「{r['stage']}」が 0。以降の率が計算できない。"
                             "期間を延ばして合算するか、段の取り違え・集計漏れを確かめる")
    k = n - 1
    if contract:
        names = [r["stage"] for r in rows]
        if contract not in names:
            raise InputError(f"--contract の段「{contract}」が CSV に無い（{', '.join(names)}）")
        k = names.index(contract)
    what_if = dict(what_if or {})
    for name in what_if:
        if name not in [r["stage"] for r in rows]:
            raise InputError(f"--set の段「{name}」が CSV に無い")
    rates = [None] + [rows[i]["count"] / rows[i - 1]["count"] for i in range(1, n)]
    caps = [r["cap"] for r in rows]
    for i in range(1, n):
        if rates[i] > 1:
            warn(f"「{rows[i]['stage']}」が前段より多い（転換率 {rates[i] * 100:.0f}%）。"
                 "縦持ち・段の取り違え・前段を通らない流入（紹介・兄弟など）を疑う")
        if caps[i] is not None and caps[i] < rows[i]["count"]:
            warn(f"「{rows[i]['stage']}」の cap {caps[i]:,.0f} が実数 {rows[i]['count']:,.0f} より小さい。実数を上限とした")
            caps[i] = rows[i]["count"]
    base = propagate(rows[0]["count"], rates, caps)

    def measure(f, i):
        return f[k] if i <= k else f[-1]

    def gain_with(i, new_rate, new_cap="keep"):
        rr = list(rates)
        rr[i] = new_rate
        cc = list(caps)
        if new_cap != "keep":
            cc[i] = new_cap
        return max(0.0, measure(propagate(rows[0]["count"], rr, cc), i) - measure(base, i))

    out = []
    for i, r in enumerate(rows):
        o = dict(r, rate=rates[i], prev=None, gain_target=None, gain_10=None, gain_cap=None, gain_set=None,
                 set_rate=what_if.get(r["stage"]),
                 at_cap=False, small=False, post=i > k)
        if i > 0:
            o["prev"] = rows[i - 1]["count"]
            o["small"] = o["prev"] < 30
            o["at_cap"] = caps[i] is not None and r["count"] >= caps[i] - 1e-9
            if r["target"] is not None:
                o["gain_target"] = gain_with(i, r["target"]) if r["target"] > rates[i] else 0.0
            o["gain_10"] = gain_with(i, min(1.0, rates[i] * 1.1)) if rates[i] < 1 else 0.0
            if o["set_rate"] is not None:
                o["gain_set"] = gain_with(i, o["set_rate"])
            if r["wait"]:
                demand = r["count"] + r["wait"]
                o["gain_cap"] = gain_with(i, demand / o["prev"], demand)
        for key in ("gain_target", "gain_10", "gain_cap", "gain_set"):
            if o[key] is not None:
                o[key] /= pooled
            o[key + "_dl"] = deadline_gain(o[key], churn, months) if o[key] is not None else None
        out.append(o)
    return out


def funnel_ranking(out):
    items = []
    for o in out[1:]:
        if o["small"]:
            continue
        if o["gain_target"] and not o["at_cap"]:
            items.append((o["gain_target"], f"{o['stage']}の率"))
        if o["gain_cap"]:
            items.append((o["gain_cap"], f"{o['stage']}の枠を増やす"))
    return sorted(items, key=lambda t: -t[0])


def cmd_funnel(a):
    churn = parse_rate(a.churn, "--churn") if a.churn is not None else None
    months = int(parse_num(a.months, "--months")) if a.months is not None else None
    if (churn is None) != (months is None):
        raise InputError("--churn と --months は両方付ける（期限時点への換算に両方要る）")
    pooled = int(parse_num(a.pooled, "--pooled"))
    if pooled < 1:
        raise InputError("--pooled は合算した月数（1 以上）")
    what_if = {}
    for item in a.set or []:
        if "=" not in item:
            raise InputError(f"--set は 段名=率 の形（{item!r}）")
        name, val = item.rsplit("=", 1)
        what_if[norm(name)] = parse_rate(val, f"--set {norm(name)}")
    out = funnel_calc(read_funnel(a.csv), a.contract, churn, months, pooled, what_if)
    flush_warnings()
    pct = lambda v: "" if v is None else f"{v * 100:.1f}%"
    num = lambda v: "" if v is None else f"{v:,.0f}" if v >= 10 or float(v).is_integer() else f"{v:,.1f}"
    dl = churn is not None
    head = "| 段階 | 数 | rate | 前段の母数 | target | target の根拠 | 上限 | gain@target | gain@+10% | gain@枠 |"
    if dl:
        head += f" 期限時点（{months}か月後, target/枠） |"
    print(head + " 印 | note |")
    print("|---" * (head.count("|") + 1) + "|")
    for o in out:
        marks = []
        if o["small"]:
            marks.append("n<30")
        if o["at_cap"]:
            marks.append("上限到達")
        if o["post"]:
            marks.append("契約後")
        src = o["target_src"] or ("仮の値" if o["target"] is not None else "")
        line = (f"| {o['stage']} | {num(o['count'])} | {pct(o['rate'])} | {num(o['prev'])} | {pct(o['target'])} | "
                f"{src} | {num(o['cap'])} | {num(o['gain_target'])} | {num(o['gain_10'])} | {num(o['gain_cap'])} |")
        if dl:
            pair = [num(o['gain_target_dl']), num(o['gain_cap_dl'])]
            line += (f" {pair[0]} / {pair[1]} |" if any(pair) else " |")
        print(line + f" {' '.join(marks)} | {o['note']} |")
    first, last = out[0], out[-1]
    print(f"\n全体の転換率（{first['stage']} → {last['stage']}）: {last['count'] / first['count'] * 100:.2f}%")
    print("増分の単位: 契約の段" + (f"「{a.contract}」" if a.contract else "（最終段）") + "の月あたり件数"
          + (f"（{pooled}か月合算を月あたりに直した）" if pooled > 1 else "") + "。"
          "契約後の段は、離脱が減って残る件数（契約数に換算済み）")
    for o in out[1:]:
        if o["at_cap"]:
            print(f"- 「{o['stage']}」は上限到達: 率を上げても増分0。候補に「枠を増やす」を出す"
                  + ("" if o["wait"] else "（wait 列に待ち・断りの件数を入れると増分を出せる）"))
    small = [o["stage"] for o in out[1:] if o["small"]]
    if small:
        print(f"- n<30 の段（{', '.join(small)}）は順位に入れない。まだなら3〜6か月を合算して再実行。"
              "合算しても30未満なら「定性で決定」: 失注・離脱の理由の原文を読んで決める")
    if any(o["target"] is not None and not o["target_src"] for o in out):
        print("- target の根拠が空欄の段は「仮の値」。自社の過去最良を優先し、業界の目安なら出典を書く")
    ranked = funnel_ranking(out)
    if ranked:
        print("増分が大きい順: " + " > ".join(f"{n}(+{g:,.1f})" for g, n in ranked))
    elif small and len(small) == len(out) - 1:
        print("順位: 定性で決定（全段 n<30）")
    else:
        print("順位を付けられる増分なし（target 列が無い・全段が目標以上・上限到達）。")
    for o in out[1:]:
        if o["gain_set"] is not None:
            dls = f"、期限時点 +{o['gain_set_dl']:,.1f}" if dl else ""
            print(f"仮説値の増分: {o['stage']} {o['rate'] * 100:.1f}% → {o['set_rate'] * 100:.1f}% で月 +{o['gain_set']:,.1f}{dls}"
                  + ("（n<30 の段。参考値）" if o["small"] else "") + ("（上限到達の段。率では増えない）" if o["at_cap"] else ""))
    if dl:
        print(f"期限時点の換算: 毎月の増分 g が今月から続き、月次解約率 {churn:g} で減る前提で g×(1−(1−c)^{months})/c。"
              "stock の新規×K・解約率×K の結果と同じ単位で並べる")


# ---------- stock ----------

def simulate(active, new, churn, months, exits=None, cap=None):
    s = [active]
    for m in range(1, months + 1):
        active = max(0.0, active + new - active * churn - (exits or {}).get(m, 0.0))
        if cap is not None:
            active = min(active, cap)
        s.append(active)
    return s


def at_month(active, new, churn, m, exits=None, cap=None):
    return simulate(active, new, churn, m, exits, cap)[-1]


def months_to_90(churn):
    if churn <= 0:
        return None
    if churn >= 1:
        return 1
    return math.ceil(math.log(0.1) / math.log(1 - churn))


def stock_calc(active, new, churn, months, new_x=1.2, churn_x=0.8, target=None, exits=None, cap=None):
    if active <= 0:
        raise InputError("有効契約数が 0。stock は使えない（立ち上げ期は funnel で新規の流入を見る）")
    if not 0 <= churn <= 1:
        raise InputError("解約率は 0〜1")
    if months < 0:
        raise InputError("期間（--months）が負")
    if cap is not None and cap < active:
        raise InputError(f"--cap {cap:,.0f} が今の有効契約数 {active:,.0f} より小さい")
    for mm in (exits or {}):
        if not 1 <= mm <= months:
            raise InputError(f"--exit の月 {mm} が 1〜{months} の外")
    cases = [("そのまま", new, churn), (f"新規×{new_x:g}", new * new_x, churn),
             (f"解約率×{churn_x:g}", new, min(1.0, churn * churn_x)),
             ("両方", new * new_x, min(1.0, churn * churn_x))]
    res = []
    for name, nw, c in cases:
        s = simulate(active, nw, c, months, exits, cap)
        steady = (nw / c) if c > 0 else None
        if steady is not None and cap is not None:
            steady = min(steady, cap)
        res.append({"name": name, "series": s, "end": s[-1], "steady": steady, "churn": c,
                    "months_total": sum(s[:months])})
    need = None
    if target is not None:
        f = lambda nw, c: at_month(active, nw, c, months, exits, cap)
        need = {}
        if f(new, churn) >= target:
            need["new_x"] = 1.0
        elif new <= 0 or (cap is not None and cap < target):
            need["new_x"] = None
        else:
            hi = 2.0
            while f(new * hi, churn) < target and hi < 1e6:
                hi *= 2
            lo = 0.0
            for _ in range(80):
                mid = (lo + hi) / 2
                if f(new * mid, churn) >= target:
                    hi = mid
                else:
                    lo = mid
            need["new_x"] = hi if hi < 1e6 else None
        if f(new, 0.0) < target:
            need["churn_x"] = None
        elif f(new, churn) >= target:
            need["churn_x"] = 1.0
        else:
            lo, hi = 0.0, 1.0
            for _ in range(60):
                mid = (lo + hi) / 2
                if f(new, churn * mid) >= target:
                    lo = mid
                else:
                    hi = mid
            need["churn_x"] = lo
    return res, need


def cmd_stock(a):
    active = parse_num(a.active, "--active")
    new = parse_num(a.new, "--new")
    churn = parse_rate(a.churn, "--churn")
    m = norm(a.months)
    if m.startswith("-"):
        raise InputError("期間（--months）が負")
    months = int(parse_num(m, "--months"))
    new_x = parse_num(a.new_x, "--new-x") if a.new_x else 1.2
    churn_x = parse_num(a.churn_x, "--churn-x") if a.churn_x else 0.8
    target = parse_num(a.target, "--target") if a.target else None
    arpu = parse_num(a.arpu, "--arpu") if a.arpu else None
    cap = parse_num(a.cap, "--cap") if a.cap else None
    exits = {}
    for item in a.exit or []:
        if "=" not in item:
            raise InputError(f"--exit は 月=件数 の形（{item!r}。例 6=32 は6か月目に32件が予定どおり終了）")
        mm, nn = item.split("=", 1)
        exits[int(parse_num(mm, "--exit の月"))] = exits.get(int(parse_num(mm, "--exit の月")), 0.0) + parse_num(nn, "--exit の件数")
    res, need = stock_calc(active, new, churn, months, new_x, churn_x, target, exits, cap)
    flush_warnings()
    print("| 月 | " + " | ".join(r["name"] for r in res) + " |")
    print("|---|" + "---|" * len(res))
    step = 1 if months <= 12 else max(1, months // 12)
    for i in range(months + 1):
        if i % step == 0 or i == months:
            print(f"| {i} | " + " | ".join(f"{r['series'][i]:,.0f}" for r in res) + " |")
    print(f"\n期限時点（{months} か月後）の有効契約数:")
    base_end = res[0]["end"]
    for r in res:
        ss = "上限なし（解約0）" if r["steady"] is None else f"{r['steady']:,.0f}"
        rev = f"、月商 {r['end'] * arpu:,.0f}" if arpu else ""
        print(f"- {r['name']}: {r['end']:,.0f}（そのまま比 {r['end'] - base_end:+,.0f}{rev}）、定常状態 {ss}")
    if exits:
        print("予定された終了（解約率とは別に引いた）: " + "、".join(f"{m}か月目 {n:,.0f}件" for m, n in sorted(exits.items())))
    if cap is not None:
        print(f"上限（定員・枠）: {cap:,.0f}。超える分は入れない")
    if arpu:
        print(f"期間の売上（各月初の有効契約数 × ARPU を {months} か月分合計）: "
              + "、".join(f"{r['name']} {r['months_total'] * arpu:,.0f}" for r in res))
    t90 = months_to_90(churn)
    if t90 is None:
        print("定常に届くまでの遅れ: 解約率 0 のため定常状態なし（直線的に増える）")
    else:
        print(f"定常に届くまでの遅れ: 今との差の 90% が埋まるまで約 {t90} か月（解約率 {churn:g}）。"
              + ("期限内に定常へ届かない。期限時点の値で比べる" if t90 > months else ""))
    lost = active * churn
    print(f"今月の解約 {lost:,.0f} 件 vs 新規 {new:,.0f} 件" + ("。新規が解約を下回り減少中" if lost > new else ""))
    if need is not None:
        print(f"\n目標 {target:,.0f} に期限時点で届くには（単独策の場合）:")
        nx = need["new_x"]
        print("- 新規: " + ("計算できない（新規0）" if nx is None else
                            ("今のままで届く" if nx <= 1 else f"×{nx:.2f}（月 {new * nx:,.0f} 件）")))
        cx = need["churn_x"]
        print("- 解約率: " + ("解約0でも届かない" if cx is None else
                             ("今のままで届く" if cx >= 1 else f"×{cx:.2f}（月次 {churn * cx:.4f}）")))
    if arpu:
        print("\n月商 = 有効契約数 × ARPU。LTV（単価×継続月数）は獲得単価の上限を決めるときだけ使う")


# ---------- 統計 ----------

def sample_size(base, mde, alpha=0.05, power=0.8):
    if mde == 0:
        raise InputError("mde が 0。検出したい相対差（例 0.2 = +20%）を入れる")
    p1, p2 = base, base * (1 + mde)
    if not (0 < p1 < 1 and 0 < p2 < 1):
        raise InputError("基準の率と改善後の率は 0〜1 の範囲に（base と mde を確かめる）")
    za, zb = Z.inv_cdf(1 - alpha / 2), Z.inv_cdf(power)
    pbar = (p1 + p2) / 2
    n = (za * math.sqrt(2 * pbar * (1 - pbar)) + zb * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2 / (p2 - p1) ** 2
    return math.ceil(n)


def cmd_ab_size(a):
    base = parse_rate(a.base, "--base")
    t = norm(a.mde)
    if t.endswith("%"):
        mde = float(t.rstrip("%")) / 100
        warn(f"--mde の {a.mde!r} を {mde:g} に直した（% 書き）")
    else:
        mde = float(t)
        if abs(mde) >= 1:
            warn(f"--mde の {a.mde!r} を % 書きとみなして {mde / 100:g} に直した（相対差。0.2 = +20%）")
            mde /= 100
    n = sample_size(base, mde, a.alpha, a.power)
    flush_warnings()
    print(f"基準 {base * 100:.2f}% → {base * (1 + mde) * 100:.2f}%（相対 {mde * 100:+.0f}%）を "
          f"有意水準 {a.alpha}（両側）・検出力 {a.power} で検出するには")
    print(f"1群あたり {n:,} 、合計 {2 * n:,}")
    if a.daily is None:
        print("期間: --daily（1日の流入、2群合計）を付けると日数を出す")
        return 0
    daily = parse_num(a.daily, "--daily")
    if daily == 0:
        raise InputError("--daily が 0")
    days = math.ceil(2 * n / daily)
    print(f"1日 {daily:,.1f} の流入で約 {days} 日" + ("（最低7日は回す）" if days < 7 else ""))
    if days > 56:
        print("判定: A/Bテスト不適（8週を超える）。次のどれかに切り替える:")
        print("- 上流の指標（クリック・申込開始・体験申込など母数の多い段）で判定する")
        print("- 差の大きい変更（相対 +30% 以上を狙える変更）に絞る")
        print("- before-after に回す（結論は既定で「判断できない」）")
    return days


def lchoose(n, k):
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)


def binom_test(x, n, p):
    """二項の正確検定（両側。観測以下の確率の和）。"""
    if n == 0:
        return 1.0
    if p <= 0:
        return 1.0 if x == 0 else 0.0
    if p >= 1:
        return 1.0 if x == n else 0.0
    lp = [lchoose(n, k) + k * math.log(p) + (n - k) * math.log(1 - p) for k in range(n + 1)]
    obs = lp[x]
    return min(1.0, sum(math.exp(v) for v in lp if v <= obs + 1e-7))


def fisher_test(xa, na, xb, nb):
    """Fisher の正確検定（両側）。表 [[xa, na-xa], [xb, nb-xb]]。"""
    xa, na, xb, nb = int(round(xa)), int(round(na)), int(round(xb)), int(round(nb))
    s, t = xa + xb, na + nb
    lo, hi = max(0, s - nb), min(s, na)
    lt = lchoose(t, s)
    lp = {k: lchoose(na, k) + lchoose(nb, s - k) - lt for k in range(lo, hi + 1)}
    obs = lp[xa]
    return min(1.0, sum(math.exp(v) for v in lp.values() if v <= obs + 1e-7))


def wilson(x, n, z):
    p = x / n
    c = (x + z * z / 2) / (n + z * z)
    h = z * math.sqrt(x * (n - x) / n + z * z / 4) / (n + z * z)
    return max(0.0, c - h), min(1.0, c + h), p


def newcombe(xa, na, xb, nb, alpha=0.05):
    """B − A の差の Newcombe（hybrid score）区間。"""
    z = Z.inv_cdf(1 - alpha / 2)
    la, ua, pa = wilson(xa, na, z)
    lb, ub, pb = wilson(xb, nb, z)
    d = pb - pa
    return d - math.sqrt((pb - lb) ** 2 + (ua - pa) ** 2), d + math.sqrt((ub - pb) ** 2 + (pa - la) ** 2)


def chi2_p1(chi2):
    return math.erfc(math.sqrt(chi2 / 2))


def srm(na, nb, share_a=0.5):
    t = na + nb
    ea, eb = t * share_a, t * (1 - share_a)
    chi2 = (na - ea) ** 2 / ea + (nb - eb) ** 2 / eb
    return chi2, chi2_p1(chi2)


def check_pair(n, x, name):
    if n <= 0:
        raise InputError(f"{name} の母数が 0")
    if x > n:
        raise InputError(f"{name} の件数 {x:,.0f} が母数 {n:,.0f} を超える（列の取り違えを確かめる）")


def judge(na, xa, nb, xb, alpha=0.05, planned=None, share_a=0.5, observational=False):
    check_pair(na, xa, "A")
    check_pair(nb, xb, "B")
    pa, pb = xa / na, xb / nb
    p = (xa + xb) / (na + nb)
    exp = [na * p, na * (1 - p), nb * p, nb * (1 - p)]
    if min(exp) < 5:
        method = "Fisher の正確検定（期待度数5未満）"
        pval = fisher_test(xa, na, xb, nb)
        z = None
    else:
        method = "両側 z 検定"
        se0 = math.sqrt(p * (1 - p) * (1 / na + 1 / nb))
        z = (pb - pa) / se0 if se0 else 0.0
        pval = 2 * (1 - Z.cdf(abs(z)))
    lo, hi = newcombe(xa, na, xb, nb, alpha)
    chi2, psrm = srm(na, nb, share_a)
    r = {"pa": pa, "pb": pb, "diff": pb - pa, "lift": (pb / pa - 1) if pa else float("nan"),
         "lo": lo, "hi": hi, "z": z, "p": pval, "method": method, "srm_chi2": chi2, "srm_p": psrm}
    if planned is not None and min(na, nb) < planned:
        r["verdict"] = f"途中経過・判定不可（事前に決めた1群 {planned:,.0f} に未達）"
    elif pval < alpha and lo > 0:
        r["verdict"] = "B が有意に高い"
    elif pval < alpha and hi < 0:
        r["verdict"] = "B が有意に低い"
    elif pval < alpha:
        r["verdict"] = "境界（p と区間が食い違う）。有意とは言わない"
    else:
        r["verdict"] = "有意差なし（差が無いとは言えない）"
    r["observational"] = observational
    if observational:
        r["verdict"] += "（観察比較: 群は無作為に分けていない。因果は未確認）"
    return r


def parse_pair(vals, name):
    if len(vals) != 2:
        raise InputError(f"{name} は「母数 件数」の2つ")
    return parse_num(vals[0], f"{name} の母数"), parse_num(vals[1], f"{name} の件数")


def cmd_ab_judge(a):
    na, xa = parse_pair(a.a, "--a")
    nb, xb = parse_pair(a.b, "--b")
    planned = parse_num(a.planned, "--planned") if a.planned else None
    share = parse_rate(a.ratio, "--ratio")
    r = judge(na, xa, nb, xb, a.alpha, planned, share, a.observational)
    flush_warnings()
    ci = int(round((1 - a.alpha) * 100))
    print(f"A: {xa:,.0f}/{na:,.0f} = {r['pa'] * 100:.2f}%   B: {xb:,.0f}/{nb:,.0f} = {r['pb'] * 100:.2f}%")
    zs = "" if r["z"] is None else f"z = {r['z']:.2f}、"
    lift = "A が 0 のため算出不可" if math.isnan(r['lift']) else f"{r['lift'] * 100:+.1f}%"
    print(f"差 {r['diff'] * 100:+.2f}pt（相対 {lift}）、{ci}% 区間（Newcombe） "
          f"[{r['lo'] * 100:+.2f}pt, {r['hi'] * 100:+.2f}pt]、{zs}p = {r['p']:.4f}（{r['method']}）")
    if r["observational"]:
        print("SRM 点検: 省略（観察比較。群の大きさは元から違ってよい）")
    else:
        print(f"SRM 点検: χ² = {r['srm_chi2']:.2f}、p {'< 0.0001' if r['srm_p'] < 1e-4 else '= ' + format(r['srm_p'], '.4f')}（想定の A の割合 {share:g}）")
    if not r["observational"] and r["srm_p"] < 0.001:
        print("警告: 振り分けの偏り（SRM）。振り分け・計測の不具合を直すまで結果を使わない")
    print(f"判定: {r['verdict']}")
    if planned is None:
        print("注: 事前に決めた標本数（--planned）が未指定。決めずに途中で見た結果なら暫定扱い")
    print("「効いた」と書けるのは、無作為の A/B で事前の標本数に届き、判定が「有意に高い」の場合だけ")


def parse_obs(vals, name):
    if vals is None:
        return None
    if len(vals) == 1:
        return (None, parse_num(vals[0], f"{name} の件数"))
    if len(vals) == 2:
        n, x = parse_pair(vals, name)
        check_pair(n, x, name)
        return (n, x)
    raise InputError(f"{name} は「件数」か「母数 件数」")


def before_after(before, after, ly_before=None, ly_after=None, alpha=0.05):
    rate_mode = before[0] is not None
    if (after[0] is not None) != rate_mode:
        raise InputError("--before と --after は同じ形（件数だけ／母数と件数）にする")
    has_ly = ly_before is not None and ly_after is not None
    if (ly_before is None) != (ly_after is None):
        raise InputError("前年同期は --ly-before と --ly-after を両方入れる")
    if has_ly and ((ly_before[0] is not None) != rate_mode or (ly_after[0] is not None) != rate_mode):
        raise InputError("前年同期も今年と同じ形にする")
    r = {"rate_mode": rate_mode, "has_ly": has_ly}
    if rate_mode:
        (nb_, xb), (na_, xa) = before, after
        r["v_before"], r["v_after"] = xb / nb_, xa / na_
        r["method"] = "Fisher の正確検定（率）"
        r["p"] = fisher_test(xb, nb_, xa, na_)
    else:
        xb, xa = before[1], after[1]
        if xb + xa == 0:
            raise InputError("前後とも 0 件。比べられない")
        r["v_before"], r["v_after"] = xb, xa
        r["method"] = "二項の正確検定（同じ長さの期間の件数）"
        r["p"] = binom_test(int(round(xa)), int(round(xa + xb)), 0.5)
    r["change"] = r["v_after"] - r["v_before"]
    if has_ly:
        if rate_mode:
            (lnb, lxb), (lna, lxa) = ly_before, ly_after
            r["ly_before"], r["ly_after"] = lxb / lnb, lxa / lna
            r["ly_change"] = r["ly_after"] - r["ly_before"]
            did = r["change"] - r["ly_change"]
            var = sum(p * (1 - p) / n for p, n in ((r["v_before"], nb_), (r["v_after"], na_),
                                                   (r["ly_before"], lnb), (r["ly_after"], lna)))
            r["season_method"] = "前年の変化との差（z 近似）"
            r["season_p"] = 2 * (1 - Z.cdf(abs(did) / math.sqrt(var))) if var > 0 else 1.0
            r["residual"] = did
        else:
            lxb, lxa = ly_before[1], ly_after[1]
            if lxb + lxa == 0:
                raise InputError("前年同期が前後とも 0 件。基準線にならない")
            r["ly_before"], r["ly_after"] = lxb, lxa
            r["ly_change"] = lxa - lxb
            r["season_method"] = "前後の比が前年と違うか（Fisher の正確検定）"
            r["season_p"] = fisher_test(xa, xa + xb, lxa, lxa + lxb)
            share, ly_share = xa / (xa + xb), lxa / (lxa + lxb)
            r["residual"] = share - ly_share
        same_dir = (r["residual"] > 0) == (r["change"] > 0) and r["residual"] != 0
        if r["p"] < alpha and r["season_p"] < alpha and same_dir:
            r["verdict"] = "差あり（因果は未確認）"
        else:
            r["verdict"] = "判断できない（前年同期と比べると差が残らない、または p ≥ 0.05）"
    else:
        r["verdict"] = ("判断できない（基準線が無い。前と後が別の年の同じ時期なら季節は揃うが、"
                        "その間の他の変化は切り分けられない）")
    return r


def cmd_before_after(a):
    b, af = parse_obs(a.before, "--before"), parse_obs(a.after, "--after")
    lb, la = parse_obs(a.ly_before, "--ly-before"), parse_obs(a.ly_after, "--ly-after")
    r = before_after(b, af, lb, la, a.alpha)
    flush_warnings()
    f = (lambda v: f"{v * 100:.2f}%") if r["rate_mode"] else (lambda v: f"{v:,.0f}")
    print("| | 前 | 後 | 変化 |")
    print("|---|---|---|---|")
    ch = (lambda v: f"{v * 100:+.2f}pt") if r["rate_mode"] else (lambda v: f"{v:+,.0f}")
    print(f"| 比べる期間 | {f(r['v_before'])} | {f(r['v_after'])} | {ch(r['change'])} |")
    if r["has_ly"]:
        print(f"| 基準線（それぞれの1年前） | {f(r['ly_before'])} | {f(r['ly_after'])} | {ch(r['ly_change'])} |")
    print(f"\n前後の差: p = {r['p']:.4f}（{r['method']}）")
    if r["has_ly"]:
        print(f"前年同期と比べた差: p = {r['season_p']:.4f}（{r['season_method']}）")
    print(f"結論: {r['verdict']}")
    print("同じ期間の他の変化（広告費・価格・チラシ・人員・新店）を並べて書く。「効いた」とは書かない")


# ---------- selftest ----------

def selftest():
    ok = True

    def check(name, got, want, tol=1e-6):
        nonlocal ok
        good = got is not None and abs(got - want) <= tol
        ok &= good
        gs = "None" if got is None else f"{got:.6g}"
        print(f"{'PASS' if good else 'FAIL'} {name}: {gs}（期待 {want} ± {tol}）")

    def truth(name, cond):
        nonlocal ok
        ok &= bool(cond)
        print(f"{'PASS' if cond else 'FAIL'} {name}")

    def raises(name, fn):
        nonlocal ok
        try:
            fn()
            good, msg = False, "止まらなかった"
        except InputError as e:
            good, msg = True, str(e)
        ok &= good
        print(f"{'PASS' if good else 'FAIL'} {name}: {msg}")

    def row(stage, count, target=None, cap=None, wait=None, src=""):
        return {"stage": stage, "count": count, "target": target, "target_src": src,
                "cap": cap, "wait": wait, "note": ""}

    print("[既知の答え]")
    check("ab-size base3% mde20% = 13914（現行と同じ）", sample_size(0.03, 0.2), 13914, 5)
    check("Fisher 紅茶の例 [[3,1],[1,3]] p = 0.4857", fisher_test(3, 4, 1, 4), 0.4857, 1e-4)
    check("Fisher 教科書の例 [[1,11],[9,3]] p = 0.002759", fisher_test(1, 12, 9, 12), 0.002759, 2e-6)
    lo, hi = newcombe(48, 80, 56, 70)
    check("Newcombe 56/70−48/80 下限 0.0524", lo, 0.0524, 1e-4)
    check("Newcombe 56/70−48/80 上限 0.3339", hi, 0.3339, 1e-4)
    check("二項の正確検定 9/10 p = 0.02148", binom_test(9, 10, 0.5), 0.021484, 1e-5)
    check("二項の正確検定 7/10 p = 0.3438", binom_test(7, 10, 0.5), 0.34375, 1e-5)
    r = judge(1000, 100, 1000, 130)
    check("ab-judge z 検定 z = 2.1027（現行と同じ）", r["z"], 2.1027, 1e-3)
    check("ab-judge z 検定 p = 0.0355（現行と同じ）", r["p"], 0.0355, 5e-4)
    chi2, p = srm(1000, 1200)
    check("SRM 1000 vs 1200 χ² = 18.18", chi2, 18.1818, 1e-3)
    check("χ²(1) = 3.8415 の p = 0.05", chi2_p1(3.841459), 0.05, 1e-5)

    print("[funnel・stock]")
    out = funnel_calc([row("訪問", 10000), row("申込開始", 500, 0.1), row("申込完了", 250, 0.5)])
    check("funnel rate 5%", out[1]["rate"], 0.05)
    check("funnel gain@target（5%→10% で倍）", out[1]["gain_target"], 250)
    check("funnel gain@target（目標達成済みは 0）", out[2]["gain_target"], 0)
    check("funnel gain@+10%（容量なしは全段 25）", out[2]["gain_10"], 25)
    check("期限時点の換算 g=100 c=0.05 m=12", deadline_gain(100, 0.05, 12), 100 * (1 - 0.95 ** 12) / 0.05)
    s = simulate(1000, 100, 0.05, 600)
    check("stock 定常状態へ収束", s[-1], 2000, 0.5)
    check("stock 1か月後", s[1], 1050)
    res, need = stock_calc(5766, 630, 0.084, 6, target=8000)
    check("stock 期限時点 そのまま 6476", res[0]["end"], 6476, 1)
    truth("stock 新規×1.2 と 解約率×0.8 を並べる", res[1]["name"] == "新規×1.2" and res[2]["name"] == "解約率×0.8")
    check("stock 目標に届く新規倍率で期限時点が目標", at_month(5766, 630 * need["new_x"], 0.084, 6), 8000, 0.01)
    check("定常の90%までの月数 c=0.05 → 45", months_to_90(0.05), 45)

    out = funnel_calc([row("訪問", 10000), row("申込開始", 500), row("申込完了", 250)], what_if={"申込開始": 0.06})
    check("funnel --set 仮説値 5%→6% で +50", out[1]["gain_set"], 50)
    s2 = simulate(100, 10, 0.1, 3, exits={2: 20})
    check("stock 予定された終了（2か月目に20件）", s2[2], 100 + 10 - 10 - 0 + 10 - 10 - 20, 1e-9)
    check("stock 上限（cap 105）で止まる", simulate(100, 30, 0.1, 5, cap=105)[-1], 105)
    res, need = stock_calc(100, 10, 0.1, 6, target=200, cap=150)
    truth("stock 上限が目標より小さければ新規では届かない", need["new_x"] is None)
    res, need = stock_calc(100, 10, 0.1, 12, target=110, exits={3: 15})
    check("stock 予定された終了つきで目標に届く新規倍率", at_month(100, 10 * need["new_x"], 0.1, 12, {3: 15}), 110, 0.01)
    check("stock 期間の延べ契約月数（各月初の合計）", stock_calc(100, 10, 0.1, 2)[0][0]["months_total"], 200)
    r = judge(300, 30, 900, 60, observational=True)
    truth("ab-judge 観察比較は SRM を省き因果未確認と出す", r["observational"] and "観察比較" in r["verdict"])

    print("[境界のケース]")
    raises("0件の段（先頭）で止まる", lambda: funnel_calc([row("訪問", 0), row("申込", 0)]))
    raises("0件の段（途中）で止まる", lambda: funnel_calc([row("訪問", 100), row("体験", 0), row("入会", 0)]))
    raises("0件の段（最終）で止まる", lambda: funnel_calc([row("訪問", 100), row("入会", 0)]))
    out = funnel_calc([row("問合せ", 40), row("体験", 50), row("入会", 25)])
    truth("前段より多い段（率100%超）で警告", any("前段より多い" in w for w in WARNINGS))
    del WARNINGS[:]
    out = funnel_calc([row("問合せ", 100), row("体験", 20, 0.3), row("入会", 12, 0.8)])
    truth("前段の母数 n<30 の印", out[2]["small"] and not out[1]["small"])
    truth("n<30 の段は順位に入らない", all("入会" not in n for _, n in funnel_ranking(out)))
    out = funnel_calc([row("問合せ", 100), row("体験", 40, 0.6, cap=40, wait=15), row("入会", 20, 0.6)])
    truth("容量: 上限到達の印", out[1]["at_cap"])
    check("容量: 上限到達の段は率を上げても増分0", out[1]["gain_target"], 0)
    check("容量: 待ち15を受けると入会 +7.5", out[1]["gain_cap"], 7.5)
    check("容量: 上流の率を上げても上限で増分0", funnel_calc(
        [row("訪問", 1000), row("問合せ", 100, 0.2), row("体験", 40, cap=40), row("入会", 20)])[1]["gain_target"], 0)
    truth("容量: 1位は「体験の枠を増やす」", funnel_ranking(out)[0][1] == "体験の枠を増やす")
    check("合算月数で割る（3か月合算の +7.5 → 月 +2.5）", funnel_calc(
        [row("問合せ", 100), row("体験", 40, 0.6, cap=40, wait=15), row("入会", 20, 0.6)], pooled=3)[1]["gain_cap"], 2.5)
    out = funnel_calc([row("訪問", 1000), row("トライアル", 100, 0.12), row("有料", 40), row("継続2か月", 30, 0.9)],
                      contract="有料")
    check("契約後の段は残る件数で換算（30→36 で +6）", out[3]["gain_target"], 6, 1e-9)
    check("契約前の段は契約の段で測る（40→48 で +8）", out[1]["gain_target"], 8, 1e-9)
    check("桁区切り 1,200 → 1200", parse_num("1,200"), 1200)
    check("全角 １６ → 16", parse_num("１６"), 16)
    raises("空欄は 0 と見なさず止まる", lambda: parse_num("", "count"))
    raises("「不明」は 0 と見なさず止まる", lambda: parse_num("不明", "count"))
    raises("人数の欄の 75% で止まる", lambda: parse_num("75%", "体験"))
    check("% 入力 30% → 0.30", parse_rate("30%"), 0.30)
    check("% 入力 30 → 0.30", parse_rate("30"), 0.30)
    truth("% 入力で警告", len(WARNINGS) == 2)
    del WARNINGS[:]
    raises("率 150 で止まる", lambda: parse_rate("150"))
    raises("active 0 で止まる", lambda: stock_calc(0, 100, 0.05, 12))
    raises("解約率が負で止まる", lambda: stock_calc(100, 10, -0.1, 12))
    raises("期間が負で止まる", lambda: stock_calc(100, 10, 0.1, -1))
    raises("mde 0 で止まる", lambda: sample_size(0.03, 0))
    r = judge(50, 50, 50, 45)
    truth("全件成約（50/50）でも落ちず Fisher に切り替え", r["method"].startswith("Fisher"))
    truth("全件成約の区間が -1〜1 の中", -1 <= r["lo"] <= r["hi"] <= 1)
    r = judge(20, 1, 20, 6)
    truth("小母数（期待度数<5）は Fisher", r["method"].startswith("Fisher"))
    r = judge(1000, 30, 1300, 60)
    truth("群の大きさの偏りで SRM 警告（p<0.001）", r["srm_p"] < 0.001)
    r = judge(500, 20, 500, 40, planned=3000)
    truth("事前の標本数に未達は「途中経過・判定不可」", r["verdict"].startswith("途中経過"))
    raises("件数 > 母数で止まる", lambda: judge(100, 120, 100, 10))
    r = before_after((None, 30), (None, 50))
    truth("before-after 前年同期なしは「判断できない」", r["verdict"].startswith("判断できない"))
    r = before_after((None, 30), (None, 70), (None, 30), (None, 30))
    truth("before-after 前年を超える差かつ p<0.05 は「差あり（因果は未確認）」", r["verdict"].startswith("差あり"))
    r = before_after((None, 30), (None, 60), (None, 30), (None, 33))
    truth("before-after 前後 p<0.05 でも前年比の差が p≥0.05 なら「判断できない」", r["verdict"].startswith("判断できない"))
    r = before_after((None, 30), (None, 60), (None, 30), (None, 58))
    truth("before-after 前年も同じだけ増えたら「判断できない」", r["verdict"].startswith("判断できない"))
    r = before_after((18, 11), (41, 20))
    truth("before-after 小母数の率は Fisher・「判断できない」", r["method"].startswith("Fisher") and
          r["verdict"].startswith("判断できない"))
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="契約数の詰まりと効果を計算する")
    sp = ap.add_subparsers(dest="cmd")
    p = sp.add_parser("funnel")
    p.add_argument("csv")
    p.add_argument("--contract", help="契約の段の名前（既定は最終段）")
    p.add_argument("--churn", help="月次解約率（期限時点への換算用）")
    p.add_argument("--months", help="期限までの月数（期限時点への換算用）")
    p.add_argument("--pooled", default="1", help="CSV が何か月分の合算か（増分を月あたりに直す）")
    p.add_argument("--set", action="append", help="仮説値: 段名=率（複数可）。その段だけを仮説値にしたときの増分を出す")
    p = sp.add_parser("stock")
    p.add_argument("--active", required=True)
    p.add_argument("--new", required=True)
    p.add_argument("--churn", required=True)
    p.add_argument("--months", default="12")
    p.add_argument("--new-x")
    p.add_argument("--churn-x")
    p.add_argument("--target")
    p.add_argument("--arpu")
    p.add_argument("--cap", help="有効契約数の上限（定員・枠）")
    p.add_argument("--exit", action="append", help="予定された終了: 月=件数（卒業・満期。複数可）")
    p = sp.add_parser("ab-size")
    p.add_argument("--base", required=True)
    p.add_argument("--mde", required=True)
    p.add_argument("--alpha", type=float, default=0.05)
    p.add_argument("--power", type=float, default=0.8)
    p.add_argument("--daily", help="1日の流入（2群合計）")
    p = sp.add_parser("ab-judge")
    p.add_argument("--a", nargs=2, required=True, metavar=("N", "X"))
    p.add_argument("--b", nargs=2, required=True, metavar=("N", "X"))
    p.add_argument("--planned", help="事前に決めた1群の標本数")
    p.add_argument("--ratio", default="0.5", help="想定の A の割合（SRM 点検用）")
    p.add_argument("--observational", action="store_true",
                   help="無作為でない群の比較（導入支援の有無など）。SRM を省き、因果は未確認と出す")
    p.add_argument("--alpha", type=float, default=0.05)
    p = sp.add_parser("before-after")
    p.add_argument("--before", nargs="+", required=True)
    p.add_argument("--after", nargs="+", required=True)
    p.add_argument("--ly-before", nargs="+")
    p.add_argument("--ly-after", nargs="+")
    p.add_argument("--alpha", type=float, default=0.05)
    sp.add_parser("selftest")
    a = ap.parse_args()
    if not a.cmd:
        ap.print_help()
        sys.exit(2)
    if a.cmd == "selftest":
        sys.exit(selftest())
    try:
        {"funnel": cmd_funnel, "stock": cmd_stock, "ab-size": cmd_ab_size, "ab-judge": cmd_ab_judge,
         "before-after": cmd_before_after}[a.cmd](a)
    except InputError as e:
        flush_warnings()
        print(f"停止: {e}")
        sys.exit(2)


if __name__ == "__main__":
    main()
