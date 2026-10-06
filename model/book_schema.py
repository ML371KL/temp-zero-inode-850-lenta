"""Закрытая схема книги: какие ключи ядро знает.

Ядро читает часть книги через `.get(ключ, умолчание)`, и опечатка в имени
ключа или блока молча дала бы умолчание. Поэтому незнакомый ключ — отказ.

Схема — снимок, а не вывод из текущей книги на лету: иначе опечатка в новой
книге сама себя узаконила бы. Новый ключ книги регистрируется здесь тем же
коммитом, что и код, который его читает; имя берётся из кода, а не из YAML.

Путь узла — ключи через точку; `[]` — элемент списка; `*P` — значение
траектории; `*` — блок с произвольными именами (сегменты сети: имя сегмента —
данные книги, а не ключ схемы; имена проверяет `model.book.segments`). Словарь
траектории (ключи — годы, полугодия, сроки кривой, `LT`, `LT_from`)
проверяется правилом, а не списком: все его ключи обязаны быть такими.
Отсутствие ключа схема не проверяет — это дело читателей книги.
"""

from __future__ import annotations

import re
from typing import Any

PERIOD_KEY = re.compile(r"^\d{4}(H[12])?$")
TRAJECTORY_WORDS = frozenset({"LT", "LT_from"})
TRAJECTORY = "*P"
ANY = "*"
# Блоки, ключи которых — имена сегментов сети (`model.book.segments`).
NAMED = frozenset({"revenue.segments", "facts.segments", "capex.segments",
                   "capex.physical.steady_per_m2", "capex.physical.young_per_m2",
                   "capex.physical.reconstruction_cycle_years.base",
                   "capex.physical.reconstruction_cycle_years.high",
                   "capex.physical.reconstruction_cycle_years.low",
                   "capex.physical.reference_area", "capex.physical.young_stock"})

# Блоки со свободными ключами (проверяет читатель, а не схема); пока таких нет.
FREE: frozenset[str] = frozenset()

_REGIMES = frozenset({"floor", "full", "partial", "stress"})
_WORLDS = frozenset({"H", "M", "N"})
_LEVELS = frozenset({"base", "high", "low"})
_SCENARIOS = frozenset({"base", "bear", "bull"})
_JOINT_CHOICE = frozenset({"credit", "growth", "nwc"})
# Запись мира. `wage_growth`, `tariff_growth`, `ofz_10y_path` ядро не читает: это
# поля рецепта миров, который переносится из 850oa как есть (миры — макро, а не
# компания); ключи разрешены, чтобы запись мира не приходилось переписывать.
_WORLD = frozenset({"cpi", "food_cpi", "key_rate", "lt", "name", "ofz_10y_path",
                    "tariff_growth", "wage_growth", "zero_curve"})
_SCENARIO = frozenset({"capex", "credit", "demand", "growth", "margin_regime", "nwc", "world"})
_QUARTERS = frozenset({"Q1", "Q2", "Q3", "Q4"})
_STRESS_LEVELS = frozenset({"base", "stress"})
# Ключи осей полосы и строк чувствительностей (`model.book.AXIS_KEYS`).
_AXIS = frozenset({"high", "high_label", "kind", "low", "low_label", "name", "path", "paths",
                   "shift_high", "shift_low"})

# Строки моста (`model.book.BRIDGE_ITEM_KEYS`; выплата выводится из строки с
# `settle_period`), факты якоря (`model.book.ANCHOR_KEYS`), сегменты сети
# (`model.book.SEGMENT_REVENUE_KEYS`, `SEGMENT_FACT_KEYS`, `SEGMENT_CAPEX_KEYS`).
_BRIDGE_ITEM = frozenset({"accrete_rate_half", "amount", "as_of", "basis", "haircut", "id",
                          "kind", "name", "settle_amount", "settle_period"})
