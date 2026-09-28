"""Проверки листа «Оценка»: фрагмент fragments/valuation.yaml ↔ выводы out/*.json ↔ книга 850oa (правила-копии).
Запуск после beta.py, governance.py, market_headline.py. Код 0 — все проверки «ДА», иначе 1.
"""
from __future__ import annotations

import json
import re
import sys

import yaml

from common import BOOK_DRAFT, HERE, OUT, magnit_dir

ok = True


def check(cond, msg):
    global ok
    print(("ДА  " if cond else "НЕТ ") + msg)
    ok &= bool(cond)


frag = yaml.safe_load((BOOK_DRAFT / "fragments" / "valuation.yaml").read_text(encoding="utf-8"))
b = json.loads((OUT / "beta_out.json").read_text(encoding="utf-8"))
g = json.loads((OUT / "governance_out.json").read_text(encoding="utf-8"))
m = json.loads((OUT / "market_out.json").read_text(encoding="utf-8"))
mk, va = frag["market"], frag["valuation"]

# 1. цена книги
check(mk["price"] == m["price"]["price"] == 1619.5, "market.price = LEGALCLOSEPRICE 18.09.2026 = 1 619,5 (три выгрузки ISS)")
check(mk["price_convention"] == "legalclose" and mk["price_date"] == "2026-09-18", "конвенция legalclose, дата 2026-09-18")
# 2. цели инвестдомов
lt = [(x["house"], x["target"], x["date"]) for x in m["sellside"]["latest"]]
fr = [(x["house"], x["target"], x["date"]) for x in mk["sellside_targets"]]
check(lt == fr, f"sellside_targets = последняя цель каждого дома ({len(fr)} домов)")
a = m["sellside"]["agg"]["all_latest"]
s = mk["sellside_summary"]
check(s["n"] == a["n"] and s["median"] == a["median"] and abs(s["mean"] - a["mean"]) < 0.05 and s["min"] == a["min"] and s["max"] == a["max"], "агрегаты целей")
aq = m["sellside"]["agg"]["after_q2_2026-08-03"]
check(s["after_last_report"]["n"] == aq["n"] and s["after_last_report"]["median"] == aq["median"], "агрегаты после отчёта 2 кв.")
check(all(x["date"] <= "2026-09-28" for x in mk["sellside_targets"]), "у каждой цели дата не позже среза")
# 3. аналоги
pm = {p["name"]: p["ev_ebitda"] for p in m["peers"]}
check(all(abs(p["ev_ebitda"] - pm[p["name"]]) < 5e-4 for p in mk["peers"]), "peers: EV/EBITDA = выводу market_headline.py")
check(all(p["basis"] == "IAS 17" for p in mk["peers"]), "peers: одна база IAS 17")
# 4. β_u и ERP
check(va["beta_u"] == b["choice"]["beta_u"] and b["choice"]["range"] == [0.45, 0.70], "beta_u 0,55 [0,45–0,70] = выбор beta.py")
cl = b["choice"]["anchor_cluster"]
check(min(cl) > 0.50 and max(cl) < 0.56, f"кластер якоря на проформе {min(cl):.3f}–{max(cl):.3f} внутри (0,50; 0,56)")
check(0.45 <= min(b["choice"]["own_monthly_u_1_2_3"][1:]) and max(b["choice"]["own_monthly_u_1_2_3"][1:]) <= 0.71, "месячные собственные β_u 2/3 г. внутри диапазона оси")
check(va["erp"] == 0.0557 and b["erp"]["high"] == 0.067 and b["erp"]["low"] == 0.049, "ERP 5,57 % [4,9–6,7 %]")
lam = 0.55 * 4.23 + 3.895 - 2.557
check(abs(lam / 0.55 - b["erp"]["high_unrounded_pct"]) < 1e-3, "верх ERP = λ-конвенция при β_u центра (6,663 %)")
check(abs(b["anchor_candidates"]["ВП/EBITDA, LTM, отчётный периметр"]["k"] - 3.3274 / 4.0851) < 2e-4, "k отчётного периметра = листу 850oa (3,327 / 4,085)")
# 5. дисконт за управление
comp = va["governance_components"]
ssum = sum(c["sign"] * c["value"] for c in comp)
check(abs(ssum - va["governance_discount"]) < 1e-9, f"Σ sign·value компонентов = governance_discount ({ssum:.4f})")
check(abs(va["governance_discount"] - g["result"]["governance_discount"]) < 1e-9, "governance_discount = выводу governance.py")
ch = {c["id"]: c for c in g["channels"]}
ids = ["а", "б", "в", "г", "д", "е"]
check(all(abs(comp[i]["value"] - ch[k]["value"]) < 1e-9 and comp[i]["sign"] == ch[k]["sign"] for i, k in enumerate(ids)), "компоненты = каналам governance.py (значение и знак)")
check(sum(1 for c in comp if c["sign"] < 0) >= 1, "есть встречная асимметрия со знаком минус (D5 д)")
check(all(x > 0 for x in g["pdf_checks"]["ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf"]["567 578"]), "беспроцентный заём 2022 (0,568) найден в МСФО 2023")
check(abs(g["leakage"]["equity_charges_total"] - 1.418702) < 1e-6, "утечка через капитал 2022–2024 = 1,4187 млрд")
check(g["leakage"]["implied_rate_2H2025_min"] > 0.16, "ставка займов 2П2025 ≥ 16 % (не «≈10 %»)")
# 6. заголовок
h = va["headline"]
check(h["method"] == "intrinsic" and h["diagnostics"] == "median" and h["print_step"] == 50, "headline: intrinsic, median, шаг 50")
check(h["limited_liability"] == m["headline"]["limited_liability"] and h["jump_guard"] == m["headline"]["jump_guard"], "пороги limited_liability и jump_guard = выводу")
row13 = next(r for r in m["headline"]["option_table"] if r["x"] == 1.3)
check(row13["s0.3"]["opt_rub"] < 50, f"при V0 = 1,3·D и σ√T 0,30 опционная часть {row13['s0.3']['opt_rub']:.0f} ₽ < шага печати")
check(not any(k in h for k in ("strike_premium", "credit_share_c", "sigma_ev", "strike_decay", "recalibration_flag")) and "option" not in va,
      "нет σ, K′, кредитного пута, затухания страйка, option (D4)")
