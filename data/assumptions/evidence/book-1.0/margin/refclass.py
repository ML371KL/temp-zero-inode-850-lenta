# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 4: вероятности режимов (A-P2) из двух классов с правилом сведения,
заданным ДО подсчёта (README, раздел «Правило сведения»; текст правила — RULE ниже).

Класс E — эпизоды падения маржи (копия класса 850oa, книга 1.5, правило B; inputs/episodes_850oa.json).
Класс I — интеграции поглощений в продуктовой рознице (inputs/integrations.json): T0 — год
консолидации; исход — маржа группы в среднем за T0+3…T0+4 (для Ленты T0 = 2026 → 2029–2030,
когда цели режимов выходят на LT); f = (m_исход − m_проформа,T0)/(m_покупатель,T0 − m_проформа,T0) —
доля «разбавления» маржи покупкой, которая вернулась; пороги f — как у класса E
(стресс < −0,10; дно −0,10…0,25; частичный 0,25…0,75; полный ≥ 0,75). Сделки без разбавления
(маржа покупки выше маржи покупателя) и с ненаблюдаемым исходом в класс не входят.
Вывод: refclass_out.json, refclass_out.txt.
"""
from __future__ import annotations

import json
import math

from _common import HERE, dump

OUT_JSON = HERE / "refclass_out.json"
OUT_TXT = HERE / "refclass_out.txt"
REGIMES = ("stress", "floor", "partial", "full")
RULE = (
    "P(режим) = w_I·P_I + w_E·P_E, где P_I и P_E — частоты классов со сглаживанием Лапласа ½ "
    "((k + ½)/(n + 2) по каждому из четырёх режимов); w_I — доля падения маржи группы от 2025 к проформе "
    "1П2026 (в годовом уровне), объяснённая составом периметра (приобретённые сети), w_E = 1 − w_I — доля, "
    "объяснённая унаследованным периметром (органика). В классе I эпизоды класса надёжности C имеют вес ½. "
    "Книга берёт результат, округлённый до 0,5 п.п.; отклонение книги от правила — только записью суждения "
    "с ценой в ₽."
)


def classify(f):
    if f < -0.10:
        return "stress"
    if f < 0.25:
        return "floor"
    if f < 0.75:
        return "partial"
    return "full"


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, c - h), min(1.0, c + h))


def main():
    E = json.loads((HERE / "inputs" / "episodes_850oa.json").read_text(encoding="utf-8"))
    I = json.loads((HERE / "inputs" / "integrations.json").read_text(encoding="utf-8"))
    A = json.loads((HERE / "anchor_out.json").read_text(encoding="utf-8"))
    S = json.loads((HERE / "series_out.json").read_text(encoding="utf-8"))
    L = []
    P = L.append
    P("# refclass.py — вероятности режимов из двух классов")
    P("правило (задано до подсчёта): " + RULE)

    # --- класс E: f по правилу B из листа 850oa
    series = E["series"]
    e_rows = []
    for ep in E["episodes"]:
        if ep.get("B") is None and ep.get("series") is None:
            continue
        if ep.get("series"):
            ser = {int(k): v for k, v in series[ep["series"]]["values"].items()}
            peak_y = ep["peak_year"]
            peak = ser[peak_y]
            t0 = peak_y + 4
            m0 = ser[t0]
            out_vals = [ser[y] for y in (t0 + 4, t0 + 5, t0 + 6) if y in ser]
            outcome = sum(out_vals) / len(out_vals)
        else:
            (peak_y, peak), (t0, m0, outcome) = ep["peak"], ep["B"]
        f = (outcome - m0) / (peak - m0)
        e_rows.append({"id": ep["id"], "rel": ep["rel"], "f": f, "class": classify(f)})
    kE = {r: sum(1 for x in e_rows if x["class"] == r) for r in REGIMES}
    nE = len(e_rows)
    P(f"\n## класс E (эпизоды 850oa, правило B): n = {nE}")
    for x in e_rows:
        P(f"{x['id']} ({x['rel']}): f {x['f']:+.2f} → {x['class']}")

    # --- класс I
    i_rows = []
    P("\n## класс I (интеграции поглощений): T0; m_покупатель; m_проформа; исход (T0+3…T0+4); f; класс; надёжность")
    for c in I["cases"]:
        if c.get("excluded"):
            P(f"{c['id']}: НЕ В КЛАССЕ — {c['excluded']}")
            continue
        f = (c["m_outcome"] - c["m_pf_t0"]) / (c["m_acquirer_t0"] - c["m_pf_t0"])
        if "f_override" in c:
            f = c["f_override"]
        w = 0.5 if c["rel"] == "C" else 1.0
        cl = classify(f)
        i_rows.append({"id": c["id"], "rel": c["rel"], "f": f, "class": cl, "w": w})
        P(f"{c['id']}: {c['t0']}; {c['m_acquirer_t0']:.2f}; {c['m_pf_t0']:.2f}; {c['m_outcome']:.2f}; f {f:+.2f}; {cl}; {c['rel']} (вес {w})")
    kI = {r: sum(x["w"] for x in i_rows if x["class"] == r) for r in REGIMES}
    nI = sum(x["w"] for x in i_rows)

    def lap(k, n):
        return {r: (k[r] + 0.5) / (n + 2) for r in REGIMES}
    pE, pI = lap(kE, nE), lap(kI, nI)

    # --- вес классов из разложения падения маржи (anchor_out + series_out)
    s_h1 = -S["seasonality"]["summary"]["сырые 2П−1П 2021–2025 без 2023"]["median"] / 2 / 100
    m25 = S["annual"]["2025"]["margin_norm"]
    m_pf_a = A["margin_pro_forma"] - s_h1                       # проформа 1П2026 в годовом уровне
    m_leg_a = A["legacy"]["margin"] - s_h1                      # унаследованный периметр в годовом уровне
    w_acq = A["acquired"]["share"]
    drop_total = m25 - m_pf_a
    drop_legacy = (m25 - m_leg_a) * (1 - w_acq)                 # органика: падение маржи унаследованного периметра × его вес
    drop_mix = drop_total - drop_legacy                         # состав: разбавление приобретёнными сетями
    wI = max(0.0, min(1.0, drop_mix / drop_total))
    wE = 1 - wI
    P(f"\n## вес классов: маржа 2025 {m25 * 100:.2f} %; проформа 1П2026 (год. уровень) {m_pf_a * 100:.2f} %; "
      f"падение {drop_total * 100:.2f} п.п.: органика {drop_legacy * 100:.2f}, состав {drop_mix * 100:.2f} → w_I {wI:.3f}, w_E {wE:.3f}")

    comb = {r: wI * pI[r] + wE * pE[r] for r in REGIMES}
    book = {r: round(comb[r] * 200) / 200 for r in REGIMES}
    diff = round(1 - sum(book.values()), 6)
    if abs(diff) > 1e-9:  # невязку округления — в самый вероятный режим
        top = max(book, key=book.get)
        book[top] = round(book[top] + diff, 6)
    P("\n## частоты (сглаживание Лапласа ½), %")
    P("режим; класс E (k/n); класс I (k/n); P_E; P_I; сведение; книга (округл. 0,5 п.п.); Уилсон 95 % E; Уилсон 95 % I")
    for r in REGIMES:
        wE_ = wilson(kE[r], nE)
        wI_ = wilson(kI[r], nI)
        P(f"{r}; {kE[r]}/{nE}; {kI[r]:g}/{nI:g}; {pE[r] * 100:.1f}; {pI[r] * 100:.1f}; {comb[r] * 100:.2f}; {book[r] * 100:.1f}; "
          f"{wE_[0] * 100:.0f}–{wE_[1] * 100:.0f}; {wI_[0] * 100:.0f}–{wI_[1] * 100:.0f}")
    sens = {
        "только класс I": pI,
        "только класс E": pE,
        "веса ½/½": {r: 0.5 * pI[r] + 0.5 * pE[r] for r in REGIMES},
        "веса по n": {r: (nI * pI[r] + nE * pE[r]) / (nI + nE) for r in REGIMES},
        "класс I без надёжности C": lap({r: sum(1 for x in i_rows if x["class"] == r and x["rel"] != "C") for r in REGIMES},
                                        sum(1 for x in i_rows if x["rel"] != "C")),
    }
    P("\n## чувствительность правила (стресс / дно / частичный / полный, %)")
    for k, v in sens.items():
        P(f"{k}: " + " / ".join(f"{v[r] * 100:.1f}" for r in REGIMES))

    res = {"schema": "lenta-book-1.0-margin-refclass-v1", "rule": RULE,
           "class_E": {"n": nE, "counts": kE, "rows": e_rows, "p_laplace": pE},
           "class_I": {"n_weighted": nI, "counts_weighted": kI, "rows": i_rows, "p_laplace": pI},
           "weights": {"w_I": wI, "w_E": wE, "drop_total": drop_total, "drop_legacy": drop_legacy, "drop_mix": drop_mix,
                       "m_2025": m25, "m_pf_annual": m_pf_a, "m_legacy_annual": m_leg_a},
           "combined": comb, "book": book, "sensitivity": sens}
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
