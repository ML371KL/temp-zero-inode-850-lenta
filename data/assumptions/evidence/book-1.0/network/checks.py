# -*- coding: utf-8 -*-
"""Лист «Сеть и выручка», часть 4: проверки фрагмента `fragments/network.yaml` упрощённым ядром (revsim.py).

  1. Путь выручки 2026H2–2036H2 в клетке «H × base × mid × partial» и соседних клетках миров/режимов.
  2. Тест 2П/1П: выручка 2026H2 / проформа 2026H1 в коридоре истории (network_facts: группа 2016–2019, 2022 и
     гипермаркеты 2023–2025); сверка с 2025 на периметре якоря.
  3. Взвешенный по выручке LFL-сдвиг сегментов периметра LFL ≈ 0 (иначе LFL группы ≠ параметрам чека/трафика).
  4. Годовая выручка 2026–2028 против стратегии-2028 (2,2 трлн с M&A) и гайденса.
  5. Цена ошибки (грубо, до движка): одиночные подмены на концах диапазонов → Δвыручки 2028 и 2036H2 →
     ΔEV ≈ 0,4·среднее Δ 2027–2036 + 0,6·Δ 2036 (доля терминала ≈60 %) при постоянной марже; 1 % EV ≈ 26,5 ₽/акцию.
     Для путей площади выручка завышает стоимость (открытия стоят capex) — там же NPV-оценка по A-K5.

  python -B checks.py        → печать + checks_out.json
"""
from __future__ import annotations

import copy
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402
import revsim as RS  # noqa: E402
import space_paths as SPM  # noqa: E402

FRAG = os.path.normpath(os.path.join(L.HERE, '..', '..', '..', 'fragments', 'network.yaml'))
NF = json.load(open(os.path.join(L.HERE, 'network_facts_out.json'), encoding='utf-8'))
SP = json.load(open(os.path.join(L.HERE, 'space_paths_out.json'), encoding='utf-8'))
OUT = os.path.join(L.HERE, 'checks_out.json')
RUB_PER_EV_PCT = 26.5


def load():
    import yaml
    return yaml.safe_load(open(FRAG, encoding='utf-8'))


def annual(tot, y):
    return tot[f'{y}H1'] + tot[f'{y}H2'] if f'{y}H1' in tot else None


