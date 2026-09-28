# -*- coding: utf-8 -*-
"""Лист «Сеть и выручка» (книга 1.0), часть 1: история форматов и факты сегментов на периметре якоря.

Что делает:
  1. Датабук (листы Operating Results и Monthly Operating Results) → ряды по форматам: выручка, магазины, площадь,
     чистые открытия, новая площадь, LFL (выручка/трафик/чек), онлайн — поквартально и по полугодиям.
  2. Сделки периметра (Молния, Улыбка, Реми, Дом Лента, О'КЕЙ) — из месячного листа датабука с проверкой по МСФО
     и релизам; органика = изменение сети − периметр сделки (строка «Net Openings» датабука непоследовательна).
  3. Сегменты DESIGN D3 (hyper, okey, super, conv, droge, remi, diy, wholesale): выручка 2025H1/2025H2/2026H1 с
     базисом reported|pro_forma|estimate и se, площадь и магазины на 30.06.2026; правило оценки баз «О'КЕЙ» и «Реми».
  4. Проверки: сумма сегментов 1П2026 = проформа D3 (648,49 + 66,12 − 9,36); отношение 2П/1П группы на периметре
     якоря — в коридоре истории; доли кварталов на периметре с «О'КЕЙ».

  python -B network_facts.py            → печать + network_facts_out.json
"""
from __future__ import annotations

import json
import os
import re
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402

OUT = os.path.join(L.HERE, 'network_facts_out.json')
FORMATS = ['hyper', 'super', 'conv', 'droge', 'remi', 'diy', 'other', 'wholesale', 'total', 'retail']
AREA_FORMATS = ['hyper', 'super', 'conv', 'droge', 'remi', 'diy', 'other', 'total']


def ifrs_facts():
    """Числа МСФО, вынутые регуляркой из текста PDF (страница указана)."""
    t26 = L.pdf_text('ifrs_mkpao/ifrs_1H2026.pdf', [16, 18, 19])
    t25 = L.pdf_text('ifrs_mkpao/ifrs_FY2025.pdf', [47, 49])
    flat26, flat25 = re.sub(r'\s+', ' ', t26), re.sub(r'\s+', ' ', t25)

    def grab(pat, s, what):
        m = re.search(pat, s)
        if not m:
            sys.exit(f'не найдено в МСФО: {what}')
        return L.rub_thousands(m.group(1))
    f = dict(
        okey_contrib_2026=grab(r'вклад сети гипермаркетов «О’КЕЙ» в выручку Группы составил ([\d ]+) тыс', flat26,
                               'вклад О’КЕЙ с даты'),
        okey_full_2026H1=grab(r'влияние на выручку за 2026 год составило бы ([\d ]+) тыс', flat26, 'проформа О’КЕЙ'),
        okey_pbt_contrib=-grab(r'убыток до налогообложения с даты приобретения ([\d ]+) тыс', flat26, 'убыток О’КЕЙ'),
        okey_pbt_full=-grab(r'до налогообложения составило бы ([\d ]+) тыс', flat26, 'прибыль до налога О’КЕЙ'),
        diy_contrib_2026H1=grab(r'вклад сети гипермаркетов «Дом Лента» в выручку составил ([\d ]+) тыс', flat26,
                                'вклад Дом Лента'),
        molnia_contrib_2025=grab(r'вклад магазинов сети «Молния - Spar» в выручку Группы составил ([\d ]+) тыс', flat25,
                                 'вклад Молнии'),
        molnia_full_2025=grab(r'Если бы объединение произошло в начале 2025 года, то выручка Группы за 2025 год '
                              r'составила бы ([\d ]+) тыс\. руб\. Признанный', flat25, 'проформа Молнии'),
        remi_contrib_2025=grab(r'вклад компаний «Реми» в выручку Группы составил ([\d ]+) тыс', flat25, 'вклад Реми'),
        remi_full_2025=grab(r'составила бы ([\d ]+) тыс\. руб\. Так как приобретенный бизнес', flat25, 'проформа Реми'),
    )
    m = re.search(r'75 гипермаркетов «О’КЕЙ» с совокупной торговой площадью (\d+) тыс', flat26)
    f['okey_area_ifrs_th'] = float(m.group(1)) if m else None
    m = re.search(r'вошли (\d+) магазинов с общей торговой площадью (\d+) тыс', flat26)
    f['diy_stores_ifrs'], f['diy_area_ifrs_th'] = (int(m.group(1)), float(m.group(2))) if m else (None, None)
    f['source'] = ('МСФО 6М2026 прим. 5, с. 16, 18–19 (ifrs_mkpao/ifrs_1H2026.pdf); МСФО 2025 прим. 8, с. 47, 49 '
                   '(ifrs_mkpao/ifrs_FY2025.pdf)')
    return f


