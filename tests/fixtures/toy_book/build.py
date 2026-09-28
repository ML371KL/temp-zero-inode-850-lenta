# -*- coding: utf-8 -*-
"""Синтетическая книга в схеме Ленты для тестов ядра (DESIGN D17).

Числа выдуманы и круглые: книга проверяет МЕХАНИКУ ядра, а не оценку
компании. В ней есть всё, что схема 1.0 добавила к книге 850oa, — чтобы общие
тесты шли на ней до книги 1.0 и после неё:

* сегменты сети во всех трёх режимах выручки: `yoy` (гипермаркеты, «у дома»,
  приобретённая сеть с `lfl_offset` по режимам маржи), `level` (товары для
  дома: площадь × плотность × индекс цен × доля полугодия), `revenue` (опт);
* метод заголовка `intrinsic` с гейтом `limited_liability` и порогами скачка;
* строки моста с расчётом (`settle_period`, с `settle_amount` и без), активы с
  `haircut`, наращение за полугодие;
* корзины ставок действующего долга, дивидендная лестница;
* ОК и запертые убытки приобретённого периметра, интеграционный capex;
* маржа якоря на проформе с ошибкой (`margin_pro_forma_se`), квартальные доли
  выручки и поправки маржи, разложение дисконта за управление;
* соглашение полугодовой ставки терминала `compound`.

Запуск: `python tests/fixtures/toy_book/build.py` — пишет `assumptions.yaml`
рядом (идемпотентно). Тесты читают YAML, а не этот модуль: файл — то, что
видит ядро; сборщик — как он получен.
"""
from __future__ import annotations

from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
OUT = HERE / "assumptions.yaml"

REGIMES = ("stress", "floor", "partial", "full")
LEVELS = ("low", "mid", "high")


def lt(value: float) -> dict:
    return {"LT": value}


def space(low: tuple, mid: tuple, high: tuple) -> dict:
    """Сценарии площади {low, mid, high}: (валовые открытия, закрытия) — доли в год."""
    return {name: {"gross_open": o if isinstance(o, dict) else lt(o),
                   "close": c if isinstance(c, dict) else lt(c)}
            for name, (o, c) in zip(LEVELS, (low, mid, high))}


def world(name: str, key: dict, cpi: float, curve: dict, lt_inflation: float) -> dict:
    return {
        "name": name,
        "key_rate": key,
        "cpi": lt(cpi),
        "food_cpi": lt(cpi),
        "wage_growth": lt(cpi + 0.02),
        "ofz_10y_path": lt(curve[10]),
        "tariff_growth": lt(cpi),
        "zero_curve": curve,
        "lt": {"inflation": lt_inflation},
    }