# 7. копии 850oa
cp = m["copied_from_850oa"]
for k in ("terminal_real_growth", "roll_along_forwards", "distress", "market_curve_years", "ronic_spread", "terminal"):
    check(va[k] == cp[k], f"{k} = 850oa")
check({k: va["uncertainty"][k] for k in cp["uncertainty"]} == cp["uncertainty"], "правило полосы (draws, seed, квантили, median_draws, reverse_bounds, median_refine) = 850oa")
# 8. диапазоны согласованы: оси = чувствительности = обратный DCF
axes = {x["path"]: (x["low"], x["high"]) for x in va["uncertainty"]["axes"]}
sens = {x["path"]: (x["low"], x["high"]) for x in frag["sensitivities"]}
rdcf = {x["paths"][0]: tuple(x["range"]) for x in frag["reverse_dcf"]}
check(all(axes[p] == sens[p] == rdcf[p] for p in axes), "диапазоны осей = строк чувствительности = обратного DCF")
check(all(lo <= {"valuation.beta_u": va["beta_u"], "valuation.erp": va["erp"], "valuation.governance_discount": va["governance_discount"]}[p] <= hi
          for p, (lo, hi) in axes.items()), "центр каждой оси внутри диапазона")
# 9. гигиена кода: нет пользовательских путей
bad = [p.name for p in HERE.glob("*.py") if re.search(r"[A-Za-z]:[\\/]+Users", p.read_text(encoding="utf-8"))]
check(not bad, "в скриптах нет абсолютных путей пользователя" + (f" ({bad})" if bad else ""))
print("ИТОГ:", "все проверки ДА" if ok else "ЕСТЬ НЕТ")
sys.exit(0 if ok else 1)
