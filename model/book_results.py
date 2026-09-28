"""Таблицы книги — `results.json` и `run_output.txt` — расчётом ядра.

Книга допущений ссылается на свои числа полями `results.json` (сценарии, слои,
диапазон, чувствительности, контроли, полоса, диагностики медианы) и печатью
`run_output.txt`. Оба файла — функция книги:

* `book_results(A)` считает словарь теми же функциями, что и выпуск
  (`run_release`, `payload.slow_blocks`): число в таблицах книги и число
  выпуска — один расчёт, а не два;
* `render_run_output(results, A)` печатает его без нового расчёта: от книги
  нужны только подписи и входные литералы (дата, акции, веса, подписи концов
  строк чувствительности).

Три поля таблиц книги определены иначе, чем одноимённые поля выпуска, и
считаются здесь по определению таблиц (поля выпуска не меняются):

* `exit_multiple` — (TV + щит TV) / EBITDA первого терминального года с
  сезонностью полугодий (выпуск делит на годовую выручку × целевую маржу);
* `creditor_loss_floor_method` слоя — E[max(D клетки − EV клетки, 0)], потери
  кредиторов в клетках без дисконта за управление (выпуск: D − (V0 − E[max(капитал, 0)]));
* `p_above_market` слоя — доля клеток с ценой С ПОЛОМ выше рынка.

Справочные наборы таблиц (контрольные наблюдения правила A-P2u, строки «что
оправдывает рыночную цену», справочные таблицы режимов) — не суждения книги и
не код: они лежат рядом с книгой в `results_spec.yaml` (`results_spec()`).

Запуск: `python -B -m model.book_results [--out КАТАЛОГ]` — пишет оба файла
(по умолчанию в каталог книги `data/assumptions`) с переводом строк LF;
`--fast` — быстрый путь (выпуск на книге без полосы и диагностик, печать сводки,
файлов не пишет).
Единицы: млрд ₽; цена — ₽/акцию.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import platform
import sys
from pathlib import Path

from model.book import (BOOK_DIR, BookError, book, headline_block, median_draws, named_cells,
                        path_value)
from model.core import margin_observations, run_cell
from model.engine import Release, judgement_table, run_release
from model.financing import credit_limit
from model.grid import (GridCell, Layer, layer_mix, layer_stats, layer_variants,
                        next_report_neutral, peer_crosscheck, regime_unconditional,
                        report_period, summarize, variance_breakdown, with_observation)
from model.uncertainty import center_ev_median, evaluate, headline_of

# Тексты-пояснения таблиц книги (часть самого `results.json`).
NOTES = {
    "layers": "По каждому слою: v0 = E[EV], d = E[требования] (млрд руб.); intrinsic — внутренняя "
              "стоимость (V0 − D) на акцию без пола; headline — ЗАГОЛОВОЧНАЯ оценка, отображение книги "
              "max(V0 − D, 0) после дисконта за управление; mean — «старый метод» (вероятностно-"
              "взвешенное среднее цен клеток, обрезанных нулём); mean_no_floor — то же без обрезки; "
              "p10…p90 — перцентили цен клеток с полом в нуле.",
    "p_equity_nonpositive": "Доля вероятности клеток, где DCF-капитал <= 0. Это НЕ вероятность "
                            "дефолта: в этих клетках компания обслуживает долг; отрицательный "
                            "DCF-капитал — следствие ставки дисконтирования r_u заметно выше "
                            "стоимости долга.",
    "fair_value": "low/high — заголовочные оценки слоёв «рыночные ставки как есть» и «свой "
                  "макро-взгляд»; central = low + λ × (high − low), λ = joint.own_macro_confidence "
                  "(вес собственного макро-взгляда); printed — округление до 50 руб.; by_method — "
                  "те же три точки по всем методам.",
}
RATES_VIEW_NOTE = ("вклад собственного взгляда на инфляцию и ставки: верх (свой макро-взгляд) "
                   "минус низ (рыночные ставки как есть)")

# Справочные наборы таблиц книги (`results_spec.yaml` рядом с книгой). Это НЕ
# суждения книги (поэтому их нет в assumptions.yaml) и не код, а входы проверок
# и справок: контрольные наблюдения правила A-P2u (тест-векторы реализации),
# строки чувствительности для «что оправдывает рыночную цену» (конец к рынку
# выбирает расчёт), справочная P(полная сходимость) и справочные таблицы
# P(режим | мир) для тех же строк.
RESULTS_SPEC = "results_spec.yaml"
RESULTS_SPEC_KEYS = frozenset({"control_cases", "justification_ends", "full_return_reference",
                               "reference_regime_tables"})


def results_spec(path: Path | None = None) -> dict:
    """Справочные наборы таблиц книги; файла нет или ключ не тот — отказ."""
    import yaml

    path = path or BOOK_DIR / RESULTS_SPEC
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict) or set(spec) != RESULTS_SPEC_KEYS:
        raise BookError(f"{path.name}: ожидаются ровно ключи {', '.join(sorted(RESULTS_SPEC_KEYS))}")
    return spec


# Сдвиг спредов в проверке знака чувствительности к спреду: +5 п.п.
SPREAD_SIGN_SHIFT = 0.05
SPREAD_SIGN_CASES = (("spread_float_plus_5pp", ("financing.spread_float",)),
                     ("spread_float_and_fixed_plus_5pp",
                      ("financing.spread_float", "financing.spread_fixed")))
# Единица наклона «что даст отчёт»: 0,1 п.п. маржи.
SLOPE_STEP = 0.001
# Узел кривых в проверке «какую 10-летнюю доходность воспроизводят веса миров», лет.
MARKET_TENOR = 10

# Подписи слоёв и порядок режимов в печати `run_output.txt`.
LAYER_NAMES = {"macro_neutral": "рыночные ставки как есть (мир M, безусловные вероятности режимов)",
               "market_implied": "веса миров, вменённые рынком облигаций",
               "analytical": "свой макро-взгляд (аналитические веса миров)"}
REGIMES = ("stress", "floor", "partial", "full")


# ------------------------------------------------------------ клетка и слой


def _row(r) -> dict:
    return dict(period=r.period, revenue=r.revenue, lfl=r.lfl_ticket + r.lfl_traffic,
                area_end=r.area_end, margin=r.margin, ebitda=r.ebitda, capex=r.capex,
                capex_pct=r.capex_pct, da=r.da, d_nwc=r.nwc_change, tax_u=r.tax_unlevered,
                tax_l=r.tax_actual, ts=r.tax_shield, fcff=r.fcff, fcff_margin=r.fcff / r.revenue,
                net_int=r.net_interest, rate_d=r.debt_rate, nd=r.net_debt, debt=r.gross_debt,
                lev=r.leverage, cover=r.interest_cover, div=r.dividends,
                # Сеть по сегментам (карточка «сеть по форматам»).
                segments={sid: dict(revenue=seg.revenue, area_end=seg.area_end)
                          for sid, seg in r.segments.items()})


def scenario_block(result) -> dict:
    """Именованный сценарий целиком: оценка, мост, терминал, путь долга и полугодия."""
    r = result
    return dict(cell=r.cell.as_dict(), ev=r.ev, pv_fcff=r.pv_fcff, pv_ts=r.pv_tax_shield,
                tv_share=r.terminal_share, claims=r.claims, claims_par=r.claims_par,
                debt_cost_addon=r.debt_cost_addon, cash_carry=r.cash_carry, equity=r.equity,
                price=r.price, price_floor=r.price_floor,
                ev_ebitda_ltm=r.ev_ebitda_ltm, ev_ebitda_ntm=r.ev_ebitda_ntm, r_lt=r.r_long,
                r_real=r.r_real, g_terminal=r.terminal_growth,
                exit_multiple=r.terminal_value / r.terminal_ebitda,
                fcff_T_margin=r.fcff_terminal_margin,
                max_net_debt=r.max_net_debt, max_net_debt_period=r.max_net_debt_period,
                max_gross_debt=r.max_gross_debt, max_gross_debt_period=r.max_gross_debt_period,
                max_leverage=r.max_leverage, rows=(rows := [_row(x) for x in r.rows]),
                annual=annual_block(rows))


def annual_block(rows: list[dict]) -> list[dict]:
    """Годы пути клетки (таблица «Проверка согласованности» книги): суммы
    полугодий; LFL — среднее полугодий; ЧД и ЧД/EBITDA — на конец года; первый
    год — только прогнозные полугодия (`halves`)."""
    return [dict(year=a["year"], halves=len(a["lfl"]), revenue=a["revenue"],
                 lfl=sum(a["lfl"]) / len(a["lfl"]), margin=a["ebitda"] / a["revenue"],
                 ebitda=a["ebitda"], capex=a["capex"], capex_pct=a["capex"] / a["revenue"],
                 fcff=a["fcff"], nd=a["nd"], lev=a["lev"], div=a["div"])
            for a in _annual(rows)]


def grid_cell_block(c: GridCell) -> dict:
    r = c.result
    return dict(prob=c.probability, cell=c.cell.as_dict(), ev=r.ev, claims=r.claims,
                debt_cost_addon=r.debt_cost_addon, equity=r.equity, price=r.price,
                price_floor=r.price_floor,
                ev_ebitda_ltm=r.ev_ebitda_ltm, max_net_debt=r.max_net_debt,
                max_net_debt_period=r.max_net_debt_period, max_gross_debt=r.max_gross_debt,
                max_gross_debt_period=r.max_gross_debt_period, max_leverage=r.max_leverage)


def layer_block(A: dict, layer: Layer, cells: list[GridCell]) -> dict:
    """Слой в форме таблиц книги: `mean` — «старый метод» (среднее цен с полом),
    потери кредиторов «старого метода» и доля выше рынка — по клеткам слоя."""
    total = sum(c.probability for c in cells)
    market = A["market"]["price"]
    shortfall = layer.implied_creditor_loss_old
    return dict(v0=layer.v0, d=layer.claims, intrinsic=layer.intrinsic, headline=layer.headline,
                mean=layer.old_method, mean_no_floor=layer.mean_without_floor,
                p10=layer.p10, p25=layer.p25, p50=layer.p50, p75=layer.p75, p90=layer.p90,
                p_equity_nonpositive=layer.p_equity_nonpositive,
                p_above_market=sum(c.probability for c in cells if c.price_floor > market) / total,
                creditor_loss_floor_method=shortfall,
                creditor_loss_floor_method_pct=shortfall / layer.claims)


def fair_value_block(A: dict, release: Release) -> dict:
    fv, layer_map = release.fair_value, release.layers
    out = dict(
        method=fv.method, low=fv.low, central=fv.central, high=fv.high,
        own_macro_confidence=fv.own_macro_confidence,
        # Шаг печати — книги (`print_step`); в таблицах книги — целые рубли.
        printed=dict(low=int(fv.printed_low), central=int(fv.printed_central),
                     high=int(fv.printed_high)),
        by_method={name: layer_mix(A, layer_map, attr) for name, attr in (
            ("headline", "headline"), ("intrinsic", "intrinsic"),
            ("old_floor_mean", "old_method"), ("mean_no_floor", "mean_without_floor"))},
        ev_comparison={name: dict(v0=v.v0, d=v.claims, market_cap=v.market_cap,
                                  market_implied_ev=v.market_implied_ev, gap_pct=v.gap_pct,
                                  rub_per_share_per_1pct_ev=v.rub_per_share_per_1pct_ev,
                                  market_ev_to_equity=v.market_ev_to_equity, v_star=v.v_star,
                                  gap_vs_v_star=v.gap_vs_v_star)
                       for name, v in fv.ev_comparison.items()},
        grid_median=fv.grid_median, modal_world=fv.modal_world, market=fv.market)
    out["center_ev"] = dataclasses.asdict(fv.center_ev)
    out["rates_view"] = dict(rub=fv.rates_view.rub, v0=fv.rates_view.v0, note=RATES_VIEW_NOTE)
    return out


# ----------------------------------------------------- справочные таблицы


def by_world_regime(cells: list[GridCell]) -> dict:
    acc: dict[str, list[float]] = {}
    for c in cells:
        a = acc.setdefault(f"{c.cell.world}|{c.cell.margin_regime}", [0.0, 0.0, 0.0, 0.0])
        a[0] += c.probability
        a[1] += c.probability * c.result.price
        a[2] += c.probability * c.result.ev_ebitda_ltm
        a[3] += c.probability * c.result.ev
    return {k: dict(prob=p, ev=e / p, price_no_floor=v / p, ev_ebitda_ltm=m / p)
            for k, (p, v, m, e) in acc.items()}


def next_report_blocks(A: dict, release: Release, spec: dict) -> dict:
    """«Что даст ближайший отчёт» для точки: строки, наклон, нейтральная маржа и
    контрольные наборы правила A-P2u. Без `demo_values` в книге — пустые строки."""
    # E[m_LT] после факта — та же таблица вероятностей режимов на цели LT книги
    # (текст книги печатает её рядом с вероятностями).
    targets = {r: A["margin"]["regimes"][r]["target"]["LT"] for r in A["margin"]["regimes"]}
    rows = [dict(period=r["period"], margin=r["margin"], regime_prob=r["probabilities"],
                 lt_margin=sum(p * targets[g] for g, p in r["probabilities"].items()),
                 low=r["low"], central=r["central"], high=r["high"],
                 old_method_central=r["old_method_central"],
                 p_equity_nonpositive=r["p_equity_nonpositive"])
            for r in release.next_report]
    out: dict = {}
    controls = []
    if rows:
        span = (rows[-1]["margin"] - rows[0]["margin"]) / SLOPE_STEP
        out["next_report_slope_rub_per_0p1pp"] = dict(
            headline=(rows[-1]["central"] - rows[0]["central"]) / span,
            old_method=(rows[-1]["old_method_central"] - rows[0]["old_method_central"]) / span)
        neutral = next_report_neutral(A)
        out["next_report_neutral"] = dict(period=neutral["period"], margin=neutral["margin"],
                                          central=neutral["central"])
        for case in spec["control_cases"]:
            label, observations = case["label"], case["observations"]
            trial = A
            for period, value in observations.items():
                trial = with_observation(trial, period, value)
            _, _, _, fv = evaluate(trial)
            controls.append(dict(case=label, observations=copy.deepcopy(observations),
                                 regime_prob=regime_unconditional(trial),
                                 low=fv.low, central=fv.central, high=fv.high))
    out["next_report_value"] = rows
    out["regime_update_controls"] = controls
    return out


def _curve_node(curve: dict, tenor: float) -> float:
    return next(float(v) for k, v in curve.items() if k != "LT" and float(k) == float(tenor))


def checks_block(A: dict, release: Release) -> dict:
    """Контроли книги: путь долга против лимита линий, добавка стоимости долга и
    знак чувствительности к спреду, 10-летняя доходность весов миров, ожидания
    маржи и безусловные вероятности capex."""
    F, J, M = A["facts"], A["joint"], A["margin"]
    cells, fv = release.cells, release.fair_value
    limit = credit_limit(A)

    def tag(c: GridCell) -> str:
        return f"{c.cell.world}·{c.cell.margin_regime}·{c.cell.capex}"

    over_net = [dict(cell=tag(c), prob=c.probability, max_net_debt=c.result.max_net_debt,
                     period=c.result.max_net_debt_period)
                for c in cells if c.result.max_net_debt > limit]
    over_gross = [dict(cell=tag(c), prob=c.probability, max_gross_debt=c.result.max_gross_debt,
                       period=c.result.max_gross_debt_period)
                  for c in cells if c.result.max_gross_debt > limit]
    worst = max(cells, key=lambda c: c.result.max_net_debt)
    credit_lines = dict(
        start_gross_debt=F["anchor"]["net_debt"] + F["anchor"]["cash"],
        undrawn_lines=F["undrawn_credit_lines"], limit=limit,
        worst_cell=tag(worst), worst_max_net_debt=worst.result.max_net_debt,
        worst_period=worst.result.max_net_debt_period,
        worst_share_of_limit=worst.result.max_net_debt / limit,
        worst_max_leverage=max(c.result.max_leverage for c in cells),
        cells_over_limit_net_debt=over_net, prob_over_limit_net_debt=sum(x["prob"] for x in over_net),
        cells_over_limit_gross_debt=over_gross,
        prob_over_limit_gross_debt=sum(x["prob"] for x in over_gross),
        by_cell=[dict(cell=tag(c), prob=c.probability, max_net_debt=c.result.max_net_debt,
                      period=c.result.max_net_debt_period, max_gross_debt=c.result.max_gross_debt,
                      max_leverage=c.result.max_leverage) for c in cells])

    spread = dict(base_central=fv.central,
                  base_old_method_central=layer_mix(A, release.layers, "old_method")["central"])
    for name, paths in SPREAD_SIGN_CASES:
        trial, _, lm, shifted = evaluate(A, {p: {"__shift__": SPREAD_SIGN_SHIFT} for p in paths})
        base = run_cell(trial, named_cells(trial)["base"])
        spread[name] = dict(low=shifted.low, central=shifted.central, high=shifted.high,
                            old_method_central=layer_mix(trial, lm, "old_method")["central"],
                            base_scenario_ev=base.ev, base_scenario_debt_cost_addon=base.debt_cost_addon,
                            base_scenario_price=base.price, lowers_headline=shifted.central < fv.central)

    node = {w: _curve_node(A["worlds"][w]["zero_curve"], MARKET_TENOR) for w in J["world_prob"]}
    market = node[J["macro_neutral_world"]]
    implied = dict(market_10y=market, fair_10y_by_world=node)
    for name, weights in (("analytical", J["world_prob"]),
                          ("market_implied", J["world_prob_market_implied"])):
        linear = sum(weights[w] * node[w] for w in weights)
        mix = sum(weights[w] * (1 + node[w]) ** -MARKET_TENOR for w in weights) ** (-1 / MARKET_TENOR) - 1
        implied[name] = dict(weights=dict(weights), linear=linear, discount_factor_mix=mix,
                             gap_to_market_linear=linear - market)

    un = release.regime_unconditional
    period = report_period(A)
    capex: dict[str, float] = {}
    for regime in un:
        for level, p in J["capex_prob_given_regime"][regime].items():
            capex[level] = capex.get(level, 0.0) + un[regime] * p
    return dict(
        credit_lines=credit_lines,
        debt_cost_addon_base_max=max(c.result.debt_cost_addon for c in cells),
        spread_sign=spread, market_implied_10y=implied,
        expected_lt_margin=sum(un[r] * M["regimes"][r]["target"]["LT"] for r in un),
        expected_margin_demo_period=sum(un[r] * path_value(M["regimes"][r]["target"], period) for r in un),
        capex_prob_unconditional=capex)


def sensitivity_rows(A: dict, judgements) -> list[dict]:
    """Строки чувствительности книги: концы каждой строки, сортировка по размаху центра."""
    specs = {s["name"]: s for s in A.get("sensitivities", [])}
    if len(specs) != len(A.get("sensitivities", [])):
        raise BookError("книга: имена строк sensitivities повторяются — строку таблицы не найти")
    rows = []
    for j in judgements:
        s = specs[j.label]
        rows.append(dict(
            name=j.label, path=j.key, low=s.get("low", s.get("shift_low")),
            high=s.get("high", s.get("shift_high")),
            base_low=j.scenario_low, base_high=j.scenario_high,
            central_low=j.price_low, central_high=j.price_high,
            range_low=list(j.range_low), range_high=list(j.range_high),
            old_method_central_low=j.old_method_low, old_method_central_high=j.old_method_high,
            grid_low=j.grid_low, grid_high=j.grid_high,
            swing_base=abs(j.scenario_high - j.scenario_low), swing_central=j.spread,
            swing_old_method_central=abs(j.old_method_high - j.old_method_low)))
    return rows


def regime_table_with_full(J: dict, target_full: float) -> dict:
    """P(режим | мир), в которой безусловная P(полный возврат) доведена до target_full:
    «полный» во всех мирах умножается на один коэффициент, масса снимается
    пропорционально с остальных режимов."""
    k = target_full / sum(J["world_prob"][w] * J["regime_given_world"][w]["full"] for w in J["world_prob"])
    out = {}
    for world, probs in J["regime_given_world"].items():
        full = probs["full"] * k
        out[world] = {r: (full if r == "full" else v * (1 - full) / (1 - probs["full"]))
                      for r, v in probs.items()}
    return out


def market_price_justification(A: dict, release: Release, sensitivities: list[dict],
                               spec: dict) -> dict:
    """Что оправдывает рыночную цену: V0 слоя «свой макро-взгляд», при котором его
    заголовочная оценка равна рынку, и центр при одиночных изменениях допущений.

    Направление — к рынку: точка выше рынка (`direction: down`) — конец строки
    с меньшим центром, и рынок «достигнут», когда центр не выше цены; точка
    ниже рынка (`up`, случай 850oa) — наоборот. Прежде блок всегда брал конец с
    БОЛЬШИМ центром и мерил «≥ рынка»: при точке выше рынка каждая строка
    «достигала» рынка, уходя от него.
    """
    layer, px = release.layers["analytical"], A["market"]["price"]
    required = release.fair_value.ev_comparison["analytical"].v_star
    down = release.fair_value.central > px
    out = dict(market_price=px, direction="down" if down else "up", v0_required=required,
               v0_model=layer.v0, v0_gap_pct=required / layer.v0 - 1,
               v0_required_multiple=required / A["facts"]["anchor"]["ebitda_ltm"], cases=[])

    def toward(*ends):
        """Из пар (значение, центр) — та, что ближе к рынку по направлению."""
        return (min if down else max)(ends, key=lambda end: end[1])

    def add(name, value, central, note):
        out["cases"].append(dict(assumption=name, value=value, central=central,
                                 reaches_market=central <= px if down else central >= px,
                                 note=note))

    rows = {s["name"]: s for s in sensitivities}
    for end in spec["justification_ends"]:
        if end["row"] not in rows:
            raise BookError(f"{RESULTS_SPEC}: строки чувствительности «{end['row']}» в книге нет")
        s = rows[end["row"]]
        value, central = toward((s["low"], s["central_low"]), (s["high"], s["central_high"]))
        add(end["name"], value, central, "конец диапазона книги к рынку")
    ref_full = spec["full_return_reference"]
    table = regime_table_with_full(A["joint"], ref_full["value"])
    add(ref_full["name"], ref_full["value"],
        evaluate(A, {"joint.regime_given_world": table})[3].central, ref_full["note"])
    for ref in spec["reference_regime_tables"]:
        add(ref["name"], ref["value_label"],
            evaluate(A, {"joint.regime_given_world": copy.deepcopy(ref["table"])})[3].central,
            ref["note"])
    mix = layer_mix(A, release.layers, "headline")
    label, central = toward(("λ = 0", mix["low"]), ("λ = 1", mix["high"]))
    add("A-P1c вес собственного макро-взгляда", label, central,
        "центр совпадает с " + ("нижней" if central == min(mix["low"], mix["high"]) else "верхней")
        + " границей")
    out["any_single_change_reaches_market"] = any(c["reaches_market"] for c in out["cases"])
    return out


def _layer_draws(draws: list[dict]) -> list[dict]:
    """Отображение каждого прогона полосы — словарями с именами полей (V0 и D)."""
    fields = ("v0", "d")
    return [dict(governance=d["governance"], own=dict(zip(fields, d["own"])),
                 market=dict(zip(fields, d["market"]))) for d in draws]


# ------------------------------------------------------------------ сборка


def environment() -> dict:
    """Интерпретатор и платформа прогона (DESIGN D17). Канон чисел — Python 3.12;
    регрессия сравнивает числа с допуском, а это поле — только по версии
    Python (в CI — Linux, у автора книги — Windows)."""
    return dict(python=platform.python_version(), implementation=platform.python_implementation(),
                platform=platform.platform())


def book_results(A: dict | None = None, *, slow: bool = True, spec: dict | None = None) -> dict:
    """Словарь `results.json` книги: ключи, порядок и типы таблиц книги.

    `slow=False` — без полосы, обратного DCF, заголовка и диагностик медианы
    (тысячи сеток): для быстрой сверки остальных блоков. `spec` — справочные
    наборы таблиц (по умолчанию `results_spec.yaml` рядом с книгой).
    """
    from model.payload import slow_blocks

    A = A if A is not None else book()
    spec = spec if spec is not None else results_spec()
    release = run_release(A, gates=False)
    blocks = slow_blocks(A) if slow else None
    judgements = blocks["judgements"] if slow else judgement_table(A, limit=None)
    cells, layer_map = release.cells, release.layers

    out: dict = {"book_version": A["meta"]["version"], "environment": environment(),
                 "notes": dict(NOTES),
                 "scenarios": {name: scenario_block(r) for name, r in release.named.items()}}
    out["weighted_named"] = sum(w * release.named[k].price_floor for k, w in A["weights"].items())
    out["weights_from_grid"] = dict(release.named_weights)
    variants = layer_variants(A, cells)
    out["layers"] = {name: layer_block(A, L, variants[name]) for name, L in layer_map.items()}
    out["regime_prob"] = dict(unconditional=release.regime_unconditional,
                              given_world=release.regime_given_world,
                              observations=margin_observations(A))
    out["fair_value"] = fair_value_block(A, release)
    out["by_world_regime"] = by_world_regime(cells)
    by_world = {}
    for world in A["joint"]["world_prob"]:
        subset = [c for c in cells if c.cell.world == world]
        by_world[world] = layer_block(A, layer_stats(A, subset, f"world:{world}"), subset)
    out["by_world"] = by_world
    out["variance"] = {key: variance_breakdown(cells, key) for key in ("price_floor", "ev")}
    out.update(next_report_blocks(A, release, spec))
    out["checks"] = checks_block(A, release)
    mean, pct = summarize(cells)
    px = A["market"]["price"]
    out["grid"] = dict(mean=mean, percentiles={str(k): v for k, v in pct.items()},
                       p_equity_nonpositive=sum(c.probability for c in cells if c.equity <= 0),
                       p_above_market=sum(c.probability for c in cells if c.price_floor > px),
                       by_world={w: b["mean"] for w, b in by_world.items()},
                       cells=[grid_cell_block(c) for c in cells])
    out["sensitivities"] = sensitivities = sensitivity_rows(A, judgements)
    out["peer_crosscheck"] = peer_crosscheck(A, layer_map)
    out["market_price_justification"] = market_price_justification(
        A, release, sensitivities, spec)
    if not slow:
        return out

    band = blocks["uncertainty"]
    out["reverse_dcf"] = copy.deepcopy(blocks["point_reverse_dcf"])
    out["uncertainty"] = {k: (_layer_draws(v) if k == "layer_draws" else copy.deepcopy(v))
                          for k, v in band.items()}
    out["headline"] = headline_of(A, release.fair_value, band)
    neutral = blocks["neutral"]
    if "error" in neutral:
        raise BookError(f"диагностики медианы: {neutral['error']}")
    out["median_diagnostics"] = dict(
        target="median", median_draws=median_draws(A),
        center_ev={name: dataclasses.asdict(center_ev_median(A, band, lam))
                   for name, lam in (("center", None), ("analytical", 1.0), ("macro_neutral", 0.0))},
        reverse_dcf=copy.deepcopy(blocks["reverse_dcf"]),
        next_report_value=copy.deepcopy(blocks["next_report"]),
        next_report_neutral=copy.deepcopy(neutral),
        evaluations=blocks["median_evaluations"])
    return out


# ------------------------------------------------------------------ печать


def _annual(rows: list[dict]) -> list[dict]:
    """Годовые агрегаты полугодий (первый год — только прогнозные полугодия)."""
    years: dict[str, dict] = {}
    for r in rows:
        y = r["period"][:4]
        a = years.setdefault(y, dict(year=y, revenue=0.0, ebitda=0.0, capex=0.0, fcff=0.0, tax_l=0.0,
                                     net_int=0.0, div=0.0, lfl=[], rate_d=[]))
        for k in ("revenue", "ebitda", "capex", "fcff", "tax_l", "net_int", "div"):
            a[k] += r[k]
        a["lfl"].append(r["lfl"])
        a["rate_d"].append(r["rate_d"])
        a.update(nd=r["nd"], lev=r["lev"], cover=r["cover"], area_end=r["area_end"])
    return list(years.values())


def _fmt_rows(rows: list[dict]) -> str:
    out = [f"{'год':>6}{'выручка':>9}{'LFL%':>6}{'маржа%':>7}{'EBITDA':>8}{'capex%':>7}{'FCFF':>7}"
           f"{'FCFF%':>6}{'налогФ':>7}{'%долг':>6}{'ЧД':>7}{'ЧД/E':>6}{'покр':>6}{'див':>7}{'площадь':>9}"]
    for a in _annual(rows):
        out.append(f"{a['year']:>6}{a['revenue']:>9.0f}{100*sum(a['lfl'])/len(a['lfl']):>6.1f}"
                   f"{100*a['ebitda']/a['revenue']:>7.2f}{a['ebitda']:>8.1f}{100*a['capex']/a['revenue']:>7.2f}"
                   f"{a['fcff']:>7.1f}{100*a['fcff']/a['revenue']:>6.2f}{a['tax_l']:>7.1f}"
                   f"{100*sum(a['rate_d'])/len(a['rate_d']):>6.1f}{a['nd']:>7.0f}{a['lev']:>6.2f}"
                   f"{min(a['cover'], 99):>6.2f}{a['div']:>7.1f}{a['area_end']:>9.0f}")
    return "\n".join(out)


def render_run_output(results: dict, A: dict) -> str:
    """Текст `run_output.txt`: печать таблиц книги из готового `results`.

    От книги — только входные литералы и подписи (дата оценки, число акций, веса
    сценариев, параметры правила A-P2u, подписи концов строк чувствительности).
    """
    lines: list[str] = []

    def emit(*parts) -> None:
        lines.append(" ".join(str(p) for p in parts) + "\n")

    px, sh = A["market"]["price"], A["facts"]["shares_out_mln"]
    U = A["joint"]["regime_update"]
    emit(f"Книга допущений v{A['meta']['version']}. Оценка на {A['meta']['valuation_date']}; "
         f"рынок {px} руб. x {sh} млн = {px*sh/1000:.1f} млрд руб.\n")

    for name, res in results["scenarios"].items():
        emit(f"=== {name} {res['cell']}\n    EV {res['ev']:.0f} (PV FCFF {res['pv_fcff']:.0f}; щит {res['pv_ts']:.0f}; "
             f"доля TV {100*res['tv_share']:.0f}%) | требования {res['claims']:.0f} | "
             f"капитал {res['equity']:.0f} | ЦЕНА клетки {res['price']:.0f} руб. (с полом "
             f"{res['price_floor']:.0f}) | EV/EBITDA LTM {res['ev_ebitda_ltm']:.2f}x, NTM {res['ev_ebitda_ntm']:.2f}x | "
             f"r_LT {100*res['r_lt']:.1f}% (реальная {100*res['r_real']:.1f}%), g терминала {100*res['g_terminal']:.2f}%, "
             f"мульт. выхода {res['exit_multiple']:.1f}x, FCFF_T {100*res['fcff_T_margin']:.2f}% выручки")
        emit(_fmt_rows(res["rows"]), "\n")
    emit("веса именованных сценариев, выведенные из сетки (клетка -> ближайший по EV сценарий): "
         + ", ".join(f"{k} {100*v:.0f}%" for k, v in results["weights_from_grid"].items())
         + f"; в книге: {A['weights']}")

    layers = results["layers"]
    order = ("macro_neutral", "market_implied", "analytical")
    emit("\nСЛОИ (V0 = E[EV], D = E[требования], млрд руб.; далее руб./акцию):")
    emit(f"  {'слой':<68}{'V0':>7}{'D':>7}{'внутренняя':>12}{'ЗАГОЛОВОК':>13}{'старый метод':>14}{'ср. без пола':>14}")
    for k in order:
        L = layers[k]
        emit(f"  {LAYER_NAMES[k]:<68}{L['v0']:>7.1f}{L['d']:>7.1f}{L['intrinsic']:>12.0f}{L['headline']:>13.0f}"
             f"{L['mean']:>14.0f}{L['mean_no_floor']:>14.0f}")
    emit("  распределение цен клеток с полом в нуле (P10 | P25 | P50 | P75 | P90) и доля вероятности клеток "
         "с DCF-капиталом <= 0:")
    for k in order:
        L = layers[k]
        emit(f"  {LAYER_NAMES[k]:<68}{L['p10']:>7.0f} |{L['p25']:>6.0f} |{L['p50']:>6.0f} |{L['p75']:>6.0f} "
             f"|{L['p90']:>6.0f} | {100*L['p_equity_nonpositive']:.0f}%")
    emit("  ВАЖНО: доля клеток с DCF-капиталом <= 0 — НЕ вероятность дефолта: в этих клетках компания "
         "обслуживает долг; отрицательный")
    emit("  DCF-капитал — следствие ставки дисконтирования r_u заметно выше стоимости долга.")
    emit("  потери кредиторов, которые неявно закладывает старый метод, % требований: " + "; ".join(
        f"{k}: {100*layers[k]['creditor_loss_floor_method_pct']:.1f}%" for k in ("macro_neutral", "analytical")))

    fv = results["fair_value"]
    emit(f"\nИТОГ (метод: {fv['method']}; вес собственного макро-взгляда "
         f"{fv['own_macro_confidence']:.2f}):")
    emit(f"  диапазон {fv['low']:.0f}–{fv['high']:.0f} руб., центр {fv['central']:.0f} руб.  ->  печатать: "
         f"{fv['printed']['low']}–{fv['printed']['high']} руб., центр {fv['printed']['central']} руб.; рынок {px} руб.")
    for method, label in (("intrinsic", "внутренняя стоимость (без пола)"), ("headline", "заголовочная оценка"),
                          ("old_floor_mean", "старый метод (среднее с полом)"),
                          ("mean_no_floor", "среднее цен клеток без пола")):
        b = fv["by_method"][method]
        emit(f"    {label:<34} рыночные ставки {b['low']:>6.0f} | центр {b['central']:>6.0f} | свой макро-взгляд "
             f"{b['high']:>6.0f}")
    for k in ("analytical", "macro_neutral"):
        e = fv["ev_comparison"][k]
        emit(f"  сравнение на уровне EV, слой «{LAYER_NAMES[k]}»: V0 {e['v0']:.0f} против рыночно-вменённого EV "
             f"{e['market_implied_ev']:.0f} (капитализация {e['market_cap']:.1f} + D {e['d']:.1f}): "
             f"{100*e['gap_pct']:+.1f}%; 1 % EV ≈ {e['rub_per_share_per_1pct_ev']:.0f} руб. на акцию; "
             f"EV/капитализация {e['market_ev_to_equity']:.1f}x")

    grid = results["grid"]
    cells, pct = grid["cells"], grid["percentiles"]
    below = sum(c["prob"] for c in cells if c["price_floor"] <= px)
    emit(f"\nСЕТКА ({len(cells)} клеток), распределение цен клеток с полом в нуле при аналитических весах: "
         f"P10 {pct['0.1']:.0f} | P25 {pct['0.25']:.0f} | P50 {pct['0.5']:.0f} | P75 {pct['0.75']:.0f} | "
         f"P90 {pct['0.9']:.0f}; доля клеток с DCF-капиталом <= 0: {100*grid['p_equity_nonpositive']:.0f}%; "
         f"доля клеток с ценой выше рынка: {100*grid['p_above_market']:.0f}%; рыночная цена — "
         f"{100*below:.0f}-й перцентиль")
    emit("\nмир x режим: вероятность | средний EV | средняя цена клеток (без пола) | EV/EBITDA LTM")
    for key, b in sorted(results["by_world_regime"].items(), key=lambda kv: tuple(kv[0].split("|"))):
        world, regime = key.split("|")
        emit(f"  {world:>2} x {regime:<8} {100*b['prob']:5.1f}% | {b['ev']:6.0f} | {b['price_no_floor']:8.0f} | "
             f"{b['ev_ebitda_ltm']:5.2f}x")
    emit("по мирам (V0 | внутренняя | заголовочная | старый метод): " + "; ".join(
        f"{k}: {v['v0']:.0f} | {v['intrinsic']:.0f} | {v['headline']:.0f} | {v['mean']:.0f}"
        for k, v in results["by_world"].items()))

    variance = results["variance"]
    emit("\nЧЕМ ОБЪЯСНЯЕТСЯ РАЗБРОС (доля дисперсии: цена клеток с полом | EV; оси сетки с v1.2 зависимы, "
         "доли могут пересекаться):")
    for axis, label in (("world", "мир ставок"), ("margin_regime", "режим маржи"), ("capex", "уровень capex")):
        gm = "; ".join(f"{k}: {v:.0f}" for k, v in variance["ev"]["group_mean"][axis].items())
        emit(f"  {label:<14} {100*variance['price_floor']['share'][axis]:4.0f}% | "
             f"{100*variance['ev']['share'][axis]:4.0f}%   средний EV: {gm}")
    emit(f"  стандартное отклонение EV по сетке {variance['ev']['sd']:.0f} млрд руб. = "
         f"{100*variance['ev']['sd']/variance['ev']['mean']:.0f}% от среднего EV")

    if results["next_report_value"]:
        emit(f"\nЧТО ДАСТ БЛИЖАЙШИЙ ОТЧЁТ: маржа EBITDA {U['demo_period']} (факт) -> вероятности режимов "
             f"(стресс/дно/частичный/полный) и точка при центральных значениях "
             f"(sigma {100*U['sigma_pp']:.2f} п.п., предел сдвига {U['max_shift_pp']})")
        for r in results["next_report_value"]:
            emit(f"  маржа {100*r['margin']:4.1f}%: " + " / ".join(f"{100*r['regime_prob'][g]:4.1f}%" for g in REGIMES)
                 + f" | диапазон {r['low']:.0f}–{r['high']:.0f}, центр {r['central']:.0f} (старый метод "
                 f"{r['old_method_central']:.0f}) | доля клеток с DCF-капиталом<=0 {100*r['p_equity_nonpositive']:.0f}%")
        slope = results["next_report_slope_rub_per_0p1pp"]
        emit(f"  чувствительность центра: {slope['headline']:.0f} руб. на 0,1 п.п. маржи (по старому методу "
             f"{slope['old_method']:.0f})")
        emit(f"  нейтральная маржа {U['demo_period']}: {100*results['next_report_neutral']['margin']:.2f} % "
             "(факт, при котором центр не меняется)")
        emit("  контрольные значения правила A-P2u (для сверки реализации в основном движке):")
        for c in results["regime_update_controls"]:
            emit(f"    {c['case']:<58} " + " / ".join(f"{100*c['regime_prob'][g]:4.1f}" for g in REGIMES)
                 + f" | {c['low']:.0f}–{c['high']:.0f}, центр {c['central']:.0f}")

    checks = results["checks"]
    cl = checks["credit_lines"]
    emit(f"\nКОНТРОЛИ: лимит долга {cl['limit']:.1f} = стартовый общий долг {cl['start_gross_debt']:.1f} + "
         f"неиспользованные линии {cl['undrawn_lines']:.1f}; худшая клетка {cl['worst_cell']}: максимум чистого "
         f"долга {cl['worst_max_net_debt']:.0f} ({cl['worst_period']}) = {100*cl['worst_share_of_limit']:.0f}% лимита; "
         f"клеток с превышением по чистому долгу: {len(cl['cells_over_limit_net_debt'])}, по общему долгу: "
         f"{len(cl['cells_over_limit_gross_debt'])}; максимум ЧД/EBITDA по сетке {cl['worst_max_leverage']:.1f}x")
    for x in cl["cells_over_limit_net_debt"]:
        emit(f"    превышение: {x['cell']} (вероятность {100*x['prob']:.1f}%): {x['max_net_debt']:.0f} в {x['period']}")
    ss = checks["spread_sign"]
    emit(f"  добавка стоимости долга в базе: максимум по клеткам {checks['debt_cost_addon_base_max']:.3f} "
         "млрд руб. (должна быть 0)")
    for name, _ in SPREAD_SIGN_CASES:
        s = ss[name]
        emit(f"  {name}: центр {ss['base_central']:.0f} -> {s['central']:.0f} "
             f"({'снижает' if s['lowers_headline'] else 'ПОВЫШАЕТ'}); по старому методу "
             f"{ss['base_old_method_central']:.0f} -> {s['old_method_central']:.0f}; EV базового сценария "
             f"{results['scenarios']['base']['ev']:.1f} -> {s['base_scenario_ev']:.1f}, добавка к требованиям "
             f"{s['base_scenario_debt_cost_addon']:.1f}")
    mi = checks["market_implied_10y"]
    emit(f"  10-летняя доходность, которую воспроизводят веса миров (рынок {100*mi['market_10y']:.2f}%): "
         f"аналитические {100*mi['analytical']['linear']:.2f}%, «рыночные» {mi['market_implied']['weights']} -> "
         f"{100*mi['market_implied']['linear']:.2f}% (через смесь дисконт-факторов "
         f"{100*mi['market_implied']['discount_factor_mix']:.2f}%)")
    emit(f"  ожидаемая долгосрочная маржа {100*checks['expected_lt_margin']:.2f}%; ожидание маржи "
         f"{U.get('demo_period', '')} {100*checks['expected_margin_demo_period']:.2f}%; безусловные "
         "вероятности capex: " + ", ".join(f"{k} {100*v:.1f}%" for k, v in checks["capex_prob_unconditional"].items()))

    base_price = results["scenarios"]["base"]["price"]
    emit(f"\nЦена ошибки допущений, руб./акцию: базовый сценарий ({base_price:.0f}) | точка при центральных "
         f"значениях ({fv['central']:.0f}) | центр по старому методу "
         f"({fv['by_method']['old_floor_mean']['central']:.0f})")
    labels = {s["name"]: (s.get("low_label"), s.get("high_label")) for s in A.get("sensitivities", [])}

    def short(v) -> str:
        return (str(v) if not isinstance(v, dict) else "/".join(str(x) for x in v.values()))[:24]

    for s in results["sensitivities"]:
        lo_l, hi_l = (labels[s["name"]][0] or short(s["low"])), (labels[s["name"]][1] or short(s["high"]))
        emit(f"  {s['name']:<72} {lo_l:>18}: {s['base_low']:>6.0f} | {s['central_low']:>6.0f} | "
             f"{s['old_method_central_low']:>6.0f}   {hi_l:>18}: {s['base_high']:>6.0f} | {s['central_high']:>6.0f} | "
             f"{s['old_method_central_high']:>6.0f}   размах {s['swing_base']:>5.0f} | {s['swing_central']:>5.0f} | "
             f"{s['swing_old_method_central']:>5.0f}")

    pc = results["peer_crosscheck"]
    emit(f"\nНЕЗАВИСИМАЯ СВЕРКА АНАЛОГАМИ (не вход модели): V0 = мультипликатор x EBITDA за 12 месяцев "
         f"{pc['ebitda_ltm']:.1f} против D слоя «свой макро-взгляд» {pc['d']:.1f}; заголовочная оценка — та же "
         f"формула (дисконт за управление {100*pc['governance_discount']:.0f}%)")
    for r in pc["peers"]:
        emit(f"  {r['peer']:<6} {r['multiple']:.1f}x -> V0 {r['v0']:.0f} млрд руб.: внутренняя {r['intrinsic']:.0f}, "
             f"заголовочная {r['headline']:.0f} руб.")
    emit("  модельный V0 в единицах EBITDA за 12 месяцев: " + "; ".join(
        f"{LAYER_NAMES[k].split(' (')[0]} {v:.2f}x" for k, v in pc["model_v0_multiple"].items())
         + f"; рыночно-вменённый EV {pc['market_implied_ev_multiple']:.2f}x")

    mj = results["market_price_justification"]
    emit(f"\nЧТО ОПРАВДЫВАЕТ РЫНОЧНУЮ ЦЕНУ {mj['market_price']} руб.: заголовочная оценка слоя «свой "
         f"макро-взгляд» равна рынку при V0 = {mj['v0_required']:.0f} млрд руб. ({100*mj['v0_gap_pct']:+.1f}% к "
         f"модельному V0 {mj['v0_model']:.0f}; {mj['v0_required_multiple']:.2f}x EBITDA за 12 месяцев)")
    emit("  одиночные изменения допущений -> точка при центральных значениях:")
    for c in mj["cases"]:
        # Число — десятью знаками: полная запись float (σ калибровки) разная на
        # Windows и Linux в последних битах, а печать обязана совпадать.
        value = f"{c['value']:.10g}" if isinstance(c["value"], float) else str(c["value"])
        emit(f"  {c['assumption']:<46} {value:<24} -> {c['central']:>6.0f} "
             f"{'достигает рынка' if c['reaches_market'] else ''}   ({c['note']})")

    ce = fv["center_ev"]
    emit(f"\nEV ТОЧКИ: V0 {ce['v0']:.1f} против V* {ce['v_star']:.1f} ({100*ce['gap_vs_v_star']:+.1f}%); "
         f"1 % EV = {ce['rub_per_1pct_ev']:.0f} руб. точки; вклад взгляда на инфляцию и ставки "
         f"{fv['rates_view']['rub']:.0f} руб. ({fv['rates_view']['v0']:.1f} млрд руб. V0)")
    for k in ("analytical", "macro_neutral"):
        e = fv["ev_comparison"][k]
        emit(f"  {LAYER_NAMES[k]}: V0 {e['v0']:.1f} против V* {e['v_star']:.1f} ({100*e['gap_vs_v_star']:+.1f}%); "
             f"«капитализация + D» {e['market_implied_ev']:.1f}")

    def root(x) -> str:
        return "недостижимо в пределах поиска" if x["value"] is None else f"{x['value']:.4f}"

    reverse = results.get("reverse_dcf")
    if reverse:
        emit(f"\nОБРАТНЫЙ DCF: одно суждение, при котором точка при центральных значениях = рынку {px} руб. "
             "(остальное — как в книге):")
        for x in reverse:
            book_value = x["book_value"] if x["book_value"] is not None else "сдвиг 0"
            emit(f"  {x['name']:<58} книга {book_value} -> {root(x)} "
                 f"({'внутри' if x['inside_range'] else 'ВНЕ'} диапазона книги {x['range']})")
    band = results.get("uncertainty")
    if band:
        c = band["central"]
        emit(f"\nПОЛОСА НЕОПРЕДЕЛЁННОСТИ (Монте-Карло по {len(A['valuation']['uncertainty']['axes'])} суждениям, "
             f"{band['draws']} прогонов): центр P10 {c['0.10']:.0f} | P25 {c['0.25']:.0f} | P50 {c['0.50']:.0f} | "
             f"P75 {c['0.75']:.0f} | P90 {c['0.90']:.0f} руб.; среднее {band['mean_central']:.0f}; "
             f"P(центр < рынка {px}) = {100*band['p_central_below_market']:.0f} %")
        emit("  вклад суждений (доля квадрата ранговой корреляции с центром): "
             + "; ".join(f"{x['axis']} {100*x['share']:.0f}%" for x in band["contributions"][:8]))
        hl = results["headline"]
        emit(f"\nПЕЧАТАЕМЫЙ ЗАГОЛОВОК: медиана по суждениям {hl['printed_central']:.0f} руб.; 80 % — "
             f"{hl['printed_band'][0]:.0f}–{hl['printed_band'][1]:.0f}, 50 % — {hl['printed_inner'][0]:.0f}–"
             f"{hl['printed_inner'][1]:.0f}; при центральных значениях всех суждений книги {hl['printed_point']:.0f} "
             f"руб.; P(ниже рынка {px}) = {100*hl['p_central_below_market']:.0f} %")
    md = results.get("median_diagnostics")
    if md:
        ce = md["center_ev"]["center"]
        emit(f"\nДИАГНОСТИКИ ПЕЧАТАЕМОЙ МЕДИАНЫ (1.5; пересчёт на {md['median_draws']} прогонах, "
             f"{md['evaluations']} пересчётов): EV медианы {ce['v0']:.1f} против V* {ce['v_star']:.1f} "
             f"({100*ce['gap_vs_v_star']:+.1f}%); 1 % EV = {ce['rub_per_1pct_ev']:.0f} руб. медианы")
        for k in ("analytical", "macro_neutral"):
            e = md["center_ev"][k]
            emit(f"  {LAYER_NAMES[k]}: V0 {e['v0']:.1f} против V* {e['v_star']:.1f} ({100*e['gap_vs_v_star']:+.1f}%)")
        emit(f"  обратный DCF — одно суждение, при котором МЕДИАНА = рынку {px} руб.:")
        for x in md["reverse_dcf"]:
            emit(f"    {x['name']:<58} -> {root(x)} ({'внутри' if x['inside_range'] else 'ВНЕ'} диапазона книги "
                 f"{x['range']})")
        emit("  что даст отчёт (медиана): " + "; ".join(
            f"{100*r['margin']:.1f}% -> {r['central']:.0f}" for r in md["next_report_value"])
             + f"; нейтральная маржа {100*md['next_report_neutral']['margin']:.2f} %")
    return "".join(lines)


# ------------------------------------------------------------------- запуск


def write(results: dict, A: dict, out_dir: Path) -> tuple[Path, Path]:
    """Пишет `results.json` и `run_output.txt`; перевод строк — LF на любой ОС."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = out_dir / "results.json", out_dir / "run_output.txt"
    with open(paths[0], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    with open(paths[1], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_run_output(results, A))
    return paths


def fast_summary(A: dict) -> str:
    """Быстрый путь: выпуск на книге как есть (сетка 36 клеток, слои, точка,
    именованные сценарии, гейты) — без полосы A-V9, таблицы суждений и
    диагностик медианы. Файлов не пишет: это проба «книга считается ядром»,
    а не таблицы книги (их пишет полный прогон)."""
    import time

    started = time.perf_counter()
    release = run_release(A, gates=True)
    fv = release.fair_value
    lines = [f"книга {A['meta']['version']} · дата оценки {A['meta']['valuation_date']} · "
             f"цена {A['market']['price']} ₽ · метод {headline_block(A)['method']}"]
    for key, layer in release.layers.items():
        lines.append(f"  слой {key}: V0 {layer.v0:.1f}, D {layer.claims:.1f} млрд ₽ → "
                     f"внутренняя {layer.intrinsic:.0f} ₽, заголовок {layer.headline:.0f} ₽")
    lines.append(f"  точка: низ {fv.low:.0f} / центр {fv.central:.0f} / верх {fv.high:.0f} ₽ "
                 f"(печать {fv.printed_central:.0f}); λ {fv.own_macro_confidence}; "
                 f"V* {fv.center_ev.v_star:.1f} против V0 {fv.center_ev.v0:.1f} млрд ₽")
    for name, cell in release.named.items():
        lines.append(f"  сценарий {name}: EV {cell.ev:.1f}, требования {cell.claims:.1f} млрд ₽, "
                     f"цена {cell.price:.0f} ₽, EV/EBITDA LTM {cell.ev_ebitda_ltm:.2f}")
    lines.append("  вероятности режимов: " + ", ".join(
        f"{r} {p:.3f}" for r, p in release.regime_unconditional.items()))
    for gate in release.gate_summary:
        lines.append(f"  гейт {gate.key}: масса {gate.mass:.3f}, клеток {gate.cells}, "
                     f"{'объяснён' if gate.explained else 'НЕ ОБЪЯСНЁН'}"
                     f"{', совещательный' if gate.advisory else ''}")
    blocking = [f.key for f in release.findings if f.blocking]
    lines.append(f"  инварианты: {'нарушены — ' + ', '.join(sorted(set(blocking))) if blocking else 'чисто'}; "
                 f"расчёт {time.perf_counter() - started:.1f} с")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="results.json и run_output.txt книги расчётом ядра")
    parser.add_argument("--out", type=Path, default=BOOK_DIR,
                        help="каталог для обоих файлов (по умолчанию — каталог книги)")
    parser.add_argument("--fast", action="store_true",
                        help="быстрый путь: выпуск на книге (сетка, слои, точка, сценарии, гейты) "
                             "без полосы и диагностик; файлов не пишет")
    args = parser.parse_args(argv)
    A = book()
    if args.fast:
        sys.stdout.write(fast_summary(A))
        return 0
    for path in write(book_results(A), A, args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