def release_facts():
    """Релизы lentagroup.ru: периметр «О'КЕЙ» и «Реми», валовые открытия по форматам."""
    ok = L.text_file('lentagroup_news/2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.txt')
    m = re.search(r'войдут (\d+) гипермаркетов «О’КЕЙ» с совокупной торговой площадью (\d+) тыс', ok)
    m2 = re.search(r'выручка которой составила (\d+) млрд рублей в 2025 году', ok)
    remi = L.text_file('lentagroup_news/2025-12-10_gruppa-lenta-vykhodit-na-dalniy-vostok-pokupaet-kontrolnuyu-dolyu-v-od.txt')
    m3 = re.search(r'войдут (\d+) торговых точек', remi)
    m4 = re.search(r'совокупная выручка сети составила около (\d+) млрд рублей', remi)
    q226 = L.text_file('lentagroup_news/2026-08-03_gruppa-lenta-obyavlyaet-o-roste-vyruchki-na-28-8-roste-lfl-prodazh-na-.txt')
    m5 = re.search(r'открыв (\d+) магазинов за квартал', q226)
    q126 = L.text_file('lentagroup_news/2026-04-30_gruppa-lenta-obyavlyaet-o-roste-vyruchki-na-23-4-v-1-kvartale-2026-god.txt')
    q125 = L.text_file('lentagroup_news/2025-04-28_lenta-announces-232-growth-in-sales-and-124-lfl-sales-increase-in-q1-2.txt')
    m6 = re.search(r'открыв (\d+) новых магазина в первом квартале', q125)
    q325 = L.text_file('lentagroup_news/2025-10-31_lenta-group-announces-268-revenue-growth-in-q3-2025-ebitda-margin-of-7.txt')
    m7 = re.search(r'формат дрогери, открыв (\d+) магазинов', q325)
    q124 = L.text_file('lentagroup_news/2024-04-26_lenta-announces-revenue-growth-of-621-and-ebitda-margin-expansion-of-4.txt')
    m8 = re.search(r'открыла (\d+) магазина у дома и (\d+) супермаркетов на валовой основе', q124)
    return dict(
        okey_stores=int(m.group(1)), okey_area_th=float(m.group(2)), okey_rev_2025=float(m2.group(1)),
        remi_points=int(m3.group(1)), remi_rev_2024=float(m4.group(1)),
        # валовые открытия (магазины) по кварталам, где компания их раскрыла
        gross_open={
            '2024Q1': {'conv': int(m8.group(1)), 'super': int(m8.group(2))},
            '2025Q1': {'conv': int(m6.group(1))},                       # «Монетка» 224 новых магазина
            '2025Q3': {'droge': int(m7.group(1))},                      # дрогери 128
            # 4 кв. 2025 и 1 кв. 2026 — англ. релизы (PDF): Q425 «381 new stores on a gross basis, including 232
            # convenience stores, 98 drogerie stores, 2 hypermarkets, 13 supermarkets and 34 Vingarazh stores»;
            # Q126 «395 … 263 convenience stores, 101 drogerie stores, 11 supermarkets (including 7 refurbishments)
            # and 20 Vingarazh stores» — текстовый слой PDF, выписка ниже в check_gross_pdf()
            '2026Q2': {'conv': int(m5.group(1))},                       # «Монетка» 229 за квартал
        },
        source='релизы 02.06.2026, 10.12.2025, 03.08.2026, 30.04.2026, 28.04.2025, 31.10.2025, 26.04.2024 '
               '(lentagroup_news/*.txt)',
        _q126_text_ok=bool(re.search(r'245 магазинов у дома', q126)),
    )


