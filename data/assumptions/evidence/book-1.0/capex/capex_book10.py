# -*- coding: utf-8 -*-
"""Лист capex и D&A книги 1.0 «Ленты» (850, базис IAS 17, шаг полугодие).

Что делает (разделы печати совпадают с разделами README):
  0. Проверка цитат: каждое число, взятое из PDF, ищется на своей странице (pypdfium2);
     числа датабука читаются openpyxl из Lenta_Q22026_DATABOOK.xlsx (блок IAS 17).
  1. История по полугодиям 2014H1–2026H1: выручка, денежный capex (ОС + НМА + права аренды),
     D&A до МСФО 16, поступления от выбытия, capex/выручка, capex/D&A, чистый прирост площади.
  2. Сезонность денежного capex: доля 2П, множители m1/m2 (правило выборки задано до подсчёта).
  3. Структура capex 2025 (презентация 2 кв. 2026, с. 24) и разложение 1П2026 и 2024 года.
  4. Capex открытия по форматам: калибровка по открытиям 2025 года → growth_capex_per_m2 (цены 2026).
  5. Поддерживающий capex снизу вверх при нулевом чистом росте, периметр 30.06.2026, цены 2026:
     low / base / high по форматам и статьям.
  6. Путь 2026–2036 (когорты реконструкций по годам открытия из датабука), ключи полугодий.
  7. Физическая доля (maintenance_area_share).
  8. Сверки: стоимость замещения по классам ОС (прим. 4 МСФО 1П2026, прим. 7 МСФО 2025),
     тождество capex/D&A, история.
  9. Срок службы (asset_life_years), D&A якоря на проформе.
 10. Поступления от выбытия (disposal_proceeds_pct).
 11. Интеграционный capex «О'КЕЙ».
 12. Цена ошибки (правило ведущего до ядра).
 13. Запись capex_out.json и фрагмента book-draft/fragments/capex.yaml.

Первичка — каталог из env LENTA_PRIMARY_DIR (по умолчанию <H>/reference/primary, путь
вычисляется от места скрипта). Сеть не нужна. Python 3.12 + openpyxl + pypdfium2.
Запуск: python -B capex_book10.py > capex_out.txt
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from pathlib import Path

import openpyxl
import pypdfium2 as pdfium

HERE = Path(__file__).resolve().parent
H = HERE.parents[3]                      # .../lenta-850-handoff
PRIMARY = Path(os.environ.get("LENTA_PRIMARY_DIR", str(H / "reference" / "primary")))
FRAGMENT = H / "book-draft" / "fragments" / "capex.yaml"
OUT_JSON = HERE / "capex_out.json"

DATABOOK = PRIMARY / "Lenta_Q22026_DATABOOK.xlsx"
IFRS_FY25 = PRIMARY / "ifrs_mkpao" / "ifrs_FY2025.pdf"
IFRS_1H26 = PRIMARY / "ifrs_mkpao" / "ifrs_1H2026.pdf"
PRES_Q226 = PRIMARY / "Lenta_Q2-2026_Investor-Presentation_rus.pdf"
REL_Q425 = PRIMARY / "Lenta_Group_Q425_Financial_Results_ENG_vF.pdf"
REL_Q126 = PRIMARY / "Lenta_Group_Q126_Financial_Results_ENG_vF.pdf"
REL_Q226 = PRIMARY / "Lenta-Group_Q226_Financial_Results_ENG.pdf"
AR25 = PRIMARY / "lenta_annual_report_2025_ru.pdf"
STRAT = PRIMARY / "Lenta_strategy-presentation-2028.pdf"
NEWS_OKEY = PRIMARY / "lentagroup_news" / "2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.txt"
NEWS_Q126 = PRIMARY / "lentagroup_news" / "2026-04-30_gruppa-lenta-obyavlyaet-o-roste-vyruchki-na-23-4-v-1-kvartale-2026-god.txt"

OUT: dict = {"meta": {"sheet": "book-1.0/capex", "date": "2026-09-28", "basis": "IAS 17", "prices": "2026"}}
FAIL: list[str] = []


def p(*a):
    print(*a)


def pct(x, d=2):
    return f"{100 * x:.{d}f} %"


# ----------------------------------------------------------------------------- 0. цитаты
_PDF_CACHE: dict = {}


def pdf_pages(path: Path) -> list[str]:
    if path not in _PDF_CACHE:
        doc = pdfium.PdfDocument(str(path))
        pages = []
        for i in range(len(doc)):
            t = doc[i].get_textpage().get_text_range()
            t = t.replace(" ", " ").replace(" ", " ").replace("￾", "").replace("\r", "")
            pages.append(t)
        _PDF_CACHE[path] = pages
    return _PDF_CACHE[path]


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s)


def cite(path: Path, page: int, needles: list[str], what: str) -> None:
    """Проверяет, что все needles есть на странице page (1-based) файла path."""
    txt = norm(pdf_pages(path)[page - 1])
    miss = [n for n in needles if norm(n) not in txt]
    status = "ДА" if not miss else f"НЕТ: {miss}"
    if miss:
        FAIL.append(f"{path.name} с. {page}: {what}: {miss}")
    p(f"  [{status}] {path.name}, с. {page}: {what}")
    OUT.setdefault("citations", []).append({"file": path.name, "page": page, "what": what, "ok": not miss})


def cite_txt(path: Path, needles: list[str], what: str) -> None:
    txt = norm(path.read_text(encoding="utf-8"))
    miss = [n for n in needles if norm(n) not in txt]
    if miss:
        FAIL.append(f"{path.name}: {what}: {miss}")
    p(f"  [{'ДА' if not miss else 'НЕТ: ' + str(miss)}] {path.name}: {what}")
    OUT.setdefault("citations", []).append({"file": path.name, "page": None, "what": what, "ok": not miss})


# ----------------------------------------------------------------------------- датабук
def load_databook():
    wb = openpyxl.load_workbook(DATABOOK, data_only=True, read_only=True)
    sheets = {}
    for name in ("PL", "CF", "Operating Results", "DC Space"):
        sheets[name] = [list(r) for r in wb[name].iter_rows(values_only=True)]
    return sheets


def ias17_block(rows):
    """Возвращает {метка колонки: индекс} для блока IAS 17 листов PL/CF (до колонки 'IFRS 16')."""
    r7 = rows[6]
    stop = next((i for i, v in enumerate(r7) if isinstance(v, str) and "IFRS 16" in v), len(r7))
    hdr = rows[7]
    # в датабуке встречается кириллическая «Н» в метке («1Н 2015») — приводим к латинице
    return {str(v).strip().replace("Н", "H"): i for i, v in enumerate(hdr[:stop]) if v is not None and i > 0}


def row_by_label(rows, label, start=0):
    for i, r in enumerate(rows[start:], start):
        if isinstance(r[0], str) and r[0].strip().startswith(label):
            return r
    raise KeyError(label)


def main() -> int:
    db = load_databook()
    PL, CF, OR, DC = db["PL"], db["CF"], db["Operating Results"], db["DC Space"]
    colPL, colCF = ias17_block(PL), ias17_block(CF)

    def pl(label, col):
        v = row_by_label(PL, label)[colPL[col]]
        return (v if isinstance(v, (int, float)) else 0.0) / 1e6          # тыс. руб. → млрд; «-» и пусто → 0

    def cf(label, col):
        v = row_by_label(CF, label)[colCF[col]]
        return (v if isinstance(v, (int, float)) else 0.0) / 1e6

    # Operating Results: заголовки в строке 7
    or_hdr = [str(v).strip() if v is not None else None for v in OR[6]]

    def orow(label, occurrence=1):
        n = 0
        for r in OR:
            if isinstance(r[0], str) and r[0].strip() == label:
                n += 1
                if n == occurrence:
                    return r
        raise KeyError(label)

    or_norm = [h.replace(" ", "") if h else None for h in or_hdr]      # «FY 2021» и «FY2022» в одной строке заголовка

    def oval(r, col):
        v = r[or_norm.index(col.replace(" ", ""))]
        return float(v) if isinstance(v, (int, float)) else 0.0

    # ------------------------------------------------------------------------- 0. цитаты
    p("0. ПРОВЕРКА ЦИТАТ (число → страница первички)")
    cite(PRES_Q226, 24, ["46,1", "48,7%", "12,2%", "11,1%", "7,6%", "9,3%", "Открытия", "Реконструкции", "Логистика",
                         "Обслуживание и", "10,4", "12,0", "14,6", "10,8", "11,7", "3,9%", "4,5%", "3,5%", "3,4%"],
         "структура capex 2025 (46,1 млрд) и квартальный capex 2кв25–2кв26")
    cite(IFRS_FY25, 42, ["352 574 993", "152 069 979", "22 177 303", "42 775 850", "8 504 972", "8 068 711",
                         "179 682 413", "127 349 575", "63 759 417", "73 800 509", "14 145 449", "13 492 215",
                         "10 427 275", "11 388 050", "12 999 112", "3 576 783", "24 154 634", "262 998",
                         "1 088 979", "28 687 759", "7 689 722", "6 909 912", "20 543 484"],
         "прим. 7 МСФО 2025: ОС по классам, амортизация, выбытие")
    cite(IFRS_FY25, 19, ["до 30", "от 2 до 15", "линейным методом"], "учётные сроки ОС (здания до 30, машины 2–15, благоустройство 7)")
    cite(IFRS_FY25, 56, ["21 992 124", "6 176 443", "2 121 848", "1 445 022", "18 426 915"], "прим. 13 МСФО 2025: НМА")
    cite(IFRS_1H26, 15, ["400 235 318", "163 165 629", "13 084 648", "17 395 875", "32 303 120", "210 964 481",
                         "137 921 505", "69 769 444", "78 736 649", "6 195 750", "6 723 915", "2 216 195",
                         "2 109 423", "14 161 425", "13 656 043", "164 983", "24 854 884", "4 370 134", "2 974 612"],
         "прим. 4 МСФО 1П2026: ОС по классам, приобретение дочерних")
    cite(IFRS_1H26, 16, ["13 084 648", "10 894 378", "226 523", "2 378 509", "26 584 058"], "прим. 4: состав амортизации 1П2026")
    cite(IFRS_1H26, 17, ["30 387 441", "21 152 577"], "прим. 5: ОС «О'КЕЙ» по справедливой стоимости")
    cite(IFRS_1H26, 25, ["25 495 328", "3 523 194", "1 568 013", "810 496", "18 426 948"], "прим. 10: НМА 1П2026")
    cite(IFRS_1H26, 37, ["11 370 371", "9 216 001"], "прим. 31: договорные обязательства по капвложениям")
    cite(REL_Q425, 5, ["381 new stores on a gross basis", "232 convenience stores", "98 drogerie",
                       "2 hypermarkets, 13 supermarkets and 34 Vingarazh"], "валовые открытия 4 кв. 2025")
    cite(REL_Q126, 7, ["395 new stores on a gross basis", "263 convenience stores", "101 drogerie",
                       "11 supermarkets (including 7 refurbishments)", "20 Vingarazh"], "валовые открытия 1 кв. 2026")
    p("  [ИЗОБРАЖЕНИЕ] Lenta-Group_Q226_Financial_Results_ENG.pdf, с. 7: 291 открытие брутто (229 у дома, 2 гипер, 15 супер,"
      " 11 дрогери, 33 «Вингараж»), capex 11,7 млрд — PDF без текстового слоя, прочитано по изображению страницы")
    cite(AR25, 11, ["собственных грузовиков", "1 200"], "ГО-2025: 1 200 собственных грузовиков")
    cite(AR25, 17, ["9 новых гипермаркетов", "2,5 тыс. кв. м"], "ГО-2025: 9 гипермаркетов (с «Молнией»), городской гипер 2,5 тыс. м²")
    cite(AR25, 19, ["71 реконструкцию", "34 магазина"], "ГО-2025: супермаркеты — 34 открытия и 71 реконструкция")
    cite(AR25, 21, ["было открыто более", "1 000 магазинов"], "ГО-2025: «Монетка» открыла более 1 000 магазинов")
    cite(AR25, 22, ["322 новых магазина"], "ГО-2025: «Улыбка радуги» 322 магазина (с учётом закрытий)")
    cite(STRAT, 30, ["≤1%"], "стратегия-2028: ИТ-затраты (capex + opex) ≤ 1 % выручки")
    cite(STRAT, 33, ["5,5%"], "стратегия-2028: capex ≤ 5,5 % выручки без M&A")
    cite_txt(NEWS_OKEY, ["ребрендинг большинства магазинов"], "релиз 02.06.2026: ребрендинг «О'КЕЙ» в «Гипер Ленту»")
    cite_txt(NEWS_Q126, ["прошла ребрендинг в «Дом Лента»"], "релиз 30.04.2026: ребрендинг «Дом Лента» завершён в 1 кв.")

    # ------------------------------------------------------------------------- 1. история
    p("\n1. ИСТОРИЯ ПО ПОЛУГОДИЯМ (IAS 17, млрд ₽; датабук PL/CF/Operating Results)")
    years = list(range(2014, 2026))
    H = {}   # 'YYYYH1' -> dict

    def half_vals(y, h):
        c1, cfy = f"1H {y}", f"FY {y}"
        def get(fn, lab):
            v1 = fn(lab, c1)
            if h == 1:
                return v1
            return fn(lab, cfy) - v1
        rev = get(pl, "Sales")
        da = -get(pl, "Depreciation")
        ppe = -get(cf, "Purchases of property, plant and equipment")
        intg = -get(cf, "Purchases of intangible assets other than leasehold rights")
        lease = -get(cf, "Purchases of leasehold rights")
        proc = get(cf, "Proceeds from sale of property, plant and equipment") + get(cf, "Proceeds from disposals and leasehold rights")
        loss = get(cf, "Loss/(gain) from disposal of property, plant and equipment") + get(cf, "Loss/(gain) from disposal of leasehold rights")
        preop = -get(pl, "Pre-opening cost")
        return dict(revenue=rev, da=da, capex=ppe + intg + lease, capex_ppe=ppe, capex_intang=intg, capex_lease=lease,
                    proceeds=proc, disposal_loss=loss, proceeds_net_of_gain=proc + loss, preopening=preop)

    for y in years:
        for h in (1, 2):
            H[f"{y}H{h}"] = half_vals(y, h)
    H["2026H1"] = half_vals(2026, 1)

    # чистый прирост торговой площади по полугодиям (сумма кварталов строки New Selling Space) и по форматам
    new_space = row_by_label(OR, "New Selling Space")
    idx_ns = OR.index(new_space)
    fmt_names = ["hyper", "super", "conv", "droge", "remi", "diy", "other"]
    def q_sum(r, y, h):
        qs = (1, 2) if h == 1 else (3, 4)
        return sum(oval(r, f"{q}Q {y}") for q in qs) / 1000.0
    for key in H:
        y, h = int(key[:4]), int(key[-1])
        H[key]["net_new_area_k"] = q_sum(new_space, y, h) if y >= 2020 else None
        H[key]["net_new_area_fmt_k"] = ({f: q_sum(OR[idx_ns + 1 + i], y, h) for i, f in enumerate(fmt_names)} if y >= 2020 else None)
    # площадь на конец полугодия (строка Total Selling Space)
    tot_space = row_by_label(OR, "Total Selling Space")
    for key in H:
        y, h = int(key[:4]), int(key[-1])
        H[key]["area_end_k"] = oval(tot_space, f"{2 if h == 1 else 4}Q {y}") / 1000.0

    p("полугодие | выручка | capex | %выр | D&A | %выр | capex/D&A | поступл.−прибыль выбытия | чист. прирост площади, тыс. м²")
    for key, v in H.items():
        nn = v["net_new_area_k"]
        p(f"{key} | {v['revenue']:7.1f} | {v['capex']:6.2f} | {pct(v['capex'] / v['revenue'])} | {v['da']:6.2f} | "
          f"{pct(v['da'] / v['revenue'])} | {v['capex'] / v['da']:5.2f} | {v['proceeds_net_of_gain']:6.3f} | "
          f"{'—' if nn is None else f'{nn:7.1f}'}")
    # годовые
    Y = {}
    for y in years:
        a, b = H[f"{y}H1"], H[f"{y}H2"]
        Y[y] = {k: a[k] + b[k] for k in ("revenue", "da", "capex", "proceeds", "disposal_loss", "proceeds_net_of_gain", "preopening")}
        Y[y]["share_h2_capex"] = b["capex"] / Y[y]["capex"]
        Y[y]["share_h1_rev"] = a["revenue"] / Y[y]["revenue"]
    p("\nгод | выручка | capex | %выр | D&A | %выр | capex/D&A | доля 2П capex | предоткрытие")
    for y in years:
        v = Y[y]
        p(f"{y} | {v['revenue']:7.1f} | {v['capex']:6.2f} | {pct(v['capex'] / v['revenue'])} | {v['da']:5.2f} | "
          f"{pct(v['da'] / v['revenue'])} | {v['capex'] / v['da']:5.2f} | {v['share_h2_capex']:.3f} | {v['preopening']:.3f}")
    p("Сноска: D&A — строка P&L «Depreciation» (строка ДДС IAS 17 за 1П2020, 2020, 1П2021 ошибочна — research/02 §5.3).")
    p("Предоткрытие — отдельная строка SG&A до 1П2018, в годовом 2018 переклассифицировано, дальше ноль: в capex/м² его нет (урок М16).")
    OUT["history_half"] = {k: {kk: (round(vv, 4) if isinstance(vv, float) else vv) for kk, vv in v.items()} for k, v in H.items()}
    OUT["history_year"] = {str(k): {kk: round(vv, 4) for kk, vv in v.items()} for k, v in Y.items()}

    # ------------------------------------------------------------------------- 2. сезонность
    p("\n2. СЕЗОННОСТЬ ДЕНЕЖНОГО CAPEX (правило выборки до подсчёта: 2014–2025 без лет, где capex 2П < 1П)")
    rows_s = {}
    for y in years:
        c1, c2 = H[f"{y}H1"]["capex"], H[f"{y}H2"]["capex"]
        r1, r2 = H[f"{y}H1"]["revenue"], H[f"{y}H2"]["revenue"]
        yr = (c1 + c2) / (r1 + r2)
        rows_s[y] = dict(ratio=c2 / c1, share2=c2 / (c1 + c2), m1=(c1 / r1) / yr, m2=(c2 / r2) / yr, s1=r1 / (r1 + r2))
        p(f"{y}: 2П/1П {c2 / c1:5.2f}; доля 2П {c2 / (c1 + c2):.3f}; m1 {rows_s[y]['m1']:.3f}; m2 {rows_s[y]['m2']:.3f}; доля 1П в выручке {rows_s[y]['s1']:.3f}")
    sample = [y for y in years if rows_s[y]["ratio"] >= 1.0]
    excl = [y for y in years if y not in sample]
    def mean(v): return sum(v) / len(v)
    def sd(v):
        m = mean(v); return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))
    m1s, m2s = [rows_s[y]["m1"] for y in sample], [rows_s[y]["m2"] for y in sample]
    M1, M2 = round(mean(m1s), 2), round(mean(m2s), 2)
    recent = [2023, 2024, 2025]
    p(f"исключены (2П < 1П): {excl}; выборка n = {len(sample)}: m1 {mean(m1s):.4f} ± {sd(m1s):.4f}, m2 {mean(m2s):.4f} ± {sd(m2s):.4f}; "
      f"доля 2П {mean([rows_s[y]['share2'] for y in sample]):.4f}")
    p(f"последние три года 2023–2025: m1 {mean([rows_s[y]['m1'] for y in recent]):.3f}, m2 {mean([rows_s[y]['m2'] for y in recent]):.3f}")
    S1_2027 = round(mean([rows_s[y]["s1"] for y in (2024, 2025)]), 4)   # доля 1П в выручке, прокси до сетки
    kq = 1.0 / (M1 * S1_2027 + M2 * (1 - S1_2027))
    p(f"множители книги M1 = {M1}, M2 = {M2}; нормировка пары 2027 при доле 1П в выручке {S1_2027} (среднее 2024–2025, прокси до сетки): "
      f"k = {kq:.5f} → 1П {M1 * kq:.4f}, 2П {M2 * kq:.4f}")
    OUT["seasonality"] = dict(sample=sample, excluded=excl, m1=M1, m2=M2, m1_sd=round(sd(m1s), 4), m2_sd=round(sd(m2s), 4),
                              s1_2027_proxy=S1_2027, k=round(kq, 5), m1_norm=round(M1 * kq, 4), m2_norm=round(M2 * kq, 4),
                              by_year={str(y): {k: round(v, 4) for k, v in rows_s[y].items()} for y in years})

    # ------------------------------------------------------------------------- 3. структура 2025
    p("\n3. СТРУКТУРА CAPEX 2025 (презентация 2 кв. 2026, с. 24; управленческий capex 46,1 млрд против денежного IAS 17 "
      f"{Y[2025]['capex']:.2f})")
    CAPEX25 = 46.1
    STRUCT = {"openings": 0.487, "reconstructions": 0.122, "maint_operation": 0.111, "it": 0.111, "logistics": 0.076, "other": 0.093}
    assert abs(sum(STRUCT.values()) - 1.0) < 1e-9
    R25 = Y[2025]["revenue"]
    s25 = {k: CAPEX25 * v for k, v in STRUCT.items()}
    for k, v in s25.items():
        p(f"  {k:<16} {v:6.2f} млрд = {pct(v / R25)} выручки 2025")
    non_open = CAPEX25 - s25["openings"]
    p(f"  без открытий: {non_open:.2f} млрд = {pct(non_open / R25)}; без открытий и логистики: {non_open - s25['logistics']:.2f} = "
      f"{pct((non_open - s25['logistics']) / R25)}")
    OUT["structure_2025"] = {k: round(v, 3) for k, v in s25.items()}

    # ------------------------------------------------------------------------- цены
    # ИПЦ Росстата, декабрь к декабрю (2024–2025 сверены с таблицей ЦБ research/_work07/cbr_infl_2024-2026.html;
    # 2026 — июнь 2026 г/г 6,02 %, там же). До 2024 — официальный ряд Росстата.
    CPI = {2008: 13.28, 2009: 8.80, 2010: 8.78, 2011: 6.10, 2012: 6.58, 2013: 6.45, 2014: 11.36, 2015: 12.91, 2016: 5.38,
           2017: 2.52, 2018: 4.27, 2019: 3.05, 2020: 4.91, 2021: 8.39, 2022: 11.94, 2023: 7.42, 2024: 9.52, 2025: 5.59}
    CPI_2026_YOY_JUN = 6.02
    F25_26 = 1 + CPI_2026_YOY_JUN / 100            # цены 2025 года (середина) → цены 2026 года (середина)
    F24_25 = 1 + CPI[2024] / 100

    # ------------------------------------------------------------------------- 4. открытия → capex на м²
    p("\n4. CAPEX ОТКРЫТИЯ ПО ФОРМАТАМ (калибровка по открытиям 2025 года; предоткрытия нет — оно в EBITDA)")
    # валовые открытия 2025 (штук, м² на новый магазин) — центр [диапазон]; основания в README §4
    OPEN25 = {
        "conv": dict(n=1040, m2=225, n_rng=(1000, 1080), m2_rng=(215, 240)),
        "droge": dict(n=400, m2=130, n_rng=(360, 430), m2_rng=(125, 135)),
        "super": dict(n=34, m2=700, n_rng=(30, 38), m2_rng=(600, 800)),
        "hyper": dict(n=4, m2=2500, n_rng=(3, 5), m2_rng=(2500, 4000)),
        "vingarazh": dict(n=80, m2=90, n_rng=(78, 90), m2_rng=(80, 115)),
    }
    # относительная стоимость м² нового магазина по форматам к «у дома» (безразмерно; суждение листа, ориентир —
    # раскрытые удельные цены публичного аналога, README §4). Уровень задаёт только capex открытий «Ленты» 2025.
    W = {"conv": 1.0, "droge": 0.875, "super": 1.25, "hyper": 1.25, "vingarazh": 1.0}
    open_bn = s25["openings"]

    def calib(o):
        wa = sum(v["n"] * v["m2"] * W[f] for f, v in o.items())                  # взвешенные м²
        return open_bn * 1e6 / wa                                                 # тыс. ₽/м² «у дома», цены 2025
    kL = calib(OPEN25)
    area25 = {f: v["n"] * v["m2"] / 1000 for f, v in OPEN25.items()}
    p(f"  валовая площадь открытий 2025, тыс. м²: " + ", ".join(f"{f} {a:.1f}" for f, a in area25.items()) + f"; всего {sum(area25.values()):.1f}")
    p(f"  capex открытий 2025 = 48,7 % × 46,1 = {open_bn:.2f} млрд → «у дома» {kL:.1f} тыс. ₽/м² (цены 2025)")
    # разброс по крайним значениям числа и площади
    lo = {f: dict(n=v["n_rng"][1], m2=v["m2_rng"][1]) for f, v in OPEN25.items()}
    hi = {f: dict(n=v["n_rng"][0], m2=v["m2_rng"][0]) for f, v in OPEN25.items()}
    k_lo, k_hi = calib(lo), calib(hi)
    net_fr = HERE.parents[3] / "book-draft" / "fragments" / "network.yaml"
    if net_fr.exists():
        import yaml as _y
        nf = _y.safe_load(net_fr.read_text(encoding="utf-8"))["facts"]["segments"]
        g25 = {f: sum((nf[f].get("new_area_gross_hist") or [0, 0, 0, 0])[1:3]) for f in ("hyper", "super", "conv", "droge", "remi")}
        k_net = open_bn * 1000 / sum(g25[f] * W["super" if f == "remi" else f] for f in g25)
        p(f"  сверка с листом network (валовые открытия 2025 из new_area_gross_hist, тыс. м²): " + ", ".join(f"{f} {v:.1f}" for f, v in g25.items())
          + f"; всего {sum(g25.values()):.1f} против {sum(area25.values()):.1f} здесь → «у дома» {k_net:.1f} против {kL:.1f} тыс. ₽/м²")
        OUT["network_crosscheck_k"] = round(k_net, 4)
    per_m2_25 = {f: W[f] * kL for f in W}                 # тыс. ₽/м², цены 2025
    per_m2_26 = {f: W[f] * kL * F25_26 for f in W}        # цены 2026
    for f in W:
        p(f"  {f:<10} {per_m2_25[f]:6.1f} тыс. ₽/м² (2025) → {per_m2_26[f]:6.1f} (2026); на магазин {OPEN25[f]['m2'] * per_m2_26[f] / 1000:6.1f} млн ₽")
    p(f"  диапазон по числу и площади открытий: «у дома» {k_lo * F25_26:.0f}–{k_hi * F25_26:.0f} тыс. ₽/м² (цены 2026)")
    # сегменты книги (D3): hyper, okey, super, conv (+ «Вингараж»), droge, remi
    GROWTH = {
        "hyper": per_m2_26["hyper"], "okey": per_m2_26["hyper"], "super": per_m2_26["super"],
        "conv": per_m2_26["conv"], "droge": per_m2_26["droge"], "remi": per_m2_26["super"],
    }
    GROWTH["diy"] = 0.5 * per_m2_26["hyper"]                            # DIY-коробка без холода: половина ставки гипер (суждение, C)
    GROWTH = {k: round(v / 1000, 3) for k, v in GROWTH.items()}           # млрд ₽ на тыс. м² (= тыс. ₽/м² / 1000)
    p(f"  growth_capex_per_m2 (млрд ₽ на тыс. м², цены 2026): {GROWTH}")
    # сверка 1П2026: доля открытий по тем же удельным ценам
    OPEN_1H26 = {"conv": (263 + 229, 230), "droge": (101 + 11, 130), "super": (4 + 15, 700), "hyper": (0 + 2, 2500), "vingarazh": (20 + 33, 90)}
    # супер: в 1 кв. 11 открытий, из них 7 — после реконструкции (не новая площадь); во 2 кв. 15 — считаем новыми (верх)
    f_half = 1 + CPI_2026_YOY_JUN / 100 / 2
    open_1h26 = sum(n * m2 * W[f] * kL * f_half for f, (n, m2) in OPEN_1H26.items()) / 1e6
    capex_1h26 = H["2026H1"]["capex"]
    p(f"  сверка 1П2026: открытия ≈ {open_1h26:.2f} млрд из {capex_1h26:.2f} ({open_1h26 / capex_1h26 * 100:.1f} %; в 2025 году — 48,7 %); "
      f"без открытий {capex_1h26 - open_1h26:.2f} млрд = {pct((capex_1h26 - open_1h26) / H['2026H1']['revenue'])} выручки 1П; "
      f"с сезонной поправкой /m1 = {pct((capex_1h26 - open_1h26) / H['2026H1']['revenue'] / M1)}")
    # 2024: открытия (у дома ≈ 690 брутто × 215 м², супер ≈ 15 × 700, гипер 3 × 4 000, «Вингараж» 8 × 90)
    OPEN24 = {"conv": (690, 215), "super": (15, 700), "hyper": (3, 4000), "vingarazh": (8, 90)}
    open_24 = sum(n * m2 * W[f] * kL / F24_25 for f, (n, m2) in OPEN24.items()) / 1e6
    p(f"  2024: открытия ≈ {open_24:.2f} млрд из {Y[2024]['capex']:.2f}; без открытий {Y[2024]['capex'] - open_24:.2f} = "
      f"{pct((Y[2024]['capex'] - open_24) / Y[2024]['revenue'])} выручки (оценка)")
    # контекст: эпоха собственных гипермаркетов 2014–2017 — весь capex на чистый прирост площади (с землёй и зданиями)
    tot_fy = {y: oval(tot_space, f"FY {y}") / 1000 for y in range(2013, 2018)}
    def cpi_to_2026(y):
        f = 1 + CPI_2026_YOY_JUN / 100 / 2
        for yy in range(y + 1, 2026):
            f *= 1 + CPI[yy] / 100
        return f * (1 + CPI[y] / 100) ** 0.5
    era = {}
    for y in range(2014, 2018):
        dn = tot_fy[y] - tot_fy[y - 1] - (42.5 if y == 2016 else 0.0)       # 2016 — без купленных магазинов Kesko (42,5 тыс. м²)
        era[y] = dict(capex=Y[y]["capex"], net_new_k=dn, per_m2=Y[y]["capex"] / dn * 1000, per_m2_2026=Y[y]["capex"] / dn * 1000 * cpi_to_2026(y))
        p(f"  {y}: capex {Y[y]['capex']:.1f} млрд / чистый прирост {dn:.1f} тыс. м² = {era[y]['per_m2']:.0f} тыс. ₽/м² ({era[y]['per_m2_2026']:.0f} в ценах 2026) — собственные гипермаркеты с землёй")
    OUT["owned_hyper_era"] = era
    OUT["openings"] = dict(open_2025_bn=round(open_bn, 3), area_2025_k=area25, conv_cost_2025=round(kL, 3), conv_cost_2025_range=[round(k_lo, 3), round(k_hi, 3)],
                           per_m2_2025=per_m2_25, per_m2_2026=per_m2_26, growth_capex_per_m2=GROWTH,
                           check_1h26=dict(openings_bn=round(open_1h26, 3), capex_bn=round(capex_1h26, 3),
                                           non_open_pct_rev=round((capex_1h26 - open_1h26) / H["2026H1"]["revenue"], 5)),
                           check_2024=dict(openings_bn=round(open_24, 3), non_open_pct_rev=round((Y[2024]["capex"] - open_24) / Y[2024]["revenue"], 5)))

    # ------------------------------------------------------------------------- 5. снизу вверх
    p("\n5. ПОДДЕРЖИВАЮЩИЙ CAPEX СНИЗУ ВВЕРХ (нулевой чистый рост, периметр 30.06.2026, цены 2026)")
    # сеть на 30.06.2026 (датабук, 2 кв. 2026); «О'КЕЙ» — 75 гипермаркетов 478 тыс. м² (МСФО 1П2026 прим. 5, с. 16)
    def area_eop(occ_label_row_offset, col="2Q 2026"):
        return oval(OR[OR.index(tot_space) + occ_label_row_offset], col) / 1000.0
    A = {"hyper_all": area_eop(1), "super": area_eop(2), "conv_only": area_eop(3), "droge": area_eop(4), "remi": area_eop(5),
         "diy": area_eop(6), "other": area_eop(7)}
    OKEY_AREA = 478.0
    AREA = {"hyper": A["hyper_all"] - OKEY_AREA, "okey": OKEY_AREA, "super": A["super"], "conv": A["conv_only"] + A["other"],
            "droge": A["droge"], "remi": A["remi"], "diy": A["diy"]}
    p("  площадь по сегментам, тыс. м²: " + ", ".join(f"{k} {v:.1f}" for k, v in AREA.items()) + f"; всего {sum(AREA.values()):.1f}")
    # выручка-знаменатель: 2026E на проформе = 1П2026 проформа 705,26 (D3) × (1 + m2H), m2H = 1,12 [1,08–1,16]
    REV1H_PF = 648.488 + 66.12 - 9.36
    M2H = 1.12
    REV26 = REV1H_PF * (1 + M2H)
    p(f"  выручка 2026E на проформе: {REV1H_PF:.2f} × (1 + {M2H}) = {REV26:.1f} млрд (расчёт; 2П/1П чистых лет: "
      + ", ".join(f"{y} {H[f'{y}H2']['revenue'] / H[f'{y}H1']['revenue']:.3f}" for y in (2018, 2019, 2020, 2022, 2024)) + ")")

    # параметры реконструкции: стоимость м² (тыс. ₽, цены 2026) и цикл (лет) по уровням
    RATIO_REDES = {"conv": 0.79, "droge": 0.62, "super": 0.68}     # редизайн / новый магазин на магазин (суждение; ориентир — публичный аналог, README §5)
    # средняя площадь действующего магазина (датабук 2 кв. 2026): у дома с «Вингаражом», дрогери, супер
    def avg_store(off_area, off_store_list):
        a = sum(oval(OR[OR.index(tot_space) + o], "2Q 2026") for o in off_area)
        n = sum(oval(OR[OR.index(orow("Total Retail Stores, eop")) + o], "2Q 2026") for o in off_store_list)
        return a / n
    EXIST_M2 = {"conv": avg_store([3, 7], [3, 7]), "droge": avg_store([4], [4]), "super": avg_store([2], [2])}
    # редизайн на МАГАЗИН = доля аналога × стоимость нового магазина «Ленты»; на м² — делением на площадь действующего магазина
    C_RED = {f: per_m2_26[f] * RATIO_REDES[f] * OPEN25[f]["m2"] / EXIST_M2[f] for f in ("conv", "droge", "super")}
    C_RED.update({"remi": C_RED["super"], "hyper": 30.0, "okey": 30.0, "diy": 15.0})
    p("  средняя площадь действующего магазина, м²: " + ", ".join(f"{k} {v:.0f}" for k, v in EXIST_M2.items())
      + "; редизайн на магазин, млн ₽: " + ", ".join(f"{k} {C_RED[k] * EXIST_M2[k] / 1000:.1f}" for k in EXIST_M2))
    LEVELS = {
        "low": dict(cost=0.85, cycle={"hyper": 16, "okey": 16, "super": 12, "conv": 12.5, "droge": 10, "remi": 12.5, "diy": 20},
                    mo=0.85, it=0.0032, other_share=0.25, fleet_cycle=10, dc_eq_cycle=15, dc_bld=0.007, fleet_cost=10.0),
        "base": dict(cost=1.00, cycle={"hyper": 13, "okey": 13, "super": 10, "conv": 10.5, "droge": 9, "remi": 10.5, "diy": 15},
                     mo=1.00, it=0.0042, other_share=0.50, fleet_cycle=8, dc_eq_cycle=12, dc_bld=0.010, fleet_cost=12.0),
        "high": dict(cost=1.00, cycle={"hyper": 10, "okey": 10, "super": 8, "conv": 8, "droge": 7, "remi": 8, "diy": 12},
                     mo=1.20, it=0.0050, other_share=0.75, fleet_cycle=7, dc_eq_cycle=10, dc_bld=0.015, fleet_cost=14.0),
    }
    # закрытия (доля площади в год) — как LT сценария mid листа network (fragments/network.yaml: 1,5 %, дрогери 3 %),
    # чтобы «закрытый и заменённый магазин не проходит реконструкцию» считалось на тех же закрытиях, что и замещающие открытия ядра
    CLOSE = {"hyper": 0.015, "okey": 0.015, "super": 0.015, "conv": 0.015, "droge": 0.030, "remi": 0.015, "diy": 0.015}
    p("  стоимость реконструкции, тыс. ₽/м² (base): " + ", ".join(f"{k} {v:.1f}" for k, v in C_RED.items()))
    # сверка 2025: реконструкции 5,62 млрд = 71 супер × 750 м² (ГО-2025) + гипер + прочие форматы
    rec_super25 = 71 * 0.75 * C_RED["super"] / F25_26 / 1000
    resid25 = s25["reconstructions"] - rec_super25
    known_h25 = 0.9 * resid25 / (C_RED["hyper"] / F25_26 / 1000)
    p(f"  сверка 2025: реконструкции {s25['reconstructions']:.2f} млрд − супер 71 × 750 м² × {C_RED['super'] / F25_26:.1f} тыс. = {rec_super25:.2f} → остаток {resid25:.2f}; "
      f"90 % на гипер при {C_RED['hyper'] / F25_26:.1f} тыс. ₽/м² (2025) = {known_h25:.0f} тыс. м² ≈ {known_h25 / 5.06:.0f} гипермаркетов "
      f"(стационар base при цикле 13 лет — {(A['hyper_all'] - OKEY_AREA) / 13:.0f} тыс. м² в год)")
    # обслуживание и эксплуатация: 5,12 млрд в 2025 году на взвешенную среднюю площадь 2025 года
    WEQ = {"hyper": 1.0, "okey": 1.0, "super": 1.5, "conv": 1.5, "droge": 1.0, "remi": 1.2, "diy": 0.5}
    def avg_area_2025(off):
        r = OR[OR.index(tot_space) + off]
        q = [oval(r, c) / 1000 for c in ("4Q 2024", "1Q 2025", "2Q 2025", "3Q 2025", "4Q 2025")]
        return (q[0] / 2 + q[1] + q[2] + q[3] + q[4] / 2) / 4
    avg25 = {"hyper": avg_area_2025(1), "super": avg_area_2025(2), "conv": avg_area_2025(3) + avg_area_2025(7),
             "droge": avg_area_2025(4), "remi": 107.15 / 12}
    w_area25 = sum(avg25[f] * WEQ[f] for f in avg25)
    mo_unit = s25["maint_operation"] / w_area25 * F25_26               # млрд на тыс. взвеш. м², цены 2026
    w_area26 = sum(AREA[f] * WEQ[f] for f in AREA)
    p(f"  обслуживание и эксплуатация: 5,12 млрд / взвешенная средняя площадь 2025 {w_area25:.0f} тыс. м² × {F25_26:.4f} = "
      f"{mo_unit * 1000:.2f} тыс. ₽/м² → периметр 30.06.2026 ({w_area26:.0f} тыс. взвеш. м²): {mo_unit * w_area26:.2f} млрд")
    # логистика: автопарк 1 200 машин; РЦ 784,6 тыс. м² (собственные — по листу DC Space)
    dc_rows = [r for r in DC if isinstance(r[0], str) and len(r) > 3 and isinstance(r[3], (int, float)) and r[2] in ("own", "rent")]
    dc_own = sum(r[3] for r in dc_rows if r[2] == "own") / 1000
    dc_all = sum(r[3] for r in dc_rows) / 1000
    TRUCKS = 1200
    DC_EQ_COST, DC_BLD_COST = 15.0, 55.0                              # тыс. ₽/м² РЦ (оборудование; здание), цены 2026
    p(f"  РЦ: {len(dc_rows)} шт., {dc_all:.1f} тыс. м², из них собственные {dc_own:.1f} тыс. м² (датабук, лист DC Space)")

    def closure_factor(c, cyc):
        """Доля объёма реконструкций, которую не заменяют закрытия (решение ведущего по аудиту 30.09.2026, п. 15,
        capex-03): середина отрезка [закрывается ровно магазин, которому подошёл срок реконструкции: 1 − c·T;
        закрытия не зависят от возраста: T·(n₀ − c), n₀ = c / (1 − (1 − c)^T) — обновлений в год на м² при
        дискретном обновлении]. Данных о возрасте закрываемых магазинов нет; прежний центр книги стоял на
        крайнем случае 1 − c·T."""
        edge = max(0.0, 1 - c * cyc)
        n0 = c / (1 - (1 - c) ** cyc)
        return 0.5 * (edge + cyc * (n0 - c))

    def stationary(lv):
        L = LEVELS[lv]
        red = {}
        for f in AREA:
            cyc = L["cycle"][f]
            c = C_RED[f] * L["cost"]
            gross = AREA[f] / cyc * c / 1000
            red[f] = gross * closure_factor(CLOSE[f], cyc)                 # закрытый и заменённый магазин не проходит реконструкцию
        mo = mo_unit * w_area26 * L["mo"]
        fleet = TRUCKS * L["fleet_cost"] / L["fleet_cycle"] / 1000
        dc_eq = dc_all * DC_EQ_COST / L["dc_eq_cycle"] / 1000
        dc_bld = dc_own * DC_BLD_COST * L["dc_bld"] / 1000
        it = L["it"] * REV26
        other = s25["other"] / R25 * L["other_share"] * REV26
        items = dict(reconstruction=sum(red.values()), maint_operation=mo, fleet=fleet, dc_equipment=dc_eq, dc_buildings=dc_bld,
                     it=it, other=other)
        tot = sum(items.values())
        phys = items["reconstruction"] + items["maint_operation"] + items["dc_equipment"] + items["dc_buildings"]
        return dict(items=items, by_format=red, total=tot, pct=tot / REV26, phys_share=phys / tot)

    ST = {lv: stationary(lv) for lv in LEVELS}
    for lv, s in ST.items():
        it = s["items"]
        p(f"  {lv:<4}: реконструкции {it['reconstruction']:5.2f} + обсл./экспл. {it['maint_operation']:5.2f} + автопарк {it['fleet']:4.2f} + "
          f"РЦ обор. {it['dc_equipment']:4.2f} + РЦ здания {it['dc_buildings']:4.2f} + ИТ {it['it']:4.2f} + прочее {it['other']:4.2f} = "
          f"{s['total']:5.2f} млрд = {pct(s['pct'])}; физ. доля {s['phys_share']:.3f}")
        p("        реконструкции по форматам: " + ", ".join(f"{f} {v:.2f}" for f, v in s["by_format"].items()))
    p(f"  сравнение: 2025 без открытий {pct(non_open / R25)} (без роста логистики ≈ {pct((non_open - 2.0) / R25)}); "
      f"на м²: base {ST['base']['total'] / sum(AREA.values()) * 1000:.2f} тыс. ₽/м² в год")
    OUT["stationary"] = {lv: dict(items={k: round(v, 3) for k, v in s["items"].items()}, by_format={k: round(v, 3) for k, v in s["by_format"].items()},
                                  total_bn=round(s["total"], 3), pct=round(s["pct"], 5), phys_share=round(s["phys_share"], 4)) for lv, s in ST.items()}
    OUT["bottom_up_inputs"] = dict(area_k=AREA, rev_2026e_pf=round(REV26, 2), rev_1h26_pf=round(REV1H_PF, 3), m2h=M2H,
                                   redesign_cost_k_per_m2=C_RED, levels=LEVELS, closures=CLOSE, equip_weights=WEQ,
                                   mo_unit_k_per_wm2=round(mo_unit * 1000, 4), dc_all_k=dc_all, dc_own_k=dc_own, trucks=TRUCKS)

    # ------------------------------------------------------------------------- 6. путь по когортам
    p("\n6. ПУТЬ 2026–2036 (реконструкции — когорты по году открытия/последней реконструкции; прочие статьи ровные, логистика — разгон)")
    # история площади гипермаркетов «Ленты» (датабук: FY-площадь гипер; 1 кв. 2012 — база «до 2012»)
    hyp_row = OR[OR.index(tot_space) + 1]
    fy = {y: oval(hyp_row, f"FY {y}") / 1000 for y in range(2012, 2025)}
    fy[2011] = oval(hyp_row, "1Q 2012") / 1000
    fy[2025] = oval(hyp_row, "FY2025") / 1000
    add = {y: fy[y] - fy[y - 1] for y in range(2012, 2023)}
    add_neg = sum(fy[y] - fy[y - 1] for y in (2023, 2024, 2025))          # сокращение площади (субаренда) и «Молния» +16,4
    MOLNIYA_HYP = 16.358                                                     # 2 кв. 2025: прирост площади гипер при 5 магазинах «Молнии»
    old_hyp = fy[2011] + add_neg - MOLNIYA_HYP                               # сокращения 2023–2025 списываем на старый блок
    VINT = {}
    VINT["hyper"] = dict(explicit={y: a for y, a in add.items()}, known={2024: round(known_h25 / 2, 1), 2025: round(known_h25, 1)},
                         old=None)                                           # old — остаток до площади сегмента
    VINT["hyper"]["explicit"][2026] = AREA["hyper"] - (fy[2025])             # 1П2026 прирост без «О'КЕЙ»
    # «у дома» (+ «Вингараж»): 2020–2022 ≈ 200 открытий в год × 280 м² (стратегия с. 15), 2023 ≈ 330 × 260 (оценка),
    # 2024 — брутто ≈ 690 × 215, 2025 — брутто 1 040 × 225 + «Вингараж» 7,0, 1П2026 — 492 × 230 + 53 × 90
    VINT["conv"] = dict(explicit={2020: 56.0, 2021: 56.0, 2022: 56.0, 2023: 85.8, 2024: 148.4 + 0.8, 2025: 234.0 + 7.0,
                                  2026: 492 * 0.230 + 53 * 0.090}, known={})
    # супер: реконструкции 2024 ≈ 30 магазинов, 2025 — 71 (ГО-2025), 1П2026 ≈ 12; новые 2025 — 34 × 700, 1П2026 ≈ 19 × 700
    VINT["super"] = dict(explicit={2024: 30 * 0.75, 2025: 71 * 0.75 + 34 * 0.70, 2026: 12 * 0.75 + 19 * 0.70}, known={})
    VINT["droge"] = dict(explicit={2025: 400 * 0.130, 2026: 112 * 0.130}, known={})
    for f in ("okey", "remi", "diy"):
        VINT[f] = dict(explicit={}, known={})
    SPREAD = {"hyper": 3, "okey": 3, "diy": 3, "super": 2, "conv": 2, "droge": 2, "remi": 2}
    YEARS = list(range(2026, 2037))

    def recon_path(lv):
        L = LEVELS[lv]
        out = {f: {t: 0.0 for t in YEARS} for f in AREA}
        diag = {}
        for f in AREA:
            cyc = L["cycle"][f]
            ex = VINT[f]["explicit"]
            old_area = AREA[f] - sum(ex.values())
            due = {t: 0.0 for t in range(1990, 2060)}
            # старый блок: последняя реконструкция равномерно за последний цикл → ровный поток
            for t in YEARS:
                due[t] += old_area / cyc
            sp = SPREAD[f]
            for y, a in ex.items():
                k = 1
                while True:
                    t0 = y + k * cyc
                    if t0 - sp > 2040:
                        break
                    for d in range(-sp, sp + 1):
                        due[int(round(t0)) + d] += a / (2 * sp + 1)
                    k += 1
            backlog = sum(v for t, v in due.items() if t < 2026)
            known = sum(VINT[f]["known"].values())
            back_net = max(0.0, backlog - known)
            nb = 4 if f in ("hyper", "okey") else 2                         # долг до 2026 года — за 4 (гипер) / 2 года
            for i in range(nb):
                due[2026 + i] += back_net / nb
            c = C_RED[f] * L["cost"] / 1000
            fac = closure_factor(CLOSE[f], cyc)
            for t in YEARS:
                out[f][t] = due[t] * c * fac
            diag[f] = dict(old_area=round(old_area, 1), backlog=round(backlog, 1), known=known, backlog_net=round(back_net, 1))
        return out, diag

    PATH = {}
    for lv in LEVELS:
        rp, diag = recon_path(lv)
        s = ST[lv]
        rows = {}
        for t in YEARS:
            recon = sum(rp[f][t] for f in AREA)
            it = s["items"]
            # логистика: 2026 — оценка 2025 года без новых РЦ (≈1,5 млрд в ценах 2026), линейно до стационара к 2030
            log_st = it["fleet"] + it["dc_equipment"] + it["dc_buildings"]
            log_26 = min(log_st, 1.5)
            w = min(1.0, (t - 2026) / 4)
            logi = log_26 + (log_st - log_26) * w
            tot = recon + it["maint_operation"] + logi + it["it"] + it["other"]
            rows[t] = dict(recon=recon, logistics=logi, total=tot, pct=tot / REV26,
                           recon_fmt={f: rp[f][t] for f in AREA})
        # 2036 = LT = стационар по построению (ключ полугодия на последний период не ставится)
        rows[2036] = dict(recon=s["items"]["reconstruction"], logistics=s["items"]["fleet"] + s["items"]["dc_equipment"] + s["items"]["dc_buildings"],
                          total=s["total"], pct=s["pct"], recon_fmt=s["by_format"])
        PATH[lv] = dict(rows=rows, diag=diag)
        p(f"  {lv}: " + "; ".join(f"{t} {100 * rows[t]['pct']:.2f}" for t in YEARS) + " (% выручки 2026E в ценах 2026)")
        p(f"        реконструкции, млрд: " + "; ".join(f"{t} {rows[t]['recon']:.1f}" for t in YEARS))
        p(f"        долг до 2026 (тыс. м²): " + ", ".join(f"{f} {d['backlog_net']}" for f, d in diag.items() if d["backlog_net"] > 0))
        if lv == "base":
            for f in AREA:
                p(f"        {f:<6}" + "; ".join(f"{t} {rows[t]['recon_fmt'][f]:.2f}" for t in YEARS))
    # ключи
    def r4(x): return round(x, 4)
    KEYS = {}
    for lv in LEVELS:
        rows = PATH[lv]["rows"]
        lvl = {t: rows[t]["pct"] for t in YEARS}
        k = {}
        k["2026H2"] = r4(lvl[2026] * M2)
        k["2027H1"] = r4(lvl[2027] * M1 * kq)
        k["2027H2"] = r4(lvl[2027] * M2 * kq)
        for t in range(2027, 2037):
            k[str(t)] = r4(lvl[t])
        k["LT"] = r4(lvl[2036])
        KEYS[lv] = k
    p("  ключи книги (доля выручки): ")
    for lv, k in KEYS.items():
        p(f"    {lv}: {k}")
    # сверка «достройкой года» для 2026: 1П — факт без открытий, 2П — ключ
    rev_2h26 = REV1H_PF * M2H
    for lv in LEVELS:
        y26 = (capex_1h26 - open_1h26) + KEYS[lv]["2026H2"] * rev_2h26
        p(f"    {lv}: поддерживающий capex 2026 = факт 1П без открытий {capex_1h26 - open_1h26:.1f} + 2П {KEYS[lv]['2026H2'] * rev_2h26:.1f} = "
          f"{y26:.1f} млрд ({pct(y26 / (H['2026H1']['revenue'] + rev_2h26))} выручки года; уровень пути 2026 {pct(PATH[lv]['rows'][2026]['pct'])})")
    OUT["path"] = {lv: {str(t): dict(pct=round(r["pct"], 5), total_bn=round(r["total"], 3), recon_bn=round(r["recon"], 3),
                                      logistics_bn=round(r["logistics"], 3)) for t, r in PATH[lv]["rows"].items()} for lv in LEVELS}
    OUT["maintenance_pct"] = KEYS
    OUT["cohort_diag"] = {lv: PATH[lv]["diag"] for lv in LEVELS}

    # сверка со стратегией: весь capex 2027 года при целях открытий стратегии-2028 (у дома 1 000+, дрогери 350+, супер 50+)
    rev27 = REV26 * 1.10
    opn = {"conv": 1000 * 0.225 * GROWTH["conv"], "droge": 350 * 0.130 * GROWTH["droge"], "super": 50 * 0.700 * GROWTH["super"],
           "hyper": 2 * 2.5 * GROWTH["hyper"], "vingarazh": 100 * 0.090 * GROWTH["conv"]}
    net_new = 1000 * 0.225 * 0.95 + 350 * 0.130 * 0.75 + 50 * 0.7 + 5 + 9
    tot27 = KEYS["base"]["2027"] * rev27 + sum(opn.values()) * 1.05 + 0.010 * net_new * 1.05 + 1.0
    p(f"  сверка со стратегией (≤ 5,5 %): 2027 base — поддерживающий {KEYS['base']['2027'] * rev27:.1f} + открытия {sum(opn.values()) * 1.05:.1f} + "
      f"инфраструктура {0.010 * net_new * 1.05:.1f} + интеграция 1,0 = {tot27:.1f} млрд = {pct(tot27 / rev27)} выручки ≈{rev27:.0f} (факт 2025 — 4,19 %)")
    OUT["strategy_check_2027"] = dict(total_bn=round(tot27, 2), pct=round(tot27 / rev27, 5))

    # A-K5 (проверка, не основание): окупаемость открытия по форматам при ГРУППОВОЙ марже
    p("\n  A-K5. Окупаемость открытия (иллюстрация): плотность 2025 по датабуку × 0,85 (новая площадь), разгон 70/90/100 %, "
      "маржа EBITDA IAS 17 7,0 % (аренда внутри), каннибализация 10 %, рост выручки 6 %/год, налог 25 % с EBIT, 12 лет без остатка")
    sales_rows = {"hyper": "Hypermarket", "super": "Supermarkets", "conv": "Convenience stores", "droge": "Drogerie", "other": "Other formats"}
    area_off = {"hyper": 1, "super": 2, "conv": 3, "droge": 4, "other": 7}
    dens = {}
    for f, lab in sales_rows.items():
        rv = oval(orow(lab, 1), "FY2025") / 1000                                           # млрд
        dens[f] = rv / avg_area_2025(area_off[f]) * 1000                                   # тыс. ₽/м² в год
    def irr(cfs):
        lo_, hi_ = -0.9, 3.0
        for _ in range(200):
            mid = (lo_ + hi_) / 2
            v = sum(c / (1 + mid) ** t for t, c in enumerate(cfs))
            lo_, hi_ = (mid, hi_) if v > 0 else (lo_, mid)
        return (lo_ + hi_) / 2
    AK5 = {}
    for f, capf, m2 in (("conv", "conv", 225), ("droge", "droge", 130), ("super", "super", 700), ("other", "vingarazh", 90), ("hyper", "hyper", 2500)):
        capex0 = per_m2_26[capf] * m2 / 1000                                              # млн ₽
        rev_m = dens[f] * 0.85 * m2 / 1000 * 0.90                                          # млн ₽ в год зрелого, после каннибализации
        cfs = [-capex0]
        for yr in range(1, 13):
            ramp = 0.7 if yr == 1 else (0.9 if yr == 2 else 1.0)
            r_ = rev_m * ramp * 1.06 ** (yr - 1)
            e = 0.07 * r_
            tax = 0.25 * max(0.0, e - capex0 / 12)
            cfs.append(e - tax)
        AK5[f] = dict(density_k=round(dens[f], 1), capex_mn=round(capex0, 2), rev_mature_mn=round(rev_m, 2), irr=round(irr(cfs), 4),
                      payback_years=round(capex0 / (0.07 * rev_m), 2))
        p(f"    {f:<6}: плотность {dens[f]:5.0f} тыс. ₽/м²; capex {capex0:6.1f} млн; зрелая выручка {rev_m:6.1f} млн; простая окупаемость {capex0 / (0.07 * rev_m):4.1f} г.; IRR {100 * irr(cfs):5.1f} %")
    p("    ставка дисконтирования активов ≈16–19 % номинально: при групповой марже «Улыбка» не окупается — открытия дрогери разрушают стоимость в модели,"
      " если маржа формата не выше групповой (компания: «+2 п.п. EBITDA margin» дрогери за 2 года, стратегия с. 23)")
    OUT["ak5"] = AK5

    # ------------------------------------------------------------------------- 7. физическая доля
    # физические статьи на м² по форматам (решение ведущего по аудиту 30.09.2026, п. 11, capex-04): зрелый м² —
    # реконструкции стационара base своего формата + обслуживание/эксплуатация (удельная цена × вес формата WEQ) + РЦ;
    # молодой м² — без реконструкций (первая — через цикл формата); ядро (capex.physical) делит их на среднее по
    # эталонной сети: эталонная сеть идёт путём A-K1, новая площадь — когортами по весу своего формата.
    dc_m2 = (ST["base"]["items"]["dc_equipment"] + ST["base"]["items"]["dc_buildings"]) / sum(AREA.values())
    PHYS = {f: dict(steady=round((ST["base"]["by_format"][f] / AREA[f] + mo_unit * WEQ[f] + dc_m2) * 1000, 2),
                    young=round((mo_unit * WEQ[f] + dc_m2) * 1000, 2)) for f in AREA}
    OUT["physical"] = dict(per_m2=PHYS, reconstruction_cycle_years={lv: dict(LEVELS[lv]["cycle"]) for lv in LEVELS},
                           dc_per_m2_k=round(dc_m2 * 1000, 4), mo_unit_k=round(mo_unit * 1000, 4))
    p("  физические статьи на м² в год (base, тыс. ₽): зрелый м² — " + ", ".join(f"{f} {v['steady']:.2f}" for f, v in PHYS.items())
      + "; молодой (без реконструкций) — " + ", ".join(f"{f} {v['young']:.2f}" for f, v in PHYS.items()))
    s_phys = round(ST["base"]["phys_share"], 2)
    p(f"\n7. ФИЗИЧЕСКАЯ ДОЛЯ: base {ST['base']['phys_share']:.3f} (low {ST['low']['phys_share']:.3f}, high {ST['high']['phys_share']:.3f}) → "
      f"maintenance_area_share = {s_phys}")
    OUT["maintenance_area_share"] = s_phys

    # ------------------------------------------------------------------------- 8. сверки
    p("\n8. СВЕРКИ")
    def infl_from(age):
        """индекс цен от момента age лет назад до середины 2026 года"""
        idx = 1.0
        t = age
        step = min(t, 0.5)                                          # 1П2026: половина годового ИПЦ
        idx *= (1 + CPI_2026_YOY_JUN / 100) ** step
        t -= step
        yr = 2025
        while t > 1e-9:
            st_ = min(t, 1.0)
            idx *= (1 + CPI.get(yr, 8.0) / 100) ** st_
            t -= st_
            yr -= 1
        return idx

    def repl(classes, rev, label):
        tot = 0.0
        det = {}
        for k, (g, a, d_year, life_override) in classes.items():
            life = life_override or g / d_year
            age = a / g * life
            n = 50
            ix = sum(infl_from(2 * age * (j + 0.5) / n) for j in range(n)) / n
            rep = g * ix / life
            tot += rep
            det[k] = dict(gross=g, life=round(life, 1), age=round(age, 1), index=round(ix, 3), repl_bn=round(rep, 2))
            p(f"     {k:<14} ПС {g:6.1f}; срок {life:4.1f} г.; ср. возраст {age:4.1f}; индекс {ix:.3f}; замещение {rep:5.2f} млрд = {pct(rep / rev)}")
        p(f"     {label}: ИТОГО {tot:.2f} млрд = {pct(tot / rev)} при учётных (подразумеваемых) сроках; при сроках +30 % — {pct(tot / 1.3 / rev)}")
        return tot, det
    p("  8а. Стоимость замещения по классам ОС (метод 850oa: срок = ПС / амортизация, возраст = накопл./ПС × срок, индекс ИПЦ):")
    p("     30.06.2026 (прим. 4 и 10 МСФО 1П2026; амортизация полугодия × 2; «О'КЕЙ» в справедливой стоимости — занижает возраст и ПС):")
    cls26 = {"здания": (210.964481, 69.769444, 6.195750 * 2, None), "машины и обор.": (137.921505, 78.736649, 6.723915 * 2, None),
             "благоустройство": (14.161425, 13.656043, 0.164983 * 2, 7.0), "ПО (НМА)": (25.495328, 10.933288, 1.568013 * 2, None)}
    rep26, det26 = repl(cls26, REV26, "30.06.2026 на выручке 2026E")
    p("     31.12.2025 (прим. 7 и 13 МСФО 2025; периметр без «О'КЕЙ» и «Дом Ленты»):")
    cls25 = {"здания": (179.682413, 63.759417, 10.427275, None), "машины и обор.": (127.349575, 73.800509, 11.388050, None),
             "благоустройство": (14.145449, 13.492215, 0.361978, 7.0), "ПО (НМА)": (21.992124, 9.385259, 2.121848, None)}
    rep25, det25 = repl(cls25, R25, "31.12.2025 на выручке 2025")
    repl_open = {f: CLOSE[f] * AREA[f] * {"hyper": GROWTH["hyper"], "okey": GROWTH["okey"], "super": GROWTH["super"], "conv": GROWTH["conv"],
                                            "droge": GROWTH["droge"], "remi": GROWTH["remi"], "diy": GROWTH["diy"]}[f] for f in AREA}
    ro = sum(repl_open.values())
    model_cov = ST["base"]["total"] + ro                                       # охват сверки: A-K1 + замещающие открытия
    p(f"     охват сверки = A-K1 base {ST['base']['total']:.2f} + замещающие открытия {ro:.2f} "
      f"(закрытия × capex/м²) = {model_cov:.2f} млрд = {pct(model_cov / REV26)} против коридора {pct(rep26 / 1.3 / REV26)}–{pct(rep26 / REV26)}")
    OUT["crosscheck_replacement"] = dict(at_2026h1=dict(classes=det26, total_bn=round(rep26, 2), pct=[round(rep26 / 1.3 / REV26, 5), round(rep26 / REV26, 5)]),
                                         at_2025=dict(classes=det25, total_bn=round(rep25, 2), pct=[round(rep25 / 1.3 / R25, 5), round(rep25 / R25, 5)]),
                                         model_ak1_plus_repl_open=dict(bn=round(model_cov, 2), pct=round(model_cov / REV26, 5), repl_open_bn=round(ro, 3)))
    # 8а′. Стационар base — у ЦЕНТРА собственных стационарных свидетельств книги (решение ведущего по аудиту 30.09.2026,
    # п. 13, capex-01): середина коридора стоимости замещения 30.06.2026 (сроки +30 % … учётные) за вычетом замещающих
    # открытий (они в A-K3); high — тем же сдвигом в рублях; путь 2026–2027 не трогается (его подтверждает история
    # без открытий), подъём к стационару — линейно 2028–2036. Расчёт снизу вверх остаётся путём и формой уровней;
    # low — снизу вверх (решение п. 26: ограничение «низкий уровень без догоняющих реконструкций» — в разделе 16 книги).
    corr_mid = (rep26 / 1.3 + rep26) / 2
    steady_target = corr_mid - ro
    steady_shift = steady_target - PATH["base"]["rows"][2036]["total"]
    for lv in ("base", "high"):
        for t in range(2028, 2037):
            row = PATH[lv]["rows"][t]
            row["bottom_up_total"] = row["total"]
            row["total"] += steady_shift * (t - 2027) / 9
            row["pct"] = row["total"] / REV26
    for lv in ("base", "high"):
        rows = PATH[lv]["rows"]
        KEYS[lv].update({str(t): r4(rows[t]["pct"]) for t in range(2028, 2037)})
        KEYS[lv]["LT"] = r4(rows[2036]["pct"])
    OUT["path"] = {lv: {str(t): dict(pct=round(r["pct"], 5), total_bn=round(r["total"], 3), recon_bn=round(r["recon"], 3),
                                      logistics_bn=round(r["logistics"], 3),
                                      bottom_up_total_bn=round(r.get("bottom_up_total", r["total"]), 3))
                        for t, r in PATH[lv]["rows"].items()} for lv in LEVELS}
    OUT["maintenance_pct"] = KEYS
    OUT["steady_centre"] = dict(corridor_bn=[round(rep26 / 1.3, 3), round(rep26, 3)], corridor_mid_bn=round(corr_mid, 3),
                                repl_open_bn=round(ro, 3), steady_target_bn=round(steady_target, 3),
                                shift_bn=round(steady_shift, 3), shift_pct=round(steady_shift / REV26, 5),
                                bottom_up_2036_base_bn=round(PATH["base"]["rows"][2036]["bottom_up_total"], 3))
    p(f"  8а′. стационар base — середина коридора замещения {corr_mid:.2f} млрд ({pct(corr_mid / REV26)}) − замещающие открытия "
      f"{ro:.2f} = {steady_target:.2f} млрд ({pct(steady_target / REV26)}) против снизу вверх "
      f"{PATH['base']['rows'][2036]['bottom_up_total']:.2f}: сдвиг {steady_shift:+.2f} млрд ({100 * steady_shift / REV26:+.3f} п.п.) "
      f"для base и high, линейно 2028–2036; ключи: base LT {KEYS['base']['LT']}, high LT {KEYS['high']['LT']}")
    # 8б. тождество capex/D&A
    p("  8б. Тождество стационарной сети: capex/D&A = 1 / ratio(g, L), ratio = (1 − (1+g)^−L)/(L·g):")
    def ratio(g, L): return (1 - (1 + g) ** (-L)) / (L * g)
    tbl = {}
    for L in (10, 12, 14):
        tbl[L] = {g: round(1 / ratio(g, L), 3) for g in (0.04, 0.056, 0.08, 0.10)}
        p(f"     L = {L}: " + ", ".join(f"g {100 * g:.1f} % → {v:.2f}×" for g, v in tbl[L].items()))
    p("     история capex/D&A: " + ", ".join(f"{y} {Y[y]['capex'] / Y[y]['da']:.2f}" for y in years) + f", 1П2026 {capex_1h26 / H['2026H1']['da']:.2f}")
    p("     2019–2023: 0,5–0,7× — сеть недоинвестировала (capex 1,7–2,0 % выручки при D&A 3,0–3,5 %); правило «capex = k × D&A» не основание")
    OUT["crosscheck_capex_da"] = {str(L): {str(g): v for g, v in t.items()} for L, t in tbl.items()}
    # 8в. история
    p("  8в. История (не основание уровня): денежный capex, % выручки, с открытиями: "
      + ", ".join(f"{y} {100 * Y[y]['capex'] / Y[y]['revenue']:.2f}" for y in range(2019, 2026)) + f", 1П2026 {pct(capex_1h26 / H['2026H1']['revenue'])}")
    p(f"     без открытий: 2025 {pct(non_open / R25)} (структура компании); 2024 ≈ {pct((Y[2024]['capex'] - open_24) / Y[2024]['revenue'])}; "
      f"1П2026 ≈ {pct((capex_1h26 - open_1h26) / H['2026H1']['revenue'])} (сезонно ≈ {pct((capex_1h26 - open_1h26) / H['2026H1']['revenue'] / M1)})")
    p("     2020–2023 весь capex 1,7–2,0 % — верхняя граница поддерживающего в годы «голодного» capex (сеть почти не росла органически)")

    # ------------------------------------------------------------------------- 9. срок службы, D&A
    p("\n9. СРОК СЛУЖБЫ И D&A")
    # подразумеваемые сроки 2025: амортизация / средняя ПС
    def implied(g0, g1, d): return (g0 + g1) / 2 / d
    lives = {"здания": implied(164.190402, 179.682413, 10.427275), "машины и обор.": implied(106.724937, 127.349575, 11.388050),
             "ПО (НМА)": implied(15.717320, 21.992124, 2.121848)}
    p("  подразумеваемые сроки 2025 (средняя ПС / амортизация): " + ", ".join(f"{k} {v:.1f} г." for k, v in lives.items())
      + "; учётная политика: здания до 30, благоустройство 7, машины 2–15")
    # структура ввода 2025: здания (поступление + перевод из НЗС), машины, благоустройство, ПО
    mix = {"здания": 12.999112 + 3.576783, "машины и обор.": 1.088979 + 24.154634, "благоустройство": 0.262998, "ПО (НМА)": 6.176443}
    tm = sum(mix.values())
    life_mix = {"здания": lives["здания"], "машины и обор.": lives["машины и обор."], "благоустройство": 7.0, "ПО (НМА)": lives["ПО (НМА)"]}
    def pv_sl(L, r):
        """PV (на момент ввода) линейной амортизации 1 ₽ за L лет, полугодовой шаг, ставка r годовая"""
        n = int(round(2 * L))
        rh = (1 + r) ** 0.5 - 1
        return sum((1 / n) / (1 + rh) ** (i + 0.5) for i in range(n))
    res_life = {}
    for r in (0.14, 0.17, 0.20):
        target = sum(mix[k] / tm * pv_sl(life_mix[k], r) for k in mix)
        # ищем L с тем же PV
        Ls = [x / 2 for x in range(8, 61)]
        best = min(Ls, key=lambda L: abs(pv_sl(L, r) - target))
        res_life[r] = best
    harm = 1 / sum(mix[k] / tm / life_mix[k] for k in mix)
    p(f"  структура ввода 2025, млрд: " + ", ".join(f"{k} {v:.2f} ({v / tm * 100:.0f} %)" for k, v in mix.items()))
    p(f"  срок, эквивалентный по PV амортизационного щита: r 14 % → {res_life[0.14]} г.; r 17 % → {res_life[0.17]} г.; r 20 % → {res_life[0.20]} г.; "
      f"по темпу амортизации (гармоническое) {harm:.1f} г.")
    # учётные сроки вместо подразумеваемых (здания 30)
    life_pol = dict(life_mix, **{"здания": 30.0})
    tgt = sum(mix[k] / tm * pv_sl(life_pol[k], 0.17) for k in mix)
    L_pol = min([x / 2 for x in range(8, 61)], key=lambda L: abs(pv_sl(L, 0.17) - tgt))
    p(f"  при учётном сроке зданий 30 лет (остальное как выше), r 17 %: {L_pol} г.")
    ASSET_LIFE = 12
    OUT["asset_life"] = dict(implied_lives=lives, mix_2025=mix, pv_equivalent={str(k): v for k, v in res_life.items()}, harmonic=round(harm, 2),
                             pv_equiv_policy_buildings30=L_pol, chosen=ASSET_LIFE)
    # D&A якоря
    da_1h26 = H["2026H1"]["da"]
    tm_amort = 0.810496
    okey_bld, okey_me = 24.854884 * 30.387441 / 32.303120, 4.370134 * 30.387441 / 32.303120   # доля «О'КЕЙ» в приобретённых ОС
    def okey_da5(lb, lm): return (okey_bld / lb + okey_me / lm) * 5 / 12                # земля (≈2,8) не амортизируется
    da_ok_c, da_ok_lo, da_ok_hi = okey_da5(25, 5.5), okey_da5(35, 7), okey_da5(18, 4)
    p(f"  D&A до МСФО 16 1П2026 (датабук): {da_1h26:.3f} млрд = {pct(da_1h26 / H['2026H1']['revenue'])}; в т.ч. амортизация торговых марок (ППА, прим. 10) {tm_amort:.3f}")
    p(f"  «О'КЕЙ» январь–май (ОС по СС 30,39 млрд, прим. 5; здания ≈{okey_bld:.1f}, машины ≈{okey_me:.1f}; сроки 18–35 / 4–7 лет): "
      f"{da_ok_c:.2f} [{da_ok_lo:.2f}–{da_ok_hi:.2f}] млрд → D&A 1П2026 на проформе ≈ {da_1h26 + da_ok_c:.2f} [{da_1h26 + da_ok_lo:.2f}–{da_1h26 + da_ok_hi:.2f}]")
    grp_rate = da_1h26 / (200.505014 + 25.839484)                           # D&A полугодия / (ОС + НМА на 31.12.2025)
    p(f"  альтернатива «темп D&A группы × ОС «О'КЕЙ»»: {grp_rate:.3f} за полугодие × 31,05 × 5/6 = {grp_rate * 31.049 * 5 / 6:.2f} млрд — "
      f"завышает: земля не амортизируется, 77 % ОС «О'КЕЙ» — здания, у группы в темпе НМА (торговые марки, ПО) и короткое оборудование")
    OUT["da_anchor"] = dict(reported=round(da_1h26, 3), trademark_amort_1h26=tm_amort, okey_5m=dict(center=round(da_ok_c, 3), low=round(da_ok_lo, 3), high=round(da_ok_hi, 3)),
                            pro_forma=round(da_1h26 + da_ok_c, 3), capex_1h26=round(capex_1h26, 3))

    # ------------------------------------------------------------------------- 10. выбытие
    p("\n10. ПОСТУПЛЕНИЯ ОТ ВЫБЫТИЯ ОС (в FCFF — поступления минус прибыль от выбытия, т. е. остаточная стоимость: прибыль уже в EBITDA)")
    for y in range(2014, 2026):
        v = Y[y]
        p(f"  {y}: поступления {v['proceeds']:.3f}, убыток(+)/прибыль(−) {v['disposal_loss']:+.3f} → {v['proceeds_net_of_gain']:.3f} млрд = {pct(v['proceeds_net_of_gain'] / v['revenue'], 3)}")
    h = H["2026H1"]
    p(f"  1П2026: {h['proceeds']:.3f}, {h['disposal_loss']:+.3f} → {h['proceeds_net_of_gain']:.3f} = {pct(h['proceeds_net_of_gain'] / h['revenue'], 3)}")
    def wavg(ys, with_h1=True):
        num = sum(Y[y]["proceeds_net_of_gain"] for y in ys) + (h["proceeds_net_of_gain"] if with_h1 else 0)
        den = sum(Y[y]["revenue"] for y in ys) + (h["revenue"] if with_h1 else 0)
        return num / den
    w_rec, w_22, w_ex23 = wavg([2024, 2025]), wavg([2022, 2023, 2024, 2025]), wavg([2022, 2024, 2025])
    p(f"  средневзвешенно: 2024–1П2026 {pct(w_rec, 3)}; 2022–1П2026 {pct(w_22, 3)} (2023 — волна закрытий «Мини Лент»); 2022–1П2026 без 2023 {pct(w_ex23, 3)}")
    DISP = 0.0005
    p(f"  → disposal_proceeds_pct = {DISP} (диапазон 0,0003–0,0008); продажи с обратной арендой не найдены (собственная площадь гипер 2024–2026 падала переводом в инвест. недвижимость, прим. 7)")
    OUT["disposal"] = dict(w_2024_1h26=round(w_rec, 6), w_2022_1h26=round(w_22, 6), w_2022_1h26_ex2023=round(w_ex23, 6), chosen=DISP)

    # ------------------------------------------------------------------------- 11. интеграция
    p("\n11. ИНТЕГРАЦИОННЫЙ CAPEX «О'КЕЙ» (сверх A-K1)")
    INTEG = {"2026H2": 1.0, "2027H1": 0.5, "2027H2": 0.5}
    p(f"  путь, млрд ₽ (номинал): {INTEG}; ребрендинг ≈1 млрд (Коммерсантъ, вторичный; research/03 §6.3) до конца 2026 (релиз 02.06.2026) + догоняющие "
      f"вложения в оборудование 75 магазинов 1,0 млрд в 2027 (суждение: 478 тыс. м² × ≈2 тыс. ₽/м²); «Дом Лента» — ребрендинг завершён в 1 кв. 2026")
    p(f"  обязательства по капвложениям выросли 9,22 → 11,37 млрд за 1П2026 (+2,15; прим. 31) — совместимо")
    OUT["integration_capex"] = INTEG

    # ------------------------------------------------------------------------- 12. цена ошибки
    p("\n12. ЦЕНА ОШИБКИ (правило до ядра: 1 млрд требований ≈ 7,8 ₽/акц. при g 10 %; 1 % EV ≈ 26–27 ₽; акций 115,074675 млн)")
    SH, G = 115.074675, 0.10
    def rub(dv0): return dv0 * (1 - G) / SH * 1000
    REV27 = REV26 * 1.10
    shield = 0.25 * pv_sl(ASSET_LIFE, 0.17)
    net = 1 - shield
    def perp(dpct, rmg): return dpct * net * REV27 / rmg
    costs = {}
    for rmg in (0.09, 0.11, 0.13):
        costs[str(rmg)] = round(rub(perp(0.001, rmg)), 1)
    p(f"  +0,1 п.п. поддерживающего capex навсегда: ΔV0 = 0,1 % × (1 − щит {shield:.3f}) × выручка 2027 {REV27:.0f} / (r − g) → "
      + ", ".join(f"r−g {100 * float(k):.0f} п.п.: −{v} ₽" for k, v in costs.items()))
    # capex открытия: ±10 тыс. ₽/м² на валовые открытия ~330 тыс. м² в год 10 лет + замещение в терминале
    gross_k = sum(area25.values())
    ann = sum(1 / 1.17 ** (i + 0.5) for i in range(10))
    close_k = sum(CLOSE[f] * AREA[f] for f in AREA)                        # тыс. м² закрытий в год (замещение в терминале)
    dv_open = 0.010 * gross_k * net * ann + 0.010 * close_k * net / 0.11 / 1.17 ** 10
    p(f"  ±10 тыс. ₽/м² capex открытия (≈{gross_k:.0f} тыс. м² брутто в год 10 лет + замещение в терминале): ΔV0 ≈ {dv_open:.1f} млрд ≈ ∓{rub(dv_open):.0f} ₽")
    dv_int = 1.0 / 1.17 ** 0.5
    p(f"  ±1 млрд интеграционного capex в 2026H2–2027: ≈ ∓{rub(dv_int):.1f} ₽")
    dv_disp = 0.0002 * net / net * REV27 / 0.11
    p(f"  ±0,02 п.п. выручки поступлений от выбытия: ≈ ±{rub(dv_disp):.0f} ₽")
    # срок службы: 10 против 12 лет — разница PV щита на поддерживающий+ростовой capex ≈ 2,5–3 % выручки
    dsh = 0.25 * (pv_sl(10, 0.17) - pv_sl(12, 0.17)) * 0.03 * REV27 / 0.11
    p(f"  срок 10 лет вместо 12 (щит на capex ≈3 % выручки): ≈ +{rub(dsh):.0f} ₽; 14 вместо 12: ≈ −{rub(0.25 * (pv_sl(12, 0.17) - pv_sl(14, 0.17)) * 0.03 * REV27 / 0.11):.0f} ₽")
    OUT["cost_of_error"] = dict(maint_plus_0_1pp_rub=costs, openings_10k_per_m2_rub=round(rub(dv_open), 1), integration_1bn_rub=round(rub(dv_int), 1),
                                disposal_0_02pp_rub=round(rub(dv_disp), 1), life_10_vs_12_rub=round(rub(dsh), 1))

    # ------------------------------------------------------------------------- P(capex | режим)
    PCR = {"stress": {"low": 0.35, "base": 0.50, "high": 0.15}, "floor": {"low": 0.20, "base": 0.60, "high": 0.20},
           "partial": {"low": 0.20, "base": 0.60, "high": 0.20}, "full": {"low": 0.15, "base": 0.60, "high": 0.25}}
    for r, d in PCR.items():
        assert abs(sum(d.values()) - 1) < 1e-12
    OUT["capex_prob_given_regime"] = PCR
    # ожидаемый уровень при равных режимах (иллюстрация; вероятности режимов — лист маржи)
    eq = {lv: sum(PCR[r][lv] for r in PCR) / 4 for lv in ("low", "base", "high")}
    exp_lt = sum(eq[lv] * KEYS[lv]["LT"] for lv in eq)
    p(f"\nP(capex | режим): {PCR}; при равных режимах безусловно {eq} → ожидаемый LT {pct(exp_lt)}")

    # ------------------------------------------------------------------------- 13. фрагмент
    write_fragment(KEYS, s_phys, GROWTH, DISP, ASSET_LIFE, INTEG, PCR, OUT, ST, REV26)
    OUT["fail"] = FAIL
    OUT_JSON.write_text(json.dumps(OUT, ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    p(f"\nзаписано: {OUT_JSON.name}, book-draft/fragments/{FRAGMENT.name}")
    if FAIL:
        p("ПРОВАЛЫ ЦИТАТ: " + "; ".join(FAIL))
        return 1
    p("все цитаты найдены")
    return 0


def fmt_path(k: dict) -> str:
    parts = []
    for key, v in k.items():
        kk = key if key == "LT" else f'"{key}"'
        parts.append(f"{kk}: {v}")
    return "{" + ", ".join(parts) + "}"


def write_fragment(KEYS, s_phys, GROWTH, DISP, LIFE, INTEG, PCR, OUT, ST, REV26):
    sea = OUT["seasonality"]
    b, l, h = ST["base"], ST["low"], ST["high"]
    txt = f"""# Фрагмент машинной книги 1.0 «Ленты» — раздел capex (лист evidence/book-1.0/capex/, скрипт capex_book10.py).
