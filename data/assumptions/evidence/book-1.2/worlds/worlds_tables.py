# -*- coding: utf-8 -*-
"""Таблицы раздела 2 книги 1.2 «Ленты» на записи миров 28.10.2026 — рядом с записью 18.09.2026 (книга 1.1).

Что печатает (как раздел 5 листа `book-1.0/worlds-calendar/worlds_check.py`, на двух записях):
  * годовые ключевая / средний ИПЦ / продовольствие / зарплаты / ОФЗ 10 лет по мирам (среднее полугодий записи);
  * справедливые кривые сегодня 1/3/5/10 лет и уровень кривой в терминале машинной книги (`zero_curve.LT`);
  * контроль A-P1m: 10-летняя доходность смесей весов против рыночной;
  * реальная r_u в терминале = (1 + LT + β_u·ERP)/(1 + π_LT) − 1 при β_u книги и сетке β_u.
Запись 18.09.2026 и машинная книга 1.1 — из git (коммит 62f858a), запись 28.10.2026 и книга 1.2 — из каталога книги.

Запуск из корня репозитория: python -B data/assumptions/evidence/book-1.2/worlds/worlds_tables.py
Пишет рядом worlds_tables_out.json и worlds_tables_out.txt.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
BOOK = ROOT / "data" / "assumptions"
BASE_COMMIT = "62f858a"
COLS = {"2П2026": ["2026H2"], "2027": ["2027H1", "2027H2"], "2028": ["2028H1", "2028H2"], "2029": ["2029H1", "2029H2"],
        "2030": ["2030H1", "2030H2"], "2031–33": [f"{y}H{h}" for y in (2031, 2032, 2033) for h in (1, 2)],
        "2034–36": [f"{y}H{h}" for y in (2034, 2035, 2036) for h in (1, 2)],
        "2031–36": [f"{y}H{h}" for y in range(2031, 2037) for h in (1, 2)]}
FIELDS = {"key": "key_avg", "cpi_avg": "cpi_yoy_avg", "food_avg": "food_cpi_yoy_avg", "wage": "nominal_wage_yoy",
          "ofz_10y": "ofz_10y", "cpi_eop": "cpi_yoy_eop"}
BETA_GRID = (0.52, 0.55, 0.60)


def git_show(path: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:{path}"], capture_output=True,
                          check=True).stdout.decode("utf-8")


def tables(src: dict, book: dict) -> dict:
    out = {"annual": {}}
    for w in ("N", "H", "M"):
        by_p = {r["period"]: r for r in src["rows"] if r["world"] == w}
        out["annual"][w] = {f: {c: round(sum(by_p[p][sf] for p in ps) / len(ps), 3) for c, ps in COLS.items()}
                            for f, sf in FIELDS.items()}
    out["fair_zero_curve_today"] = src["fair_zero_curve_today"]
    out["curve_lt"] = {w: book["worlds"][w]["zero_curve"]["LT"] for w in ("N", "H", "M")}
    out["lt_inflation"] = {w: book["worlds"][w]["lt"]["inflation"] for w in ("N", "H", "M")}
    J = book["joint"]
    node = {w: src["fair_zero_curve_today"][w]["10"] / 100.0 for w in ("N", "H", "M")}
    market = node[J["macro_neutral_world"]]
    imp = {"market_10y": market, "fair_10y_by_world": node}
    for name, weights in (("analytical", J["world_prob"]), ("market_implied", J["world_prob_market_implied"])):
        imp[name] = {"linear": sum(weights[w] * node[w] for w in weights),
                     "discount_factor_mix": sum(weights[w] * (1 + node[w]) ** -10 for w in weights) ** (-1 / 10) - 1}
    out["market_implied_10y"] = imp
    erp = float(book["valuation"]["erp"]) if "erp" in book["valuation"] else None
    out["erp"] = erp
    out["beta_u_book"] = book["valuation"].get("beta_u")
    if erp is not None:
        grid = sorted(set(BETA_GRID) | ({float(out["beta_u_book"])} if out["beta_u_book"] else set()))
        out["terminal_real_r_u_pct"] = {
            f"{b:.2f}": {w: round(((1 + out["curve_lt"][w] + b * erp) / (1 + out["lt_inflation"][w]) - 1) * 100, 3)
                         for w in ("N", "H", "M")} for b in grid}
    out["m_inflation"] = {k: v for k, v in src["m_inflation"].items() if not isinstance(v, (list, dict))}
    return out


def fmt(x: float, d: int = 2) -> str:
    s = f"{x:.{d}f}".rstrip("0").rstrip(".") if d else f"{x:.0f}"
    return s.replace(".", ",")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    old = tables(json.loads(git_show("data/assumptions/worlds_source.json")),
                 yaml.safe_load(git_show("data/assumptions/assumptions.yaml")))
    new = tables(json.loads((BOOK / "worlds_source.json").read_text(encoding="utf-8")),
                 yaml.safe_load((BOOK / "assumptions.yaml").read_text(encoding="utf-8")))
    L = ["Таблицы раздела 2: запись 18.09.2026 (книга 1.1) → запись 28.10.2026 (книга 1.2)", ""]
    for w in ("N", "H", "M"):
        L.append(f"## мир {w}")
        L.append("| поле | " + " | ".join(COLS) + " |")
        L.append("|---|" + "---:|" * len(COLS))
        for f in FIELDS:
            L.append(f"| {f} | " + " | ".join(f"{fmt(old['annual'][w][f][c])} → {fmt(new['annual'][w][f][c])}"
                                              for c in COLS) + " |")
        L.append("")
    L.append("| мир | справедливая кривая 1/3/5/10, % | LT книги | π_LT |")
    L.append("|---|---|---:|---:|")
    for w in ("N", "H", "M"):
        o, n = old["fair_zero_curve_today"][w], new["fair_zero_curve_today"][w]
        L.append(f"| {w} | " + " / ".join(f"{fmt(o[k])} → {fmt(n[k])}" for k in ("1", "3", "5", "10"))
                 + f" | {fmt(old['curve_lt'][w] * 100, 1)} → {fmt(new['curve_lt'][w] * 100, 1)}"
                 + f" | {fmt(old['lt_inflation'][w] * 100, 1)} → {fmt(new['lt_inflation'][w] * 100, 1)} |")
    L.append("")
    for name in ("analytical", "market_implied"):
        o, n = old["market_implied_10y"][name], new["market_implied_10y"][name]
        L.append(f"A-P1m {name}: линейно {o['linear'] * 100:.2f} → {n['linear'] * 100:.2f} %, смесь DF "
                 f"{o['discount_factor_mix'] * 100:.2f} → {n['discount_factor_mix'] * 100:.2f} % "
                 f"(рынок {old['market_implied_10y']['market_10y'] * 100:.2f} → {new['market_implied_10y']['market_10y'] * 100:.2f} %)")
    if new.get("terminal_real_r_u_pct"):
        L.append("")
        L.append(f"Реальная r_u в терминале, % (ERP {new['erp']}, β_u книги {new['beta_u_book']}):")
        for b, row in new["terminal_real_r_u_pct"].items():
            orow = old["terminal_real_r_u_pct"].get(b, {})
            L.append(f"  β_u {b}: " + " · ".join(f"{w} {orow.get(w, float('nan')):.2f} → {row[w]:.2f}" for w in ("N", "H", "M")))
    text = "\n".join(L) + "\n"
    print(text)
    (HERE / "worlds_tables_out.txt").write_text(text, encoding="utf-8", newline="\n")
    (HERE / "worlds_tables_out.json").write_text(json.dumps({"book_1_1": old, "book_1_2": new}, ensure_ascii=False, indent=1),
                                                 encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
