# -*- coding: utf-8 -*-
"""Лист «Маржа», передача листу фактов: EBITDA и выручка LTM на проформе 30.06.2026 (facts.anchor.ebitda_ltm,
revenue_ltm — ключи темы «Факты»; здесь — расчёт и диапазон).

LTM проформа = отчётные 2П2025 + 1П2026 (датабук, IAS 17)
  + «О'КЕЙ» 2П2025: выручка 142 млрд за 2025 (релиз 02.06.2026) × доля 2П гипермаркетов «Ленты» 2025 (датабук),
    маржа — суждение (между 2023–2024 и январём–маем 2026);
  + «О'КЕЙ» январь–май 2026: выручка и EBITDA из моста (anchor_out.json);
  + «Реми» июль–ноябрь 2025: проформа 2025 55,488 (МСФО 2025 прим. 8) × доля 2П (гипер + супер + у дома 2025)
    минус декабрь 5,100; маржа — как в мосте (≈1 %).
«Дом Лента» до консолидации в проформу выручки D3 не входит — и в EBITDA тоже.
Вывод: ltm_out.json, ltm_out.txt.
"""
from __future__ import annotations

import json
import math
import re

from _common import HERE, PRIMARY, dump, fq_table, pl_value

OUT_JSON = HERE / "ltm_out.json"
OUT_TXT = HERE / "ltm_out.txt"
OKEY_M_2H25 = (0.015, 0.030, 0.050)   # маржа EBITDA IAS 17 «О'КЕЙ» 2П2025 (суждение): январь–май 2026 ≈2,1 %; 2023–2024 ≈5 % (сегмент O'KEY МСФО 16 8,9 % минус аренда ≈3,5–4 п.п.)
REMI_M = (-0.02, 0.01, 0.04)


def tri_mean_sd(a, c, b):
    m = (a + b + c) / 3
    v = (a * a + b * b + c * c - a * b - a * c - b * c) / 18
    return m, math.sqrt(v)


