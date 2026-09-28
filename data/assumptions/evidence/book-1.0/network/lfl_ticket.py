# -*- coding: utf-8 -*-
"""Лист «Сеть и выручка», часть 2: LFL-чек против ИПЦ, сдвиг s, трафик, поправка НДС, сдвиги форматов.

  1. МНК: LFL-чек группы (датабук, квартал г/г) = a + k × прод. ИПЦ (Росстат, средний уровень квартала г/г);
     выборки: все годы без пандемии 2020–2021 и без 2019 (смена НДС 18 → 20 %), 2016+, 2022+; то же к общему ИПЦ;
     по форматам (гипер, супер; у дома и дрогери — коротко). Стандартные ошибки — обычные МНК.
  2. Сдвиг s = LFL-чек − k × прод. ИПЦ по полугодиям (правило книги Магнита A-R2).
  3. НДС: LFL «Ленты» публикуется БЕЗ НДС (релизы 2019: «5,0 % без НДС … 5,9 % с НДС»); клин 2019 года (0,5–0,9 п.п.)
     → доля продаж по основной ставке s_std; клин 2026 (20 → 22 %) по форматам; поправка к модели чека.
  4. Трафик: история группы и форматов; сдвиги LFL форматов к группе (для `lfl_offset`).

  python -B lfl_ticket.py          → печать + lfl_ticket_out.json
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402

OUT = os.path.join(L.HERE, 'lfl_ticket_out.json')
COVID = {f'{y}Q{q}' for y in (2020, 2021) for q in (1, 2, 3, 4)}
VAT19 = {f'2019Q{q}' for q in (1, 2, 3, 4)}


def ols(X, y):
    """МНК без numpy: β, se(β), σ остатка, R², n."""
    n, p = len(y), len(X[0])
    xtx = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(p)] for a in range(p)]
    xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(p)]
    inv = _inv(xtx)
    beta = [sum(inv[a][b] * xty[b] for b in range(p)) for a in range(p)]
    res = [y[i] - sum(beta[a] * X[i][a] for a in range(p)) for i in range(n)]
    s2 = sum(r * r for r in res) / (n - p)
    se = [math.sqrt(s2 * inv[a][a]) for a in range(p)]
    ybar = sum(y) / n
    r2 = 1 - sum(r * r for r in res) / sum((v - ybar) ** 2 for v in y)
    return beta, se, math.sqrt(s2), r2, n


def _inv(m):
    n = len(m)
    a = [row[:] + [1.0 if i == j else 0.0 for j in range(n)] for i, row in enumerate(m)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(a[r][c]))
        a[c], a[piv] = a[piv], a[c]
        pv = a[c][c]
        a[c] = [v / pv for v in a[c]]
        for r in range(n):
            if r != c:
                f = a[r][c]
                a[r] = [v - f * w for v, w in zip(a[r], a[c])]
    return [row[n:] for row in a]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    D = L.load_databook()
    O = D['oper']
    cpi = L.load_cpi()
    quarters = sorted(q for q in O['revenue']['total'] if re.fullmatch(r'\d{4}Q\d', q))
    lfl = {}
    for fk, bk in (('group', 'lfl_group'), ('hyper', 'lfl_hyper'), ('super', 'lfl_super'), ('conv', 'lfl_conv'),
                   ('droge', 'lfl_droge')):
        lfl[fk] = {n: {q: O[bk][n][q] for q in quarters} for n in L.LFL_NAMES}
    # формат есть, если в квартале ненулевой LFL хотя бы по одной компоненте (нули датабука до запуска формата)
    def avail(fk, q):
        v = [lfl[fk][n][q] for n in L.LFL_NAMES]
        return all(isinstance(x, float) for x in v) and any(abs(x) > 1e-12 for x in v)

    cq = {nm: {q: L.cpi_period(cpi, nm, q) for q in quarters} for nm in ('food', 'all', 'nonfood')}
    # проверка: LFL датабука 2019 — без НДС (релиз 25.04.2019: 5,0 % без НДС; 25.07.2019: 0,8 %)
    rel = L.text_file('lentagroup_news/2019-04-25_vyruchka-i-operacionnye-rezultaty-za-1kv-2019g.txt')
    m = re.search(r'выросли на ([\d,]+)% без НДС.{0,200}?на ([\d,]+)% с НДС', rel, re.S)
    net19, gross19 = (float(m.group(1).replace(',', '.')), float(m.group(2).replace(',', '.')))
    assert abs(lfl['group']['sales']['2019Q1'] * 100 - net19) < 0.05, 'LFL датабука 2019Q1 ≠ «без НДС» релиза'
    vat_wedge_2019 = {}
    for fn, q in (('2019-04-25_vyruchka-i-operacionnye-rezultaty-za-1kv-2019g.txt', '2019Q1'),
                  ('2019-07-25_vyruchka-i-operacionnye-rezultaty-za-2kv-2019g.txt', '2019Q2'),
                  ('2019-10-28_3q2-019-sales-and-operating-results.txt', '2019Q3'),
                  ('2020-01-24_vyruchka-i-operacionnye-rezultaty-za-4kv-i-12-mesyacev-2019g.txt', '2019Q4')):
        t = L.text_file('lentagroup_news/' + fn)
        mm = re.search(r'(выросли|сократились) на ([\d,]+)% без НДС.{0,200}?(росту|сокращению).{0,80}?на ([\d,]+)%,? с НДС',
                       t, re.S)
        sgn1 = -1 if mm.group(1) == 'сократились' else 1
        sgn2 = -1 if mm.group(3) == 'сокращению' else 1
        net, gross = sgn1 * float(mm.group(2).replace(',', '.')), sgn2 * float(mm.group(4).replace(',', '.'))
        vat_wedge_2019[q] = dict(net=net, gross=gross, wedge_pp=gross - net)
    wedge19 = st.mean(v['wedge_pp'] for v in vat_wedge_2019.values()) / 100
    # s_std — доля продаж по основной ставке: клин = 0,02·s/(1,10 + 0,08·s) при 18 → 20 %
    s_std_2019 = wedge19 * 1.10 / (0.02 - 0.08 * wedge19)

    # --- 1. регрессии
    res = {}

    def run(name, fk, comp, cname, keep):
        qs = [q for q in quarters if keep(q) and avail(fk, q) and cq[cname][q] is not None]
        X = [[1.0, cq[cname][q]] for q in qs]
        y = [lfl[fk][comp][q] for q in qs]
        if len(qs) < 5:
            return None
        b, se, s, r2, n = ols(X, y)
        res[name] = dict(a=b[0], k=b[1], se_a=se[0], se_k=se[1], sigma=s, r2=r2, n=n, first=qs[0], last=qs[-1])
        return res[name]

    base = lambda q: q not in COVID and q not in VAT19 and q >= '2013Q1'            # noqa: E731
    run('group_food_all', 'group', 'ticket', 'food', base)
    run('group_food_2016', 'group', 'ticket', 'food', lambda q: base(q) and q >= '2016Q1')
    run('group_food_2022', 'group', 'ticket', 'food', lambda q: base(q) and q >= '2022Q1')
    run('group_cpi_all', 'group', 'ticket', 'all', base)
    run('group_cpi_2016', 'group', 'ticket', 'all', lambda q: base(q) and q >= '2016Q1')
    run('group_cpi_2022', 'group', 'ticket', 'all', lambda q: base(q) and q >= '2022Q1')
    run('hyper_food_2016', 'hyper', 'ticket', 'food', lambda q: base(q) and q >= '2016Q1')
    run('hyper_cpi_2016', 'hyper', 'ticket', 'all', lambda q: base(q) and q >= '2016Q1')
    run('super_food_2016', 'super', 'ticket', 'food', lambda q: base(q) and q >= '2016Q1')
    run('conv_food', 'conv', 'ticket', 'food', lambda q: base(q) and q >= '2024Q1')
    run('droge_nonfood', 'droge', 'ticket', 'nonfood', lambda q: base(q) and q >= '2025Q1')
    run('group_sales_food_2016', 'group', 'sales', 'food', lambda q: base(q) and q >= '2016Q1')
    # с пандемией — для диапазона
    run('group_food_incl_covid', 'group', 'ticket', 'food', lambda q: q not in VAT19 and q >= '2013Q1')

    # k с весами форматов периметра LFL (выручка 1П2026 по датабуку): гипер, супер, у дома (к прод. ИПЦ), дрогери
    # (к непрод. ИПЦ)
    wrev = {f: (O['revenue'][f]['2026Q1'] or 0) + (O['revenue'][f]['2026Q2'] or 0) for f in ('hyper', 'super', 'conv', 'droge')}
    kf = {'hyper': res['hyper_food_2016']['k'], 'super': res['super_food_2016']['k'], 'conv': res['conv_food']['k'],
          'droge': res['droge_nonfood']['k']}
    k_mix = sum(wrev[f] * kf[f] for f in wrev) / sum(wrev.values())
    k_mix_ex_short = sum(wrev[f] * (kf[f] if f in ('hyper', 'super') else res['group_food_2016']['k']) for f in wrev) / sum(wrev.values())
    res['k_mix'] = dict(weights={f: wrev[f] / sum(wrev.values()) for f in wrev}, k=kf, k_mix=k_mix,
                        k_mix_short_formats_at_group=k_mix_ex_short)

    # --- 2. сдвиг s по полугодиям при выбранном k
    k_book = 0.55                 # k книги (README §3): между собственной оценкой 0,44 (2016+) и пулом 850oa 0,60–0,64
    halves = sorted({q[:4] + ('H1' if q[5] in '12' else 'H2') for q in quarters if q >= '2013Q1'})
    def half_val(fk, comp, p):
        qs = L.quarters_of_half(p)
        if not all(avail(fk, q) for q in qs):
            return None
        return st.mean(lfl[fk][comp][q] for q in qs)
    shift = {}
    for p in halves:
        t = half_val('group', 'ticket', p)
        f = L.cpi_period(cpi, 'food', p)
        if t is None or f is None:
            continue
        shift[p] = dict(ticket=t, food=f, all=L.cpi_period(cpi, 'all', p), s=t - k_book * f)

    # --- 3. НДС 2026: клин 20 → 22 % по форматам и поправка чека
    # доли продаж по основной ставке (суждение; якорь — s_std 2019 года для сети из одних гипермаркетов):
    s_std = {'hyper': s_std_2019, 'okey': s_std_2019, 'super': 0.45, 'conv': 0.45, 'droge': 0.95, 'remi': 0.45,
             'diy': 1.00}
    wedge26 = {f: 0.02 * v / (1.10 + 0.10 * v) for f, v in s_std.items()}
    # клин в LFL: при полном переносе налога в полку нетто-чек не меняется, а прод. ИПЦ растёт на вклад НДС;
    # вклад НДС в прод. ИПЦ (январь 2026 м/м 101,95 против 101,33 годом раньше) — оценка: разность январских м/м
    # продовольственного ИПЦ 2026 и среднего января 2023–2025
    fl = cpi['food']
    jan = {y: fl[(y, 1)] / fl[(y - 1, 12)] - 1 for y in (2023, 2024, 2025, 2026)}
    vat_cpi_food = jan[2026] - st.mean(jan[y] for y in (2023, 2024, 2025))
    al = cpi['all']
    jan_all = {y: al[(y, 1)] / al[(y - 1, 12)] - 1 for y in (2023, 2024, 2025, 2026)}
    vat_cpi_all = jan_all[2026] - st.mean(jan_all[y] for y in (2023, 2024, 2025))
    nf = cpi['nonfood']
    jan_nf = {y: nf[(y, 1)] / nf[(y - 1, 12)] - 1 for y in (2023, 2024, 2025, 2026)}
    vat_cpi_nonfood = jan_nf[2026] - st.mean(jan_nf[y] for y in (2023, 2024, 2025))

    # --- 4. трафик и сдвиги форматов
    traffic = {}
    for fk in lfl:
        traffic[fk] = {}
        for y in range(2013, 2027):
            vals = [lfl[fk]['traffic'][f'{y}Q{q}'] for q in (1, 2, 3, 4) if f'{y}Q{q}' in quarters
                    and avail(fk, f'{y}Q{q}')]
            if vals:
                traffic[fk][y] = st.mean(vals)
    tr_g = traffic['group']
    tr_stats = dict(mean_2013_2019=st.mean(tr_g[y] for y in range(2013, 2020)),
                    mean_2022_2025=st.mean(tr_g[y] for y in range(2022, 2026)),
                    mean_ex_covid=st.mean(v for y, v in tr_g.items() if y not in (2020, 2021)),
                    min_ex_covid=min(v for y, v in tr_g.items() if y not in (2020, 2021)),
                    max_ex_covid=max(v for y, v in tr_g.items() if y not in (2020, 2021)),
                    h1_2026=half_val('group', 'traffic', '2026H1'))
    offsets = {}
    for fk in ('hyper', 'super', 'conv', 'droge'):
        offsets[fk] = {}
        for p in halves:
            a, g = half_val(fk, 'sales', p), half_val('group', 'sales', p)
            if a is not None and g is not None and p >= '2023H1':
                offsets[fk][p] = dict(sales=a - g, traffic=half_val(fk, 'traffic', p) - half_val('group', 'traffic', p),
                                      ticket=half_val(fk, 'ticket', p) - half_val('group', 'ticket', p))

    out = dict(regressions=res, k_book=k_book, shift_by_half=shift,
               vat=dict(lfl_is_net_of_vat=True, release_2019q1=dict(net=net19, gross=gross19), wedge_2019=vat_wedge_2019,
                        wedge_2019_mean=wedge19, s_std_2019=s_std_2019, s_std_by_segment=s_std,
                        wedge_2026_by_segment=wedge26, jan_mom_food=jan, vat_in_food_cpi_jan=vat_cpi_food,
                        jan_mom_all=jan_all, vat_in_cpi_jan=vat_cpi_all, jan_mom_nonfood=jan_nf,
                        vat_in_nonfood_cpi_jan=vat_cpi_nonfood),
               traffic_by_year=traffic, traffic_stats=tr_stats, offsets_by_half=offsets,
               cpi_quarters={nm: {q: v for q, v in d.items() if v is not None and q >= '2019Q1'} for nm, d in cq.items()})
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print('== МНК: LFL-чек (квартал г/г) = a + k × ИПЦ (средний уровень квартала г/г); без 2020–2021 и 2019 (НДС)')
    for k, r in res.items():
        if k == 'k_mix':
            print(f'k с весами форматов 1П2026: {r["k_mix"]:.3f} (у дома и дрогери — короткие ряды; с k группы для них: '
                  f'{r["k_mix_short_formats_at_group"]:.3f})')
            continue
        print(f'{k:24s} n={r["n"]:3d} {r["first"]}–{r["last"]}  k={r["k"]:.3f} (se {r["se_k"]:.3f})  '
              f'a={r["a"]*100:+.2f} п.п. (se {r["se_a"]*100:.2f})  σ={r["sigma"]*100:.2f}  R²={r["r2"]:.2f}')
    print(f'\n== Сдвиг s = чек − {k_book} × прод. ИПЦ, по полугодиям (п.п.)')
    print(' '.join(f'{p}:{v["s"]*100:+.1f}' for p, v in shift.items()))
    v = out['vat']
    print(f'\n== НДС: LFL датабука 2019 = «без НДС» релиза ({net19} %); клин 2019 по кварталам '
          f'{[round(x["wedge_pp"], 1) for x in vat_wedge_2019.values()]} п.п., среднее {wedge19*100:.2f} → '
          f's_std 2019 = {s_std_2019:.3f}')
    print('клин 2026 (20→22 %) по сегментам, п.п.:', {k: round(x * 100, 2) for k, x in wedge26.items()})
    print(f'январь м/м прод. ИПЦ: {({y: round(x*100, 2) for y, x in jan.items()})} → вклад НДС ≈ {vat_cpi_food*100:.2f} п.п.; '
          f'общий ИПЦ ≈ {vat_cpi_all*100:.2f}; непрод. ≈ {vat_cpi_nonfood*100:.2f}')
    print('\n== Трафик группы по годам:', {y: round(x * 100, 1) for y, x in tr_g.items()})
    print('статистика:', {k: round(x * 100, 2) for k, x in tr_stats.items()})
    for fk in ('hyper', 'super', 'conv', 'droge'):
        print(f'трафик {fk}:', {y: round(x * 100, 1) for y, x in traffic[fk].items() if y >= 2019})
    print('\n== Сдвиги LFL форматов к группе (выручка / трафик / чек, п.п.)')
    for fk, d in offsets.items():
        print(fk, ' '.join(f'{p}:{x["sales"]*100:+.1f}/{x["traffic"]*100:+.1f}/{x["ticket"]*100:+.1f}' for p, x in d.items()))


if __name__ == '__main__':
    main()
