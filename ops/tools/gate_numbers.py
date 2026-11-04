# -*- coding: utf-8 -*-
"""Числа объяснений гейтов книги «Ленты», которых нет полем в таблицах: составы клеток гейтов и их величины на дату
книги, массы и даты дрейфа по развёртке (`evidence/book-<версия>/gates/out/gate_sweep.json`), V0/D слоёв (`results.json`).

    python -B ops/tools/gate_numbers.py [--out data/assumptions/gate_numbers.json]

Слова объяснений — шаблон `data/assumptions/gate_explanations.tpl` (пишет ведущий); файл объяснений из шаблона пишет
команда приёма миров (`ops/tools/adopt_worlds.py`, шаг `gate-texts`), числа — метками из этого выхода, таблиц и книги.
Ядро (`model/`) не трогается: выход — счёт по развёртке и таблицам. Блокирующая находка развёртки (инвариант, гейт без
объяснения на какой-нибудь дате) — код 1: это суждение ведущего, приём останавливается.
Масса записи — масса гейта на дату книги, коридор дрейфа — минимум и максимум развёртки (4 знака).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
BOOK_DIR = ROOT / "data" / "assumptions"
OUT = BOOK_DIR / "gate_numbers.json"
ND = 4                                   # знаков массы (как печатает сводка развёртки)
FLOOR_PARTIAL = ("floor", "partial")     # режимы, на которые откалиброван коридор EV/EBITDA


def sweep_path(version: str) -> Path:
    return BOOK_DIR / "evidence" / f"book-{version}" / "gates" / "out" / "gate_sweep.json"


def parts(label: str) -> tuple[str, str, str]:
    w, r, c = label.split("|")
    return w, r, c


def span(xs: list[float]) -> list[float]:
    return [min(xs), max(xs)]


def first_day(days: list[dict], pred) -> str | None:
    return next((d["day"] for d in days if pred(d)), None)


def numbers(version: str) -> dict:
    sw = json.loads(sweep_path(version).read_text(encoding="utf-8"))
    res = json.loads((BOOK_DIR / "results.json").read_text(encoding="utf-8"))
    book = yaml.safe_load((BOOK_DIR / "assumptions.yaml").read_text(encoding="utf-8"))
    at, cells, days = sw["book_date"]["gates"], sw["book_date"]["cells"], sw["by_day"]
    out: dict = {"book_version": str(version), "sweep": {"first_day": days[0]["day"], "last_day": days[-1]["day"],
                                                         "days": len(days)}, "masses": {},
                 "fired": sorted(at),                                         # гейты, сработавшие на дату книги
                 "fired_any": sorted({n for d in days for n in d["gates"]}),       # гейты, сработавшие хоть на одной дате
                 "r_real": {w: next(c["r_real"] for c in cells.values() if c["world"] == w) for w in ("N", "H", "M")}}
    out["cells"] = {k: {f: c[f] for f in ("probability", "ev_ebitda_ltm", "terminal_share", "equity", "r_real", "cover_min")}
                    for k, c in cells.items()}
    eq = min(cells, key=lambda k: cells[k]["equity"])
    cv = min(cells, key=lambda k: cells[k]["cover_min"])
    out["equity_min"] = {"value": cells[eq]["equity"], "cell": eq}
    out["cover_min"] = {"value": cells[cv]["cover_min"], "cell": cv}
    for name, g in at.items():
        masses = [d["gates"].get(name, {}).get("mass", 0.0) for d in days]
        out["masses"][name] = {"mass": round(g["mass"], ND), "range": [round(min(masses), ND), round(max(masses), ND)]}

    # EV/EBITDA LTM: клетки «дна» и «частичной сходимости» за коридором и в нём, крайние даты дрейфа массы
    ev = at.get("ev_ebitda")
    if ev:
        fp = sorted(k for k, c in cells.items() if c["regime"] in FLOOR_PARTIAL)
        bad = sorted(ev["labels"])
        mm = [d["gates"].get("ev_ebitda", {}).get("mass", 0.0) for d in days]
        hi, lo = max(mm), min(mm)
        lo_day = next(d for d in days if d["gates"].get("ev_ebitda", {}).get("mass", 0.0) == lo)
        hi_day = next(d for d in days if d["gates"].get("ev_ebitda", {}).get("mass", 0.0) == hi)
        out["ev_ebitda"] = {
            "cells": bad, "count": len(bad), "of": len(fp), "inside": [k for k in fp if k not in bad],
            "span": {w: span([cells[k]["ev_ebitda_ltm"] for k in bad if parts(k)[0] == w])
                     for w in ("N", "H", "M") if any(parts(k)[0] == w for k in bad)},
            "max_mass": hi, "max_from": hi_day["day"], "max_count": hi_day["gates"]["ev_ebitda"]["cells"],
            "max_added": sorted(hi_day["gates"]["ev_ebitda"].get("added") or []),
            "min_mass": lo, "min_day": lo_day["day"], "min_count": lo_day["gates"]["ev_ebitda"]["cells"],
            "min_days": sum(1 for x in mm if x == lo), "min_removed": sorted(lo_day["gates"]["ev_ebitda"].get("removed") or []),
        }
    # доля терминала: клетки за потолком, «дно» и «частичная» того же мира, дрейф, минимум сетки
    ts = at.get("terminal_share")
    if ts:
        bad = sorted(ts["labels"])
        worlds = sorted({parts(k)[0] for k in bad})
        mm = [d["gates"].get("terminal_share", {}).get("mass", 0.0) for d in days]
        hi = max(mm)
        hi_days = [d for d in days if d["gates"].get("terminal_share", {}).get("mass", 0.0) == hi]
        low = min(cells, key=lambda k: cells[k]["terminal_share"])
        out["terminal_share"] = {
            "cells": bad, "count": len(bad), "span": span([cells[k]["terminal_share"] for k in bad]),
            "worlds": worlds,
            "floor_partial": span([cells[k]["terminal_share"] for k in cells
                                   if parts(k)[0] in worlds and cells[k]["regime"] in FLOOR_PARTIAL]),
            "drift": {"from": hi_days[0]["day"], "to": hi_days[-1]["day"], "mass": hi,
                      "count": hi_days[0]["gates"]["terminal_share"]["cells"],
                      "added": sorted(hi_days[0]["gates"]["terminal_share"].get("added") or [])},
            "min_cell": low, "min": cells[low]["terminal_share"],
        }
    # capex и маржа полугодий по сетке: крайние клетки и полугодия
    cx = [(v, p, k) for k, c in cells.items() for v, p in c["capex_pct"]]
    lo, hi = min(cx), max(cx)
    out["capex_range"] = {"min": lo[0], "min_period": lo[1], "min_cell": lo[2], "max": hi[0], "max_period": hi[1],
                          "max_cell": hi[2]}
    mg = [(v, p, k) for k, c in cells.items() for v, p in c["margin"]]
    target = book["margin"]["regimes"]

    def edge(t: tuple) -> dict:
        v, p, k = t
        ties = sorted({"|".join(parts(x)[:2]) for vv, pp, x in mg if abs(vv - v) <= 1e-12 and pp == p})
        reg = parts(k)[1]
        year = p[:4]
        tgt = target[reg]["target"].get(year, target[reg]["target"]["LT"])
        return {"value": v, "period": p, "cell": ties[0] if len(ties) == 1 else ties, "target": tgt}
    out["margin_range"] = {"min": edge(min(mg)), "max": edge(max(mg))}
    # ограниченная ответственность: V0, D и V0/D слоёв на точке
    lay = res["layers"]
    out["limited_liability"] = {"v0": span([x["v0"] for x in lay.values()]), "d": span([x["d"] for x in lay.values()]),
                                "v0_to_d": span([x["v0"] / x["d"] for x in lay.values()])}
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    version = str(yaml.safe_load((BOOK_DIR / "assumptions_template.yaml").read_text(encoding="utf-8"))["meta"]["version"])
    summary = sweep_path(version).with_suffix(".txt").read_text(encoding="utf-8")
    line = next((ln for ln in summary.splitlines() if ln.startswith("Блокирующие находки")), "")
    if not line.endswith("нет ни на одной дате."):
        print(f"ОТКАЗ: развёртка гейтов книги {version}: {line or 'нет строки блокирующих находок'} — суждение ведущего")
        return 1
    n = numbers(version)
    text = json.dumps(n, ensure_ascii=False, indent=1) + "\n"
    a.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"ИТОГ: числа объяснений гейтов книги {version} — {a.out.relative_to(ROOT).as_posix()} "
          f"(гейты на дату книги: {', '.join(sorted(n['masses']))})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
