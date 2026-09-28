# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 2: маржа якоря на проформе 1П2026 (facts.anchor.margin_pro_forma, _se).

Мост «прибыль до налога МСФО 16 → EBITDA IAS 17» для «О'КЕЙ» за январь–май 2026
(проформа прим. 5 минус фактический вклад с 02.06), та же процедура для июня «О'КЕЙ»,
«Дом Ленты» (1П2026) и «Реми» (декабрь 2025 и 1П2026). Итог: EBITDA и маржа группы
на проформе, разложение «унаследованный периметр + приобретённый периметр».

Факты (класс A) читаются скриптом из первички и сверяются с числами листа:
  ifrs_1H2026.pdf прим. 5 (с. 16–19), прим. 7 (с. 20–22), прим. 4 (с. 16);
  ifrs_FY2025.pdf прим. 8 (с. 47–49, «Реми»);
  Lenta_Q22026_DATABOOK.xlsx: PL IAS 17 (1П2026), Financials quarterly (выручка «Реми»);
  moex_iss_okey_bonds/*_bondization.json (купоны облигаций «О'КЕЙ» в январе–мае 2026).
Суждения (класс C) — треугольные распределения (мин, мода, макс) в PARAMS ниже;
ключевая ставка — таблица ЦБ (inputs/key_rate_2026.json). Монте-Карло 200 000
прогонов, зерно 20260918. Вывод: anchor_out.json, anchor_out.txt.
"""
from __future__ import annotations

import json
import random
import statistics as st
from datetime import date

from _common import HERE, PRIMARY, dump, fmt, pdf_number, pdf_page_of, pl_value, fq_table

OUT_JSON = HERE / "anchor_out.json"
OUT_TXT = HERE / "anchor_out.txt"
INPUTS = HERE / "inputs"
N_DRAWS = 200_000
SEED = 20260918

H126 = "ifrs_mkpao/ifrs_1H2026.pdf"
FY25 = "ifrs_mkpao/ifrs_FY2025.pdf"
G = r"(\d{1,3}(?:[  ]\d{3})+)"  # одно число с разделителями тысяч


def num(name, pattern):
    return pdf_number(name, pattern)


def facts():
    """Факты из первички (млрд руб.)."""
    f = {}
    f["okey_rev_since"] = num(H126, r"вклад сети гипермаркетов «О.КЕЙ» в выручку Группы составил\s*([\d ]+) тыс")
    f["okey_pbt_since"] = -num(H126, r"убыток до налогообложения с даты приобретения\s*([\d ]+) тыс")
    f["okey_rev_pf"] = num(H126, r"влияние на выручку за 2026 год составило бы\s*([\d ]+) тыс")
    f["okey_pbt_pf"] = -num(H126, r"влияние на прибыль\s*до налогообложения составило бы\s*([\d ]+) тыс")
    f["okey_ppe"] = num(H126, r"Приобретение сети гипермаркетов «О.КЕЙ» \(продолжение\).*?Основные средства \(Прим\. 4\)\s*([\d ]+)")
    f["okey_rou"] = num(H126, r"Приобретение сети гипермаркетов «О.КЕЙ» \(продолжение\).*?Активы в форме права пользования \(Прим\. 7\)\s*([\d ]+)")
    f["okey_lease_lt"] = num(H126, r"Долгосрочные обязательства по аренде \(Прим\. 7\)\s*\(([\d ]+)\)")
    f["okey_debt_lt"] = num(H126, r"Долгосрочные кредиты и займы\s*\(([\d ]+)\)")
    f["okey_debt_st"] = num(H126, r"Краткосрочные кредиты, краткосрочная часть долгосрочных кредитов\s*\(([\d ]+)\)")
    f["okey_lease_st"] = num(H126, r"Краткосрочные обязательства по аренде \(Прим\. 7\)\s*\(([\d ]+)\)")
    f["okey_seller_loan"] = num(H126, r"с учетом задолженности по\s*процентам,\s*([\d ]+) тыс")
    f["okey_cash_acq"] = num(H126, r"Денежные средства и денежные эквиваленты\s*([\d ]+)\s*Долгосрочные обязательства по аренде")
    f["diy_rev"] = num(H126, r"вклад сети гипермаркетов «Дом Лента» в выручку составил\s*([\d ]+) тыс")
    f["diy_pbt"] = -num(H126, r"был получен убыток до налогообложения\s*([\d ]+) тыс\. руб\., соответственно\.\s*В течение 6 месяцев")
    f["diy_ppe"] = num(H126, r"«ОБИ Россия» \(продолжение\).*?Основные средства \(Прим\. 4\)\s*([\d ]+)")
    f["diy_rou"] = num(H126, r"«ОБИ Россия» \(продолжение\).*?Активы в форме права пользования \(Прим\. 7\)\s*([\d ]+)")
    f["diy_lease"] = (num(H126, r"«ОБИ Россия» \(продолжение\).*?Долгосрочные обязательства по аренде \(Прим\. 7\)\s*\(([\d ]+)\)")
                      + num(H126, r"«ОБИ Россия» \(продолжение\).*?Краткосрочные обязательства по аренде \(Прим\. 7\)\s*\(([\d ]+)\)"))
    # прим. 7 (группа, 6М2026)
    f["grp_rou_dep"] = num(H126, r"Расходы по амортизации активов в форме права пользования\s*" + G)
    f["grp_lease_int"] = num(H126, r"Процентные расходы на обязательство по аренде\s*" + G)
    f["grp_lease_principal"] = num(H126, r"Выплаты основной суммы обязательств по аренде\s*\(([\d ]+)\)")
    f["grp_lease_open"] = num(H126, r"Обязательства по аренде на начало года\s*" + G)
    f["grp_lease_close"] = num(H126, r"Обязательства по аренде на конец года\s*" + G)
    f["grp_lease_acq"] = num(H126, r"Обязательства по аренде на начало года.*?Приобретение дочерних компаний \(Прим\. 5\)\s*" + G)
    f["grp_rou_open"] = num(H126, r"На 1 января 2026 г\. за вычетом накопленной амортизации\s*([\d ]+)")
    f["grp_rou_close"] = num(H126, r"На 30 июня 2026 г\. за вычетом накопленной амортизации\s*([\d ]+)")
    f["grp_rou_acq"] = num(H126, r"На 1 января 2026 г\. за вычетом накопленной амортизации.*?Приобретение дочерних компаний \(Прим\. 5\)\s*([\d ]+)")
    f["grp_ppe_dep_6m"] = num(H126, r"Амортизация основных средств \(Прим\. 4\)\s*" + G)
    f["grp_ppe_dep_6m_2025"] = num(H126, r"Амортизация основных средств \(Прим\. 4\)\s*\d{1,3}(?: \d{3})+\s+" + G)
    # «Реми»: декабрь 2025 (FY2025 прим. 8)
    f["remi_rev_dec"] = num(FY25, r"вклад компаний «Реми» в выручку Группы составил\s*([\d ]+) тыс")
    f["remi_pbt_dec"] = -num(FY25, r"«Реми» в выручку Группы составил\s*[\d ]+ тыс\. руб\.,\s*был получен убыток до налогообложения с даты приобретения\s*([\d ]+) тыс")
    f["remi_pf_2025"] = num(FY25, r"«Реми» в выручку Группы составил.*?выручка Группы за\s*2025 год составила бы\s*" + G)
    f["remi_lease"] = (num(FY25, r"группы «Реми» согласно.*?Долгосрочные обязательства по аренде \(Прим\. 10\)\s*\(([\d ]+)\)")
                       + num(FY25, r"группы «Реми» согласно.*?Краткосрочные обязательства по аренде \(Прим\. 10\)\s*\(([\d ]+)\)"))
    f["remi_rou"] = num(FY25, r"группы «Реми» согласно.*?Активы в форме права пользования \(Прим\. 10\)\s*([\d ]+)")
    f["remi_ppe"] = num(FY25, r"группы «Реми» согласно.*?Основные средства \(Прим\. 7\)\s*([\d ]+)")
    # датабук: группа 1П2026 (IAS 17) и «Реми» по кварталам
    f["grp_rev_1h26"] = pl_value("Sales", "IAS 17", "1H", "2026")
    f["grp_ebitda_1h26"] = pl_value("EBITDA", "IAS 17", "1H", "2026")
    f["grp_rev_1h25"] = pl_value("Sales", "IAS 17", "1H", "2025")
    f["grp_ebitda_1h25"] = pl_value("EBITDA", "IAS 17", "1H", "2025")
    f["grp_rev_fy25"] = pl_value("Sales", "IAS 17", "FY", "2025")
    f["grp_ebitda_fy25"] = pl_value("EBITDA", "IAS 17", "FY", "2025")
    Q = fq_table()
    f["remi_rev_1h26"] = Q["2026Q1"]["remi"] + Q["2026Q2"]["remi"]
    f["diy_rev_q_1h26"] = Q["2026Q1"]["diy"] + Q["2026Q2"]["diy"]
    return f


def bond_interest_jan_may(start=date(2026, 1, 1), end=date(2026, 6, 1)):
    """Начисленный купон облигаций «О'КЕЙ» за [start; end) по графикам ISS (valueprc — ставка купона)."""
    spec = json.loads((INPUTS / "okey_debt_jan_may_2026.json").read_text(encoding="utf-8"))
    out = []
    for b in spec["bonds"]:
        d = json.loads((PRIMARY / "moex_iss_okey_bonds" / f"{b['isin']}_bondization.json").read_text(encoding="utf-8"))
        cols = d["coupons"]["columns"]
        ci = {c: i for i, c in enumerate(cols)}
        acc = 0.0
        for row in d["coupons"]["data"]:
            s, e = date.fromisoformat(row[ci["startdate"]]), date.fromisoformat(row[ci["coupondate"]])
            rate = row[ci["valueprc"]]
            lo, hi = max(s, start), min(e, end, date.fromisoformat(b.get("out_until", "2099-12-31")))
            if hi > lo and rate is not None:
                acc += b["amount_bn"] * rate / 100 * (hi - lo).days / 365
        out.append({"isin": b["isin"], "name": b["name"], "amount_bn": b["amount_bn"], "interest_bn": acc})
    return out, spec


def key_rate_avg(start=date(2026, 1, 1), end=date(2026, 6, 1)):
    kr = json.loads((INPUTS / "key_rate_2026.json").read_text(encoding="utf-8"))
    steps = sorted((date.fromisoformat(s["from"]), s["rate"]) for s in kr["steps"])
    tot, days = 0.0, 0
    d = start
    while d < end:
        r = [rate for f, rate in steps if f <= d][-1]
        tot += r
        days += 1
        d = date.fromordinal(d.toordinal() + 1)
    return tot / days / 100, days


# Суждения: треугольные (мин, мода, макс). Обоснования — README, раздел «Мост якоря».
PARAMS = {
    # «О'КЕЙ», январь–май (151 день), своя (до сделки) учётная база по МСФО 16
    "alfa_spread": (0.010, 0.025, 0.035),          # спред кредита Альфа-Банка к ключевой (не раскрыт)
    "seller_loan_rate": (0.10, 0.16, 0.22),        # ставка займа прежнего владельца (не раскрыта)
    "seller_loan_share": (0.5, 1.0, 1.0),          # доля периода, когда заём был в долге
    "other_fin": (0.0, 0.05, 0.20),                # прочие финансовые расходы, млрд
    "int_income": (0.05, 0.15, 0.30),              # процентные доходы, млрд за 5 мес.
    "ppe_dep_rate": (0.065, 0.095, 0.13),          # годовая амортизация ОС на справедливую стоимость (с поправкой на учётную стоимость до сделки)
    "lease_scale": (0.8, 1.0, 1.2),                # обязательство по аренде до сделки к оценке «Ленты» (17,6)
    "lease_int_rate": (0.15, 0.18, 0.20),          # процент по аренде, годовых (прим. 7 группы: ≈18 %)
    "rou_to_lease": (0.85, 0.95, 1.20),            # право пользования / обязательство (группа 0,91; «О'КЕЙ» при покупке 1,20)
    "rou_dep_rate": (0.14, 0.18, 0.20),            # амортизация права пользования, годовых (группа ≈18,7 %)
    "lease_pay_ratio": (0.26, 0.30, 0.34),         # арендные платежи / обязательство, годовых (группа ≈30 %)
    "okey_oneoff": (-0.3, 0.1, 1.2),               # статьи вне EBITDA Ленты в прибыли до налога «О'КЕЙ» (обесценение, прочие неоперационные; в РСБУ 2023–2024 прочие расходы нетто 0,8–2,2 % выручки), млрд
    # «О'КЕЙ», июнь (учёт «Ленты»): проценты по принятому долгу после зачёта займа продавца
    "june_rate": (0.150, 0.170, 0.190),
    # «Дом Лента», 1П2026
    "diy_months": (3.5, 4.5, 5.5),                 # средняя длительность консолидации (поэтапно январь–март)
    "diy_ppe_dep_rate": (0.08, 0.12, 0.16),
    "diy_ig_interest": (0.0, 0.3, 0.8),            # внутригрупповые проценты в «вкладе» (если есть), млрд
    # «Реми»
    "remi_margin_1h26": (-0.02, 0.01, 0.04),       # EBITDA IAS 17 / выручка «Реми» в 1П2026 (не раскрыта)
}


def tri(rng, p):
    a, c, b = p
    return rng.triangular(a, b, c)


def main():
    f = facts()
    L = []
    P = L.append
    P("# anchor_bridge.py — маржа якоря на проформе 1П2026")
    P("\n## факты (млрд руб.; первичка)")
    for k, v in f.items():
        P(f"{k}: {v:.6f}")

    # --- тождества и проверки фактов
    rev_5m = f["okey_rev_pf"] - f["okey_rev_since"]
    pbt_5m = f["okey_pbt_pf"] - f["okey_pbt_since"]
    rev_pf = f["grp_rev_1h26"] + rev_5m
    okey_lease = f["okey_lease_lt"] + f["okey_lease_st"]
    okey_debt = f["okey_debt_lt"] + f["okey_debt_st"]
    checks = {
        "проформа выручки = 648,5 + 66,12 − 9,36 ≈ 705,26": abs(rev_pf - 705.2490) < 0.01,
        "принятый долг «О'КЕЙ» ≈ 50,90": abs(okey_debt - 50.896) < 0.01,
        "аренда «О'КЕЙ» ≈ 17,64": abs(okey_lease - 17.636) < 0.01,
        "аренда ОКЕЙ + DIY = приобретённая аренда группы (28,49)": abs(okey_lease + f["diy_lease"] - f["grp_lease_acq"]) < 0.01,
        "ПП ОКЕЙ + DIY = приобретённое ПП группы (32,21)": abs(f["okey_rou"] + f["diy_rou"] - f["grp_rou_acq"]) < 0.01,
        "выручка DIY: прим. 5 = датабук (Q1+Q2), ±0,05": abs(f["diy_rev"] - f["diy_rev_q_1h26"]) < 0.05,
    }
    P("\n## проверки фактов")
    for k, ok in checks.items():
        P(f"{'ДА' if ok else 'НЕТ'} — {k}")
    P(f"«О'КЕЙ» январь–май: выручка {rev_5m:.4f}, прибыль до налога {pbt_5m:.4f} (проформа минус вклад с 02.06)")

    # --- коэффициенты аренды группы 1П2026 (прим. 7): годовые, на средний остаток без июня приобретений
    avg_lease = (f["grp_lease_open"] + f["grp_lease_close"] - f["grp_lease_acq"] * 5 / 6) / 2
    avg_rou = (f["grp_rou_open"] + f["grp_rou_close"] - f["grp_rou_acq"] * 5 / 6) / 2
    k_int = 2 * f["grp_lease_int"] / avg_lease
    k_dep = 2 * f["grp_rou_dep"] / avg_rou
    k_pay = 2 * (f["grp_lease_int"] + f["grp_lease_principal"]) / avg_lease
    P(f"\n## коэффициенты аренды группы 1П2026 (годовые): процент {k_int:.4f}; амортизация ПП {k_dep:.4f}; "
      f"платежи/обязательство {k_pay:.4f}; ПП/обязательство {avg_rou / avg_lease:.3f}")
    ppe_dep_grp = 2 * f["grp_ppe_dep_6m_2025"]
    P(f"амортизация ОС группы 1П2025 (год. экв.) {ppe_dep_grp:.3f} млрд (для сверки ставки амортизации)")

    # --- проценты «О'КЕЙ» январь–май: облигации по графикам ISS + кредит Альфа-Банка + заём продавца
    bonds, spec = bond_interest_jan_may()
    kr, days = key_rate_avg()
    frac = days / 365
    bonds_int = sum(b["interest_bn"] for b in bonds)
    P(f"\n## проценты «О'КЕЙ» 01.01–31.05.2026 ({days} дн.); средняя ключевая {kr * 100:.3f} %")
    for b in bonds:
        P(f"{b['name']} ({b['isin']}): {b['amount_bn']:.3f} млрд → купон {b['interest_bn']:.4f}")
    P(f"облигации итого {bonds_int:.4f}; кредит Альфа-Банка {spec['alfa_bn']} млрд по ключевой + спред (суждение)")

    rng = random.Random(SEED)
    draws = {k: [] for k in ("okey5_ebitda", "okey_june_ebitda", "diy_ebitda", "remi_ebitda", "margin_pf",
                             "legacy_margin", "acq_margin", "okey5_int")}
    for _ in range(N_DRAWS):
        pr = {k: tri(rng, v) for k, v in PARAMS.items()}
        interest = (spec["alfa_bn"] * (kr + pr["alfa_spread"]) * frac + bonds_int
                    + f["okey_seller_loan"] * pr["seller_loan_rate"] * frac * pr["seller_loan_share"] + pr["other_fin"])
        lease = okey_lease * pr["lease_scale"]
        lease_net = lease * (pr["lease_int_rate"] + pr["rou_dep_rate"] * pr["rou_to_lease"] - pr["lease_pay_ratio"]) * frac
        ppe_dep = f["okey_ppe"] * pr["ppe_dep_rate"] * frac
        e5 = pbt_5m + interest - pr["int_income"] + ppe_dep + lease_net + pr["okey_oneoff"]
        # июнь (29 дн. консолидации, учёт «Ленты»: ПП/обязательство 1,20 при покупке)
        fj = 29 / 365
        debt_after = okey_debt - f["okey_seller_loan"]
        lease_net_j = okey_lease * (pr["lease_int_rate"] + pr["rou_dep_rate"] * f["okey_rou"] / okey_lease - pr["lease_pay_ratio"]) * fj
        ej = f["okey_pbt_since"] + debt_after * pr["june_rate"] * fj + f["okey_ppe"] * pr["ppe_dep_rate"] * fj + lease_net_j
        # «Дом Лента»
        fm = pr["diy_months"] / 12
        ed = (f["diy_pbt"] + f["diy_lease"] * (pr["lease_int_rate"] + pr["rou_dep_rate"] * f["diy_rou"] / f["diy_lease"]
                                               - pr["lease_pay_ratio"]) * fm
              + f["diy_ppe"] * pr["diy_ppe_dep_rate"] * fm + pr["diy_ig_interest"])
        er = f["remi_rev_1h26"] * pr["remi_margin_1h26"]
        m_pf = (f["grp_ebitda_1h26"] + e5) / rev_pf
        leg_rev = f["grp_rev_1h26"] - f["okey_rev_since"] - f["diy_rev"] - f["remi_rev_1h26"]
        leg_e = f["grp_ebitda_1h26"] - ej - ed - er
        acq_rev = f["okey_rev_pf"] + f["diy_rev"] + f["remi_rev_1h26"]
        acq_e = e5 + ej + ed + er
        for k, v in (("okey5_ebitda", e5), ("okey_june_ebitda", ej), ("diy_ebitda", ed), ("remi_ebitda", er),
                     ("margin_pf", m_pf), ("legacy_margin", leg_e / leg_rev), ("acq_margin", acq_e / acq_rev),
                     ("okey5_int", interest)):
            draws[k].append(v)

    def summ(x):
        xs = sorted(x)
        n = len(xs)
        return {"mean": st.fmean(xs), "sd": st.pstdev(xs), "p05": xs[int(0.05 * n)], "p10": xs[int(0.10 * n)],
                "p50": xs[n // 2], "p90": xs[int(0.90 * n)], "p95": xs[int(0.95 * n)]}
    S = {k: summ(v) for k, v in draws.items()}
    leg_rev = f["grp_rev_1h26"] - f["okey_rev_since"] - f["diy_rev"] - f["remi_rev_1h26"]
    acq_rev = f["okey_rev_pf"] + f["diy_rev"] + f["remi_rev_1h26"]
    P("\n## Монте-Карло (млрд руб. / доли): среднее; sd; P10; медиана; P90")
    for k, s in S.items():
        P(f"{k}: {s['mean']:.4f}; {s['sd']:.4f}; {s['p10']:.4f}; {s['p50']:.4f}; {s['p90']:.4f}")

    m_rep = f["grp_ebitda_1h26"] / f["grp_rev_1h26"]
    mc = S["margin_pf"]
    P(f"\nмаржа 1П2026 отчётная {m_rep * 100:.3f} %; на проформе (медиана) {mc['p50'] * 100:.3f} % "
      f"(P10–P90 {mc['p10'] * 100:.3f}–{mc['p90'] * 100:.3f}; sd {mc['sd'] * 100:.3f} п.п.)")
    P(f"выручка проформы {rev_pf:.4f}; EBITDA проформы (медиана) {f['grp_ebitda_1h26'] + S['okey5_ebitda']['p50']:.4f}")
    P(f"унаследованный периметр: выручка {leg_rev:.3f}, маржа (медиана) {S['legacy_margin']['p50'] * 100:.3f} % "
      f"(P10–P90 {S['legacy_margin']['p10'] * 100:.2f}–{S['legacy_margin']['p90'] * 100:.2f})")
    P(f"приобретённый периметр («О'КЕЙ» полное 1П + «Дом Лента» + «Реми»): выручка {acq_rev:.3f} "
      f"(доля {acq_rev / rev_pf:.4f}), маржа (медиана) {S['acq_margin']['p50'] * 100:.3f} % "
      f"(P10–P90 {S['acq_margin']['p10'] * 100:.2f}–{S['acq_margin']['p90'] * 100:.2f})")
    okey_1h = S["okey5_ebitda"]["p50"] + S["okey_june_ebitda"]["p50"]
    P(f"«О'КЕЙ» полное 1П: EBITDA ≈ {okey_1h:.3f} ({okey_1h / f['okey_rev_pf'] * 100:.2f} %); "
      f"«Дом Лента» {S['diy_ebitda']['p50']:.3f} ({S['diy_ebitda']['p50'] / f['diy_rev'] * 100:.1f} %); "
      f"«Реми» {S['remi_ebitda']['p50']:.3f}")
    # «Реми», декабрь 2025 (для рядов): EBITDA ≈ PBT + аренда (нетто, центр) + амортизация ОС
    remi_dec = (f["remi_pbt_dec"] + f["remi_lease"] * (0.18 + 0.18 * f["remi_rou"] / f["remi_lease"] - 0.30) * 29 / 365
                + f["remi_ppe"] * 0.12 * 29 / 365)
    P(f"«Реми» декабрь 2025 (29 дн.): EBITDA ≈ {remi_dec:.3f} на выручке {f['remi_rev_dec']:.3f} ({remi_dec / f['remi_rev_dec'] * 100:.1f} %)")

    # --- разложение падения маржи (для веса классов, refclass.py): 2025 → 1П2026 на проформе в годовом уровне
    ser = json.loads((HERE / "series_out.json").read_text(encoding="utf-8")) if (HERE / "series_out.json").exists() else None
    res = {
        "schema": "lenta-book-1.0-margin-anchor-v1",
        "facts": {k: round(v, 6) for k, v in f.items()},
        "fact_checks": checks,
        "okey_5m": {"revenue": rev_5m, "pbt": pbt_5m},
        "revenue_pro_forma_1h26": rev_pf,
        "lease_ratios_group_1h26": {"interest": k_int, "rou_dep": k_dep, "payments": k_pay, "rou_to_lease": avg_rou / avg_lease},
        "okey_interest_jan_may": {"key_rate_avg": kr, "days": days, "bonds": bonds, "bonds_total": bonds_int},
        "params": PARAMS, "n_draws": N_DRAWS, "seed": SEED,
        "mc": S,
        "margin_reported_1h26": m_rep,
        "margin_pro_forma": round(mc["p50"], 4),
        "margin_pro_forma_se": round(mc["sd"], 4),
        "ebitda_pro_forma_1h26": f["grp_ebitda_1h26"] + S["okey5_ebitda"]["p50"],
        "legacy": {"revenue": leg_rev, "margin": S["legacy_margin"]["p50"], "margin_sd": S["legacy_margin"]["sd"]},
        "acquired": {"revenue": acq_rev, "share": acq_rev / rev_pf, "margin": S["acq_margin"]["p50"],
                     "margin_sd": S["acq_margin"]["sd"],
                     "parts": {"okey_1h": okey_1h, "diy": S["diy_ebitda"]["p50"], "remi": S["remi_ebitda"]["p50"]}},
        "remi_dec_2025": {"revenue": f["remi_rev_dec"], "ebitda": remi_dec},
        "pages": {"okey_pf": pdf_page_of(H126, r"влияние на выручку за 2026 год"),
                  "okey_fv": pdf_page_of(H126, r"Справедливая стоимость идентифицируемых чистых активов\s*\(12"),
                  "diy": pdf_page_of(H126, r"«Дом Лента» в выручку составил"),
                  "lease_note": pdf_page_of(H126, r"Расходы по амортизации активов в форме права пользования"),
                  "remi_fy25": pdf_page_of(FY25, r"вклад компаний «Реми» в выручку")},
    }
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    if not all(checks.values()):
        raise SystemExit("проверки фактов не прошли")


if __name__ == "__main__":
    main()