def gross_from_pdf():
    """Валовые открытия 4 кв. 2025 и 1 кв. 2026 — из текстового слоя англоязычных релизов (reference/primary)."""
    out = {}
    for rel, q in (('Lenta_Group_Q425_Financial_Results_ENG_vF.pdf', '2025Q4'),
                   ('Lenta_Group_Q126_Financial_Results_ENG_vF.pdf', '2026Q1')):
        from pypdf import PdfReader
        r = PdfReader(L.primary(rel, check=False))
        t = re.sub(r'\s+', ' ', ' '.join((p.extract_text() or '') for p in r.pages))
        t = t.replace('- ', '-')
        m = re.search(r'opened (\d+) new stores on a gross basis, including (\d+) convenience stores, (\d+) drogerie '
                      r'stores,(?: (\d+) hypermarkets,)? (\d+) supermarkets(?: \(including (\d+) refurbishments\))? and '
                      r'(\d+) Vingarazh', t)
        if not m:
            out[q] = None
            continue
        out[q] = dict(total=int(m.group(1)), conv=int(m.group(2)), droge=int(m.group(3)),
                      hyper=int(m.group(4) or 0), super=int(m.group(5)), super_refurb=int(m.group(6) or 0),
                      other=int(m.group(7)))
    return out


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    D = L.load_databook()
    O, M = D['oper'], D['month']
    IF, RL = ifrs_facts(), release_facts()
    GP = gross_from_pdf()
    for q, v in GP.items():
        if v:
            RL['gross_open'][q] = {k: v[k] for k in ('conv', 'droge', 'hyper', 'super', 'other')}
            RL['gross_open'][q]['super_refurb'] = v['super_refurb']

    years = list(range(2012, 2027))
    quarters = [f'{y}Q{q}' for y in years for q in (1, 2, 3, 4) if f'{y}Q{q}' in O['revenue']['total']]
    halves = [f'{y}H{h}' for y in years for h in (1, 2) if all(q in quarters for q in L.quarters_of_half(f'{y}H{h}'))]

    # ---------------------------------------------------------- 1. ряды по форматам
    rev_q = {f: {q: (O['revenue'][f][q] or 0.0) / 1e3 for q in quarters} for f in FORMATS}   # млрд руб.
    rev_h = {f: {p: sum(rev_q[f][q] for q in L.quarters_of_half(p)) for p in halves} for f in FORMATS}
    area_q = {f: {q: (O['area'][f][q] or 0.0) / 1e3 for q in quarters} for f in AREA_FORMATS}   # тыс. м²
    stores_q = {f: {q: O['stores'][f][q] or 0.0 for q in quarters} for f in AREA_FORMATS}
    netop_q = {f: {q: O['net_open'][f][q] or 0.0 for q in quarters} for f in AREA_FORMATS}
    newar_q = {f: {q: (O['new_area'][f][q] or 0.0) / 1e3 for q in quarters} for f in AREA_FORMATS}
    # сверки датабука: 1П2026 = 1 кв. + 2 кв.; итог = сумма форматов
    for f in FORMATS:
        col = (O['revenue'][f].get('2026H1') or 0.0) / 1e3
        assert abs(col - rev_h[f]["2026H1"]) < 1e-3, (f, col, rev_h[f]["2026H1"])   # датабук: 1П — сумма кварталов до 0,05 млн руб.
    for q in quarters[-12:]:
        s = sum(rev_q[f][q] for f in ('hyper', 'super', 'conv', 'droge', 'remi', 'diy', 'other', 'wholesale'))
        s += (O['revenue']['utkonos'][q] or 0.0) / 1e3 if isinstance(O['revenue']['utkonos'][q], float) else 0.0
        assert abs(s - rev_q['total'][q]) < 0.01, (q, s, rev_q['total'][q])

    lfl_q = {}
    for fk, bk in (('group', 'lfl_group'), ('hyper', 'lfl_hyper'), ('super', 'lfl_super'), ('conv', 'lfl_conv'),
                   ('droge', 'lfl_droge')):
        lfl_q[fk] = {n: {q: O[bk][n][q] for q in quarters} for n in L.LFL_NAMES}

    def lfl_half(fk, p, n):
        """Полугодовой LFL — средний кварталов с весами выручки базы прошлого года (формата; для группы — розница)."""
        base_f = {'group': 'retail'}.get(fk, fk)
        qs = L.quarters_of_half(p)
        vals = [lfl_q[fk][n][q] for q in qs]
        if any(v is None for v in vals):
            return None
        w = [rev_q[base_f].get(f'{int(q[:4]) - 1}{q[4:]}', 0.0) or 0.0 for q in qs]
        if sum(w) <= 0:
            w = [1, 1]
        return sum(a * b for a, b in zip(vals, w)) / sum(w)

    lfl_h = {fk: {n: {p: lfl_half(fk, p, n) for p in halves} for n in L.LFL_NAMES} for fk in lfl_q}
    online_q = {k: {q: (O['online'][k][q] or 0.0) / 1e3 for q in quarters} for k in ('total', 'partners', 'own')}

    # ---------------------------------------------------------- 2. сезонность 2П/1П
    ratio_h2h1 = {}
    for f in ('total', 'retail', 'hyper', 'super', 'conv', 'droge'):
        rq = rev_q.get(f) or {q: (O['revenue'][f][q] or 0) / 1e3 for q in quarters}
        ratio_h2h1[f] = {}
        for y in years:
            h1, h2 = L.halves_of(rq, y)
            if h1 and h2 and h1 > 0:
                ratio_h2h1[f][y] = h2 / h1
    # разрывы периметра группы (сделки во 2П): 2021 «Билла»/«Семья», 2023 «Монетка», 2024 «Улыбка»,
    # 2025 «Молния» (конец 1П — почти целиком во 2П) и «Реми»; 2020–2021 — пандемия
    breaks = {2020: 'пандемия', 2021: 'Билла, Семья (авг.)', 2023: 'Монетка (окт.)', 2024: 'Улыбка (дек.)',
              2025: 'Молния (24.06), Реми (дек.)'}
    # коридор: годы без разрывов периметра после эпохи массовых открытий гипермаркетов (2013–2015 — открытия
    # концентрировались во 2П и завышали 2П/1П) — группа 2016–2019, 2022 — плюс органические 2П/1П гипермаркетов
    # 2023–2025 (формат без сделок, кроме 5 гипермаркетов «Молнии» в 2025)
    corridor_years = [y for y in range(2016, 2026) if y not in breaks and y in ratio_h2h1['total']]
    corr_vals = [ratio_h2h1['total'][y] for y in corridor_years] +         [ratio_h2h1['hyper'][y] for y in (2023, 2024, 2025)]
    hyper_vals = [ratio_h2h1['hyper'][y] for y in range(2013, 2026) if y != 2020 and y in ratio_h2h1['hyper']]

    # ---------------------------------------------------------- 3. сделки периметра (месячный лист)
    mm = M
    def mval(block, f, ym):
        v = mm[block][f].get(ym)
        return v or 0.0
    deals = {}
    # «Молния» (24.06.2025): июнь 2025 — гипер +5 (весь прирост), супер +29 (ГО-2025 «интегрировав в сеть 29
    # супермаркетов»), у дома: 72 − 5 − 29 = 38 из 122 чистых июньских (органика июня ≈ 84 при 80/89 в мае/июле)
    molnia_total_stores = 72     # релиз 26.06.2025 (lentagroup_news/2025-06-26_…molniyu…txt)
    mt = L.text_file('lentagroup_news/2025-06-26_lenta-priobretaet-molniyu-odnu-iz-krupnejshih-torgovyh-setej-chelyabin.txt')
    assert re.search(r'72 магазин', mt), 'Молния: 72 магазина'
    m_h, m_s = mval('net_open', 'hyper', '2025-06'), mval('net_open', 'super', '2025-06')
    m_c = molnia_total_stores - m_h - m_s
    org_c_jun = mval('net_open', 'conv', '2025-06') - m_c
    per_store_new = (mval('new_area', 'conv', '2025-05') + mval('new_area', 'conv', '2025-07')) / \
        (mval('net_open', 'conv', '2025-05') + mval('net_open', 'conv', '2025-07'))
    molnia_conv_area = mval('new_area', 'conv', '2025-06') - org_c_jun * per_store_new
    deals['molnia'] = dict(date='2025-06-24', half='2025H1', stores={'hyper': m_h, 'super': m_s, 'conv': m_c},
                           area_th={'hyper': mval('new_area', 'hyper', '2025-06') / 1e3,
                                    'super': mval('new_area', 'super', '2025-06') / 1e3,
                                    'conv': molnia_conv_area / 1e3},
                           revenue_contrib_2025=IF['molnia_contrib_2025'], revenue_full_2025=IF['molnia_full_2025'],
                           note='месячный лист, июнь 2025; у дома — расчёт (органика июня по средней площади '
                                'новых магазинов мая/июля); сверка: сумма > 60 тыс. м² (релиз 26.06.2025)')
    # «Улыбка радуги» (01.12.2024): декабрь 2024, дрогери
    deals['ulybka'] = dict(date='2024-12-01', half='2024H2', stores={'droge': mval('stores', 'droge', '2024-12')},
                           area_th={'droge': mval('area', 'droge', '2024-12') / 1e3})
    # «Реми» (03.12.2025): прирост магазинов 118 − чистые открытия 2
    deals['remi'] = dict(date='2025-12-03', half='2025H2',
                         stores={'remi': mval('stores', 'remi', '2025-12') - mval('net_open', 'remi', '2025-12')},
                         area_th={'remi': (mval('area', 'remi', '2025-12') - mval('new_area', 'remi', '2025-12')) / 1e3},
                         revenue_contrib_2025=IF['remi_contrib_2025'], revenue_full_2025=IF['remi_full_2025'])
    # «Дом Лента» (янв.–март 2026)
    deals['diy'] = dict(date='2026-01..03', half='2026H1',
                        stores={'diy': sum(mval('net_open', 'diy', f'2026-0{m}') for m in (1, 2, 3))},
                        area_th={'diy': sum(mval('new_area', 'diy', f'2026-0{m}') for m in (1, 2, 3)) / 1e3},
                        revenue_contrib_2026H1=IF['diy_contrib_2026H1'],
                        ifrs=dict(stores=IF['diy_stores_ifrs'], area_th=IF['diy_area_ifrs_th']))
    # «О'КЕЙ» (02.06.2026): гипер июнь — прирост 75 магазинов при 1 чистом открытии (эконом-гипермаркет в Саратове,
    # релиз 03.08.2026); площадь: прирост июня − органика (формат «Эконом» 2,5–4,0 тыс. м², стратегия, с. 27)
    econ_lo, econ_hi = 2.5, 4.0
    okey_area_resid = (mval('area', 'hyper', '2026-06') - mval('area', 'hyper', '2026-05')) / 1e3 - (econ_lo + econ_hi) / 2
    deals['okey'] = dict(date='2026-06-02', half='2026H1',
                         stores_databook=mval('stores', 'hyper', '2026-06') - mval('stores', 'hyper', '2026-05')
                         - mval('net_open', 'hyper', '2026-06'),
                         stores_ifrs=RL['okey_stores'],
                         area_th_databook=okey_area_resid, area_th_ifrs=IF['okey_area_ifrs_th'],
                         area_th_se=(econ_hi - econ_lo) / 2,
                         new_area_row_june_th=mval('new_area', 'hyper', '2026-06') / 1e3,
                         revenue_contrib_2026=IF['okey_contrib_2026'], revenue_full_2026H1=IF['okey_full_2026H1'],
                         revenue_2025_release=RL['okey_rev_2025'])

    # ---------------------------------------------------------- 4. органика по форматам (полугодия)
    acq_stores = {('2025H1', 'hyper'): m_h, ('2025H1', 'super'): m_s, ('2025H1', 'conv'): m_c,
                  ('2024H2', 'droge'): deals['ulybka']['stores']['droge'],
                  ('2025H2', 'remi'): deals['remi']['stores']['remi'],
                  ('2026H1', 'diy'): deals['diy']['stores']['diy'],
                  ('2026H1', 'hyper'): deals['okey']['stores_ifrs']}
    acq_area = {('2025H1', 'hyper'): deals['molnia']['area_th']['hyper'],
                ('2025H1', 'super'): deals['molnia']['area_th']['super'],
                ('2025H1', 'conv'): deals['molnia']['area_th']['conv'],
                ('2024H2', 'droge'): deals['ulybka']['area_th']['droge'],
                ('2025H2', 'remi'): deals['remi']['area_th']['remi'],
                ('2026H1', 'diy'): deals['diy']['area_th']['diy'],
                ('2026H1', 'hyper'): deals['okey']['area_th_databook']}
    organic = {}
    for f in ('hyper', 'super', 'conv', 'droge', 'remi', 'diy', 'other'):
        organic[f] = {}
        for p in halves:
            if p < '2021H1':
                continue
            q_end, q0 = L.eop_quarter(p), L.quarters_of_half(p)[0]
            prev_end = quarters[quarters.index(q0) - 1]
            ds = stores_q[f][q_end] - stores_q[f][prev_end]
            da = area_q[f][q_end] - area_q[f][prev_end]
            organic[f][p] = dict(net_stores=ds - acq_stores.get((p, f), 0), net_area_th=da - acq_area.get((p, f), 0.0),
                                 databook_net_openings=sum(netop_q[f][q] for q in L.quarters_of_half(p)))
    # Монетка до 4 кв. 2023 — у дома пустые; 4 кв. 2023 — покупка «Монетки» (2 120) + передача ≈88 «Мини Лент» из
    # супермаркетов — органикой не считается
    for f in ('conv', 'super'):
        if '2023H2' in organic[f]:
            organic[f]['2023H2']['note'] = 'периметр: покупка «Монетки» 17.10.2023 и передача «Мини Лент» в у дома'

    # закрытия из пар «валовые − чистые» (магазины), где валовые раскрыты
    closures_q = {}
    for q, g in RL['gross_open'].items():
        closures_q[q] = {}
        for f, n in g.items():
            if f == 'super_refurb':
                continue
            net = netop_q[f][q] if f != 'other' else netop_q['other'][q]
            closures_q[q][f] = n - net

    # ---------------------------------------------------------- 5. плотности (тыс. руб./м² в год)
    def avg_area(f, p):
        qs = L.quarters_of_half(p)
        q0 = quarters[quarters.index(qs[0]) - 1]
        pts = [area_q[f][q0], area_q[f][qs[0]], area_q[f][qs[1]]]
        return (pts[0] / 2 + pts[1] + pts[2] / 2) / 2      # трапеция по концам кварталов
    density_h = {}
    for f in ('hyper', 'super', 'conv', 'droge', 'remi', 'diy', 'other'):
        density_h[f] = {}
        for p in halves:
            if p < '2019H1':
                continue
            a = avg_area(f, p)
            if a > 0 and rev_h[f][p] > 0:
                density_h[f][p] = rev_h[f][p] * 2 / a * 1e3       # млрд/тыс. м² → тыс. руб./м² в год
    density_fy = {}
    for f in density_h:
        density_fy[f] = {}
        for y in range(2019, 2026):
            a = (avg_area(f, f'{y}H1') + avg_area(f, f'{y}H2')) / 2
            r = rev_h[f][f'{y}H1'] + rev_h[f][f'{y}H2']
            if a > 0 and r > 0 and rev_h[f][f'{y}H1'] > 0:
                density_fy[f][y] = r / a * 1e3

    # ---------------------------------------------------------- 6. сегменты на периметре якоря
    okc = IF['okey_contrib_2026']
    hyper_rev = {'2025H1': rev_h['hyper']['2025H1'], '2025H2': rev_h['hyper']['2025H2'],
                 '2026H1': rev_h['hyper']['2026H1'] - okc}
    # сезонное правило баз «О'КЕЙ»: год 142 (релиз 02.06.2026) делится долей 2П гипермаркетов «Ленты» 2025
    s2_hyper25 = rev_h['hyper']['2025H2'] / (rev_h['hyper']['2025H1'] + rev_h['hyper']['2025H2'])
    ok25 = RL['okey_rev_2025']
    ok_h1, ok_h2 = ok25 * (1 - s2_hyper25), ok25 * s2_hyper25
    # se: год ±4 % (142 по релизу против 147,9 РСБУ ООО «О'Кей» по СМИ — другой периметр юрлица) и доля 2П
    # ±0,5 п.п. (разброс доли 2П гипермаркетов 2016–2019, 2022 — см. seasonal_spread)
    hyper_s2_hist = [rev_h['hyper'][f'{y}H2'] / (rev_h['hyper'][f'{y}H1'] + rev_h['hyper'][f'{y}H2'])
                     for y in (2016, 2017, 2018, 2019, 2022, 2023, 2024, 2025)]
    s2_sd = st.pstdev(hyper_s2_hist)
    ok_se_year = 0.04 * ok25
    ok_se_h2 = ((ok_se_year * s2_hyper25) ** 2 + (ok25 * s2_sd) ** 2) ** 0.5
    ok_se_h1 = ((ok_se_year * (1 - s2_hyper25)) ** 2 + (ok25 * s2_sd) ** 2) ** 0.5
    # альтернативное правило рецензии (critique-valuation Б5): 2П2025 = проформа 1П2026 × 2П/1П гипер 2025 / (1 + LFL)
    # — при LFL «О'КЕЙ», выведенном из того же года, совпадает по построению; сверка — г/г 1П2026 к оценке 1П2025
    okey_yoy_2026H1 = IF['okey_full_2026H1'] / ok_h1 - 1
    # темп «О'КЕЙ» внутри 1П2026: январь–май (проформа − вклад с 02.06) против июня под «Лентой» (29 дней), г/г к
    # 2025, разложенному месячным профилем гипермаркетов «Ленты» 2025
    hyp_m25 = {m: mval('revenue', 'hyper', f'2025-{m:02d}') for m in range(1, 13)}
    hyp_y25 = sum(hyp_m25.values())
    ok_jan_may_25 = ok25 * sum(hyp_m25[m] for m in range(1, 6)) / hyp_y25
    ok_jun_25 = ok25 * hyp_m25[6] / hyp_y25
    ok_jan_may_26 = IF['okey_full_2026H1'] - okc
    okey_intra = dict(jan_may_2026=ok_jan_may_26, jan_may_2025_est=ok_jan_may_25,
                      yoy_jan_may=ok_jan_may_26 / ok_jan_may_25 - 1,
                      june_2026_29d=okc, june_2025_est=ok_jun_25,
                      yoy_june_per_day=(okc / 29) / (ok_jun_25 / 30) - 1,
                      monthly_avg_jan_may_2026=ok_jan_may_26 / 5)

    # «Реми»: проформа года 55,488 (МСФО 2025) делится долей 2П розницы «Ленты» 2025 без покупок
    # (гипер + супер + у дома); 2П2025 включает декабрь в периметре (5,17 по датабуку)
    core = ('hyper', 'super', 'conv')
    s2_core25 = sum(rev_h[f]['2025H2'] for f in core) / sum(rev_h[f]['2025H1'] + rev_h[f]['2025H2'] for f in core)
    s2_formats = {f: rev_h[f]['2025H2'] / (rev_h[f]['2025H1'] + rev_h[f]['2025H2']) for f in core}
    remi25 = IF['remi_full_2025']
    remi_h1, remi_h2 = remi25 * (1 - s2_core25), remi25 * s2_core25
    remi_s2_spread = max(abs(v - s2_core25) for v in s2_formats.values())
    # se: доля 2П — разброс по форматам (гипер/супер/у дома) и декабрьская сезонность «Реми» слабее «Ленты»
    # (дек. 2025 5,17 против среднего янв.–июн. 2026 4,61: 1,12× против 1,45× у группы) → +0,5 п.п.;
    # проформа года — оценка менеджмента без МСФО «Реми» (прим. 8): ±3 %
    remi_se_h2 = ((remi25 * (remi_s2_spread + 0.005)) ** 2 + (0.03 * remi_h2) ** 2) ** 0.5
    remi_se_h1 = ((remi25 * (remi_s2_spread + 0.005)) ** 2 + (0.03 * remi_h1) ** 2) ** 0.5
    remi_dec_ratio = mval('revenue', 'remi', '2025-12') / (sum(mval('revenue', 'remi', f'2026-0{m}')
                                                               for m in range(1, 7)) / 6)
    grp_dec_ratio = mval('revenue', 'total', '2025-12') / (sum(mval('revenue', 'total', f'2025-{m:02d}')
                                                               for m in range(1, 12)) / 11)
    remi_yoy_2026H1 = rev_h['remi']['2026H1'] / remi_h1 - 1

    conv_rev = {p: rev_h['conv'][p] + rev_h['other'][p] for p in ('2025H1', '2025H2', '2026H1')}
    segs = {
        'hyper': dict(revenue=hyper_rev, basis={'2025H1': 'reported', '2025H2': 'reported', '2026H1': 'reported'},
                      se={'2025H1': 0.0, '2025H2': 0.0, '2026H1': 0.3},
                      note='строка Hypermarket датабука; 1П2026 — минус вклад «О’КЕЙ» за июнь по МСФО (9,36): '
                           'se 0,3 — управленческий учёт против МСФО за один месяц'),
        'okey': dict(revenue={'2025H1': ok_h1, '2025H2': ok_h2, '2026H1': IF['okey_full_2026H1']},
                     basis={'2025H1': 'estimate', '2025H2': 'estimate', '2026H1': 'pro_forma'},
                     se={'2025H1': ok_se_h1, '2025H2': ok_se_h2, '2026H1': 0.5},
                     note='1П2026 — проформа МСФО 6М2026 прим. 5 (весь вклад сети за полугодие, включая июнь); '
                          '2025 — год 142 × доля полугодия гипермаркетов «Ленты» 2025'),
        'super': dict(revenue={p: rev_h['super'][p] for p in ('2025H1', '2025H2', '2026H1')},
                      basis={p: 'reported' for p in ('2025H1', '2025H2', '2026H1')},
                      se={p: 0.0 for p in ('2025H1', '2025H2', '2026H1')},
                      note='строка Supermarkets; «Молния» (29 супермаркетов) — с 24.06.2025, 2П2025 и 1П2026 на периметре'),
        'conv': dict(revenue=conv_rev, basis={p: 'reported' for p in conv_rev},
                     se={p: 0.0 for p in conv_rev},
                     note='Convenience stores («Монетка», «Мини Лента» с 4 кв. 2023) + Other formats («Вингараж»)'),
        'droge': dict(revenue={p: rev_h['droge'][p] for p in ('2025H1', '2025H2', '2026H1')},
                      basis={p: 'reported' for p in ('2025H1', '2025H2', '2026H1')},
                      se={p: 0.0 for p in ('2025H1', '2025H2', '2026H1')}, note='строка Drogerie'),
        'remi': dict(revenue={'2025H1': remi_h1, '2025H2': remi_h2, '2026H1': rev_h['remi']['2026H1']},
                     basis={'2025H1': 'estimate', '2025H2': 'estimate', '2026H1': 'reported'},
                     se={'2025H1': remi_se_h1, '2025H2': remi_se_h2, '2026H1': 0.0},
                     note='1П2026 — строка Remi (полное полугодие); 2025 — проформа МСФО 2025 прим. 8 (55,488) × доля '
                          'полугодия розницы «Ленты» (гипер+супер+у дома) 2025'),
        'diy': dict(revenue={'2025H1': None, '2025H2': None, '2026H1': rev_h['diy']['2026H1']},
                    basis={'2025H1': None, '2025H2': None, '2026H1': 'reported'},
                    se={'2025H1': None, '2025H2': None, '2026H1': 0.0},
                    note='режим level: база прошлого года не нужна; 1П2026 — неполный периметр (покупка янв.–март, '
                         'ребрендинг); первички по ОБИ Россия до сделки нет'),
        'wholesale': dict(revenue={p: rev_h['wholesale'][p] for p in ('2025H1', '2025H2', '2026H1')},
                          basis={p: 'reported' for p in ('2025H1', '2025H2', '2026H1')},
                          se={p: 0.0 for p in ('2025H1', '2025H2', '2026H1')}, note='строка Wholesales'),
    }
    # площадь и магазины на 30.06.2026 (тыс. м²)
    q = '2026Q2'
    okey_area = deals['okey']['area_th_databook']
    area_end = {'hyper': area_q['hyper'][q] - okey_area, 'okey': okey_area, 'super': area_q['super'][q],
                'conv': area_q['conv'][q] + area_q['other'][q], 'droge': area_q['droge'][q],
                'remi': area_q['remi'][q], 'diy': area_q['diy'][q], 'wholesale': None}
    stores_end = {'hyper': int(stores_q['hyper'][q]) - RL['okey_stores'], 'okey': RL['okey_stores'],
                  'super': int(stores_q['super'][q]), 'conv': int(stores_q['conv'][q] + stores_q['other'][q]),
                  'droge': int(stores_q['droge'][q]), 'remi': int(stores_q['remi'][q]), 'diy': int(stores_q['diy'][q]),
                  'wholesale': None}
    assert abs(sum(v for v in area_end.values() if v) - area_q['total'][q]) < 1e-6
    assert sum(v for v in stores_end.values() if v) == stores_q['total'][q]
    for sname, s in segs.items():
        s['area_end'] = area_end[sname]
        s['stores_end'] = stores_end[sname]

    # проформа группы 1П2026 = сумма сегментов = отчёт + проформа «О’КЕЙ» − вклад с даты (D3, рецензия Б1)
    pf_26h1 = sum(s['revenue']['2026H1'] for s in segs.values())
    pf_check = rev_h['total']['2026H1'] + IF['okey_full_2026H1'] - okc
    assert abs(pf_26h1 - pf_check) < 1e-3, (pf_26h1, pf_check)   # итог датабука = сумма форматов до 0,1 млн руб.

    # ---------------------------------------------------------- 7. 2П/1П группы на периметре якоря (без DIY)
    ex = ('hyper', 'okey', 'super', 'conv', 'droge', 'remi', 'wholesale')
    pf25h1 = sum(segs[s]['revenue']['2025H1'] for s in ex)
    pf25h2 = sum(segs[s]['revenue']['2025H2'] for s in ex)
    ratio_pf25 = pf25h2 / pf25h1
    lo, hi = min(corr_vals), max(corr_vals)

    # ---------------------------------------------------------- 8. доли кварталов на периметре с «О’КЕЙ»
    # база — 2025 на периметре якоря: отчётные кварталы + «О’КЕЙ» (год 142 разложен по кварталам профилем
    # гипермаркетов «Ленты» 2025) + «Реми» до покупки (проформа × профиль гипер+супер+у дома) — без DIY (level)
    q25 = [f'2025Q{i}' for i in (1, 2, 3, 4)]
    hyp_prof = [rev_q['hyper'][x] for x in q25]
    core_prof = [sum(rev_q[f][x] for f in core) for x in q25]
    okey_q = [ok25 * v / sum(hyp_prof) for v in hyp_prof]
    remi_q_full = [remi25 * v / sum(core_prof) for v in core_prof]
    rep_q = [rev_q['total'][x] - rev_q['remi'][x] for x in q25]   # Реми отчётный декабрь заменяется проформой
    pf_q = [a + b + c for a, b, c in zip(rep_q, okey_q, remi_q_full)]
    share_q_pf = [v / sum(pf_q) for v in pf_q]
    share_q_hist = {}
    for y in range(2013, 2026):
        vals = [rev_q['total'].get(f'{y}Q{i}') for i in (1, 2, 3, 4)]
        if all(vals):
            share_q_hist[y] = [v / sum(vals) for v in vals]
    share_q_clean = {y: v for y, v in share_q_hist.items() if y not in breaks}
    share_q_mean_clean = [st.mean(v[i] for v in share_q_clean.values()) for i in range(4)]
    within_hist = {y: [v[0] / (v[0] + v[1]), v[1] / (v[0] + v[1]), v[2] / (v[2] + v[3]), v[3] / (v[2] + v[3])]
                   for y, v in share_q_hist.items()}
    within_clean = [within_hist[y] for y in (2016, 2017, 2018, 2019, 2022, 2024, 2025)]
    within_mean = [st.mean(w[i] for w in within_clean) for i in range(4)]
    within_sd = [st.pstdev(w[i] for w in within_clean) for i in range(4)]
    # полугодовые доли внутри полугодия (для ожидания квартала = выручка полугодия × доля): Q1/(Q1+Q2) и т. д.
    within = [share_q_pf[0] / (share_q_pf[0] + share_q_pf[1]), share_q_pf[1] / (share_q_pf[0] + share_q_pf[1]),
              share_q_pf[2] / (share_q_pf[2] + share_q_pf[3]), share_q_pf[3] / (share_q_pf[2] + share_q_pf[3])]

    out = dict(
        meta=dict(databook_sha256=L.SHA[L.DATABOOK], primary_dir='LENTA_PRIMARY_DIR', units='млрд руб.; тыс. м²; '
                  'плотность — тыс. руб./м² в год (выручка полугодия × 2 / средняя площадь трапецией)'),
        ifrs=IF, releases={k: v for k, v in RL.items() if not k.startswith('_')},
        deals=deals, closures_q=closures_q,
        history=dict(
            revenue_q={f: {q: round(v, 4) for q, v in rev_q[f].items() if q >= '2019Q1'} for f in FORMATS},
            revenue_h={f: {p: round(v, 4) for p, v in rev_h[f].items() if p >= '2012H1'} for f in FORMATS},
            area_q={f: {q: round(v, 3) for q, v in area_q[f].items() if q >= '2019Q1'} for f in AREA_FORMATS},
            stores_q={f: {q: v for q, v in stores_q[f].items() if q >= '2019Q1'} for f in AREA_FORMATS},
            net_open_q={f: {q: v for q, v in netop_q[f].items() if q >= '2021Q1'} for f in AREA_FORMATS},
            new_area_q={f: {q: round(v, 3) for q, v in newar_q[f].items() if q >= '2021Q1'} for f in AREA_FORMATS},
            lfl_q=lfl_q, lfl_h=lfl_h, online_q=online_q, organic_h=organic,
            density_h={f: {p: round(v, 1) for p, v in d.items()} for f, d in density_h.items()},
            density_fy={f: {y: round(v, 1) for y, v in d.items()} for f, d in density_fy.items()},
        ),
        seasonality=dict(ratio_h2h1={f: {y: round(v, 4) for y, v in d.items()} for f, d in ratio_h2h1.items()},
                         breaks=breaks, corridor_years=corridor_years,
                         corridor=[lo, hi], corridor_mean=st.mean(corr_vals),
                         hyper_ratio_range=[min(hyper_vals), max(hyper_vals)],
                         pro_forma_2025=dict(h1=pf25h1, h2=pf25h2, ratio=ratio_pf25,
                                             in_corridor=lo - 0.01 <= ratio_pf25 <= hi + 0.01)),
        estimation=dict(
            okey=dict(rule='2025: 142 × доля полугодия гипермаркетов «Ленты» 2025 (2П = %.4f); 1П2026 — проформа МСФО'
                      % s2_hyper25, s2_hyper25=s2_hyper25, s2_hyper_hist=hyper_s2_hist, s2_sd=s2_sd,
                      yoy_2026H1=okey_yoy_2026H1, intra_2026H1=okey_intra),
            remi=dict(rule='2025: проформа 55,488 × доля полугодия гипер+супер+у дома 2025 (2П = %.4f)' % s2_core25,
                      s2_core25=s2_core25, s2_formats=s2_formats, dec_ratio_remi=remi_dec_ratio,
                      dec_ratio_group=grp_dec_ratio, yoy_2026H1=remi_yoy_2026H1),
        ),
        segments=segs,
        pro_forma_2026H1=dict(sum_segments=pf_26h1, reported=rev_h['total']['2026H1'], check=pf_check),
        quarter_share=dict(pro_forma_2025=share_q_pf, within_half=within, within_hist=within_hist,
                           within_mean_clean=within_mean, within_sd_clean=within_sd, hist=share_q_hist,
                           hist_mean_clean=share_q_mean_clean, okey_q=okey_q, remi_q=remi_q_full),
    )
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=float)
    report(out, rev_h, area_q, stores_q, lfl_h, density_fy)


