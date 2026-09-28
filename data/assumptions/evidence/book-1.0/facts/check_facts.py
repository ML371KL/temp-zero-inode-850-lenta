"""Проверка тождеств базы фактов (book-draft/facts/*.json). Печатает каждое тождество.

    python check_facts.py            # код 0 — все обязательные тождества держатся
                                     # код 1 — нарушено обязательное тождество

Обязательные тождества (FAIL ломает сборку книги) и известные расхождения датабука
(KNOWN — печатаются, но не ломают: они описаны в accounting_base.json → data_issues).
Первичку не читает: работает только по facts/*.json (и потому годится для CI).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FACTS = Path(os.environ.get("LENTA_FACTS_OUT", HERE.parents[2] / "facts"))

RESULTS = []


def load(name):
    return json.loads((FACTS / name).read_text(encoding="utf-8"))


def check(name, lhs, rhs, tol=0.0015, kind="MUST", note=""):
    ok = lhs is not None and rhs is not None and abs(lhs - rhs) <= tol
    status = "OK  " if ok else ("FAIL" if kind == "MUST" else "KNOWN")
    RESULTS.append((status, name, lhs, rhs, note))
    lv = "None" if lhs is None else f"{lhs:,.6f}".replace(",", " ")
    rv = "None" if rhs is None else f"{rhs:,.6f}".replace(",", " ")
    print(f"[{status}] {name}: {lv} vs {rv}" + (f"  — {note}" if note else ""))
    return ok


def v(d, *path):
    for p in path:
        if d is None:
            return None
        d = d.get(p) if isinstance(d, dict) else None
    if isinstance(d, dict):
        return d.get("v")
    return d


def main():
    acc = load("accounting_base.json")
    anc = load("anchor.json")
    sh = load("shares.json")
    debt = load("debt.json")
    br = load("bridge_balance.json")
    ino = load("inorganic.json")
    peers = load("peers.json")
    H, FY, BS, Q = acc["halves"], acc["fy"], acc["balance"], acc["quarters"]

    print("== 1. EBITDA = операционная прибыль до обесценения + амортизация (обе базы)")
    for per in sorted(H):
        for b in ("ias17", "ifrs16"):
            pl = H[per].get(b, {}).get("pl", {})
            if not pl or "ebitda" not in pl:
                continue
            e, o, d = v(pl, "ebitda"), v(pl, "opbi"), v(pl, "da")
            if None in (e, o, d):
                continue
            kind = "KNOWN" if per == "2025H2" else "MUST"
            check(f"{per} {b} EBITDA = OPBI − D&A", e, o - d, tol=0.0015 if kind == "MUST" else 0.05, kind=kind,
                  note="FY2025: +0,039 в обеих базах (строка сверки прим. 12 КФО 2025)" if kind == "KNOWN" else "")
    for fy in ("FY2025",):
        for b in ("ias17", "ifrs16"):
            pl = FY[fy][b]["pl"]
            check(f"{fy} {b} EBITDA − (OPBI − D&A) = 0,039 (известное)", v(pl, "ebitda") - (v(pl, "opbi") - v(pl, "da")), 0.039, tol=0.001, kind="MUST")

    print("\n== 2. Выручка одинакова в обеих базах; валовая прибыль = выручка + себестоимость")
    for per in sorted(H):
        a, b = v(H[per].get("ias17", {}).get("pl", {}), "revenue"), v(H[per].get("ifrs16", {}).get("pl", {}), "revenue")
        if a is not None and b is not None:
            check(f"{per} выручка IAS17 = МСФО16", a, b)
        for bs_ in ("ias17", "ifrs16"):
            pl = H[per].get(bs_, {}).get("pl", {})
            if pl and v(pl, "cogs") is not None:
                check(f"{per} {bs_} ВП = выручка + себестоимость", v(pl, "gross_profit"), v(pl, "revenue") + v(pl, "cogs"))
    for q in sorted(Q):
        a, b = v(Q[q].get("ias17", {}), "revenue"), v(Q[q].get("ifrs16", {}), "revenue")
        if a is not None and b is not None:
            check(f"{q} выручка квартала IAS17 = МСФО16", a, b, tol=0.002, kind="KNOWN" if q == "2022Q1" else "MUST",
                  note="DB-6: блок МСФО 16 повторяет 3кв2021" if q == "2022Q1" else "")

    print("\n== 3. Кварталы складываются в полугодия; розница + опт = выручка; форматы = розница")
    for per in sorted(H):
        y, h = per[:4], per[-1]
        qs = (f"{y}Q1", f"{y}Q2") if h == "1" else (f"{y}Q3", f"{y}Q4")
        if all(q in Q for q in qs):
            s = sum(v(Q[q]["ias17"], "revenue") for q in qs)
            check(f"{per} Σ кварталов = выручка PL", s, v(H[per]["ias17"]["pl"], "revenue"), tol=0.05, kind="KNOWN" if per in ("2020H2", "2021H2", "2024H2", "2025H2") else "MUST",
                  note="расхождения ≤0,05: квартальный лист в млн ₽ с округлением" if per in ("2020H2", "2021H2", "2024H2", "2025H2") else "")
            e = sum(v(Q[q]["ias17"], "ebitda") for q in qs)
            check(f"{per} Σ EBITDA кварталов = EBITDA PL (IAS 17)", e, v(H[per]["ias17"]["pl"], "ebitda"), tol=0.05, kind="KNOWN")
    segs = ("seg_hyper_db", "seg_super", "seg_convenience", "seg_utkonos", "seg_droge", "seg_remi", "seg_diy", "seg_other_formats")
    for q in sorted(Q):
        x = Q[q]["ias17"]
        if v(x, "retail") is None:
            continue
        kq = q in ("2023Q4", "2024Q3", "2024Q4", "2025Q4")
        check(f"{q} розница + опт = выручка", v(x, "retail") + (v(x, "wholesale") or 0), v(x, "revenue"), tol=0.1 if kq else 0.002,
              kind="KNOWN" if kq else "MUST", note="DB-7: итог квартального листа ≠ сумме его строк (≤0,07)" if kq else "")
        s = sum((v(x, k) or 0) for k in segs)
        kf = q in ("2024Q2", "2024Q3")
        check(f"{q} Σ форматов = розница", s, v(x, "retail"), tol=0.1 if kf else 0.002, kind="KNOWN" if kf else "MUST",
              note="DB-11: «Other formats» 2–3 кв. 2024 = 0 в Financials quarterly (в Operating Results 0,029 / 0,072)" if kf else "")

    print("\n== 4. Баланс: активы = капитал + обязательства; касса ОДДС = баланс; ЧД = долг − касса")
    for date in sorted(BS):
        for b in ("ias17", "ifrs16"):
            x = BS[date].get(b)
            if not x or v(x, "total_assets") is None:
                continue
            check(f"{date} {b} активы = капитал + обязательства", v(x, "total_assets"), v(x, "total_le"), tol=0.002)
    for per in ("2025H1", "2026H1"):
        date = f"{per[:4]}-06-30"
        check(f"{per} касса на конец ОДДС = баланс", v(H[per]["ifrs16"]["cf"], "cash_end"), v(BS[date]["ifrs16"], "cash"))
    for date, rec in sorted(acc["debt_sheet"]["ias17"].items()):
        if date < "2020-01-01" or "net_debt" not in rec:
            continue
        bsd = BS.get(date, {}).get("ias17")
        if bsd:
            nd = v(bsd, "lt_borrowings") + v(bsd, "st_borrowings") - v(bsd, "cash")
            check(f"{date} ЧД (баланс) = лист Debt", nd, rec["net_debt"]["v"], tol=0.002)
    check("30.06.2026 ЧД = 100,030371 + 42,553313 − 25,172516", anc["net_debt"]["v"], 117.411168, tol=1e-6)
    check("ЧД/EBITDA LTM = 1,39 (датабук)", round(anc["net_debt"]["v"] / anc["ebitda_ltm"]["reported"], 2), 1.39, tol=0.0001)

    print("\n== 5. Проформа якоря (прим. 5 МСФО 6М2026)")
    ok = ino["deals"]
    okey = next(d for d in ok if d["id"] == "okey")
    check("выручка проформа = отчёт + (66,120725 − 9,359828)", anc["revenue"]["pro_forma"],
          anc["revenue"]["reported"] + okey["pro_forma"]["revenue_1H2026_full"] - okey["contribution"]["revenue"], tol=1e-6)
    check("прибавка к выручке = 56,760897", okey["pro_forma"]["add_to_reported_revenue"], 56.760897, tol=1e-6)
    check("прибыль до налога проформа = 15,077669 − 5,530179 + 0,907372", anc["pbt_ifrs16"]["pro_forma"], 15.077669 - 5.530179 + 0.907372, tol=1e-6)
    check("выручка «О’КЕЙ» 2025 × доля 1П «Ленты» 2025 ≈ 66,12 (прочтение «вклад за всё полугодие»)",
          142.0 * v(H["2025H1"]["ias17"]["pl"], "revenue") / (v(FY["FY2025"]["ias17"]["pl"], "revenue")), okey["pro_forma"]["revenue_1H2026_full"], tol=0.1,
          kind="KNOWN", note="контроль прочтения, не тождество")

    print("\n== 6. Сделки и мост")
    check("гудвил: 63,935312 + 13,694519 + 8,758502 = 86,388333", ino["goodwill_bridge_1H2026"]["opening"] + ino["goodwill_bridge_1H2026"]["okey"] + ino["goodwill_bridge_1H2026"]["domlenta"], ino["goodwill_bridge_1H2026"]["closing"], tol=1e-6)
    check("гудвил по ЕГДП = итог", sum(ino["goodwill_bridge_1H2026"]["by_cgu"].values()), ino["goodwill_bridge_1H2026"]["closing"], tol=1e-6)
    a = ino["acquisition_cash_1H2026"]
    check("ОДДС приобретения = «О’КЕЙ» 0,698271 + «ОБИ» 4,498910 + «Реми» 2,0 + прочие 0,600271", a["total"], a["cf_line"], tol=1e-6)
    obi = next(d for d in ok if d["id"] == "obi_domlenta")
    check("«ОБИ»: 17,303283 = деньги + зачёт + остаток", 4.498910 + 8.734162 + br["obi_remaining_payment"]["amount"], obi["consideration"], tol=1e-6)
    check("«О’КЕЙ»: гудвил = возмещение − чистые активы", okey["consideration"] - okey["net_assets"], okey["goodwill"], tol=1e-6)
    check("«ОБИ»: гудвил = возмещение − чистые активы", obi["consideration"] - obi["net_assets"], obi["goodwill"], tol=1e-6)
    check("принятый долг «О’КЕЙ» = 32,283177 + 18,613045", okey["assumed_debt"]["total"], 50.896222, tol=1e-6)
    p = br["remi_put"]
    check("пут «Реми»: 4,766102 + размотка 0,448072 = 5,214174", p["prior_2025_12_31"] + p["accretion_1H2026"], p["amount"], tol=1e-6)
    i = debt["interest_1H2026"]
    check("процентные расходы = кредиты + аренда + прочие", i["loans"] + i["lease"] + i["put_unwind_other"], i["total"], tol=1e-6)
    n = br["nci"]
    check("НДУ = трек «Реми» (0,978523 − 0,042919 + 0,056103)", n["remi_track"]["implied_2026h1"], n["balance_2026h1"], tol=1e-6)
    check("НДУ без «Реми» = 0", n["nci_ex_remi"], 0.0, tol=1e-6)
    check("НДУ 31.12.2025 = 0,978523 − 0,042919", n["remi_track"]["at_acquisition"] + n["remi_track"]["loss_share_2025"], n["balance_2025fy"], tol=1e-6)
    check("прочие долгосрочные 31.12.2025 = пут + LTIP", p["prior_2025_12_31"] + br["ltip"]["amount_lt_2025fy"], 6.531216, tol=1e-6)
    pay = br["payables_note22"]
    check("кредиторка прим. 22: Σ строк = итог", pay["trade"] + pay["capex_and_business"] + pay["accrued_other"] + pay["personnel"] + pay["option_put"] + pay["factoring"], pay["total"], tol=1e-6)
    fv = debt["fair_value_note32"]
    check("балансовая: фикс + плав = долг баланса", fv["fixed"]["book"] + fv["floating"]["book"], debt["reported"]["gross"], tol=1e-6)
    check("справедливая: фикс + плав = итог", fv["fixed"]["fair"] + fv["floating"]["fair"], fv["total"]["fair"], tol=1e-6)
    check("справедливая фикс = уровни 2 + 1", fv["levels"]["fixed_loans_l2"] + fv["levels"]["bonds_l1"], fv["fixed"]["fair"], tol=1e-6)
    r = debt["reported"]
    check("прим. 20: основной долг + проценты = валовой", r["principal"] + r["accrued_interest"], r["gross"], tol=1e-6)
    check("прим. 20: фикс + плав + облигации = основной", r["fixed_principal"] + r["float_principal"] + r["bonds_carrying"], r["principal"], tol=1e-6)
    ll = br["lease_liabilities"]
    check("аренда: долгосрочная + краткосрочная = итог", ll["lt"] + ll["st"], ll["total"], tol=1e-6)
    lr = br["loans_issued"]["reconstruction"]
    check("займы выданные 3,339890: реконструкция схемы зачётов", lr["v"], br["loans_issued"]["amount"], tol=0.002, kind="KNOWN",
          note="гипотеза; невязка ≤2 млн ₽ — контрагент не раскрыт")
    check("компенсирующий актив = прочие оборотные активы 30.06.2026", br["indemnification_asset"]["amount"], 2.471493, tol=1e-6)

    print("\n== 7. Акции")
    check("в обращении = выпущено − квазиказначейские", sh["issued"]["v"] - sh["quasi_treasury"]["v"], sh["outstanding"]["v"], tol=0)
    check("выпущено = ISS ISSUESIZE", sh["issued"]["v"], sh["issued"]["crosscheck"]["iss_issuesize"], tol=0)
    check("EPS 6М2026 = 10 612 827 / 115 074 675 (тыс. ₽ на акцию, до 3 знаков)", round(sh["eps_check"]["profit_parent_th"] / sh["outstanding"]["v"], 3), sh["eps_check"]["eps_reported_th_per_share"], tol=0)
    check("голосующие ГЗОСА + ДР без голоса = выпущено", 111104354.4 + sh["voting_at_agm_2026"]["dr_program_non_voting"]["v"], sh["issued"]["v"], tol=0.01)

    print("\n== 8. LTM и аналоги")
    fy, h1_25, h1_26 = FY["FY2025"]["ias17"]["pl"], H["2025H1"]["ias17"]["pl"], H["2026H1"]["ias17"]["pl"]
    check("выручка LTM = FY2025 − 1П2025 + 1П2026", anc["revenue_ltm"]["reported"], v(fy, "revenue") - v(h1_25, "revenue") + v(h1_26, "revenue"), tol=1e-6)
    check("EBITDA LTM (полугодия) = Σ 4 кварталов", anc["ebitda_ltm"]["reported"], anc["ebitda_ltm"]["quarters_check"], tol=0.002)
    lt = peers["lent"]
    check("LENT EV = капитализация + ЧД", lt["mcap"] + lt["net_debt"], lt["ev"], tol=0.002)
    check("LENT капитализация = 1 619,5 × 115,074675 млн", lt["price_legal_close"] * lt["shares_mln"] / 1000, lt["mcap"], tol=0.002)
    for pr in peers["peers"]:
        check(f"{pr['ticker']} EV = капитализация + ЧД + дивиденды после баланса", pr["mcap"] + pr["net_debt"] + pr["dividends_after_balance"], pr["ev"], tol=0.002)
    check("плотность «О’КЕЙ» 2025 ≈ 297 тыс. ₽/м²", okey["density_k_rub_per_m2"]["okey_2025"], 297.1, tol=0.1)
    check("плотность гипермаркетов «Ленты» 2025 ≈ 418 тыс. ₽/м²", okey["density_k_rub_per_m2"]["lenta_hyper_2025"], 418.1, tol=0.2)

    print("\n== 9. Сегменты полугодий (сумма форматов + опт = выручка PL)")
    SH = acc["segment_revenue_halves_ias17"]
    for per in ("2025H1", "2025H2", "2026H1"):
        x = SH[per]
        s = sum(v(x, k) or 0 for k in segs) + v(x, "wholesale")
        check(f"{per} Σ форматов + опт = выручка PL", s, v(H[per]["ias17"]["pl"], "revenue"), tol=0.05)

    fails = [r for r in RESULTS if r[0] == "FAIL"]
    known = [r for r in RESULTS if r[0] == "KNOWN"]
    print(f"\nИТОГ: тождеств {len(RESULTS)}, OK {len(RESULTS) - len(fails) - len(known)}, известных расхождений {len(known)}, FAIL {len(fails)}")
    for r in fails:
        print("  FAIL:", r[1])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
