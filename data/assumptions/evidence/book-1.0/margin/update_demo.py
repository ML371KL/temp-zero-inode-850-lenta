# -*- coding: utf-8 -*-
"""Лист «Маржа», шаг 7: поведение правила A-P2u на значениях книги (без ядра).

Повторяет формулу 850oa (model/grid.py::regime_likelihoods, cap_shift — переписано здесь, без импорта):
наблюдение 2026H1 — проформа с se (фильтр Калмана), затем факт 2026H2 из demo_values.
Печатает апостериорные вероятности, E[m_LT], «нейтральную маржу» 2П2026 (факт, при котором E[m_LT]
не меняется) и проверку мартингала в пространстве маржи: E_предиктивное[E[m_LT] после отчёта] − E[m_LT] сейчас
(с пределом сдвига 10 п.п. и без него). Проверка цены (медиана полосы) — на ядре (README, «Сжатие s»).
Вывод: update_demo_out.json, update_demo_out.txt.
"""
from __future__ import annotations

import json
import math

from _common import HERE, dump

OUT_JSON = HERE / "update_demo_out.json"
OUT_TXT = HERE / "update_demo_out.txt"
REG = ("stress", "floor", "partial", "full")


def main():
    R = json.loads((HERE / "regimes_out.json").read_text(encoding="utf-8"))
    A1 = json.loads((HERE / "ar1_out.json").read_text(encoding="utf-8"))
    A = json.loads((HERE / "anchor_out.json").read_text(encoding="utf-8"))
    tg = R["targets"]
    prior = R["probs"]
    s = R["seasonal_h1_pp"]
    rho = A1["choice"]["rho"]
    sigma = A1["choice"]["sigma_pp"]
    se0 = 0.001                      # se наблюдения 2026H1 (книга; README «Мост якоря»)
    obs0 = A["margin_pro_forma"]
    cap = 0.10
    demo = [0.050, 0.055, 0.060, 0.065, 0.070, 0.075, 0.080]
    L = []
    P = L.append
    P(f"# update_demo.py — A-P2u на книге: σ {sigma}, ρ {rho}, наблюдение 2026H1 {obs0} (se {se0}), предел сдвига {cap}")

    def season(p):
        return s if p.endswith("H1") else -s

    def step_lik(value, se, p, state, first):
        out, new = {}, {}
        for r in REG:
            dev = value - tg[r][p] - season(p)
            if first:
                pm, pv = 0.0, sigma ** 2
            else:
                pm = rho * state[r][0]
                pv = sigma ** 2 * (1 - rho ** 2) + rho ** 2 * state[r][1]
            err = dev - pm
            out[r] = math.exp(-0.5 * err * err / (pv + se * se))
            if se:
                gain = pv / (pv + se * se)
                new[r] = (pm + gain * err, (1 - gain) * pv)
            else:
                new[r] = (dev, 0.0)
        return out, new

    def cap_shift(pri, post, lim):
        adj = {r: min(max(post[r], pri[r] - lim), pri[r] + lim) for r in REG}
        z = sum(adj.values())
        return {r: adj[r] / z for r in REG}

    def update(pri, lik, lim):
        z = sum(pri[r] * lik[r] for r in REG)
        post = {r: pri[r] * lik[r] / z for r in REG}
        return cap_shift(pri, post, lim) if lim else post

    lik0, st0 = step_lik(obs0, se0, "2026H1", None, True)
    p1 = update(prior, lik0, cap)
    P("после 2026H1 (проформа, se): " + ", ".join(f"{r} {p1[r] * 100:.2f}" for r in REG) +
      "  (цели 2026H1 у режимов равны — наблюдение нейтрально)")

    def elt(p):
        return sum(p[r] * tg[r]["LT"] for r in REG)

    base = elt(p1)
    rows = []
    P("\nфакт 2П2026; вероятности стресс/дно/частичный/полный, %; E[m_LT], %; ΔE[m_LT], п.п.")
    for v in demo:
        lik, _ = step_lik(v, 0.0, "2026H2", st0, False)
        p2 = update(p1, lik, cap)
        rows.append({"obs": v, "probs": p2, "e_lt": elt(p2)})
        P(f"{v * 100:.1f}; " + " / ".join(f"{p2[r] * 100:.1f}" for r in REG) + f"; {elt(p2) * 100:.3f}; {(elt(p2) - base) * 100:+.3f}")
    # нейтральная маржа: бисекция по факту 2П2026
    lo, hi = 0.03, 0.10
    for _ in range(80):
        mid = (lo + hi) / 2
        lik, _ = step_lik(mid, 0.0, "2026H2", st0, False)
        if elt(update(p1, lik, cap)) > base:
            hi = mid
        else:
            lo = mid
    neutral = (lo + hi) / 2
    e2h = sum(p1[r] * (tg[r]["2026H2"] - s) for r in REG)
    P(f"\nнейтральная маржа 2П2026 (E[m_LT] не меняется): {neutral * 100:.3f} %; ожидание модели 2П2026 {e2h * 100:.3f} %")

    # мартингал в пространстве маржи: предиктивное распределение факта 2П2026 — смесь режимов
    def predictive_drift(lim):
        tot = 0.0
        for r in REG:
            mu = tg[r]["2026H2"] - s + rho * st0[r][0]
            var = sigma ** 2 * (1 - rho ** 2) + rho ** 2 * st0[r][1]
            sd = math.sqrt(var)
            # квадратура Гаусса — Эрмита (20 узлов через равномерную сетку ±6σ, Симпсон)
            nq = 241
            acc = 0.0
            for i in range(nq):
                z = -6 + 12 * i / (nq - 1)
                w = (1 if i in (0, nq - 1) else (4 if i % 2 else 2)) * (12 / (nq - 1)) / 3
                x = mu + z * sd
                lik, _ = step_lik(x, 0.0, "2026H2", st0, False)
                acc += w * math.exp(-0.5 * z * z) / math.sqrt(2 * math.pi) * elt(update(p1, lik, lim))
            tot += p1[r] * acc
        return tot - base
    d_cap = predictive_drift(cap)
    d_nocap = predictive_drift(None)
    P(f"мартингал E[m_LT]: дрейф после отчёта {d_nocap * 1e4:+.3f} б.п. без предела сдвига, {d_cap * 1e4:+.3f} б.п. с пределом 10 п.п.")
    dump({"schema": "lenta-book-1.0-margin-update-demo-v1", "sigma": sigma, "rho": rho, "se0": se0, "obs0": obs0,
          "after_anchor": p1, "rows": rows, "neutral_2026H2": neutral, "expected_2026H2": e2h,
          "martingale_drift_pp": {"no_cap": d_nocap * 100, "cap": d_cap * 100}}, OUT_JSON)
    OUT_TXT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