# Сегменты: выручка по полугодиям (млрд ₽), площадь (тыс. м²), когорты.
SEGMENT_FACTS = {
    "hyper": dict(revenue={"2025H1": 200.0, "2025H2": 230.0, "2026H1": 205.0},
                  revenue_basis={"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
                  area_end=1600.0, stores_end=150, new_area_gross_hist=[5.0, 8.0, 4.0, 6.0],
                  eff_area_avg_hist={"2025H2": 1580.0, "2026H1": 1585.0}),
    "conv": dict(revenue={"2025H1": 150.0, "2025H2": 165.0, "2026H1": 170.0},
                 revenue_basis={"2025H1": "reported", "2025H2": "reported", "2026H1": "reported"},
                 area_end=900.0, stores_end=3000, new_area_gross_hist=[30.0, 40.0, 35.0, 45.0],
                 eff_area_avg_hist={"2025H2": 830.0, "2026H1": 860.0}),
    "acq": dict(revenue={"2025H1": 60.0, "2025H2": 70.0, "2026H1": 62.0},
                revenue_basis={"2025H1": "estimate", "2025H2": "estimate", "2026H1": "pro_forma"},
                area_end=500.0, stores_end=70, new_area_gross_hist=[],
                eff_area_avg_hist={"2025H2": 505.0, "2026H1": 500.0}),
    "diy": dict(revenue={"2025H1": 18.0, "2025H2": 22.0, "2026H1": 17.0},
                revenue_basis={"2025H1": "reported", "2025H2": "reported", "2026H1": "estimate"},
                area_end=120.0, stores_end=20, new_area_gross_hist=[]),
    "wholesale": dict(revenue={"2025H1": 12.0, "2025H2": 13.0, "2026H1": 11.0},
                      revenue_basis={"2025H1": "reported", "2025H2": "reported",
                                     "2026H1": "reported"}),
}
GROUP_REVENUE = {p: sum(s["revenue"][p] for s in SEGMENT_FACTS.values())
                 for p in ("2025H1", "2025H2", "2026H1")}
GROUP_EBITDA = {"2025H1": 28.6, "2025H2": 37.5, "2026H1": 27.9}

SEGMENTS = {
    "hyper": {"name": "Гипермаркеты", "mode": "yoy", "new_space_density": 0.9,
              "closed_productivity": 0.6,
              "space": space((0.0, 0.01), (0.01, 0.01), (0.02, 0.01))},
    "conv": {"name": "Магазины у дома", "mode": "yoy", "new_space_density": 0.85,
             "closed_productivity": 0.5, "lfl_offset": lt(0.01),
             "space": space((0.03, 0.02), ({"2026H2": 0.06, "2027": 0.05, "LT": 0.04}, 0.02),
                            (0.08, 0.02))},
    "acq": {"name": "Приобретённая сеть", "mode": "yoy", "new_space_density": 1.0,
            "closed_productivity": 0.4,
            # Сходимость плотности приобретённой сети — по режиму интеграции.
            "lfl_offset": {"by_regime": {
                "stress": lt(-0.01),
                "floor": lt(0.0),
                "partial": {"2026H2": 0.0, "2027": 0.02, "2028": 0.03, "LT": 0.0, "LT_from": 2030},
                "full": {"2026H2": 0.0, "2027": 0.04, "2028": 0.05, "LT": 0.0, "LT_from": 2030}}},
            "space": space((0.0, {"2026H2": 0.05, "LT": 0.0}), (0.0, {"2026H2": 0.03, "LT": 0.0}),
                           (0.0, {"2026H2": 0.02, "LT": 0.0}))},
    "diy": {"name": "Товары для дома", "mode": "level", "new_space_density": 1.0,
            "closed_productivity": 0.6,
            # Плотность — тыс. ₽ на м² эффективной площади в год, в ценах
            # двенадцати месяцев до якоря (разгон после ребрендинга).
            "density_path": {"2026H2": 250.0, "2027": 300.0, "2028": 340.0, "LT": 350.0,
                             "LT_from": 2030},
            "h1_share": 0.45,
            "space": space((0.0, 0.0), (0.02, 0.0), (0.04, 0.0))},
    "wholesale": {"name": "Опт и прочая выручка", "mode": "revenue",
                  "growth": {"2026H2": 0.02, "LT": 0.04}},
}

AXIS_LT = [f"margin.regimes.{r}.target.LT" for r in REGIMES]
AXIS_CAPEX = [f"capex.maintenance_pct.{lv}" for lv in ("low", "base", "high")]
AXIS_TRAFFIC = [f"revenue.traffic.{d}" for d in ("bear", "base", "bull")]


def book() -> dict:
    return {
        "meta": {
            "version": "toy-1",
            "company": {"name": "Синтетика", "ticker": "TOY"},
            "period_unit": "half",
            "valuation_date": "2026-09-18",
            "facts_date": "2026-06-30",
            "bridge_as_of": "2026-06-30",
            "first_period": "2026H2",
            "last_period": "2036H2",
        },
        "market": {
            "price": 1000.0,
            "price_convention": "legalclose",
            "price_date": "2026-09-18",
            "peers": [
                {"key": "peer_a", "name": "Аналог А", "ev_ebitda": 4.0, "basis": "IAS 17",
                 "as_of": "2026-09-18"},
                {"key": "peer_b", "name": "Аналог Б", "ev_ebitda": 5.0, "basis": "IAS 17",
                 "as_of": "2026-09-18"},
                {"key": "toy", "name": "Синтетика", "ev_ebitda": 4.5, "basis": "IAS 17, проформа",
                 "as_of": "2026-09-18", "subject": True},
            ],
            "sellside_targets": [
                {"house": "Дом 1", "target": 1100.0, "date": "2026-08-01", "rating": "держать",
                 "source": "синтетика"},
                {"house": "Дом 2", "target": 1400.0, "date": "2026-08-15", "source": "синтетика"},
                {"house": "Дом 3", "target": 1800.0, "date": "2026-09-01", "rating": "покупать",
                 "source": "синтетика"},
            ],
            "sellside_summary": {"median": 1400.0, "mean": 1433.3, "min": 1100.0, "max": 1800.0,
                                 "n": 3, "as_of": "2026-09-18", "note": "синтетика"},
        },
        "facts": {
            "shares_out_mln": 100.0,
            "revenue": dict(GROUP_REVENUE),
            "ebitda_pre16": dict(GROUP_EBITDA),
            "reported": {"revenue": {"2025H1": 420.0, "2025H2": 480.0, "2026H1": 445.0},
                         "ebitda_pre16": {"2025H1": 28.0, "2025H2": 36.6, "2026H1": 27.5}},
            "undrawn_credit_lines": 300.0,
            # Гайденс по марже года (гейт guidance_gap): 1П — отчёт, 2П — клетка.
            "guidance": {"period": "2026", "ebitda_margin_min": 0.065, "source": "синтетика"},
            "segments": {sid: dict(f) for sid, f in SEGMENT_FACTS.items()},
            "anchor": {
                "period": "2026H1",
                "da_pre16": 12.0,
                "capex": 14.0,
                "net_debt": 120.0,
                "cash": 30.0,
                "ebitda_ltm": GROUP_EBITDA["2025H2"] + GROUP_EBITDA["2026H1"],
                "ebitda_ltm_reported": 36.6 + 27.5,
                "revenue_ltm": GROUP_REVENUE["2025H2"] + GROUP_REVENUE["2026H1"],
                "margin_pro_forma": 0.06,
                "margin_pro_forma_se": 0.004,
            },
        },
        "worlds": {
            "N": world("Нормализация (синтетика)", lt(0.09), 0.04,
                       {1: 0.10, 3: 0.098, 5: 0.097, 10: 0.096, "LT": 0.095}, 0.04),
            "H": world("Высокие ставки (синтетика)", {"2026H2": 0.15, "2027": 0.13, "LT": 0.11}, 0.06,
                       {1: 0.14, 3: 0.135, 5: 0.13, 10: 0.125, "LT": 0.12}, 0.055),
            "M": world("Рынок (синтетика)", lt(0.12), 0.05,
                       {1: 0.125, 3: 0.12, 5: 0.115, 10: 0.11, "LT": 0.105}, 0.05),
        },
        "revenue": {
            "maturity_curve": [0.7, 0.85, 1.0],
            "ticket_k": {"bear": 0.5, "base": 0.6, "bull": 0.7},
            "ticket_shift": {"bear": lt(0.0), "base": {"2026H2": 0.01, "LT": 0.0},
                             "bull": {"2026H2": 0.02, "2027": 0.01, "LT": 0.0}},
            "vat_adjustment": {"2026H2": 0.0, "LT": 0.0},
            "traffic": {"bear": lt(-0.01), "base": lt(0.0), "bull": lt(0.005)},
            "ticket_lt_homogeneity": {"reference_world": "N", "ramp_from": 2027, "ramp_to": 2031},
            # Доли кварталов внутри полугодия (пары дают 1).
            "quarter_share": {"Q1": 0.48, "Q2": 0.52, "Q3": 0.45, "Q4": 0.55},
            "segments": SEGMENTS,
        },
        "margin": {
            "deviation_persistence": 0.6,
            "nowcast_margin_shocks_pp": None,
            # Знак минус: второе полугодие сильнее первого.
            "seasonal_h1_pp": -0.005,
            "season_from_period": "2027H1",
            "season_free_halves": 2,
            "cash_lease_adj_pct": -0.001,
            # Поправки внутри полугодия обнуляются с весами выручки кварталов.
            "quarter_offset_pp": {"Q1": -0.0026, "Q2": 0.0024, "Q3": -0.0022, "Q4": 0.0018},
            "regimes": {
                "stress": {"target": {"2026H2": 0.055, "2027": 0.05, "LT": 0.05}},
                "floor": {"target": {"2026H2": 0.058, "LT": 0.058}},
                "partial": {"target": {"2026H2": 0.059, "2027": 0.063, "2028": 0.066, "LT": 0.066}},
                "full": {"target": {"2026H2": 0.06, "2027": 0.067, "2028": 0.072, "LT": 0.075}},
            },
        },
        "capex": {
            "maintenance_pct": {"low": lt(0.015), "base": lt(0.02), "high": lt(0.028)},
            "maintenance_area_share": 0.5,
            "disposal_proceeds_pct": 0.0005,
            "infra_capex_per_net_m2": 0.02,
            "infra_from_year": 2029,
            "asset_life_years": 10,
            "integration_capex": {"2026H2": 2.0, "2027H1": 1.0, "2027H2": 0.5, "LT": 0.0},
            "segments": {"hyper": {"growth_capex_per_m2": 0.06}, "conv": {"growth_capex_per_m2": 0.04},
                         "acq": {"growth_capex_per_m2": 0.05}, "diy": {"growth_capex_per_m2": 0.05}},
        },
        "nwc": {
            "nwc_pct_start": 0.005,
            "seasonal_june_excess": 5.0,
            "nwc_pct": {"hold": 0.005, "partial": {"2026H2": 0.005, "2027": 0.0, "LT": 0.0},
                        "half": lt(0.0025), "full": {"2026H2": 0.005, "2027": -0.005, "LT": -0.005}},
            # ОК приобретённого периметра — доля годовой выручки поверх nwc_pct:
            # сходится к условиям покупателя.
            "acquired_path": {"2026H2": 0.004, "2027": 0.002, "2028": 0.0, "LT": 0.0},
            # Доли изменения ОК по кварталам внутри полугодия (перекат ЧД).
            "quarter_share": {"Q1": 1.2, "Q2": -0.2, "Q3": -0.15, "Q4": 1.15},
        },
        "tax": {
            "rate": 0.25,
            "alpha": 0.6,
            "alpha_terminal_shield": 1.0,
            "permanent_addback_pct": lt(0.002),
            "nol_start": 5.0,
            "nol_limit": 0.5,
            "nol_full_from_year": 2031,
            # Запертые убытки приобретённых юрлиц: до присоединения прибавляются к
            # базе и копятся, в usable_from переходят в пул группы за вычетом haircut.
            "acquired_nol": {"amount": 20.0, "usable_from": "2027H2",
                             "discount_rule": {"method": "frozen_until_usable_from",
                                               "haircut": 0.25,
                                               "locked_addback": {"2026H2": 3.0, "2027H1": 2.0,
                                                                  "2027H2": 1.0}}},
            # Ускоренная налоговая амортизация: доля capex, вычитаемая сразу.
            "capex_tax_premium_share": 0.2,
        },
        "financing": {
            "operating_cash_pct": 0.006,
            "liquidity_buffer_pct": 0.01,
            "prefunded_cash_decay": 0.5,
            "cash_yield_k": 0.8,
            # Доля фиксированной ставки в новом долге (вне живых корзин).
            "fixed_share": {"2026H2": 0.7, "2027": 0.6, "2028": 0.5, "LT": 0.5},
            # Корзины действующего долга — доли всего долга, своя ставка по until.
            "rate_baskets": [
                {"name": "Корзина 1", "share": 0.35, "rate": {"fixed": 0.11}, "until": "2027H1"},
                {"name": "Корзина 2", "share": 0.30, "rate": {"fixed": 0.12}, "until": "2028H2"},
                {"name": "Корзина 3", "share": 0.15, "rate": {"key_plus": 0.02},
                 "until": "2029H1"},
            ],
            "spread_float": {"base": 0.015, "stress": 0.025},
            "spread_fixed": {"base": 0.017, "stress": 0.03},
            "spread_fair": {"float": {"base": 0.015, "stress": 0.025},
                            "fixed": {"base": 0.017, "stress": 0.03}},
            "leverage_target": 1.0,
            "dividends_from_year": 2028,
            "dividend_ladder": [
                {"max_leverage": 1.0, "payout_max": None},
                {"max_leverage": 1.5, "payout_max": 1.0},
                {"max_leverage": None, "payout_max": 0.5},
            ],
        },
        "valuation": {
            "beta_u": 0.55,
            "erp": 0.0557,
            "terminal_real_growth": 0.005,
            "terminal": {"half_rate_convention": "compound"},
            # Порог рычага — так, чтобы издержки неустойчивости срабатывали в
            # части стресс-клеток (правило должно быть видно на синтетике).
            "distress": {"cost_pct_ev": 0.1, "net_leverage_trigger": 2.3,
                         "credit_limit_trigger": True},
            "market_curve_years": 0.0,
            "ronic_spread": 0.0,
            "governance_discount": 0.12,
            "governance_components": [
                {"name": "Утечка к контролирующему акционеру", "value": 0.05, "sign": 1,
                 "basis": "синтетика"},
                {"name": "Сделки со связанными сторонами", "value": 0.05, "sign": 1,
                 "basis": "синтетика", "in_850oa_scope": False},
                {"name": "Права миноритария", "value": 0.04, "sign": 1, "basis": "синтетика"},
                {"name": "Добровольная оферта (встречная асимметрия)", "value": 0.02, "sign": -1,
                 "basis": "синтетика"},
            ],
            "headline": {
                "method": "intrinsic",
                "print_step": 50,
                "diagnostics": "median",
                "limited_liability": {"v0_to_d_min": 1.2, "max_share": 0.05},
                "jump_guard": {"median_pct": 0.09, "v0_pct": 0.10},
            },
            "uncertainty": {
                "draws": 40,
                "seed": 20260918,
                "quantiles": [0.1, 0.25, 0.5, 0.75, 0.9],
                "median_draws": 20,
                "reverse_bounds": "follow_center",
                "median_refine": 1,
                "axes": [
                    {"name": "Долгосрочный уровень маржи", "paths": AXIS_LT,
                     "shift_low": -0.006, "shift_high": 0.006},
                    {"name": "Поддерживающий capex", "paths": AXIS_CAPEX,
                     "shift_low": -0.003, "shift_high": 0.005},
                    {"name": "β_u", "path": "valuation.beta_u", "low": 0.45, "high": 0.7},
                    {"name": "ERP", "path": "valuation.erp", "low": 0.049, "high": 0.062},
                    {"name": "Дисконт за управление", "path": "valuation.governance_discount",
                     "low": 0.05, "high": 0.2},
                    {"name": "Продуктивность закрываемой площади «у дома»",
                     "path": "revenue.segments.conv.closed_productivity", "low": 0.4, "high": 0.7},
                    {"name": "Целевой рычаг", "path": "financing.leverage_target",
                     "low": 1.0, "high": 1.5},
                    {"name": "Трафик LFL", "paths": AXIS_TRAFFIC,
                     "shift_low": -0.003, "shift_high": 0.003},
                ],
            },
        },
        "bridge": {
            "items": [
                {"id": "put", "name": "Пут на долю дочерней сети", "kind": "claim", "amount": 5.0,
                 "as_of": "2026-06-30", "accrete_rate_half": 0.045, "settle_period": "2027H1",
                 "basis": "синтетика: выплата — сумма, наращенная до конца полугодия расчёта"},
                {"id": "deferred", "name": "Остаток оплаты покупки", "kind": "claim", "amount": 4.0,
                 "as_of": "2026-06-30", "settle_period": "2026H2", "settle_amount": 4.0,
                 "basis": "синтетика"},
                {"id": "ltip", "name": "Долгосрочная мотивация", "kind": "claim", "amount": 2.0,
                 "as_of": "2026-06-30", "basis": "синтетика"},
                {"id": "nci", "name": "Неконтролирующие доли", "kind": "claim", "amount": 1.5,
                 "as_of": "2026-06-30", "basis": "синтетика"},
                {"id": "loans", "name": "Займы выданные", "kind": "asset", "amount": 3.0,
                 "as_of": "2026-06-30", "haircut": 0.5, "basis": "синтетика: половина не засчитана"},
            ],
        },
        "joint": {
            "world_prob": {"N": 0.3, "H": 0.5, "M": 0.2},
            "world_prob_market_implied": {"N": 0.2, "H": 0.3, "M": 0.5},
            "macro_neutral_world": "M",
            "own_macro_confidence": 0.5,
            "regime_given_world": {w: {"stress": 0.2, "floor": 0.4, "partial": 0.3, "full": 0.1}
                                   for w in ("N", "H", "M")},
            "capex_prob_given_regime": {r: {"low": 0.2, "base": 0.6, "high": 0.2} for r in REGIMES},
            "growth_by_regime_override": {"stress": "low"},
            "by_world": {"N": {"growth": "high", "nwc": "hold", "credit": "base"},
                         "H": {"growth": "mid", "nwc": "hold", "credit": "base"},
                         "M": {"growth": "low", "nwc": "hold", "credit": "stress"}},
            "demand_by_regime": {"stress": "bear", "floor": "base", "partial": "base",
                                 "full": "bull"},
            "regime_update": {"sigma_pp": 0.006, "sigma_range": [0.004, 0.008],
                              "max_shift_pp": 0.1, "observations": None,
                              "demo_period": "2026H2",
                              "demo_values": [0.05, 0.055, 0.06, 0.065, 0.07]},
        },
        "scenarios": {
            "bear": {"world": "H", "margin_regime": "floor", "capex": "base", "growth": "mid",
                     "demand": "base", "nwc": "hold", "credit": "base"},
            "base": {"world": "H", "margin_regime": "partial", "capex": "base", "growth": "mid",
                     "demand": "base", "nwc": "hold", "credit": "base"},
            "bull": {"world": "N", "margin_regime": "full", "capex": "base", "growth": "high",
                     "demand": "bull", "nwc": "hold", "credit": "base"},
        },
        "weights": {"bear": 0.4, "base": 0.4, "bull": 0.2},
        "scenario_names": {"bear": "Дно (синтетика)", "base": "Частичная сходимость (синтетика)",
                           "bull": "Полная сходимость (синтетика)"},
        "sensitivities": [
            {"name": "Вероятности миров", "path": "joint.world_prob",
             "low": {"N": 0.2, "H": 0.55, "M": 0.25}, "high": {"N": 0.4, "H": 0.45, "M": 0.15}},
            {"name": "Поддерживающий capex, сдвиг", "path": "capex.maintenance_pct.base",
             "shift_low": -0.004, "shift_high": 0.006},
            {"name": "β_u", "path": "valuation.beta_u", "low": 0.45, "high": 0.7},
            {"name": "Дисконт за управление", "path": "valuation.governance_discount",
             "low": 0.05, "high": 0.2},
            {"name": "Capex открытия «у дома»", "path": "capex.segments.conv.growth_capex_per_m2",
             "low": 0.03, "high": 0.05},
            {"name": "Пут на долю дочерней сети", "path": "bridge.items[put].amount",
             "low": 4.0, "high": 6.0},
            {"name": "Долгосрочный уровень маржи", "paths": AXIS_LT,
             "shift_low": -0.006, "shift_high": 0.006},
        ],
        "reverse_dcf": [
            {"name": "Долгосрочный уровень маржи (сдвиг)", "paths": AXIS_LT, "kind": "shift",
             "search": [-0.03, 0.03], "range": [-0.006, 0.006]},
            {"name": "Бета активов β_u", "paths": ["valuation.beta_u"], "kind": "value",
             "search": [0.05, 2.0], "range": [0.45, 0.7]},
            {"name": "Дисконт за управление", "paths": ["valuation.governance_discount"],
             "kind": "value", "search": [0.0, 0.9], "range": [0.05, 0.2]},
        ],
        "reference_class_margin": {
            "n": 6, "share": {"stress": 0.3, "floor": 0.3, "partial": 0.3, "full": 0.1},
            "e_m_lt": 0.06, "mean_target_by_regime": {"stress": 0.05, "floor": 0.058,
                                                      "partial": 0.066},
            "note": "синтетика",
        },
    }


HEADER = """\
# Синтетическая книга в схеме Ленты для тестов ядра (DESIGN D17).
# Сгенерировано tests/fixtures/toy_book/build.py — правьте сборщик, не этот файл.
# Числа выдуманы: книга проверяет механику ядра, а не оценку компании.
"""


def main() -> None:
    text = HEADER + yaml.safe_dump(book(), allow_unicode=True, sort_keys=False, width=100)
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(OUT)


if __name__ == "__main__":
    main()
