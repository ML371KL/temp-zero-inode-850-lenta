# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 1: ряд EBITDA-маржи Ленты по IAS 17 (полугодия 2013H1–2026H1,
кварталы 2020Q1–2026Q2), нормализации, периметровые разрывы, сезонность
(margin.seasonal_h1_pp) и квартальные поправки (margin.quarter_offset_pp).

Источник: датабук Ленты за 2 кв. 2026 (Lenta_Q22026_DATABOOK.xlsx), листы PL
(блок IAS 17, строки Sales / EBITDA / Labor costs / Lease of premises / Land and
equipment lease / Depreciation / Other operating income), CF (строка «Profit from
purchase» — доход от выгодной покупки), Financials quarterly (IAS 17, млн руб.).
2П = FY − 1П (расчёт). Вывод: series_out.json, series_out.txt.
"""
from __future__ import annotations

import statistics as st

from _common import (DATABOOK, HERE, dump, fmt, fq_table, manifest_sha, pl_value, sha256)

OUT_JSON = HERE / "series_out.json"
OUT_TXT = HERE / "series_out.txt"

FIELDS = {
    "revenue": "Sales",
    "gp": "Gross profit",
    "labor": "Labor costs",
    "da": "Depreciation",
    "lease_prem": "Lease of premises",
    "lease_land": "Land and equipment lease",
    "other_inc": "Other operating income",
    "ebitda": "EBITDA",
}

# Периметровые события: полугодие, где сеть консолидирована впервые (скачок периметра).
# Источники — примечания «Приобретение дочерних компаний» МСФО и релизы (research/02 §5.2).
PERIMETER = [
    {"half": "2016H2", "event": "Kesko: 11 гипермаркетов (ноябрь 2016)", "size": "малый",
     "source": "lentagroup_news 2016-11-30, 2016-12-07"},
    {"half": "2017H2", "event": "Holiday: 22 супермаркета Сибири (ноябрь 2017 – 2018)", "size": "малый",
     "source": "lentagroup_news 2017-11-07"},
    {"half": "2021H2", "event": "Billa (с авг. 2021; 5 мес.: выручка 9,5, убыток до налога 1,1) и «Семья» (сен. 2021; 4,2)",
     "size": "скачок", "source": "ifrs_FY2021, прим. 8, с. 47–49"},
    {"half": "2023H2", "event": "«Монетка» (с окт. 2023; 4 кв.: выручка 58,7, прибыль до налога 3,46)",
     "size": "скачок", "source": "ifrs_FY2023_v2_20240903, прим. 8, с. 47–49"},
    {"half": "2024H2", "event": "«Улыбка радуги» (с 01.12.2024; выручка 4,71) + доход от выгодной покупки 1,10 в прочих доходах",
     "size": "скачок (разовая статья)", "source": "ifrs_FY2024, прим. 8, с. 42–44; CF «Profit from purchase»"},
    {"half": "2025H1", "event": "«Молния-Spar» (с 24.06.2025; ≈1 неделя в 1П)", "size": "малый",
     "source": "ifrs_FY2025, прим. 8, с. 45–47"},
    {"half": "2025H2", "event": "«Реми» 67 % (с 03.12.2025; выручка 5,10, убыток до налога 0,11); «Молния» полное полугодие",
     "size": "скачок", "source": "ifrs_FY2025, прим. 8, с. 47–49"},
    {"half": "2026H1", "event": "«Дом Лента» (1 кв. 2026, поэтапно; 7,96 / −2,96), «О'КЕЙ» (с 02.06.2026; 9,36 / −0,91)",
     "size": "скачок", "source": "ifrs_1H2026, прим. 5, с. 16–19"},
]
JUMP_HALVES = [p["half"] for p in PERIMETER if p["size"].startswith("скачок")]
COVID_HALVES = ["2020H1", "2020H2"]


def halves():
    out = {}
    for y in range(2013, 2027):
        ys = str(y)
        h1 = {k: pl_value(lab, "IAS 17", "1H", ys) for k, lab in FIELDS.items()}
        if h1["revenue"] is not None:
            out[f"{y}H1"] = h1
        fy = {k: pl_value(lab, "IAS 17", "FY", ys) for k, lab in FIELDS.items()}
        if fy["revenue"] is not None and h1["revenue"] is not None:
            out[f"{y}H2"] = {k: (fy[k] - h1[k]) if (fy[k] is not None and h1[k] is not None) else None
                             for k in FIELDS}
            out[f"{y}FY"] = fy
    for y in (2010, 2011, 2012):
        fy = {k: pl_value(lab, "IAS 17", "FY", str(y)) for k, lab in FIELDS.items()}
        out[f"{y}FY"] = fy
    # IFRS 16 EBITDA (с 2019)
    for y in range(2019, 2027):
        ys = str(y)
        e1 = pl_value("EBITDA", "IFRS 16", "1H", ys)
        ef = pl_value("EBITDA", "IFRS 16", "FY", ys)
        if e1 is not None and f"{y}H1" in out:
            out[f"{y}H1"]["ebitda16"] = e1
        if ef is not None and e1 is not None and f"{y}H2" in out:
            out[f"{y}H2"]["ebitda16"] = ef - e1
            out[f"{y}FY"]["ebitda16"] = ef
    return out


def bargain_gain_2024() -> float:
    """Доход от выгодной покупки «Улыбки радуги» (CF, корректировка «Profit from purchase», FY2024)."""
    v = pl_value("Profit from purchase", "IAS 17", "FY", "2024", sheet="CF")
    v16 = pl_value("Profit from purchase", "IFRS 16", "FY", "2024", sheet="CF")
    assert v is not None and abs(v - v16) < 1e-6, (v, v16)
    return -v  # в ДДС — вычитаемая корректировка (отрицательная)


def main():
    H = halves()
    Q = fq_table()
    gain = bargain_gain_2024()
    lines = []
    P = lines.append
    P("# series.py — ряд маржи Ленты по IAS 17 (датабук 2 кв. 2026)")
    P(f"датабук sha256 {sha256(DATABOOK)}; в MANIFEST: {manifest_sha('Lenta_Q22026_DATABOOK.xlsx')}")
    P(f"доход от выгодной покупки FY2024 (CF «Profit from purchase», обе базы): {gain:.4f} млрд")

    # --- проверка кварталов против полугодий
    checks = []
    for y in range(2020, 2027):
        for h, qs in (("H1", ("Q1", "Q2")), ("H2", ("Q3", "Q4"))):
            key = f"{y}{h}"
            if key not in H or any(f"{y}{q}" not in Q or Q[f"{y}{q}"]["revenue"] is None for q in qs):
                continue
            rq = sum(Q[f"{y}{q}"]["revenue"] for q in qs)
            eq = sum(Q[f"{y}{q}"]["ebitda"] for q in qs)
            checks.append((key, rq - H[key]["revenue"], eq - H[key]["ebitda"]))
    P("\n## сверка «сумма кварталов = полугодие» (млрд): период; Δвыручка; ΔEBITDA")
    for k, dr, de in checks:
        P(f"{k}; {dr:+.3f}; {de:+.3f}")
    bad = [c for c in checks if abs(c[1]) > 0.5 or abs(c[2]) > 0.15]

    # --- ряд маржи
    series = {}
    order = [k for k in H if k[4:] in ("H1", "H2")]
    order.sort()
    for k in order:
        d = H[k]
        m = d["ebitda"] / d["revenue"]
        adj, note = 0.0, ""
        if k == "2024H2":
            adj, note = -gain, "без дохода от выгодной покупки «Улыбки радуги»"
        mn = (d["ebitda"] + adj) / d["revenue"]
        series[k] = {
            "revenue": round(d["revenue"], 4), "ebitda": round(d["ebitda"], 4),
            "margin": round(m, 6), "margin_norm": round(mn, 6), "norm_note": note,
            "gm": round(d["gp"] / d["revenue"], 6),
            "labor_pct": round(-d["labor"] / d["revenue"], 6),
            "rent_pct": round(-(d["lease_prem"] + d["lease_land"]) / d["revenue"], 6),
            "ebitdar_pct": round((d["ebitda"] - d["lease_prem"] - d["lease_land"]) / d["revenue"], 6),
            "ebitda16_margin": round(d["ebitda16"] / d["revenue"], 6) if d.get("ebitda16") is not None else None,
            "jump": k in JUMP_HALVES, "covid": k in COVID_HALVES,
        }
    annual = {}
    for y in range(2010, 2026):
        k = f"{y}FY"
        if k in H and H[k]["revenue"]:
            e = H[k]["ebitda"] - (gain if y == 2024 else 0.0)
            annual[str(y)] = {"margin": round(H[k]["ebitda"] / H[k]["revenue"], 6),
                              "margin_norm": round(e / H[k]["revenue"], 6)}

    P("\n## полугодия: период; выручка; EBITDA; маржа; маржа норм.; ВМ; персонал; аренда; EBITDAR; МСФО16; флаги")
    for k in order:
        s = series[k]
        fl = ("скачок периметра" if s["jump"] else "") + (" ковид" if s["covid"] else "")
        P(f"{k}; {fmt(s['revenue'],1)}; {fmt(s['ebitda'],2)}; {fmt(100*s['margin'])}; {fmt(100*s['margin_norm'])}; "
          f"{fmt(100*s['gm'])}; {fmt(100*s['labor_pct'])}; {fmt(100*s['rent_pct'])}; {fmt(100*s['ebitdar_pct'])}; "
          f"{fmt(100*s['ebitda16_margin']) if s['ebitda16_margin'] is not None else '—'}; {fl}")

    # --- сезонность полугодий
    def m(k):
        return series[k]["margin_norm"] * 100 if k in series else None

    raw = {}
    for y in range(2013, 2026):
        a, b = m(f"{y}H1"), m(f"{y}H2")
        if a is not None and b is not None:
            raw[y] = b - a
    # центрированные оценки (снимают локальный линейный тренд): 2П_t − ср(1П_t, 1П_t+1) и ср(2П_t−1, 2П_t) − 1П_t
    cent = []
    for y in range(2013, 2027):
        h1, h2, h1n, h2p = f"{y}H1", f"{y}H2", f"{y + 1}H1", f"{y - 1}H2"
        if all(x in series for x in (h1, h2, h1n)):
            trip = (h1, h2, h1n)
            clean = not any(series[t]["jump"] for t in trip[1:]) and not any(series[t]["covid"] for t in trip)
            cent.append({"center": h2, "value": m(h2) - (m(h1) + m(h1n)) / 2, "members": trip, "clean": clean})
        if all(x in series for x in (h2p, h1, h2)):
            trip = (h2p, h1, h2)
            clean = not any(series[t]["jump"] for t in trip[1:]) and not any(series[t]["covid"] for t in trip)
            cent.append({"center": h1, "value": (m(h2p) + m(h2)) / 2 - m(h1), "members": trip, "clean": clean})
    P("\n## сезонность: 2П − 1П по годам (п.п., норм.)")
    for y, v in raw.items():
        P(f"{y}; {v:+.2f}" + ("  [ковид]" if y == 2020 else "") + ("  [скачок периметра во 2П]" if f"{y}H2" in JUMP_HALVES else ""))
    P("\n## центрированные оценки «2П − 1П» (тройки без скачка периметра внутри и без ковида)")
    for c in cent:
        P(f"{c['center']}; {c['value']:+.2f}; {'чисто' if c['clean'] else 'исключено'}; {'/'.join(c['members'])}")

    def med(vals):
        return st.median(vals) if vals else None
    eras = {
        "гипермаркеты 2013–2019 (центр., чисто)": [c["value"] for c in cent if c["clean"] and int(c["center"][:4]) <= 2019],
        "современная 2021–2026 (центр., чисто)": [c["value"] for c in cent if c["clean"] and int(c["center"][:4]) >= 2021],
        "все чистые (центр.)": [c["value"] for c in cent if c["clean"]],
        "сырые 2П−1П 2014–2025 без 2020 и 2023": [v for y, v in raw.items() if y >= 2014 and y not in (2020, 2023)],
        "сырые 2П−1П 2017–2025 без 2020 и 2023": [v for y, v in raw.items() if y >= 2017 and y not in (2020, 2023)],
        "сырые 2П−1П 2021–2025 без 2023": [v for y, v in raw.items() if y >= 2021 and y != 2023],
    }
    P("\n## сводка сезонности (2П − 1П, п.п.): медиана / среднее / n")
    seas_summary = {}
    for name, vals in eras.items():
        seas_summary[name] = {"median": med(vals), "mean": st.mean(vals) if vals else None, "n": len(vals)}
        P(f"{name}: {fmt(med(vals))} / {fmt(st.mean(vals)) if vals else '—'} / {len(vals)}")

    # --- квартальные поправки
    qm = {q: (Q[q]["ebitda"] / Q[q]["revenue"] * 100) for q in Q if Q[q]["revenue"]}
    qadj = dict(qm)
    if "2024Q4" in qm:  # разовый доход 2024 — в 4 кв. (консолидация «Улыбки» 01.12.2024)
        qadj["2024Q4"] = (Q["2024Q4"]["ebitda"] - gain) / Q["2024Q4"]["revenue"] * 100
    P("\n## кварталы IAS 17: квартал; выручка; EBITDA; маржа; маржа норм.")
    for q in sorted(Q):
        if Q[q]["revenue"]:
            P(f"{q}; {fmt(Q[q]['revenue'],1)}; {fmt(Q[q]['ebitda'],2)}; {fmt(qm[q])}; {fmt(qadj[q])}")
    gaps = {"H1": [], "H2": []}
    shares = {"Q1": [], "Q2": [], "Q3": [], "Q4": []}
    within = {"Q1": [], "Q3": []}
    excl_h1 = {2020, 2023}           # ковид; кризис малых форматов 1П2023 учитывается, 2020 — нет
    excl_h2 = {2020, 2023}           # ковид; «Монетка» только в 4 кв. 2023
    rows_gap = []
    for y in range(2020, 2027):
        q = {i: f"{y}Q{i}" for i in (1, 2, 3, 4)}
        if all(q[i] in qadj for i in (1, 2)) and y not in excl_h1:
            gaps["H1"].append(qadj[q[2]] - qadj[q[1]])
            r1, r2 = Q[q[1]]["revenue"], Q[q[2]]["revenue"]
            within["Q1"].append(r1 / (r1 + r2))
            rows_gap.append(f"{y} 1П: Q2 − Q1 = {qadj[q[2]] - qadj[q[1]]:+.2f}; доля Q1 в 1П {r1 / (r1 + r2):.3f}")
        if all(q[i] in qadj for i in (3, 4)) and y not in excl_h2:
            gaps["H2"].append(qadj[q[4]] - qadj[q[3]])
            r3, r4 = Q[q[3]]["revenue"], Q[q[4]]["revenue"]
            within["Q3"].append(r3 / (r3 + r4))
            rows_gap.append(f"{y} 2П: Q4 − Q3 = {qadj[q[4]] - qadj[q[3]]:+.2f}; доля Q3 в 2П {r3 / (r3 + r4):.3f}")
        if all(q[i] in Q and Q[q[i]]["revenue"] for i in (1, 2, 3, 4)) and y >= 2024:
            tot = sum(Q[q[i]]["revenue"] for i in (1, 2, 3, 4))
            for i in (1, 2, 3, 4):
                shares[f"Q{i}"].append(Q[q[i]]["revenue"] / tot)
    P("\n## внутриполугодовые разрывы маржи (п.п.; без 2020 и 2023; 4 кв. 2024 без разовой статьи)")
    for r in rows_gap:
        P(r)
    # центр — медиана разрыва (2021 1П — база ковидного 1 кв. 2020, выброс); среднее — чувствительность
    g1, g2 = st.median(gaps["H1"]), st.median(gaps["H2"])
    g1m, g2m = st.mean(gaps["H1"]), st.mean(gaps["H2"])
    s1, s3 = st.mean(within["Q1"]), st.mean(within["Q3"])
    off = {"Q1": -g1 * (1 - s1), "Q2": g1 * s1, "Q3": -g2 * (1 - s3), "Q4": g2 * s3}
    off_mean = {"Q1": -g1m * (1 - s1), "Q2": g1m * s1, "Q3": -g2m * (1 - s3), "Q4": g2m * s3}
    P(f"разрыв 1П (Q2 − Q1): медиана {g1:+.3f}, среднее {g1m:+.3f} п.п. (n={len(gaps['H1'])}, мин {min(gaps['H1']):+.2f}, "
      f"макс {max(gaps['H1']):+.2f}); доля Q1 в 1П {s1:.4f}")
    P(f"разрыв 2П (Q4 − Q3): медиана {g2:+.3f}, среднее {g2m:+.3f} п.п. (n={len(gaps['H2'])}, мин {min(gaps['H2']):+.2f}, "
      f"макс {max(gaps['H2']):+.2f}); доля Q3 в 2П {s3:.4f}")
    P("по среднему (чувствительность): " + ", ".join(f"{k} {v:+.3f}" for k, v in off_mean.items()))
    # доли кварталов внутри полугодия — ключ revenue.quarter_share листа «Сеть» (периметр с «О'КЕЙ»), если он есть:
    # ядро обнуляет поправки именно с этими весами (DESIGN D2)
    book_shares, src_shares = None, "исторические доли листа (фрагмента «Сеть» нет)"
    net = HERE.parents[2] / "fragments" / "network.yaml"
    if net.exists():
        import yaml
        qs_book = (yaml.safe_load(net.read_text(encoding="utf-8")).get("revenue") or {}).get("quarter_share")
        if qs_book:
            book_shares = (qs_book["Q1"], qs_book["Q3"])
            src_shares = f"revenue.quarter_share фрагмента «Сеть»: Q1 {qs_book['Q1']}, Q3 {qs_book['Q3']}"
    b1, b3 = book_shares if book_shares else (s1, s3)
    off_book = {"Q1": -g1 * (1 - b1), "Q2": g1 * b1, "Q3": -g2 * (1 - b3), "Q4": g2 * b3}
    P(f"на долях книги ({src_shares}): " + ", ".join(f"{k} {v:+.3f}" for k, v in off_book.items()))
    P("quarter_offset_pp (п.п. к марже полугодия; s·off обнуляется внутри полугодия): " +
      ", ".join(f"{k} {v:+.3f}" for k, v in off.items()))
    P(f"проверка нуля: 1П {s1 * off['Q1'] + (1 - s1) * off['Q2']:+.2e}; 2П {s3 * off['Q3'] + (1 - s3) * off['Q4']:+.2e}")
    qs = {k: st.mean(v) for k, v in shares.items()}
    P("доли кварталов в годовой выручке (2024–2025; справочно для revenue.quarter_share): " +
      ", ".join(f"{k} {v:.4f}" for k, v in qs.items()))

    res = {
        "schema": "lenta-book-1.0-margin-series-v1",
        "source": {"databook": "Lenta_Q22026_DATABOOK.xlsx", "sha256": sha256(DATABOOK),
                   "sheets": "PL (IAS 17 / IFRS 16), CF (Profit from purchase), Financials quarterly"},
        "bargain_gain_2024": round(gain, 6),
        "perimeter_events": PERIMETER, "jump_halves": JUMP_HALVES, "covid_halves": COVID_HALVES,
        "halves": series, "annual": annual,
        "quarters": {q: {"revenue": Q[q]["revenue"], "ebitda": Q[q]["ebitda"], "margin": round(qm[q] / 100, 6),
                         "margin_norm": round(qadj[q] / 100, 6),
                         "segments": {k: Q[q][k] for k in ("hyper", "super", "conv", "droge", "remi", "diy", "other", "wholesale")}}
                     for q in sorted(Q) if Q[q]["revenue"]},
        "quarter_checks": [{"half": k, "d_revenue": round(a, 4), "d_ebitda": round(b, 4)} for k, a, b in checks],
        "quarter_checks_ok": not bad,
        "seasonality": {"raw_h2_minus_h1_pp": {str(k): round(v, 4) for k, v in raw.items()},
                        "centered": [{**c, "value": round(c["value"], 4)} for c in cent],
                        "summary": seas_summary},
        "quarter_offsets": {"gap_h1_pp": g1, "gap_h2_pp": g2, "share_q1_in_h1": s1, "share_q3_in_h2": s3,
                            "offset_pp": off, "offset_pp_mean": off_mean, "offset_pp_book": off_book,
                            "book_shares": {"Q1_in_H1": b1, "Q3_in_H2": b3, "source": src_shares}, "gaps_h1": gaps["H1"], "gaps_h2": gaps["H2"],
                            "n_h1": len(gaps["H1"]), "n_h2": len(gaps["H2"])},
        "quarter_share_2024_2025": qs,
    }
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    if bad:
        raise SystemExit(f"сверка кварталов не сошлась: {bad}")


if __name__ == "__main__":
    main()
