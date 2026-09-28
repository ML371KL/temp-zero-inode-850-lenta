# -*- coding: utf-8 -*-
"""Прогон листа «Маржа» целиком и проверки, которые обязаны выполняться (VERIFY.md).

Порядок: series → anchor_bridge → ar1 → refclass → regimes → lease_labor → update_demo,
затем сверка фрагмента книги (book-draft/fragments/margin.yaml) с выводами скриптов.
Код возврата 0 — все проверки «ДА».
"""
from __future__ import annotations

import json
import runpy
import sys

import yaml

from _common import HERE

STEPS = ["series", "anchor_bridge", "ltm_pro_forma", "ar1", "refclass", "regimes", "lease_labor", "update_demo"]
FRAG = HERE.parents[2] / "fragments" / "margin.yaml"


def load(name):
    return json.loads((HERE / f"{name}_out.json").read_text(encoding="utf-8"))


def main():
    import io
    import contextlib
    for s in STEPS:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            runpy.run_path(str(HERE / f"{s}.py"), run_name="__main__")
        print(f"прогон {s}.py — готово")
    S, A, AR, RC, RG, LL, UD = (load(x) for x in ("series", "anchor", "ar1", "refclass", "regimes", "lease_labor", "update_demo"))
    F = yaml.safe_load(FRAG.read_text(encoding="utf-8"))
    M, J = F["margin"], F["joint"]
    ok = {}
    ok["кварталы датабука складываются в полугодия"] = S["quarter_checks_ok"]
    ok["факты моста сверены с первичкой (6 тождеств)"] = all(A["fact_checks"].values())
    bs = S["quarter_offsets"]["book_shares"]
    ok["поправки кварталов обнуляются внутри полугодия на долях книги (|Σ| < 1e-5)"] = (
        abs(bs["Q1_in_H1"] * M["quarter_offset_pp"]["Q1"] + (1 - bs["Q1_in_H1"]) * M["quarter_offset_pp"]["Q2"]) < 1e-5
        and abs(bs["Q3_in_H2"] * M["quarter_offset_pp"]["Q3"] + (1 - bs["Q3_in_H2"]) * M["quarter_offset_pp"]["Q4"]) < 1e-5)
    ok["квартальные поправки фрагмента = series_out на долях книги (±5e-5)"] = all(
        abs(M["quarter_offset_pp"][q] - S["quarter_offsets"]["offset_pp_book"][q] / 100) < 5e-5 for q in ("Q1", "Q2", "Q3", "Q4"))
    ok["seasonal_h1_pp фрагмента = regimes_out"] = abs(M["seasonal_h1_pp"] - RG["seasonal_h1_pp"]) < 1e-9
    ok["маржа якоря фрагмента = anchor_out (±5e-5)"] = abs(F["facts"]["anchor"]["margin_pro_forma"] - A["margin_pro_forma"]) < 5e-5
    LT = json.loads((HERE / "ltm_out.json").read_text(encoding="utf-8"))
    ok["EBITDA проформы по полугодиям и LTM фрагмента = ltm_out (±0,01)"] = (
        all(abs(F["facts"]["ebitda_pre16"]["pro_forma"][k] - LT["halves_pro_forma"][k]["ebitda"]) < 0.01 for k in ("2025H1", "2025H2", "2026H1"))
        and abs(F["facts"]["anchor"]["ebitda_ltm"] - LT["ebitda_ltm_pro_forma"]) < 0.01
        and abs(F["facts"]["anchor"]["revenue_ltm"] - LT["revenue_ltm_pro_forma"]) < 0.01)
    ok["se якоря ≥ sd Монте-Карло"] = F["facts"]["anchor"]["margin_pro_forma_se"] >= A["mc"]["margin_pf"]["sd"]
    ok["наблюдение 2026H1 = маржа якоря и её se"] = (
        J["regime_update"]["observations"]["2026H1"]["value"] == F["facts"]["anchor"]["margin_pro_forma"]
        and J["regime_update"]["observations"]["2026H1"]["se"] == F["facts"]["anchor"]["margin_pro_forma_se"])
    tg = {r: M["regimes"][r]["target"] for r in M["regimes"]}
    ok["цели фрагмента = regimes_out (±5e-5)"] = all(
        abs(tg[r][k] - RG["targets"][r][k]) < 5e-5 for r in tg for k in RG["targets"][r])
    ok["цели 2026H1 равны у всех режимов (наблюдение якоря нейтрально)"] = len({tg[r]["2026H1"] for r in tg}) == 1
    ok["якорь: цель 2026H1 + сезонность = наблюдение (±5e-5)"] = abs(tg["floor"]["2026H1"] + M["seasonal_h1_pp"] - F["facts"]["anchor"]["margin_pro_forma"]) < 5e-5
    ok["порядок режимов с 2027 г.: стресс < дно < частичный < полный"] = all(
        tg["stress"][k] < tg["floor"][k] < tg["partial"][k] < tg["full"][k] for k in ("2027", "2028", "2029", "2030", "LT"))
    ok["«полный» LT в коридоре DESIGN D9 (7,5–8 %)"] = 0.075 <= tg["full"]["LT"] <= 0.080
    probs = J["regime_given_world"]
    ok["вероятности режимов одинаковы по мирам и = refclass (округл. 0,5 п.п.)"] = (
        probs["N"] == probs["H"] == probs["M"] and all(abs(probs["N"][r] - RC["book"][r]) < 1e-9 for r in probs["N"]))
    ok["сумма вероятностей = 1"] = abs(sum(probs["N"].values()) - 1) < 1e-9
    ok["σ и ρ фрагмента = ar1_out"] = (abs(J["regime_update"]["sigma_pp"] - AR["choice"]["sigma_pp"]) < 1e-9
                                       and abs(M["deviation_persistence"] - AR["choice"]["rho"]) < 1e-9)
    ok["класс эпизодов воспроизводит 850oa (5/3/2/0 из 10)"] = RC["class_E"]["counts"] == {"stress": 5, "floor": 3, "partial": 2, "full": 0}
    ok["гайденс-проверка записана (нужная маржа 2П 7,6–7,9 %)"] = 0.076 <= RG["guidance"]["need_2h"] <= 0.079
    ok["demo_values 5,0–8,0 % и покрывают ожидание модели"] = (
        min(J["regime_update"]["demo_values"]) == 0.05 and max(J["regime_update"]["demo_values"]) == 0.08
        and min(J["regime_update"]["demo_values"]) < UD["expected_2026H2"] < max(J["regime_update"]["demo_values"]))
    ok["мартингал E[m_LT] в пространстве маржи (|дрейф| < 0,01 п.п.)"] = abs(UD["martingale_drift_pp"]["cap"]) < 0.01
    ok["A-C6 фрагмента в диапазоне медиан листа"] = (
        min(LL["cash_lease"]["median"], LL["cash_lease"]["median_2024_2026"]) - 1e-4 <= M["cash_lease_adj_pct"]
        <= max(LL["cash_lease"]["median"], LL["cash_lease"]["median_2024_2026"]) + 1e-4)
    ok["доля труда фрагмента = lease_labor (±5e-4)"] = abs(M["labor_share_total"] - LL["labor"]["total_1h26"]) < 5e-4
    ok["κ фрагмента = lease_labor (±0,03)"] = all(abs(M["cost_passthrough_kappa"][r] - LL["kappa"]["values"][r]) <= 0.03 for r in M["cost_passthrough_kappa"])
    ok["leased_share = 1 − доля собственной площади 1П2026 (±5e-4)"] = abs(M["line_drivers"]["leased_share"] - (1 - LL["own_share"]["1H 2026"])) < 5e-4
    print()
    for k, v in ok.items():
        print(f"{'ДА ' if v else 'НЕТ'} — {k}")
    bad = [k for k, v in ok.items() if not v]
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
