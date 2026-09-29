# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 5: цели режимов (margin.regimes.<r>.target) как смесь
«унаследованный периметр + приобретённый периметр × доля сходимости» с весами выручки,
проверки против гайденса ≥7 % за 2026 и стратегии 8 % в 2028, ожидания модели,
цена ошибки по правилу большого пальца.

Цель режима r (годовой уровень, без сезонности):
  m_r(t) = (1 − w_A(t))·m_L,r(t) + w_A(t)·[m_A0 + f_r(t)·(m_L,r(t) − m_A0)],
  m_L,r(t) = m_L0 + g_L(t)·(m_L,r,LT − m_L0)     — унаследованный периметр: свой путь AR(1) от якоря,
                                                  g_L — средняя по полугодиям года доля пути 1 − ρ^n
                                                  (n — полугодий от якоря, ρ — ar1_out, A-C4);
  f_r(t) = f_r,LT·g(t)                            — доля сходимости приобретённого периметра;
  g(t) — J-кривая перехода одной покупки (Billa: 0 на якоре → 1 к 2030) — только у приобретённого
  периметра (решение ведущего по аудиту 30.09.2026, п. 20, margin-07).
Ключи целей — 2026H2, годы 2027…2035 и LT (2036): каждый год — со своими весами периметра (аудит
30.09.2026, п. 8, margin-04: ключ LT с весами 2036 г. ядро применяло бы с 2031 г.).
m_L0, m_A0 — маржи периметров 1П2026 (anchor_out.json) без сезонности (s из series_out.json).
w_A(t) — доля приобретённого периметра в выручке (временный путь до листа «Выручка»).
Вывод: regimes_out.json, regimes_out.txt, regimes_out.yaml (фрагмент целей).
"""
from __future__ import annotations

import json

from _common import HERE, dump

OUT_JSON = HERE / "regimes_out.json"
OUT_TXT = HERE / "regimes_out.txt"
OUT_YAML = HERE / "regimes_out.yaml"

YEARS_LT = [str(y) for y in range(2031, 2036)]
KEYS = ["2026H2", "2027", "2028", "2029", "2030"] + YEARS_LT + ["LT"]
G = {"2026H2": 0.10, "2027": 0.35, "2028": 0.65, "2029": 0.90, "2030": 1.00, **{y: 1.00 for y in YEARS_LT},
     "LT": 1.00}   # J-кривая перехода приобретённого периметра (суждение: Billa → «Супер Лента»)
W_A = {"2026H1": None, "2026H2": 0.145, "2027": 0.142, "2028": 0.136, "2029": 0.130, "2030": 0.124,
       **{y: 0.120 for y in YEARS_LT}, "LT": 0.120}
# ↑ временный путь доли приобретённого периметра («О'КЕЙ» + «Дом Лента» + «Реми»): 1П2026 — факт проформы
#   (anchor_out.json → acquired.share); 2027 — разгон «Дом Ленты» после ребрендинга и сходимость плотности
#   «О'КЕЙ» уравновешивают органику унаследованного периметра (≈+11 %); 2028–2030 — приобретённый +6 %/год
#   против +11 %/год (открытия «Монетки» и «Улыбки»); после 2030 — постоянная доля. Заменяется путём сети клетки.
LEGACY_FULL = 0.077      # унаследованный периметр в «полном»: пик 2024–2025 в годовом уровне (2П2024 без разовой статьи — 7,72; 1П2025 — 7,65)
LEGACY_STRESS_DROP = 0.010   # «стресс»: унаследованный периметр −1,0 п.п. к 1П2026 (труд, аренда, регулирование наценки)
F_LT = {"stress": 0.0, "floor": 0.0, "partial": 0.5, "full": 1.0}
LEGACY_SHARE = {"stress": None, "floor": 0.0, "partial": 0.5, "full": 1.0}   # доля пути m_L0 → LEGACY_FULL
RUB_PER_0P1PP_PERM = 60.0   # ₽/акцию на 0,1 п.п. постоянной маржи: 1 % EV ≈ 26,5 ₽; 0,1 п.п. ≈ 1,7 % EBITDA ≈ 1,7–2,9 % EV (эластичность 1,0–1,7) → 45–80 ₽, центр 60 (README)


def legacy_path(rho: float) -> dict:
    """Доля пути унаследованного периметра к его цели LT по ключам: AR(1) от якоря — после n полугодий
    пройдено 1 − ρ^n; ключ года — среднее двух полугодий; LT — 1."""
    out = {"2026H2": 1 - rho}
    for y in range(2027, 2036):
        n = 2 * (y - 2026)
        out[str(y)] = ((1 - rho ** n) + (1 - rho ** (n + 1))) / 2
    out["LT"] = 1.0
    return out


def main():
    S = json.loads((HERE / "series_out.json").read_text(encoding="utf-8"))
    A = json.loads((HERE / "anchor_out.json").read_text(encoding="utf-8"))
    R = json.loads((HERE / "refclass_out.json").read_text(encoding="utf-8"))
    AR = json.loads((HERE / "ar1_out.json").read_text(encoding="utf-8"))
    s_h1 = AR["season_common"]["seasonal_h1_pp"]  # ключ seasonal_h1_pp: общий периметр (ar1.py, margin-01)
    gap = -2 * s_h1
    G_L = legacy_path(AR["choice"]["rho"])
    mL0 = A["legacy"]["margin"] - s_h1
    mA0 = A["acquired"]["margin"] - s_h1
    wA0 = A["acquired"]["share"]
    m_pf = A["margin_pro_forma"]
    anchor_level = m_pf - s_h1
    probs = R["book"]
    L = []
    P = L.append
    P("# regimes.py — цели режимов (смесь периметров)")
    P(f"сезонность (общий периметр, ar1.py): 2П − 1П = {gap * 100:+.3f} п.п. → seasonal_h1_pp = {s_h1:+.4f}")
    P("доля пути унаследованного периметра (AR(1) от якоря, ρ " + f"{AR['choice']['rho']}): "
      + ", ".join(f"{k} {v:.3f}" for k, v in G_L.items()) + "; J-кривая приобретённого: "
      + ", ".join(f"{k} {G[k]:.2f}" for k in ("2026H2", "2027", "2028", "2029", "2030")))
    P(f"якорь (годовой уровень): проформа {m_pf * 100:.3f} % → {anchor_level * 100:.3f} %; унаследованный m_L0 {mL0 * 100:.3f} %; "
      f"приобретённый m_A0 {mA0 * 100:.3f} % (доля {wA0:.4f}); проверка смеси {((1 - wA0) * mL0 + wA0 * mA0) * 100:.3f} %")

    def legacy_lt(r):
        if r == "stress":
            return mL0 - LEGACY_STRESS_DROP
        return mL0 + LEGACY_SHARE[r] * (LEGACY_FULL - mL0)

    targets, parts = {}, {}
    for r in ("stress", "floor", "partial", "full"):
        tr = {"2026H1": round(anchor_level, 6)}
        pr = {}
        for k in KEYS:
            g = G[k]
            mL = mL0 + G_L[k] * (legacy_lt(r) - mL0)
            f = F_LT[r] * g
            mA = mA0 + f * (mL - mA0)
            w = W_A[k]
            tr[k] = round((1 - w) * mL + w * mA, 6)
            pr[k] = {"m_legacy": mL, "f": f, "g": g, "g_legacy": G_L[k], "m_acquired": mA, "w_acquired": w}
        targets[r], parts[r] = tr, pr
    P("\nцели (годовой уровень, % выручки): режим; 2026H1 (якорь); " + "; ".join(KEYS))
    for r, tr in targets.items():
        P(f"{r}; " + "; ".join(f"{tr[k] * 100:.2f}" for k in ["2026H1"] + KEYS))
    P("\nразложение LT: режим; унаследованный m_L,LT; доля сходимости f; приобретённый m_A,LT; вес w_A")
    for r in targets:
        p = parts[r]["LT"]
        P(f"{r}; {p['m_legacy'] * 100:.2f}; {p['f']:.2f}; {p['m_acquired'] * 100:.2f}; {p['w_acquired']:.3f}")

    # --- ожидания модели и проверки
    exp = {k: sum(probs[r] * targets[r][k] for r in targets) for k in KEYS}
    m2h = {r: targets[r]["2026H2"] - s_h1 for r in targets}            # маржа полугодия 2П2026 (сезонность −s)
    e2h = sum(probs[r] * m2h[r] for r in targets)
    ratio = S["halves"]["2025H2"]["revenue"] / S["halves"]["2025H1"]["revenue"]
    ratio24 = S["halves"]["2024H2"]["revenue"] / S["halves"]["2024H1"]["revenue"]
    rev2h = A["revenue_pro_forma_1h26"] * (ratio + ratio24) / 2
    need2h = (0.07 * (A["facts"]["grp_rev_1h26"] + rev2h) - A["facts"]["grp_ebitda_1h26"]) / rev2h
    fy_model = (A["facts"]["grp_ebitda_1h26"] + e2h * rev2h) / (A["facts"]["grp_rev_1h26"] + rev2h)
    P(f"\nвероятности (refclass): " + ", ".join(f"{r} {probs[r] * 100:.1f} %" for r in targets))
    P("ожидание цели (годовой уровень): " + ", ".join(f"{k} {exp[k] * 100:.2f}" for k in KEYS))
    P(f"маржа 2П2026 по режимам: " + ", ".join(f"{r} {m2h[r] * 100:.2f}" for r in targets) + f"; ожидание {e2h * 100:.2f} %")
    P(f"гайденс ≥7 % за 2026: выручка 2П ≈ проформа 1П × {((ratio + ratio24) / 2):.3f} = {rev2h:.1f} млрд → нужна маржа 2П ≥ {need2h * 100:.2f} %; "
      f"ожидание модели за год {fy_model * 100:.2f} %; режимов, дающих гайденс: {sum(1 for r in m2h if m2h[r] >= need2h)} из 4")
    strat = {r: targets[r]["2028"] for r in targets}
    P("стратегия 8 % в 2028 (сверка, периметр стратегии включает будущие сделки): " +
      ", ".join(f"{r} {v * 100:.2f}" for r, v in strat.items()))
    # квартальные ожидания 3 и 4 кв. 2026 (для нау-каста и журнала)
    qo = S["quarter_offsets"]["offset_pp"]
    q3 = e2h * 100 + qo["Q3"]
    q4 = e2h * 100 + qo["Q4"]
    P(f"ожидание кварталов 2П2026 (ядро + quarter_offset_pp из series.py): 3 кв. {q3:.2f} %, 4 кв. {q4:.2f} %")

    # --- цена ошибки (правило большого пальца)
    cost = {r: probs[r] * RUB_PER_0P1PP_PERM for r in targets}
    e_lt = exp["LT"]
    P(f"\nE[m_LT] = {e_lt * 100:.2f} % (2025 — {S['annual']['2025']['margin_norm'] * 100:.2f} %, проформа 1П2026 в годовом уровне — {anchor_level * 100:.2f} %)")
    P("цена ошибки цели LT режима (±0,1 п.п.), ₽/акцию: " + ", ".join(f"{r} ±{c:.0f}" for r, c in cost.items()) +
      f"; сдвиг всех целей LT на 0,1 п.п. — ±{RUB_PER_0P1PP_PERM:.0f}")

    # фрагмент целей
    y = ["margin:", "  regimes:"]
    for r, tr in targets.items():
        y.append(f"    {r}:")
        y.append("      target: {" + ", ".join(f'"{k}": {tr[k]:.4f}' if k != "LT" else f"LT: {tr[k]:.4f}" for k in ["2026H1"] + KEYS) + "}")
    OUT_YAML.write_text("\n".join(y) + "\n", encoding="utf-8")
    res = {"schema": "lenta-book-1.0-margin-regimes-v1", "seasonal_h1_pp": s_h1, "anchor_level": anchor_level,
           "m_legacy0": mL0, "m_acquired0": mA0, "w_acquired0": wA0, "G": G, "G_legacy": G_L, "W_A": W_A,
           "legacy_full": LEGACY_FULL, "legacy_stress_drop": LEGACY_STRESS_DROP, "f_lt": F_LT,
           "targets": targets, "parts": parts, "probs": probs, "expected_target": exp,
           "margin_2026H2_by_regime": m2h, "expected_margin_2026H2": e2h,
           "guidance": {"rev_2h_est": rev2h, "need_2h": need2h, "fy_model": fy_model},
           "strategy_2028": strat, "quarter_expect_2026": {"Q3": q3 / 100, "Q4": q4 / 100},
           "cost_rub_per_0p1pp": cost, "rub_per_0p1pp_perm": RUB_PER_0P1PP_PERM}
    dump(res, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
