#!/usr/bin/env python3
"""Лист «Финансирование и мост EV → капитал» книги 1.0 Ленты (разделы 8 и 10).

Читает первичку из каталога LENTA_PRIMARY_DIR (по умолчанию — reference/primary
папки передачи, найденный относительно этого файла), сверяет sha256 с MANIFEST.md,
вынимает числа из датабука и текстового слоя МСФО (цифры в нём есть, кириллицы нет),
считает производные величины листа и проверяет тождества. Результат —
out/financing_bridge_out.json и отчёт в stdout. Код возврата 1 — провал проверки.

Запуск:  python financing_bridge.py            (расчёт + проверки)
         python financing_bridge.py --fragment  (плюс сверка fragments/financing-bridge.yaml)

Единицы: млн руб. внутри, млрд руб. в выходе книги. Литералы в коде — только
ожидаемые значения первички (их и проверяем) и явно названные суждения листа.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
HANDOFF = HERE.parents[3]                     # .../lenta-850-handoff
PRIMARY = Path(os.environ.get("LENTA_PRIMARY_DIR", HANDOFF / "reference" / "primary"))
FRAGMENT = HERE.parents[2] / "fragments" / "financing-bridge.yaml"
OUT = HERE / "out"

FAIL: list[str] = []
LOG: list[str] = []


def say(msg: str = "") -> None:
    LOG.append(msg)
    print(msg)


def check(cond: bool, what: str) -> None:
    say(("  OK   " if cond else "  FAIL ") + what)
    if not cond:
        FAIL.append(what)


def close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- sha256
def manifest() -> dict[str, str]:
    rows = {}
    for line in (PRIMARY / "MANIFEST.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.split("|")]
        if len(cells) > 5 and re.fullmatch(r"[0-9a-f]{64}", cells[4] or ""):
            rows[cells[1]] = cells[4]
    return rows


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


USED = [
    "Lenta_Q22026_DATABOOK.xlsx",
    "ifrs_mkpao/ifrs_1H2026.pdf",
    "ifrs_mkpao/ifrs_FY2025.pdf",
    "lenta_dividend_policy_2021_ru.pdf",
    "Lenta_strategy-presentation-2028.pdf",
    "Lenta_Q2-2026_Investor-Presentation_rus.pdf",
    "ratings/acra_pr6824_Lenta_2026-04-28.txt",
    "corp_docs/issuer_report_MKPAO_6M2026_sec1_7_extracted.txt",
    "lentagroup_news/2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.html",
    "moex_iss_lenta_bonds/RU000A0JXRX3_bondization.json",
    "moex_iss_lenta_bonds/RU000A100782_bondization.json",
    "moex_iss_lenta_bonds/RU000A1011A7_bondization.json",
    "moex_iss_lenta_bonds/RU000A101R33_bondization.json",
    "moex_iss_okey_bonds/marketdata_okey_traded_2026-09-28.json",
]


# --------------------------------------------------------------------------- readers
def pdf_pages(rel: str):
    import pypdf
    return pypdf.PdfReader(str(PRIMARY / rel)).pages


_TEXT_CACHE: dict[tuple[str, int], str] = {}


def page_text(rel: str, page: int) -> str:
    key = (rel, page)
    if key not in _TEXT_CACHE:
        _TEXT_CACHE[key] = (pdf_pages(rel)[page - 1].extract_text() or "").replace("\xa0", " ")
    return _TEXT_CACHE[key]


def page_numbers(rel: str, page: int) -> list[str]:
    return re.findall(r"\d{1,3}(?: \d{3})+", page_text(rel, page))


def num(rel: str, page: int, token: str) -> float:
    """Число из текстового слоя страницы — только если оно там есть (тыс. руб. → млн руб.)."""
    present = token in page_numbers(rel, page)
    check(present, f"{rel} с. {page}: есть число {token}")
    return int(token.replace(" ", "")) / 1000.0


def databook():
    import openpyxl
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(PRIMARY / "Lenta_Q22026_DATABOOK.xlsx", data_only=True)


def row_by_label(ws, label: str, col: int = 1, exact: bool = False) -> int:
    """Первая строка листа, чья подпись начинается с label (exact — совпадает целиком)."""
    for r in range(1, ws.max_row + 1):
        v = ws.cell(r, col).value
        if not isinstance(v, str):
            continue
        v = v.strip().lower()
        if (v == label.lower()) if exact else v.startswith(label.lower()):
            return r
    raise KeyError(label)


def col_by_header(ws, header_row: int, header, first: bool = True) -> int:
    cols = [c for c in range(1, ws.max_column + 1) if ws.cell(header_row, c).value == header]
    if not cols:
        raise KeyError(header)
    return cols[0] if first else cols[-1]


# --------------------------------------------------------------------------- grid dates
def half_bounds(period: str) -> tuple[dt.date, dt.date]:
    y, h = int(period[:4]), period[4:]
    return (dt.date(y, 1, 1), dt.date(y, 6, 30)) if h == "H1" else (dt.date(y, 7, 1), dt.date(y, 12, 31))


def elapsed_in_half(period: str, day: dt.date) -> float:
    """Доля полугодия на дату — конвенция переносимого ядра: (день − начало) / число дней полугодия."""
    start, end = half_bounds(period)
    span = (end - start).days + 1
    return max(0.0, min(1.0, (day - start).days / span))


# =========================================================================== main
def main() -> int:
    want_fragment = "--fragment" in sys.argv
    say(f"LENTA_PRIMARY_DIR = <каталог первички> ({PRIMARY.name})")
    say("\n## 0. Первичка: sha256 против MANIFEST.md")
    man = manifest()
    for rel in USED:
        p = PRIMARY / rel
        exp = man.get(rel)
        if exp is None:
            check(False, f"{rel}: нет строки в MANIFEST")
            continue
        check(p.exists() and sha256(p) == exp, f"{rel}: sha256 {exp[:12]}…")

    R: dict = {"meta": {"script": "financing_bridge.py", "facts_date": "2026-06-30",
                        "valuation_date": "2026-09-18", "units": "млрд руб., если не сказано иное"}}
    H1 = "ifrs_mkpao/ifrs_1H2026.pdf"
    FY = "ifrs_mkpao/ifrs_FY2025.pdf"

    # ------------------------------------------------------------------ 1. долг
    say("\n## 1. Реестр долга на 30.06.2026 (МСФО 1П2026, прим. 20, с. 31; млн руб.)")
    st_fixed = num(H1, 31, "30 655 777")
    st_float = num(H1, 31, "5 000 000")
    st_bonds = num(H1, 31, "6 500 000")
    acc = [num(H1, 31, t) for t in ("63 604", "135 740", "141 621", "56 571", "262 932")]
    st_total = num(H1, 31, "42 553 313")
    lt_fixed = num(H1, 31, "67 484 262")
    lt_float = num(H1, 31, "32 000 000")
    lt_bonds = num(H1, 31, "283 177")
    lt_total = num(H1, 31, "100 030 371")
    undrawn = num(H1, 31, "283 863 000")
    accrued = sum(acc)
    check(close(st_fixed + st_float + st_bonds + sum(acc[:4]), st_total, 0.002), "краткосрочный долг = сумма строк")
    check(close(lt_fixed + lt_float + lt_bonds + acc[4], lt_total, 0.002), "долгосрочный долг = сумма строк")
    gross = st_total + lt_total
    principal = st_fixed + st_float + st_bonds + lt_fixed + lt_float + lt_bonds
    fixed_loans = st_fixed + lt_fixed
    floating = st_float + lt_float
    bonds = st_bonds + lt_bonds
    say(f"  валовой долг {gross:,.1f}; основной долг {principal:,.1f}; проценты к уплате {accrued:,.1f}")
    say(f"  фикс. кредиты {fixed_loans:,.1f} ({fixed_loans/principal:.1%}); плав. {floating:,.1f} "
        f"({floating/principal:.1%}); облигации {bonds:,.1f} ({bonds/principal:.1%})")

    # Кредитор 3 = материнская компания (прим. 3, с. 14) = отчёт эмитента п. 1.7.1
    sever = num(H1, 14, "30 500 000")
    sever_int = num(H1, 14, "262 932")
    er = (PRIMARY / "corp_docs/issuer_report_MKPAO_6M2026_sec1_7_extracted.txt").read_text(encoding="utf-8")
    check("30\\~762 932 000" in er and "25.05.202628.05.2029" in er,
          "отчёт эмитента п. 1.7.1: Кредитор 3 = 30 762 932 тыс. руб., договор 25.05.2026 → 28.05.2029")
    check(close(sever + sever_int, 30762.932, 0.001), "Кредитор 3 = займ материнской компании 30 500 + проценты 262,9")

    # датабук, лист Debt (IAS 17)
    wb = databook()
    ws = wb["Debt"]
    c26 = col_by_header(ws, 8, dt.datetime(2026, 6, 30), first=True)
    nd_db = ws.cell(row_by_label(ws, "Net Debt"), c26).value
    cash_db = ws.cell(row_by_label(ws, "Cash and cash equivalents"), c26).value
    tot_db = ws.cell(row_by_label(ws, "Total Debt"), c26).value
    lev_db = ws.cell(row_by_label(ws, "Net Debt/Adjusted EBITDA"), c26).value
    cash = num(H1, 6, "25 172 516")
    check(close(tot_db, gross, 1.0), f"датабук Debt: валовой долг {tot_db} = МСФО {gross:,.1f}")
    check(close(nd_db, gross - cash, 2.0), f"датабук Debt: ЧД {nd_db} = долг − касса {gross - cash:,.1f}")
    ebitda_ltm = nd_db / lev_db
    say(f"  ЧД/EBITDA {lev_db:.4f} → EBITDA LTM (IAS 17) = {ebitda_ltm:,.1f}")
    R["facts"] = {"anchor": {"net_debt": round((gross - cash) / 1000, 6), "net_debt_databook": round(nd_db / 1000, 3),
                             "cash": round(cash / 1000, 6),
                             "gross_debt": round(gross / 1000, 6), "ebitda_ltm_implied": round(ebitda_ltm / 1000, 3)},
                  "undrawn_credit_lines": round(undrawn / 1000, 3),
                  "credit_limit": round((gross + undrawn) / 1000, 3)}
    say(f"  лимит линий для A-F9 = долг {gross:,.1f} + линии {undrawn:,.1f} = {gross + undrawn:,.1f} (одна дата)")

    # ------------------------------------------------------------------ 2. корзины ставок
    say("\n## 2. Корзины ставок (доли основного долга 30.06.2026)")
    baskets = [
        # name, amount (млн), rate, until, основание ставки
        ("lenta_fixed_12m", st_fixed, {"fixed": 0.136}, "2026H2"),
        ("lenta_float_12m", st_float, {"key_plus": 0.015}, "2026H2"),
        ("lenta_fixed_2027", lt_fixed - sever, {"fixed": 0.136}, "2027H2"),
        ("severgroup_2029", sever, {"fixed": 0.100}, "2029H1"),
        ("okey_alfa_float_2031", lt_float, {"key_plus": 0.020}, "2031H2"),
        ("okey_bonds_put_2027", st_bonds, {"fixed": round((3500 * 0.155 + 3000 * 0.169) / 6500, 4)}, "2027H1"),
        ("okey_bonds_2029", lt_bonds, {"fixed": 0.18}, "2029H1"),
    ]
    check(close(sum(b[1] for b in baskets), principal, 0.001), "сумма корзин = основной долг")
    R["rate_baskets"] = []
    for name, amt, rate, until in baskets:
        share = amt / principal
        R["rate_baskets"].append({"name": name, "share": round(share, 6), "amount_bn": round(amt / 1000, 4),
                                  "rate": rate, "until": until})
        say(f"  {name:22s} {amt:10,.1f}  доля {share:.6f}  ставка {rate}  до {until}")
    resid = round(1.0 - sum(b["share"] for b in R["rate_baskets"]), 6)       # остаток округления — в крупнейшую корзину
    big = max(R["rate_baskets"], key=lambda b: b["share"])
    big["share"] = round(big["share"] + resid, 6)
    say(f"  остаток округления {resid:+.6f} → {big['name']}")
    check(abs(sum(b["share"] for b in R["rate_baskets"]) - 1.0) < 1e-9, "доли корзин в сумме ровно 1 (6 знаков)")
    # профиль погашений презентации 2 кв. 2026, с. 25: 35 / 36 / 30 % — база без периметра «О'КЕЙ»
    base_ex_okey = st_fixed + st_float + (lt_fixed - sever) + sever
    prof = [(st_fixed + st_float) / base_ex_okey, (lt_fixed - sever) / base_ex_okey, sever / base_ex_okey]
    say(f"  профиль без «О'КЕЙ» (база {base_ex_okey:,.1f}): " + " / ".join(f"{x:.1%}" for x in prof))
    check([round(x * 100) for x in prof] == [35, 36, 30], "реконструкция 35/36/30 % сходится с округлением")
    pres = page_text("Lenta_Q2-2026_Investor-Presentation_rus.pdf", 25)
    check(all(t in pres for t in ("35%", "36%", "30%", "13,6%", ">200")), "презентация 2 кв. 2026, с. 25: 35/36/30 %, 13,6 %, >200 млрд линий")

    # ставка займа Севергрупп по начисленным процентам
    say("\n  Займ Севергрупп: ставка по начисленным 262,9 млн на 30.06.2026 при разных датах выдачи")
    sev_rates = {}
    for label, d0 in (("договор 25.05", dt.date(2026, 5, 25)), ("закрытие «О'КЕЙ» 02.06", dt.date(2026, 6, 2)),
                      ("09.06", dt.date(2026, 6, 9))):
        days = (dt.date(2026, 6, 30) - d0).days
        r = sever_int / sever * 365 / days
        sev_rates[label] = round(r, 4)
        say(f"    {label:24s} {days:3d} дн. → {r:.2%}")
    for key_rate in (0.1425, 0.155):
        days = sever_int / sever * 365 / key_rate
        say(f"    при рыночной {key_rate:.2%}: выдача за {days:.0f} дн. до 30.06 (≈{dt.date(2026,6,30) - dt.timedelta(days=round(days))})")
    R["severgroup_rate_by_drawdown"] = sev_rates

    # ------------------------------------------------------------------ 3. справедливая стоимость
    say("\n## 3. Справедливая стоимость долга (прим. 32, с. 38; млн руб.)")
    fix_book = num(H1, 38, "105 442 063")
    fix_fv = num(H1, 38, "108 141 111")
    flt_book = num(H1, 38, "37 141 621")
    flt_fv = num(H1, 38, "37 151 656")
    loans_fv = num(H1, 38, "101 349 812")
    bonds_fv = num(H1, 38, "6 791 299")
    check(close(loans_fv + bonds_fv, fix_fv, 0.002), "СС фикс. = кредиты ур. 2 + облигации ур. 1")
    check(close(fix_book + flt_book, gross, 0.002), "балансовая фикс. + плав. = валовой долг")
    check(close(fix_book, fixed_loans + bonds + acc[0] + acc[1] + acc[3] + acc[4], 0.002),
          "балансовая фикс. = фикс. кредиты + облигации + их проценты")
    fv_prem = fix_fv - fix_book
    bank_prem = loans_fv - (fixed_loans + acc[0] + acc[1] + acc[4])
    bond_prem = bonds_fv - (bonds + acc[3])
    say(f"  СС − балансовая, фикс.: {fv_prem:+,.1f} (кредиты {bank_prem:+,.1f}; облигации {bond_prem:+,.1f}); "
        f"плав.: {flt_fv - flt_book:+,.1f}")
    check(fv_prem > 0, "знак по факту: СС фиксированного долга выше балансовой → требование (+)")
    R["fixed_debt_fv_premium_bn"] = round(fv_prem / 1000, 6)

    # ------------------------------------------------------------------ 4. строки моста
    say("\n## 4. Строки моста вне ЧД (млн руб.)")
    # 4.1 пут «Реми»
    put = num(H1, 33, "5 214 174")
    put0 = num(FY, 9, "4 766 102")
    unw = num(H1, 36, "448 072")
    check(close(put - put0, unw, 0.001), "прирост пута 1П2026 = «прочие» процентные расходы прим. 29 (размотка)")
    rate_half = unw / put0
    say(f"  пут «Реми»: {put0:,.1f} (31.12.2025, прочие долгоср.) → {put:,.1f} (30.06.2026, в кредиторке прим. 22)")
    say(f"  размотка за полугодие {rate_half:.4%} (год {((1 + rate_half) ** 2 - 1):.2%})")
    val = dt.date(2026, 9, 18)
    halves_val = elapsed_in_half("2026H2", val)
    put_val = put * (1 + rate_half) ** halves_val
    put_pay = put * (1 + rate_half) ** 1.0   # к началу 2027H1 (начало окна исполнения)
    say(f"  на дату оценки {val} (полугодий от 30.06: {halves_val:.5f}) — {put_val:,.1f}; "
        f"выплата в 2027H1 (наращение к началу периода) — {put_pay:,.1f}")
    # 4.2 «ОБИ»
    obi_price = num(H1, 19, "17 303 283")
    obi_cash = num(H1, 19, "4 498 910")
    obi_offset = num(H1, 19, "8 734 162")
    obi_rest = obi_price - obi_cash - obi_offset
    acq_pay = num(H1, 33, "9 119 601")
    check(obi_rest < acq_pay, "остаток «ОБИ» помещается в «кредиторку за ОС, НМА и приобретение бизнеса» 9 119,6")
    say(f"  остаток оплаты «ОБИ»: {obi_price:,.1f} − {obi_cash:,.1f} − {obi_offset:,.1f} = {obi_rest:,.1f} (погашение в 2026)")
    # 4.3 LTIP
    ltip = num(H1, 6, "2 000 523")
    other_lt_fy = num(FY, 6, "6 531 216")
    say(f"  LTIP, долгосрочная часть: {ltip:,.1f} (31.12.2025: {other_lt_fy - put0:,.1f} = {other_lt_fy:,.1f} − пут)")
    lti_exp = num(H1, 14, "1 377 954")
    say(f"  начислено долгосрочных вознаграждений 1П2026: {lti_exp:,.1f} (прим. 3)")
    # 4.4 НДУ
    nci = num(H1, 6, "991 707")
    nci_fy = num(H1, 9, "935 604")
    nci_pl = num(H1, 9, "56 103")
    nci_acq = num(FY, 9, "978 523")
    nci_loss = num(FY, 9, "42 919")
    check(close(nci_acq - nci_loss, nci_fy, 0.001), "НДУ 31.12.2025 = «Реми» при покупке 978,5 − убыток 42,9 (иных НДУ не было)")
    check(close(nci_fy + nci_pl, nci, 0.001), "НДУ 30.06.2026 = 935,6 + прибыль 56,1 (иных движений нет)")
    check("978 523" in page_numbers(FY, 48), "прим. 8 FY2025: НДУ «Реми» 978 523 в распределении цены")
    obi_ppa = num(H1, 19, "8 544 781") + num(H1, 19, "8 758 502")
    check(close(obi_ppa, obi_price, 0.001), "«ОБИ»: чистые активы + гудвил = возмещение → строки НДУ нет")
    nci_remi = nci
    nci_ex_remi = nci - nci_remi
    say(f"  НДУ баланса {nci:,.1f} = доля «Реми» {nci_remi:,.1f} + прочие {nci_ex_remi:,.1f}")
    # 4.5 займы выданные
    loans = num(H1, 6, "3 339 890")
    loans_fy = num(FY, 6, "11 533 698")
    assign = num(H1, 14, "11 875 152")
    rp_int = num(H1, 14, "341 454")
    int_loans = num(H1, 37, "579 220")
    check(close(loans_fy + rp_int, assign, 0.001), "займ под общим контролем 11 533,7 + проценты 341,5 = переуступлено 11 875,2")
    resid = assign - obi_offset
    tail = loans - resid
    say(f"  реконструкция: переуступка {assign:,.1f} − зачёт «ОБИ» {obi_offset:,.1f} = {resid:,.1f}; "
        f"остаток 30.06 {loans:,.1f} → проценты {tail:,.1f}")
    other_int = int_loans - rp_int - tail
    say(f"  процентный доход по займам 1П2026 {int_loans:,.1f} = связанные {rp_int:,.1f} + остаток {tail:,.1f} + "
        f"прочие {other_int:,.1f} (займ продавцу «О'КЕЙ» 8 419,7 до зачёта 02.06)")
    check(0 < other_int < 100, "невязка процентного дохода мала и положительна (займ продавцу «О'КЕЙ» на ≈2–4 недели)")
    for label, d0 in (("с 15.02", dt.date(2026, 2, 15)), ("с 31.03", dt.date(2026, 3, 31))):
        days = (dt.date(2026, 6, 30) - d0).days
        say(f"    ставка остатка {label}: {tail / resid * 365 / days:.1%}")
    # 4.6 компенсирующий актив, ограниченные средства
    comp = num(H1, 29, "2 471 493")
    check("8 191 000" in page_numbers(H1, 29), "прим. 17: аккредитив «Реми» 8 191 000 на 31.12.2025, на 30.06.2026 — «–»")
    check("2 471 493" in page_numbers(FY, 48) and "2 471 493" in page_numbers(FY, 62),
          "прим. 8/20 FY2025: компенсирующий актив 2 471 493 признан при покупке «Реми»")
    bs = wb["BS"]
    c_bs = col_by_header(bs, 8, "1H 2026", first=False)
    rc_db = bs.cell(row_by_label(bs, "Restricted cash"), c_bs).value
    check(close(rc_db / 1000, comp, 0.001), f"датабук BS «Restricted cash» {rc_db} = компенсирующий актив прим. 17 (ярлык датабука ошибочен)")
    factoring = num(H1, 33, "3 019 428")
    other_nca = num(H1, 6, "624 347")

    items = [
        {"id": "remi_put", "kind": "claim", "amount": put, "accrete_rate_half": rate_half, "settle_period": "2027H1", "haircut": 0.0},
        {"id": "obi_remaining", "kind": "claim", "amount": obi_rest, "accrete_rate_half": 0.0, "settle_period": "2026H2", "haircut": 0.0},
        {"id": "ltip_long_term", "kind": "claim", "amount": ltip, "accrete_rate_half": 0.0, "settle_period": "2028H1", "haircut": 0.0},
        {"id": "nci_ex_remi", "kind": "claim", "amount": nci_ex_remi, "accrete_rate_half": 0.0, "settle_period": None, "haircut": 0.0},
        {"id": "loans_issued", "kind": "asset", "amount": loans, "accrete_rate_half": 0.0, "settle_period": "2026H2", "haircut": 0.0},
        {"id": "fixed_debt_fv_premium", "kind": "claim", "amount": fv_prem, "accrete_rate_half": 0.0, "settle_period": None, "haircut": 0.0},
        {"id": "remi_indemnification_asset", "kind": "asset", "amount": comp, "accrete_rate_half": 0.0, "settle_period": None, "haircut": 1.0},
    ]
    say("\n  Строки bridge.items (на 30.06.2026 и на дату оценки 18.09.2026, млрд руб.):")
    tot_asof = tot_val = 0.0
    for it in items:
        sign = 1 if it["kind"] == "claim" else -1
        a = it["amount"] * (1 - it["haircut"]) * sign
        v = a * (1 + it["accrete_rate_half"]) ** halves_val
        tot_asof += a
        tot_val += v
        say(f"    {it['id']:28s} {it['kind']:5s} {it['amount'] / 1000:8.4f}  haircut {it['haircut']:.2f}  "
            f"в требованиях {a / 1000:+8.4f} → {v / 1000:+8.4f}")
    say(f"    итого нетто: {tot_asof / 1000:+.3f} (30.06) → {tot_val / 1000:+.3f} (18.09)")
    rub_per_bn = 1000 / 115.074675 * (1 - 0.10)
    say(f"    правило большого пальца: 1 млрд требований ≈ {rub_per_bn:.2f} ₽/акц. при g 10 % → нетто ≈ {tot_val / 1000 * rub_per_bn:.0f} ₽")
    R["bridge_items"] = [{**it, "amount": round(it["amount"] / 1000, 6),
                          "accrete_rate_half": round(it["accrete_rate_half"], 6)} for it in items]
    R["bridge_totals_bn"] = {"net_claims_asof": round(tot_asof / 1000, 4), "net_claims_valuation": round(tot_val / 1000, 4),
                             "halves_asof_to_valuation": round(halves_val, 6), "remi_put_at_valuation": round(put_val / 1000, 4),
                             "remi_put_payment_2027H1": round(put_pay / 1000, 4)}
    R["not_in_bridge"] = {"supplier_finance_payables_bn": round(factoring / 1000, 4),
                          "other_noncurrent_fin_assets_bn": round(other_nca / 1000, 4),
                          "restricted_cash_30_06_2026_bn": 0.0}

    # ровно один раз: ЧД не содержит строк моста
    say("\n  Проверка «ровно один раз»: строки моста вне ЧД и вне долга")
    check(close(nd_db, gross - cash, 2.0), "ЧД = кредиты, займы и облигации (с процентами) − денежные средства; пут, «ОБИ», LTIP, займы выданные, компенсирующий актив не входят")
    tp = num(H1, 33, "157 281 140")
    check(put < tp and obi_rest < tp, "пут и остаток «ОБИ» — внутри торговой и прочей кредиторки 157 281,1 → лист ОК обязан их исключить")

    # ------------------------------------------------------------------ 4б. ориентиры спредов
    say("\n## 4б. Ориентиры спредов: прошлые облигации «Ленты» и рынок облигаций «О'КЕЙ»")
    # ключевая ЦБ на даты размещения (https://www.cbr.ru/hd_base/KeyRate/): 02.05.2017 9,25; 17.12.2018 7,75; 28.10.2019 6,5; 27.04.2020 5,5
    key_at = {"2017-05-30": 0.0925, "2019-03-27": 0.0775, "2019-11-14": 0.065, "2020-06-03": 0.055}
    hist_sp = {}
    for isin in ("RU000A0JXRX3", "RU000A100782", "RU000A1011A7", "RU000A101R33"):
        b = json.loads((PRIMARY / f"moex_iss_lenta_bonds/{isin}_bondization.json").read_text(encoding="utf-8"))["coupons"]
        cols, row = b["columns"], b["data"][0]
        start, cpn, name = row[cols.index("startdate")], row[cols.index("valueprc")] / 100, row[cols.index("name")]
        hist_sp[name] = round(cpn - key_at[start], 4)
        say(f"  {name}: размещение {start}, купон {cpn:.2%}, ключевая {key_at[start]:.2%} → {cpn - key_at[start]:+.2%}")
    md = json.loads((PRIMARY / "moex_iss_okey_bonds/marketdata_okey_traded_2026-09-28.json").read_text(encoding="utf-8"))["marketdata_yields"]
    okey_g = {}
    for r in md["data"]:
        c = md["columns"]
        okey_g[r[c.index("SECID")]] = {"gspread_bp": r[c.index("GSPREADBP")], "zspread_bp": r[c.index("ZSPREADBP")], "duration_d": r[c.index("DURATION")]}
        say(f"  «О'КЕЙ» {r[c.index('SECID')]}: G-спред {r[c.index('GSPREADBP')]} б.п., Z-спред {r[c.index('ZSPREADBP')]} б.п., дюрация {r[c.index('DURATION')]} дн.")
    check(min(v["gspread_bp"] for v in okey_g.values()) >= 150, "облигации «О'КЕЙ» — верхняя граница спредов группы (G-спред ≥ 1,5 п.п.)")
    R["spread_evidence"] = {"lenta_bonds_coupon_minus_key": hist_sp, "okey_bonds_2026_09_28": okey_g}

    # ------------------------------------------------------------------ 5. доходность кассы
    say("\n## 5. Доходность кассы k = проценты по депозитам (год.) / средняя касса / средняя ключевая")
    dep = {"1H2025": num(H1, 37, "2 408 235"), "FY2025": num(FY, 71, "4 044 363"), "1H2026": num(H1, 37, "1 703 750")}
    cash_pts = {"2024-12-31": num(FY, 62, "47 032 323"), "2025-06-30": num(H1, 8, "30 935 770"),
                "2025-12-31": num(FY, 62, "51 063 533"), "2026-06-30": cash}
    # ключевая ставка ЦБ (https://www.cbr.ru/hd_base/KeyRate/; совпадает с research/07 §1.1): дата вступления → ставка
    key_path = [(dt.date(2024, 10, 28), 0.21), (dt.date(2025, 6, 9), 0.20), (dt.date(2025, 7, 28), 0.18),
                (dt.date(2025, 9, 15), 0.17), (dt.date(2025, 10, 27), 0.165), (dt.date(2025, 12, 22), 0.16),
                (dt.date(2026, 2, 16), 0.155), (dt.date(2026, 3, 23), 0.15), (dt.date(2026, 4, 27), 0.145),
                (dt.date(2026, 6, 22), 0.1425), (dt.date(2026, 7, 27), 0.14)]

    def key_avg(a: dt.date, b: dt.date) -> float:
        tot, d = 0.0, a
        while d <= b:
            tot += [r for s, r in key_path if s <= d][-1]
            d += dt.timedelta(days=1)
        return tot / ((b - a).days + 1)

    halves = {"1H2025": (dt.date(2025, 1, 1), dt.date(2025, 6, 30), dep["1H2025"], "2024-12-31", "2025-06-30"),
              "2H2025": (dt.date(2025, 7, 1), dt.date(2025, 12, 31), dep["FY2025"] - dep["1H2025"], "2025-06-30", "2025-12-31"),
              "1H2026": (dt.date(2026, 1, 1), dt.date(2026, 6, 30), dep["1H2026"], "2025-12-31", "2026-06-30")}
    ks = {}
    for h, (a, b, inc, c0, c1) in halves.items():
        days = (b - a).days + 1
        y = inc * 365 / days / ((cash_pts[c0] + cash_pts[c1]) / 2)
        kr = key_avg(a, b)
        ks[h] = y / kr
        say(f"  {h}: доход {inc:,.1f}, доходность {y:.2%}, ключевая {kr:.2%} → k = {ks[h]:.3f}")
    check(close(key_avg(dt.date(2026, 1, 1), dt.date(2026, 6, 30)), 0.1516, 0.0005), "средняя ключевая 1П2026 = 15,16 % (research/07)")
    k_mean = sum(ks.values()) / len(ks)
    say(f"  среднее k = {k_mean:.3f} (диапазон {min(ks.values()):.3f}–{max(ks.values()):.3f})")
    R["cash_yield_k"] = {"by_half": {h: round(v, 4) for h, v in ks.items()}, "mean": round(k_mean, 4)}

    # касса сверх операционной на сезонном минимуме (30.06.2026)
    dep_0626 = num(H1, 28, "19 457 779")
    opcash_0626 = cash - dep_0626
    wsq = wb["Financials quarterly"]
    hdr = {wsq.cell(8, c).value: c for c in range(2, 28)}
    sales_row = row_by_label(wsq, "Total Sales")
    ltm_sales = sum(wsq.cell(sales_row, hdr[q]).value for q in ("3Q 2025", "4Q 2025", "1Q 2026", "2Q 2026"))
    say(f"  30.06.2026: касса {cash:,.1f} = депозиты {dep_0626:,.1f} + касса/в пути/счета {opcash_0626:,.1f}; "
        f"выручка LTM (отчётная) {ltm_sales:,.0f} → депозиты {dep_0626 / ltm_sales:.2%} выручки, операционная {opcash_0626 / ltm_sales:.2%}")
    R["treasury_cash_share_ltm_revenue"] = round(dep_0626 / ltm_sales, 4)

    # ------------------------------------------------------------------ 6. перекат ЧД на дату оценки
    say("\n## 6. Перекат ЧД 30.06 → 18.09.2026: линейно по дням против квартальных весов ОК")
    rows = {k: row_by_label(wsq, k) for k in ("Movements in Working Capital", "Net Cash generated from Operating Activities",
                                              "Net cash used in Investing Activities")}
    rows["EBITDA"] = row_by_label(wsq, "EBITDA", exact=True)     # не «EBITDAR»

    def q(label: str, quarter: str) -> float:
        return wsq.cell(rows[label], hdr[quarter]).value

    dnwc = {qq: q("Movements in Working Capital", qq) for qq in
            ("1Q 2024", "2Q 2024", "3Q 2024", "4Q 2024", "1Q 2025", "2Q 2025", "3Q 2025", "4Q 2025", "1Q 2026", "2Q 2026")}
    check(close(dnwc["4Q 2025"], 24952, 0.5) and close(dnwc["1Q 2026"], -20105, 0.5), "датабук: ΔОК 4 кв. 2025 +24 952, 1 кв. 2026 −20 105")
    s3 = {y: dnwc[f"3Q {y}"] / (dnwc[f"3Q {y}"] + dnwc[f"4Q {y}"]) for y in (2024, 2025)}
    s1 = {y: dnwc[f"1Q {y}"] / (dnwc[f"1Q {y}"] + dnwc[f"2Q {y}"]) for y in (2024, 2025, 2026)}
    s3_pool = sum(dnwc[f"3Q {y}"] for y in (2024, 2025)) / sum(dnwc[f"3Q {y}"] + dnwc[f"4Q {y}"] for y in (2024, 2025))
    s1_pool = sum(dnwc[f"1Q {y}"] for y in (2024, 2025, 2026)) / sum(dnwc[f"1Q {y}"] + dnwc[f"2Q {y}"] for y in (2024, 2025, 2026))
    for y, v in s3.items():
        say(f"  {y}: ΔОК 3 кв. {dnwc[f'3Q {y}']:+,} / 4 кв. {dnwc[f'4Q {y}']:+,} → доля 3 кв. в 2П {v:+.3f}")
    for y, v in s1.items():
        say(f"  {y}: ΔОК 1 кв. {dnwc[f'1Q {y}']:+,} / 2 кв. {dnwc[f'2Q {y}']:+,} → доля 1 кв. в 1П {v:+.3f}")
    say(f"  пул: 1 кв. {s1_pool:.3f}, 2 кв. {1 - s1_pool:.3f}; 3 кв. {s3_pool:.3f}, 4 кв. {1 - s3_pool:.3f}")
    # весь поток: ΔЧД ≈ −(ДДС операц. + инвест.), IAS 17
    for y in (2024, 2025):
        f3 = q("Net Cash generated from Operating Activities", f"3Q {y}") + q("Net cash used in Investing Activities", f"3Q {y}")
        f4 = q("Net Cash generated from Operating Activities", f"4Q {y}") + q("Net cash used in Investing Activities", f"4Q {y}")
        say(f"  {y}: поток до долга 3 кв. {f3:+,} / 4 кв. {f4:+,} → доля 3 кв. в 2П {f3 / (f3 + f4):+.3f}")
    e_share = [q("EBITDA", f"3Q {y}") / (q("EBITDA", f"3Q {y}") + q("EBITDA", f"4Q {y}")) for y in (2024, 2025)]
    say(f"  доля 3 кв. в EBITDA 2П: {e_share[0]:.3f} (2024), {e_share[1]:.3f} (2025) — против 0,5 по дням")
    lin = elapsed_in_half("2026H2", val)
    q3_start = dt.date(2026, 7, 1)
    w_q = s3_pool * (val - q3_start).days / ((dt.date(2026, 9, 30) - q3_start).days + 1)
    say(f"  вес на 18.09: линейно {lin:.4f}; ОК по кварталам {w_q:+.4f}")
    ill = {}
    for r_u in (0.15, 0.185, 0.20):
        def val_share(w: float) -> float:
            return w + (1 - w) * (1 + r_u) ** (-(1 - w) * 0.25)
        for X in (20000.0, 25000.0, 30000.0):
            d_nd = (lin - w_q) * X
            d_val = (val_share(lin) - val_share(w_q)) * X
            ill[f"r{r_u}_X{int(X / 1000)}"] = {"nd_diff_bn": round(d_nd / 1000, 2), "value_diff_bn": round(d_val / 1000, 3),
                                                "rub_per_share": round(d_val / 1000 * rub_per_bn, 1)}
            if r_u == 0.185:
                say(f"    ΔОК 2П2026 = {X / 1000:.0f} млрд: ЧД на 18.09 ниже на {d_nd / 1000:.1f} млрд при линейном перекате; "
                    f"стоимость выше на {d_val / 1000:.2f} млрд ≈ {d_val / 1000 * rub_per_bn:.1f} ₽ (r = {r_u:.1%})")
    R["rollforward"] = {"elapsed_linear": round(lin, 6), "w_nwc_quarterly": round(w_q, 6),
                        "nwc_quarter_share_proposed": {"Q1": round(s1_pool, 3), "Q2": round(1 - s1_pool, 3),
                                                       "Q3": round(s3_pool, 3), "Q4": round(1 - s3_pool, 3)},
                        "q3_share_by_year": {str(k): round(v, 4) for k, v in s3.items()},
                        "q1_share_by_year": {str(k): round(v, 4) for k, v in s1.items()},
                        "illustration": ill}

    # ------------------------------------------------------------------ 7. дивиденды, рычаг, триггеры
    say("\n## 7. Дивидендная лестница, целевой рычаг, триггеры")
    dp4 = page_text("lenta_dividend_policy_2021_ru.pdf", 4)
    dp5 = page_text("lenta_dividend_policy_2021_ru.pdf", 5)
    check("1.5х" in dp4 and "100%" in dp4 and "IAS 17" in dp4, "положение 2021, п. 3.1(c): ≤100 % FCF при ЧД/EBITDA (IAS 17) < 1,5х")
    check("1.0х" in dp4 and "превыси" in dp4.replace(" ", ""), "п. 3.1(d): при < 1,0х выплата может превысить 100 % FCF")
    check("50%" in dp5 and "1.5х" in dp5, "п. 3.1(e): при > 1,5х — не выше 50 % FCF до возврата к 1,5х")
    sp = page_text("Lenta_strategy-presentation-2028.pdf", 34)
    check(all(t in sp for t in ("1,0x", "1,5x", ">100%", "max 100%", "max 50%", "IAS 17")), "стратегия-2028, с. 34: та же лестница")
    pres7 = page_text("Lenta_Q2-2026_Investor-Presentation_rus.pdf", 7)
    check("1,0-1,5х" in pres7 and "2,0х" in pres7, "презентация 2 кв. 2026, с. 7: ЧД/EBITDA 1,0–1,5х, при M&A ≤ 2,0х")
    okey = (PRIMARY / "lentagroup_news/2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.txt").read_text(encoding="utf-8")
    check("на уровне 1,0х" in okey, "релиз 02.06.2026: ЧД/EBITDA по итогам 2026 — «в нижней границе … на уровне 1,0х»")
    R["dividend_ladder"] = [{"max_leverage": 1.0, "payout_max": None}, {"max_leverage": 1.5, "payout_max": 1.0},
                            {"max_leverage": None, "payout_max": 0.5}]
    lev_row = row_by_label(ws, "Net Debt/Adjusted EBITDA")
    hist = {ws.cell(8, c).value: ws.cell(lev_row, c).value for c in range(2, c26 + 1)}
    hist = {(k.year if isinstance(k, dt.datetime) else k): round(v, 2) for k, v in hist.items() if v}
    say(f"  ЧД/EBITDA IAS 17 по годам: {hist}")
    check(max(v for k, v in hist.items() if k >= 2014) < 3.5, "исторический максимум ЧД/EBITDA < 3,5 → порог неустойчивости 4,0 вне истории")
    acra = (PRIMARY / "ratings/acra_pr6824_Lenta_2026-04-28.txt").read_text(encoding="utf-8").replace(" ", " ")
    check("выше 2,0х и 4,0х" in acra and "1,3х" in acra and "3,1х" in acra, "АКРА 28.04.2026: триггеры 2,0х / 4,0х; 1,3х за 2025; 3,1х")
    # перевод триггера АКРА 2,0х (общий долг без аренды / FFO) в ЧД/EBITDA IAS 17
    ebitda25 = sum(q("EBITDA", f"{i}Q 2025") for i in (1, 2, 3, 4))
    debt25 = 99899.296
    ffo_lo, ffo_hi = debt25 / 1.35, debt25 / 1.25      # «1,3х» с округлением до 0,1
    equiv = []
    for ffo in (ffo_lo, ffo_hi):
        g = 2.0 * ffo
        for cash_ratio in (cash / ebitda_ltm, 51063.5 / ebitda25):   # касса: сезонный минимум 30.06 и пик 31.12
            equiv.append(g / ebitda25 - cash_ratio)
            say(f"    FFO {ffo:,.0f}: долг при 2,0х = {g:,.0f} = {g / ebitda25:.2f}× EBITDA; ЧД/EBITDA ≈ {g / ebitda25 - cash_ratio:.2f} (касса {cash_ratio:.2f}×)")
    R["acra_trigger_nd_ebitda_equiv"] = [round(min(equiv), 2), round(max(equiv), 2)]
    R["ebitda_2025_ias17"] = round(ebitda25 / 1000, 3)
    say(f"  EBITDA 2025 (IAS 17, сумма кварталов) = {ebitda25:,.0f}; триггер АКРА ≈ ЧД/EBITDA {R['acra_trigger_nd_ebitda_equiv']}")

    # ------------------------------------------------------------------ 8. цена ошибки (до ядра)
    say("\n## 8. Цена ошибки (правила большого пальца до ядра)")
    cost = {
        "loans_issued_haircut_0.5": -loans * 0.5 / 1000 * rub_per_bn,
        "ltip_as_operating_float": ltip / 1000 * rub_per_bn,
        "nci_domlenta_10pct_upper": -0.9 * rub_per_bn,
        "indemnification_asset_counted": comp / 1000 * rub_per_bn,
        "fv_premium_dropped": fv_prem / 1000 * rub_per_bn,
        "fv_premium_runoff_to_2026_12_31": 0.6 * rub_per_bn,
        "supplier_finance_as_debt": -factoring / 1000 * rub_per_bn,
    }
    # рычаг: грубая оценка терминального щита при +0,5× (EBITDA 2036 ≈ 190 млрд, миры N/M)
    lev_cost = {}
    for world, (r_d, r, g, disc) in {"N": (0.1145, 0.129, 0.045, 0.129), "M": (0.1806, 0.195, 0.10, 0.195)}.items():
        shield = 0.25 * 0.5 * 190.0 * r_d
        tv = shield / (r - g) * (1 + disc) ** -10.25
        explicit = 0.25 * 0.5 * 140.0 * (r_d * 0.8) * 4.6 * (1 + disc) ** -1.5
        lev_cost[world] = round((tv + explicit) * rub_per_bn, 0)
    cost["leverage_target_1.5_vs_1.0_N"] = lev_cost["N"]
    cost["leverage_target_1.5_vs_1.0_M"] = lev_cost["M"]
    for k, v in cost.items():
        say(f"  {k:36s} {v:+7.1f} ₽/акц.")
    R["cost_of_error_rub_per_share"] = {k: round(v, 1) for k, v in cost.items()}
    R["rub_per_bn_claims"] = round(rub_per_bn, 3)

    # ------------------------------------------------------------------ 9. фрагмент книги
    if want_fragment:
        say("\n## 9. Сверка фрагмента fragments/financing-bridge.yaml с расчётом листа")
        import yaml
        fr = yaml.safe_load(FRAGMENT.read_text(encoding="utf-8"))
        fb = fr["financing"]["rate_baskets"]
        check([b["name"] for b in fb] == [b["name"] for b in R["rate_baskets"]], "имена и порядок корзин")
        for b, rb in zip(fb, R["rate_baskets"]):
            check(close(b["share"], rb["share"], 1e-6) and b["rate"] == rb["rate"] and b["until"] == rb["until"],
                  f"корзина {b['name']}: доля/ставка/срок")
        check(abs(sum(b["share"] for b in fb) - 1) < 1e-9, "доли фрагмента в сумме ровно 1")
        check(fr["financing"]["dividend_ladder"] == R["dividend_ladder"], "лестница = положение 2021")
        check(close(fr["facts"]["undrawn_credit_lines"], R["facts"]["undrawn_credit_lines"], 1e-9), "неиспользованные линии")
        check(close(fr["facts"]["anchor"]["net_debt"], R["facts"]["anchor"]["net_debt"], 1e-9), "ЧД якоря (МСФО: долг − касса)")
        check(close(fr["facts"]["anchor"]["cash"], R["facts"]["anchor"]["cash"], 1e-9), "касса якоря")
        items_fr = {i["id"]: i for i in fr["bridge"]["items"]}
        check(set(items_fr) == {i["id"] for i in R["bridge_items"]}, "набор строк моста")
        for it in R["bridge_items"]:
            f = items_fr[it["id"]]
            check(f["kind"] == it["kind"] and close(f["amount"], it["amount"], 1e-6)
                  and close(f.get("accrete_rate_half") or 0.0, it["accrete_rate_half"], 1e-6)
                  and f.get("settle_period") == it["settle_period"] and close(f.get("haircut") or 0.0, it["haircut"], 1e-9),
                  f"строка {it['id']}")
        check(fr["meta"]["bridge_as_of"] == "2026-06-30", "meta.bridge_as_of = 2026-06-30")
        check(close(fr["financing"]["cash_yield_k"], round(R["cash_yield_k"]["mean"], 2), 0.005), "cash_yield_k = среднее k листа (2 знака)")

    OUT.mkdir(exist_ok=True)
    R["checks_failed"] = FAIL
    (OUT / "financing_bridge_out.json").write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    say(f"\nПроверок провалено: {len(FAIL)}")
    (OUT / "financing_bridge_out.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