_ANCHOR = frozenset({"capex", "cash", "da_pre16", "ebitda_ltm", "ebitda_ltm_reported",
                     "fcfe_ytd", "margin_pro_forma", "margin_pro_forma_se", "net_debt", "period",
                     "revenue_ltm"})
_SEGMENT_REVENUE = frozenset({"closed_productivity", "density_path", "growth", "h1_share",
                              "lfl_offset", "mode", "name", "new_space_density", "other_growth",
                              "space"})
# `revenue_se` — стандартная ошибка базы выручки (D3, решение ведущего B13);
# `new_area_dense_cohorts` — сколько младших исторических когорт дозревают до d;
# `closed_area_hist` — закрытия двух полугодий до якоря: история эффективной
# площади выводится правилом сети (`model.core.effective_history`, E12).
_SEGMENT_FACTS = frozenset({"area_end", "closed_area_hist", "eff_area_avg_hist",
                            "new_area_dense_cohorts", "new_area_gross_hist", "revenue",
                            "revenue_basis", "revenue_se", "stores_end"})

# Схема книги.
BOOK: dict[str, frozenset[str]] = {
    "": frozenset({"bridge", "capex", "facts", "financing", "joint", "margin", "market", "meta",
                   "nwc", "reference_class_margin", "revenue", "reverse_dcf", "scenario_names",
                   "scenarios", "sensitivities", "tax", "valuation", "weights", "worlds"}),
    "bridge": frozenset({"items"}),
    "bridge.items[]": _BRIDGE_ITEM,
    # `unit_price_basis` — дата удельных цен открытий и инфраструктуры (control-model-03);
    # `physical` — физическая часть поддерживающего capex по форматам и когортам (capex-04).
    "capex": frozenset({"asset_life_years", "disposal_proceeds_pct", "infra_capex_per_net_m2",
                        "infra_from_year", "integration_capex", "maintenance_area_share",
                        "maintenance_pct", "physical", "segments", "unit_price_basis"}),
    "capex.physical": frozenset({"reconstruction_cycle_years", "steady_per_m2", "young_per_m2"}),
    "capex.physical.reconstruction_cycle_years": _LEVELS,
    "capex.maintenance_pct": _LEVELS,
    "capex.segments": frozenset({ANY}),
    "capex.segments.*": frozenset({"growth_capex_per_m2"}),
    # `guidance` — гайденс компании по марже года (гейт `guidance_gap`, C20).
    "facts": frozenset({"anchor", "ebitda_pre16", "guidance", "reported", "revenue", "segments",
                        "shares_out_mln", "undrawn_credit_lines"}),
    "facts.guidance": frozenset({"ebitda_margin_min", "period", "source"}),
    "facts.anchor": _ANCHOR,
    "facts.reported": frozenset({"ebitda_pre16", "revenue"}),
    "facts.segments": frozenset({ANY}),
    "facts.segments.*": _SEGMENT_FACTS,
    # `dividend_timing`, `dividend_net_debt_basis` — сроки и база ЧД лестницы (F1).
    "financing": frozenset({"cash_yield_k", "dividend_ladder", "dividend_net_debt_basis",
                            "dividend_timing", "dividends_from_year",
                            "fixed_share", "leverage_target", "liquidity_buffer_pct",
                            "operating_cash_pct", "prefunded_cash_decay", "rate_baskets",
                            "spread_fair", "spread_fixed", "spread_float"}),
    "financing.dividend_ladder[]": frozenset({"max_leverage", "payout_max"}),
    "financing.rate_baskets[]": frozenset({"basis", "name", "rate", "share", "until"}),
    # Ставка корзины: `{fixed: x}` или `{key_plus: s}` (решение ведущего A10).
    "financing.rate_baskets[].rate": frozenset({"fixed", "key_plus"}),
    "financing.spread_fair": frozenset({"fixed", "float"}),
    "financing.spread_fair.fixed": _STRESS_LEVELS,
    "financing.spread_fair.float": _STRESS_LEVELS,
    "financing.spread_fixed": _STRESS_LEVELS,
    "financing.spread_float": _STRESS_LEVELS,
    "joint": frozenset({"by_world", "capex_prob_given_regime", "demand_by_regime",
                        "growth_by_regime_override", "macro_neutral_world",
                        "own_macro_confidence", "regime_given_world", "regime_update",
                        "world_prob", "world_prob_market_implied"}),
    "joint.by_world": _WORLDS,
    **{f"joint.by_world.{w}": _JOINT_CHOICE for w in _WORLDS},
    "joint.capex_prob_given_regime": _REGIMES,
    **{f"joint.capex_prob_given_regime.{r}": _LEVELS for r in _REGIMES},
    "joint.demand_by_regime": _REGIMES,
    "joint.growth_by_regime_override": frozenset({"stress"}),
    "joint.regime_given_world": _WORLDS,
    **{f"joint.regime_given_world.{w}": _REGIMES for w in _WORLDS},
    "joint.regime_update": frozenset({"demo_period", "demo_values", "max_shift_pp",
                                      "observations", "sigma_pp", "sigma_range"}),
    "joint.regime_update.observations": frozenset(),
    "joint.world_prob": _WORLDS,
    "joint.world_prob_market_implied": _WORLDS,
    # `quarter_sigma_pp` — σ квартала нау-каста (`indicators.quarterly`), необязательный.
    "margin": frozenset({"cash_lease_adj_pct", "deviation_persistence",
                         "nowcast_margin_shocks_pp", "quarter_offset_pp", "quarter_sigma_pp",
                         "regimes", "season_free_halves", "season_from_period",
                         "seasonal_h1_pp"}),
    "margin.quarter_offset_pp": _QUARTERS,
    "margin.nowcast_margin_shocks_pp": frozenset(),
    "margin.regimes": _REGIMES,
    **{f"margin.regimes.{r}": frozenset({"target"}) for r in _REGIMES},
    # Цена книги и её конвенция (`price_convention`: legalclose и т. п.) и дата —
    # справочно: ядро считает на `price`.
    "market": frozenset({"peers", "price", "price_convention", "price_date",
                         "sellside_summary", "sellside_targets"}),
    # Аналоги сверки мультипликатором — данными книги: {key, name, ev_ebitda,
    # subject, basis, as_of}; `subject: true` — сама компания (справочно, в
    # сверку не входит).
    "market.peers[]": frozenset({"as_of", "basis", "ev_ebitda", "key", "name", "subject"}),
    # Цели инвестдомов — список последних целей домов и агрегаты рядом (E32).
    "market.sellside_targets[]": frozenset({"date", "house", "rating", "source", "target"}),
    "market.sellside_summary": frozenset({"as_of", "max", "mean", "median", "min", "n", "note"}),
    # `company` и `period_unit` — справочно (витрина, выпуск); шаг ядра —
    # полугодие, другое значение `period_unit` — отказ (`model.book.meta_rules`).
    "meta": frozenset({"bridge_as_of", "company", "facts_date", "first_period", "last_period",
                       "period_unit", "valuation_date", "version"}),
    "meta.company": frozenset({"name", "ticker"}),
    # `anchor_level` — ОК якоря по балансу (старт вместо `nwc_pct_start`, D27);
    # `quarter_share` — квартальные веса изменения ОК для переката (A9).
    "nwc": frozenset({"acquired_path", "anchor_level", "nwc_pct", "nwc_pct_start",
                      "quarter_share", "seasonal_june_excess"}),
    "nwc.quarter_share": _QUARTERS,
    "nwc.nwc_pct": frozenset({"full", "half", "hold", "partial"}),
    "reference_class_margin": frozenset({"e_m_lt", "mean_target_by_regime", "n", "note",
                                         "share"}),
    "reference_class_margin.mean_target_by_regime": frozenset({"floor", "partial", "stress"}),
    "reference_class_margin.share": _REGIMES,
    "revenue": frozenset({"maturity_curve", "quarter_share", "segments", "ticket_k",
                          "ticket_lt_homogeneity", "ticket_shift", "traffic", "vat_adjustment"}),
    "revenue.quarter_share": _QUARTERS,
    "revenue.segments": frozenset({ANY}),
    "revenue.segments.*": _SEGMENT_REVENUE,
    "revenue.segments.*.lfl_offset": frozenset({"by_regime"}),
    "revenue.segments.*.lfl_offset.by_regime": _REGIMES,
    "revenue.segments.*.space": frozenset({"high", "low", "mid"}),
    **{f"revenue.segments.*.space.{s}": frozenset({"close", "gross_open"})
       for s in ("high", "low", "mid")},
    "revenue.ticket_k": _SCENARIOS,
    "revenue.ticket_lt_homogeneity": frozenset({"ramp_from", "ramp_to", "reference_world"}),
    "revenue.ticket_shift": _SCENARIOS,
    "revenue.traffic": _SCENARIOS,
    "reverse_dcf[]": frozenset({"kind", "name", "paths", "range", "search"}),
    "scenario_names": _SCENARIOS,
    "scenarios": _SCENARIOS,
    **{f"scenarios.{s}": _SCENARIO for s in _SCENARIOS},
    "sensitivities[]": _AXIS,
    # Концы-словари: веса миров или вероятности режимов (ось A-P2).
    "sensitivities[].high": _WORLDS | _REGIMES,
    "sensitivities[].low": _WORLDS | _REGIMES,
    **{f"sensitivities[].{end}.{w}": _JOINT_CHOICE for end in ("high", "low") for w in _WORLDS},
    # `capex_tax_premium_share` — ускоренная налоговая амортизация (D25);
    # `acquired_nol.discount_rule` — {method, haircut, locked_addback} (D26);
    # `nondeductible_da_anchor` — невычитаемая часть D&A якоря (control-model-02).
    "tax": frozenset({"acquired_nol", "alpha", "alpha_terminal_shield", "capex_tax_premium_share",
                      "nol_full_from_year", "nol_limit", "nol_start", "nondeductible_da_anchor",
                      "permanent_addback_pct", "rate"}),
    "tax.acquired_nol": frozenset({"amount", "discount_rule", "usable_from"}),
    "tax.acquired_nol.discount_rule": frozenset({"haircut", "locked_addback", "method"}),
    # Пустой путь прибавок (убытков запертых юрлиц в прогнозе нет) — пустой блок.
    "tax.acquired_nol.discount_rule.locked_addback": frozenset(),
    "valuation": frozenset({"beta_u", "distress", "erp", "governance_components",
                            "governance_discount", "headline", "market_curve_years",
                            "ronic_spread", "terminal", "terminal_real_growth", "uncertainty"}),
    # `in_850oa_scope` — канал входит в охват 850oa (строка витрины, E31).
    "valuation.governance_components[]": frozenset({"basis", "in_850oa_scope", "name", "sign",
                                                    "value"}),
    # `revenue_base` — база выручки терминала (discount-terminal-01); `da_convention` —
    # амортизация терминала в налоге (capex-06); `shield_leverage` — рычаг долга
    # терминального щита: ключ L или средний рычаг цикла (проверка пакета аудита).
    "valuation.terminal": frozenset({"da_convention", "half_rate_convention", "revenue_base",
                                     "shield_leverage"}),
    "valuation.distress": frozenset({"cost_pct_ev", "credit_limit_trigger",
                                     "net_leverage_trigger"}),
    # Заголовок: метод (только `intrinsic`), шаг печати, диагностики, пороги
    # скачка и гейта ограниченной ответственности (`model.book.headline_block`).
    "valuation.headline": frozenset({"diagnostics", "jump_guard", "limited_liability", "method",
                                     "print_step"}),
    "valuation.headline.jump_guard": frozenset({"median_pct", "v0_pct"}),
    "valuation.headline.limited_liability": frozenset({"max_share", "v0_to_d_min"}),
    "valuation.uncertainty": frozenset({"axes", "draws", "median_draws", "quantiles", "seed"}),
    "valuation.uncertainty.axes[]": _AXIS,
    "valuation.uncertainty.axes[].high": _WORLDS | _REGIMES,
    "valuation.uncertainty.axes[].low": _WORLDS | _REGIMES,
    "weights": _SCENARIOS,
    "worlds": _WORLDS,
    **{f"worlds.{w}": _WORLD for w in _WORLDS},
    **{f"worlds.{w}.lt": frozenset({"inflation"}) for w in _WORLDS},
}

