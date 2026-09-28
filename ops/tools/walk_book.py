# -*- coding: utf-8 -*-
"""Прогулка версий книги: правки по одной, накопительно, ядром.

От стартовой книги шаги применяются по очереди, и после каждого печатается
точка при центральных значениях (низ / центр / верх), её сдвиг, V0 обоих
слоёв, требования и σ; с `--median` — ещё медиана полосы A-V9 (печатаемый
заголовок) и её сдвиг, полосы 80 % и 50 % и P(центр < рынка). В конце — две
проверки: последний шаг = прямой расчёт конечной книги и книга после шагов =
конечная книга целиком. Прогулка, где хоть одна «НЕТ», не объясняет переход
между версиями — код 1.

Как запускать (из корня репозитория):
  python -B ops/tools/walk_book.py --start СТАРАЯ.yaml --end НОВАЯ.yaml --steps ШАГИ.yaml
      [--median [--draws N]] [--json ФАЙЛ]

Файл шагов (YAML или JSON) — список шагов:
  - name: "Поступления от выбытия ОС"
    from_end: [capex.disposal_proceeds_pct]      # значения — из конечной книги
  - name: "β_u = 0,9 (проба)"
    set: {valuation.beta_u: 0.9}                 # значения — явно
Путь, которого нет в конечной книге (`from_end`), или значение `null` (`set`)
убирают ключ; недостающие блоки создаются. Книга после каждого шага
проверяется (`validate_book`); не читается — код 2 с номером шага.

Коды возврата: 0 — прогулка сходится; 1 — хоть одна проверка «НЕТ»;
2 — негодные входы (файл, шаг, книга после шага).
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from model.book import BookError, load_book, validate_book  # noqa: E402
from model.uncertainty import evaluate, uncertainty  # noqa: E402

OK, NOT_CONSISTENT, BAD_INPUT = 0, 1, 2
# Прямой расчёт и последний шаг — один код на одной книге: допуск только на
# порядок сложения, если шаги собрали словарь в ином порядке ключей.
REL = 1e-12
_MISSING = object()


class Refused(Exception):
    """Негодные входы — код 2."""


def _get(A: dict, dotted: str):
    node = A
    for key in dotted.split("."):
        if not isinstance(node, dict) or key not in node:
            return _MISSING
        node = node[key]
    return node


def _put(A: dict, dotted: str, value) -> None:
    node, keys = A, dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if value is _MISSING:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = copy.deepcopy(value)


def read_steps(path: Path) -> list[dict]:
    try:
        steps = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Refused(f"шаги не читаются ({path}): {exc}") from exc
    if not isinstance(steps, list) or not steps:
        raise Refused(f"шаги ({path}): ожидается непустой список")
    for i, step in enumerate(steps, 1):
        if (not isinstance(step, dict) or not isinstance(step.get("name"), str)
                or set(step) - {"name", "set", "from_end"}
                or not (step.get("set") or step.get("from_end"))):
            raise Refused(f"шаг {i}: нужны name и хотя бы одно из set {{путь: значение}} / from_end [пути]")
        if not isinstance(step.get("set", {}), dict) or not isinstance(step.get("from_end", []), list):
            raise Refused(f"шаг {i} «{step['name']}»: set — словарь, from_end — список путей")
    return steps


def point(A: dict, median: bool = False, draws: int | None = None) -> dict:
    """Точка и слои ядром; с `median` — медиана, полосы 80 % / 50 % и P(центр < рынка)
    полосы A-V9 на `draws` прогонах."""
    _, _, layer_map, fv = evaluate(A)
    own, market = layer_map["analytical"], layer_map["macro_neutral"]
    out = dict(low=fv.low, central=fv.central, high=fv.high, v0_own=own.v0, v0_market=market.v0,
               d=own.claims)
    if median:
        u = uncertainty(copy.deepcopy(A), draws=draws)
        q = u["central"]
        out.update(median=q["0.50"], p10=q["0.10"], p25=q["0.25"], p75=q["0.75"], p90=q["0.90"],
                   p_below=u["p_central_below_market"])
    return out


def walk(start: dict, end: dict, steps: list[dict], *, median: bool = False,
         draws: int | None = None) -> dict:
    """Строки прогулки, прямой расчёт конечной книги и обе проверки."""
    A = copy.deepcopy(start)
    rows = [dict(step="старт", **point(A, median, draws))]
    for i, step in enumerate(steps, 1):
        for dotted in step.get("from_end", []):
            _put(A, dotted, _get(end, dotted))
        for dotted, value in (step.get("set") or {}).items():
            _put(A, dotted, _MISSING if value is None else value)
        try:
            validate_book(A)
        except BookError as exc:
            raise Refused(f"шаг {i} «{step['name']}»: книга не читается — {exc}") from exc
        rows.append(dict(step=step["name"], **point(A, median, draws)))
    direct = point(copy.deepcopy(end), median, draws)
    consistent = all(abs(rows[-1][k] - v) <= REL * max(1.0, abs(v)) for k, v in direct.items())
    return dict(rows=rows, direct=direct, consistent=consistent, same_book=A == end)


def render(res: dict) -> str:
    median = "median" in res["direct"]
    head = f"{'шаг':<60}{'низ':>8}{'центр':>8}{'верх':>8}{'Δцентр':>8}"
    head += f"{'медиана':>9}{'Δмед.':>8}{'P10–P90':>14}{'P25–P75':>14}{'P<рын.':>8}" if median else ""
    lines = [head + f"{'V0 свой':>9}{'V0 рын.':>9}{'D':>8}"]
    prev = None
    for r in res["rows"]:
        line = (f"{r['step'][:59]:<60}{r['low']:>8.1f}{r['central']:>8.1f}{r['high']:>8.1f}"
                + ("" if prev is None else f"{r['central'] - prev['central']:+8.1f}").rjust(8))
        if median:
            line += f"{r['median']:>9.1f}" + (
                "" if prev is None else f"{r['median'] - prev['median']:+8.1f}").rjust(8)
            line += (f"{r['p10']:>7.1f}–{r['p90']:<6.1f}".rjust(14) + f"{r['p25']:>7.1f}–{r['p75']:<6.1f}".rjust(14)
                     + f"{100 * r['p_below']:>7.1f}%")
        lines.append(line + f"{r['v0_own']:>9.1f}{r['v0_market']:>9.1f}{r['d']:>8.1f}")
        prev = r
    d = res["direct"]
    lines += ["", f"последний шаг = прямой расчёт конечной книги: {'ДА' if res['consistent'] else 'НЕТ'} "
                  f"(прямой: {d['low']:.1f} / {d['central']:.1f} / {d['high']:.1f}"
                  + (f", медиана {d['median']:.1f}" if median else "") + ")",
              f"книга после шагов = конечная книга целиком: {'ДА' if res['same_book'] else 'НЕТ'}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", required=True, help="стартовая книга (assumptions.yaml версии)")
    ap.add_argument("--end", required=True, help="конечная книга (assumptions.yaml версии)")
    ap.add_argument("--steps", required=True, help="файл шагов (YAML или JSON)")
    ap.add_argument("--median", action="store_true", help="ещё медиана полосы A-V9 на каждом шаге")
    ap.add_argument("--draws", type=int, default=None,
                    help="прогонов полосы для --median (по умолчанию — как в книге, 2 000 ≈ 40 с на шаг)")
    ap.add_argument("--json", help="записать строки и проверки в JSON")
    args = ap.parse_args(argv)
    try:
        start, end = (load_book(Path(p)) for p in (args.start, args.end))
        res = walk(start, end, read_steps(Path(args.steps)), median=args.median, draws=args.draws)
    except (OSError, BookError, Refused) as exc:
        print(f"НЕГОДНЫЕ ВХОДЫ: {exc}", file=sys.stderr)
        return BAD_INPUT
    print(render(res))
    if args.json:
        Path(args.json).write_text(json.dumps(res, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return OK if res["consistent"] and res["same_book"] else NOT_CONSISTENT


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