# Генерируется скриптом; руками не править — менять параметры листа и перезапускать.
# Единицы: доли выручки; capex на м² — млрд ₽ на тыс. м² торговой площади (= тыс. ₽/м² / 1000), цены 2026 года, индексация ИПЦ мира.
# Базис IAS 17 (денежный capex = покупка ОС + НМА + права аренды по ДДС до МСФО 16). Предоткрытие — в EBITDA, не в capex.
capex:
  maintenance_pct:     # A-K1: поддерживающий capex БЕЗ открытий (замещающие открытия — gross_open × growth_capex_per_m2), доля выручки.
                       # Снизу вверх при нулевом чистом росте, периметр 30.06.2026 (с «О'КЕЙ», «Дом Лентой», «Реми»), цены 2026, знаменатель — выручка 2026E
                       # на проформе {REV26:.0f} млрд. Статьи base в стационаре: реконструкции {b['items']['reconstruction']:.1f} + обслуживание/эксплуатация {b['items']['maint_operation']:.1f}
                       # + автопарк {b['items']['fleet']:.1f} + РЦ {b['items']['dc_equipment'] + b['items']['dc_buildings']:.1f} + ИТ {b['items']['it']:.1f} + прочее {b['items']['other']:.1f} = {b['total']:.1f} млрд.
                       # Путь 2026–2035 — когорты реконструкций (волна гипермаркетов 2014–2017 годов открытия, пауза молодой «Монетки»); 2036 = LT = стационар.
                       # Ключи полугодий: 2026H2 = уровень 2026 × m2 ({sea['m2']}); 2027 — × {sea['m1_norm']} (1П) и × {sea['m2_norm']} (2П), год = ключу 2027;
                       # m1/m2 — денежный capex 2014–2025 без лет с 2П < 1П ({sea['excluded']}). Ключ полугодия на последний период НЕ ставится (терминал читает его как LT).
    low:  {fmt_path(KEYS['low'])}   # стационар {100 * l['pct']:.2f} %: циклы длиннее (гипер 16, у дома 12,5, супер 12 лет), объём ×0,85, ИТ 0,32 %
    base: {fmt_path(KEYS['base'])}   # стационар {100 * b['pct']:.2f} %: гипер 13, у дома 10,5, супер 10, дрогери 9 лет; ИТ 0,42 %
    high: {fmt_path(KEYS['high'])}   # стационар {100 * h['pct']:.2f} %: гипер 10, у дома 8, супер 8 лет; обслуживание ×1,2; ИТ 0,50 %
  maintenance_area_share: {s_phys}   # A-K1 (доля): реконструкции + обслуживание/эксплуатация + РЦ = {b['phys_share']:.3f} base (low {l['phys_share']:.2f}, high {h['phys_share']:.2f}); ИТ, автопарк, прочее — доля выручки
  segments:            # A-K3: capex открытия, млрд ₽ на тыс. м² (цены 2026), без предоткрытия. Уровень — capex открытий 2025 (48,7 % × 46,1 млрд,
                       # презентация 2кв26 с. 24) на валовые открытия 2025 года по форматам; отношения форматов — удельные цены аналога. Диапазон ±15 %.
    hyper: {{growth_capex_per_m2: {GROWTH['hyper']}}}   # новый арендный гипер XS/«Семья» 2,5 тыс. м²; собственный классический был бы ×2,5–3
    okey:  {{growth_capex_per_m2: {GROWTH['okey']}}}   # как hyper; новых «О'КЕЙ» нет (ребрендинг в «Гипер Ленту»)
    super: {{growth_capex_per_m2: {GROWTH['super']}}}
    conv:  {{growth_capex_per_m2: {GROWTH['conv']}}}   # «Монетка» ≈{GROWTH['conv'] * 225:.1f} млн ₽ на магазин 225 м²; «Вингараж» — та же ставка
    droge: {{growth_capex_per_m2: {GROWTH['droge']}}}
    remi:  {{growth_capex_per_m2: {GROWTH['remi']}}}   # смесь форматов ≈0,9 тыс. м² — по ставке супер
    diy:   {{growth_capex_per_m2: {GROWTH['diy']}}}   # DIY-коробка без холода: 0,5 × гипер (суждение, C; диапазон 0,03–0,09); читается, только если у diy есть gross_open (лист network: mid 1,6 % в 2026H2)
  infra_capex_per_net_m2: 0.010   # A-K4: РЦ + автопарк на ЧИСТЫЙ прирост площади; 2025: рост логистики ≈2 млрд / ≈290 тыс. м² органики ≈7 тыс. ₽/м²;
                                  # снизу вверх 0,2 м² РЦ на м² зала × ≈46 тыс. ₽/м² РЦ + автопарк ≈3 тыс. → ≈12; диапазон 0,007–0,015
  infra_from_year: 2026           # запаса мощностей у малых форматов нет (2 РЦ в 2025, выход «Монетки» в центр России); диапазон 2026–2028
  disposal_proceeds_pct: {DISP}   # A-K7: поступления минус прибыль от выбытия (≈ остаточная стоимость): 2024–1П2026 {100 * OUT['disposal']['w_2024_1h26']:.3f} %, 2022–1П2026 без 2023 {100 * OUT['disposal']['w_2022_1h26_ex2023']:.3f} %; диапазон 0,0003–0,0008
  asset_life_years: {LIFE}        # A-K6: PV-эквивалент щита для структуры ввода 2025 (здания 34 %, машины 52 %, ПО 13 %) при подразумеваемых сроках: {OUT['asset_life']['pv_equivalent']['0.17']} г. (r 17 %); диапазон 10–14
  da_method: straight_line        # A-K6: линейно по когортам (копия правила 850oa); учётная политика «Ленты» — линейный метод
  integration_capex: {{"2026H2": {INTEG['2026H2']}, "2027H1": {INTEG['2027H1']}, "2027H2": {INTEG['2027H2']}}}   # млрд ₽ номинал: ребрендинг «О'КЕЙ» ≈1 (вторичный) + догоняющее оборудование 1,0 (суждение); диапазон 1–4 млрд всего