# Ключи, которые дописывают в книгу перезаякоривание (`ops/tools/reanchor.py`),
# живые входы (`model/live.py`) и пересборка миров (`ops/tools/refresh_worlds.py`).
WRITTEN: dict[str, frozenset[str]] = {
    "meta": frozenset({"curve_as_of"}),
    "capex": frozenset({"maintenance_area_base"}),
    # эталонная сеть A-K1 и запас новой площади сверх неё на конец закрытых полугодий
    "capex.physical": frozenset({"reference_area", "young_stock"}),
    "facts": frozenset({"da_straight_line"}),
    "facts.segments.*": frozenset({"eff_area_end"}),
    "facts.da_straight_line": frozenset({"legacy", "legacy_halves", "vintages"}),
    f"joint.regime_update.observations.{TRAJECTORY}": frozenset({"se", "value"}),
}

# Необязательные ключи правил диагностик медианы и сложения осей полосы на общем
# пути (без ключа — первое значение: `model.book.REVERSE_BOUNDS`,
# `MEDIAN_REFINE_STEPS`, `AXIS_MERGES`).
OPTIONAL: dict[str, frozenset[str]] = {
    "valuation.uncertainty": frozenset({"axis_merge", "median_refine", "reverse_bounds"}),
}


def _merge(*parts: dict[str, frozenset[str]]) -> dict[str, frozenset[str]]:
    out: dict[str, frozenset[str]] = {}
    for part in parts:
        for path, keys in part.items():
            out[path] = out.get(path, frozenset()) | keys
    return out


