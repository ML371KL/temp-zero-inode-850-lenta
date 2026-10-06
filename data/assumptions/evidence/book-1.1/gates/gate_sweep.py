# -*- coding: utf-8 -*-
"""Развёртка масс гейтов по датам оценки: числа `gate_explanations.yaml`.

Книга не меняется, дата оценки — каждый день от даты книги до последнего срока
объяснений (включительно), кривые — на дату книги (`meta.curve_as_of`), как в
выпуске такта на книжных входах. На каждой дате — выпуск ядра (36 клеток, гейты
на всех) и то, чем объяснения гейтов оперируют:

* масса и число клеток каждого сработавшего гейта;
* EV/EBITDA LTM клеток режимов «дно» и «частичная сходимость» по мирам;
* доля терминала в EV по клеткам; минимум капитала и покрытия процентов;
* размах маржи и capex/выручка полугодий по сетке.

Запуск (из корня репозитория; ≈286 выпусков, несколько минут на 8 ядрах):

    python -B data/assumptions/evidence/book-1.1/gates/gate_sweep.py

Пишет `out/gate_sweep.json` (по датам) и `out/gate_sweep.txt` (сводка: диапазоны
масс, даты смены состава клеток, крайние значения). Сроки объяснений читаются
из `gate_explanations.yaml`; массы в него вносятся руками по сводке, сверка —
`tests/test_gates_and_blocking.py` (опорные даты развёртки) и сборка выпуска.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
import multiprocessing as mp
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = HERE / "out"
_BOOK = None


def _book():
    global _BOOK
    if _BOOK is None:
        from model.book import book
        _BOOK = book()
    return _BOOK


def one_day(day_iso: str) -> dict:
    """Выпуск на дату оценки `day_iso`: гейты и величины их объяснений."""
    from model.checks import EV_EBITDA_REGIMES, RELEASE_LABEL, load_gate_explanations, summarize_gates
    from model.engine import run_release

    A0 = _book()
    A = copy.deepcopy(A0)
    A["meta"]["valuation_date"] = day_iso
    A["meta"].setdefault("curve_as_of", A0["meta"]["valuation_date"])
    release = run_release(A)
    mass = {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0}
    explanations = load_gate_explanations(today=dt.date.fromisoformat(day_iso))
    gates = {}
    for g in summarize_gates(release.findings, mass, explanations):
        labels = sorted({f.label for f in release.findings if f.key == g.key})
        gates[g.key] = {"mass": g.mass, "cells": g.cells, "labels": labels,
                        "mass_mismatch": bool(g.mass_mismatch), "advisory": bool(g.advisory)}
    cells = {}
    for c in release.cells:
        r = c.result
        cells[c.cell.key] = {
            "world": c.cell.world, "regime": c.cell.margin_regime, "capex": c.cell.capex,
            "probability": c.probability,
            "ev_ebitda_ltm": r.ev_ebitda_ltm if c.cell.margin_regime in EV_EBITDA_REGIMES else None,
            "terminal_share": r.terminal_share,
            "equity": r.equity,
            "r_real": r.r_real,
            "cover_min": min(row.interest_cover for row in r.rows),
            "margin": [min((row.margin, row.period) for row in r.rows),
                       max((row.margin, row.period) for row in r.rows)],
            "capex_pct": [min((row.capex_pct, row.period) for row in r.rows),
                          max((row.capex_pct, row.period) for row in r.rows)],
        }
    fv = release.fair_value
    return {"day": day_iso, "gates": gates, "cells": cells,
            "blocking": sorted({f.key for f in release.findings if f.blocking}),
            "point": fv.central,
            "layers": {k: {"v0": layer.v0, "claims": layer.claims} for k, layer in release.layers.items()}}


def _runs(days: list[str], flags: list) -> list[tuple[str, str, object]]:
    """Отрезки дат с одним значением: (первый день, последний день, значение)."""
    out = []
    for day, flag in zip(days, flags):
        if out and out[-1][2] == flag:
            out[-1] = (out[-1][0], day, flag)
        else:
            out.append((day, day, flag))
    return out


def summary(rows: list[dict]) -> str:
    days = [r["day"] for r in rows]
    first = rows[0]
    lines = [f"Развёртка гейтов: {len(rows)} дат оценки, {days[0]} … {days[-1]}; книга "
             f"{_book()['meta']['version']}, кривые на {_book()['meta']['valuation_date']}.", ""]
    blocking = sorted({key for r in rows for key in r["blocking"]})
    lines.append("Блокирующие находки (инварианты, гейты без объяснения): "
                 + (", ".join(blocking) if blocking else "нет ни на одной дате") + ".")
    off = sorted({(key, r["day"]) for r in rows for key, g in r["gates"].items() if g["mass_mismatch"]})
    lines.append("Масса вне коридора объяснения: "
                 + ("; ".join(f"{key} {day}" for key, day in off[:20]) if off else "нет ни на одной дате") + ".")
    lines.append("")
    for key in sorted({key for r in rows for key in r["gates"]}):
        masses = [r["gates"].get(key, {}).get("mass", 0.0) for r in rows]
        lines.append(f"{key}: масса на дату книги {first['gates'].get(key, {}).get('mass', 0.0):.4f}; "
                     f"по развёртке {min(masses):.4f} … {max(masses):.4f}")
        states = [(round(r["gates"].get(key, {}).get("mass", 0.0), 4),
                   r["gates"].get(key, {}).get("cells", 0)) for r in rows]
        for a, b, (m, n) in _runs(days, states):
            lines.append(f"    {a} … {b}: масса {m:.4f}, клеток {n}")
        base = set(first["gates"].get(key, {}).get("labels", []))
        extra = sorted({label for r in rows for label in r["gates"].get(key, {}).get("labels", [])} - base)
        if extra:
            lines.append("    клетки сверх состава даты книги: " + ", ".join(extra))
        lines.append("")

    cells = first["cells"]
    lines.append("EV/EBITDA LTM на дату книги, режимы «дно» и «частичная сходимость»:")
    for world in ("N", "H", "M"):
        values = sorted((c["ev_ebitda_ltm"], key) for key, c in cells.items()
                        if c["world"] == world and c["ev_ebitda_ltm"] is not None)
        fired = set(first["gates"].get("ev_ebitda", {}).get("labels", []))
        out = [v for v, key in values if key in fired]
        inside = [(v, key) for v, key in values if key not in fired]
        lines.append(f"    мир {world}: за коридором {min(out):.2f}–{max(out):.2f}× ({len(out)} клеток)"
                     + "".join(f"; в коридоре {key} {v:.3f}×" for v, key in inside))
    lines.append("")
    lines.append("Доля терминала в EV на дату книги:")
    by_share = sorted((c["terminal_share"], key) for key, c in cells.items())
    fired = set(first["gates"].get("terminal_share", {}).get("labels", []))
    for share, key in by_share:
        if key in fired or share == by_share[0][0] or (cells[key]["world"] == "N" and share > 0.45):
            lines.append(f"    {key}: {share:.4f}{' — гейт' if key in fired else ''}")
    lines.append("")

    def extreme(field, pick, index=None):
        """Крайнее значение поля по всем клеткам и датам: (значение, клетка, дата)."""
        best = None
        for r in rows:
            for key, c in r["cells"].items():
                value = c[field] if index is None else c[field][index]
                if best is None or pick(value, best[0]):
                    best = (value, key, r["day"])
        return best

    less, more = (lambda a, b: a < b), (lambda a, b: a > b)
    eq = extreme("equity", less)
    lines.append(f"Минимум капитала клетки по развёртке: {eq[0]:.2f} млрд ₽ — {eq[1]}, {eq[2]}; "
                 f"на дату книги {min((c['equity'], k) for k, c in cells.items())}")
    cv = extreme("cover_min", less)
    lines.append(f"Минимум покрытия процентов по развёртке: {cv[0]:.3f}× — {cv[1]}, {cv[2]}")
    for name, field in (("Маржа EBITDA полугодий", "margin"), ("Capex/выручка полугодий", "capex_pct")):
        lo, hi = extreme(field, less, 0), extreme(field, more, 1)
        lines.append(f"{name} по развёртке: от {lo[0][0] * 100:.3f} % ({lo[1]}, {lo[0][1]}, дата {lo[2]}) "
                     f"до {hi[0][0] * 100:.3f} % ({hi[1]}, {hi[0][1]}, дата {hi[2]})")
        lo = min((c[field][0][0], k, c[field][0][1]) for k, c in cells.items())
        hi = max((c[field][1][0], k, c[field][1][1]) for k, c in cells.items())
        lines.append(f"    на дату книги: от {lo[0] * 100:.3f} % ({lo[1]}, {lo[2]}) до {hi[0] * 100:.3f} % ({hi[1]}, {hi[2]})")
    real = {w: sorted({round(c["r_real"], 5) for c in cells.values() if c["world"] == w}) for w in ("N", "H", "M")}
    lines.append(f"Реальная r_u терминала по мирам: {real}")
    lines.append("Слои на дату книги (V0, D, млрд ₽): "
                 + "; ".join(f"{k} {v['v0']:.1f} / {v['claims']:.1f} (V0/D {v['v0'] / v['claims']:.2f})"
                             for k, v in first["layers"].items()))
    points = [r["point"] for r in rows]
    lines.append(f"Точка на книжных входах: {points[0]:.1f} ₽ на дату книги; по развёртке "
                 f"{min(points):.1f} … {max(points):.1f} ₽")
    return "\n".join(lines) + "\n"


def compact(rows: list[dict]) -> dict:
    """Запись развёртки: клетки — на дату книги, по датам — массы гейтов, состав
    клеток против даты книги и крайние значения сетки."""
    first = rows[0]
    by_day = []
    for r in rows:
        gates = {}
        for key, g in r["gates"].items():
            base = set(first["gates"].get(key, {}).get("labels", []))
            gates[key] = {"mass": round(g["mass"], 6), "cells": g["cells"],
                          "added": sorted(set(g["labels"]) - base),
                          "removed": sorted(base - set(g["labels"]))}
        cells = r["cells"]
        by_day.append({
            "day": r["day"], "point": round(r["point"], 3), "gates": gates,
            "equity_min": min((round(c["equity"], 3), k) for k, c in cells.items()),
            "cover_min": min((round(c["cover_min"], 4), k) for k, c in cells.items()),
            "margin": [min(round(c["margin"][0][0], 6) for c in cells.values()),
                       max(round(c["margin"][1][0], 6) for c in cells.values())],
            "capex_pct": [min(round(c["capex_pct"][0][0], 6) for c in cells.values()),
                          max(round(c["capex_pct"][1][0], 6) for c in cells.values())],
        })
    return {"book_version": _book()["meta"]["version"], "curve_as_of": _book()["meta"]["valuation_date"],
            "book_date": {"gates": first["gates"], "cells": first["cells"], "layers": first["layers"]},
            "by_day": by_day}


def main() -> int:
    import yaml

    A = _book()
    start = dt.date.fromisoformat(A["meta"]["valuation_date"])
    specs = yaml.safe_load((ROOT / "data" / "assumptions" / "gate_explanations.yaml").read_text(encoding="utf-8"))
    end = max(dt.date.fromisoformat(str(s["valid_until"])) for s in specs.values() if s.get("valid_until"))
    days = [(start + dt.timedelta(days=n)).isoformat() for n in range((end - start).days + 1)]
    with mp.Pool(min(8, mp.cpu_count())) as pool:
        rows = pool.map(one_day, days, chunksize=4)
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "gate_sweep.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(compact(rows), fh, ensure_ascii=False, indent=1)
    text = summary(rows)
    with open(OUT / "gate_sweep.txt", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