def ev_proxy(base_tot, alt_tot):
    yrs = range(2027, 2037)
    d_avg = st.mean(annual(alt_tot, y) / annual(base_tot, y) - 1 for y in yrs)
    d_term = alt_tot['2036H2'] / base_tot['2036H2'] - 1
    return 0.4 * d_avg + 0.6 * d_term


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    B = load()
    pf26h1 = NF['pro_forma_2026H1']['sum_segments']
    rep26h1 = NF['pro_forma_2026H1']['reported']
    rh = NF['history']['revenue_h']['total']
    out = {}

    # ---------------------------------------------------------- 1–2. базовая клетка и 2П/1П
    seg, tot, lfl = RS.simulate(B, 'H', 'base', 'mid', 'partial')
    tot_2026h1 = pf26h1
    ratio = tot['2026H2'] / tot_2026h1
    corr = NF['seasonality']['corridor']
    out['base_cell'] = dict(segments={s: {p: round(v, 3) for p, v in d.items()} for s, d in seg.items()},
                            total={p: round(v, 3) for p, v in tot.items()}, lfl_group=lfl)
    out['h2h1'] = dict(ratio_2026=ratio, corridor=corr, ok=corr[0] <= ratio <= corr[1],
                       ratio_2025_pro_forma=NF['seasonality']['pro_forma_2025']['ratio'])
    assert out['h2h1']['ok'], f'2П/1П 2026 = {ratio:.3f} вне коридора {corr}'
    # клетки: миры × режимы (площадь по миру, спрос по режиму — как joint 850oa)
    growth_by_world = {'N': 'high', 'H': 'mid', 'M': 'low'}
    demand_by_regime = {'stress': 'bear', 'floor': 'base', 'partial': 'base', 'full': 'bull'}
    cells = {}
    for w in ('N', 'H', 'M'):
        for rg in ('stress', 'floor', 'partial', 'full'):
            g = 'low' if rg == 'stress' else growth_by_world[w]
            _, t, _ = RS.simulate(B, w, demand_by_regime[rg], g, rg)
            cells[f'{w}/{rg}'] = dict(r2026H2=t['2026H2'], ratio=t['2026H2'] / tot_2026h1, y2028=annual(t, 2028),
                                      y2031=annual(t, 2031), y2036=annual(t, 2036))
    out['cells'] = cells
    ratios = [c['ratio'] for c in cells.values()]
    out['h2h1']['cells_range'] = [min(ratios), max(ratios)]

    # ---------------------------------------------------------- 3. взвешенный сдвиг LFL
    wavg = {}
    for p in RS.PERIODS:
        w_ = {s: seg[s][p] for s in ('hyper', 'super', 'conv', 'droge')}
        wavg[p] = sum(w_[s] * RS.path(B['revenue']['segments'][s]['lfl_offset'], p) for s in w_) / sum(w_.values())
    out['lfl_offset_weighted'] = wavg
    assert all(abs(v) < 0.002 for v in wavg.values()), 'взвешенный сдвиг LFL вне ±0,2 п.п.'

    # ---------------------------------------------------------- 4. годы против стратегии
    y2025 = rh['2025H1'] + rh['2025H2']
    out['annual'] = dict(y2026_reported_basis=rep26h1 + tot['2026H2'], y2026_growth=(rep26h1 + tot['2026H2']) / y2025 - 1,
                         y2026_pro_forma=pf26h1 + tot['2026H2'], y2027=annual(tot, 2027), y2028=annual(tot, 2028),
                         strategy_2028=2200.0, cagr_2025_2028=(annual(tot, 2028) / y2025) ** (1 / 3) - 1)

    # ---------------------------------------------------------- 5. цена ошибки
    sens = []

    def run(name, mutate, note=''):
        lo_b, hi_b = mutate('lo'), mutate('hi')
        _, tl, _ = RS.simulate(lo_b, 'H', 'base', 'mid', 'partial')
        _, th, _ = RS.simulate(hi_b, 'H', 'base', 'mid', 'partial')
        el, eh = ev_proxy(tot, tl), ev_proxy(tot, th)
        sens.append(dict(name=name, d2028_lo=annual(tl, 2028) / annual(tot, 2028) - 1,
                         d2028_hi=annual(th, 2028) / annual(tot, 2028) - 1,
                         d2036_lo=tl['2036H2'] / tot['2036H2'] - 1, d2036_hi=th['2036H2'] / tot['2036H2'] - 1,
                         ev_lo=el, ev_hi=eh, rub_lo=el * 100 * RUB_PER_EV_PCT, rub_hi=eh * 100 * RUB_PER_EV_PCT,
                         note=note))

    def setp(b, keys, val):
        b = copy.deepcopy(b)
        node = b
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = val
        return b

    R = B['revenue']
    run('A-R1 k (base) 0,45 / 0,65', lambda s: setp(B, ['revenue', 'ticket_k', 'base'], 0.45 if s == 'lo' else 0.65),
        'k действует и на долгий темп (уровень мира N)')
    def k_comp(kv):
        b = setp(B, ['revenue', 'ticket_k', 'base'], kv)
        sh = dict(b['revenue']['ticket_shift']['base'])
        sh['LT'] = sh['LT'] + (B['revenue']['ticket_k']['base'] - kv) * 0.043   # прод. ИПЦ мира N в долгую 4,3 %
        b['revenue']['ticket_shift']['base'] = sh
        return b
    run('A-R1 k 0,45 / 0,65 при неизменном реальном LFL LT', lambda s: k_comp(0.45 if s == 'lo' else 0.65),
        's LT пересчитан: s = r_LFL + π_N − k·прод.ИПЦ_N − трафик; так k влияет только на путь 2026–2030')
    run('A-R2 s LT (base) 1,0 / 2,0', lambda s: setp(B, ['revenue', 'ticket_shift', 'base', 'LT'], 0.010 if s == 'lo' else 0.020))
    run('A-R4 трафик LT (base) −0,6 / 0,0', lambda s: setp(B, ['revenue', 'traffic', 'base', 'LT'], -0.006 if s == 'lo' else 0.0))
    run('A-R3 НДС 2026H2 −0,8 / −0,2 п.п.', lambda s: setp(B, ['revenue', 'vat_adjustment', '2026H2'], -0.008 if s == 'lo' else -0.002))
    se_ok = B['facts']['segments']['okey']['revenue_se']['2025H2']
    b0 = B['facts']['segments']['okey']['revenue']['2025H2']
    run(f'A-R0 база «О’КЕЙ» 2025H2 ± se ({se_ok:.1f})',
        lambda s: setp(B, ['facts', 'segments', 'okey', 'revenue', '2025H2'], b0 - se_ok if s == 'lo' else b0 + se_ok))
    se_r = B['facts']['segments']['remi']['revenue_se']['2025H2']
    b1 = B['facts']['segments']['remi']['revenue']['2025H2']
    run(f'A-R0 база «Реми» 2025H2 ± se ({se_r:.1f})',
        lambda s: setp(B, ['facts', 'segments', 'remi', 'revenue', '2025H2'], b1 - se_r if s == 'lo' else b1 + se_r))
    def with_d(seg_, d_, mat=None):
        b = setp(B, ['revenue', 'segments', seg_, 'new_space_density'], d_)
        m_ = mat or b['revenue']['maturity_curve']
        c = SPM.chain(seg_, m_, d_, b['revenue']['segments'][seg_]['closed_productivity'],
                      pre_cohorts=SPM.pre_cohorts_for(seg_))
        av = dict(zip(c['halves'], c['averages']))
        b['facts']['segments'][seg_]['eff_area_avg_hist'] = {p: av[p] for p in ('2025H2', '2026H1')}
        return b
    run('A-R5 d «у дома» 0,65 / 0,90', lambda s: with_d('conv', 0.65 if s == 'lo' else 0.90),
        'история пересчитана тем же правилом (плотные когорты)')
    run('A-R5 d дрогери 0,55 / 0,85', lambda s: with_d('droge', 0.55 if s == 'lo' else 0.85))
    run('A-R5 d супер 0,75 / 1,05', lambda s: with_d('super', 0.75 if s == 'lo' else 1.05))
    run('A-R6 cp гипер 0,7 / 0,2', lambda s: setp(B, ['revenue', 'segments', 'hyper', 'closed_productivity'], 0.7 if s == 'lo' else 0.2))
    ok_off = R['segments']['okey']['lfl_offset']
    run('A-R10 «О’КЕЙ»: путь floor / full вместо partial',
        lambda s: setp(B, ['revenue', 'segments', 'okey', 'lfl_offset', 'partial'], ok_off['floor'] if s == 'lo' else ok_off['full']),
        'в сетке сближение уже привязано к режиму; ось — сдвиг цели 2029 при данном режиме')
    dp = R['segments']['diy']['density_path']
    run('A-R11 плотность DIY LT 80 / 160',
        lambda s: setp(B, ['revenue', 'segments', 'diy', 'density_path'], dict(dp, **({'2028': 90.0, 'LT': 80.0} if s == 'lo' else {'2028': 130.0, 'LT': 160.0}))),
        'выручка DIY — по марже группы; сегмент убыточен (−2,96 млрд до налога за 1П26)')
    ho = R['segments']['hyper']['lfl_offset']
    run('A-R9 сдвиг гипер LT −2,0 / 0,0', lambda s: setp(B, ['revenue', 'segments', 'hyper', 'lfl_offset'], dict(ho, LT=-0.02 if s == 'lo' else 0.0)))
    so = R['segments']['super']['lfl_offset']
    run('A-R9 сдвиг супер LT 0 / +1,5', lambda s: setp(B, ['revenue', 'segments', 'super', 'lfl_offset'], dict(so, LT=0.0 if s == 'lo' else 0.015)))
    sp_conv = R['segments']['conv']['space']
    run('A-R7 площадь «у дома»: low / high вместо mid',
        lambda s: setp(B, ['revenue', 'segments', 'conv', 'space', 'mid'], sp_conv['low'] if s == 'lo' else sp_conv['high']),
        'выручка без capex — завышает; стоимость — через NPV открытия (A-K5)')
    sp_d = R['segments']['droge']['space']
    run('A-R7 площадь дрогери: low / high', lambda s: setp(B, ['revenue', 'segments', 'droge', 'space', 'mid'], sp_d['low'] if s == 'lo' else sp_d['high']),
        'при марже группы и capex группы IRR «Улыбки» ≈7 % — рост почти не создаёт стоимости')
    sp_ok = R['segments']['okey']['space']
    run('A-R7 закрытия «О’КЕЙ»: low (7 %/год) / high (2 %/год)',
        lambda s: setp(B, ['revenue', 'segments', 'okey', 'space', 'mid'], sp_ok['low'] if s == 'lo' else sp_ok['high']))
    def with_mat(mat):
        b = setp(B, ['revenue', 'maturity_curve'], mat)
        for sg in ('hyper', 'super', 'conv', 'droge'):
            S_ = b['revenue']['segments'][sg]
            c = SPM.chain(sg, mat, S_['new_space_density'], S_['closed_productivity'], pre_cohorts=SPM.pre_cohorts_for(sg))
            av = dict(zip(c['halves'], c['averages']))
            b['facts']['segments'][sg]['eff_area_avg_hist'] = {p: av[p] for p in ('2025H2', '2026H1')}
        return b
    run('A-R5 кривая созревания [0,60; 0,80; 1] / [0,80; 0,90; 1]',
        lambda s: with_mat([0.60, 0.80, 1.0] if s == 'lo' else [0.80, 0.90, 1.0]), 'история пересчитана тем же правилом')
    out['sensitivities'] = sens

    # NPV открытия «Монетки» на м² (A-K5): денежный поток при марже группы, реальная ставка 9 %, 10 лет
    pb = SP['payback']
    mon = pb['formats']['Монетка']['маржа группы']
    ramp = [0.36, 0.77, 0.91] + [1.0] * 17
    npv = sum(mon['density'] * mon['nsd'] * ramp[h] / 2 * mon['margin'] / 1.09 ** ((h + 1) / 2) for h in range(20)) - pb['capex_per_m2']
    out['npv_monetka_per_m2'] = npv
    # стоимость путей площади «у дома» через NPV открытия: Δ валовой площади 2026H2–2036 (сценарий − mid) × NPV/м²,
    # приведено к 2026 по 9 % реальных (середина открытия), на акцию × (1 − g 0,10)
    gv = {}
    for sc in ('low', 'mid', 'high'):
        rows = SP['plan_tables']['conv'][sc]['rows']
        tot_pv = 0.0
        for k_, v in rows.items():
            t = 0.25 if k_ == '2026H2' else (int(k_) - 2026)
            tot_pv += v['gross_area'] / 1.09 ** t
        gv[sc] = tot_pv
    out['conv_space_npv'] = {sc: dict(pv_gross_area_th=gv[sc],
                                      rub_per_share=(gv[sc] - gv['mid']) * npv / 115.074675 * 0.9)
                             for sc in gv}
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print('== Базовая клетка H × base × mid × partial, млрд руб.')
    for s, d in seg.items():
        print(f'  {s:9s}', ' '.join(f'{p}:{d[p]:8.1f}' for p in ('2026H2', '2027H1', '2027H2', '2028H2', '2031H2', '2036H2')))
    print('  итого    ', ' '.join(f'{p}:{tot[p]:8.1f}' for p in ('2026H2', '2027H1', '2027H2', '2028H2', '2031H2', '2036H2')))
    h = out['h2h1']
    print(f'2П/1П 2026 = {h["ratio_2026"]:.4f} (коридор {corr[0]:.3f}–{corr[1]:.3f}; 2025 на периметре якоря '
          f'{h["ratio_2025_pro_forma"]:.4f}); по клеткам {h["cells_range"][0]:.4f}–{h["cells_range"][1]:.4f}')
    a = out['annual']
    print(f'2026 (отчётная база) {a["y2026_reported_basis"]:.1f} ({a["y2026_growth"]:+.1%}); проформа {a["y2026_pro_forma"]:.1f}; '
          f'2027 {a["y2027"]:.1f}; 2028 {a["y2028"]:.1f} против 2 200 (стратегия с M&A); CAGR 2025–28 {a["cagr_2025_2028"]:.1%}')
    print('взвешенный сдвиг LFL, п.п.:', {p: round(v * 100, 2) for p, v in list(wavg.items())[:6]})
    print('клетки (2026H2 / 2028 / 2036, млрд):')
    for k, c in cells.items():
        print(f'  {k:10s} {c["r2026H2"]:7.1f} {c["y2028"]:8.1f} {c["y2036"]:8.1f}  2П/1П {c["ratio"]:.3f}')
    print('\n== Цена ошибки (одиночные подмены; ΔEV ≈ 0,4·Δср.2027–36 + 0,6·Δ2036; 1 % EV ≈ 26,5 ₽)')
    for s in sens:
        print(f'  {s["name"]:52s} 2028 {s["d2028_lo"]:+.2%}/{s["d2028_hi"]:+.2%}  2036 {s["d2036_lo"]:+.2%}/{s["d2036_hi"]:+.2%}  '
              f'≈ {s["rub_lo"]:+.0f}/{s["rub_hi"]:+.0f} ₽')
    print(f'NPV открытия «Монетки» (маржа группы, 9 % реальных, 10 лет): {npv:.1f} тыс. руб./м² при capex {pb["capex_per_m2"]:.1f}')
    print('Пути площади «у дома» по NPV открытия, ₽/акцию к mid:', {k: round(v['rub_per_share']) for k, v in out['conv_space_npv'].items()})


if __name__ == '__main__':
    main()
