# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 6: A-C6 (margin.cash_lease_adj_pct), доля труда (margin.labor_share_total),
доля арендованной площади (margin.line_drivers.leased_share), κ переноса расходов по режимам
(margin.cost_passthrough_kappa).

A-C6 — как в 850oa (evidence/agent-margin/cashrent.py): денежные арендные платежи по МСФО 16
(основная сумма, ДДС + проценты по аренде = разница процентных расходов баз) минус «прибавка»
EBITDA МСФО 16 к EBITDA IAS 17; поправка = −(платежи − прибавка)/выручка; центр — медиана
полугодий 2019H1–2026H1.
Доля труда = оплата труда в коммерческих расходах (датабук PL «Labor costs») + оплата труда в
себестоимости (МСФО, прим. «Себестоимость»: 2023–2025; 1П — не раскрыта, берётся доля 2025 г.).
Вывод: lease_labor_out.json, lease_labor_out.txt.
"""
from __future__ import annotations

import json
import re
import statistics as st

from _common import HERE, databook_rows, dump, pdf_text, pl_value

OUT_JSON = HERE / "lease_labor_out.json"
OUT_TXT = HERE / "lease_labor_out.txt"


def halves_value(label, basis, sheet="PL"):
    out = {}
    for y in range(2019, 2027):
        ys = str(y)
        h1 = pl_value(label, basis, "1H", ys, sheet)
        fy = pl_value(label, basis, "FY", ys, sheet)
        if h1 is not None:
            out[f"{y}H1"] = h1
        if fy is not None and h1 is not None:
            out[f"{y}H2"] = fy - h1
    return out


def cos_labor(name, year_label):
    """Оплата труда в себестоимости (тыс. руб. → млрд) из прим. «Себестоимость» годового МСФО."""
    txt = pdf_text(name)
    m = re.search(r"Себестоимость за год, закончившийся 31 декабря " + year_label +
                  r" г\., включает расходы на оплату\s*труда в размере\s*(\d{1,3}(?: \d{3})+) тыс", txt)
    return float(m.group(1).replace(" ", "")) / 1e6 if m else None


def own_share():
    rows = databook_rows("Operating Results", 116)
    hdr = rows[6]
    lab = {str(r[0]).strip(): i for i, r in enumerate(rows) if r[0]}
    out = {}
    for j, h in enumerate(hdr):
        if h and str(h).strip() in ("FY 2020", "FY 2021", "FY2022", "FY2023", "FY2024", "FY2025", "1H 2026"):
            own, rent = rows[lab["Owned"]][j], rows[lab["Rented"]][j]
            if own and rent:
                out[str(h).strip()] = own / (own + rent)
    return out


def main():
    L = []
    P = L.append
    P("# lease_labor.py — A-C6, доля труда, аренда, κ")
    rev = halves_value("Sales", "IAS 17")
    e16 = halves_value("EBITDA", "IFRS 16")
    e17 = halves_value("EBITDA", "IAS 17")
    int16 = halves_value("Interest expense", "IFRS 16")
    int17 = halves_value("Interest expense", "IAS 17")
    princ = halves_value("Payments for the principal portion of the lease liabilities", "IFRS 16", sheet="CF")
    P("\n## A-C6 по полугодиям (млрд): период; выручка; прибавка МСФО16−IAS17; платежи (осн. + %); разрыв; поправка, % выручки")
    rows = []
    for k in sorted(rev):
        if k not in e16 or k not in princ or princ[k] is None:
            continue
        add = e16[k] - e17[k]
        li = -(int16[k] - int17[k])
        cash = -princ[k] + li
        gap = cash - add
        adj = -gap / rev[k]
        rows.append({"half": k, "revenue": rev[k], "addback": add, "lease_interest": li, "principal": -princ[k],
                     "cash": cash, "gap": gap, "adj_pct": adj})
        P(f"{k}; {rev[k]:.1f}; {add:.2f}; {cash:.2f}; {gap:+.2f}; {adj * 100:+.3f}")
    adjs = [r["adj_pct"] for r in rows]
    med = st.median(adjs)
    recent = [r["adj_pct"] for r in rows if r["half"] >= "2024H1"]
    P(f"медиана 2019H1–2026H1 {med * 100:+.3f} %; медиана 2024H1–2026H1 {st.median(recent) * 100:+.3f} %; "
      f"мин {min(adjs) * 100:+.3f}, макс {max(adjs) * 100:+.3f}")
    # сверка 1П2026 с прим. 7 (платежи 7,758 + 11,501 = 19,26)
    r26 = [r for r in rows if r["half"] == "2026H1"][0]
    P(f"сверка 1П2026: платежи по базам {r26['cash']:.3f} против прим. 7 (7,758 + 11,501 = 19,260)")

    # --- доля труда
    lab_sga = {y: -pl_value("Labor costs", "IAS 17", "FY", str(y)) for y in (2023, 2024, 2025)}
    rev_fy = {y: pl_value("Sales", "IAS 17", "FY", str(y)) for y in (2023, 2024, 2025)}
    cos = {2023: cos_labor("ifrs_mkpao/ifrs_FY2024.pdf", "2023") or None,
           2024: cos_labor("ifrs_mkpao/ifrs_FY2024.pdf", "2024"),
           2025: cos_labor("ifrs_mkpao/ifrs_FY2025.pdf", "2025")}
    if cos[2023] is None:
        m = re.search(r"за год, закончившийся 31 декабря 2023 г\. –\s*(\d{1,3}(?: \d{3})+) тыс", pdf_text("ifrs_mkpao/ifrs_FY2024.pdf"))
        cos[2023] = float(m.group(1).replace(" ", "")) / 1e6
    P("\n## доля труда (% выручки): год; в коммерческих; в себестоимости; итого")
    share = {}
    for y in (2023, 2024, 2025):
        a, b = lab_sga[y] / rev_fy[y], cos[y] / rev_fy[y]
        share[y] = a + b
        P(f"{y}; {a * 100:.2f}; {b * 100:.2f}; {(a + b) * 100:.2f}")
    lab1h = -pl_value("Labor costs", "IAS 17", "1H", "2026") / pl_value("Sales", "IAS 17", "1H", "2026")
    cos_share25 = cos[2025] / rev_fy[2025]
    total1h = lab1h + cos_share25
    P(f"1П2026: в коммерческих {lab1h * 100:.2f} % + в себестоимости (доля 2025) {cos_share25 * 100:.2f} % = {total1h * 100:.2f} %")

    # --- аренда
    osh = own_share()
    P("\n## доля собственной площади (датабук, Operating Results): " + ", ".join(f"{k} {v * 100:.1f} %" for k, v in osh.items()))
    rent_pct = {k: -(pl_value("Lease of premises", "IAS 17", k[-2:] == "H1" and "1H" or "FY", k[:4]) +
                     pl_value("Land and equipment lease", "IAS 17", k[-2:] == "H1" and "1H" or "FY", k[:4])) /
                pl_value("Sales", "IAS 17", k[-2:] == "H1" and "1H" or "FY", k[:4])
                for k in ("2023FY", "2024FY", "2025FY", "2026H1")}
    P("аренда IAS 17, % выручки: " + ", ".join(f"{k} {v * 100:.2f}" for k, v in rent_pct.items()))

    # --- κ по режимам: κ = 1 + Δm_унасл,LT / накопленное давление расходов за 4 года
    R = json.loads((HERE / "regimes_out.json").read_text(encoding="utf-8"))
    pressure_labor = (total1h - share[2023]) / 2.5        # п.п./год, 2023 → 1П2026
    pressure_rent = (rent_pct["2026H1"] - rent_pct["2023FY"]) / 2.5
    pressure = 0.5 * (pressure_labor + pressure_rent)      # половина — эффект состава (малые форматы, покупки), половина — давление расходов (суждение)
    cum4 = 4 * pressure
    kappa = {r: round(1 + (R["parts"][r]["LT"]["m_legacy"] - R["m_legacy0"]) / cum4, 2) for r in R["parts"]}
    P(f"\n## κ: рост доли труда {pressure_labor * 100:+.2f} п.п./год, аренды {pressure_rent * 100:+.2f} п.п./год (2023 → 1П2026); "
      f"давление расходов (половина — состав) {pressure * 100:.2f} п.п./год, за 4 года {cum4 * 100:.2f} п.п.")
    P("κ = 1 + Δm_унасл,LT / давление: " + ", ".join(f"{r} {v}" for r, v in kappa.items()))

    res = {"schema": "lenta-book-1.0-margin-lease-labor-v1",
           "cash_lease": {"rows": rows, "median": med, "median_2024_2026": st.median(recent), "min": min(adjs), "max": max(adjs)},
           "labor": {"share_by_year": share, "cos_labor": cos, "sga_1h26": lab1h, "cos_share_2025": cos_share25, "total_1h26": total1h},
           "own_share": osh, "rent_pct": rent_pct,
           "kappa": {"pressure_labor": pressure_labor, "pressure_rent": pressure_rent, "pressure": pressure,
                     "cum4": cum4, "values": kappa}}
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