def main():
    A = json.loads((HERE / "anchor_out.json").read_text(encoding="utf-8"))
    rel = (PRIMARY / "lentagroup_news" / "2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.txt").read_text(encoding="utf-8")
    okey_2025 = float(re.search(r"выручка которой составила (\d+) млрд рублей в 2025 году", rel).group(1))
    Q = fq_table()
    hyp = [Q[f"2025Q{i}"]["hyper"] for i in (1, 2, 3, 4)]
    sh_hyp = (hyp[2] + hyp[3]) / sum(hyp)
    base = [sum(Q[f"2025Q{i}"][k] for k in ("hyper", "super", "conv")) for i in (1, 2, 3, 4)]
    sh_mix = (base[2] + base[3]) / sum(base)
    okey_2h25_rev = okey_2025 * sh_hyp
    remi_pf25 = A["facts"]["remi_pf_2025"]
    remi_jn_rev = remi_pf25 * sh_mix - A["facts"]["remi_rev_dec"]
    rev_rep = pl_value("Sales", "IAS 17", "FY", "2025") - pl_value("Sales", "IAS 17", "1H", "2025") + pl_value("Sales", "IAS 17", "1H", "2026")
    e_rep = pl_value("EBITDA", "IAS 17", "FY", "2025") - pl_value("EBITDA", "IAS 17", "1H", "2025") + pl_value("EBITDA", "IAS 17", "1H", "2026")
    m_ok, sd_ok = tri_mean_sd(*OKEY_M_2H25)
    m_re, sd_re = tri_mean_sd(*REMI_M)
    e_ok2h = okey_2h25_rev * m_ok
    e_ok5 = A["mc"]["okey5_ebitda"]["p50"]
    e_re = remi_jn_rev * m_re
    ebitda = e_rep + e_ok2h + e_ok5 + e_re
    sd = math.sqrt((okey_2h25_rev * sd_ok) ** 2 + A["mc"]["okey5_ebitda"]["sd"] ** 2 + (remi_jn_rev * sd_re) ** 2)
    rev = rev_rep + okey_2h25_rev + A["okey_5m"]["revenue"] + remi_jn_rev
    L = [
        "# ltm_pro_forma.py — LTM на проформе 30.06.2026 (для листа фактов)",
        f"отчётные LTM (2П2025 + 1П2026): выручка {rev_rep:.3f}, EBITDA {e_rep:.3f} ({e_rep / rev_rep * 100:.2f} %)",
        f"«О'КЕЙ» 2025: {okey_2025:.0f} млрд (релиз 02.06.2026); доля 2П гипермаркетов «Ленты» 2025 {sh_hyp:.4f} → 2П2025 {okey_2h25_rev:.3f}; "
        f"маржа {m_ok * 100:.2f} % (1,5–5,0) → EBITDA {e_ok2h:.3f}",
        f"«О'КЕЙ» январь–май 2026: выручка {A['okey_5m']['revenue']:.3f}, EBITDA {e_ok5:.3f} (мост)",
        f"«Реми» июль–ноябрь 2025: {remi_pf25:.3f} × {sh_mix:.4f} − {A['facts']['remi_rev_dec']:.3f} = {remi_jn_rev:.3f}; маржа {m_re * 100:.2f} % → {e_re:.3f}",
        f"ИТОГО LTM на проформе: выручка {rev:.3f}, EBITDA {ebitda:.3f} ± {sd:.3f} (1σ; ≈{ebitda - 2 * sd:.1f}–{ebitda + 2 * sd:.1f}), маржа {ebitda / rev * 100:.2f} %",
        f"ЧД 117,41 (датабук, Debt, 30.06.2026) / EBITDA LTM проформа ≈ {117.41 / ebitda:.2f}× (отчётная 1,39×)",
    ]
    # полугодия проформы (facts.ebitda_pre16.pro_forma; D3: «Дом Лента» — только отчётный вклад)
    okey_1h25_rev = okey_2025 - okey_2h25_rev
    remi_1h25_rev = remi_pf25 * (1 - sh_mix)
    e_1h25 = pl_value("EBITDA", "IAS 17", "1H", "2025") + okey_1h25_rev * m_ok + remi_1h25_rev * m_re
    r_1h25 = pl_value("Sales", "IAS 17", "1H", "2025") + okey_1h25_rev + remi_1h25_rev
    sd_1h25 = math.sqrt((okey_1h25_rev * sd_ok) ** 2 + (remi_1h25_rev * sd_re) ** 2)
    e_2h25 = e_rep - pl_value("EBITDA", "IAS 17", "1H", "2026") + e_ok2h + e_re
    r_2h25 = rev_rep - pl_value("Sales", "IAS 17", "1H", "2026") + okey_2h25_rev + remi_jn_rev
    sd_2h25 = math.sqrt((okey_2h25_rev * sd_ok) ** 2 + (remi_jn_rev * sd_re) ** 2)
    e_1h26 = A["ebitda_pro_forma_1h26"]
    r_1h26 = A["revenue_pro_forma_1h26"]
    halves = {"2025H1": {"revenue": r_1h25, "ebitda": e_1h25, "sd": sd_1h25},
              "2025H2": {"revenue": r_2h25, "ebitda": e_2h25, "sd": sd_2h25},
              "2026H1": {"revenue": r_1h26, "ebitda": e_1h26, "sd": A["mc"]["okey5_ebitda"]["sd"]}}
    for k, h in halves.items():
        L.append(f"проформа {k}: выручка {h['revenue']:.3f}, EBITDA {h['ebitda']:.3f} ± {h['sd']:.3f}, маржа {h['ebitda'] / h['revenue'] * 100:.2f} %")
    dump({"schema": "lenta-book-1.0-margin-ltm-v1", "halves_pro_forma": halves, "revenue_ltm_reported": rev_rep, "ebitda_ltm_reported": e_rep,
          "okey_2025_revenue": okey_2025, "okey_2h25_revenue": okey_2h25_rev, "okey_2h25_ebitda": e_ok2h,
          "okey_5m_ebitda": e_ok5, "remi_jul_nov_revenue": remi_jn_rev, "remi_jul_nov_ebitda": e_re,
          "revenue_ltm_pro_forma": rev, "ebitda_ltm_pro_forma": ebitda, "ebitda_ltm_pro_forma_sd": sd,
          "params": {"okey_margin_2h25": OKEY_M_2H25, "remi_margin": REMI_M}}, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
