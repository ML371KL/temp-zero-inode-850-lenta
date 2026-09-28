# -*- coding: utf-8 -*-
"""Сборка фрагмента машинной книги `book-draft/fragments/network.yaml` из выводов листа (части 1–3) и суждений
листа (константы ниже, у каждой — основание в комментарии). Числа фрагмента = числа скриптов (одно число на
допущение). После записи файл читается `yaml.safe_load` — фрагмент обязан быть валидным YAML.

  python -B make_fragment.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import netlib as L  # noqa: E402

NF = json.load(open(os.path.join(L.HERE, 'network_facts_out.json'), encoding='utf-8'))
LT = json.load(open(os.path.join(L.HERE, 'lfl_ticket_out.json'), encoding='utf-8'))
SP = json.load(open(os.path.join(L.HERE, 'space_paths_out.json'), encoding='utf-8'))
FRAG = os.path.normpath(os.path.join(L.HERE, '..', '..', '..', 'fragments', 'network.yaml'))

# ------------------------------------------------------------------ суждения листа (README §3–§6)
K = {'bear': 0.50, 'base': 0.55, 'bull': 0.60}
SHIFT = {'bear': {'2026H2': 0.010, '2027': 0.005, 'LT': 0.005},
         'base': {'2026H2': 0.020, '2027': 0.015, 'LT': 0.015},
         'bull': {'2026H2': 0.025, '2027': 0.020, 'LT': 0.020}}
TRAFFIC = {'bear': {'2026H2': -0.005, '2027': -0.010, '2030': -0.010, 'LT': -0.008},
           'base': {'2026H2': 0.005, '2027': 0.0, '2030': -0.003, 'LT': -0.003},
           'bull': {'2026H2': 0.010, '2027': 0.005, 'LT': 0.002}}
VAT_2026H2 = round(-K['base'] * LT['vat']['vat_in_food_cpi_jan'], 4)
DENSITY_DIY = {'2026H2': 80.0, '2027H1': 90.0, '2027H2': 95.0, '2028': 110.0, 'LT': 120.0}
WHOLESALE_GROWTH = {'2026H2': -0.05, '2027': 0.0, 'LT': 0.0}
NAMES = {'hyper': 'Гипермаркеты «Ленты»', 'okey': 'Гипермаркеты «О’КЕЙ» (75, с 02.06.2026)',
         'super': 'Супермаркеты «Супер Лента»', 'conv': 'Магазины у дома: «Монетка», «Мини Лента», «Вингараж»',
         'droge': 'Дрогери «Улыбка радуги»', 'remi': '«Реми» (Дальний Восток, 67 %)',
         'diy': '«Дом Лента» (DIY, бывш. OBI)', 'wholesale': 'Опт и прочая выручка без площади'}
MODES = {'hyper': 'yoy', 'okey': 'yoy', 'super': 'yoy', 'conv': 'yoy', 'droge': 'yoy', 'remi': 'yoy', 'diy': 'level',
         'wholesale': 'revenue'}


def fl(d):
    """Словарь → YAML flow с ключами периодов в кавычках."""
    if isinstance(d, (int, float)):
        return repr(round(d, 6))
    items = []
    for k, v in d.items():
        kk = k if k == 'LT' else f'"{k}"'
        if isinstance(v, dict):
            items.append(f'{kk}: {fl(v)}')
        elif v is None:
            items.append(f'{kk}: null')
        elif isinstance(v, str):
            items.append(f'{kk}: {v}')
        else:
            items.append(f'{kk}: {round(v, 6)!r}')
    return '{' + ', '.join(items) + '}'


def r3(x):
    return None if x is None else round(x, 3)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    S = NF['segments']
    E = SP['eff_area']
    NSD = SP['nsd_book']
    CP = SP['cp_seg']
    SPACE = SP['space']
    OFF = SP['lfl_offset']
    qs = NF['quarter_share']['within_half']
    reg = LT['regressions']
    est = NF['estimation']
    L_ = []
    w = L_.append
    w('# Фрагмент машинной книги Ленты 1.0 — тема «Сеть и выручка» (раздел 03). Сгенерирован make_fragment.py.')
    w('# Лист: book-draft/evidence/book-1.0/network/ (README, VERIFY, скрипты). Единицы: млрд руб.; тыс. м²; доли.')
    w('# Пути ключей — DESIGN §4; ключи, которых там нет, помечены «ПРЕДЛОЖЕНИЕ К СХЕМЕ» (README, возражения).')
    w('')
    w('facts:')
    w('  segments:')
    for seg in ('hyper', 'okey', 'super', 'conv', 'droge', 'remi', 'diy', 'wholesale'):
        s = S[seg]
        rev = {p: r3(v) for p, v in s['revenue'].items()}
        basis = {p: (v if v else 'null') for p, v in s['basis'].items()}
        se = {p: (None if v is None else round(v, 2)) for p, v in s['se'].items()}
        w(f'    {seg}:')
        note = {'hyper': 'датабук, строка Hypermarket; 1П2026 − вклад «О’КЕЙ» с 02.06 по МСФО 6М2026 прим. 5 (9,360)',
                'okey': f'1П2026 — проформа МСФО 6М2026 прим. 5 (66,121, всё полугодие); 2025 — расчёт: 142 (релиз '
                        f'02.06.2026) × доля полугодия гипер «Ленты» 2025 (2П {L.fmt(est["okey"]["s2_hyper25"], 4)})',
                'super': 'датабук, Supermarkets; «Молния» (29 супер) в периметре с 24.06.2025',
                'conv': 'датабук, Convenience stores + Other formats («Вингараж»)',
                'droge': 'датабук, Drogerie', 'remi': f'1П2026 — датабук, Remi (полное полугодие); 2025 — расчёт: '
                         f'проформа года 55,488 (МСФО 2025 прим. 8) × доля полугодия гипер+супер+у дома 2025 '
                         f'(2П {L.fmt(est["remi"]["s2_core25"], 4)})',
                'diy': 'датабук, Dom Lenta; режим level — базы 2025 не нужны (первички по OBI Россия нет)',
                'wholesale': 'датабук, Wholesales'}[seg]
        w(f'      revenue: {fl(rev)}   # {note}')
        w(f'      revenue_basis: {fl(basis)}')
        w(f'      revenue_se: {fl(se)}   # ПРЕДЛОЖЕНИЕ К СХЕМЕ: se базы, млрд; оценки — правило листа, README §2')
        if seg == 'wholesale':
            w('      area_end: null        # площади нет (режим revenue)')
            w('      stores_end: null')
            continue
        ae = s['area_end']
        area_note = {'hyper': 'датабук 1 850,0 − «О’КЕЙ» (июньский прирост гипер 488,0 − органика июня ≈3,25: «Эконом» '
                              '2,5–4,0 тыс. м²)',
                     'okey': f'датабук: прирост гипер в июне 2026 − органика (±0,75); МСФО/релиз — 478 тыс. м² '
                             f'(датабук на ≈1,4 % больше, как у «Дом Ленты» 269,6 против 263)',
                     'conv': 'Convenience 1 124,1 + Other formats 12,6', 'super': 'датабук', 'droge': 'датабук',
                     'remi': 'датабук', 'diy': 'датабук (26 магазинов, 270,6)'}[seg]
        w(f'      area_end: {ae:.3f}   # тыс. м² на 30.06.2026: {area_note}')
        st_note = {'hyper': 'датабук 346 − 75 «О’КЕЙ» (МСФО); по месячному листу выходит 272 (74 «О’КЕЙ» при 1 '
                            'открытии в июне) — расхождение в 1 магазин, README §7',
                   'okey': 'МСФО 6М2026 прим. 5, релиз 02.06.2026'}.get(seg, 'датабук')
        w(f'      stores_end: {s["stores_end"]}   # {st_note}')
        if seg in ('hyper', 'super', 'conv', 'droge', 'okey', 'remi'):
            e = E[seg]
            w(f'      new_area_gross_hist: {e["new_area_gross_hist"]}   # валовые открытия, тыс. м²: 2П24, 1П25, 2П25, '
              f'1П26 (органика = изменение сети − периметр сделок + закрытия; расчёт, space_paths.py)')
            if seg in ('hyper', 'super', 'conv', 'droge'):
                w(f'      new_area_dense_cohorts: 2   # ключ 850oa facts.new_area_dense_cohorts по сегменту: две '
                  f'младшие когорты дозревают до d (калибровка на истории — README §4)')
            w(f'      eff_area_avg_hist: {fl(e["eff_area_avg_hist"])}   # правило ядра (непрерывный индекс, '
              f'сведённый к уровню на якоре; когорты — d сегмента, закрытия — cp сегмента)')
        else:
            w(f'      eff_area_avg_hist: {fl(E["diy"]["eff_area_avg_hist"])}   # 1П2026 — средняя физическая площадь '
              f'по месячным концам (покупка янв.–март)')
    w('')
    w('revenue:')
    w(f'  maturity_curve: {SP["maturity_curve"]}   # A-R5 (общая группы): доля зрелой плотности по возрасту когорты '
      f'(полугодия); LFL «Ленты» — с 13-го полного месяца; калибровка d по кривым 0,60/0,72/0,80 даёт ±0,01 — '
      f'кривая не идентифицируется, берётся 850oa')
    km = reg['k_mix']
    w(f'  ticket_k: {fl(K)}   # A-R1: LFL-чек = k × прод. ИПЦ + s; МНК «Ленты» 2016–2к26 без 2019–2021: '
      f'{L.fmt(reg["group_food_2016"]["k"], 2)} (se {L.fmt(reg["group_food_2016"]["se_k"], 2)}); с весами форматов 1П26 '
      f'{L.fmt(km["k_mix"], 2)} (у дома {L.fmt(reg["conv_food"]["k"], 2)} по 10 кварталам); пул 850oa 0,60–0,64; '
      f'диапазон 0,40–0,70')
    w('  ticket_shift:   # A-R2: s — режим потребления и сдвиг корзины; история при k 0,55: 2016–19 ≈0, 2П23–1П26 '
      '+2,6…+5,1, 1П26 +2,7 (Q2 ≈+2,1); LT base 1,5 даёт реальный LFL ≈ −0,4 %/год (как 850oa); диапазон LT −0,5…+2,5')
    for dmd in ('bear', 'base', 'bull'):
        w(f'    {dmd}: {fl(SHIFT[dmd])}')
    w(f'  vat_adjustment: {fl({"2026H2": VAT_2026H2, "2027": 0.0, "LT": 0.0})}   # A-R3: LFL «Ленты» — без НДС '
      f'(релизы 2019); вклад НДС 22 % в прод. ИПЦ г/г 2026 ≈{L.fmt(LT["vat"]["vat_in_food_cpi_jan"]*100, 2)} п.п. (январь м/м '
      f'против 2023–25) при полном переносе в полку нетто-чек не растёт → −k × вклад; диапазон −0,008…−0,002')
    w('  traffic:   # A-R4: LFL-трафик группы; история: 2016–19 −0,2 %, 2022–25 +1,1 %, 1П26 +1,5 % (Q2 +2,8 на скидках); '
      'LT base −0,3; диапазон LT −1,0…+0,5')
    for dmd in ('bear', 'base', 'bull'):
        w(f'    {dmd}: {fl(TRAFFIC[dmd])}')
    w('  ticket_lt_homogeneity: {enabled: true, reference_world: N, ramp_from: 2027, ramp_to: 2031}   # A-R1h — '
      'правило 850oa без изменений')
    w(f'  quarter_share: {fl({"Q1": round(qs[0], 3), "Q2": round(qs[1], 3), "Q3": round(qs[2], 3), "Q4": round(qs[3], 3)})}'
      f'   # доля квартала ВНУТРИ полугодия на периметре с «О’КЕЙ» (2025: отчёт + «О’КЕЙ» и «Реми» по профилю); '
      f'чистые годы 0,487/0,513/0,453/0,547, σ 0,010/0,006')
    w('  segments:')
    for seg in ('hyper', 'okey', 'super', 'conv', 'droge', 'remi', 'diy', 'wholesale'):
        w(f'    {seg}:')
        w(f'      name: "{NAMES[seg]}"')
        w(f'      mode: {MODES[seg]}')
        if seg == 'wholesale':
            w(f'      growth: {fl(WHOLESALE_GROWTH)}   # A-R8: прибавка к LFL группы (не номинальный рост); 1П26 −1,9 % '
              f'г/г при LFL +6,7 %; 2025 +26 %; в g терминала не входит; диапазон 2026H2 −0,10…+0,05')
            continue
        d_note = {'conv': 'калибровка правилом ядра на 2025H1–2026H1: 0,76 (0,75–0,78 по cp и кривым); диапазон 0,65–0,90',
                  'super': 'калибровка 0,95 (0,84–1,05; шум LFL-периметра реконцепции); диапазон 0,75–1,05',
                  'droge': 'калибровка 0,64 по одной паре (0,58–0,70); суждение 0,70; диапазон 0,55–0,85',
                  'hyper': 'XS/«Эконом»: 5 «Эконом» и 3+ млрд выручки 2025 (стратегия, с. 27) ≈0,5–0,7 средней; '
                           'диапазон 0,5–0,9',
                  'okey': 'открытий до 2031 нет; как 850oa', 'remi': 'как 850oa; диапазон 0,7–1,0',
                  'diy': 'формат 1,1 тыс. м² внутри гипермаркета (релиз 29.04.2026); суждение 0,6–1,0'}[seg]
        w(f'      new_space_density: {NSD[seg]}   # A-R5: {d_note}')
        cp_note = {'hyper': 'история: снятие площади 2П24–1П26 (−51 тыс. м²) почти без потери выручки (калибровка cp ≈ 0); '
                            'закрытия магазинов ≈0,6 → смесь 0,5; диапазон 0,2–0,7',
                   'okey': 'закрываются слабейшие магазины сети; диапазон 0,4–0,8'}.get(seg, 'как 850oa (A-R6); диапазон 0,4–0,8')
        w(f'      closed_productivity: {CP[seg]}   # A-R6: {cp_note}')
        if seg != 'diy':
            off = OFF[seg]
            if seg == 'okey':
                od = SP['okey_density']
                w(f'      lfl_offset:   # A-R10: сближение плотности с «Гипер Лентой» по РЕЖИМУ маржи = сдвиг гипер + член '
                  f'сближения; отношение плотностей (эфф. площадь): 2025H2 {L.fmt(od["ratio_2025H2"], 3)}, 1П26 '
                  f'{L.fmt(od["ratio_2026H1"], 3)}; 2026H2 — провал перехода (июнь −15,6 % г/г по дням, янв.–май +3,0 %); '
                  f'цель 2029H2: stress 0,62 / floor 0,72 / partial 0,83 / full 0,95')
                for rg in ('stress', 'floor', 'partial', 'full'):
                    w(f'        {rg}: {fl(off[rg])}')
            else:
                o_note = {'hyper': 'история к группе: 2025 −0,8, 1П26 −0,2 п.п.; канал гипермаркетов сжимается, '
                                   'выигрыш от ухода конкурентов исчерпан с покупкой «О’КЕЙ»; диапазон LT −2,0…0',
                          'super': '2025 +10, 1П26 +7,1 п.п. (перезапуск формата); затухание к +0,5; диапазон LT 0…+1,5',
                          'conv': '2025 −1,7, 1П26 −1,5 п.п.; трафик «Монетки» вернулся в плюс во 2 кв. 2026; LT +0,5 — '
                                  'переток из гипермаркетов; диапазон LT −0,5…+1,0',
                          'droge': '2025 −3,7, 1П26 −3,9 п.п.; диапазон LT −1,5…+1,0',
                          'remi': 'вне LFL до 12.2026; г/г 1П26 к оценке 1П25 +6,6 % ≈ LFL группы; диапазон ±2 п.п.'}[seg]
                w(f'      lfl_offset: {fl(off)}   # A-R9: {o_note}; взвешенный по выручке сдвиг ≈0 (checks.py)')
        w('      space:   # A-R7: доли площади сегмента в год (за полугодие — половина); N → high, H → mid, M → low, '
          'стресс → low')
        for sc in ('low', 'mid', 'high'):
            v = SPACE[seg][sc]
            w(f'        {sc}: {{gross_open: {fl(v["gross_open"])}, close: {fl(v["close"])}}}')
        if seg == 'diy':
            w(f'      density_path: {fl(DENSITY_DIY)}   # A-R11: тыс. руб./м² в год, цены 2026H1 × индекс ИПЦ мира; '
              f'факт 2 кв. 2026 ≈87 (апр. 72, май 103, июнь 87 — сезон дачи), 1П26 78 на средней площади; '
              f'LT 120 — суждение, диапазон 80–160')
    w('')
    w('# ПРЕДЛОЖЕНИЕ для блока joint (ведущему): привязка площади к миру и режиму — конструкция 850oa (A-P4, A-P4g)')
    w('joint:')
    w('  growth_by_regime_override: {stress: low}')
    w('  by_world_growth: {N: high, H: mid, M: low}   # в книге — поле growth внутри joint.by_world.<мир>')
    w('  demand_by_regime: {stress: bear, floor: base, partial: base, full: bull}   # копия 850oa')
    text = '\n'.join(L_) + '\n'
    os.makedirs(os.path.dirname(FRAG), exist_ok=True)
    with open(FRAG, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    import yaml
    y = yaml.safe_load(open(FRAG, encoding='utf-8'))
    assert set(y['revenue']['segments']) == set(y['facts']['segments'])
    assert abs(sum(v for v in y['revenue']['quarter_share'].values()) - 2.0) < 0.002
    print('записан', FRAG, len(text), 'байт; YAML валиден')


if __name__ == '__main__':
    main()
