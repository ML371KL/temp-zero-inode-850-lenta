# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 3: σ и ρ правила A-P2u (joint.regime_update.sigma_pp, margin.deviation_persistence).

Семантика (урок К4): sigma_pp — СТАЦИОНАРНОЕ стандартное отклонение отклонения маржи от цели
режима; инновация AR(1) = sigma_pp·√(1 − ρ²). Правдоподобие ядра: первое наблюдение — σ²,
следующие — σ²(1 − ρ^{2k}).

Оценки:
 (1) Лента, современная эпоха 2021H1–2026H1: приросты «полугодие к полугодию» на ОБЩЕМ периметре
     (без полугодий со скачком периметра: поглощённая сеть снимается с нового полугодия или
     добавляется проформой к старому — мост «прибыль до налога → EBITDA IAS 17» по прим. 8 МСФО);
 (2) Лента, эпоха гипермаркетов 2013H1–2021H1 (справочно; DESIGN D9 её из σ исключает);
 (3) сверка: Магнит 2017H1–2026H1 (тот же метод; оценка AR по истории книги-источника — 0,51)
     и X5 2023H1–2026H1.
Из прироста d = x_t − x_{t−1} (x — маржа без сезонности): Var(d) = 2σ²(1 − ρ), отсюда
σ_стац(ρ) = sd(d)/√(2(1 − ρ)), σ_инн = sd(d)·√((1 + ρ)/2). ρ — прямой МНК x_t = a + b·t + ρ·x_{t−1} (+ H1)
на длинных непрерывных отрезках. Вывод: ar1_out.json, ar1_out.txt.
"""
from __future__ import annotations

import json
import math
import statistics as st

from _common import HERE, dump, pdf_number

OUT_JSON = HERE / "ar1_out.json"
OUT_TXT = HERE / "ar1_out.txt"
FY21 = "ifrs_mkpao/ifrs_FY2021.pdf"
FY23 = "ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf"
FY24 = "ifrs_mkpao/ifrs_FY2024.pdf"
G = r"(\d{1,3}(?:[ , ]\d{3})+)"
LEASE_NET = 0.065   # (процент + амортизация ПП − платежи) / обязательство, в год (≈ 4,8–6,6 %: группа 2025 и 1П2026)
PPE_DEP = 0.11      # амортизация ОС, в год (группа 1П2025: 21,2 / 181 ≈ 11,7 %)
INT_AMORT = 0.09    # амортизация НМА (торговые марки), в год (прим. 10 1П2026: 0,81 × 2 / 18,4 ≈ 8,8 %)


def n(name, pat):
    return pdf_number(name, pat)


def bridge(pbt, lease, ppe, intang, months):
    """EBITDA IAS 17 из прибыли до налога МСФО 16 для приобретённой сети без долга."""
    f = months / 12
    add = lease * LEASE_NET * f + ppe * PPE_DEP * f + intang * INT_AMORT * f
    return pbt + add, 0.3 * add + 0.05   # (EBITDA, se)


def acquired_facts():
    a = {}
    a["billa_rev"] = n(FY21, r"Billa Realty LLC and Billa LLC was RUB\s*" + G)
    a["billa_pbt"] = -n(FY21, r"Billa Realty LLC and Billa LLC was RUB\s*[\d,]+\s*and RUB\s*" + G)
    a["billa_ppe"] = n(FY21, r"Billa LLC at\s*the date of acquisition were:.*?Property, plant and equipment \(Note 7\)\s*" + G)
    a["billa_lease"] = (n(FY21, r"Billa LLC at\s*the date of acquisition were:.*?Long-term lease liabilities \(Note 10\)\s*\(" + G)
                        + n(FY21, r"Billa LLC at\s*the date of acquisition were:.*?Short-term lease liabilities \(Note 10\)\s*\(" + G))
    a["semya_rev"] = n(FY21, r"profit before income tax of Semya Group\s*was RUB\s*" + G)
    a["semya_pbt"] = n(FY21, r"profit before income tax of Semya Group\s*was RUB\s*[\d,]+\s*and RUB\s*" + G)
    a["semya_lease"] = (n(FY21, r"Semya Group at the date of\s*acquisition were:.*?Long-term lease liabilities \(Note 10\)\s*\(" + G)
                        + n(FY21, r"Semya Group at the date of\s*acquisition were:.*?Short-term lease liabilities \(Note 10\)\s*\(" + G))
    a["semya_ppe"] = n(FY21, r"Semya Group at the date of\s*acquisition were:.*?Property, plant and equipment \(Note 7\)\s*" + G)
    a["mon_rev_q4"] = n(FY23, r"вклад Группы Б\* в выручку и прибыль до налогообложения\s*Группы составил\s*" + G)
    a["mon_pbt_q4"] = n(FY23, r"вклад Группы Б\* в выручку и прибыль до налогообложения\s*Группы составил\s*[\d ]+ тыс\. руб\. и\s*" + G)
    a["grp_rev_pf23"] = n(FY23, r"выручка Группы за 2023 год составила бы\s*" + G)
    a["grp_pbt_pf23"] = n(FY23, r"прибыль до налогообложения составила бы\s*" + G)
    a["mon_ppe"] = n(FY23, r"Группы Б\* согласно предварительным значениям.*?Основные средства \(Прим\. 7\)\s*" + G)
    a["mon_int"] = n(FY23, r"Группы Б\* согласно предварительным значениям.*?Нематериальные активы \(Прим\. 12\)\s*" + G)
    a["mon_lease"] = (n(FY23, r"Группы Б\* согласно предварительным значениям.*?Долгосрочные обязательства по аренде \(Прим\. 10\)\s*\(" + G)
                      + n(FY23, r"Группы Б\* согласно предварительным значениям.*?Краткосрочные обязательства по аренде \(Прим\. 10\)\s*\(" + G))
    a["uly_rev_dec"] = n(FY24, r"«Улыбка радуги» в выручку и прибыль до\s*налогообложения Группы составил\s*" + G)
    a["uly_pbt_dec"] = n(FY24, r"«Улыбка радуги» в выручку и прибыль до\s*налогообложения Группы составил\s*[\d ]+ тыс\. руб\. и\s*" + G)
    a["uly_rev_pf24"] = n(FY24, r"выручка Группы за 2024 год\s*составила бы\s*" + G)
    a["uly_pbt_pf24"] = n(FY24, r"выручка Группы за 2024 год\s*составила бы\s*[\d ]+ тыс\. руб\., прибыль до налогообложения составила бы\s*" + G)
    a["uly_ppe"] = n(FY24, r"группы «Улыбка радуги» согласно предварительным значениям.*?Основные средства \(Прим\. 7\)\s*" + G)
    a["uly_int"] = n(FY24, r"группы «Улыбка радуги» согласно предварительным значениям.*?Нематериальные активы \(Прим\.13\)\s*" + G)
    a["uly_lease"] = (n(FY24, r"группы «Улыбка радуги» согласно предварительным значениям.*?Долгосрочные обязательства по аренде \(Прим\. 10\)\s*\(" + G)
                      + n(FY24, r"группы «Улыбка радуги» согласно предварительным значениям.*?Краткосрочные обязательства по аренде \(Прим\. 10\)\s*\(" + G))
    return a


def ols(X, y):
    """МНК без внешних библиотек: (β, остатки)."""
    k = len(X[0])
    XtX = [[sum(r[i] * r[j] for r in X) for j in range(k)] for i in range(k)]
    Xty = [sum(r[i] * yy for r, yy in zip(X, y)) for i in range(k)]
    A = [row[:] + [b] for row, b in zip(XtX, Xty)]
    for c in range(k):
        p = max(range(c, k), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]
        for r in range(k):
            if r != c:
                fct = A[r][c] / A[c][c]
                A[r] = [x - fct * z for x, z in zip(A[r], A[c])]
    beta = [A[i][k] / A[i][i] for i in range(k)]
    res = [yy - sum(b * x for b, x in zip(beta, r)) for r, yy in zip(X, y)]
    return beta, res


def ar1_fit(xs, with_trend=True, h1_flags=None):
    X, y = [], []
    for t in range(1, len(xs)):
        row = [1.0]
        if with_trend:
            row.append(float(t))
        if h1_flags is not None:
            row.append(1.0 if h1_flags[t] else 0.0)
        row.append(xs[t - 1])
        X.append(row)
        y.append(xs[t])
    beta, res = ols(X, y)
    dof = len(y) - len(beta)
    s = math.sqrt(sum(r * r for r in res) / dof) if dof > 0 else float("nan")
    rho = beta[-1]
    return {"rho": rho, "sigma_innov": s, "sigma_stat": s / math.sqrt(1 - rho * rho) if abs(rho) < 1 else float("nan"),
            "n": len(y), "beta": beta}


def fd_stats(ds, rhos=(0.5, 0.6, 0.7)):
    sd = st.stdev(ds) if len(ds) > 1 else float("nan")
    med = st.median(ds)
    mad = st.median([abs(d - med) for d in ds]) * 1.4826
    out = {"n": len(ds), "sd_d": sd, "robust_sd_d": mad, "by_rho": {}}
    for r in rhos:
        out["by_rho"][str(r)] = {
            "sigma_stat": sd / math.sqrt(2 * (1 - r)), "sigma_innov": sd * math.sqrt((1 + r) / 2),
            "robust_sigma_stat": mad / math.sqrt(2 * (1 - r)), "robust_sigma_innov": mad * math.sqrt((1 + r) / 2)}
    if len(ds) > 2:
        m = st.fmean(ds)
        num = sum((ds[i] - m) * (ds[i - 1] - m) for i in range(1, len(ds)))
        den = sum((d - m) ** 2 for d in ds)
        c1 = num / den
        out["lag1_corr"] = c1
        out["rho_from_lag1"] = 1 + 2 * c1
    return out


def main():
    S = json.loads((HERE / "series_out.json").read_text(encoding="utf-8"))
    A = json.loads((HERE / "anchor_out.json").read_text(encoding="utf-8"))
    peers = json.loads((HERE / "inputs" / "peers_halves.json").read_text(encoding="utf-8"))
    H = S["halves"]
    af = acquired_facts()
    L = []
    P = L.append
    P("# ar1.py — σ и ρ правила A-P2u")
    P("\n## факты поглощений (млрд руб.; прим. 8 МСФО)")
    for k, v in af.items():
        P(f"{k}: {v:.4f}")

    # сезонность книги (из series.py: медиана сырых 2П − 1П, 2021–2025 без 2023)
    # сезонность — после пар на общем периметре (ниже): 2П − 1П тех же лет без скачка периметра

    def m(k):
        return H[k]["margin_norm"] * 100

    def rev(k):
        return H[k]["revenue"]

    def e(k):
        return H[k]["margin_norm"] * H[k]["revenue"]

    # --- EBITDA поглощённых сетей в полугодиях скачка (мост)
    billa, billa_se = bridge(af["billa_pbt"], af["billa_lease"], af["billa_ppe"], 0.0, 5)
    semya, semya_se = bridge(af["semya_pbt"], af["semya_lease"], af["semya_ppe"], 0.0, 4)
    mon_q4, mon_q4_se = bridge(af["mon_pbt_q4"], af["mon_lease"], af["mon_ppe"], af["mon_int"], 3)
    # «Монетка» 9М2023 до покупки: проформа группы минус отчёт (выручка/прибыль до налога МСФО 16)
    grp_rev23 = H["2023FY"]["revenue"] if "2023FY" in H else rev("2023H1") + rev("2023H2")
    from _common import pl_value, fq_table
    grp_pbt23_ifrs16 = pl_value("Profit / (loss) before income tax", "IFRS 16", "FY", "2023")
    mon_rev_9m = af["grp_rev_pf23"] - grp_rev23
    mon_pbt_9m = af["grp_pbt_pf23"] - grp_pbt23_ifrs16
    mon_9m, mon_9m_se = bridge(mon_pbt_9m, af["mon_lease"], af["mon_ppe"], af["mon_int"], 9)
    mon_q3_rev = mon_rev_9m / 3 * 1.02      # 3 кв. ≈ треть 9М с сезонной надбавкой (суждение)
    mon_q3 = mon_9m / 3 * 1.02
    uly_dec, uly_dec_se = bridge(af["uly_pbt_dec"], af["uly_lease"], af["uly_ppe"], af["uly_int"], 1)
    uly_fy, uly_fy_se = bridge(af["uly_pbt_pf24"], af["uly_lease"], af["uly_ppe"], af["uly_int"], 12)
    uly_2h_rev = af["uly_rev_pf24"] * 0.53    # доля 2П в годовой выручке сети (суждение, как у группы ≈0,53)
    uly_2h = uly_fy * 0.53
    remi_dec = A["remi_dec_2025"]["ebitda"]
    remi_dec_rev = A["remi_dec_2025"]["revenue"]
    Qd = fq_table()
    base25 = [sum(Qd[f"2025Q{i}"][k] for k in ("hyper", "super", "conv")) for i in (1, 2, 3, 4)]
    remi_2h_rev = A["facts"]["remi_pf_2025"] * (base25[2] + base25[3]) / sum(base25)   # проформа «Реми» 2025 × доля 2П гипер+супер+у дома 2025
    remi_2h = remi_2h_rev * 0.01               # маржа «Реми» ≈1 % (как в anchor_bridge.py)
    okey_j = A["mc"]["okey_june_ebitda"]["p50"]
    diy = A["mc"]["diy_ebitda"]["p50"]
    P(f"\nмост: Billa 5 мес. EBITDA {billa:.3f} ({billa / af['billa_rev'] * 100:.1f} %), «Семья» {semya:.3f}; "
      f"«Монетка» 4 кв. 2023 {mon_q4:.3f} ({mon_q4 / af['mon_rev_q4'] * 100:.1f} %), 9М2023 до покупки: выручка {mon_rev_9m:.2f}, "
      f"EBITDA {mon_9m:.2f} ({mon_9m / mon_rev_9m * 100:.1f} %); «Улыбка» дек. 2024 {uly_dec:.3f}, 2024 проформа {uly_fy:.3f} "
      f"({uly_fy / af['uly_rev_pf24'] * 100:.1f} %)")

    # --- пары на общем периметре: (метка, x_prev, x_curr, тип перехода, se); d — после сезонности
    def pair(label, m_prev, m_curr, prev_half, se=0.0):
        return {"pair": label, "m_prev": m_prev, "m_curr": m_curr, "prev_half": prev_half, "se": se}

    leg21h2 = (e("2021H2") - billa - semya) / (rev("2021H2") - af["billa_rev"] - af["semya_rev"]) * 100
    leg23h2 = (e("2023H2") - mon_q4) / (rev("2023H2") - af["mon_rev_q4"]) * 100
    pf23h2 = (e("2023H2") + mon_q3) / (rev("2023H2") + mon_q3_rev) * 100
    leg24h2 = (e("2024H2") - uly_dec) / (rev("2024H2") - af["uly_rev_dec"]) * 100
    pf24h2 = (e("2024H2") - uly_dec + uly_2h) / (rev("2024H2") - af["uly_rev_dec"] + uly_2h_rev) * 100
    leg25h2 = (e("2025H2") - remi_dec) / (rev("2025H2") - remi_dec_rev) * 100
    pf25h2 = (e("2025H2") - remi_dec + remi_2h) / (rev("2025H2") - remi_dec_rev + remi_2h_rev) * 100
    fct = A["facts"]
    leg26h1_with_remi = (fct["grp_ebitda_1h26"] - okey_j - diy) / (fct["grp_rev_1h26"] - fct["okey_rev_since"] - fct["diy_rev"]) * 100
    modern = [
        pair("2021H1→2021H2 (без Billa и «Семьи»)", m("2021H1"), leg21h2, "H1", 0.10),
        pair("2021H2→2022H1 (Billa в обоих)", m("2021H2"), m("2022H1"), "H2", 0.05),
        pair("2022H1→2022H2", m("2022H1"), m("2022H2"), "H1"),
        pair("2022H2→2023H1", m("2022H2"), m("2023H1"), "H2"),
        pair("2023H1→2023H2 (без «Монетки»)", m("2023H1"), leg23h2, "H1", 0.15),
        pair("2023H2 проформа («Монетка» полное 2П)→2024H1", pf23h2, m("2024H1"), "H2", 0.25),
        pair("2024H1→2024H2 (без «Улыбки» и разового дохода)", m("2024H1"), leg24h2, "H1", 0.05),
        pair("2024H2 проформа («Улыбка» полное 2П)→2025H1", pf24h2, m("2025H1"), "H2", 0.15),
        pair("2025H1→2025H2 (без «Реми»)", m("2025H1"), leg25h2, "H1", 0.05),
        pair("2025H2 проформа («Реми» полное 2П)→2026H1 (без «О'КЕЙ» и «Дом Ленты»)", pf25h2, leg26h1_with_remi, "H2", 0.15),
    ]
    # --- сезонность A-C5 (аудит 30.09.2026, margin-01): медиана 2П − 1П на ОБЩЕМ периметре —
    #     переходы 1П → 2П тех же пар (2021, 2022, 2024, 2025; 2023 — год скачка «Монетки»), а не
    #     сырые полугодия, разбавленные покупками только во 2П (Billa и «Семья» 2021, «Реми» 2025).
    #     Ключ книги seasonal_h1_pp = −медиана/2, округление до 0,01 п.п. (как в regimes.py).
    h1h2 = [p for p in modern if p["prev_half"] == "H1" and not p["pair"].startswith("2023H1")]
    gaps = [p["m_curr"] - p["m_prev"] for p in h1h2]
    gap = st.median(gaps)
    s_key = round(-gap / 2 / 100, 4)
    s_h1 = s_key * 100
    season_common = {"pairs": {p["pair"]: p["m_curr"] - p["m_prev"] for p in h1h2}, "median_gap_pp": gap,
                     "mean_gap_pp": st.fmean(gaps), "seasonal_h1_pp": s_key,
                     "raw_2021_2025_ex2023_pp": S["seasonality"]["summary"]["сырые 2П−1П 2021–2025 без 2023"]["median"]}
    P("\nсезонность на общем периметре (2П − 1П, п.п.): " + "; ".join(f"{k.split(' ')[0]} {v:+.2f}" for k, v in season_common["pairs"].items())
      + f" → медиана {gap:+.3f} (среднее {st.fmean(gaps):+.3f}; сырые полугодия 2021–2025 без 2023 — "
      f"{season_common['raw_2021_2025_ex2023_pp']:+.3f}) → seasonal_h1_pp {s_key:+.4f}: поправка 1П {s_h1:+.2f} п.п., 2П {-s_h1:+.2f} п.п.")
    for p in modern:
        # x = маржа без сезонности: 1П — m − s_h1; 2П — m + s_h1
        x_prev = p["m_prev"] - s_h1 if p["prev_half"] == "H1" else p["m_prev"] + s_h1
        x_curr = p["m_curr"] + s_h1 if p["prev_half"] == "H1" else p["m_curr"] - s_h1
        p["d"] = x_curr - x_prev
    P("\n## Лента, современная эпоха: пары на общем периметре (п.п.): m_prev → m_curr; d (без сезонности); se")
    for p in modern:
        P(f"{p['pair']}: {p['m_prev']:.2f} → {p['m_curr']:.2f}; d {p['d']:+.2f}; se {p['se']:.2f}")
    d_mod = [p["d"] for p in modern]
    crisis = ("2021H2→2022H1 (Billa в обоих)", "2022H2→2023H1", "2023H1→2023H2 (без «Монетки»)")
    d_calm = [p["d"] for p in modern if p["pair"] not in crisis]
    fd_mod = fd_stats(d_mod)
    fd_calm = fd_stats(d_calm)

    # --- эпоха гипермаркетов 2013H1–2021H1
    hyper_keys = [k for k in sorted(H) if k[4:] in ("H1", "H2") and "2013H1" <= k <= "2021H1"]
    # сезонность эпохи — медиана центрированных оценок эпохи (series.py)
    gap_h = S["seasonality"]["summary"]["гипермаркеты 2013–2019 (центр., чисто)"]["median"]
    sh = -gap_h / 2
    x_h = [m(k) - sh if k.endswith("H1") else m(k) + sh for k in hyper_keys]
    d_h_all = [x_h[i] - x_h[i - 1] for i in range(1, len(x_h))]
    d_h = [x_h[i] - x_h[i - 1] for i in range(1, len(x_h))
           if not (hyper_keys[i] in S["covid_halves"] or hyper_keys[i - 1] in S["covid_halves"])]
    fd_hyp = fd_stats(d_h)
    fit_hyp = ar1_fit(x_h[: hyper_keys.index("2019H2") + 1], True)
    fit_hyp_h1 = ar1_fit([m(k) for k in hyper_keys[: hyper_keys.index("2019H2") + 1]], True,
                         [k.endswith("H1") for k in hyper_keys[: hyper_keys.index("2019H2") + 1]])
    P(f"\n## эпоха гипермаркетов 2013H1–2021H1: сезонность эпохи 2П − 1П {gap_h:+.2f} п.п.")
    P("d (без ковидных пар): " + ", ".join(f"{d:+.2f}" for d in d_h))

    # --- Магнит 2017H1–2026H1 и X5 2023H1–2026H1
    mh = peers["magnit_halves"]
    mk = sorted(mh)
    mm = [mh[k]["ebitda_bn"] / mh[k]["revenue_bn"] * 100 for k in mk]
    fit_mag_h1 = ar1_fit(mm, True, [k.endswith("H1") for k in mk])
    mag_raw = [mm[i] - mm[i - 1] for i in range(1, len(mm))]
    # сезонность Магнита: медиана 2П − 1П
    mg = st.median([mm[i] - mm[i - 1] for i in range(1, len(mm)) if mk[i].endswith("H2")])
    xm = [v + mg / 2 if k.endswith("H1") else v - mg / 2 for k, v in zip(mk, mm)]
    fd_mag = fd_stats([xm[i] - xm[i - 1] for i in range(1, len(xm))])
    fit_mag = ar1_fit(xm, True)
    xq = peers["x5_quarters"]
    x5h = {}
    for y in range(2023, 2027):
        for h, qs in (("H1", (1, 2)), ("H2", (3, 4))):
            ks = [f"{y}Q{q}" for q in qs]
            if all(k in xq for k in ks):
                x5h[f"{y}{h}"] = sum(xq[k]["ebitda_bn"] for k in ks) / sum(xq[k]["revenue_bn"] for k in ks) * 100
    x5k = sorted(x5h)
    x5v = [x5h[k] for k in x5k]
    g5 = st.median([x5v[i] - x5v[i - 1] for i in range(1, len(x5v)) if x5k[i].endswith("H2")])
    x5x = [v + g5 / 2 if k.endswith("H1") else v - g5 / 2 for k, v in zip(x5k, x5v)]
    fd_x5 = fd_stats([x5x[i] - x5x[i - 1] for i in range(1, len(x5x))])
    P(f"\n## сверка: Магнит {mk[0]}–{mk[-1]} (сезонность 2П − 1П {mg:+.2f}); X5 {x5k[0]}–{x5k[-1]} "
      f"(2П − 1П {g5:+.2f}); X5 по полугодиям: " + ", ".join(f"{k} {v:.2f}" for k, v in x5h.items()))

    def show(name, fd):
        r = fd["by_rho"]
        extra = f"; ρ из автокорр. приростов {fd['rho_from_lag1']:+.2f}" if "rho_from_lag1" in fd else ""
        P(f"{name}: n {fd['n']}; sd(d) {fd['sd_d']:.3f} (робастно {fd['robust_sd_d']:.3f}); "
          f"σ_стац при ρ 0,5/0,6/0,7 — {r['0.5']['sigma_stat']:.2f}/{r['0.6']['sigma_stat']:.2f}/{r['0.7']['sigma_stat']:.2f} "
          f"(робастно {r['0.5']['robust_sigma_stat']:.2f}/{r['0.6']['robust_sigma_stat']:.2f}/{r['0.7']['robust_sigma_stat']:.2f}); "
          f"σ_инн при ρ 0,7 — {r['0.7']['sigma_innov']:.2f} (робастно {r['0.7']['robust_sigma_innov']:.2f}){extra}")
    P("\n## сводка по приростам (п.п.)")
    show("Лента, современная эпоха (все 10 пар)", fd_mod)
    show("Лента, современная эпоха без кризиса 2022–2023 (7 пар)", fd_calm)
    show("Лента, гипермаркеты 2013H1–2021H1 без ковида", fd_hyp)
    show("Магнит 2017H1–2026H1", fd_mag)
    show("X5 2023H1–2026H1", fd_x5)
    P("\n## прямой МНК AR(1) с трендом (п.п.)")
    for name, f in (("Лента, гипермаркеты 2013H1–2019H2 (очищ.)", fit_hyp),
                    ("Лента, гипермаркеты 2013H1–2019H2 (фиктивная H1)", fit_hyp_h1),
                    ("Магнит 2017H1–2026H1 (очищ.)", fit_mag), ("Магнит 2017H1–2026H1 (фиктивная H1)", fit_mag_h1)):
        P(f"{name}: ρ {f['rho']:+.3f}; σ_инн {f['sigma_innov']:.3f}; σ_стац {f['sigma_stat']:.3f}; n {f['n']}")

    # --- выбор книги по правилу, записанному до расчёта (README, «σ и ρ»); DESIGN D9: без эпохи одних
    # гипермаркетов — её оценки только справочно.
    # ρ: ряд Ленты на проформе ρ не идентифицирует (10 разорванных пар); ρ = среднее (МНК Магнита с
    #    поправкой Кендалла ρ̂ + (1 + 3ρ̂)/n; историческая оценка 850oa 0,51), округлённое вверх до 0,1;
    # σ_стац = медиана четырёх оценок sd(d)/√(2(1 − ρ)) при ρ книги (Лента современная: все пары и
    #    робастно; Магнит; X5), округлённая до 0,1 п.п.; диапазоны — суждение: ρ 0,3–0,7; σ 0,5–1,0 п.п.
    def kendall(f):
        return f["rho"] + (1 + 3 * f["rho"]) / f["n"]
    rho_parts = {"Магнит (МНК, Кендалл)": kendall(fit_mag), "850oa (история 2017H1–2026H1)": 0.51}
    rho_ref = {"Лента гипермаркеты (МНК, Кендалл; справочно)": kendall(fit_hyp)}
    rho_book = math.ceil(st.fmean(rho_parts.values()) * 10 - 1e-9) / 10
    denom = math.sqrt(2 * (1 - rho_book))
    sig_parts = {"Лента совр. (все пары)": fd_mod["sd_d"] / denom, "Лента совр. (робастно)": fd_mod["robust_sd_d"] / denom,
                 "Магнит": fd_mag["sd_d"] / denom, "X5": fd_x5["sd_d"] / denom}
    sig_ref = {"Лента гипермаркеты (справочно)": fd_hyp["sd_d"] / denom}
    sig_book = round(st.median(sig_parts.values()), 1)
    choice = {"rho": rho_book, "rho_range": [0.3, 0.7], "rho_parts": rho_parts, "rho_ref": rho_ref,
              "sigma_pp": round(sig_book / 100, 4), "sigma_range": [0.005, 0.010], "sigma_parts": sig_parts,
              "sigma_ref": sig_ref, "innovation_pp": sig_book * math.sqrt(1 - rho_book ** 2)}
    P("\n## выбор книги (правило README; эпоха гипермаркетов — справочно, DESIGN D9)")
    P("ρ: " + "; ".join(f"{k} {v:.3f}" for k, v in rho_parts.items()) + f" → среднее {st.fmean(rho_parts.values()):.3f} → ρ = {rho_book}"
      + "; справочно: " + "; ".join(f"{k} {v:.3f}" for k, v in rho_ref.items()))
    P("σ_стац при ρ книги: " + "; ".join(f"{k} {v:.2f}" for k, v in sig_parts.items()) + f" → медиана → σ = {sig_book:.1f} п.п.; справочно: "
      + "; ".join(f"{k} {v:.2f}" for k, v in sig_ref.items()))
    P(f"инновация книги {choice['innovation_pp']:.2f} п.п.")

    res = {"schema": "lenta-book-1.0-margin-ar1-v1", "acquired_facts": af, "season_h1_pp": s_h1,
           "season_common": season_common,
           "bridges": {"billa_5m": billa, "semya_4m": semya, "monetka_q4_2023": mon_q4, "monetka_9m_2023": mon_9m,
                       "monetka_9m_rev": mon_rev_9m, "ulybka_dec_2024": uly_dec, "ulybka_2024_pf": uly_fy,
                       "remi_dec_2025": remi_dec},
           "legacy_margins": {"2021H2": leg21h2, "2023H2": leg23h2, "2024H2": leg24h2, "2025H2": leg25h2,
                              "2023H2_pf": pf23h2, "2024H2_pf": pf24h2, "2025H2_pf": pf25h2,
                              "2026H1_ex_okey_diy": leg26h1_with_remi},
           "modern_pairs": modern, "fd_modern": fd_mod, "fd_modern_calm": fd_calm, "fd_hyper": fd_hyp,
           "fd_magnit": fd_mag, "fd_x5": fd_x5, "x5_halves": x5h,
           "ols": {"lenta_hyper": fit_hyp, "lenta_hyper_h1dummy": fit_hyp_h1, "magnit": fit_mag, "magnit_h1dummy": fit_mag_h1},
           "choice": choice}
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
