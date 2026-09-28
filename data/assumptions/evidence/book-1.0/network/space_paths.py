# -*- coding: utf-8 -*-
"""Лист «Сеть и выручка», часть 3: плотность новой площади, история эффективной площади, сценарии площади,
пути LFL-сдвигов, проверка ближайшего полугодия и окупаемость открытий (A-K5).

Читает network_facts_out.json и lfl_ticket_out.json (их пишут части 1–2; запускать после них).

  1. Когорты и закрытия по полугодиям 2024H1–2026H1 для hyper/super/conv/droge: валовые открытия = органический
     чистый прирост площади + закрытия (закрытия магазинов — из пар «валовые − чистые» релизов, иначе — медиана
     известных кварталов) × средняя площадь закрываемого магазина.
  2. Калибровка `new_space_density` (d) правилом ядра 850oa: прогноз выручки пары полугодий г/г =
     A_eff(p)/A_eff(p−2) × (1 + LFL сегмента) (+ «Молния» отдельно) против факта; сетка d; три кривые созревания.
  3. `eff_area_avg_hist` {2025H2, 2026H1} и `new_area_gross_hist` (2024H2…2026H1) — правилом ядра (непрерывный
     индекс, сведённый к уровню ядра на якоре; вариант «в» листа Магнита book-1.5/facts/eff_area_hist.py).
  4. Сценарии площади low/mid/high: магазины в год × площадь нового магазина → доли площади сегмента в год.
  5. Пути `lfl_offset`, включая сближение плотности «О'КЕЙ» по режимам.
  6. A-K5: IRR и окупаемость открытия по форматам при марже группы.

  python -B space_paths.py        → печать + space_paths_out.json
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402

NF = json.load(open(os.path.join(L.HERE, 'network_facts_out.json'), encoding='utf-8'))
LT = json.load(open(os.path.join(L.HERE, 'lfl_ticket_out.json'), encoding='utf-8'))
OUT = os.path.join(L.HERE, 'space_paths_out.json')

H = NF['history']
HALVES = ['2023H2', '2024H1', '2024H2', '2025H1', '2025H2', '2026H1']
MATURITY = [0.72, 0.82, 1.0]                      # общая кривая (обоснование — README §4)
MAT_ALT = {'быстрая [0,80; 0,90; 1]': [0.80, 0.90, 1.0], 'книга [0,72; 0,82; 1]': MATURITY,
           'медленная [0,60; 0,80; 1]': [0.60, 0.80, 1.0]}
CLOSED_PROD = 0.6
# продуктивность закрываемой площади по сегментам (книга): гипер — смесь снятия площади (история: cp ≈ 0 —
# калибровка) и закрытия магазинов (0,6) → 0,5 с диапазоном 0,2–0,7; прочие — 0,6 (как A-R6 Магнита)
CP_SEG = {'hyper': 0.5, 'okey': 0.6, 'super': 0.6, 'conv': 0.6, 'droge': 0.6, 'remi': 0.6, 'diy': 0.6}
# средняя площадь закрываемого магазина, тыс. м² (средняя площадь формата: датабук 30.06.2026 — 1 124,1/4 396 = 0,256
# у дома; 297,5/395 = 0,753 супер; 270,5/2 041 = 0,133 дрогери)
CLOSE_AREA = {'conv': 0.256, 'super': 0.753, 'droge': 0.133}
# новая площадь гипермаркета органики: XS 2,5 тыс. м², «Эконом»/«Семья» 2,5–4,0 (стратегия, с. 10, 27) → 2,8
HYPER_NEW = 2.8


def q_end(p):
    return L.eop_quarter(p)


def area(seg, p):
    q = q_end(p)
    if seg == 'conv':
        return H['area_q']['conv'][q] + H['area_q']['other'][q]
    if seg == 'hyper' and p == '2026H1':
        return H['area_q']['hyper'][q] - NF['deals']['okey']['area_th_databook']
    return H['area_q'][seg][q]


def closures_half(seg, p):
    """Закрытые магазины за полугодие: раскрытые пары «валовые − чистые»; прочие кварталы — медиана известных."""
    cq = NF['closures_q']
    known = {q: v[seg] for q, v in cq.items() if seg in v}
    if seg == 'conv':
        known_o = {q: v.get('other', 0) for q, v in cq.items() if 'other' in v}
    default = st.median([v for q, v in known.items() if q >= '2025Q1']) if known else 0
    tot = 0.0
    for q in L.quarters_of_half(p):
        if q in known:
            tot += known[q]
        else:
            tot += default
    if seg == 'droge' and p == '2026H1':
        # 2 кв. 2026: чистые −32 (апр. 0, май −10, июнь −22); валовые не раскрыты — суждение 40 открытий
        # (1 кв. — 101), закрытия = 40 + 32 = 72
        tot = known.get('2026Q1', default) + 72
    return tot


def history_inputs(seg):
    """Концы полугодий, валовые открытия (тыс. м²) и приобретения (зрелая площадь) по сегменту."""
    org = H['organic_h']
    acq = {}
    if seg in ('hyper', 'super', 'conv'):
        acq['2025H1'] = NF['deals']['molnia']['area_th'][seg]
    halves = HALVES if seg != 'droge' else ['2024H2', '2025H1', '2025H2', '2026H1']
    ends = [area(seg, p) for p in halves]
    gross, closed_area = [], []
    for p in halves[1:]:
        if seg == 'hyper':
            # органика гипер: валовые = новые магазины × 2,8 тыс. м²; всё прочее изменение площади — закрытия,
            # сокращение и передача площади (субаренда, «Дом Лента» внутри гипермаркета)
            n_new = {'2024H1': 1, '2024H2': 2, '2025H1': 0, '2025H2': 3, '2026H1': 2}[p]
            g = n_new * HYPER_NEW
            net = org['hyper'][p]['net_area_th']
            closed_area.append(g - net)
            gross.append(g)
            continue
        net = org[seg][p]['net_area_th'] + (org['other'][p]['net_area_th'] if seg == 'conv' else 0.0)
        if seg == 'super':
            # супер: площадь сети меняется и без смены числа магазинов (реконцепция, сокращение) — валовые из числа
            # магазинов (чистые органические + закрытия) × 0,65 тыс. м², снятие площади — остаток
            g = max(0.0, (org['super'][p]['net_stores'] + closures_half('super', p)) * 0.65)
            gross.append(g)
            closed_area.append(g - net)
            continue
        c = closures_half(seg, p) * CLOSE_AREA[seg]
        gross.append(net + c)
        closed_area.append(c)
    return dict(halves=halves, ends=ends, gross=gross, closed=closed_area, acq=acq)


def chain(seg, maturity, d, cp, n_dense_hist=None, pre_cohorts=None, include_acq=True):
    """Индекс эффективной площади правилом ядра по истории сегмента; приобретения входят зрелыми в начале
    следующего полугодия (Молния 24.06.2025 — в 2П2025; в среднем 1П2025 её нет). include_acq=False — площадь
    без приобретённой (для калибровки на выручке без «Молнии»)."""
    hi = history_inputs(seg)
    if not include_acq and hi['acq']:
        ends = list(hi['ends'])
        for p, a in hi['acq'].items():
            j = hi['halves'].index(p)
            for jj in range(j, len(ends)):
                ends[jj] -= a
        hi = dict(hi, ends=ends, acq={})
    k = len(maturity) - 1
    cohorts = list(pre_cohorts if pre_cohorts is not None else [hi['gross'][0]] * k)
    n_dense = k if n_dense_hist is None else n_dense_hist
    levels_start = hi['ends'][0] - L.immature(cohorts, maturity, d, n_dense)
    idx = [levels_start]
    avgs, steps = [], []
    area0 = hi['ends'][0]
    for i, p in enumerate(hi['halves'][1:]):
        acq_prev = hi['acq'].get(hi['halves'][i], 0.0) if i > 0 else 0.0
        a_end = hi['ends'][i + 1] - hi['acq'].get(p, 0.0)
        s = L.effective_area_step(cohorts, area0 + acq_prev if i > 0 else area0, idx[-1] + acq_prev, hi['gross'][i],
                                  a_end, maturity, cp, d, n_dense)
        steps.append(s)
        avgs.append(s['average'])
        cohorts, n_dense = s['cohorts'], s['n_dense']
        idx.append(s['end'])
        area0 = a_end
    # непрерывный индекс сводится к уровню ядра на последнем якоре («физическая − незрелая», reset): приросты шагов
    # (end − start) от уровня не зависят, поэтому сдвиг — одна константа (вариант «в» листа Магнита)
    shift = steps[-1]['reset'] - steps[-1]['end']
    avgs = [a + shift for a in avgs]
    return dict(halves=hi['halves'][1:], averages=avgs, steps=steps, inputs=hi, end_level=idx[-1] + shift)


def molnia_rev(seg, p):
    """Выручка «Молнии» в сегменте: вклад 2025 (9,644 с 24.06) и проформа года (19,796) по МСФО 2025 прим. 8;
    доля сегмента — по площади сделки; 1П2026 = проформа года × доля 1П розницы «Ленты» 2025 × (1 + LFL группы 1П26)."""
    d = NF['deals']['molnia']
    if seg not in d['area_th']:
        return 0.0
    share = d['area_th'][seg] / sum(d['area_th'].values())
    if p == '2025H2':
        return d['revenue_contrib_2025'] * share
    if p == '2026H1':
        rh = H['revenue_h']['retail']
        s1 = rh['2025H1'] / (rh['2025H1'] + rh['2025H2'])
        return d['revenue_full_2025'] * s1 * (1 + H['lfl_h']['group']['sales']['2026H1']) * share
    return 0.0


def seg_revenue(seg, p):
    rh = H['revenue_h']
    if seg == 'conv':
        return rh['conv'][p] + rh['other'][p]
    if seg == 'hyper' and p == '2026H1':
        return rh['hyper'][p] - NF['deals']['okey']['revenue_contrib_2026']
    return rh[seg][p]


def pre_cohorts_for(seg):
    """Когорты до начала цепочки. Дрогери: «Улыбка» 30.09.2024 — 1 535 магазинов и 204 тыс. м² (релиз 02.12.2024),
    при покупке 221,3 тыс. м² (датабук, дек. 2024) → когорта 2П2024 ≈ 17,3 тыс. м²; 1П2024 — та же (суждение).
    Прочие — открытия первого полугодия цепочки (по умолчанию)."""
    if seg == 'droge':
        import re
        t = L.text_file('lentagroup_news/2024-12-02_lenta-priobretaet-vtoruyu-v-rossii-krupnejshuyu-set-drogeri-ulybka-rad.txt')
        m = re.search(r'30 сентября 2024 года, под управлением компании находилось ([\d ]+) магазина с общей торговой '
                      r'площадью (\d+) тыс', t)
        c = NF['deals']['ulybka']['area_th']['droge'] - float(m.group(2))
        return [c, c]
    return None


def calibrate(seg, maturity, cp, grid=None):
    """Подбор d по парам г/г (без «Молнии» в выручке и площади): минимум суммы квадратов лог-ошибок."""
    grid = grid or [x / 100 for x in range(30, 161, 1)]
    lfl = H['lfl_h'][seg]['sales']
    best = None
    table = []
    for d in grid:
        c = chain(seg, maturity, d, cp, include_acq=False, pre_cohorts=pre_cohorts_for(seg))
        av = dict(zip(c['halves'], c['averages']))
        errs = []
        for p, pp in (('2025H1', '2024H1'), ('2025H2', '2024H2'), ('2026H1', '2025H1')):
            if p not in av or pp not in av:
                continue
            actual = (seg_revenue(seg, p) - molnia_rev(seg, p)) / (seg_revenue(seg, pp) - molnia_rev(seg, pp))
            pred = av[p] / av[pp] * (1 + lfl[p])
            errs.append((p, actual, pred, math.log(pred / actual)))
        sse = sum(e[3] ** 2 for e in errs)
        table.append((d, sse, errs))
        if best is None or sse < best[1]:
            best = (d, sse, errs)
    return best, table


def calibrate_cp_hyper(maturity):
    """Гипермаркеты: d = 0,85 (органика мала), подбор продуктивности закрываемой/снимаемой площади cp."""
    lfl = H['lfl_h']['hyper']['sales']
    best = None
    for cp in [x / 100 for x in range(0, 151, 5)]:
        c = chain('hyper', maturity, 0.85, cp, include_acq=False)
        av = dict(zip(c['halves'], c['averages']))
        errs = []
        for p, pp in (('2025H1', '2024H1'), ('2025H2', '2024H2'), ('2026H1', '2025H1')):
            actual = (seg_revenue('hyper', p) - molnia_rev('hyper', p)) / (seg_revenue('hyper', pp) - molnia_rev('hyper', pp))
            pred = av[p] / av[pp] * (1 + lfl[p])
            errs.append((p, actual, pred, math.log(pred / actual)))
        sse = sum(e[3] ** 2 for e in errs)
        if best is None or sse < best[1]:
            best = (cp, sse, errs)
    return best


# ---------------------------------------------------------------- сценарии площади
# магазины в год (2026H2 — за полугодие), площадь нового магазина (тыс. м²), закрытия (% площади в год)
NEW_STORE = {'conv_monetka': 0.23, 'conv_ving': 0.095, 'super': 0.65, 'droge': 0.125, 'hyper': HYPER_NEW,
             'diy': 1.1}
# новая площадь «Монетки»: 1 кв. 2026 55,3 тыс. м² на 245 чистых (0,226), 2 кв. 52,7 на 223 (0,236); «Вингараж»
# 80–115 м² (стратегия, с. 27); супер 1 кв. 6,3/10, 2 кв. 7,9/12 (0,63–0,66); дрогери 1 кв. 10,7/88 (0,122);
# гипер XS/«Эконом»; «Дом Лента» внутри гипермаркета 1,1 тыс. м² (релиз 29.04.2026)
YEARS = list(range(2027, 2037))
PLANS = {
    'conv': {  # «Монетка» (стратегия-2028: 1 000+ в год; 2026 — «более тысячи в год в 2026–2028») + «Вингараж»
        'mid': dict(monetka={'2026H2': 550, 2027: 1000, 2028: 1000, 2029: 800, 2030: 700, 2031: 600, 2032: 550,
                             2033: 500, 2034: 450, 2035: 400, 2036: 350},
                    ving={'2026H2': 40, 2027: 170, 2028: 300, 2029: 200, 2030: 150, 2031: 100, 2032: 100, 2033: 80,
                          2034: 60, 2035: 50, 2036: 50},
                    close={'2026H2': 0.010, 2027: 0.010, 2028: 0.010, 2029: 0.012, 2030: 0.013, 2031: 0.015}),
        'high': dict(monetka={'2026H2': 580, 2027: 1200, 2028: 1200, 2029: 1200, 2030: 1000, 2031: 800, 2032: 700,
                              2033: 650, 2034: 600, 2035: 550, 2036: 500},
                     ving={'2026H2': 45, 2027: 175, 2028: 350, 2029: 300, 2030: 200, 2031: 150, 2032: 120, 2033: 100,
                           2034: 80, 2035: 70, 2036: 60},
                     close={'2026H2': 0.010, 2027: 0.010, 2028: 0.010, 2029: 0.011, 2030: 0.012, 2031: 0.013}),
        'low': dict(monetka={'2026H2': 450, 2027: 700, 2028: 600, 2029: 500, 2030: 450, 2031: 400, 2032: 350,
                             2033: 330, 2034: 300, 2035: 280, 2036: 260},
                    ving={'2026H2': 30, 2027: 80, 2028: 80, 2029: 60, 2030: 50, 2031: 40, 2032: 40, 2033: 30,
                          2034: 30, 2035: 30, 2036: 30},
                    close={'2026H2': 0.012, 2027: 0.015, 2028: 0.015, 2029: 0.017, 2030: 0.018, 2031: 0.020}),
    },
    'droge': {  # «Улыбка радуги»: 350+ открытий в год (стратегия, с. 24; ГО-2025)
        'mid': dict(stores={'2026H2': 180, 2027: 350, 2028: 350, 2029: 300, 2030: 250, 2031: 200, 2032: 180, 2033: 160,
                            2034: 150, 2035: 140, 2036: 130},
                    close={'2026H2': 0.060, 2027: 0.040, 2028: 0.035, 2029: 0.030}),
        'high': dict(stores={'2026H2': 200, 2027: 450, 2028: 450, 2029: 450, 2030: 350, 2031: 300, 2032: 250, 2033: 220,
                             2034: 200, 2035: 180, 2036: 170},
                     close={'2026H2': 0.050, 2027: 0.030, 2028: 0.030, 2029: 0.030}),
        'low': dict(stores={'2026H2': 100, 2027: 200, 2028: 200, 2029: 180, 2030: 160, 2031: 150, 2032: 140, 2033: 130,
                            2034: 120, 2035: 110, 2036: 100},
                    close={'2026H2': 0.070, 2027: 0.050, 2028: 0.045, 2029: 0.040}),
    },
    'super': {  # «Супер Лента»: 50+ магазинов в год (стратегия, с. 32)
        'mid': dict(stores={'2026H2': 30, 2027: 50, 2028: 50, 2029: 45, 2030: 40, 2031: 35, 2032: 32, 2033: 30,
                            2034: 28, 2035: 26, 2036: 25},
                    close={'2026H2': 0.015, 2027: 0.015, 2028: 0.015}),
        'high': dict(stores={'2026H2': 35, 2027: 70, 2028: 70, 2029: 65, 2030: 55, 2031: 45, 2032: 40, 2033: 38,
                             2034: 36, 2035: 34, 2036: 32},
                     close={'2026H2': 0.012, 2027: 0.012, 2028: 0.012}),
        'low': dict(stores={'2026H2': 20, 2027: 25, 2028: 25, 2029: 22, 2030: 20, 2031: 20, 2032: 18, 2033: 18,
                            2034: 16, 2035: 16, 2036: 15},
                    close={'2026H2': 0.020, 2027: 0.020, 2028: 0.020}),
    },
    'hyper': {  # гипер «Ленты»: органика XS/«Эконом»/«Семья» 2–6 в год, закрытия и снятие площади
        'mid': dict(stores={'2026H2': 2, 2027: 4, 2028: 4, 2029: 4, 2030: 5, 2031: 5, 2032: 5, 2033: 5, 2034: 5,
                            2035: 5, 2036: 5},
                    close={'2026H2': 0.015, 2027: 0.015}),
        'high': dict(stores={'2026H2': 3, 2027: 6, 2028: 6, 2029: 6, 2030: 6, 2031: 6, 2032: 6, 2033: 6, 2034: 6,
                             2035: 6, 2036: 6},
                     close={'2026H2': 0.012, 2027: 0.012}),
        'low': dict(stores={'2026H2': 1, 2027: 2, 2028: 2, 2029: 2, 2030: 3, 2031: 3, 2032: 3, 2033: 3, 2034: 3,
                            2035: 3, 2036: 3},
                    close={'2026H2': 0.020, 2027: 0.020}),
    },
}
# сегменты с путями долей напрямую (нет плана в магазинах): доли площади в год
DIRECT = {
    'okey': {  # открытий нет до конца интеграции; закрытия слабых магазинов (суждение: 75 → −7…−16 магазинов к 2029)
        'mid': dict(gross_open={'2026H2': 0.0, '2028': 0.0, '2031': 0.010, 'LT': 0.010},
                    close={'2026H2': 0.040, '2028': 0.040, '2029': 0.015, 'LT': 0.015}),
        'high': dict(gross_open={'2026H2': 0.0, '2028': 0.0, '2031': 0.010, 'LT': 0.010},
                     close={'2026H2': 0.020, '2028': 0.020, '2029': 0.015, 'LT': 0.015}),
        'low': dict(gross_open={'2026H2': 0.0, '2028': 0.0, '2031': 0.010, 'LT': 0.010},
                    close={'2026H2': 0.070, '2028': 0.070, '2029': 0.015, 'LT': 0.015}),
    },
    'remi': {  # 118 магазинов без изменений в 1П2026; рост на Дальнем Востоке — суждение
        'mid': dict(gross_open={'2026H2': 0.015, '2027': 0.040, '2030': 0.040, 'LT': 0.030},
                    close={'2026H2': 0.010, '2027': 0.015, 'LT': 0.015}),
        'high': dict(gross_open={'2026H2': 0.020, '2027': 0.060, '2030': 0.060, 'LT': 0.035},
                     close={'2026H2': 0.010, '2027': 0.015, 'LT': 0.015}),
        'low': dict(gross_open={'2026H2': 0.005, '2027': 0.010, 'LT': 0.020},
                    close={'2026H2': 0.015, '2027': 0.020, 'LT': 0.020}),
    },
    'diy': {  # «Дом Лента»: форматы 1,1 тыс. м² внутри гипермаркетов (пилот 28.04.2026) и закрытия слабых DIY
        'mid': dict(gross_open={'2026H2': 0.016, '2028': 0.016, 'LT': 0.015},
                    close={'2026H2': 0.020, '2028': 0.020, 'LT': 0.015}),
        'high': dict(gross_open={'2026H2': 0.016, '2027': 0.030, '2030': 0.030, 'LT': 0.015},
                     close={'2026H2': 0.010, '2028': 0.010, 'LT': 0.015}),
        'low': dict(gross_open={'2026H2': 0.0, '2028': 0.0, 'LT': 0.010},
                    close={'2026H2': 0.040, '2028': 0.040, 'LT': 0.015}),
    },
}
LT_RATES = {  # стационар сети после 2036 (открытия/закрытия, % площади в год) — суждение, как A-R7 Магнита
    'conv': {'low': (0.025, 0.020), 'mid': (0.030, 0.015), 'high': (0.035, 0.013)},
    'droge': {'low': (0.035, 0.035), 'mid': (0.045, 0.030), 'high': (0.050, 0.030)},
    'super': {'low': (0.025, 0.020), 'mid': (0.030, 0.015), 'high': (0.035, 0.012)},
    'hyper': {'low': (0.006, 0.020), 'mid': (0.010, 0.015), 'high': (0.012, 0.012)},
}


def plan_rates(seg, scen, area0):
    """План в магазинах → годовые доли площади (gross_open, close) по периодам 2026H2, 2027…2036 и LT."""
    P = PLANS[seg][scen]
    a = area0
    rows = {}
    def close_at(key):
        c = P['close']
        if key in c:
            return c[key]
        ks = sorted(k for k in c if isinstance(k, int))
        last = [k for k in ks if isinstance(key, int) and k <= key]
        return c[last[-1]] if last else c['2026H2']
    for key in ['2026H2'] + YEARS:
        if seg == 'conv':
            g_area = P['monetka'][key] * NEW_STORE['conv_monetka'] + P['ving'][key] * NEW_STORE['conv_ving']
        else:
            g_area = P['stores'][key] * NEW_STORE[seg]
        c = close_at(key)
        if key == '2026H2':
            go = 2 * g_area / a                    # доля в год: за полугодие ×2 к площади начала
            a_next = a + g_area - a * c / 2
        else:
            mid_a = a * (1 + (g_area / a - c) / 2)  # средняя площадь года ≈ середина
            go = g_area / mid_a
            a_next = a + g_area - mid_a * c
        rows[key] = dict(gross_open=go, close=c, area_start=a, gross_area=g_area)
        a = a_next
    return rows, a


def to_path(rows, field, lt):
    """Годовые доли → путь книги (точный ключ 2026H2, ключи лет с линейной интерполяцией ядра, LT)."""
    keys = ['2026H2', 2027, 2028, 2029, 2030, 2031, 2033, 2036]
    out = {}
    for k in keys:
        out[str(k)] = round(rows[k][field], 4)
    out['LT'] = lt
    return out


def interp_path(path, p):
    """Значение пути книги в периоде p (как model/book.path_value 850oa: точный ключ → год → интерполяция → LT)."""
    if p in path:
        return path[p]
    y = int(p[:4])
    if str(y) in path:
        return path[str(y)]
    pts = sorted((int(k), v) for k, v in path.items() if k.isdigit())
    if not pts:
        return path['LT']
    if y <= pts[0][0]:
        return pts[0][1]
    if y > pts[-1][0]:
        return path.get('LT', pts[-1][1])
    for (y0, v0), (y1, v1) in zip(pts, pts[1:]):
        if y0 <= y <= y1:
            return v0 + (v1 - v0) * (y - y0) / (y1 - y0)
    return path['LT']


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    res = {}

    # ---------------------------------------------------------- 1–2. калибровка d
    calib = {}
    for seg in ('conv', 'super', 'droge'):
        calib[seg] = {}
        for name, m in MAT_ALT.items():
            best, table = calibrate(seg, m, CLOSED_PROD)
            calib[seg][name] = dict(d=best[0], sse=best[1], pairs=[dict(p=e[0], actual=e[1], pred=e[2], log_err=e[3])
                                                                    for e in best[2]])
        # чувствительность к cp при книжной кривой
        calib[seg]['cp_0.4'] = calibrate(seg, MATURITY, 0.4)[0][0]
        calib[seg]['cp_0.8'] = calibrate(seg, MATURITY, 0.8)[0][0]
    cp_h = calibrate_cp_hyper(MATURITY)
    calib['hyper_cp'] = dict(cp=cp_h[0], sse=cp_h[1], pairs=[dict(p=e[0], actual=e[1], pred=e[2], log_err=e[3])
                                                            for e in cp_h[2]])
    res['calibration'] = calib

    # выбранные плотности новой площади (книга) — см. README: калибровка + суждение
    res['nsd_calibrated'] = {seg: calib[seg]['книга [0,72; 0,82; 1]']['d'] for seg in ('conv', 'super', 'droge')}
    # книга: калибровка, округлённая к суждению (README §4): conv 0,76 (калибровка 0,75–0,78), super 0,90 (0,89–1,10,
    # шум LFL-периметра перезапуска), droge 0,70 (одна пара, 0,58–0,70), hyper 0,70 («Эконом»: 5 магазинов, 3+ млрд в
    # 2025 — стратегия, с. 27), okey/remi 0,85 (как 850oa), diy 0,80 (формат 1,1 тыс. м² внутри гипермаркета)
    NSD = {'conv': 0.76, 'super': 0.90, 'droge': 0.70, 'hyper': 0.70, 'okey': 0.85, 'remi': 0.85, 'diy': 0.80}
    res['nsd_book'] = dict(NSD)

    # ---------------------------------------------------------- 3. история эффективной площади
    eff = {}
    for seg in ('hyper', 'super', 'conv', 'droge'):
        # книга: исторические когорты — плотные (дозревают до d, как в калибровке): facts.segments.<seg>.
        # new_area_dense_cohorts = 2 (две младшие когорты; старшие в списке зрелые) — уровень ядра на якоре тот же,
        # что reset цепочки; вариант «до 1,0» — для сравнения
        c = chain(seg, MATURITY, NSD[seg], CP_SEG[seg], pre_cohorts=pre_cohorts_for(seg))
        av = dict(zip(c['halves'], c['averages']))
        c_d = chain(seg, MATURITY, 1.0, CP_SEG[seg], n_dense_hist=0, pre_cohorts=pre_cohorts_for(seg))
        av_d = dict(zip(c_d['halves'], c_d['averages']))
        hi = c['inputs']
        cohorts = dict(zip(hi['halves'][1:], hi['gross']))
        if seg == 'droge':
            cohorts['2024H2'] = pre_cohorts_for('droge')[-1]
        eff[seg] = dict(eff_area_avg_hist={p: round(av[p], 2) for p in ('2025H2', '2026H1')},
                        eff_area_avg_hist_to_1={p: round(av_d[p], 2) for p in ('2025H2', '2026H1')},
                        new_area_dense_cohorts=2,
                        new_area_gross_hist=[round(cohorts.get(p, 0.0), 2) for p in ('2024H2', '2025H1', '2025H2',
                                                                                       '2026H1')],
                        closed_hist={p: round(v, 2) for p, v in zip(hi['halves'][1:], hi['closed'])},
                        area_ends=dict(zip(hi['halves'], [round(x, 3) for x in hi['ends']])),
                        level_at_anchor=round(c['steps'][-1]['reset'], 2))
    # сегменты без органики в истории: площадь постоянна (О'КЕЙ — периметр сделки; Реми — 2 открытия в дек. 2025)
    ok_a = NF['deals']['okey']['area_th_databook']
    eff['okey'] = dict(eff_area_avg_hist={'2025H2': round(ok_a, 2), '2026H1': round(ok_a, 2)},
                       new_area_gross_hist=[0.0, 0.0, 0.0, 0.0], note='площадь сети до сделки не раскрыта — постоянна')
    remi_acq = NF['deals']['remi']['area_th']['remi']
    remi_open = H['area_q']['remi']['2025Q4'] - remi_acq
    r_start = remi_acq
    r_step = L.effective_area_step([0.0, 0.0], remi_acq, r_start, remi_open, H['area_q']['remi']['2025Q4'], MATURITY,
                                   CLOSED_PROD, 1.0, 0)
    r_step2 = L.effective_area_step(r_step['cohorts'], H['area_q']['remi']['2025Q4'], r_step['end'], 0.0,
                                    H['area_q']['remi']['2026Q2'], MATURITY, CLOSED_PROD, 1.0, 0)
    shift_r = r_step2['reset'] - r_step2['end']
    eff['remi'] = dict(eff_area_avg_hist={'2025H2': round(r_step['average'] + shift_r, 2),
                                          '2026H1': round(r_step2['average'] + shift_r, 2)},
                       new_area_gross_hist=[0.0, 0.0, round(remi_open, 3), 0.0],
                       note='площадь до сделки = площадь при покупке (105,45 тыс. м²); 2 открытия в декабре 2025')
    # DIY: средняя физическая площадь 1П2026 трапецией по месячным концам (поэтапная покупка янв.–март)
    mon = [0.0, 46.150, 233.097, 269.580, 270.575, 270.575, 270.575]
    diy_avg = (mon[0] / 2 + sum(mon[1:6]) + mon[6] / 2) / 6
    eff['diy'] = dict(eff_area_avg_hist={'2025H2': None, '2026H1': round(diy_avg, 2)},
                      note='месячный лист датабука, строка Dom Lenta (площадь на концы месяцев 12.2025–06.2026)')
    res['eff_area'] = eff

    # ---------------------------------------------------------- 4. сценарии площади
    area_end = {s: v['area_end'] for s, v in NF['segments'].items()}
    space = {}
    plan_tables = {}
    for seg in ('conv', 'droge', 'super', 'hyper'):
        space[seg] = {}
        plan_tables[seg] = {}
        for scen in ('low', 'mid', 'high'):
            rows, a_2036 = plan_rates(seg, scen, area_end[seg])
            g_lt, c_lt = LT_RATES[seg][scen]
            space[seg][scen] = dict(gross_open=to_path(rows, 'gross_open', g_lt), close=to_path(rows, 'close', c_lt))
            plan_tables[seg][scen] = dict(area_2036=a_2036, rows={str(k): v for k, v in rows.items()})
    for seg, d in DIRECT.items():
        space[seg] = d
    res['space'] = space
    res['plan_tables'] = plan_tables

    # ---------------------------------------------------------- 5. LFL-сдвиги
    # «О'КЕЙ»: отношение плотности к «Гипер Ленте» (обе — на площади датабука): 2025H2 и 2026H1 — факт/проформа;
    # цель к 2029H2 по режиму; 2026H2 — провал перехода по режиму
    seg = NF['segments']
    hy_av_25h2 = eff['hyper']['eff_area_avg_hist']['2025H2']
    hy_av_26h1 = eff['hyper']['eff_area_avg_hist']['2026H1']
    r25h2 = (seg['okey']['revenue']['2025H2'] / ok_a) / (seg['hyper']['revenue']['2025H2'] / hy_av_25h2)
    r26h1 = (seg['okey']['revenue']['2026H1'] / ok_a) / (seg['hyper']['revenue']['2026H1'] / hy_av_26h1)
    okey_targets = {'stress': dict(r26h2=0.62, t2029=0.62), 'floor': dict(r26h2=0.65, t2029=0.72),
                    'partial': dict(r26h2=0.67, t2029=0.83), 'full': dict(r26h2=0.69, t2029=0.95)}
    hyper_off = {'2026H2': -0.005, '2027': -0.006, '2028': -0.008, 'LT': -0.010}
    okey_off = {}
    for rg, t in okey_targets.items():
        c26 = t['r26h2'] / r25h2 - 1
        c_h2 = (t['t2029'] / t['r26h2']) ** (1 / 3) - 1          # 2027H2, 2028H2, 2029H2
        c_h1 = (t['t2029'] / r26h1) ** (1 / 3) - 1                # 2027H1, 2028H1, 2029H1
        path = {}
        for p in ('2026H2', '2027H1', '2027H2', '2028H1', '2028H2', '2029H1', '2029H2'):
            conv_term = c26 if p == '2026H2' else (c_h1 if p.endswith('H1') else c_h2)
            path[p] = round(interp_path(hyper_off, p) + conv_term, 4)
        path['2030'] = hyper_off['LT']
        path['LT'] = hyper_off['LT']
        okey_off[rg] = path
    offsets = {
        'hyper': hyper_off,
        'super': {'2026H2': 0.050, '2027': 0.030, '2028': 0.015, 'LT': 0.005},
        'conv': {'2026H2': -0.008, '2027': -0.002, '2028': 0.005, 'LT': 0.005},
        'droge': {'2026H2': -0.030, '2027': -0.020, '2028': -0.010, 'LT': 0.0},
        'remi': {'2026H2': 0.0, 'LT': 0.0},
        'okey': okey_off,
    }
    res['lfl_offset'] = offsets
    res['okey_density'] = dict(ratio_2025H2=r25h2, ratio_2026H1=r26h1, targets=okey_targets,
                               density_okey_2025=seg['okey']['revenue']['2025H1'] and
                               (NF['estimation']['okey']['s2_hyper25'] and
                                (seg['okey']['revenue']['2025H1'] + seg['okey']['revenue']['2025H2']) / ok_a * 1e3),
                               density_okey_2025_ifrs_area=(seg['okey']['revenue']['2025H1'] +
                                                            seg['okey']['revenue']['2025H2']) /
                               NF['deals']['okey']['area_th_ifrs'] * 1e3,
                               density_hyper_2025=H['density_fy']['hyper']['2025'])

    # ---------------------------------------------------------- 7. A-K5: окупаемость открытия
    res['payback'] = payback(NSD)

    res['cp_seg'] = dict(CP_SEG)
    res['maturity_curve'] = MATURITY
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=float)
    report(res)


def payback(NSD):
    """IRR открытия формата при марже группы (IAS 17: аренда в EBITDA) и capex открытий 2025 на м² валовой площади."""
    import openpyxl
    wb = openpyxl.load_workbook(L.primary(L.DATABOOK), read_only=True, data_only=True)
    rows = list(wb['PL'].iter_rows(values_only=True))
    hdr = rows[7]
    col = next(c for c, v in enumerate(hdr) if isinstance(v, str) and v.replace(' ', '') == 'FY2025')   # первый — IAS 17
    sales = next(r for r in rows if isinstance(r[0], str) and r[0].strip().startswith('Sales'))[col] / 1e6
    ebitda = next(r for r in rows if isinstance(r[0], str) and r[0].strip() == 'EBITDA')[col] / 1e6
    rent = -next(r for r in rows if isinstance(r[0], str) and r[0].strip().startswith('Lease of premises'))[col] / 1e6
    m_group = ebitda / sales
    # capex открытий 2025: 46,1 × 48,7 % (презентация 2 кв. 2026, с. 24); валовая новая площадь 2025 — органика
    capex_open = 46.1 * 0.487
    org = H['organic_h']
    gross_2025 = 0.0
    for seg, ca in (('conv', CLOSE_AREA['conv']), ('super', CLOSE_AREA['super']), ('droge', CLOSE_AREA['droge'])):
        for p in ('2025H1', '2025H2'):
            gross_2025 += org[seg][p]['net_area_th'] + closures_half(seg, p) * ca
    gross_2025 += sum(org['other'][p]['net_area_th'] for p in ('2025H1', '2025H2'))
    gross_2025 += 4 * HYPER_NEW                  # 4 органических гипермаркета 2025 (ГО-2025: 9 с учётом «Молнии»)
    capex_m2 = capex_open / gross_2025 * 1e3     # тыс. руб./м²
    dens = H['density_fy']
    formats = {'Монетка': ('conv', dens['conv']['2025']), 'Улыбка радуги': ('droge', dens['droge']['2025']),
               'Супер Лента': ('super', dens['super']['2025']), 'Вингараж': ('conv', dens['other']['2025'])}
    out = dict(margin_group_2025=m_group, rent_pct_2025=rent / sales, capex_open_2025=capex_open,
               gross_new_area_2025_th=gross_2025, capex_per_m2=capex_m2, formats={}, breakeven_capex_12pct={})
    for name, (seg, dsty) in formats.items():
        d = NSD.get(seg) or 0.85
        for m_lab, m in (('маржа группы', m_group), ('маржа −2 п.п.', m_group - 0.02), ('маржа +2 п.п.', m_group + 0.02)):
            cfs = [-capex_m2]
            ramp = [MATURITY[0] / 2, (MATURITY[0] + MATURITY[1]) / 2, (MATURITY[1] + 1) / 2] + [1.0] * 17  # полугодия
            for h in range(20):                 # 10 лет, реальные цены, аренда — в марже (IAS 17)
                cfs.append(dsty * d * ramp[h] / 2 * m)
            irr_h = _irr(cfs)
            irr = (1 + irr_h) ** 2 - 1 if irr_h is not None else None
            cum, pb = 0.0, None
            for h, c in enumerate(cfs):
                cum += c
                if cum >= 0 and pb is None and h > 0:
                    pb = h / 2
            out['formats'].setdefault(name, {})[m_lab] = dict(density=dsty, nsd=d, margin=m, irr_real=irr,
                                                              payback_years=pb)
        # capex на м², при котором реальная IRR = 12 % при марже группы (10 лет, без остаточной стоимости)
        r_h = 1.12 ** 0.5 - 1
        ramp = [MATURITY[0] / 2, (MATURITY[0] + MATURITY[1]) / 2, (MATURITY[1] + 1) / 2] + [1.0] * 17
        pv = sum(dsty * d * ramp[h] / 2 * m_group / (1 + r_h) ** (h + 1) for h in range(20))
        out['breakeven_capex_12pct'][name] = pv
    return out


def _irr(cfs):
    lo, hi = -0.99, 5.0
    f = lambda r: sum(c / (1 + r) ** i for i, c in enumerate(cfs))  # noqa: E731
    if f(lo) * f(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def report(r):
    F = L.fmt
    print('== Калибровка плотности новой площади d (правило ядра; пары г/г без «Молнии»)')
    for seg in ('conv', 'super', 'droge'):
        for name, v in r['calibration'][seg].items():
            if isinstance(v, dict):
                pe = ' '.join(f'{x["p"]}: факт {x["actual"]:.4f} прогн {x["pred"]:.4f}' for x in v['pairs'])
                print(f'  {seg:6s} {name:26s} d = {v["d"]:.2f}  ({pe})')
        print(f'  {seg:6s} при cp 0,4 / 0,8: d = {r["calibration"][seg]["cp_0.4"]:.2f} / {r["calibration"][seg]["cp_0.8"]:.2f}')
    h = r['calibration']['hyper_cp']
    print(f'  hyper  cp (d = 0,85): {h["cp"]:.2f}  ' + ' '.join(f'{x["p"]}: {x["actual"]:.4f}/{x["pred"]:.4f}' for x in h['pairs']))
    print('\n== eff_area_avg_hist (тыс. м²; плотные когорты d) | вариант «до 1,0» | new_area_gross_hist')
    for s, v in r['eff_area'].items():
        print(f'  {s:6s} {v["eff_area_avg_hist"]}  {v.get("eff_area_avg_hist_to_1", "")}  {v.get("new_area_gross_hist", "")}')
    print('\n== Сценарии площади (доли площади в год)')
    for s, d in r['space'].items():
        for sc, v in d.items():
            print(f'  {s:6s} {sc:4s} open {v["gross_open"]}')
            print(f'  {"":6s} {"":4s} close {v["close"]}')
    print('\n== LFL-сдвиги', json.dumps(r['lfl_offset'], ensure_ascii=False))
    od = r['okey_density']
    print(f'О’КЕЙ / Гипер Лента, плотность на эфф. площади: 2025H2 {od["ratio_2025H2"]:.3f}, 2026H1 {od["ratio_2026H1"]:.3f}; '
          f'плотность О’КЕЙ 2025 {od["density_okey_2025"]:.0f} (на 478: {od["density_okey_2025_ifrs_area"]:.0f}), '
          f'гипер 2025 {od["density_hyper_2025"]:.0f} тыс. руб./м²')
    pb = r['payback']
    print(f'\n== A-K5: маржа группы 2025 {pb["margin_group_2025"]:.4f}, аренда {pb["rent_pct_2025"]:.4f} выручки; capex открытий '
          f'2025 {pb["capex_open_2025"]:.2f} млрд / {pb["gross_new_area_2025_th"]:.0f} тыс. м² = {pb["capex_per_m2"]:.1f} тыс. руб./м²')
    print('  capex/м² при IRR 12 % (маржа группы):', {k: round(v, 1) for k, v in pb['breakeven_capex_12pct'].items()})
    for f_, d in pb['formats'].items():
        print(f'  {f_:14s}', ' | '.join(f'{k}: IRR {v["irr_real"]:.0%} окуп. {v["payback_years"]} г.' if v['irr_real'] is not None
                                       else f'{k}: IRR — ' for k, v in d.items()))


if __name__ == '__main__':
    main()
