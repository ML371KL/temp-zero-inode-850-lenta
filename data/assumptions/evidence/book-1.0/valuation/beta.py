"""A-V1 β_u и A-V2 ERP: лист «Оценка» книги 1.0 «Ленты» → out/beta_out.txt, out/beta_out.json.

Метод (DESIGN D14):
  1) якорь X5 β_u = 0,601 (лист 850oa evidence/book-1.4/beta: полная доходность к MCFTR, недели, 3 года до 18.09.2026,
     Харрис — Пингл при D/V 0,273, β_d 0,1) × операционный рычаг «Ленты» к X5 НА ПРОФОРМЕ (ВП/EBITDA IAS 17, LTM 30.06.2026);
     варианты: отчётный периметр, полугодие 1П2026, будущая маржа листа margin; путь через EBITDAR с явным
     разрычаживанием/перерычаживанием аренды (X5 почти вся площадь в аренде, «Лента» — 34 % своей);
  2) сверка — собственные беты LENT (ГДР LNTA×5 до 25.02.2022) к MCFTR и IMOEX: недели и месяцы, 1/2/3 года (+5 лет
     справочно), МНК, Ньюи — Уэст, Димсон, разрычаживание Харрис — Пингл по ЧД IAS 17 датабука (β_u с β_u);
  3) ERP: центр 5,57 % (общий с 850oa); верх — λ-конвенция при β_u центра «Ленты» (решение листа).
Детерминированно; сеть не нужна.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import random

import openpyxl

from common import (INPUTS, OUT, BOOK_DRAFT, Log, dump_json, load_iss, magnit_dir, mean, primary_dir, quantile, regress,
                    rel, research_dir, sha256_file, tri_from_halves)

END = dt.date(2026, 9, 18)            # дата книги (запись миров, D7)
BETA_D = 0.10                         # как в 850oa (лист beta §2 п. 5): суждение, чувствительность 0 / 0,2
SHARES_BEFORE = 97_585_932            # до допэмиссии 21.06.2022 (research/01 §2; МСФО)
SHARES_AFTER = 115_985_197            # выпущено (ISS ISSUESIZE); для рыночного рычага окна — как у ISS-капитализации
SHARES_OUT = 115_074_675              # в обращении (D8) — для рычага на дату книги
PRICE_BOOK = 1619.5                   # LEGALCLOSEPRICE 18.09.2026 (проверка — market.py)
X5_ANCHOR_KEY = "X5|3 года|W"

L = Log(OUT / "beta_out.txt")
R: dict = {"schema": "lenta-book-1.0-valuation-beta-v1", "end": END.isoformat(), "beta_d": BETA_D, "sources": {}}


def src(name, path):
    R["sources"][name] = {"file": rel(path), "sha256": sha256_file(path)}
    return path


# =============================================================== 1. данные «Ленты» из датабука (IAS 17)
DB = src("databook", primary_dir() / "Lenta_Q22026_DATABOOK.xlsx")
wb = openpyxl.load_workbook(DB, data_only=True, read_only=True)


def sheet_rows(name):
    return [list(r) for r in wb[name].iter_rows(values_only=True)]


def norm_label(s):
    if s is None:
        return None
    s = str(s).strip().replace("Н", "H")
    if s.startswith("FY") and "restated" not in s:
        return f"{s.split()[1]}-12-31"
    if s.startswith("1H"):
        return f"{s.split()[1]}-06-30"
    return None


pl = sheet_rows("PL")
bs = sheet_rows("BS")
debt = sheet_rows("Debt")
# PL: строка 8 — подписи периодов блока IAS 17
pl_hdr = pl[7]
PLR = {"sales": 10, "gp": 12, "rent_premises": 23, "rent_land_equipment": 28, "ebitda": 49}


def pl_val(row, label):
    j = pl_hdr.index(label)
    return pl[row - 1][j] / 1e6


PL = {lab: {k: pl_val(r, lab) for k, r in PLR.items()} for lab in ("1H 2025", "FY 2025", "1H 2026")}
ltm = {k: PL["FY 2025"][k] - PL["1H 2025"][k] + PL["1H 2026"][k] for k in PLR}
ltm["rent"] = ltm["rent_premises"] + ltm["rent_land_equipment"]
for k in ("gp", "rent_premises", "rent_land_equipment", "rent"):
    ltm[k] = abs(ltm[k])
h126 = {k: abs(PL["1H 2026"][k]) for k in PLR}
h126["rent"] = h126["rent_premises"] + h126["rent_land_equipment"]
h126["ebitda"] = PL["1H 2026"]["ebitda"]
R["lenta_ltm_reported"] = ltm
L("# beta.py — A-V1 β_u и A-V2 ERP (лист «Оценка», книга 1.0 «Ленты»)")
L(f"источник: {rel(DB)} sha256 {R['sources']['databook']['sha256'][:12]}…; PL строки {PLR} (блок IAS 17)")
L(f"LTM 30.06.2026 (FY2025 − 1П2025 + 1П2026), млрд ₽: выручка {ltm['sales']:.3f}; ВП {ltm['gp']:.3f}; EBITDA {ltm['ebitda']:.3f}; "
  f"аренда помещений {ltm['rent_premises']:.3f} + земля/оборудование {ltm['rent_land_equipment']:.3f} = {ltm['rent']:.3f}")

# ЧД IAS 17 по балансовым датам: долгосрочные (стр. 54) + краткосрочные (стр. 62) займы − деньги (стр. 34), блок IAS 17
bs_hdr = bs[7]
ND: dict[dt.date, float] = {}
for j, lab in enumerate(bs_hdr):
    d = norm_label(lab)
    if d is None or j > 31:          # блок IAS 17 заканчивается колонкой 1H 2026 (индекс 31)
        continue
    lt, st, cash = bs[53][j], bs[61][j], bs[33][j]
    if isinstance(lt, (int, float)) and isinstance(st, (int, float)) and isinstance(cash, (int, float)):
        ND[dt.date.fromisoformat(d)] = (lt + st - cash) / 1e6
ND = dict(sorted(ND.items()))
R["lenta_nd_ias17"] = {k.isoformat(): round(v, 3) for k, v in ND.items()}
# сверка с листом Debt (IAS 17): 2025 → 48 835,8; 30.06.2026 → 117 410
assert abs(ND[dt.date(2025, 12, 31)] - 48.8358) < 0.01 and abs(ND[dt.date(2026, 6, 30)] - 117.410) < 0.01, "ЧД BS ≠ Debt"
# обязательства по аренде (МСФО 16), лист Debt стр. 13: 2025 — 119 512,9; 30.06.2026 — 159 266 (млн)
debt_hdr = debt[7]
LEASE_L = {}
for j, lab in enumerate(debt_hdr):
    if j >= 18 and lab is not None:
        key = lab.date().isoformat() if isinstance(lab, dt.datetime) else f"{lab}-12-31"
        v = debt[12][j]
        if isinstance(v, (int, float)):
            LEASE_L[key] = v / 1000 if v < 1e7 else v / 1e6
R["lenta_lease_liabilities"] = LEASE_L
L(f"ЧД IAS 17 (BS: займы − деньги), сверено с листом Debt на 31.12.2025 и 30.06.2026: {ND[dt.date(2025,12,31)]:.3f} / {ND[dt.date(2026,6,30)]:.3f}")
L(f"обязательства по аренде МСФО 16 (Debt стр. 13): {LEASE_L}")

# =============================================================== 2. X5 и якорь 850oa
X5 = json.loads(src("x5_ias17", INPUTS / "x5_ias17.json").read_text(encoding="utf-8"))
x5l = X5["ltm_2026H1"]
BS_MAG = src("magnit_beta_summary", magnit_dir() / "data" / "assumptions" / "evidence" / "book-1.4" / "beta" / "beta_summary.json")
mag = json.loads(BS_MAG.read_text(encoding="utf-8"))
anc = mag["results"][X5_ANCHOR_KEY]
X5_BU = anc["bu1"]                       # 0,601
X5_BU_W = {"2 года": mag["results"]["X5|2 года|W"]["bu1"], "3 года": X5_BU, "5 лет": mag["results"]["X5|5 лет|W"]["bu1"]}
x5_gp_eb = x5l["gross_profit"] / x5l["ebitda"]
x5_gp_ebr = x5l["gross_profit"] / (x5l["ebitda"] + x5l["rent"])
# X5 полугодие 1П2026
x5_h1 = {k: X5["quarters"]["1 КВ. 2026"][k] + X5["quarters"]["2 КВ. 2026"][k] for k in ("gross_profit", "ebitda", "rent")}
L()
L("## якорь X5 (лист 850oa book-1.4/beta, только чтение)")
L(f"{rel(BS_MAG)}: β_E {anc['beta']:.3f} (se НУ {anc['se_hac']:.3f}, n {anc['n']}), D/V {anc['dv']:.3f}, E {anc['e_mean']:.1f}, ЧД {anc['nd_mean']:.1f} → β_u {X5_BU:.4f}")
L(f"окна недель 2/3/5 лет β_u X5: {X5_BU_W['2 года']:.3f} / {X5_BU_W['3 года']:.3f} / {X5_BU_W['5 лет']:.3f}")
L(f"X5 LTM 30.06.2026 (inputs/x5_ias17.json, sha256 книги {X5['sha256'][:12]}…): ВП {x5l['gross_profit']:.3f}; EBITDA {x5l['ebitda']:.3f}; "
  f"аренда {x5l['rent']:.3f}; ВП/EBITDA {x5_gp_eb:.4f} (лист 850oa: {mag['ltm_gp_ebitda']['X5']:.4f}); ВП/EBITDAR {x5_gp_ebr:.4f}")
assert abs(x5_gp_eb - mag["ltm_gp_ebitda"]["X5"]) < 1e-3, "ВП/EBITDA X5 ≠ листу 850oa"
assert abs(ltm["gp"] / ltm["ebitda"] - mag["ltm_gp_ebitda"]["LENT"]) < 1e-3, "ВП/EBITDA Ленты ≠ листу 850oa"

# =============================================================== 3. проформа LTM
# Периметр 30.06.2026 (D3): к отчётной LTM добавляются «О'КЕЙ» 2П2025 и январь–май 2026, «Реми» июль–ноябрь 2025.
# «Дом Лента» до консолидации не добавляется (выручки «ОБИ» в первичке нет; так же в facts/anchor.json).
MA = BOOK_DRAFT / "evidence" / "book-1.0" / "margin" / "anchor_out.json"
FA = BOOK_DRAFT / "facts" / "anchor.json"
ma = json.loads(src("margin_anchor_out", MA).read_text(encoding="utf-8")) if MA.exists() else None
fa = json.loads(src("facts_anchor", FA).read_text(encoding="utf-8")) if FA.exists() else None
rev_okey_5m = ma["okey_5m"]["revenue"] if ma else 56.760897                        # прим. 5 с. 18: 66,120725 − 9,359828
ebitda_okey_5m = ma["mc"]["okey5_ebitda"] if ma else {"p10": 0.64, "p50": 1.17, "p90": 1.76}   # лист margin (мост прим. 5)
remi_1h26 = (ma["acquired"]["parts"]["remi"] if ma else 0.275, ma["facts"]["remi_rev_1h26"] if ma else 27.665)
okey_2h25 = fa["revenue_ltm"]["pro_forma_candidate"]["components"]["okey_2H2025"] if fa else {"v": 75.431049, "range": [73.0, 78.0]}
remi_jn = fa["revenue_ltm"]["pro_forma_candidate"]["components"]["remi_jul_nov_2025"] if fa else {"v": 22.903597, "range": [21.0, 25.0]}
okey_lease = ma["facts"]["okey_lease_lt"] + ma["facts"]["okey_lease_st"] if ma else 17.635883
remi_lease = ma["facts"]["remi_lease"] if ma else 6.828734
pay_ratio = ma["lease_ratios_group_1h26"]["payments"] if ma else 0.302073
gm_group = h126["gp"] / PL["1H 2026"]["sales"]                                     # 22,39 %
# маржа EBITDA «О'КЕЙ» январь–май (лист margin) → на 2П2025; «Реми» — маржа 1П2026
m_okey = {q: ebitda_okey_5m[q] / rev_okey_5m for q in ("p10", "p50", "p90")}
m_remi = remi_1h26[0] / remi_1h26[1]
PF_AXES = {   # (низ, центр, верх) — треугольно по половинам (как A-V9)
    "okey_rev_2h25": (okey_2h25["range"][0], okey_2h25["v"], okey_2h25["range"][1]),
    "okey_m_ebitda": (m_okey["p10"], m_okey["p50"], m_okey["p90"]),
    "remi_rev_jul_nov": (remi_jn["range"][0], remi_jn["v"], remi_jn["range"][1]),
    "remi_m_ebitda": (-0.009, m_remi, 0.025),      # низ — декабрь 2025 (−0,9 %, лист margin), верх — 2,5 % (суждение)
    "gm_acquired": (0.20, gm_group, 0.264),         # низ 20 %; центр — ВП группы 1П2026; верх — РСБУ ООО «О'КЕЙ» 2024 (39,75/150,62)
    "rent_scale": (0.85, 1.0, 1.15),                # аренда приобретённых = обязательство × платежи/обязательство группы 1П2026 × масштаб
}


def pro_forma(v):
    add_rev = rev_okey_5m + v["okey_rev_2h25"] + v["remi_rev_jul_nov"]
    add_ebitda = v["okey_m_ebitda"] * (rev_okey_5m + v["okey_rev_2h25"]) + v["remi_m_ebitda"] * v["remi_rev_jul_nov"]
    add_gp = v["gm_acquired"] * add_rev
    add_rent = v["rent_scale"] * pay_ratio * (okey_lease * (5 / 12 + 0.5) + remi_lease * 5 / 12)
    gp = ltm["gp"] + add_gp
    eb = ltm["ebitda"] + add_ebitda
    rent = ltm["rent"] + add_rent
    return {"revenue": ltm["sales"] + add_rev, "gp": gp, "ebitda": eb, "rent": rent, "ebitdar": eb + rent,
            "gp_ebitda": gp / eb, "gp_ebitdar": gp / (eb + rent), "margin": eb / (ltm["sales"] + add_rev),
            "add_ebitda": add_ebitda, "add_gp": add_gp, "add_rent": add_rent}


pf_c = pro_forma({k: c for k, (lo, c, hi) in PF_AXES.items()})
rng = random.Random(20260918)
draws = []
for _ in range(20000):
    v = {k: tri_from_halves(rng.random(), lo, c, hi) for k, (lo, c, hi) in PF_AXES.items()}
    draws.append(pro_forma(v))
pf_q = {k: {q: quantile([d[k] for d in draws], q) for q in (0.10, 0.50, 0.90)} for k in ("ebitda", "gp_ebitda", "gp_ebitdar", "margin")}
R["pro_forma_ltm"] = {"center": pf_c, "quantiles": {k: {str(q): v for q, v in d.items()} for k, d in pf_q.items()}, "axes": PF_AXES,
                      "inputs": {"rev_okey_jan_may": rev_okey_5m, "okey_ebitda_jan_may": ebitda_okey_5m, "remi_1h26_ebitda_rev": remi_1h26,
                                 "okey_lease": okey_lease, "remi_lease": remi_lease, "lease_payments_ratio": pay_ratio, "gm_group_1h26": gm_group}}
L()
L("## проформа LTM 30.06.2026 (периметр якоря, D3) — расчёт листа; канон EBITDA LTM на проформе — за листом margin/facts")
L(f"входы: «О'КЕЙ» янв–май выручка {rev_okey_5m:.3f}, EBITDA P10/P50/P90 {ebitda_okey_5m['p10']:.3f}/{ebitda_okey_5m['p50']:.3f}/{ebitda_okey_5m['p90']:.3f} "
  f"(лист margin, маржа {m_okey['p50']*100:.2f} %); «О'КЕЙ» 2П2025 выручка {okey_2h25['v']:.3f} {okey_2h25['range']}; «Реми» июль–ноябрь {remi_jn['v']:.3f} "
  f"{remi_jn['range']}, маржа 1П2026 {m_remi*100:.2f} %; ВП приобретённых {gm_group*100:.2f} % [20; 26,4]; аренда: обязательства «О'КЕЙ» {okey_lease:.2f}, "
  f"«Реми» {remi_lease:.2f} × платежи/обязательство {pay_ratio:.3f}/год")
L(f"центр: выручка {pf_c['revenue']:.2f}; EBITDA {pf_c['ebitda']:.2f} (+{pf_c['add_ebitda']:.2f}); маржа {pf_c['margin']*100:.2f} %; ВП {pf_c['gp']:.2f}; "
  f"аренда {pf_c['rent']:.2f}; ВП/EBITDA {pf_c['gp_ebitda']:.4f}; ВП/EBITDAR {pf_c['gp_ebitdar']:.4f}")
L(f"Монте-Карло 20 000 (зерно 20260918): EBITDA P10–P90 {pf_q['ebitda'][0.1]:.2f}–{pf_q['ebitda'][0.9]:.2f}; ВП/EBITDA {pf_q['gp_ebitda'][0.1]:.3f}–{pf_q['gp_ebitda'][0.9]:.3f}; "
  f"ВП/EBITDAR {pf_q['gp_ebitdar'][0.1]:.3f}–{pf_q['gp_ebitdar'][0.9]:.3f}")

# =============================================================== 4. операционный рычаг и кандидаты от якоря
ma_pf_1h = ma["ebitda_pro_forma_1h26"] if ma else 40.448
gp_pf_1h = h126["gp"] + gm_group * rev_okey_5m
E_LT = 0.0607      # ожидание цели LT по режимам (лист margin, regimes_out.txt: E[m_LT] = 6,07 %) — будущая маржа, вариант (в) 850oa
cands = {}


def add(name, k, note):
    cands[name] = {"k": k, "beta_u": X5_BU * k, "note": note}


add("ВП/EBITDA, LTM, отчётный периметр", (ltm["gp"] / ltm["ebitda"]) / x5_gp_eb, "как k(а) 850oa; «О'КЕЙ» в ЧД целиком, в EBITDA — месяц")
add("ВП/EBITDA, LTM, проформа (центр)", pf_c["gp_ebitda"] / x5_gp_eb, "D14: периметр якоря")
add("ВП/EBITDA, LTM, проформа P10", pf_q["gp_ebitda"][0.1] / x5_gp_eb, "")
add("ВП/EBITDA, LTM, проформа P90", pf_q["gp_ebitda"][0.9] / x5_gp_eb, "")
add("ВП/EBITDA, 1П2026, проформа / X5 1П2026", (gp_pf_1h / ma_pf_1h) / (x5_h1["gross_profit"] / x5_h1["ebitda"]), "обе — одно полугодие")
add("ВП/EBITDA, будущая маржа E[m_LT] 6,07 %", (gm_group / E_LT) / x5_gp_eb, "как k(в) 850oa; ВП 22,39 % постоянна")
add("ВП/EBITDAR, LTM, проформа (без перерычаживания аренды)", pf_c["gp_ebitdar"] / x5_gp_ebr, "только сверка: аренда X5 остаётся в β якоря")
for name, c in cands.items():
    pass
# окна X5 × проформенный k
k_pf = cands["ВП/EBITDA, LTM, проформа (центр)"]["k"]
for w, b in X5_BU_W.items():
    cands[f"окно X5 {w} × k проформы"] = {"k": k_pf, "beta_u": b * k_pf, "note": f"β_u X5 {b:.3f}"}

# путь EBITDAR с явной арендой: β_ops X5 (EV + аренда) → × k_R → перерычаживание аренды «Ленты» на дату книги
# X5: средняя аренда по неделям окна 3 года (ступень по последней дате баланса ≤ недели; до 12.2023 — значение 12.2022)
xl = {dt.date.fromisoformat(k): v for k, v in X5["lease_liabilities"].items()}
x5_weeks = []
d = dt.date(2023, 9, 22)
while d <= END:
    in_gdr = d <= dt.date(2024, 4, 3)
    in_stock = d >= dt.date(2025, 1, 17)
    if in_gdr or in_stock:
        prev = [k for k in xl if k <= d]
        x5_weeks.append(xl[max(prev)])
    d += dt.timedelta(days=7)
x5_L = mean(x5_weeks)
x5_V = anc["e_mean"] + anc["nd_mean"]
x5_ops = (X5_BU * x5_V + BETA_D * x5_L) / (x5_V + x5_L)
k_R = pf_c["gp_ebitdar"] / x5_gp_ebr
E_book = PRICE_BOOK * SHARES_OUT / 1e9
V_L = E_book + ND[dt.date(2026, 6, 30)]
L_L = LEASE_L["2026-06-30"]
bu_R = (x5_ops * k_R * (V_L + L_L) - BETA_D * L_L) / V_L
k_R_lo, k_R_hi = pf_q["gp_ebitdar"][0.1] / x5_gp_ebr, pf_q["gp_ebitdar"][0.9] / x5_gp_ebr
bu_R_lo = (x5_ops * k_R_lo * (V_L + L_L) - BETA_D * L_L) / V_L
bu_R_hi = (x5_ops * k_R_hi * (V_L + L_L) - BETA_D * L_L) / V_L
cands["EBITDAR + разрычаживание/перерычаживание аренды"] = {"k": k_R, "beta_u": bu_R,
    "note": f"X5: V {x5_V:.0f}, аренда (среднее окна) {x5_L:.0f} → β_ops {x5_ops:.4f}; Лента 18.09: V {V_L:.1f}, аренда {L_L:.1f}; P10–P90 {bu_R_lo:.3f}–{bu_R_hi:.3f}"}
R["anchor_candidates"] = cands
R["ebitdar_route"] = {"x5_V_mean": x5_V, "x5_lease_mean": x5_L, "x5_beta_ops": x5_ops, "k_R": k_R, "lenta_V": V_L, "lenta_lease": L_L,
                      "lenta_E_book": E_book, "beta_u": bu_R, "p10_p90": [bu_R_lo, bu_R_hi]}
L()
L("## операционный рычаг и β_u от якоря X5 (β_u = 0,601 × k, если не сказано иное)")
L(f"X5 ВП/EBITDA LTM {x5_gp_eb:.4f}; 1П2026 {x5_h1['gross_profit']/x5_h1['ebitda']:.4f}; ВП/EBITDAR LTM {x5_gp_ebr:.4f}")
L(f"Лента ВП/EBITDA: отчётная LTM {ltm['gp']/ltm['ebitda']:.4f}; проформа LTM {pf_c['gp_ebitda']:.4f}; 1П2026 проформа {gp_pf_1h/ma_pf_1h:.4f} "
  f"(ВП {gp_pf_1h:.2f} / EBITDA {ma_pf_1h:.2f}); при E[m_LT] 6,07 % — {gm_group/E_LT:.4f}")
for name, c in cands.items():
    L(f"  {name}: k {c['k']:.4f} → β_u {c['beta_u']:.4f}  {c['note']}")

# =============================================================== 5. собственные беты LENT
F_LENT = src("iss_LENT", research_dir() / "_work05" / "LENT_TQBR.json")
F_LNTA = src("iss_LNTA", research_dir() / "_work05" / "LNTA_TQBR.json")
F_MCFTR = src("iss_MCFTR", research_dir() / "_work05" / "MCFTR.json")
F_IMOEX = src("iss_IMOEX", research_dir() / "_work05" / "IMOEX.json")
lent = load_iss(F_LENT)
lnta = {k: v * 5 for k, v in load_iss(F_LNTA).items() if k < dt.date(2022, 2, 25)}   # 5 ГДР = 1 акция
mcftr = load_iss(F_MCFTR, "CLOSE", None, skip_zero_volume=False)
imoex = load_iss(F_IMOEX, "CLOSE", None, skip_zero_volume=False)
# сшивка: до 25.02.2022 — ГДР × 5 (акция с 14.12.2021 торговалась тонко, как в 850oa берём ГДР), после — LENT
stock: dict[dt.date, tuple[float, str]] = {}
for k, v in lnta.items():
    stock[k] = (v, "LNTA")
for k, v in lent.items():
    if k >= dt.date(2022, 3, 24):
        stock[k] = (v, "LENT")
assert abs(lent[END] - PRICE_BOOK) < 1e-9, "legal close 18.09.2026 ≠ 1 619,5"


def shares_at(d):
    return SHARES_BEFORE if d < dt.date(2022, 6, 21) else SHARES_AFTER


def nd_at(d):
    prev = [k for k in ND if k <= d]
    return ND[max(prev)] if prev else None


def period_ends(freq, mkt):
    common = sorted(d for d in set(stock) & set(mkt) if d <= END)   # окна заканчиваются датой книги: данные после неё не берутся
    ends = {}
    for d in common:
        key = d.isocalendar()[:2] if freq == "W" else (d.year, d.month)
        ends[key] = d          # последний общий день периода
    return sorted(ends.values())


def returns(freq, mkt):
    ends = period_ends(freq, mkt)
    gap = 10 if freq == "W" else 45
    out = []
    for a, b in zip(ends, ends[1:]):
        if (b - a).days > gap or stock[a][1] != stock[b][1] and not (stock[a][1] == "LNTA" and stock[b][1] == "LENT" and (b - a).days <= gap):
            continue
        ri = stock[b][0] / stock[a][0] - 1
        rm = mkt[b] / mkt[a] - 1
        e = stock[a][0] * shares_at(a) / 1e9
        nd = nd_at(a)
        out.append({"t0": a, "t": b, "ri": ri, "rm": rm, "dv": nd / (e + nd), "e": e, "nd": nd})
    return out


def beta_window(rets, years, freq, start=None, end=END):
    lo = start or dt.date(end.year - years, end.month, end.day)
    w = [r for r in rets if lo < r["t"] <= end]
    if len(w) < 10:
        return None
    lags = 4 if freq == "W" else 2
    reg = regress([r["ri"] for r in w], [[r["rm"] for r in w]], lags)
    beta, se = reg["b"][1], reg["se_hac"][1]
    dv = mean([r["dv"] for r in w])
    # Димсон: только наблюдения с соседними доходностями рынка (подряд идущие периоды)
    idx = {r["t"]: i for i, r in enumerate(rets)}
    ys, x0, xm, xp = [], [], [], []
    for r in w:
        i = idx[r["t"]]
        if 0 < i < len(rets) - 1 and rets[i - 1]["t"] == r["t0"] and rets[i + 1]["t0"] == r["t"]:
            ys.append(r["ri"]); x0.append(r["rm"]); xm.append(rets[i - 1]["rm"]); xp.append(rets[i + 1]["rm"])
    dim = dim_se = None
    if len(ys) >= 10:
        rd = regress(ys, [xm, x0, xp], lags)
        dim = sum(rd["b"][1:4])
        Vh = rd["V_hac"]
        dim_se = math.sqrt(max(sum(Vh[i][j] for i in (1, 2, 3) for j in (1, 2, 3)), 0))
    sd_i = math.sqrt(sum((r["ri"] - mean([x["ri"] for x in w])) ** 2 for r in w) / (len(w) - 1))
    sd_m = math.sqrt(sum((r["rm"] - mean([x["rm"] for x in w])) ** 2 for r in w) / (len(w) - 1))
    ann = math.sqrt(52 if freq == "W" else 12)
    return {"n": len(w), "beta_e": beta, "se_hac": se, "se_ols": reg["se_ols"][1], "r2": reg["r2"], "dv": dv,
            "beta_u": beta * (1 - dv) + BETA_D * dv, "beta_u_bd0": beta * (1 - dv), "beta_u_bd02": beta * (1 - dv) + 0.2 * dv,
            "dimson": dim, "dimson_se": dim_se, "beta_u_dimson": (dim * (1 - dv) + BETA_D * dv) if dim is not None else None,
            "vol_i": sd_i * ann, "vol_m": sd_m * ann, "first": w[0]["t"].isoformat(), "last": w[-1]["t"].isoformat()}


OWN = {}
for mname, mkt in (("MCFTR", mcftr), ("IMOEX", imoex)):
    for freq in ("W", "M"):
        rets = returns(freq, mkt)
        for years in (1, 2, 3, 5):
            b = beta_window(rets, years, freq)
            if b:
                OWN[f"{mname}|{freq}|{years}"] = b
        if freq == "W":
            b = beta_window(rets, 0, freq, start=dt.date(2023, 9, 21), end=dt.date(2024, 4, 3))
            OWN[f"{mname}|W|эпизод 22.09.2023–03.04.2024"] = b
R["own_betas"] = OWN
L()
L("## собственные беты LENT (LNTA×5 до 25.02.2022; дивидендов не было — полная доходность = цене); окна до 18.09.2026")
L(f"ряды: {rel(F_LENT)}, {rel(F_LNTA)}, {rel(F_MCFTR)}, {rel(F_IMOEX)} (сырые ответы ISS 28.09.2026; sha256 в beta_out.json)")
L("рынок | част. | окно | n | β_E (НУ se) | R² | Димсон (se) | D/V | β_u (β_d 0,1) | β_u Димсон | σ бумаги / рынка")
for k, b in OWN.items():
    if b is None:
        continue
    dim = f"{b['dimson']:.3f} ({b['dimson_se']:.3f})" if b["dimson"] is not None else "—"
    bud = f"{b['beta_u_dimson']:.3f}" if b["beta_u_dimson"] is not None else "—"
    L(f"{k.replace('|', ' | ')} | {b['n']} | {b['beta_e']:.3f} ({b['se_hac']:.3f}) | {b['r2']:.2f} | {dim} | {b['dv']:.2f} | {b['beta_u']:.3f} | {bud} | "
      f"{b['vol_i']*100:.1f} % / {b['vol_m']*100:.1f} %")
m_ = [OWN[f"MCFTR|M|{y}"]["beta_u"] for y in (1, 2, 3)]
w_ = [OWN[f"MCFTR|W|{y}"]["beta_u"] for y in (1, 2, 3)]
wd_ = [OWN[f"MCFTR|W|{y}"]["beta_u_dimson"] for y in (1, 2, 3)]
L(f"сверка с листом 850oa (окна до 18.09.2026, недели 2/3/5 лет β_u 0,298/0,373/0,493; месяцы 0,585/0,703/0,665): "
  f"здесь недели 2/3/5 — {OWN['MCFTR|W|2']['beta_u']:.3f}/{OWN['MCFTR|W|3']['beta_u']:.3f}/{OWN['MCFTR|W|5']['beta_u']:.3f}; "
  f"месяцы — {OWN['MCFTR|M|2']['beta_u']:.3f}/{OWN['MCFTR|M|3']['beta_u']:.3f}/{OWN['MCFTR|M|5']['beta_u']:.3f}")

# =============================================================== 6. выбор центра и диапазона
anchor_cluster = [cands["ВП/EBITDA, LTM, проформа (центр)"]["beta_u"], cands["EBITDAR + разрычаживание/перерычаживание аренды"]["beta_u"],
                  cands["ВП/EBITDA, будущая маржа E[m_LT] 6,07 %"]["beta_u"], cands["ВП/EBITDA, 1П2026, проформа / X5 1П2026"]["beta_u"]]
CENTER, LOW, HIGH = 0.55, 0.45, 0.70
R["choice"] = {"beta_u": CENTER, "range": [LOW, HIGH], "anchor_cluster": anchor_cluster,
               "own_weekly_u_1_2_3": w_, "own_weekly_dimson_u_1_2_3": wd_, "own_monthly_u_1_2_3": m_}
L()
L("## выбор (суждение листа)")
L(f"кластер якоря на периметре якоря (ВП/EBITDA проформа, EBITDAR с арендой, будущая маржа, полугодие): "
  + " / ".join(f"{x:.3f}" for x in anchor_cluster) + f"; среднее {mean(anchor_cluster):.3f}")
L(f"собственные LENT к MCFTR, β_u 1/2/3 года: недели {' / '.join(f'{x:.3f}' for x in w_)}; недели Димсон {' / '.join(f'{x:.3f}' for x in wd_)}; "
  f"месяцы {' / '.join(f'{x:.3f}' for x in m_)}")
L(f"ЦЕНТР β_u = {CENTER:.2f}; диапазон {LOW:.2f}–{HIGH:.2f} (треугольно по половинам: −{CENTER-LOW:.2f} / +{HIGH-CENTER:.2f})")

# =============================================================== 7. ERP (A-V2)
DAM = {"mature": 4.23, "crp": 3.895, "ds": 2.557, "total": 8.125, "erp_book": 5.57, "low_realized": 4.9}
# Damodaran ctryprem 05.01.2026 (Россия) — числа из книги 850oa A-V2 и аудита 850oa 26.09.2026 (C2_major, стр. 112, 3024)


def lam_prem(b, lam=1.0):
    return b * DAM["mature"] + lam * DAM["crp"] - DAM["ds"]


erp_rows = []
for b in (0.45, 0.49, 0.50, 0.55, 0.60, 0.70):
    lp = lam_prem(b)
    erp_rows.append({"beta": b, "lambda1_premium_pp": lp, "lambda1_erp_equiv": lp / b,
                     "beta_approach_premium_pp": b * DAM["total"] - DAM["ds"], "book_premium_pp": b * DAM["erp_book"]})
ERP_TOP = round(lam_prem(CENTER) / CENTER, 3)
R["erp"] = {"center": 0.0557, "low": 0.049, "high": round(math.ceil(ERP_TOP * 10) / 10 / 100, 3), "high_unrounded_pct": ERP_TOP,
            "damodaran": DAM, "table": erp_rows}
L()
L("## ERP (A-V2): центр 5,57 % — общий с 850oa; верх — решение листа")
L("Damodaran 05.01.2026 (Россия): зрелый ERP 4,23; CRP 3,895; спред дефолта 2,557; итого 8,125 (книга 850oa A-V2, аудит 850oa C2)")
L("β | премия λ=1: β·4,23 + 3,895 − 2,557, п.п. | в единицах ERP | β-подход β·8,125 − 2,557 | книга β·5,57")
for r in erp_rows:
    L(f"{r['beta']:.2f} | {r['lambda1_premium_pp']:.3f} | {r['lambda1_erp_equiv']:.3f} % | {r['beta_approach_premium_pp']:.3f} | {r['book_premium_pp']:.3f}")
L(f"верх ERP = λ-конвенция при β_u центра {CENTER}: {ERP_TOP:.3f} % → {R['erp']['high']*100:.1f} %; низ — реализованная премия с 2003 г. 4,9 % (как 850oa)")
L(f"премия β_u × ERP центра: {CENTER*5.57:.3f} п.п.; по осям: {LOW*4.9:.2f}–{HIGH*R['erp']['high']*100:.2f} п.п.")

# =============================================================== 8. согласованность ρ ≤ 1 (справочно; σ_EV в методе intrinsic нет)
b3w, b3m = OWN["MCFTR|W|3"], OWN["MCFTR|M|3"]
ev_book = E_book / V_L                                   # E/V на дату книги (IAS 17)
rho = {}
for tag, b in (("недели 3 г.", b3w), ("месяцы 3 г.", b3m)):
    sig_v = b["vol_i"] * ev_book
    rho[tag] = {"sigma_E": b["vol_i"], "sigma_M": b["vol_m"], "sigma_V": sig_v, "rho_needed": CENTER * b["vol_m"] / sig_v,
                "rho_needed_high": HIGH * b["vol_m"] / sig_v, "rho_obs": math.sqrt(max(b["r2"], 0))}
R["rho_check"] = {"E_over_V_book": ev_book, "by_freq": rho}
L()
L(f"## ρ ≤ 1 (справочно; σ_EV в методе intrinsic нет): E/V на 18.09.2026 {ev_book:.3f}")
for tag, x in rho.items():
    L(f"{tag}: σ_E {x['sigma_E']*100:.1f} % → σ_V {x['sigma_V']*100:.1f} %; σ MCFTR {x['sigma_M']*100:.1f} %; для β_u {CENTER} нужна ρ ≥ {x['rho_needed']:.2f} "
      f"(для {HIGH} — {x['rho_needed_high']:.2f}); наблюдаемая ρ {x['rho_obs']:.2f}")
L("вывод: центр совместим с месячной корреляцией; недельная ρ занижена несинхронной торговлей (Димсон поднимает β на 0,12–0,15)")

# =============================================================== 9. цена ошибки (правило пальца до ядра)
D_EV = 8.5         # дюрация EV к ставке, лет: по отклику ядра книги-источника на β — оценка до ядра Ленты
RUB_PER_1PCT_EV = 26.5
def rub(d_prem_pp):
    return d_prem_pp * D_EV * RUB_PER_1PCT_EV


R["cost_of_error"] = {"duration_ev_years": D_EV, "rub_per_1pct_ev": RUB_PER_1PCT_EV,
                      "beta_0.05": rub(0.05 * 5.57), "beta_low": rub((CENTER - LOW) * 5.57), "beta_high": -rub((HIGH - CENTER) * 5.57),
                      "erp_low": rub(CENTER * (5.57 - 4.9)), "erp_high": -rub(CENTER * (R["erp"]["high"] * 100 - 5.57)),
                      "erp_top_6.7_vs_6.2": -rub(CENTER * (R["erp"]["high"] * 100 - 6.2))}
c = R["cost_of_error"]
L()
L(f"## цена ошибки (до ядра: дюрация EV {D_EV} лет по отклику ядра 850oa; 1 % EV ≈ {RUB_PER_1PCT_EV} ₽/акц.)")
L(f"0,05 β_u ≈ {0.05*5.57:.3f} п.п. ставки ≈ {c['beta_0.05']:.0f} ₽; β_u {LOW} → +{c['beta_low']:.0f} ₽; {HIGH} → {c['beta_high']:.0f} ₽")
L(f"ERP 4,9 % → +{c['erp_low']:.0f} ₽; {R['erp']['high']*100:.1f} % → {c['erp_high']:.0f} ₽; верх 6,7 против 6,2 — {c['erp_top_6.7_vs_6.2']:.0f} ₽ на конце оси")

L.save()
dump_json(R, OUT / "beta_out.json")