joint:
  capex_prob_given_regime:        # A-P3: P(уровень capex | режим маржи); конструкция 850oa (Р4а), стресс сдвинут к low по истории «Ленты»
                                  # (2020–2023: capex 1,7–2,0 % выручки при марже 6,1→5,5 %; 2019–2020 — «строгий контроль capex»)
    stress:  {{low: {PCR['stress']['low']}, base: {PCR['stress']['base']}, high: {PCR['stress']['high']}}}
    floor:   {{low: {PCR['floor']['low']}, base: {PCR['floor']['base']}, high: {PCR['floor']['high']}}}
    partial: {{low: {PCR['partial']['low']}, base: {PCR['partial']['base']}, high: {PCR['partial']['high']}}}
    full:    {{low: {PCR['full']['low']}, base: {PCR['full']['base']}, high: {PCR['full']['high']}}}

# Предложение листа capex для раздела facts (сводит ведущий/лист фактов; здесь только комментарием, чтобы не было двух источников):
# facts.anchor.capex: {OUT['da_anchor']['capex_1h26']}        # 1П2026, IAS 17 ДДС: ОС 18,658 + НМА 3,824 (датабук CF)
# facts.anchor.da_pre16: {OUT['da_anchor']['pro_forma']}     # проформа: отчётные {OUT['da_anchor']['reported']} + «О'КЕЙ» янв.–май {OUT['da_anchor']['okey_5m']['center']} [{OUT['da_anchor']['okey_5m']['low']}–{OUT['da_anchor']['okey_5m']['high']}];
#                                  # в т.ч. амортизация торговых марок {OUT['da_anchor']['trademark_amort_1h26']} за полугодие — не вычитается для налога (вопрос листу налога)
"""
    FRAGMENT.parent.mkdir(parents=True, exist_ok=True)
    FRAGMENT.write_text(txt, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
