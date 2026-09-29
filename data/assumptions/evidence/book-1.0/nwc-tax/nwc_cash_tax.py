"""Лист nwc-tax книги 1.0 Ленты: оборотный капитал (A-W), операционная касса (A-F2/A-F3), налог (A-T).

Запуск:  PYTHONIOENCODING=utf-8 python nwc_cash_tax.py
Первичка: каталог LENTA_PRIMARY_DIR (см. primary.py). Выход: nwc_cash_tax_out.json и nwc_cash_tax_out.txt рядом.

Метки: [ф] — факт первички (файл, лист/страница); [р] — расчёт этим скриптом; [с] — суждение (задано ниже
константой с диапазоном и основанием). Все суммы — млрд ₽, если не сказано иное.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import primary as P

OUT_JSON = Path(__file__).with_name("nwc_cash_tax_out.json")
OUT_TXT = Path(__file__).with_name("nwc_cash_tax_out.txt")
LOG: list[str] = []
RES: dict = {}


def log(s: str = "") -> None:
    LOG.append(s)
    print(s)


def bn(x: float | None) -> float | None:
    return None if x is None else x / 1e6  # тыс. ₽ → млрд ₽


def r4(x):
    return None if x is None else round(x, 4)


# =====================================================================================
# 0. Суждения и справочные константы (единственное место чисел, не прочитанных из файлов)
# =====================================================================================
SHARES_MLN = 115.074675            # D8: акции в обращении (МСФО прим. 18/21)
GOV_G = 0.10                       # правило большого пальца задания: 1 млрд ₽ требований ≈ 7,8 ₽/акц при g≈10 %
RUB_PER_BN = (1 - GOV_G) * 1000 / SHARES_MLN     # ₽ на акцию за 1 млрд ₽ требований на дату оценки
R_DISC = 0.16                      # [с] номинальная ставка для грубой PV цены ошибки (r_u мира H ≈ 15–17 %)
TAX = 0.25                         # НК РФ ст. 284 п. 1 (25 % с 01.01.2025)

# Ключевая ставка ЦБ, средняя по полугодиям, % — [ф] cbr.ru/hd_base/KeyRate (выгрузка в
# research/_work07/cbr_keyrate_2024-2026.html; таблица решений — research/07 §1.1); средние считаются здесь
KEY_DECISIONS = [  # (дата вступления в силу, ставка %)
    ("2023-12-18", 16.00), ("2024-07-29", 18.00), ("2024-09-16", 19.00), ("2024-10-28", 21.00),
    ("2025-06-09", 20.00), ("2025-07-28", 18.00), ("2025-09-15", 17.00), ("2025-10-27", 16.50),
    ("2025-12-22", 16.00), ("2026-02-16", 15.50), ("2026-03-23", 15.00), ("2026-04-27", 14.50),
    ("2026-06-22", 14.25), ("2026-07-27", 14.00),
]

# Выручка приобретённых сетей до консолидации (для LTM-проформы на отчётных датах), млрд ₽.
# Факты — со страницей и проверкой, что число есть на странице; расчёты — с правилом.
PF_FACTS = {
    # (файл, страница, строка-число в тексте) → значение в тыс. ₽
    "monetka_pf_group_2023": ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", 49, "767 587 524"),
    "ulybka_pf_2024": ("ifrs_mkpao/ifrs_FY2024.pdf", 44, "41 566 662"),
    "ulybka_contrib_2024": ("ifrs_mkpao/ifrs_FY2024.pdf", 44, "4 709 172"),
    "molnia_pf_2025": ("ifrs_mkpao/ifrs_FY2025.pdf", 47, "19 796 109"),
    "molnia_contrib_2025": ("ifrs_mkpao/ifrs_FY2025.pdf", 47, "9 643 909"),
    "remi_pf_2025": ("ifrs_mkpao/ifrs_FY2025.pdf", 49, "55 488 000"),
    "remi_contrib_2025": ("ifrs_mkpao/ifrs_FY2025.pdf", 49, "5 100 086"),
    "billa_contrib_2021": ("ifrs_mkpao/ifrs_FY2021.pdf", 48, "9,501,250"),
    "semya_contrib_2021": ("ifrs_mkpao/ifrs_FY2021.pdf", 49, "4,159,083"),
    "okey_pf_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 18, "66 120 725"),
    "okey_contrib_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 18, "9 359 828"),
    "okey_pbt_pf_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 18, "5 530 179"),
    "okey_pbt_contrib_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 18, "907 372"),
    "domlenta_rev_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 19, "7 960 016"),
    "domlenta_pbt_2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 19, "2 963 756"),
}
OKEY_REV_2025 = 142.0     # [ф] пресс-релиз Ленты 02.06.2026 (corp_docs/lentagroup_news_2026-06-02_okey.txt): «142 млрд рублей в 2025 году»
# [р/с] доля 2П в году гипермаркетов Ленты 2025 — из датабука (Financials quarterly), применяется к «О'КЕЙ»
# [с] «Дом Лента» до консолидации: 2П2025 ≈ 2 × выручка 2 кв. 2026 (5,9, датабук), 1П2026 вне отчёта ≈ 2×5,9 − 7,96; se ±4
DOMLENTA_Q2_2026_MULT_H = 2.0


def check_pf_facts() -> dict[str, float]:
    out = {}
    for k, (rel, page, s) in PF_FACTS.items():
        t = P.pdf_pages(rel)[page - 1]
        norm = t.replace(" ", " ")
        if s not in norm:
            raise AssertionError(f"{k}: «{s}» не найдено на с. {page} {rel}")
        out[k] = float(s.replace(" ", "").replace(",", "")) / 1e6
    return out


def key_avg(d0: str, d1: str) -> float:
    """Средняя ключевая за [d0, d1) по календарным дням, %."""
    from datetime import date
    def D(s): return date.fromisoformat(s)
    a, b = D(d0), D(d1)
    total = 0.0
    for i, (ds, rate) in enumerate(KEY_DECISIONS):
        s = D(ds)
        e = D(KEY_DECISIONS[i + 1][0]) if i + 1 < len(KEY_DECISIONS) else date(2100, 1, 1)
        lo, hi = max(a, s), min(b, e)
        if hi > lo:
            total += rate * (hi - lo).days
    return total / (b - a).days


# =====================================================================================
# 1. Выручка по полугодиям и LTM (IAS 17, датабук PL), проформа
# =====================================================================================
PL = P.Databook("PL")
BS = P.Databook("BS")
CF = P.Databook("CF")
FQ = P.Databook("Financials quarterly")

YEARS = list(range(2019, 2026 + 1))


def rev_half(year: int, half: int) -> float:
    h1 = bn(PL.get("Sales", "IAS 17", f"1H {year}"))
    if half == 1:
        return h1
    return bn(PL.get("Sales", "IAS 17", f"FY {year}")) - h1


def cogs_half(year: int, half: int) -> float:
    h1 = -bn(PL.get("Cost of sales", "IAS 17", f"1H {year}"))
    if half == 1:
        return h1
    return -bn(PL.get("Cost of sales", "IAS 17", f"FY {year}")) - h1


PF = check_pf_facts()
log("== 1. Выручка и проформа (млрд ₽) ==")
q_hyper = {q: bn(FQ.get("Hypermarkets", "IAS 17", f"{q}Q 2025")) * 1e3 for q in (1, 2, 3, 4)}  # лист в млн ₽
hyper_h2_share_2025 = (q_hyper[3] + q_hyper[4]) / sum(q_hyper.values())
okey_2025h2 = OKEY_REV_2025 * hyper_h2_share_2025
okey_2026_pre = PF["okey_pf_2026"] - PF["okey_contrib_2026"]
remi_pre_2025 = PF["remi_pf_2025"] - PF["remi_contrib_2025"]            # январь–ноябрь 2025
remi_jul_nov_2025 = remi_pre_2025 * 5 / 11                              # [р] равномерно по месяцам
molnia_pre_2025 = PF["molnia_pf_2025"] - PF["molnia_contrib_2025"]      # январь–июнь 2025
ulybka_pre_2024 = PF["ulybka_pf_2024"] - PF["ulybka_contrib_2024"]      # январь–ноябрь 2024
monetka_pre_2023 = PF["monetka_pf_group_2023"] - rev_half(2023, 1) - rev_half(2023, 2)  # январь–сентябрь 2023
billa_pre_2021 = PF["billa_contrib_2021"] * 7 / 5                        # [р] вклад за ≈5 мес. → 7 мес. до покупки
semya_pre_2021 = PF["semya_contrib_2021"] * 8 / 4                        # [р] ≈4 мес. → 8 мес.
dl_q2 = bn(FQ.get("Dom Lenta", "IAS 17", "2Q 2026")) * 1e3
domlenta_2025h2 = DOMLENTA_Q2_2026_MULT_H * dl_q2
domlenta_2026_pre = max(0.0, DOMLENTA_Q2_2026_MULT_H * dl_q2 - PF["domlenta_rev_2026"])

LTM_REP, LTM_PF, PF_NOTE = {}, {}, {}
for y in range(2020, 2027):
    # декабрь
    if y <= 2025:
        LTM_REP[f"FY {y}"] = rev_half(y, 1) + rev_half(y, 2)
    LTM_REP[f"1H {y}"] = rev_half(y - 1, 2) + rev_half(y, 1)
add = {
    "FY 2021": (billa_pre_2021 + semya_pre_2021, "Billa ×7/5 + «Семья» ×8/4 (вклад с даты покупки, расчёт)"),
    "FY 2023": (monetka_pre_2023, "проформа группы 2023 767,6 (КФО 2023 прим. 8, с. 49) − отчёт"),
    "1H 2024": (monetka_pre_2023 / 3, "«Монетка» 3 кв. 2023 = треть январь–сентябрь (расчёт)"),
    "FY 2024": (ulybka_pre_2024, "«Улыбка» 41,57 − 4,71 (КФО 2024 прим. 8, с. 44)"),
    "1H 2025": (ulybka_pre_2024 * 5 / 11 + PF["molnia_pf_2025"] * 51 / 52,
                "«Улыбка» июль–ноябрь 2024 + «Молния» год (с 24.06.2025 в балансе)"),
    "FY 2025": (remi_pre_2025 + molnia_pre_2025, "«Реми» январь–ноябрь (55,49 − 5,10) + «Молния» январь–июнь (19,80 − 9,64)"),
    "1H 2026": (okey_2025h2 + okey_2026_pre + remi_jul_nov_2025 + domlenta_2025h2 + domlenta_2026_pre,
                "«О'КЕЙ» 2П2025 (142 × доля 2П гипермаркетов) + январь–май 2026 (66,12 − 9,36) + «Реми» июль–ноябрь 2025 "
                "+ «Дом Лента» 2П2025 и 1П2026 вне отчёта (2 × 2 кв. 2026)"),
}
for k, v in LTM_REP.items():
    a, note = add.get(k, (0.0, ""))
    LTM_PF[k] = v + a
    PF_NOTE[k] = note
for k in sorted(LTM_REP, key=lambda s: (int(s[-4:]), s[:2] != "1H")):
    log(f"  {k}: LTM отчёт {LTM_REP[k]:8.1f}  проформа {LTM_PF[k]:8.1f}  {PF_NOTE[k]}")
log(f"  доля 2П гипермаркетов 2025: {hyper_h2_share_2025:.4f}; «О'КЕЙ» 2П2025 {okey_2025h2:.2f}; январь–май 2026 {okey_2026_pre:.2f}")
log(f"  «Реми» июль–ноябрь 2025 {remi_jul_nov_2025:.2f}; «Дом Лента» 2П2025 {domlenta_2025h2:.2f}, 1П2026 вне отчёта {domlenta_2026_pre:.2f}")
R_LTM_PF_2026 = LTM_PF["1H 2026"]
H1_2026_PF = rev_half(2026, 1) + okey_2026_pre
log(f"  проформа 1П2026 (только «О'КЕЙ», как D3): {H1_2026_PF:.2f}; LTM-проформа на 30.06.2026: {R_LTM_PF_2026:.1f}")
RES["revenue"] = {"ltm_reported": {k: r4(v) for k, v in LTM_REP.items()},
                  "ltm_pro_forma": {k: r4(v) for k, v in LTM_PF.items()},
                  "pro_forma_notes": PF_NOTE, "h1_2026_pro_forma_okey_only": r4(H1_2026_PF),
                  "okey_2025h2_est": r4(okey_2025h2), "okey_2026_jan_may": r4(okey_2026_pre),
                  "remi_jul_nov_2025_est": r4(remi_jul_nov_2025), "domlenta_2025h2_est": r4(domlenta_2025h2),
                  "domlenta_2026_pre_est": r4(domlenta_2026_pre), "hyper_h2_share_2025": r4(hyper_h2_share_2025)}

# =====================================================================================
# 2. Оборотный капитал: определение и ряд (IAS 17, датабук BS + разбивка кредиторки МСФО)
# =====================================================================================
# Разбивка «торговой и прочей кредиторки»: строка «за ОС, НМА и приобретение бизнеса» и «по опциону»
PAYABLES_SRC = {
    "FY 2020": ("ifrs_mkpao/ifrs_FY2020.pdf", "Payables for purchases of property, plant and equipment", 0),
    "1H 2021": ("ifrs_mkpao/ifrs_1H2021.pdf", "Payables for purchases of property, plant and equipment", 0),
    "FY 2021": ("ifrs_mkpao/ifrs_FY2021.pdf", "Payables for purchases of property, plant and equipment", 0),
    "FY 2022": ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", "Кредиторская задолженность за основные средства", 1),
    "1H 2023": ("ifrs_mkpao/ifrs_1H2023.pdf", "Кредиторская задолженность за основные средства", 0),
    "FY 2023": ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", "Кредиторская задолженность за основные средства", 0),
    "1H 2024": ("ifrs_mkpao/ifrs_1H2024.pdf", "Кредиторская задолженность за основные средства", 0),
    "FY 2024": ("ifrs_mkpao/ifrs_FY2024.pdf", "Кредиторская задолженность за основные средства", 0),
    "1H 2025": ("ifrs_mkpao/ifrs_1H2025.pdf", "Кредиторская задолженность за основные средства", 0),
    "FY 2025": ("ifrs_mkpao/ifrs_FY2025.pdf", "Кредиторская задолженность за основные средства", 0),
    "1H 2026": ("ifrs_mkpao/ifrs_1H2026.pdf", "Кредиторская задолженность за основные средства", 0),
}


def payables_split(period: str) -> dict:
    rel, label, idx = PAYABLES_SRC[period]
    page, t = P.page_with(rel, label)
    nums = P.numbers_after(t, label, max_lines=3)
    capex = nums[idx] / 1e6
    put = 0.0
    if "Кредиторская задолженность по опциону" in t and idx == 0:
        put = P.numbers_after(t, "Кредиторская задолженность по опциону")[0] / 1e6
    return {"capex_business": capex, "put": put, "src": f"{rel} с. {page}"}


# Компенсирующий актив (МСФО прим. 17 6М2026 / прим. 20 2025) — в датабуке строка «Restricted cash»
def comp_asset(period: str) -> float:
    if period == "1H 2026":
        rel = "ifrs_mkpao/ifrs_1H2026.pdf"
    elif period == "FY 2025":
        rel = "ifrs_mkpao/ifrs_FY2025.pdf"
    else:
        return 0.0
    page, t = P.page_with(rel, "Компенсирующий актив")
    return P.numbers_after(t, "Компенсирующий актив")[0] / 1e6


def g(label, per):
    v = BS.get(label, "IAS 17", per)
    return 0.0 if v is None else v / 1e6


PERIODS = ["FY 2020", "1H 2021", "FY 2021", "FY 2022", "1H 2023", "FY 2023", "1H 2024", "FY 2024",
           "1H 2025", "FY 2025", "1H 2026"]
NWC = {}
log("\n== 2. Оборотный капитал в определении книги (IAS 17), млрд ₽ ==")
log("  запасы + дебиторка + авансы выданные + НДС к возмещению + предоплаты + компенсирующий актив − (кредиторка − за ОС/НМА/бизнес − опцион)"
    " − обязательства по договорам − авансы полученные − прочие налоги")
for per in PERIODS:
    sp = payables_split(per)
    ca = comp_asset(per)
    parts = {
        "inventories": g("Inventories", per), "receivables": g("Trade and other receivables", per),
        "advances_paid": g("Advances paid", per), "taxes_recoverable": g("Taxes recoverable", per),
        "prepaid": g("Prepaid expense", per), "comp_asset": ca,
        "payables_total": g("Trade and other payables", per), "payables_capex_business": sp["capex_business"],
        "payables_put": sp["put"], "contract_liab": g("Contract liabilities", per),
        "advances_received": g("Advances received", per), "other_taxes": g("Other taxes payable", per),
    }
    payables_op = parts["payables_total"] - parts["payables_capex_business"] - parts["payables_put"]
    nwc = (parts["inventories"] + parts["receivables"] + parts["advances_paid"] + parts["taxes_recoverable"]
           + parts["prepaid"] + parts["comp_asset"] - payables_op - parts["contract_liab"]
           - parts["advances_received"] - parts["other_taxes"])
    nwc_magnit_literal = nwc - parts["payables_capex_business"]   # вариант «вся кредиторка, кроме опциона» (как A-W0 850oa)
    y, h = int(per[-4:]), (1 if per.startswith("1H") else 2)
    cogs_ltm = (cogs_half(y - 1, 2) + cogs_half(y, 1)) if h == 1 else (cogs_half(y, 1) + cogs_half(y, 2))
    rev_rep = LTM_REP[per]
    rev_pf = LTM_PF[per]
    cogs_pf = cogs_ltm * rev_pf / rev_rep          # [р] себестоимость на проформе: та же доля, что в отчёте
    days = {
        "inventory_days": parts["inventories"] / cogs_pf * 365,
        "trade_payable_days": payables_op / cogs_pf * 365,
        "receivable_days": parts["receivables"] / rev_pf * 365,
    }
    NWC[per] = {**parts, "payables_operating": payables_op, "nwc": nwc, "nwc_incl_capex_payables": nwc_magnit_literal,
                "rev_ltm_reported": rev_rep, "rev_ltm_pro_forma": rev_pf, "cogs_ltm": cogs_ltm,
                "nwc_pct_reported": nwc / rev_rep, "nwc_pct_pro_forma": nwc / rev_pf, **days, "src_payables": sp["src"]}
    log(f"  {per}: NWC {nwc:7.2f} ({nwc / rev_pf * 100:+.2f} % проформы LTM {rev_pf:.0f}; отчёт {nwc / rev_rep * 100:+.2f} %);"
        f" запасы {days['inventory_days']:.0f} дн., кредиторка {days['trade_payable_days']:.0f} дн.;"
        f" за ОС/бизнес {sp['capex_business']:.2f}, опцион {sp['put']:.2f}, комп. актив {ca:.2f}")

# --- декабрьский базис и июньский излишек (периметр Ленты до «О'КЕЙ»/«Дом Ленты»)
log("\n  Июньский излишек = NWC%(июнь, LTM-проформа) − среднее NWC%(декабрь до и после, годовая проформа)")
EXC = {}
for y in (2021, 2023, 2024, 2025):
    jun, d0, d1 = NWC[f"1H {y}"], NWC[f"FY {y - 1}"], NWC[f"FY {y}"]
    exc_pct = jun["nwc_pct_pro_forma"] - 0.5 * (d0["nwc_pct_pro_forma"] + d1["nwc_pct_pro_forma"])
    exc_prev = jun["nwc_pct_pro_forma"] - d0["nwc_pct_pro_forma"]
    EXC[y] = {"excess_pct": exc_pct, "excess_bn_at_june": exc_pct * jun["rev_ltm_pro_forma"], "vs_prev_dec_pct": exc_prev}
    log(f"   {y}: июнь {jun['nwc_pct_pro_forma'] * 100:+.2f} %, декабри {d0['nwc_pct_pro_forma'] * 100:+.2f}/{d1['nwc_pct_pro_forma'] * 100:+.2f} %"
        f" → излишек {exc_pct * 100:.2f} % = {exc_pct * jun['rev_ltm_pro_forma']:.1f} млрд (к прошлому декабрю {exc_prev * 100:.2f} %)")
exc_list = [EXC[y]["excess_pct"] for y in EXC]
exc_mean = sum(exc_list) / len(exc_list)
exc_recent = sum(EXC[y]["excess_pct"] for y in (2024, 2025)) / 2
exc_bn_2026 = exc_mean * R_LTM_PF_2026
log(f"  среднее 2021, 2023–2025: {exc_mean * 100:.2f} % (мин {min(exc_list) * 100:.2f}, макс {max(exc_list) * 100:.2f}); 2024–2025: {exc_recent * 100:.2f} %")
log(f"  → излишек на 30.06.2026 = {exc_mean * 100:.2f} % × {R_LTM_PF_2026:.1f} = {exc_bn_2026:.1f} млрд")

# --- проверка потоками: изменение ОК по ДДС IAS 17 поквартально (лист Financials quarterly, млн ₽)
log("\n  Поток ОК по ДДС (IAS 17, «Movements in Working Capital», млрд ₽): 1 кв. / 2 кв. / 3 кв. / 4 кв.")
WCQ = {}
for y in range(2021, 2027):
    row = []
    for q in (1, 2, 3, 4):
        try:
            v = FQ.get("Movements in Working Capital", "IAS 17", f"{q}Q {y}")
        except KeyError:
            v = None
        row.append(None if v is None else v / 1e3)
    WCQ[y] = row
    log(f"   {y}: " + " / ".join("—" if v is None else f"{v:+.1f}" for v in row))

# --- «О'КЕЙ» на дату покупки (МСФО 6М2026 прим. 5, с. 17) и «Дом Лента» (с. 19), «Реми» (КФО 2025 прим. 8, с. 48)
def acq_table(rel: str, page_label: str, labels: list[str]) -> dict:
    page, t = P.page_with(rel, page_label, "справедливой стоимости идентифицируемых")
    out = {}
    for lab in labels:
        try:
            out[lab] = abs(P.numbers_after(t, lab, min_abs=1000.0, max_lines=1)[0]) / 1e6
        except (KeyError, ValueError):
            out[lab] = 0.0
    return out | {"_page": page}


OKEY_LABELS = ["Запасы", "Торговая и прочая дебиторская задолженность", "Авансы выданные", "Налоги к возмещению",
               "Предоплаты по налогу на прибыль", "Торговая и прочая кредиторская задолженность", "Обязательства по договорам",
               "Авансы полученные", "Обязательства по прочим налогам", "Обязательства по налогу на прибыль",
               "Долгосрочные кредиты и займы", "Краткосрочные кредиты, краткосрочная часть долгосрочных кредитов",
               "Отложенные налоговые обязательства", "Основные средства (Прим. 4)"]
okey = acq_table("ifrs_mkpao/ifrs_1H2026.pdf", "Приобретение сети гипермаркетов «О’КЕЙ» (продолжение)", OKEY_LABELS)
okey_nwc = (okey["Запасы"] + okey["Торговая и прочая дебиторская задолженность"] + okey["Авансы выданные"]
            + okey["Налоги к возмещению"] - okey["Торговая и прочая кредиторская задолженность"]
            - okey["Обязательства по договорам"] - okey["Авансы полученные"] - okey["Обязательства по прочим налогам"])
okey_nwc_with_income_tax = okey_nwc + okey["Предоплаты по налогу на прибыль"] - okey["Обязательства по налогу на прибыль"]
okey_rev_annual = PF["okey_pf_2026"] * 2      # [р] годовой темп по проформе 1П2026 (66,12 × 2)
okey_inv_days = okey["Запасы"] / (okey_rev_annual * (1 - 0.2359)) * 365   # маржа ВП Ленты 1П2026 — ниже
dl = acq_table("ifrs_mkpao/ifrs_1H2026.pdf", "Приобретение строительных гипермаркетов «ОБИ Россия» (продолжение)",
               ["Запасы", "Обязательства по договорам", "Прочие оборотные активы"])
dl_nwc_at_acq = dl["Запасы"] - dl["Обязательства по договорам"]
remi = acq_table("ifrs_mkpao/ifrs_FY2025.pdf", "Приобретение дальневосточной сети магазинов «Реми» (продолжение)",
                 ["Запасы", "Торговая и прочая дебиторская задолженность", "Авансы выданные", "Прочие оборотные активы (Прим. 20)",
                  "Торговая и прочая кредиторская задолженность", "Обязательства по договорам (Прим. 26)",
                  "Обязательства по прочим налогам", "Обязательства по налогу на прибыль"])
gm_1h26 = 1 + bn(PL.get("Cost of sales", "IAS 17", "1H 2026")) / bn(PL.get("Sales", "IAS 17", "1H 2026"))

# Целевые условия для «О'КЕЙ»: июньский уровень Ленты до «Монетки» (гипермаркеты + супермаркеты) и июнь 2025 (группа)
lenta_jun_pre_monetka = sum(NWC[p]["nwc_pct_pro_forma"] for p in ("1H 2021", "1H 2023")) / 2
lenta_jun_2025 = NWC["1H 2025"]["nwc_pct_pro_forma"]
okey_target_pre_monetka = lenta_jun_pre_monetka * okey_rev_annual
okey_target_group = lenta_jun_2025 * okey_rev_annual
okey_gap = okey_nwc - okey_target_pre_monetka
okey_gap_group = okey_nwc - okey_target_group
log("\n  «О'КЕЙ» на 02.06.2026 (МСФО 6М2026 прим. 5, с. %d): запасы %.2f, дебиторка %.2f, авансы %.2f, НДС %.2f,"
    " кредиторка %.2f, договоры %.2f, авансы получ. %.2f, прочие налоги %.2f" % (
        okey["_page"], okey["Запасы"], okey["Торговая и прочая дебиторская задолженность"], okey["Авансы выданные"],
        okey["Налоги к возмещению"], okey["Торговая и прочая кредиторская задолженность"], okey["Обязательства по договорам"],
        okey["Авансы полученные"], okey["Обязательства по прочим налогам"]))
log(f"   ОК «О'КЕЙ» в определении книги {okey_nwc:.2f} (с налогом на прибыль {okey_nwc_with_income_tax:.2f}) = "
    f"{okey_nwc / okey_rev_annual * 100:+.2f} % годовой выручки {okey_rev_annual:.1f}; запасы ≈{okey_inv_days:.0f} дн.")
log(f"   Лента в июне до «Монетки» (2021, 2023): {lenta_jun_pre_monetka * 100:+.2f} %; июнь 2025 (группа): {lenta_jun_2025 * 100:+.2f} %")
log(f"   ОК «О'КЕЙ» на условиях Ленты: {okey_target_pre_monetka:.2f} (гипер+супер) / {okey_target_group:.2f} (группа)"
    f" → разрыв {okey_gap:.2f} / {okey_gap_group:.2f} млрд")
log(f"  «Дом Лента» при покупке: запасы {dl['Запасы']:.2f} − договоры {dl['Обязательства по договорам']:.2f} = {dl_nwc_at_acq:.2f}"
    f" (кредиторки поставщикам в сделке нет — покупка магазинов)")
log(f"  «Реми» при покупке: компенсирующий актив {remi['Прочие оборотные активы (Прим. 20)']:.2f}; прочие налоги {remi['Обязательства по прочим налогам']:.2f};"
    f" налог на прибыль {remi['Обязательства по налогу на прибыль']:.2f}")

# --- старт на 30.06.2026 и декабрьский базис
n26 = NWC["1H 2026"]
nwc_start = n26["nwc"]
nwc_pct_start = (nwc_start - exc_bn_2026) / R_LTM_PF_2026
dec25 = NWC["FY 2025"]
dec_basis_2025 = dec25["nwc_pct_pro_forma"]
dec_list = {p: NWC[p]["nwc_pct_pro_forma"] for p in ("FY 2020", "FY 2021", "FY 2022", "FY 2023", "FY 2024", "FY 2025")}
dec_mean_3 = sum(dec_list[p] for p in ("FY 2023", "FY 2024", "FY 2025")) / 3
acq_gap_pct = okey_gap / R_LTM_PF_2026
hold_from_start = nwc_pct_start - acq_gap_pct
# Декабрьский базис на периметре 30.06.2026: Лента (декабрь 2025) + «Дом Лента» структурно (запасы DIY) + «О'КЕЙ» на условиях Ленты
dl_rev_annual = 4 * dl_q2
dl_share = dl_rev_annual / R_LTM_PF_2026
okey_share = okey_rev_annual / R_LTM_PF_2026
own_share = 1 - dl_share - okey_share
dl_nwc_pct_own = dl_nwc_at_acq / dl_rev_annual   # [р] верхняя оценка: без кредиторки поставщикам
dec_basis_perimeter = own_share * dec_basis_2025 + okey_share * dec_basis_2025 + dl_share * dl_nwc_pct_own * 0.5
one_off_2026h2 = (nwc_pct_start - acq_gap_pct - dec_basis_perimeter) * R_LTM_PF_2026
log("\n  Старт 30.06.2026: NWC %.2f млрд; излишек %.1f; nwc_pct_start = (%.2f − %.1f) / %.1f = %.5f"
    % (nwc_start, exc_bn_2026, nwc_start, exc_bn_2026, R_LTM_PF_2026, nwc_pct_start))
log(f"  Декабри (проформа): " + ", ".join(f"{p[-4:]} {v * 100:+.2f} %" for p, v in dec_list.items())
    + f"; среднее 2023–2025 {dec_mean_3 * 100:+.2f} %")
log(f"  hold из старта (без разрыва «О'КЕЙ» {acq_gap_pct * 100:.3f} п.п.): {hold_from_start * 100:+.3f} %")
log(f"  декабрьский базис на периметре 30.06.2026 (Лента дек. 2025 {dec_basis_2025 * 100:+.2f} % на доли {own_share + okey_share:.3f},"
    f" DIY {dl_nwc_pct_own * 100:+.1f} % × 0,5 на доле {dl_share:.3f}): {dec_basis_perimeter * 100:+.3f} %")
log(f"  разовый поток 2П2026, если hold = декабрьский базис: {one_off_2026h2:+.1f} млрд")

# Проверка потоком 2П: NWC(июнь) − NWC(декабрь) по балансу к годовой проформе (2024–2025; 2021 и 2023 искажены покупками в 2П)
h2_ratio = {y: (NWC[f"1H {y}"]["nwc"] - NWC[f"FY {y}"]["nwc"]) / NWC[f"FY {y}"]["rev_ltm_pro_forma"] for y in (2024, 2025)}
h2_ratio_mean = sum(h2_ratio.values()) / len(h2_ratio)
R_ANNUAL_2026H2 = H1_2026_PF + domlenta_2026_pre + (rev_half(2025, 2) + okey_2025h2 + remi_jul_nov_2025 + domlenta_2025h2) * 1.10  # [р] 2П2026 ≈ 2П2025-проформа × 1,10
h2_flow_expected = h2_ratio_mean * R_ANNUAL_2026H2
dec26_implied_pct = (nwc_start - h2_flow_expected) / R_ANNUAL_2026H2
log(f"  поток 2П к годовой выручке: " + ", ".join(f"{y}: {v * 100:.2f} %" for y, v in h2_ratio.items())
    + f"; ожидаемый поток 2П2026 {h2_flow_expected:.1f} млрд при годовой выручке {R_ANNUAL_2026H2:.0f} → NWC(дек. 2026) ≈ {dec26_implied_pct * 100:+.2f} %")

# ---- выбор книги (суждения листа; обоснование — README)
# Знаменатель книги — facts.anchor.revenue_ltm листа фактов (fragments/facts.yaml): 1 393,313 = та же проформа без
# «Дом Ленты» до консолидации (в первичке её нет; null ≠ 0). Ядро считает ОК якоря как nwc_pct_start × revenue_ltm + излишек,
# поэтому ключи листа считаются на этом знаменателе, а ОК якоря остаётся −10,956.
R_LTM_FACTS = 1393.313037
R_LTM_BOOK = R_LTM_FACTS
EXCESS_PCT_BOOK = 0.020      # [с] среднее четырёх лет 2,02 % (2021, 2023–2025); 2024–2025 — 1,56 %; с 2,0 % старт сходится с декабрями 2023–2025
seasonal_june_excess_book = round(EXCESS_PCT_BOOK * R_LTM_BOOK, 1)
nwc_pct_start_book = round((nwc_start - seasonal_june_excess_book) / R_LTM_BOOK, 6)
HOLD_BOOK_EX_LTIP = -0.029   # [с] декабрьский базис в определении ОК без LTIP: среднее декабрей 2023–2025 −2,97 %, hold из старта −2,94 %, поток 2П → −2,8 %
# Долгосрочная часть денежного LTIP — возобновляемый «поплавок» в ОК, как текущая часть той же программы (кредиторка перед
# персоналом), а не разовое требование моста (решение ведущего по аудиту 30.09.2026, п. 22, governance-bridge-09): программа
# назначается ежегодно, выплаты 50/25/25 % в апрели трёх лет (годовой отчёт 2025, с. 85), расход — в EBITDA при начислении.
# ОК якоря книги = ОК в определении листа − LTIP (прочие долгосрочные обязательства 2 000 523 тыс. ₽, баланс 30.06.2026,
# прим. 22); декабрьский базис — тем же сдвигом доли выручки LTM (поплавок держится долей выручки, как текущая часть).
LTIP_LT_1H26 = 2.000523
HOLD_BOOK = round(HOLD_BOOK_EX_LTIP - LTIP_LT_1H26 / R_LTM_BOOK, 4)
NWC_ANCHOR_BOOK = round(nwc_start - LTIP_LT_1H26, 4)
acq_path_start = round(okey_gap / R_LTM_BOOK, 5)
acquired_path_book = {"2026H2": acq_path_start, "2027": round(acq_path_start / 2, 5), "2028": 0.0, "LT": 0.0}
one_off_book = (nwc_pct_start_book - (HOLD_BOOK_EX_LTIP + acq_path_start)) * R_LTM_BOOK   # оба — в определении ОК без LTIP
log(f"  знаменатель книги = facts.anchor.revenue_ltm {R_LTM_BOOK:.3f}; своя проформа {R_LTM_PF_2026:.3f} − «Дом Лента» до консолидации"
    f" {domlenta_2025h2 + domlenta_2026_pre:.3f} = {R_LTM_PF_2026 - domlenta_2025h2 - domlenta_2026_pre:.3f}")
capex_payables_ex_obi_1h26 = NWC["1H 2026"]["payables_capex_business"] - 4.0702   # [р] 17,303 − 4,499 − 8,734 (прим. 5, с. 19)
log(f"  КНИГА: seasonal_june_excess {seasonal_june_excess_book} млрд ({EXCESS_PCT_BOOK:.1%}), nwc_pct_start {nwc_pct_start_book},"
    f" hold {HOLD_BOOK}, acquired_path {acquired_path_book}; разовый поток 2П2026 от округления {one_off_book:+.2f} млрд")
log(f"  КНИГА с LTIP-поплавком в ОК: anchor_level {nwc_start:.4f} − LTIP {LTIP_LT_1H26} = {NWC_ANCHOR_BOOK}; hold {HOLD_BOOK_EX_LTIP} − "
    f"{LTIP_LT_1H26}/{R_LTM_BOOK:.1f} = {HOLD_BOOK}")
log(f"  вариант «кредиторка за ОС в ОК» (A-W0 850oa дословно, без ОБИ): задолженность за ОС 1П2026 {capex_payables_ex_obi_1h26:.2f} млрд"
    f" → старт {nwc_start - capex_payables_ex_obi_1h26:.2f}, nwc_pct_start {(nwc_start - capex_payables_ex_obi_1h26 - seasonal_june_excess_book) / R_LTM_PF_2026:.5f}")

RES["nwc"] = {
    "definition": "запасы + торговая и прочая дебиторка + авансы выданные + налоги к возмещению + предоплаченные расходы "
                  "+ компенсирующий актив (Реми) − (торговая и прочая кредиторка − за ОС/НМА/приобретение бизнеса − по опциону) "
                  "− обязательства по договорам − авансы полученные − обязательства по прочим налогам; IAS 17 (датабук BS), "
                  "разбивка кредиторки — прим. МСФО; налог на прибыль, займы выданные, касса и ограниченные ДС — вне ОК",
    "series": {p: {k: (r4(v) if isinstance(v, float) else v) for k, v in d.items()} for p, d in NWC.items()},
    "june_excess_by_year": {y: {k: r4(v) for k, v in d.items()} for y, d in EXC.items()},
    "june_excess_pct_mean": r4(exc_mean), "june_excess_pct_recent": r4(exc_recent),
    "seasonal_june_excess_bn": round(exc_bn_2026, 2),
    "wc_flow_quarterly_ias17": WCQ,
    "nwc_start_bn": round(nwc_start, 3), "revenue_ltm_pro_forma": round(R_LTM_PF_2026, 2),
    "nwc_pct_start": round(nwc_pct_start, 6),
    "december_pct": {p: r4(v) for p, v in dec_list.items()}, "december_mean_2023_2025": r4(dec_mean_3),
    "hold_from_start": round(hold_from_start, 6), "december_basis_perimeter": round(dec_basis_perimeter, 6),
    "one_off_2026h2_if_hold_is_dec_basis_bn": round(one_off_2026h2, 2),
    "h2_flow_ratio": {y: r4(v) for y, v in h2_ratio.items()}, "h2_flow_expected_2026_bn": round(h2_flow_expected, 1),
    "revenue_annual_2026h2_est": round(R_ANNUAL_2026H2, 1), "dec2026_implied_pct": r4(dec26_implied_pct),
    "book": {"seasonal_june_excess": seasonal_june_excess_book, "excess_pct": EXCESS_PCT_BOOK,
             "nwc_pct_start": nwc_pct_start_book, "hold": HOLD_BOOK, "hold_ex_ltip": HOLD_BOOK_EX_LTIP,
             "ltip_long_term_in_nwc": LTIP_LT_1H26, "anchor_level": NWC_ANCHOR_BOOK, "acquired_path": acquired_path_book,
             "one_off_2026h2_bn": round(one_off_book, 2), "revenue_ltm_denominator": R_LTM_BOOK},
    "capex_payables_ex_obi_1h2026": round(capex_payables_ex_obi_1h26, 3),
    "okey": {"nwc_at_acquisition": round(okey_nwc, 3), "nwc_with_income_tax": round(okey_nwc_with_income_tax, 3),
             "revenue_annual_pf": round(okey_rev_annual, 2), "target_pre_monetka": round(okey_target_pre_monetka, 3),
             "target_group_jun2025": round(okey_target_group, 3), "gap_bn": round(okey_gap, 3),
             "gap_bn_vs_group": round(okey_gap_group, 3), "gap_pct_group_rev": round(acq_gap_pct, 6),
             "inventory_days": round(okey_inv_days, 1), "page": okey["_page"]},
    "domlenta_nwc_at_acquisition": round(dl_nwc_at_acq, 3), "domlenta_rev_annual_run_rate": round(dl_rev_annual, 2),
    "remi_comp_asset": round(remi["Прочие оборотные активы (Прим. 20)"], 3),
    "remi_other_taxes_at_acq": round(remi["Обязательства по прочим налогам"], 3),
    "remi_income_tax_at_acq": round(remi["Обязательства по налогу на прибыль"], 3),
}

# =====================================================================================
# 3. Операционная касса, буфер, доходность кассы
# =====================================================================================
CASH_SRC = {
    "FY 2020": ("ifrs_mkpao/ifrs_FY2021.pdf", 1), "FY 2021": ("ifrs_mkpao/ifrs_FY2021.pdf", 0),
    "1H 2021": ("ifrs_mkpao/ifrs_1H2021.pdf", 0),
    "FY 2022": ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", 1), "1H 2023": ("ifrs_mkpao/ifrs_1H2023.pdf", 0),
    "FY 2023": ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", 0), "1H 2024": ("ifrs_mkpao/ifrs_1H2024.pdf", 0),
    "FY 2024": ("ifrs_mkpao/ifrs_FY2024.pdf", 0), "1H 2025": ("ifrs_mkpao/ifrs_1H2025.pdf", 0),
    "FY 2025": ("ifrs_mkpao/ifrs_FY2025.pdf", 0), "1H 2026": ("ifrs_mkpao/ifrs_1H2026.pdf", 0),
}
CASH_LABELS_RU = {"deposits": "Краткосрочные депозиты в рублях", "bank": "Остатки денежных средств на банковских счетах в рублях",
                  "transit": "Денежные средства в пути в рублях", "on_hand": "Остатки денежных средств в кассе в рублях"}
CASH_LABELS_EN = {"deposits": "Rouble denominated short-term deposits", "bank": "Rouble denominated balances with banks",
                  "transit": "Rouble denominated cash in transit", "on_hand": "Rouble denominated cash on hand",
                  "fx_bank": "Foreign currency denominated balances with banks",
                  "fx_deposits": "Foreign currency denominated short-term deposits"}


def cash_parts(period: str) -> dict:
    rel, idx = CASH_SRC[period]
    ru = "ifrs_FY202" not in rel or "FY2025" in rel or "FY2023" in rel or "FY2024" in rel
    labels = CASH_LABELS_EN if rel.endswith(("FY2021.pdf", "1H2021.pdf")) else CASH_LABELS_RU
    first = next(iter(labels.values()))
    page, t = P.page_with(rel, first)
    out = {}
    for k, lab in labels.items():
        try:
            nums = P.numbers_after(t, lab, min_abs=1.0, max_lines=1)
            out[k] = nums[idx] / 1e6 if idx < len(nums) else 0.0
        except (KeyError, ValueError):
            out[k] = 0.0
    out["_src"] = f"{rel} с. {page}"
    return out


CASH = {}
log("\n== 3. Касса по составу (МСФО, прим. «Денежные средства»), % LTM-проформы ==")
for per in PERIODS:
    c = cash_parts(per)
    total = sum(v for k, v in c.items() if not k.startswith("_"))
    bs_cash = g("Cash and cash equivalents", per)
    phys = c["on_hand"] + c["transit"]
    oper = phys + c["bank"]          # валютные остатки 1П2021 (19,1 млрд евро под сделку Billa) — не операционная касса
    rev = LTM_PF[per]
    CASH[per] = {**{k: v for k, v in c.items()}, "total": total, "bs_cash": bs_cash, "physical": phys, "operating_wide": oper,
                 "physical_pct": phys / rev, "operating_wide_pct": oper / rev, "total_pct": total / rev}
    log(f"  {per}: итого {total:6.2f} (баланс {bs_cash:6.2f}); касса+в пути {phys:5.2f} = {phys / rev * 100:.2f} %;"
        f" +счета {oper:5.2f} = {oper / rev * 100:.2f} %; вся касса {total / rev * 100:.2f} %")
    assert abs(total - bs_cash) < 0.01, f"{per}: состав кассы не сходится с балансом"
jun = [p for p in PERIODS if p.startswith("1H")]
dec = [p for p in PERIODS if p.startswith("FY")]
avg = lambda ps, k: sum(CASH[p][k] for p in ps) / len(ps)
phys_mean = avg(PERIODS, "physical_pct")
phys_dec = avg(dec, "physical_pct")
phys_jun = avg(jun, "physical_pct")
bank_jun_pct = [CASH[p]["bank"] / LTM_PF[p] for p in jun]
bank_jun_mean = sum(bank_jun_pct) / len(bank_jun_pct)
wide_mean = avg(PERIODS, "operating_wide_pct")
wide_jun = avg(jun, "operating_wide_pct")
wide_dec = avg(dec, "operating_wide_pct")
total_min = min(CASH[p]["total_pct"] for p in PERIODS)
total_jun_min = min(CASH[p]["total_pct"] for p in jun)
OPC_NORM = round(phys_mean + bank_jun_mean, 3)    # [р] правило листа: физическая касса (среднее июнь+декабрь) + рабочий остаток счетов (июньское среднее)
log(f"  касса+в пути: среднее {phys_mean * 100:.2f} % (июнь {phys_jun * 100:.2f}, декабрь {phys_dec * 100:.2f});"
    f" счета в июне {bank_jun_mean * 100:.2f} % (мин {min(bank_jun_pct) * 100:.2f}, макс {max(bank_jun_pct) * 100:.2f})")
log(f"  «касса + в пути + счета» (метод A-F3 850oa): среднее {wide_mean * 100:.2f} % (июнь {wide_jun * 100:.2f}, декабрь {wide_dec * 100:.2f})")
log(f"  вся касса: минимум {total_min * 100:.2f} % (июньский минимум {total_jun_min * 100:.2f} %)")
log(f"  → норма операционной кассы = {phys_mean * 100:.2f} + {bank_jun_mean * 100:.2f} = {OPC_NORM * 100:.2f} % LTM"
    f" = {OPC_NORM * R_LTM_PF_2026:.1f} млрд на 30.06.2026 (фактическая касса {CASH['1H 2026']['total']:.1f})")

# Доходность кассы: проценты по депозитам / средний остаток депозитов / средняя ключевая
INT_SRC = [("1H 2024", "ifrs_mkpao/ifrs_1H2025.pdf", 1), ("FY 2024", "ifrs_mkpao/ifrs_FY2025.pdf", 1),
           ("1H 2025", "ifrs_mkpao/ifrs_1H2026.pdf", 1), ("FY 2025", "ifrs_mkpao/ifrs_FY2025.pdf", 0),
           ("1H 2026", "ifrs_mkpao/ifrs_1H2026.pdf", 0)]   # 1П2023–1П2024 в полугодовых отчётах без разбивки
dep_int = {}
for per, rel, idx in INT_SRC:
    page, t = P.page_with(rel, "Проценты по депозитам")
    dep_int[per] = P.numbers_after(t, "Проценты по депозитам")[idx] / 1e6
halves = {
    "2024H1": (dep_int["1H 2024"], "FY 2023", "1H 2024", ("2024-01-01", "2024-07-01")),
    "2024H2": (dep_int["FY 2024"] - dep_int["1H 2024"], "1H 2024", "FY 2024", ("2024-07-01", "2025-01-01")),
    "2025H1": (dep_int["1H 2025"], "FY 2024", "1H 2025", ("2025-01-01", "2025-07-01")),
    "2025H2": (dep_int["FY 2025"] - dep_int["1H 2025"], "1H 2025", "FY 2025", ("2025-07-01", "2026-01-01")),
    "2026H1": (dep_int["1H 2026"], "FY 2025", "1H 2026", ("2026-01-01", "2026-07-01")),
}
KY = {}
log("\n  Доходность депозитов к ключевой (полугодие; остаток — среднее концов периода):")
for h, (inc, p0, p1, (d0, d1)) in halves.items():
    dep_avg = 0.5 * (CASH[p0]["deposits"] + CASH[p1]["deposits"])
    wide_avg = dep_avg + 0.5 * (CASH[p0]["bank"] + CASH[p1]["bank"])
    kr = key_avg(d0, d1) / 100
    y_dep = 2 * inc / dep_avg
    tot_avg = 0.5 * (CASH[p0]["total"] + CASH[p1]["total"])
    jun_lvl = CASH[p1 if p1.startswith("1H") else p0]["total"]      # июньская точка полугодия — без декабрьского пика
    KY[h] = {"interest": inc, "dep_avg": dep_avg, "key": kr, "k_deposits": y_dep / kr, "k_dep_plus_bank": 2 * inc / wide_avg / kr,
             "k_total_endpoints": 2 * inc / tot_avg / kr, "k_total_june": 2 * inc / jun_lvl / kr}
    log(f"   {h}: проценты {inc:.3f}, депозиты ср. {dep_avg:.1f}, ключевая {kr * 100:.2f} % → k депозитов {y_dep / kr:.2f};"
        f" к всей кассе: по концам {KY[h]['k_total_endpoints']:.2f}, по июньской точке {KY[h]['k_total_june']:.2f}")
k_vals = sorted(v["k_deposits"] for v in KY.values())
k_med = 0.5 * (k_vals[len(k_vals) // 2] + k_vals[(len(k_vals) - 1) // 2])
k_mean = sum(k_vals) / len(k_vals)
log(f"  k депозитов: медиана {k_med:.2f}, среднее {k_mean:.2f}, размах {k_vals[0]:.2f}–{k_vals[-1]:.2f}")
k_tot_end = sum(v["k_total_endpoints"] for v in KY.values()) / len(KY)
k_tot_jun = sum(v["k_total_june"] for v in KY.values()) / len(KY)
K_BOOK = round(0.5 * (k_tot_end + k_tot_jun), 2)
log(f"  k всей кассы (ядро применяет k ко всей кассе): по концам {k_tot_end:.2f}, по июньской точке {k_tot_jun:.2f} → книга {K_BOOK}")

RES["cash"] = {"series": {p: {k: (r4(v) if isinstance(v, float) else v) for k, v in d.items()} for p, d in CASH.items()},
               "physical_mean_pct": r4(phys_mean), "physical_jun_pct": r4(phys_jun), "physical_dec_pct": r4(phys_dec),
               "bank_jun_mean_pct": r4(bank_jun_mean), "wide_mean_pct": r4(wide_mean), "wide_jun_pct": r4(wide_jun),
               "wide_dec_pct": r4(wide_dec), "total_min_pct": r4(total_min), "total_jun_min_pct": r4(total_jun_min),
               "operating_cash_pct": OPC_NORM, "operating_cash_bn_2026h1": round(OPC_NORM * R_LTM_PF_2026, 2),
               "deposit_yield": {h: {k: r4(v) for k, v in d.items()} for h, d in KY.items()},
               "k_median": r4(k_med), "k_mean": r4(k_mean), "k_total_endpoints_mean": r4(k_tot_end),
               "k_total_june_mean": r4(k_tot_jun), "cash_yield_k_book": K_BOOK}

# =====================================================================================
# 4. Налог
# =====================================================================================
log("\n== 4. Налог ==")
TAXREC = {}
for per, rel, idx, lab_rate in [
    ("1H 2026", "ifrs_mkpao/ifrs_1H2026.pdf", 0, "установленной в России"),
    ("1H 2025", "ifrs_mkpao/ifrs_1H2026.pdf", 1, "установленной в России"),
    ("FY 2025", "ifrs_mkpao/ifrs_FY2025.pdf", 0, "установленной в России"),
    ("FY 2024", "ifrs_mkpao/ifrs_FY2025.pdf", 1, "установленной в России"),
]:
    page, t = P.page_with(rel, "Условный налог на прибыль", "Прибыль до налога")
    d = {"pbt": P.numbers_after(t, "Прибыль до налога")[idx] / 1e6,
         "statutory": P.numbers_after(t, lab_rate)[idx] / 1e6,
         "nondeductible": P.numbers_after(t, "подлежащих вычету для целей налогообложения")[idx] / 1e6,
         "tax": P.numbers_after(t, "Расход по налогу на прибыль")[idx] / 1e6}
    if "Восстановление ранее непризнанных налоговых убытков" in t:
        d["loss_recovery"] = P.numbers_after(t, "Восстановление ранее непризнанных налоговых убытков")[idx] / 1e6
    page2, t2 = P.page_with(rel, "Расход по текущему налогу на прибыль")
    d["current_tax"] = P.numbers_after(t2, "Расход по текущему налогу на прибыль")[idx] / 1e6
    d["deferred_tax"] = P.numbers_after(t2, "Расход по отложенному налогу на прибыль")[idx] / 1e6
    d["src"] = f"{rel} с. {page}"
    TAXREC[per] = d
for per, d in TAXREC.items():
    pbt17 = bn(PL.get("Profit / (loss) before income tax", "IAS 17", per))
    paid = -bn(CF.get("Income taxes paid", "IAS 17", per))
    rev = LTM_REP[per] if per.startswith("FY") else rev_half(int(per[-4:]), 1)
    d |= {"etr": -d["tax"] / d["pbt"], "pbt_ias17": pbt17, "cash_paid": paid,
          "current_over_pbt17": -d["current_tax"] / pbt17, "paid_over_pbt17": paid / pbt17,
          "addback_pct_rev": -d["nondeductible"] / TAX / rev, "revenue": rev}
    log(f"  {per}: ДДН МСФО {d['pbt']:.2f}, налог {-d['tax']:.2f} → ЭСН {d['etr'] * 100:.1f} %; текущий {-d['current_tax']:.2f},"
        f" уплачен {paid:.2f}; ДДН IAS 17 {pbt17:.2f} → текущий/ДДН17 {d['current_over_pbt17'] * 100:.1f} %;"
        f" невычитаемые {-d['nondeductible']:.3f} = {d['addback_pct_rev'] * 100:.3f} % выручки"
        + (f"; восстановление убытков {d['loss_recovery']:.2f}" if "loss_recovery" in d else ""))
# 1П2026: невычитаемые за вычетом непризнанного актива по убытку «О'КЕЙ» за июнь (гипотеза листа)
ok_june_unrec = PF["okey_pbt_contrib_2026"] * TAX
p_1h26_ex_okey = (-TAXREC["1H 2026"]["nondeductible"] - ok_june_unrec) / TAX / TAXREC["1H 2026"]["revenue"]
log(f"  1П2026 без непризнанного актива по июньскому убытку «О'КЕЙ» ({ok_june_unrec:.3f}): p = {p_1h26_ex_okey * 100:.3f} %")

# Отложенный налог: убытки и ОС (КФО 2023–2025)
DT = {}
for year, rel, row_idx in [(2025, "ifrs_mkpao/ifrs_FY2025.pdf", 0), (2024, "ifrs_mkpao/ifrs_FY2025.pdf", 1),
                           (2023, "ifrs_mkpao/ifrs_FY2024.pdf", 1), (2022, "ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", 1)]:
    pages = [i for i, t in enumerate(P.pdf_pages(rel)) if "налогооблагаемого дохода" in t and "Основные средства" in t]
    t = P.pdf_pages(rel)[pages[row_idx]]
    loss = P.numbers_after(t, "налогооблагаемого дохода", min_abs=1.0)
    ppe = P.numbers_after(t, "Основные средства ", min_abs=1.0)
    DT[year] = {"loss_dta_open": loss[0] / 1e6, "loss_dta_pl": loss[1] / 1e6, "loss_dta_close": loss[-1] / 1e6,
                "loss_dta_acq": (loss[2] / 1e6 if len(loss) >= 4 else 0.0),
                "ppe_dtl_open": ppe[0] / 1e6, "ppe_dtl_pl": ppe[1] / 1e6, "ppe_dtl_close": ppe[-1] / 1e6,
                "src": f"{rel} с. {pages[row_idx] + 1}"}
unrec = {}
for year, rel in [(2025, "ifrs_mkpao/ifrs_FY2025.pdf"), (2024, "ifrs_mkpao/ifrs_FY2024.pdf"),
                  (2023, "ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf")]:
    page, t = P.page_with(rel, "не были признаны")
    i = t.find("не были признаны")
    seg = t[i:i + 400]
    import re
    nums = [float(x.replace(" ", "").replace(" ", "")) / 1e6 for x in re.findall(r"\d{1,3}(?:[  ]\d{3})+", seg)]
    unrec[year] = nums[0]
# ставка 2024: переоценка входящего ОНО по ОС с 20 % до 25 % (эффект смены ставки в сверке 2 187 475)
remeas_2024 = DT[2024]["ppe_dtl_open"] * (0.25 / 0.20 - 1)
ppe_op = {2022: DT[2022]["ppe_dtl_pl"], 2023: DT[2023]["ppe_dtl_pl"], 2024: DT[2024]["ppe_dtl_pl"] - remeas_2024,
          2025: DT[2025]["ppe_dtl_pl"]}
rate_by_year = {2022: 0.20, 2023: 0.20, 2024: 0.25, 2025: 0.25}
capex17 = {y: -(bn(CF.get("Purchases of property, plant and equipment", "IAS 17", f"FY {y}"))
                + bn(CF.get("Purchases of intangible assets other than leasehold rights", "IAS 17", f"FY {y}")))
           for y in range(2012, 2026)}
da17 = {y: -bn(PL.get("Depreciation", "IAS 17", f"FY {y}")) for y in range(2012, 2026)}
log("  Отложенный актив по убыткам (признанный): " + ", ".join(
    f"{y}: {DT[y]['loss_dta_open']:.2f}→{DT[y]['loss_dta_close']:.2f}" for y in sorted(DT)))
log(f"  Прецедент «Утконоса» (ООО «Новый Импульс-50», куплен 02.2022, присоединён к ООО «Лента» 12.2022): актив по убыткам при покупке"
    f" {DT[2022]['loss_dta_acq']:.3f} млрд (= {DT[2022]['loss_dta_acq'] / 0.20:.1f} млрд убытков по 20 %), КФО 2023 прим. 24")
log("  Непризнанный актив по убыткам дочерних: " + ", ".join(f"{y}: {v:.3f}" for y, v in sorted(unrec.items())))
log("  ОНО по ОС, движение в П/У (операционное, без переоценки ставки 2024): " + ", ".join(
    f"{y}: {v:+.3f} (база {-v / rate_by_year[y]:+.1f}; capex {capex17[y]:.1f}, D&A {da17[y]:.1f})" for y, v in ppe_op.items()))

# Пул собственных убытков на 30.06.2026 [р]: признанный на 31.12.2025 / 25 % минус оценка использования в 1П2026
nol_dec25 = DT[2025]["loss_dta_close"] / TAX
unrec_dec25 = unrec[2025] / TAX
# использование 1П2026: база до убытков ≈ ДДН IAS 17 + убытки «Дом Ленты» и «О'КЕЙ» (июнь) + невычитаемые − ускоренная налоговая амортизация (половина 2025)
base_pre_loss = (TAXREC["1H 2026"]["pbt_ias17"] + PF["domlenta_pbt_2026"] + PF["okey_pbt_contrib_2026"]
                 + ok_june_unrec / TAX * 0 + (-TAXREC["1H 2026"]["nondeductible"] - ok_june_unrec) / TAX
                 - 0.5 * (-ppe_op[2025] / TAX))
tax_pre_loss = TAX * base_pre_loss
used_1h26 = max(0.0, (tax_pre_loss - (-TAXREC["1H 2026"]["current_tax"])) / TAX)
nol_start = max(0.0, nol_dec25 - used_1h26)
log(f"  Пул Ленты: 31.12.2025 признано {nol_dec25:.2f} млрд (актив {DT[2025]['loss_dta_close']:.3f}/25 %), непризнано {unrec_dec25:.2f};"
    f" оценка использования 1П2026 {used_1h26:.2f} → nol_start ≈ {nol_start:.2f}")

# «О'КЕЙ» по РСБУ (ГИР БО): прибыль до налога, ОНА/ОНО, финансовые вложения, проценты к уплате
ok = P.rsbu_okey()
log("  ООО «О'КЕЙ» (РСБУ, ГИР БО), млрд ₽:")
ok_rows = {}
for y in sorted(ok):
    r = ok[y]
    ok_rows[y] = {"revenue": r.get("2110", 0) / 1e6, "pbt": r.get("2300", 0) / 1e6, "interest_payable": r.get("2330", 0) / 1e6,
                  "current_tax": r.get("2411", 0) / 1e6, "dta": r.get("1180", 0) / 1e6, "dtl": r.get("1420", 0) / 1e6,
                  "fin_inv_lt": r.get("1170", 0) / 1e6, "fin_inv_st": r.get("1240", 0) / 1e6,
                  "loans_lt": r.get("1410", 0) / 1e6, "loans_st": r.get("1510", 0) / 1e6}
    q = ok_rows[y]
    log(f"   {y}: выручка {q['revenue']:.1f}, ДДН {q['pbt']:+.2f}, проценты {q['interest_payable']:.2f}, текущий налог {q['current_tax']:.2f},"
        f" ОНА {q['dta']:.2f}, ОНО {q['dtl']:.2f}, фин. вложения {q['fin_inv_lt']:.1f}+{q['fin_inv_st']:.1f}, займы {q['loans_lt'] + q['loans_st']:.1f}")

# Пул «О'КЕЙ»/«Дом Ленты» на 30.06.2026 — [с] по слагаемым
OKEY_LOSS_2023_24 = -(ok_rows["2023"]["pbt"] + ok_rows["2024"]["pbt"])
OKEY_LOSS_2025_OPER = 5.0      # [с] 2025 без выбытия фин. вложений: ≈ убыток 2024 (2,26) + рост процентов (ключевая 19,2 % против 17,5 %) и падение выручки −1,8 %; 3–8
OKEY_LOSS_2026_H1 = (PF["okey_pbt_pf_2026"]) * 0.8   # [с] январь–июнь 2026: ДДН МСФО 16 −5,53 × 0,8 (налоговая база ближе к IAS 17: аренда — расход); 3,5–5,5
DL_LOSS_2026_H1 = PF["domlenta_pbt_2026"]
acq_nol_components = {"okey_2023_2024_rsbu": OKEY_LOSS_2023_24, "okey_2025_operating": OKEY_LOSS_2025_OPER,
                      "okey_2026h1": OKEY_LOSS_2026_H1, "domlenta_2026h1": DL_LOSS_2026_H1}
acq_nol = sum(acq_nol_components.values())
log(f"  Пул приобретённых на 30.06.2026: " + " + ".join(f"{k} {v:.2f}" for k, v in acq_nol_components.items()) + f" = {acq_nol:.1f}")

# Запертые убытки прогноза (locked_addback): «О'КЕЙ» (EBIT − проценты) и «Дом Лента», млрд ₽ за полугодие — [с]
okey_debt_ext = okey["Долгосрочные кредиты и займы"]  # 32,28 = Альфа 32,0 + облигации Б1Р2/Б1Р4 0,28
LOCKED = {  # [с] (EBITDA_К IAS17, D&A_К, проценты_К, убыток «Дом Ленты»)
    "2026H2": (1.0, 1.3, 3.2, 2.5),
    "2027H1": (0.5, 1.4, 2.9, 2.0),
    "2027H2": (2.0, 1.4, 2.8, 1.0),
    "2028H1": (1.5, 1.5, 2.6, 1.0),
    "2028H2": (3.0, 1.5, 2.5, 0.5),
}
locked_addback = {h: round(max(0.0, -(e - da - i)) + d, 1) for h, (e, da, i, d) in LOCKED.items()}
log("  Запертые убытки прогноза (EBITDA_К − D&A_К − проценты_К, плюс «Дом Лента»): "
    + ", ".join(f"{h} {v:.1f}" for h, v in locked_addback.items()) + f"; сумма {sum(locked_addback.values()):.1f}")
debt_principal = 141.9232   # [ф] research/04 §2.2 = МСФО 6М2026 прим. 20: фикс 98,140 + плав 37,000 + облигации 6,783
okey_debt_principal = 32.0 + 6.7832
share_debt_okey = okey_debt_principal / debt_principal
log(f"  Доля долга в юрлице «О'КЕЙ»: {okey_debt_principal:.2f}/{debt_principal:.2f} = {share_debt_okey * 100:.1f} %")

# α: при каком α вторая корзина обнуляется (центр 2027: EBIT_Л ≈ маржа 7,5 % − D&A 2,3 % на 1,5 трлн; проценты ≈ 15 млрд в год)
EBIT_L_2027, I_L_2027 = 0.052 * 1500, 15.0
alpha_min = I_L_2027 / EBIT_L_2027
log(f"  α связывает только при α·база < I: α_min ≈ {I_L_2027:.0f}/{EBIT_L_2027:.0f} = {alpha_min:.2f} (центр 2027, грубо)")

RES["tax"] = {"reconciliation": {p: {k: (r4(v) if isinstance(v, float) else v) for k, v in d.items()} for p, d in TAXREC.items()},
              "addback_1h2026_ex_okey_june": r4(p_1h26_ex_okey),
              "deferred": {y: {k: (r4(v) if isinstance(v, float) else v) for k, v in d.items()} for y, d in DT.items()},
              "unrecognised_loss_dta": {y: r4(v) for y, v in unrec.items()},
              "ppe_dtl_pl_operational": {y: r4(v) for y, v in ppe_op.items()},
              "capex_ias17": {y: r4(v) for y, v in capex17.items()}, "da_ias17": {y: r4(v) for y, v in da17.items()},
              "nol_dec2025_recognised": r4(nol_dec25), "nol_dec2025_unrecognised": r4(unrec_dec25),
              "nol_used_1h2026_est": r4(used_1h26), "nol_start_est": r4(nol_start),
              "okey_rsbu": ok_rows, "acquired_nol_components": {k: r4(v) for k, v in acq_nol_components.items()},
              "acquired_nol": round(acq_nol, 1), "locked_addback": locked_addback, "share_debt_okey": r4(share_debt_okey),
              "alpha_min_2027": r4(alpha_min)}

# --- ускоренная налоговая амортизация: подгонка «налоговая D&A = b·capex_t + (1−b)·Σcapex_{t−k}/L», k=1..L
log("\n  Ускоренная налоговая амортизация (подгонка к движению ОНО по ОС):")
obs = {y: -ppe_op[y] / rate_by_year[y] for y in (2022, 2023, 2024, 2025)}   # налоговая D&A − бухгалтерская, млрд
best = None
fits = []
for b in [i / 20 for i in range(0, 21)]:
    for L in range(3, 11):
        err = 0.0
        pred = {}
        for y in obs:
            tax_da = b * capex17[y] + (1 - b) * sum(capex17.get(y - k, capex17[2012]) for k in range(1, L + 1)) / L
            pred[y] = tax_da - da17[y]
            err += (pred[y] - obs[y]) ** 2
        fits.append((err, b, L, pred))
fits.sort(key=lambda x: x[0])
err0, b0, L0, pred0 = fits[0]
log(f"   наблюдаемая разница (налог − учёт): " + ", ".join(f"{y}: {v:+.1f}" for y, v in obs.items()))
log(f"   лучшая подгонка b={b0:.2f}, L={L0}: " + ", ".join(f"{y}: {v:+.1f}" for y, v in pred0.items()) + f" (СКО {math.sqrt(err0 / 4):.1f})")
near = [(b, L) for e, b, L, _ in fits if e <= 2 * err0 + 4]
log(f"   близкие подгонки (ошибка ≤ 2×мин + 4): " + ", ".join(f"b{b:.2f}/L{L}" for b, L in near[:12]))

# PV эффекта отсрочки на стилизованном пути: выручка 1,41 трлн (LTM-проформа) растёт 10 % до 2028, 8 % до 2031, 7 % дальше;
# capex 5 % выручки; бухгалтерская D&A — 10 лет по когортам (A-K6); налоговая — по подгонке. Грубая PV (r = R_DISC).
def pv_deferral(b: float, L_tax: int, years: int = 60) -> float:
    rev, capex_hist = R_LTM_PF_2026, [capex17[y] for y in range(2016, 2026)]
    pv = 0.0
    caps = list(capex_hist)
    for t in range(1, years + 1):
        gr = 0.10 if t <= 2 else (0.08 if t <= 5 else 0.07)
        rev *= 1 + gr
        c = 0.05 * rev
        caps.append(c)
        book = sum(caps[-1 - k] for k in range(1, 11)) / 10
        tax_da = b * c + (1 - b) * sum(caps[-1 - k] for k in range(1, L_tax + 1)) / L_tax
        pv += TAX * (tax_da - book) / (1 + R_DISC) ** (t - 0.5)
    return pv


pv_best = pv_deferral(b0, L0)
pv_b10 = pv_deferral(0.10, 10)
pv_scen = {b: pv_deferral(b, 10) for b in (0.1, 0.2, 0.3)}
log(f"   PV отсрочки налога (стилизованный путь, r {R_DISC:.0%}, налоговый срок 10 лет): "
    + ", ".join(f"премия {b:.0%}: {v:.1f} млрд = {v * RUB_PER_BN:.0f} ₽/акц" for b, v in pv_scen.items())
    + f"; подгонка по 4 точкам (неустойчива) {pv_best:.1f} млрд")
tax_formula_2025 = TAX * (TAXREC["FY 2025"]["pbt_ias17"] - TAXREC["FY 2025"]["nondeductible"] / TAX)
loss_use_2025 = DT[2025]["loss_dta_open"] + TAXREC["FY 2025"].get("loss_recovery", 0.0) - DT[2025]["loss_dta_close"]
log(f"   2025: налог по формуле ядра 25 % × (ДДН IAS 17 + невычитаемые) = {tax_formula_2025:.2f}; текущий {-TAXREC['FY 2025']['current_tax']:.2f};"
    f" разница {tax_formula_2025 + TAXREC['FY 2025']['current_tax']:.2f} = зачёт убытков {loss_use_2025:.2f} + ОС {-ppe_op[2025]:.2f} + прочее")
# Центр премии (решение ведущего по аудиту 30.09.2026, п. 2, magnit-peers-01): правило ядра даёт разницу налоговой и
# учётной D&A ровно p·(capex − D&A), поэтому p года = разница / (capex − D&A) — ПОТОК; по тому же правилу временная
# разница по ОС = p × остаточная стоимость — ЗАПАС. Книга — медиана годовых оценок потока, ось ±0,10 (вся ось внутри
# свидетельств: запас 0,36–0,45, поток без 2025 г. 0,38); подгонка b 0,75 неустойчива (СКО 2,0 на 4 точках) и не используется.
flow_p = {y: obs[y] / (capex17[y] - da17[y]) for y in obs}
PPE_NET = {2023: 177.894356, 2024: 177.689497, 2025: 200.505014}   # баланс КФО 2024 и 2025, строка «Основные средства»
DTL_RATE = {2023: 0.20, 2024: 0.25, 2025: 0.25}                    # ставка ОНО на конец года (с 01.01.2025 — 25 %)
stock_p = {y: -DT[y]["ppe_dtl_close"] / DTL_RATE[y] / PPE_NET[y] for y in PPE_NET}
flow_sorted = sorted(flow_p.values())
PREMIUM_BOOK = round((flow_sorted[1] + flow_sorted[2]) / 2, 2)
PREMIUM_AXIS = (round(PREMIUM_BOOK - 0.10, 2), round(PREMIUM_BOOK + 0.10, 2))
log("   премия по потоку p = (налоговая − учётная D&A) / (capex − D&A): " + ", ".join(f"{y}: {v:.2f}" for y, v in flow_p.items())
    + f" → медиана {PREMIUM_BOOK}; по запасу p = ОНО по ОС / ставка / остаточная стоимость ОС: "
    + ", ".join(f"{y}: {v:.2f}" for y, v in stock_p.items()) + f" → КНИГА: premium {PREMIUM_BOOK}, ось {PREMIUM_AXIS[0]}–{PREMIUM_AXIS[1]}")
RES["tax"]["tax_depreciation"] = {"observed_tax_minus_book_da": {y: r4(v) for y, v in obs.items()},
                                  "premium_by_flow": {y: r4(v) for y, v in flow_p.items()},
                                  "premium_by_stock": {y: r4(v) for y, v in stock_p.items()},
                                  "ppe_net_book": PPE_NET, "premium_book": PREMIUM_BOOK, "premium_axis": list(PREMIUM_AXIS),
                                  "fit_b": b0, "fit_L": L0, "fit_pred": {y: r4(v) for y, v in pred0.items()},
                                  "near_fits": near[:12], "pv_fit_bn": round(pv_best, 1),
                                  "pv_fit_rub_per_share": round(pv_best * RUB_PER_BN), "pv_premium10_bn": round(pv_b10, 1),
                                  "pv_premium10_rub_per_share": round(pv_b10 * RUB_PER_BN),
                                  "pv_by_premium_bn": {str(b): round(v, 1) for b, v in pv_scen.items()},
                                  "pv_by_premium_rub": {str(b): round(v * RUB_PER_BN) for b, v in pv_scen.items()},
                                  "tax_formula_2025": round(tax_formula_2025, 2), "loss_use_2025_tax": round(loss_use_2025, 3)}

# =====================================================================================
# 5. Цена ошибки (грубая, до ядра): ₽ на акцию
# =====================================================================================
log("\n== 5. Цена ошибки (грубо, до ядра; 1 млрд ₽ требований ≈ %.2f ₽/акц) ==" % RUB_PER_BN)
def pv_half(amount: float, half_index: float) -> float:   # half_index: 0.5 = середина 2026H2
    return amount / (1 + R_DISC) ** (half_index / 2)

# (а) уровень NWC%: 1 п.п. выручки на весь горизонт = PV(прирост выручки) × 1 %
g_lt = 0.075
pv_dR = R_LTM_PF_2026 * g_lt / (R_DISC - g_lt)
cost_1pp = 0.01 * pv_dR
log(f"  1 п.п. уровня NWC/выручка (hold), только рост: ≈{cost_1pp:.1f} млрд = {cost_1pp * RUB_PER_BN:.0f} ₽/акц (PV приростов выручки {pv_dR:.0f} млрд при g {g_lt:.1%})")
# при NWC якоря = балансу смена hold даёт ещё разовый поток во 2П2026: Δhold × годовая выручка 2П2026
one_off_1pp = 0.01 * R_ANNUAL_2026H2 / (1 + R_DISC) ** 0.25
cost_hold_1pp = cost_1pp + one_off_1pp
log(f"  1 п.п. hold с разовым потоком 2П2026 ({one_off_1pp:.1f} млрд): ≈{cost_hold_1pp:.1f} млрд = {cost_hold_1pp * RUB_PER_BN:.0f} ₽/акц; ось ±0,5 п.п. ≈ ±{0.5 * cost_hold_1pp * RUB_PER_BN:.0f} ₽/акц")
# (б) излишек: ошибка на X млрд → разовый поток 2П2026 X и обратные потоки в каждом 1П (масштаб выручки) — нетто ≈ X × (1 − PV сезонного цикла)
# при фиксированном hold излишек меняет только сезонный цикл будущих лет: отток в каждом 1П (с 2027H1), приток во 2П
cost_exc_10 = 10 * sum((pv_half(1, 2 * k + 1.5) - pv_half(1, 2 * k + 2.5)) * (1 + g_lt) ** k for k in range(0, 80))
log(f"  излишек +10 млрд при фиксированном hold: −{cost_exc_10:.1f} млрд = −{cost_exc_10 * RUB_PER_BN:.0f} ₽/акц (деньги связаны на полгода каждый год)")
# постоянная прибавка p ±0,1 п.п. выручки
rev_t, pv_R = R_LTM_PF_2026, 0.0
for t in range(1, 81):
    rev_t *= 1 + (0.10 if t <= 2 else (0.08 if t <= 5 else g_lt))
    pv_R += rev_t / (1 + R_DISC) ** (t - 0.5)
cost_p = TAX * 0.001 * pv_R
log(f"  постоянная прибавка p ±0,1 п.п.: ∓{cost_p:.1f} млрд = ∓{cost_p * RUB_PER_BN:.0f} ₽/акц (PV выручки {pv_R:.0f} млрд)")
# доходность кассы k ±0,1 и буфер ±0,5 п.п.: в APV 850oa влияют только через щит (τ × чистые проценты) и кэрри излишка
cash_share = OPC_NORM + 0.010
cost_k = TAX * 0.1 * 0.12 * cash_share * pv_R
cost_buf = TAX * 0.005 * (0.155 - K_BOOK * 0.12) * pv_R
log(f"  k ±0,1 (касса {cash_share:.1%} выручки, ключевая ≈12 %): ∓{cost_k:.2f} млрд = ∓{cost_k * RUB_PER_BN:.0f} ₽/акц (через щит: выше доход кассы — меньше щит)")
log(f"  буфер ±0,5 п.п.: ±{cost_buf:.2f} млрд = ±{cost_buf * RUB_PER_BN:.0f} ₽/акц (знак ядра 850oa: буфер растит валовой долг и щит, кэрри на буфер не начисляется)")
# (в) разрыв «О'КЕЙ»: высвобождение в 2027–2028 против удержания
rel_fast = pv_half(okey_gap * 0.5, 1.5) + pv_half(okey_gap * 0.5, 3.5)
log(f"  разрыв ОК «О'КЕЙ» {okey_gap:.1f} млрд: высвобождение 50 % в 2027H1 и 50 % в 2028H1 = {rel_fast:.1f} млрд PV = {rel_fast * RUB_PER_BN:.0f} ₽/акц против удержания")
# (г) операционная касса ±0,1 п.п.
cost_opc = 0.001 * R_LTM_PF_2026 * (1 + g_lt / (R_DISC - g_lt) * 0)  # мост: норма на дату; рост нормы — отток в FCFF
cost_opc_flow = 0.001 * pv_dR
log(f"  операционная касса ±0,1 п.п.: мост {cost_opc:.2f} + прирост {cost_opc_flow:.2f} = {(cost_opc + cost_opc_flow) * RUB_PER_BN:.0f} ₽/акц")
# (д) пул Ленты: 1 млрд убытка → 0,25 млрд налога в ближайшие полугодия
cost_nol = TAX * pv_half(1, 1.0)
log(f"  пул собственных убытков ±1 млрд: {cost_nol:.3f} млрд = {cost_nol * RUB_PER_BN:.1f} ₽/акц")
# (е) запирание убытков приобретённых: наивное ядро (зачёт сразу) против правила листа
USABLE_FROM_H = 3.5   # 2028H1 = четвёртое полугодие после 2026H1 (индекс середины: 2026H2 = 0,5)
HAIRCUT = 0.25
naive = sum(TAX * pv_half(v, 0.5 + i) for i, v in enumerate(locked_addback.values())) + TAX * pv_half(acq_nol, 0.5) * 0  # пул на старте ядро не видит вовсе
locked_total = sum(locked_addback.values())
book_rule = (1 - HAIRCUT) * TAX * pv_half(acq_nol + locked_total, USABLE_FROM_H)
pool_value_now = TAX * (acq_nol) * 1.0
locked_until_uf = sum(v for i, v in enumerate(locked_addback.values()) if 0.5 + i < USABLE_FROM_H)
book_rule = (1 - HAIRCUT) * TAX * pv_half(acq_nol + locked_until_uf, USABLE_FROM_H) + sum(
    TAX * pv_half(v, 0.5 + i) for i, v in enumerate(locked_addback.values()) if 0.5 + i >= USABLE_FROM_H)
log(f"  запертые убытки прогноза {locked_total:.1f} млрд: наивное ядро учло бы щит {naive:.2f} млрд PV;"
    f" правило листа (пул {acq_nol:.1f} + {locked_until_uf:.1f} до 2028H1, с 2028H1 в общий пул, haircut {HAIRCUT:.0%}) даёт {book_rule:.2f} млрд PV")
log(f"   разница правило − наивное = {book_rule - naive:+.2f} млрд = {(book_rule - naive) * RUB_PER_BN:+.0f} ₽/акц")
alt = {}
for uf_label, uf in (("2027H2", 2.5), ("2028H1", 3.5), ("2029H1", 5.5), ("2031H1", 9.5)):
    for hc in (0.0, 0.25, 0.5, 1.0):
        locked_until = [v for i, v in enumerate(locked_addback.values()) if 0.5 + i < uf]
        val = (1 - hc) * TAX * pv_half(acq_nol + sum(locked_until), uf)
        cost_locked = sum(TAX * pv_half(v, 0.5 + i) for i, v in enumerate(locked_addback.values()) if 0.5 + i < uf)
        # ₽/акц относительно наивного ядра: ценность пула − налог на запертые убытки (после usable_from убытки зачитываются сразу — как в наивном)
        alt[f"{uf_label}/h{hc}"] = round((val - cost_locked) * RUB_PER_BN)
log("   ₽/акц (ценность пула − налог на запертые убытки) по usable_from/haircut: " + ", ".join(f"{k}: {v:+d}" for k, v in alt.items()))
RES["cost_of_error"] = {"rub_per_bn": round(RUB_PER_BN, 3), "r_disc": R_DISC, "nwc_level_1pp_bn": round(cost_1pp, 1),
                        "addback_0p1pp_rub": round(cost_p * RUB_PER_BN), "cash_yield_0p1_rub": round(cost_k * RUB_PER_BN),
                        "buffer_0p5pp_rub": round(cost_buf * RUB_PER_BN), "june_excess_10bn_bn": round(cost_exc_10, 2),
                        "nwc_level_1pp_rub": round(cost_1pp * RUB_PER_BN),
                        "hold_1pp_with_one_off_rub": round(cost_hold_1pp * RUB_PER_BN), "june_excess_10bn_rub": round(cost_exc_10 * RUB_PER_BN),
                        "okey_gap_release_rub": round(rel_fast * RUB_PER_BN), "opcash_0p1pp_rub": round((cost_opc + cost_opc_flow) * RUB_PER_BN),
                        "nol_1bn_rub": round(cost_nol * RUB_PER_BN, 1), "locked_rule_minus_naive_rub": round((book_rule - naive) * RUB_PER_BN),
                        "locked_grid_rub": alt}

# =====================================================================================
# 6. Проверки (VERIFY.md): тождества и регрессия ключевых чисел листа
# =====================================================================================
CHECKS = []


def chk(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))
    log(f"  [{'OK' if ok else 'ПРОВАЛ'}] {name} {detail}")


log("\n== 6. Проверки ==")
chk("sha256 первички = MANIFEST", all(v["match"] for v in P.USED.values()), f"({len(P.USED)} файлов)")
chk("опцион «Реми» в кредиторке 30.06.2026 = 5,2142", abs(NWC["1H 2026"]["payables_put"] - 5.214174) < 1e-6)
chk("кредиторка за ОС/НМА/бизнес 30.06.2026 = 9,1196", abs(NWC["1H 2026"]["payables_capex_business"] - 9.119601) < 1e-6)
chk("остаток «ОБИ» 17,303 − 4,499 − 8,734 = 4,070 ≤ кредиторки за бизнес", abs(17.303283 - 4.49891 - 8.734162 - 4.070211) < 1e-6
    and 4.070211 <= NWC["1H 2026"]["payables_capex_business"])
chk("NWC 30.06.2026 = −10,956", abs(nwc_start + 10.9559) < 5e-4, f"({nwc_start:.4f})")
chk("NWC 30.06.2026 не содержит опциона и ОБИ (пересборка из строк)",
    abs((NWC["1H 2026"]["payables_total"] - NWC["1H 2026"]["payables_operating"])
        - (NWC["1H 2026"]["payables_put"] + NWC["1H 2026"]["payables_capex_business"])) < 1e-9)
chk("состав кассы = кассе баланса на 11 датах", all(abs(CASH[p]["total"] - CASH[p]["bs_cash"]) < 0.01 for p in PERIODS))
chk("проформа 1П2026 = 648,49 + 66,12 − 9,36 = 705,25", abs(H1_2026_PF - 705.248956) < 1e-5, f"({H1_2026_PF:.4f})")
chk("ОК «О'КЕЙ» при покупке = 3,467", abs(okey_nwc - 3.4669) < 5e-4, f"({okey_nwc:.4f})")
chk("излишек: среднее 4 лет 1,5–2,5 %", 0.015 <= exc_mean <= 0.025, f"({exc_mean:.4%})")
chk("hold книги (без LTIP) в пределах 0,1 п.п. от hold из старта", abs(HOLD_BOOK_EX_LTIP - hold_from_start) <= 0.001,
    f"({HOLD_BOOK} против {hold_from_start:.5f})")
chk("разовый поток 2П2026 от выбора hold по модулю < 1 млрд", abs(one_off_book) < 1.0, f"({one_off_book:+.2f})")
chk("ЭСН 1П2026 = 29,24 %", abs(TAXREC["1H 2026"]["etr"] - 0.2924) < 5e-4, f"({TAXREC['1H 2026']['etr']:.4%})")
chk("сверка налога 1П2026: 3,769 + 0,002 + 0,637 = 4,409",
    abs(-TAXREC["1H 2026"]["statutory"] + 0.002144 - TAXREC["1H 2026"]["nondeductible"] + TAXREC["1H 2026"]["tax"]) < 1e-3)
chk("актив по убыткам 31.12.2025 = 2,050; непризнанный = 0,677", abs(DT[2025]["loss_dta_close"] - 2.050452) < 1e-6
    and abs(unrec[2025] - 0.677267) < 1e-6)
chk("движение ОНО по ОС 2025 в П/У = −3,900", abs(ppe_op[2025] + 3.90018) < 1e-4)
chk("налог по формуле ядра 2025 − текущий = зачёт убытков + ОС ± 1 млрд",
    abs((tax_formula_2025 + TAXREC["FY 2025"]["current_tax"]) - (loss_use_2025 - ppe_op[2025])) < 1.0)
chk("пул приобретённых = сумма слагаемых", abs(acq_nol - sum(acq_nol_components.values())) < 1e-9, f"({acq_nol:.2f})")
chk("премия книги = медиана годовых оценок потока; ось внутри свидетельств потока и запаса",
    PREMIUM_BOOK == round(sorted(flow_p.values())[1] / 2 + sorted(flow_p.values())[2] / 2, 2)
    and min(stock_p.values()) - 0.1 <= PREMIUM_AXIS[0] and PREMIUM_AXIS[1] <= max(flow_p.values()), f"({PREMIUM_BOOK})")
chk("ОК якоря книги = ОК листа − долгосрочный LTIP (поплавок в ОК)", abs(NWC_ANCHOR_BOOK - (nwc_start - LTIP_LT_1H26)) < 1e-4,
    f"({NWC_ANCHOR_BOOK})")
chk("операционная касса 0,4–1,0 % выручки", 0.004 <= OPC_NORM <= 0.010, f"({OPC_NORM:.3%})")
chk("знаменатель книги = своя проформа без «Дом Ленты» до консолидации ± 0,1",
    abs(R_LTM_BOOK - (R_LTM_PF_2026 - domlenta_2025h2 - domlenta_2026_pre)) < 0.1)
chk("ОК якоря из ключей книги = балансу ± 0,05", abs(nwc_pct_start_book * R_LTM_BOOK + seasonal_june_excess_book - nwc_start) < 0.05)
chk("k книги между оценками по концам и по июньской точке", min(k_tot_end, k_tot_jun) <= K_BOOK <= max(k_tot_end, k_tot_jun))
RES["checks"] = [{"name": n, "ok": o, "detail": d} for n, o, d in CHECKS]
RES["sources"] = P.USED
OUT_JSON.write_text(json.dumps(RES, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
log(f"\nпервичка: {len(P.USED)} файлов; sha256 сверен с MANIFEST: "
    + ", ".join(f"{k.split('/')[-1]}={'да' if v['match'] else ('нет строки' if v['match'] is None else 'НЕТ')}" for k, v in P.USED.items()))
n_fail = sum(1 for _, o, _ in CHECKS if not o)
log(f"ПРОВЕРКИ: {len(CHECKS) - n_fail} из {len(CHECKS)} OK")
OUT_TXT.write_text("\n".join(LOG) + "\n", encoding="utf-8")
if n_fail:
    raise SystemExit(1)
