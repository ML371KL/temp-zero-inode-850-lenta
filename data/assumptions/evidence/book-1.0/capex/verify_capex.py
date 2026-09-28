# -*- coding: utf-8 -*-
"""Проверки листа capex книги 1.0 «Ленты»: читает capex_out.json и book-draft/fragments/capex.yaml,
падает (код 1) на первом нарушении инварианта. Запуск после capex_book10.py:
    python -B verify_capex.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
H = HERE.parents[3]
OUT = json.loads((HERE / "capex_out.json").read_text(encoding="utf-8"))
FR = yaml.safe_load((H / "book-draft" / "fragments" / "capex.yaml").read_text(encoding="utf-8"))
ERR: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(f"  [{'ДА' if cond else 'НЕТ'}] {msg}")
    if not cond:
        ERR.append(msg)


print("Проверки листа capex")
# 0. цитаты
check(not OUT["fail"] and all(c["ok"] for c in OUT["citations"]), f"все {len(OUT['citations'])} цитат найдены на своих страницах")
# 1. история — сверка с research/02 (таблицы 2 и 3: capex до МСФО 16 46,3 / 22,5; D&A 26,3 / 15,8)
hy, hh = OUT["history_year"], OUT["history_half"]
check(abs(hy["2025"]["capex"] - 46.26) < 0.01 and abs(hy["2025"]["da"] - 26.28) < 0.01, "2025: capex 46,26, D&A 26,28 (датабук IAS 17)")
check(abs(hh["2026H1"]["capex"] - 22.48) < 0.01 and abs(hh["2026H1"]["da"] - 15.79) < 0.01, "1П2026: capex 22,48, D&A 15,79")
check(abs(sum(OUT["structure_2025"].values()) - 46.1) < 1e-6, "структура 2025 складывается в 46,1 млрд")
check(abs(hy["2025"]["capex"] - 46.1) < 0.3, "управленческий capex 46,1 ≈ денежному IAS 17 (разница < 0,3 млрд)")
# 2. сезонность
s = OUT["seasonality"]
check(s["excluded"] == [2021, 2022], "из выборки сезонности исключены ровно 2021 и 2022 (2П < 1П)")
check(0.80 <= s["m1"] <= 0.92 and 1.05 <= s["m2"] <= 1.18, f"m1 {s['m1']}, m2 {s['m2']} в коридоре истории")
# 3. ключи
mp = FR["capex"]["maintenance_pct"]
half_keys = {"2026H2", "2027H1", "2027H2"}
for lv in ("low", "base", "high"):
    k = mp[lv]
    check(set(k) == half_keys | {str(y) for y in range(2027, 2037)} | {"LT"}, f"{lv}: набор ключей 2026H2, 2027H1/H2, 2027–2036, LT")
    check(k["2036"] == k["LT"], f"{lv}: 2036 = LT (стационар)")
    check(not any(x.endswith("H1") or x.endswith("H2") for x in k if x[:4].isdigit() and int(x[:4]) >= 2028),
          f"{lv}: ключей полугодий после 2027 нет (последний период без ключа полугодия)")
    s1 = s["s1_2027_proxy"]
    check(abs(k["2027H1"] * s1 + k["2027H2"] * (1 - s1) - k["2027"]) < 2e-4, f"{lv}: пара 2027 нормирована к ключу года")
for key in mp["base"]:
    check(mp["low"][key] < mp["base"][key] < mp["high"][key], f"low < base < high для ключа {key}")
# 4. стационар и доля
st = OUT["stationary"]
check(abs(st["base"]["pct"] - mp["base"]["LT"]) < 5e-5, "LT base = стационар расчёта снизу вверх")
check(abs(FR["capex"]["maintenance_area_share"] - round(st["base"]["phys_share"], 2)) < 1e-9, "maintenance_area_share = физическая доля base")
# 5. открытия
g = FR["capex"]["segments"]
check(set(g) == {"hyper", "okey", "super", "conv", "droge", "remi", "diy"}, "growth_capex_per_m2 для всех сегментов с площадью (без wholesale)")
check(0.06 <= g["conv"]["growth_capex_per_m2"] <= 0.085, "у дома 60–85 тыс. ₽/м²")
c = OUT["openings"]["check_1h26"]
check(0.40 <= c["openings_bn"] / c["capex_bn"] <= 0.56, "1П2026: доля открытий по удельным ценам 2025 — 40–56 % (в 2025 — 48,7 %)")
# 6. сверки
r = OUT["crosscheck_replacement"]
m = r["model_ak1_plus_repl_open"]["pct"]
lo, hi = r["at_2026h1"]["pct"]
check(lo - 0.004 <= m <= hi, f"A-K1 + замещающие открытия {100 * m:.2f} % — не ниже коридора замещения −0,4 п.п. ({100 * lo:.2f}–{100 * hi:.2f} %)")
check(OUT["strategy_check_2027"]["pct"] < 0.055, "весь capex 2027 base ниже цели стратегии 5,5 %")
# 7. прочее
cp = FR["capex"]
check(cp["da_method"] == "straight_line" and 10 <= cp["asset_life_years"] <= 14, "da_method straight_line, срок 10–14 лет")
check(0.0003 <= cp["disposal_proceeds_pct"] <= 0.0008, "disposal_proceeds_pct в диапазоне листа")
check(set(cp["integration_capex"]) <= {"2026H2", "2027H1", "2027H2", "2028H1", "2028H2"}, "integration_capex — путь ближних полугодий")
pc = FR["joint"]["capex_prob_given_regime"]
check(all(abs(sum(v.values()) - 1) < 1e-12 for v in pc.values()) and set(pc) == {"stress", "floor", "partial", "full"},
      "P(capex | режим): четыре режима, суммы = 1")
check(pc["stress"]["low"] >= pc["floor"]["low"] >= pc["full"]["low"] and pc["full"]["high"] >= pc["stress"]["high"],
      "P(capex | режим) монотонна: стресс — к low, полный — к high")
print("ИТОГО: " + ("все проверки пройдены" if not ERR else f"провалов {len(ERR)}"))
sys.exit(1 if ERR else 0)
