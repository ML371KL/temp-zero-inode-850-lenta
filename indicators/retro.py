"""Ретро-проверка наивных эталонов на квартальной истории Ленты (D15).

**Зачем.** Нау-каст меряется против наивных эталонов (`journal.py`), и без
планки «уравнение ошиблось на 0,3 п.п.» нечем оценить. Здесь эталоны журнала
посчитаны на истории датабука — кварталы 1кв2020–2кв2026, IAS 17, — каждый
прогноз только по данным ДО своего квартала (`journal._strictly_before`).
Пересчитывается каждым выпуском (`nowcast.retro`) и таблицей идёт в
`docs/INDICATORS.md`.

**Разрывы периметра — списком** (`indicators.perimeter`): «Монетка» 4 кв.
2023, «Улыбка радуги» 12.2024, «Молния» 06.2025, «Реми» 12.2025, «Дом Лента»
1 кв. 2026, «О'КЕЙ» 06.2026. Метрики печатаются на всех точках и на ЧИСТЫХ
(главный эталон точки не сломан ни одной сделкой); сломанные точки — отдельной
строкой с ошибкой и причиной: спрятанная точка — тоже подгонка.

**Честная подпись.** У уравнения нау-каста истории нет: первая сверка с фактом —
отчёт за 3 кв. 2026. Эталоны — планка, которую уравнению предстоит взять. «Сверка»,
а не «зачёт»: идёт ли квартал в зачёт, решает журнал (прогноз за 45 дней до
отчёта; у квартала, чей журнал начат позже этого момента, зачётного прогноза нет).

**Оговорки, которые тоже печатаются:**

* история — ОДИН винтаж датабука (2 кв. 2026): датабук переписывает прошлое
  при каждом релизе, это не то, что было известно на историческую дату;
* 2020 год — COVID (2 кв. 2020 11,7 %), 2022 — уход поставщиков: подпериоды
  «до 2022 / с 2022» печатаются отдельно.

**Детерминированно и без сети**: история — факты книги
(`data/facts/accounting_base.json → quarters.<ГГГГQn>.ias17`) или, если в
хранилище уже есть ряды датабука, они (последний винтаж на дату).
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from indicators import issuer, perimeter, periods
from indicators.journal import (
    BENCHMARK_TITLES,
    INTEREST_TARGET,
    MAIN_BENCHMARK,
    MARGIN_TARGET,
    REVENUE_TARGET,
    naive_interest,
    naive_margin,
    naive_revenue,
    report_date,
)
from indicators.periods import FIRST_FORECAST_QUARTER
from model.paths import FACTS_DIR

SPLIT_YEAR = 2022
"""Граница подпериодов «до 2022 / с 2022»: смена режима российской розницы."""

ACCOUNTING_FILE = "accounting_base.json"

# Ряды хранилища, которые пишет сборщик датабука (`sources.collect_lenta_databook`).
DATABOOK_SERIES = {
    "revenue": issuer.series("databook.q.revenue"),
    "ebitda": issuer.series("databook.q.ebitda_pre16"),
    "net_interest": issuer.series("databook.q.net_interest_pre16"),
}

MARGIN_METHODS = ["yoy_plus_shift", "seasonal_naive", "last_period", "mean_of_year"]
REVENUE_METHODS = ["yoy_growth_carried", "seasonal_naive"]


# ------------------------------------------------------------------ история


def quarterly_history(*, store=None, facts_dir: Path | None = None,
                      as_of: str | None = None) -> dict:
    """Квартальная история IAS 17: {"revenue", "ebitda", "margin", "net_interest", "source"}.

    Выручка, EBITDA и чистые проценты — млрд ₽, маржа — доля выручки.
    Хранилище (ряды датабука, винтаж на дату `as_of`) сильнее фактов книги:
    оно свежее и точечно во времени. Нет ни того, ни другого — пустые ряды.
    """
    if store is not None:
        loaded = {key: store.load(series_id) for key, series_id in DATABOOK_SERIES.items()}
        if loaded["revenue"] is not None and loaded["ebitda"] is not None:
            revenue = loaded["revenue"].history(as_of)
            ebitda = loaded["ebitda"].history(as_of)
            interest = loaded["net_interest"].history(as_of) if loaded["net_interest"] else {}
            return _assemble(revenue, ebitda, interest,
                             source=dict(kind="store", series=DATABOOK_SERIES,
                                         as_of=as_of or date.today().isoformat(),
                                         pit_warning="винтаж — день получения датабука"))
    path = Path(facts_dir or FACTS_DIR) / ACCOUNTING_FILE
    if not path.exists():
        return _assemble({}, {}, {}, source=dict(kind="none", file=str(path.name)))
    data = json.loads(path.read_text(encoding="utf-8"))
    revenue, ebitda, interest = {}, {}, {}
    for quarter, row in (data.get("quarters") or {}).items():
        ias17 = row.get("ias17") or {}
        value = lambda key: (ias17.get(key) or {}).get("v")  # noqa: E731
        if value("revenue") is not None and value("ebitda") is not None:
            revenue[quarter], ebitda[quarter] = value("revenue"), value("ebitda")
        if value("net_interest") is not None:
            interest[quarter] = -value("net_interest")
    doc = data.get("source_doc") or {}
    return _assemble(revenue, ebitda, interest, source=dict(
        kind="facts", file=ACCOUNTING_FILE, document=doc.get("id", ""),
        sha256=doc.get("sha256", ""),
        pit_warning="один винтаж датабука: прошлое переписано при каждом релизе"))


def _assemble(revenue: dict, ebitda: dict, interest: dict, *, source: dict) -> dict:
    margin = {q: ebitda[q] / revenue[q] for q in revenue if q in ebitda and revenue[q]}
    return dict(revenue=dict(sorted(revenue.items())), ebitda=dict(sorted(ebitda.items())),
                margin=dict(sorted(margin.items())), net_interest=dict(sorted(interest.items())),
                source=source)


# ------------------------------------------------------------------ метрики


def metrics(errors: list[float]) -> dict:
    """RMSE, MAE, смещение (прогноз − факт) и число точек."""
    n = len(errors)
    if not n:
        return dict(n=0, rmse=None, mae=None, bias=None)
    return dict(n=n,
                rmse=round(math.sqrt(sum(e * e for e in errors) / n), 6),
                mae=round(sum(abs(e) for e in errors) / n, 6),
                bias=round(sum(errors) / n, 6))


def _table(points: list[dict], methods: list[str], main: str, *,
           error=lambda forecast, actual: forecast - actual) -> list[dict]:
    """Строки таблицы: эталон × (все / чистые / до 2022 / с 2022)."""
    rows = []
    for method in methods:
        errors = [(p, error(p["forecasts"][method], p["actual"]))
                  for p in points if p["forecasts"].get(method) is not None]
        rows.append(dict(
            method=method, title=BENCHMARK_TITLES.get(method, method), main=method == main,
            all=metrics([e for _, e in errors]),
            clean=metrics([e for p, e in errors if not p["breaks"].get(method)]),
            before=metrics([e for p, e in errors if int(p["period"][:4]) < SPLIT_YEAR]),
            since=metrics([e for p, e in errors if int(p["period"][:4]) >= SPLIT_YEAR])))
    return rows


def _best(rows: list[dict], subset: str = "all") -> str | None:
    usable = [r for r in rows if r[subset]["rmse"] is not None]
    return min(usable, key=lambda r: r[subset]["rmse"])["method"] if usable else None


def _points(history: dict[str, float], methods: list[str], naive) -> list[dict]:
    """Вневыборочные прогнозы каждым эталоном; период входит, если есть все эталоны."""
    items = perimeter.breaks()
    out = []
    for period in sorted(history, key=periods.index):
        forecasts = naive(history, period)
        if not all(m in forecasts for m in methods):
            continue
        out.append(dict(
            period=period, actual=round(history[period], 6),
            forecasts={m: round(forecasts[m], 6) for m in methods},
            breaks={m: [b.id for b in perimeter.broken_by(period, m, items=items)]
                    for m in methods}))
    return out


# -------------------------------------------------------------------- маржа


def margin_block(history: dict[str, float]) -> dict:
    points = _points(history, MARGIN_METHODS, naive_margin)
    main = MAIN_BENCHMARK[MARGIN_TARGET]
    rows = _table(points, MARGIN_METHODS, main)
    return dict(
        target=MARGIN_TARGET, unit="доля выручки; ошибка — прогноз − факт",
        main=main, periods=[points[0]["period"], points[-1]["period"]] if points else [],
        benchmarks=rows, best=_best(rows), best_clean=_best(rows, "clean"),
        points=points,
        excluded=[dict(period=p["period"], breaks=p["breaks"][main],
                       error=round(p["forecasts"][main] - p["actual"], 6))
                  for p in points if p["breaks"][main]],
        note=("квартальная маржа EBITDA до МСФО 16 (датабук, Financials quarterly); «чистые» "
              "точки — главный эталон не пересекает ни одной консолидации"))


# ------------------------------------------------------------------ выручка


def revenue_block(history: dict[str, float]) -> dict:
    points = _points(history, REVENUE_METHODS, naive_revenue)
    main = MAIN_BENCHMARK[REVENUE_TARGET]
    relative = lambda forecast, actual: forecast / actual - 1  # noqa: E731
    rows = _table(points, REVENUE_METHODS, main, error=relative)
    return dict(
        target=REVENUE_TARGET, unit="относительная ошибка выручки квартала (прогноз / факт − 1)",
        main=main, periods=[points[0]["period"], points[-1]["period"]] if points else [],
        benchmarks=rows, best=_best(rows), best_clean=_best(rows, "clean"),
        points=points,
        excluded=[dict(period=p["period"], breaks=p["breaks"][main],
                       error=round(relative(p["forecasts"][main], p["actual"]), 6))
                  for p in points if p["breaks"][main]],
        note=("выручка квартала до МСФО 16; рост — отчётный, органической выручки "
              "приобретённых сетей по кварталам нет; кварталы на стыке сделок — отдельной "
              "строкой, в «чистые» метрики не входят"))


# ---------------------------------------------------------------- проценты


def average_key_rate(store, period: str) -> float:
    """Средняя ключевая ставка периода, взвешенная ПО ДНЯМ (ступенчатая функция).

    Окно — только дни, о которых ряд что-то знает: продлевать последнюю ставку
    до конца периода было бы прогнозом, а не средней наблюдённой.
    """
    from datetime import timedelta

    series = store.load("cbr.key_rate") if store is not None else None
    if not series:
        return 0.0
    start, end = periods.bounds(period)
    points = sorted((date.fromisoformat(p[:10]), v) for p, v in series.history().items())
    if not points:
        return 0.0
    low, high = max(start, points[0][0]), min(end, points[-1][0])
    if low > high:
        return 0.0
    index, total, day = 0, 0.0, low
    while day <= high:
        while index + 1 < len(points) and points[index + 1][0] <= day:
            index += 1
        total += points[index][1]
        day += timedelta(days=1)
    return total / ((high - low).days + 1)


def interest_block(store, history: dict[str, float]) -> dict:
    """Эталон процентов: прошлый квартал × отношение средних ключевых ставок."""
    method = MAIN_BENCHMARK[INTEREST_TARGET]
    if store is None or not store.load("cbr.key_rate") or not history:
        return dict(target=INTEREST_TARGET, available=False,
                    reason="нет ряда ключевой ставки или квартальных процентов — эталон "
                           "процентов не считается")
    points = []
    for period in sorted(history, key=periods.index):
        previous = periods.previous_period(period)
        if previous not in history:
            continue
        now, before = average_key_rate(store, period), average_key_rate(store, previous)
        naive = naive_interest(history[previous], average_rate_now=now,
                               average_rate_before=before)
        if not naive or not history[period]:
            continue
        points.append(dict(period=period, actual=round(history[period], 3),
                           forecasts={method: round(naive[method], 3),
                                      "last_period": round(history[previous], 3)},
                           breaks={}, key_rate=round(now, 6)))
    relative = lambda forecast, actual: forecast / actual - 1  # noqa: E731
    rows = _table(points, [method, "last_period"], method, error=relative)
    return dict(
        target=INTEREST_TARGET, available=True,
        unit="относительная ошибка чистых процентов квартала (прогноз / факт − 1)",
        main=method, periods=[points[0]["period"], points[-1]["period"]] if points else [],
        benchmarks=rows, best=_best(rows), points=points,
        note=("чистые проценты IAS 17 из датабука (Net Interest expense); эталон «× отношение "
              "ставок» написан для плавающего долга — у Ленты плавающая доля ≈26 % (D12), и "
              "ошибка здесь меряет структуру долга не меньше, чем эталон"))


# -------------------------------------------------------------------- выход


def honest_note() -> str:
    exam = report_date(FIRST_FORECAST_QUARTER)
    return ("у уравнения нау-каста истории нет; первая сверка с фактом — отчёт за 3 кв. 2026 "
            f"(≈{exam.strftime('%d.%m.%Y')}); эталоны — планка, которую уравнению "
            "предстоит взять")


def retro_block(A: dict | None = None, store=None, *, facts_dir: Path | None = None,
                as_of: str | None = None) -> dict:
    """Блок `nowcast.retro` выпуска: эталоны маржи, выручки и процентов на кварталах.

    `A` не читается (эталоны журнала книги не знают); параметр — для той же
    сигнатуры, что у 850oa.
    """
    history = quarterly_history(store=store, facts_dir=facts_dir, as_of=as_of)
    return dict(
        note=honest_note(),
        first_exam=report_date(FIRST_FORECAST_QUARTER).isoformat(),
        source=history["source"],
        split_year=SPLIT_YEAR,
        perimeter_breaks=[b.as_dict() for b in perimeter.breaks(facts_dir)],
        margin=margin_block(history["margin"]),
        revenue=revenue_block(history["revenue"]),
        interest=interest_block(store, history["net_interest"]),
    )


def markdown_table(block: dict) -> str:
    """Таблица для `docs/INDICATORS.md` — из результата модуля, а не руками."""
    def pp(value, scale, signed=False):
        if value is None:
            return "—"
        text = f"{abs(value) * scale:.2f}".replace(".", ",")
        return ("−" if value < 0 else "+" if signed else "") + text

    lines = ["| Величина | Эталон | Точек | RMSE | RMSE чистых (n) | MAE | Смещение | "
             "RMSE до 2022 (n) | RMSE с 2022 (n) |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key, name, unit, scale in (("margin", "маржа квартала", "п.п.", 100),
                                   ("revenue", "выручка квартала", "%", 100),
                                   ("interest", "чистые проценты", "%", 100)):
        part = block.get(key) or {}
        for row in part.get("benchmarks") or []:
            title = row["title"] + (" (главный)" if row["main"] else "")
            clean = row.get("clean") or dict(rmse=None, n=0)
            lines.append(
                f"| {name}, {unit} | {title} | {row['all']['n']} | "
                f"{pp(row['all']['rmse'], scale)} | {pp(clean['rmse'], scale)} "
                f"({clean['n']}) | {pp(row['all']['mae'], scale)} | "
                f"{pp(row['all']['bias'], scale, signed=True)} | {pp(row['before']['rmse'], scale)} "
                f"({row['before']['n']}) | {pp(row['since']['rmse'], scale)} "
                f"({row['since']['n']}) |")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - ручной запуск для документа
    import sys

    from indicators.store import Store

    sys.stdout.reconfigure(encoding="utf-8")
    facts = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    result = retro_block(store=Store(), facts_dir=facts)
    print(markdown_table(result))
    for key in ("margin", "revenue"):
        for item in result[key]["excluded"]:
            print(f"  {key}: исключён {item['period']} ({', '.join(item['breaks'])}): "
                  f"ошибка {item['error']:+.4f}")
    print(result["note"])