def report(o, rev_h, area_q, stores_q, lfl_h, density_fy):
    F = L.fmt
    print('== Сегменты на периметре 30.06.2026 (млрд руб.; тыс. м²)')
    print(f'{"сегмент":10s} {"2025H1":>9s} {"2025H2":>9s} {"2026H1":>9s}  базис 25H1/25H2/26H1       se25H2  площадь  магазины')
    for k, s in o['segments'].items():
        r, b, se = s['revenue'], s['basis'], s['se']
        print(f'{k:10s} {F(r["2025H1"],2):>9s} {F(r["2025H2"],2):>9s} {F(r["2026H1"],2):>9s}  '
              f'{str(b["2025H1"])[:9]:9s}/{str(b["2025H2"])[:9]:9s}/{str(b["2026H1"])[:9]:9s} {F(se["2025H2"],2):>6s} '
              f'{F(s["area_end"],1):>8s} {str(s["stores_end"] if s["stores_end"] is not None else "—"):>8s}')
    pf = o['pro_forma_2026H1']
    print(f'Проформа 1П2026 = {F(pf["sum_segments"],3)} (отчёт {F(pf["reported"],3)} + проформа О’КЕЙ − вклад с даты = '
          f'{F(pf["check"],3)})')
    s = o['seasonality']
    print(f'2П/1П группы: коридор {s["corridor_years"]} = {F(s["corridor"][0],3)}–{F(s["corridor"][1],3)}, '
          f'среднее {F(s["corridor_mean"],3)}; проформа 2025 на периметре якоря (без DIY) = '
          f'{F(s["pro_forma_2025"]["ratio"],4)} → в коридоре: {s["pro_forma_2025"]["in_corridor"]}')
    print('2П/1П по годам (группа | гипер):', ' '.join(f'{y}:{F(v,3)}|{F(s["ratio_h2h1"]["hyper"].get(y),3)}'
                                               for y, v in s['ratio_h2h1']['total'].items() if y >= 2013))
    e = o['estimation']
    print(f'О’КЕЙ: {e["okey"]["rule"]}; г/г 1П2026 к оценке 1П2025 = {e["okey"]["yoy_2026H1"]:+.2%}; '
          f'σ доли 2П гипер = {e["okey"]["s2_sd"]:.4f}')
    oi = e['okey']['intra_2026H1']
    print(f'О’КЕЙ внутри 1П2026: янв.–май {oi["jan_may_2026"]:.2f} г/г {oi["yoy_jan_may"]:+.1%}; июнь (29 дн.) '
          f'{oi["june_2026_29d"]:.2f} г/г по дням {oi["yoy_june_per_day"]:+.1%}')
    print(f'Реми: {e["remi"]["rule"]}; г/г 1П2026 = {e["remi"]["yoy_2026H1"]:+.2%}; декабрь/средний месяц: Реми '
          f'{e["remi"]["dec_ratio_remi"]:.2f}, группа {e["remi"]["dec_ratio_group"]:.2f}')
    d = o['deals']
    print('Сделки:', json.dumps({k: {kk: vv for kk, vv in v.items() if kk in ('stores', 'area_th', 'stores_databook',
          'stores_ifrs', 'area_th_databook', 'area_th_ifrs')} for k, v in d.items()}, ensure_ascii=False, default=float))
    print('Закрытия (валовые − чистые):', o['closures_q'])
    print('Плотность, тыс. руб./м² в год:', {f: v for f, v in density_fy.items()})
    qs = o['quarter_share']
    print('Доли кварталов, периметр с О’КЕЙ (2025):', [round(x, 4) for x in qs['pro_forma_2025']],
          ' внутри полугодий:', [round(x, 4) for x in qs['within_half']],
          ' среднее чистых лет:', [round(x, 4) for x in qs['hist_mean_clean']],
          ' внутри полугодий, чистые годы: среднее', [round(x, 4) for x in qs['within_mean_clean']],
          'σ', [round(x, 4) for x in qs['within_sd_clean']])
    print('Органика (чистые магазины / тыс. м²) по полугодиям:')
    for f, dd in o['history']['organic_h'].items():
        print(' ', f, {p: (int(v['net_stores']), round(v['net_area_th'], 1)) for p, v in dd.items() if p >= '2024H1'})


if __name__ == '__main__':
    main()
