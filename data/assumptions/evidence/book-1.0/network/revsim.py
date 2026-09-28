# -*- coding: utf-8 -*-
"""Упрощённая копия блока «сеть + выручка» ядра (850oa model/core.py, обобщённый на сегменты DESIGN D3) — только для
проверок листа до появления ядра Ленты. Не оценка: маржи, capex и дисконта здесь нет.

  yoy:     R(p) = R(p−2) · A_eff(p)/A_eff(p−2) · (1 + LFL_seg(p)),  LFL_seg = (1+чек)(1+трафик) − 1 + lfl_offset
           чек = k·прод.ИПЦ + s + НДС + однородность (1 − k)(π_LT,w − π_LT,N)·w(t), w: 0 в 2027 → 1 в 2031
  level:   R(p) = A_eff(p) · плотность(p)/2 · индекс ИПЦ мира (DIY)
  revenue: R(p) = R(p−2) · (1 + LFL группы + growth(p)) (опт)
  A_eff — шаг «сеть» ядра: открытия/закрытия долей площади в год /2, созревание когорт, плотность новой площади d,
  закрытия с продуктивностью cp; исторические когорты плотные (две младшие) — как в листе.
"""
from __future__ import annotations

import copy

import netlib as L

# миры книги 850oa (worlds_source.json sha256 7296567d…, food_cpi_yoy_avg / cpi_yoy_avg, % г/г, средние периода)
WORLD_FOOD = {
    'N': [5.1, 5.7, 5.0, 4.5, 4.3] + [4.3] * 16,
    'H': [5.3, 7.0, 7.2, 6.9, 6.6, 6.3, 6.1] + [6.0] * 14,
    'M': [5.2, 6.1, 5.8, 7.2, 8.6, 9.8, 10.6, 11.0, 11.0, 10.9, 11.0, 11.8, 12.8, 13.0, 12.1, 11.0, 10.4, 10.3, 10.3,
          10.3, 10.3],
}
WORLD_CPI = {
    'N': [6.5, 5.6, 5.0, 4.3, 4.1] + [4.0] * 16,
    'H': [6.6, 6.6, 6.7, 6.4, 6.1, 5.8, 5.6] + [5.5] * 14,
    'M': [6.5, 5.8, 5.4, 6.7, 8.1, 9.3, 10.1, 10.5, 10.5, 10.4, 10.5, 11.3, 12.3, 12.5, 11.6, 10.5, 9.9, 9.8, 9.8, 9.8,
          9.8],
}
LT_INFL = {'N': 4.0, 'H': 5.5, 'M': 9.8}
PERIODS = [f'{y}H{h}' for y in range(2026, 2037) for h in (1, 2)][1:]     # 2026H2 … 2036H2


def path(pth, p):
    if isinstance(pth, (int, float)):
        return float(pth)
    if p in pth:
        return pth[p]
    y = int(p[:4])
    if str(y) in pth:
        return pth[str(y)]
    pts = sorted((int(k), v) for k, v in pth.items() if k.isdigit())
    if not pts:
        return pth['LT']
    if y <= pts[0][0]:
        return pts[0][1]
    if y > pts[-1][0]:
        return pth.get('LT', pts[-1][1])
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if y0 <= y <= y1:
            return v0 + (v1 - v0) * (y - y0) / (y1 - y0)
    return pth['LT']


def simulate(book, world='H', demand='base', growth='mid', regime='partial', periods=PERIODS):
    """book: dict с ключами facts.segments и revenue (как фрагмент листа). → {seg: {p: выручка}}, итого."""
    F, R = book['facts']['segments'], book['revenue']
    mat = R['maturity_curve']
    food = dict(zip(PERIODS, [x / 100 for x in WORLD_FOOD[world]]))
    cpi = dict(zip(PERIODS, [x / 100 for x in WORLD_CPI[world]]))
    k = R['ticket_k'][demand]
    hom = R['ticket_lt_homogeneity']
    out, tot = {}, {p: 0.0 for p in periods}
    lfl_grp = {}
    for p in periods:
        y = int(p[:4])
        w = 0.0
        if hom.get('enabled'):
            w = min(1.0, max(0.0, (y - hom['ramp_from']) / (hom['ramp_to'] - hom['ramp_from'])))
        tk = (k * food[p] + path(R['ticket_shift'][demand], p) + path(R['vat_adjustment'], p)
              + (1 - k) * (LT_INFL[world] - LT_INFL['N']) / 100 * w)
        lfl_grp[p] = (1 + tk) * (1 + path(R['traffic'][demand], p)) - 1
    for seg, S in R['segments'].items():
        f = F[seg]
        mode = S['mode']
        rev = {kk: v for kk, v in f['revenue'].items() if v is not None}
        if mode == 'revenue':
            for p in periods:
                prev = f'{int(p[:4]) - 1}{p[4:]}'
                rev[p] = rev[prev] * (1 + lfl_grp[p] + path(S['growth'], p))
            out[seg] = {p: rev[p] for p in periods}
            continue
        d, cp = S['new_space_density'], S['closed_productivity']
        coh = list(f.get('new_area_gross_hist') or [0.0, 0.0])
        dense = f.get('new_area_dense_cohorts', 0)
        a_end = f['area_end']
        e_end = a_end - L.immature(coh, mat, d, dense)
        hist = dict(f['eff_area_avg_hist'])
        sp = S['space'][growth]
        off = S.get('lfl_offset', 0.0)
        if isinstance(off, dict) and regime in off:
            off = off[regime]
        pidx = 1.0
        n_hist = len(coh)
        for p in periods:
            go, cl = path(sp['gross_open'], p), path(sp['close'], p)
            opened, closed = a_end * go / 2, a_end * cl / 2
            # когорта на позиции pos: историческая при pos < n_hist; плотная — младшие `dense` исторические и все
            # когорты прогноза (как model/core.py 850oa с facts.new_area_dense_cohorts)
            maturing = sum(coh[-a] * (mat[a] - mat[a - 1]) * (d if (len(coh) - a) >= n_hist - dense else 1.0)
                           for a in range(1, min(len(mat), len(coh) + 1)))
            e_new = e_end - closed * cp + opened * mat[0] * d + maturing
            av = (e_end + e_new) / 2
            a_end, e_end = a_end + opened - closed, e_new
            coh.append(opened)
            if mode == 'level':
                pidx *= (1 + cpi[p]) ** 0.5
                rev[p] = av * path(S['density_path'], p) / 2 / 1e3 * pidx
            else:
                prev = f'{int(p[:4]) - 1}{p[4:]}'
                rev[p] = rev[prev] * av / hist[prev] * (1 + lfl_grp[p] + path(off, p))
            hist[p] = av
        out[seg] = {p: rev[p] for p in periods}
    for seg in out:
        for p in periods:
            tot[p] += out[seg][p]
    return out, tot, lfl_grp


def with_override(book, dotted, value):
    b = copy.deepcopy(book)
    node = b
    parts = dotted.split('.')
    for x in parts[:-1]:
        node = node[x]
    node[parts[-1]] = value
    return b