SCHEMA: dict[str, frozenset[str]] = _merge(BOOK, WRITTEN, OPTIONAL)


def is_trajectory_key(key: Any) -> bool:
    if isinstance(key, bool):
        return False
    if isinstance(key, int):
        return True
    return isinstance(key, str) and (key in TRAJECTORY_WORDS or PERIOD_KEY.match(key) is not None)


def _child(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def unknown_keys(A: Any, path: str = "", out: list[str] | None = None) -> list[str]:
    """Незнакомые ключи книги строками «путь: что не так»; пусто — книга в схеме."""
    out = [] if out is None else out
    if isinstance(A, dict):
        if path in FREE:
            return out
        if path in NAMED:
            for value in A.values():
                unknown_keys(value, _child(path, ANY), out)
            return out
        keys = list(A)
        trajectory = any(is_trajectory_key(k) for k in keys)
        if trajectory:
            odd = [k for k in keys if not is_trajectory_key(k)]
            if odd:
                out.append(f"{path or 'книга'}: в траектории ключи не периоды: "
                           f"{', '.join(map(str, odd))}")
            for key, value in A.items():
                unknown_keys(value, _child(path, TRAJECTORY), out)
            return out
        known = SCHEMA.get(path)
        if known is None:
            out.append(f"{path}: незнакомый блок")
            return out
        odd = sorted(str(k) for k in keys if k not in known)
        if odd:
            out.append(f"{path or 'книга'}: незнакомый ключ {', '.join(odd)} "
                       f"(известны: {', '.join(sorted(known)) or 'нет'})")
        for key, value in A.items():
            if key in known:
                unknown_keys(value, _child(path, str(key)), out)
    elif isinstance(A, list):
        for value in A:
            unknown_keys(value, f"{path}[]", out)
    return out
