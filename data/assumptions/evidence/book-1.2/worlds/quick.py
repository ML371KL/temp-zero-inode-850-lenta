# -*- coding: utf-8 -*-
"""Быстрые числа книги 1.2 «Ленты» (миры ставок после ЦБ 23.10.2026) — до медленного круга книги.

Что считается для книги 1.1 (main 62f858a) и книги 1.2 (запись миров 28.10.2026), ядром проекта:
  * точка сетки (низ = мир M, верх = смесь 35/45/20, точка = низ + λ·(верх − низ)) и цены миров N/H/M
    (`model.grid.world_price`) — на входах книги (дата 18.09.2026, цена 1 619,5 ₽) и на входах выпуска 26.10.2026
    (цена 1 830 ₽, дата оценки 26.10 — выпуск 5c22b0e2fddd, как у ведущего в анализе 27.10; `refresh_worlds.at_release`);
  * медиана малой полосы A-V9: DRAWS прогонов с зерном книги — одни и те же случайные числа у всех вариантов.
Справочные варианты на записи 1.2 (меняется один вход записи, якоря — выход правил, как в записи):
  (а) рыночный снимок — один день 28.10; (б) окно 10 дней 15–28.10 (оба — блок окна листа книги-источника
  `window_block.json`, путь — аргумент --window); (в) мир H по проекту ОНДКП 30.09 вместо итоговой 28.10;
  (г) продовольствие текущего полугодия без факта (спред мира, как до второго захода записи).

Запуск из корня репозитория (сеть не нужна):
  LENTA_WORKERS=2 python -B data/assumptions/evidence/book-1.2/worlds/quick.py [--draws 500] [--window <window_block.json>]
Пишет рядом quick_out.json и quick_out.txt.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
sys.path.insert(0, str(ROOT))

BOOK = ROOT / "data" / "assumptions"
BASE_COMMIT = "62f858a"                       # main, книга 1.1
RELEASE_2610 = {"market": {"price": 1830.0}, "meta": {"valuation_date": "2026-10-26"}}
DRAFT_PRO = {"key_avg": {"2027": [13.0, 15.0], "2028": [11.0, 12.0], "2029": [8.5, 9.5]},
             "cpi_eop": {"2027": [4.5, 5.5], "2028": 4.0, "2029": 4.0},
             "cpi_avg": {"2027": [4.6, 5.9], "2028": [4.0, 4.5], "2029": 4.0}}
FIELDS = ("key", "cpi_eop", "cpi_avg", "food_avg", "wage")


def _module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    was, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = was
    return mod


def by_rules(R, inp: dict) -> dict:
    """Якоря записи = выход правил (как в записи 1.7 книги-источника): N, H целиком; M — ключевая и текущее полугодие."""
    x = copy.deepcopy(inp)
    A, _ = R.candidate_anchors(x)
    P = R.periods_of(x["grid"]["first_period"], int(x["grid"]["n_periods"]))
    for w in ("N", "H"):
        x["worlds"][w]["anchors"] = {f: {p: A[w][f][p] for p in P if p in A[w][f]} for f in FIELDS}
    M = x["worlds"]["M"]
    M["anchors"]["key"] = {p: A["M"]["key"][p] for p in P if p in A["M"]["key"]}
    M["steady"]["key"] = A["M"]["key"]["steady"]
    fields = ["cpi_eop", "cpi_avg"]
    if (x.get("cpi_actual") or {}).get("food_spread"):
        fields.append("food_avg")
    if x.get("wage_actual"):
        fields.append("wage")
    for f in fields:
        M["anchors"][f][P[0]] = A["M"][f][P[0]]
    return x


def with_snapshot(R, inp: dict, block: dict) -> dict:
    x = copy.deepcopy(inp)
    x["curve"] = {"source": f"справочный вариант: {', '.join(block['days'])}", "date": block["days"][-1],
                  "nodes": dict(block["nodes"])}
    x["ofz_in"].pop("window", None)
    x["ofz_in"]["date"], x["ofz_in"]["settlement_date"] = block["days"][-1], R.next_weekday(block["days"][-1])
    x["ofz_in"]["bonds"] = [dict(b, real_yield=block["yields"][str(b["secid"])]) for b in x["ofz_in"]["bonds"]]
    return by_rules(R, x)


def evaluate(A: dict, draws: int, tool) -> dict:
    from model.engine import run_release
    from model.grid import world_price
    from model.uncertainty import headline_of, uncertainty

    out = {}
    for name, book in (("book", A), ("release_2610", tool.at_release(A, RELEASE_2610))):
        rel = run_release(copy.deepcopy(book), gates=False)
        fv = rel.fair_value
        out[name] = {"low": fv.low, "central": fv.central, "high": fv.high,
                     "worlds": {w: world_price(book, rel.cells, w) for w in ("N", "H", "M")}}
        if name == "book" and draws:
            h = headline_of(book, fv, uncertainty(copy.deepcopy(book), draws=draws))
            out[name]["median"], out[name]["printed_median"] = h["central"], h["printed_central"]
            out[name]["band80"], out[name]["draws"] = h["band"], h["draws"]
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=500)
    ap.add_argument("--window", type=Path, default=None, help="window_block.json листа книги-источника")
    args = ap.parse_args()
    import yaml
    from model.book import BOOK_YAML, load_book
    from model.uncertainty import close_pool

    draws = args.draws
    R = _module(BOOK / "worlds_recipe.py", "worlds_recipe")
    tool = _module(ROOT / "ops" / "tools" / "refresh_worlds.py", "refresh_worlds")
    rec = yaml.safe_load((BOOK / "worlds_inputs.yaml").read_text(encoding="utf-8"))
    assert by_rules(R, rec)["worlds"] == rec["worlds"], "запись — не выход правил"

    draft = copy.deepcopy(rec)
    draft["ondkp"]["proinflation"] = copy.deepcopy(DRAFT_PRO)
    nofood = copy.deepcopy(rec)
    nofood["cpi_actual"].pop("food_spread")
    variants = {"1.2": rec}
    if args.window:
        win = json.loads(args.window.read_text(encoding="utf-8"))
        variants["а: один день 28.10"] = with_snapshot(R, rec, win["one_day"])
        variants["б: окно 10 дней 15–28.10"] = with_snapshot(R, rec, win["ten_days"])
    variants["в: H по проекту ОНДКП 30.09"] = by_rules(R, draft)
    variants["г: продовольствие 2П26 без факта"] = by_rules(R, nofood)

    res, meta = {}, {}
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "v11.yaml"
        base.write_bytes(subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE_COMMIT}:data/assumptions/assumptions.yaml"],
                                        capture_output=True, check=True).stdout)
        res["1.1"] = evaluate(load_book(base), draws, tool)
        meta["1.1"] = {"pi_ss": 9.8, "curve_date": "2026-09-18"}
        print("1.1:", json.dumps(res["1.1"], ensure_ascii=False), flush=True)
        for name, inp in variants.items():
            ws = R.build_worlds(inp)
            if name == "1.2":
                assert ws == json.loads((BOOK / "worlds_source.json").read_text(encoding="utf-8"))
                A = load_book(BOOK_YAML)
                assert A["meta"]["curve_as_of"] == inp["curve"]["date"]
            else:
                d = Path(tmp) / f"v{len(res)}"
                d.mkdir()
                A = tool.build_book(ws, d)
                A["meta"]["curve_as_of"] = inp["curve"]["date"]
            res[name] = evaluate(A, draws, tool)
            meta[name] = {"pi_ss": ws["m_inflation"]["pi_ss"], "curve_date": inp["curve"]["date"],
                          "key_2027": {w: round(sum(r["key_avg"] for r in ws["rows"]
                                                    if r["world"] == w and r["period"].startswith("2027")) / 2, 3)
                                       for w in ("N", "H", "M")},
                          "curve_lt": ws.get("curve_lt")}
            print(f"{name}:", json.dumps(res[name], ensure_ascii=False), flush=True)
    close_pool()
    L = [f"Быстрые числа «Ленты»: книга 1.1 и книга 1.2 (ядро; полоса — {draws} прогонов, зерно книги)", "",
         "| вариант | π_ss M | точка (выпуск 26.10) | низ / верх (выпуск) | N / H / M (выпуск) | точка (книга) | "
         "низ / верх (книга) | N / H / M (книга) | медиана (книга) | Δ точки выпуска к 1.2 | Δ медианы к 1.2 |",
         "|---|---:|---:|---|---|---:|---|---|---:|---:|---:|"]
    ref = res["1.2"]
    for name, r in res.items():
        b, x = r["book"], r["release_2610"]
        L.append(f"| {name} | {meta[name]['pi_ss']} | {x['central']:.1f} | {x['low']:.1f} / {x['high']:.1f} | "
                 + " / ".join(f"{x['worlds'][w]:.0f}" for w in "NHM") + f" | {b['central']:.1f} | {b['low']:.1f} / {b['high']:.1f} | "
                 + " / ".join(f"{b['worlds'][w]:.0f}" for w in "NHM")
                 + f" | {b.get('median', float('nan')):.1f} (≈{b.get('printed_median', 0):.0f}) | "
                 f"{x['central'] - ref['release_2610']['central']:+.1f} | {b.get('median', 0) - ref['book'].get('median', 0):+.1f} |")
    text = "\n".join(L) + "\n"
    print(text)
    (HERE / "quick_out.txt").write_text(text, encoding="utf-8", newline="\n")
    (HERE / "quick_out.json").write_text(json.dumps({"draws": draws, "results": res, "meta": meta}, ensure_ascii=False, indent=1),
                                         encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
