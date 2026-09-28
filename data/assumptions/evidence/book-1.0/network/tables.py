# -*- coding: utf-8 -*-
"""Печать таблиц README листа из *_out.json (ничего не пишет). python -B tables.py"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402

NF = json.load(open(os.path.join(L.HERE, 'network_facts_out.json'), encoding='utf-8'))
LT = json.load(open(os.path.join(L.HERE, 'lfl_ticket_out.json'), encoding='utf-8'))
H = NF['history']
F = L.fmt
HALVES = ['2023H1', '2023H2', '2024H1', '2024H2', '2025H1', '2025H2', '2026H1']
QS = ['2025Q1', '2025Q2', '2025Q3', '2025Q4', '2026Q1', '2026Q2']
FMTS = [('hyper', 'Гипер'), ('super', 'Супер'), ('conv', 'У дома'), ('other', 'Прочие («Вингараж»)'),
        ('droge', 'Дрогери'), ('remi', 'Реми'), ('diy', 'Дом Лента'), ('wholesale', 'Опт'), ('total', 'Итого')]


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    print('### Выручка по полугодиям, млрд руб. (датабук, Operating Results; полугодие = сумма кварталов)\n')
    print('| Формат | ' + ' | '.join(HALVES) + ' |')
    print('|---|' + '---:|' * len(HALVES))
    for f, n in FMTS:
        print(f'| {n} | ' + ' | '.join(F(H['revenue_h'][f].get(p), 1) for p in HALVES) + ' |')
    print('\n### Площадь (тыс. м²) и магазины на конец полугодия\n')
    print('| Формат | ' + ' | '.join(HALVES) + ' |')
    print('|---|' + '---:|' * len(HALVES))
    for f, n in FMTS:
        if f == 'wholesale':
            continue
        print(f'| {n}, м² | ' + ' | '.join(F(H['area_q'][f].get(L.eop_quarter(p)), 1) if p >= '2019H1' and
                                         L.eop_quarter(p) in H['area_q'][f] else '—' for p in HALVES) + ' |')
        print(f'| {n}, маг. | ' + ' | '.join(str(int(H['stores_q'][f][L.eop_quarter(p)])) if L.eop_quarter(p) in
                                           H['stores_q'][f] else '—' for p in HALVES) + ' |')
    print('\n### Органика: чистые магазины / тыс. м² за полугодие (изменение сети − периметр сделок)\n')
    print('| Формат | ' + ' | '.join(HALVES[2:]) + ' |')
    print('|---|' + '---:|' * len(HALVES[2:]))
    for f, n in FMTS:
        if f not in H['organic_h']:
            continue
        o = H['organic_h'][f]
        print(f'| {n} | ' + ' | '.join(f'{int(o[p]["net_stores"])} / {F(o[p]["net_area_th"], 1)}' for p in HALVES[2:]) + ' |')
    print('\n### LFL по полугодиям, % г/г: выручка / трафик / чек (кварталы с весами выручки базы)\n')
    print('| | ' + ' | '.join(HALVES) + ' |')
    print('|---|' + '---:|' * len(HALVES))
    for f, n in (('group', 'Группа'), ('hyper', 'Гипер'), ('super', 'Супер'), ('conv', 'У дома'), ('droge', 'Дрогери')):
        cells = []
        for p in HALVES:
            v = [H['lfl_h'][f][c].get(p) for c in ('sales', 'traffic', 'ticket')]
            if any(x is None for x in v) or all(abs(x) < 1e-12 for x in v):
                cells.append('—')
            else:
                cells.append('/'.join(F(x * 100, 1) for x in v))
        print(f'| {n} | ' + ' | '.join(cells) + ' |')
    print('\n### Плотность, тыс. руб./м² в год (выручка / средняя площадь)\n')
    yrs = ['2019', '2020', '2021', '2022', '2023', '2024', '2025']
    print('| Формат | ' + ' | '.join(yrs) + ' |')
    print('|---|' + '---:|' * len(yrs))
    for f, n in FMTS:
        if f in H['density_fy']:
            print(f'| {n} | ' + ' | '.join(F(H['density_fy'][f].get(y), 0) for y in yrs) + ' |')
    print('\n### Онлайн, млрд руб. (квартал)\n')
    print('| | ' + ' | '.join(QS) + ' |')
    print('|---|' + '---:|' * len(QS))
    for k, n in (('total', 'Всего'), ('partners', 'Партнёры'), ('own', 'Собственный')):
        print(f'| {n} | ' + ' | '.join(F(H['online_q'][k][q], 1) for q in QS) + ' |')
    print('\n### Квартальная выручка по форматам 2025–2026, млрд руб.\n')
    print('| Формат | ' + ' | '.join(QS) + ' |')
    print('|---|' + '---:|' * len(QS))
    for f, n in FMTS:
        print(f'| {n} | ' + ' | '.join(F(H['revenue_q'][f].get(q), 1) for q in QS) + ' |')


if __name__ == '__main__':
    main()
