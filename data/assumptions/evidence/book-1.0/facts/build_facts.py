"""Лист фактов книги 1.0 «Ленты»: сборка facts/*.json из первички.

Запуск (Python 3.12):
    set PYTHONIOENCODING=utf-8
    python build_facts.py            # пишет book-draft/facts/*.json и out/build_log.txt

Вход — только файлы из LENTA_PRIMARY_DIR (датабук, МСФО, ISS, пресс-релизы) и, для
цены книги и аналогов, сырые ответы ISS в LENTA_RESEARCH_DIR/_work05. Каждое число
несёт ссылку: ячейку датабука («PL!AE10») или документ+страницу PDF; числа из PDF
проверяются по текстовому слою (число обязано стоять на названной странице), иначе
сборка падает. Суждения и расчёты помечены полем kind: fact | calc | judgement.
"""
from __future__ import annotations

import csv
import json
import sys
from collections import OrderedDict

import openpyxl

from lib_primary import (HERE, Sheet, dump_json, facts_out_dir, find_number_pages, magnit_dir,
                         manifest, pdf_page_has_text, pdf_page_numbers, pdf_pages, primary_dir, r6,
                         research_dir, sha256_file, text_file, numbers_in)

LOG: list[str] = []


def log(msg: str):
    LOG.append(msg)
    print(msg)


# =========================================================================== документы
DOCS = OrderedDict([
    ("DATABOOK_Q2_2026", ("Lenta_Q22026_DATABOOK.xlsx", "Датабук компании за 2 кв. 2026 (IAS 17 и МСФО 16, полугодия 2010–1П2026, кварталы 1кв2020–2кв2026, долг, операционные данные); опубликован 03.08.2026")),
    ("IFRS_1H2026", ("ifrs_mkpao/ifrs_1H2026.pdf", "Обобщённая промежуточная сокращённая КФО МСФО за 6М2026, обзор ООО «Б1 – Аудит», утверждена СД 31.07.2026")),
    ("IFRS_FY2025", ("ifrs_mkpao/ifrs_FY2025.pdf", "Обобщённая годовая КФО МСФО за 2025, аудит")),
    ("IFRS_1H2025", ("ifrs_mkpao/ifrs_1H2025.pdf", "Обобщённая промежуточная КФО МСФО за 6М2025")),
    ("IFRS_FY2024", ("ifrs_mkpao/ifrs_FY2024.pdf", "Обобщённая годовая КФО МСФО за 2024, аудит")),
    ("IFRS_FY2023_V2", ("ifrs_mkpao/ifrs_FY2023_v2_20240903.pdf", "Обобщённая годовая КФО МСФО за 2023, редакция 03.09.2024 (с раскрытием приобретений)")),
    ("IFRS_FY2021", ("ifrs_mkpao/ifrs_FY2021.pdf", "Годовая КФО МСФО за 2021 (EN), аудит")),
    ("ISS_LENT_SEC", ("moex_iss_lent_corp/iss_securities_LENT_2026-09-28.json", "MOEX ISS: карточка бумаги LENT (ISSUESIZE, LISTLEVEL) на 28.09.2026")),
    ("AGM2026_RESULTS", ("lenta_agm2026_results_ru.pdf", "Отчёт об итогах голосования ГЗОСА 25.06.2026 (голосующие акции 111 104 354 2/5)")),
    ("ISSUER_REPORT_6M2026_EXTRACT", ("corp_docs/issuer_report_MKPAO_6M2026_sec1_7_extracted.txt", "Отчёт эмитента МКПАО за 6М2026, п. 1.7 (кредиторы, обеспечение) — извлечённый текст из RTF")),
    ("PR_OKEY_2026_06_02", ("lentagroup_news/2026-06-02_gruppa-lenta-priobretaet-gipermarkety-o-key.txt", "Пресс-релиз «Группа Лента приобретает гипермаркеты «О’КЕЙ»» 02.06.2026 (выручка «О’КЕЙ» 2025 — 142 млрд; гайденс 2026)")),
    ("OKEY_B1P6", ("moex_iss_okey_bonds/RU000A1084E6_description.json", "MOEX ISS: облигация ООО «О’КЕЙ» БО 001Р-06")),
    ("OKEY_B1P8", ("moex_iss_okey_bonds/RU000A10CCN3_description.json", "MOEX ISS: облигация ООО «О’КЕЙ» БО 001Р-08")),
    ("OKEY_B1P2", ("moex_iss_okey_bonds/RU000A1009Z8_description.json", "MOEX ISS: облигация ООО «О’КЕЙ» БО 001Р-02")),
    ("OKEY_B1P4", ("moex_iss_okey_bonds/RU000A102BK7_description.json", "MOEX ISS: облигация ООО «О’КЕЙ» БО 001Р-04")),
    ("SF_2022_06_21", ("corp_filings_mkpao/2022-06-21_sf21071.pdf", "Сущфакт 21.06.2022: размещение 18 399 265 акций по 1 087 ₽ в оплату «Утконоса» (20 млрд)")),
    ("SF_2021_12_16", ("corp_filings_mkpao/2021-12-16_sf20647.pdf", "Сущфакт 16.12.2021: сделка «Утконос», база цены 20 млрд ₽")),
])


def doc_path(doc_id: str) -> str:
    return DOCS[doc_id][0]


# =========================================================================== транскрипции из PDF/текста
# (id, документ, страница, значение в тыс. ₽ или штуках, подпись). Сборка проверяет, что число
# стоит на странице (по текстовому слою). Значения — как в документе (знак — по смыслу строки).
NOTE_FACTS = [
    # --- МСФО 6М2026: баланс (с. 6)
    ("bs_cash_2026h1", "IFRS_1H2026", 6, 25172516, "Денежные средства и эквиваленты 30.06.2026"),
    ("bs_cash_2025fy", "IFRS_1H2026", 6, 51063533, "Денежные средства и эквиваленты 31.12.2025"),
    ("bs_loans_issued_2026h1", "IFRS_1H2026", 6, 3339890, "Краткосрочные займы выданные 30.06.2026 (прим. 3, 5)"),
    ("bs_loans_issued_2025fy", "IFRS_1H2026", 6, 11533698, "Краткосрочные займы выданные 31.12.2025"),
    ("bs_other_ca_2026h1", "IFRS_1H2026", 6, 2471493, "Прочие оборотные активы 30.06.2026 (прим. 17)"),
    ("bs_other_ca_2025fy", "IFRS_1H2026", 6, 10662493, "Прочие оборотные активы 31.12.2025"),
    ("bs_lt_debt_2026h1", "IFRS_1H2026", 6, 100030371, "Долгосрочные кредиты и займы 30.06.2026"),
    ("bs_st_debt_2026h1", "IFRS_1H2026", 6, 42553313, "Краткосрочные кредиты 30.06.2026"),
    ("bs_lt_debt_2025fy", "IFRS_1H2026", 6, 44458630, "Долгосрочные кредиты и займы 31.12.2025"),
    ("bs_st_debt_2025fy", "IFRS_1H2026", 6, 55440666, "Краткосрочные кредиты 31.12.2025"),
    ("bs_lt_lease_2026h1", "IFRS_1H2026", 6, 140768777, "Долгосрочные обязательства по аренде 30.06.2026"),
    ("bs_st_lease_2026h1", "IFRS_1H2026", 6, 18497093, "Краткосрочные обязательства по аренде 30.06.2026"),
    ("bs_other_ltl_2026h1", "IFRS_1H2026", 6, 2000523, "Прочие долгосрочные обязательства 30.06.2026 (прим. 22)"),
    ("bs_other_ltl_2025fy", "IFRS_1H2026", 6, 6531216, "Прочие долгосрочные обязательства 31.12.2025"),
    ("bs_payables_2026h1", "IFRS_1H2026", 6, 157281140, "Торговая и прочая кредиторская задолженность 30.06.2026"),
    ("bs_payables_2025fy", "IFRS_1H2026", 6, 167582393, "Торговая и прочая кредиторская задолженность 31.12.2025"),
    ("bs_nci_2026h1", "IFRS_1H2026", 6, 991707, "Неконтролирующая доля участия 30.06.2026"),
    ("bs_nci_2025fy", "IFRS_1H2026", 6, 935604, "Неконтролирующая доля участия 31.12.2025"),
    ("bs_other_reserves", "IFRS_1H2026", 6, 4766102, "Прочие капитальные резервы (−) 30.06.2026 = 31.12.2025 (пут «Реми», прим. 5)"),
    ("bs_treasury_cost", "IFRS_1H2026", 6, 1011190, "Собственные акции, выкупленные у акционеров (−), тыс. ₽"),
    ("bs_equity_2026h1", "IFRS_1H2026", 6, 190720331, "Итого капитал 30.06.2026"),
    ("bs_goodwill_2026h1", "IFRS_1H2026", 6, 86388333, "Гудвил 30.06.2026"),
    ("bs_goodwill_2025fy", "IFRS_1H2026", 6, 63935312, "Гудвил 31.12.2025"),
    ("bs_inventories_2026h1", "IFRS_1H2026", 6, 126780945, "Запасы 30.06.2026"),
    ("bs_receivables_2026h1", "IFRS_1H2026", 6, 10320977, "Торговая и прочая дебиторская задолженность 30.06.2026"),
    ("bs_ppe_2026h1", "IFRS_1H2026", 6, 237069689, "Основные средства 30.06.2026"),
    # --- МСФО 6М2026: ОПУ, ОДДС (с. 7–8)
    ("pl_revenue_2026h1", "IFRS_1H2026", 7, 648488059, "Выручка 6М2026"),
    ("pl_nci_profit_2026h1", "IFRS_1H2026", 7, 56103, "Прибыль, приходящаяся на НДУ, 6М2026"),
    ("pl_profit_parent_2026h1", "IFRS_1H2026", 7, 10612827, "Прибыль акционеров материнской компании 6М2026"),
    ("cf_acq_2026h1", "IFRS_1H2026", 8, 7797452, "Приобретение дочерних организаций (−) 6М2026"),
    ("cf_loan_given_2026h1", "IFRS_1H2026", 8, 8419675, "Предоставление краткосрочного займа (−) 6М2026"),
    ("cf_capex_ppe_2026h1", "IFRS_1H2026", 8, 18420039, "Приобретение основных средств (−) 6М2026"),
    ("cf_capex_ia_2026h1", "IFRS_1H2026", 8, 3823825, "Приобретение нематериальных активов (−) 6М2026"),
    ("cf_lease_principal_2026h1", "IFRS_1H2026", 8, 7758212, "Выплаты основной суммы обязательств по аренде (−) 6М2026"),
    ("cf_borrow_in_2026h1", "IFRS_1H2026", 8, 67513167, "Поступления кредитов 6М2026"),
    ("cf_borrow_out_2026h1", "IFRS_1H2026", 8, 67396654, "Погашения кредитов и облигаций (−) 6М2026"),
    # --- прим. 3 (с. 14): связанные стороны
    ("rp_assignment_2026h1", "IFRS_1H2026", 14, 11875152, "Переуступка задолженности и процентов по займу выданному (компании под общим контролем), 6М2026"),
    ("rp_interest_2026h1", "IFRS_1H2026", 14, 341454, "Процентный доход по займам компаниям под общим контролем, 6М2026"),
    ("rp_parent_loan", "IFRS_1H2026", 14, 30500000, "Долгосрочный заём от материнской компании, 30.06.2026"),
    ("rp_parent_loan_interest", "IFRS_1H2026", 14, 262932, "Долгосрочные обязательства по процентам перед материнской компанией"),
    ("rp_kmp_lt_2026h1", "IFRS_1H2026", 14, 1377954, "Долгосрочные вознаграждения ключевому персоналу, начислено за 6М2026"),
    # --- прим. 4 (с. 16): амортизация
    ("da_ppe_2026h1", "IFRS_1H2026", 16, 13084648, "Амортизация основных средств 6М2026"),
    ("da_rou_2026h1", "IFRS_1H2026", 16, 10894378, "Амортизация активов в форме права пользования 6М2026"),
    ("da_ip_2026h1", "IFRS_1H2026", 16, 226523, "Амортизация инвестиционной недвижимости 6М2026"),
    ("da_ia_2026h1", "IFRS_1H2026", 16, 2378509, "Амортизация НМА 6М2026"),
    ("da_total_2026h1", "IFRS_1H2026", 16, 26584058, "Итого амортизация 6М2026 (МСФО 16)"),
    # --- прим. 5 (с. 16–19): приобретения
    ("okey_stores", "IFRS_1H2026", 16, 75, "«О’КЕЙ»: 75 гипермаркетов"),
    ("okey_area_k", "IFRS_1H2026", 16, 478, "«О’КЕЙ»: торговая площадь 478 тыс. кв. м"),
    ("okey_ppe", "IFRS_1H2026", 17, 30387441, "«О’КЕЙ»: основные средства по справедливой стоимости"),
    ("okey_rou", "IFRS_1H2026", 17, 21152577, "«О’КЕЙ»: активы в форме права пользования"),
    ("okey_ip", "IFRS_1H2026", 17, 661970, "«О’КЕЙ»: инвестиционная недвижимость"),
    ("okey_inventories", "IFRS_1H2026", 17, 16021510, "«О’КЕЙ»: запасы"),
    ("okey_receivables", "IFRS_1H2026", 17, 1323990, "«О’КЕЙ»: торговая и прочая дебиторская задолженность"),
    ("okey_cash", "IFRS_1H2026", 17, 798922, "«О’КЕЙ»: денежные средства"),
    ("okey_lt_lease", "IFRS_1H2026", 17, 15896655, "«О’КЕЙ»: долгосрочные обязательства по аренде (−)"),
    ("okey_lt_debt", "IFRS_1H2026", 17, 32283177, "«О’КЕЙ»: долгосрочные кредиты и займы (−)"),
    ("okey_payables", "IFRS_1H2026", 17, 12792582, "«О’КЕЙ»: торговая и прочая кредиторская задолженность (−)"),
    ("okey_st_debt", "IFRS_1H2026", 17, 18613045, "«О’КЕЙ»: краткосрочные кредиты (−)"),
    ("okey_st_lease", "IFRS_1H2026", 17, 1739228, "«О’КЕЙ»: краткосрочные обязательства по аренде (−)"),
    ("okey_dtl", "IFRS_1H2026", 17, 1106052, "«О’КЕЙ»: отложенные налоговые обязательства (−)"),
    ("okey_net_assets", "IFRS_1H2026", 17, 12197326, "«О’КЕЙ»: справедливая стоимость чистых активов (−)"),
    ("okey_goodwill", "IFRS_1H2026", 17, 13694519, "«О’КЕЙ»: гудвил"),
    ("okey_consideration", "IFRS_1H2026", 17, 1497193, "«О’КЕЙ»: возмещение, переданное при приобретении"),
    ("okey_cash_shares", "IFRS_1H2026", 17, 390, "«О’КЕЙ»: денежное вознаграждение за доли"),
    ("okey_cash_assets", "IFRS_1H2026", 17, 1496803, "«О’КЕЙ»: денежное вознаграждение за активы магазинов"),
    ("okey_net_cf", "IFRS_1H2026", 17, 698271, "«О’КЕЙ»: чистые денежные потоки при приобретении"),
    ("okey_seller_loan_payable", "IFRS_1H2026", 18, 8459326, "«О’КЕЙ»: заём прежнего владельца с процентами (зачтён)"),
    ("okey_seller_loan_offset", "IFRS_1H2026", 18, 8419675, "Заём группы продавцу, зачтённый после сделки"),
    ("okey_rev_since_acq", "IFRS_1H2026", 18, 9359828, "«О’КЕЙ»: вклад в выручку с даты приобретения (02.06–30.06.2026)"),
    ("okey_pbt_since_acq", "IFRS_1H2026", 18, 907372, "«О’КЕЙ»: убыток до налога с даты приобретения"),
    ("okey_rev_pro_forma", "IFRS_1H2026", 18, 66120725, "«О’КЕЙ»: влияние на выручку, если бы объединение было в начале 2026"),
    ("okey_pbt_pro_forma", "IFRS_1H2026", 18, 5530179, "«О’КЕЙ»: влияние на прибыль до налога (уменьшение), если бы объединение было в начале 2026"),
    ("obi_stores", "IFRS_1H2026", 18, 25, "«ОБИ Россия»: 25 магазинов"),
    ("obi_area_k", "IFRS_1H2026", 18, 263, "«ОБИ Россия»: 263 тыс. кв. м"),
    ("obi_ppe", "IFRS_1H2026", 19, 1915679, "«Дом Лента»: основные средства"),
    ("obi_rou", "IFRS_1H2026", 19, 11058089, "«Дом Лента»: активы в форме права пользования"),
    ("obi_inventories", "IFRS_1H2026", 19, 6394454, "«Дом Лента»: запасы"),
    ("obi_lt_lease", "IFRS_1H2026", 19, 9871850, "«Дом Лента»: долгосрочная аренда (−)"),
    ("obi_st_lease", "IFRS_1H2026", 19, 986429, "«Дом Лента»: краткосрочная аренда (−)"),
    ("obi_net_assets", "IFRS_1H2026", 19, 8544781, "«Дом Лента»: чистые активы"),
    ("obi_goodwill", "IFRS_1H2026", 19, 8758502, "«Дом Лента»: гудвил"),
    ("obi_consideration", "IFRS_1H2026", 19, 17303283, "«Дом Лента»: возмещение"),
    ("obi_revenue_ytd", "IFRS_1H2026", 19, 7960016, "«Дом Лента»: вклад в выручку с начала года"),
    ("obi_pbt_ytd", "IFRS_1H2026", 19, 2963756, "«Дом Лента»: убыток до налога с начала года"),
    ("obi_cash_paid", "IFRS_1H2026", 19, 4498910, "«Дом Лента»: оплачено деньгами в 6М2026"),
    ("obi_offset", "IFRS_1H2026", 19, 8734162, "«Дом Лента»: зачёт займа, выданного третьей стороне"),
    ("remi_escrow_paid", "IFRS_1H2026", 19, 2000000, "«Реми»: денежное вознаграждение (эскроу), перечислено в 6М2026"),
    ("other_acq_paid", "IFRS_1H2026", 19, 600271, "Прочие приобретения прошлых периодов, оплачено в 6М2026"),
    # --- прим. 7 (с. 21–22): аренда
    ("lease_liab_2026h1", "IFRS_1H2026", 21, 159265870, "Итого обязательства по аренде 30.06.2026"),
    ("lease_liab_2025fy", "IFRS_1H2026", 21, 119512881, "Обязательства по аренде на начало года"),
    ("lease_acquired_2026h1", "IFRS_1H2026", 21, 28494162, "Аренда: приобретение дочерних компаний"),
    ("lease_interest_2026h1", "IFRS_1H2026", 21, 11501457, "Процентные расходы по аренде 6М2026"),
    ("lease_short_term_2026h1", "IFRS_1H2026", 21, 1236939, "Краткосрочная аренда в SG&A 6М2026"),
    ("lease_variable_2026h1", "IFRS_1H2026", 21, 2034377, "Переменные арендные платежи в SG&A 6М2026"),
    ("lease_cash_out_2026h1", "IFRS_1H2026", 22, 22777519, "Общий отток денег по арендным платежам 6М2026"),
    # --- прим. 11 (с. 26): гудвил по ЕГДП
    ("gw_monetka", "IFRS_1H2026", 26, 47961239, "Гудвил «Монетка»"),
    ("gw_billa", "IFRS_1H2026", 26, 2804936, "Гудвил «Билла»"),
    ("gw_semya", "IFRS_1H2026", 26, 1033099, "Гудвил «Семья»"),
    ("gw_molniya", "IFRS_1H2026", 26, 3912758, "Гудвил «Молния – Spar»"),
    ("gw_remi", "IFRS_1H2026", 26, 8204303, "Гудвил «Реми»"),
    ("gw_domlenta", "IFRS_1H2026", 26, 8758502, "Гудвил «Дом Лента»"),
    ("gw_okey", "IFRS_1H2026", 26, 13694519, "Гудвил «Окей»"),
    ("gw_other", "IFRS_1H2026", 26, 18977, "Гудвил прочие"),
    # --- прим. 16–19 (с. 28–30)
    ("cash_deposits", "IFRS_1H2026", 28, 19457779, "Краткосрочные депозиты в рублях 30.06.2026"),
    ("cash_bank", "IFRS_1H2026", 28, 3290393, "Остатки на банковских счетах 30.06.2026"),
    ("cash_transit", "IFRS_1H2026", 28, 1310953, "Денежные средства в пути 30.06.2026"),
    ("cash_hand", "IFRS_1H2026", 28, 1113391, "Денежные средства в кассе 30.06.2026"),
    ("restricted_lc_2025fy", "IFRS_1H2026", 29, 8191000, "Ограниченные денежные средства (аккредитив «Реми») 31.12.2025; на 30.06.2026 — «–»"),
    ("indemnification_asset", "IFRS_1H2026", 29, 2471493, "Компенсирующий актив 30.06.2026 и 31.12.2025"),
    ("shares_issued", "IFRS_1H2026", 29, 115985197, "Выпущено обыкновенных акций (прим. 18)"),
    ("shares_authorised", "IFRS_1H2026", 30, 200000000, "Разрешено к выпуску"),
    ("shares_treasury", "IFRS_1H2026", 30, 910522, "Собственные акции, выкупленные у акционеров, шт."),
    ("shares_wavg", "IFRS_1H2026", 30, 115074675, "Средневзвешенное число акций в обращении (прим. 19)"),
    # --- прим. 20 (с. 31–32): кредиты
    ("d_st_fixed", "IFRS_1H2026", 31, 30655777, "Краткосрочная часть кредитов и займов по фиксированной ставке"),
    ("d_st_float", "IFRS_1H2026", 31, 5000000, "Краткосрочная часть кредитов по плавающей ставке"),
    ("d_st_bonds", "IFRS_1H2026", 31, 6500000, "Краткосрочная часть облигаций"),
    ("d_st_fixed_int", "IFRS_1H2026", 31, 63604, "Проценты к уплате по фиксированным (краткосрочная часть)"),
    ("d_lt_fixed_int_in_st", "IFRS_1H2026", 31, 135740, "Проценты по долгосрочным фиксированным (в краткосрочных)"),
    ("d_st_float_int", "IFRS_1H2026", 31, 141621, "Проценты по плавающим"),
    ("d_st_bonds_int", "IFRS_1H2026", 31, 56571, "Проценты по облигациям"),
    ("d_lt_fixed", "IFRS_1H2026", 31, 67484262, "Долгосрочные кредиты и займы по фиксированной ставке"),
    ("d_lt_float", "IFRS_1H2026", 31, 32000000, "Долгосрочные кредиты по плавающей ставке"),
    ("d_lt_bonds", "IFRS_1H2026", 31, 283177, "Долгосрочные облигации"),
    ("d_lt_fixed_int", "IFRS_1H2026", 31, 262932, "Долгосрочные проценты по фиксированным займам"),
    ("d_st_fixed_2025", "IFRS_1H2026", 31, 51122737, "Краткосрочная часть фиксированных 31.12.2025"),
    ("d_st_float_2025", "IFRS_1H2026", 31, 4000000, "Краткосрочная часть плавающих 31.12.2025"),
    ("undrawn_2026h1", "IFRS_1H2026", 31, 283863000, "Неиспользованный остаток лимита 30.06.2026"),
    ("undrawn_2025fy", "IFRS_1H2026", 31, 226363000, "Неиспользованный остаток лимита 31.12.2025"),
    ("collateral_okey", "IFRS_1H2026", 32, 23275427, "Залог имущества по кредиту «О’КЕЙ»"),
    ("rate_sens_up200", "IFRS_1H2026", 32, 51945, "Чувствительность к +200 б.п. (6М2026)"),
    # --- прим. 21–22 (с. 32–33)
    ("tax_current_2026h1", "IFRS_1H2026", 32, 3583672, "Текущий налог на прибыль (−) 6М2026"),
    ("tax_deferred_2026h1", "IFRS_1H2026", 32, 825067, "Отложенный налог (−) 6М2026"),
    ("tax_nondeductible_2026h1", "IFRS_1H2026", 33, 637178, "Налоговый эффект невычитаемых расходов (−) 6М2026"),
    ("pay_trade", "IFRS_1H2026", 33, 119737664, "Торговая кредиторская задолженность 30.06.2026"),
    ("pay_capex_business", "IFRS_1H2026", 33, 9119601, "Кредиторка за ОС, НМА и приобретения бизнеса 30.06.2026"),
    ("pay_accrued", "IFRS_1H2026", 33, 10051074, "Начисленные обязательства и прочие кредиторы 30.06.2026"),
    ("pay_personnel", "IFRS_1H2026", 33, 10139199, "Кредиторка перед персоналом 30.06.2026"),
    ("pay_option", "IFRS_1H2026", 33, 5214174, "Кредиторская задолженность по опциону (пут «Реми») 30.06.2026"),
    ("pay_factoring", "IFRS_1H2026", 33, 3019428, "Кредиторка с факторингом 30.06.2026"),
    ("pay_capex_business_2025", "IFRS_1H2026", 33, 16499763, "Кредиторка за ОС, НМА и приобретения бизнеса 31.12.2025"),
    ("pay_trade_2025", "IFRS_1H2026", 33, 129319431, "Торговая кредиторская задолженность 31.12.2025"),
    # --- прим. 25–31 (с. 34–37)
    ("rev_retail_2026h1", "IFRS_1H2026", 34, 643691924, "Выручка: розничная торговля 6М2026"),
    ("rev_wholesale_2026h1", "IFRS_1H2026", 34, 4796135, "Выручка: оптовая торговля 6М2026"),
    ("sga_labor_2026h1", "IFRS_1H2026", 35, 59569676, "SG&A: оплата труда 6М2026"),
    ("sga_lease_2026h1", "IFRS_1H2026", 35, 3271316, "SG&A: расходы по аренде (МСФО 16, краткосрочная + переменная) 6М2026"),
    ("int_loans_2026h1", "IFRS_1H2026", 36, 7297475, "Процентные расходы: по кредитам 6М2026"),
    ("int_lease_2026h1", "IFRS_1H2026", 36, 11501457, "Процентные расходы: по аренде 6М2026"),
    ("int_other_2026h1", "IFRS_1H2026", 36, 448072, "Процентные расходы: прочие (размотка пута «Реми») 6М2026"),
    ("int_total_2026h1", "IFRS_1H2026", 36, 19247004, "Процентные расходы итого 6М2026"),
    ("inc_deposits_2026h1", "IFRS_1H2026", 37, 1703750, "Процентные доходы: депозиты 6М2026"),
    ("inc_loans_2026h1", "IFRS_1H2026", 37, 579220, "Процентные доходы: займы выданные 6М2026"),
    ("inc_other_2026h1", "IFRS_1H2026", 37, 385065, "Процентные доходы: прочие 6М2026"),
    ("capex_commitments", "IFRS_1H2026", 37, 11370371, "Договорные обязательства по капвложениям 30.06.2026, без НДС"),
    # --- прим. 32 (с. 38): справедливая стоимость
    ("fv_fixed_loans_l2", "IFRS_1H2026", 38, 101349812, "Справедливая стоимость: кредиты и займы по фиксированной ставке (уровень 2)"),
    ("fv_bonds_l1", "IFRS_1H2026", 38, 6791299, "Справедливая стоимость: облигационный заём (уровень 1)"),
    ("fv_float_l2", "IFRS_1H2026", 38, 37151656, "Справедливая стоимость: кредиты по плавающей ставке"),
    ("bv_float", "IFRS_1H2026", 38, 37141621, "Балансовая стоимость: кредиты по плавающей ставке 30.06.2026"),
    ("bv_fixed", "IFRS_1H2026", 38, 105442063, "Балансовая стоимость: фиксированные кредиты, займы и облигации 30.06.2026"),
    ("fv_fixed", "IFRS_1H2026", 38, 108141111, "Справедливая стоимость: фиксированные кредиты, займы и облигации 30.06.2026"),
    ("bv_total", "IFRS_1H2026", 38, 142583684, "Балансовая стоимость итого 30.06.2026"),
    ("fv_total", "IFRS_1H2026", 38, 145292767, "Справедливая стоимость итого 30.06.2026"),
    ("bv_fixed_2025", "IFRS_1H2026", 38, 95885041, "Балансовая фиксированных 31.12.2025"),
    ("fv_fixed_2025", "IFRS_1H2026", 38, 97290168, "Справедливая фиксированных 31.12.2025"),
    ("bv_float_2025", "IFRS_1H2026", 38, 4014255, "Балансовая плавающих 31.12.2025"),
    ("fv_float_2025", "IFRS_1H2026", 38, 4242075, "Справедливая плавающих 31.12.2025"),
    # --- прим. 33 (с. 40)
    ("litigation_max", "IFRS_1H2026", 40, 340088, "Условные обязательства по искам: максимальный эффект 30.06.2026"),
    # --- МСФО 2025
    ("fy25_nci_loss", "IFRS_FY2025", 7, 42919, "Прибыль (убыток) НДУ за 2025 (−)"),
    ("fy25_other_ltl", "IFRS_FY2025", 6, 6531216, "Прочие долгосрочные обязательства 31.12.2025 (прим. 25)"),
    ("fy25_molniya_goodwill", "IFRS_FY2025", 46, 4543760, "«Молния – Spar»: гудвил (итоговое распределение)"),
    ("fy25_molniya_consideration", "IFRS_FY2025", 46, 4959289, "«Молния – Spar»: возмещение (итог)"),
    ("fy25_molniya_cash", "IFRS_FY2025", 47, 3565261, "«Молния – Spar»: денежное вознаграждение"),
    ("fy25_molniya_offset", "IFRS_FY2025", 47, 1394028, "«Молния – Spar»: зачёт дебиторки прежнего владельца"),
    ("fy25_molniya_debt", "IFRS_FY2025", 46, 2231017, "«Молния – Spar»: принятые краткосрочные кредиты (−)"),
    ("fy25_molniya_rev", "IFRS_FY2025", 47, 9643909, "«Молния – Spar»: вклад в выручку с даты приобретения (с 24.06.2025)"),
    ("fy25_molniya_pf_rev", "IFRS_FY2025", 47, 19796109, "«Молния – Spar»: выручка «если бы с начала 2025» (по смыслу — выручка сети)"),
    ("fy25_remi_indemn", "IFRS_FY2025", 48, 2471493, "«Реми»: прочие оборотные активы в распределении цены (компенсирующий актив)"),
    ("fy25_remi_tax_other", "IFRS_FY2025", 48, 2931526, "«Реми»: обязательства по прочим налогам при приобретении (−)"),
    ("fy25_remi_tax_income", "IFRS_FY2025", 48, 1257489, "«Реми»: обязательства по налогу на прибыль при приобретении (−)"),
    ("fy25_remi_nci", "IFRS_FY2025", 48, 978523, "«Реми»: неконтролирующие доли по доле в чистых активах (−)"),
    ("fy25_remi_goodwill", "IFRS_FY2025", 48, 8204303, "«Реми»: гудвил"),
    ("fy25_remi_consideration", "IFRS_FY2025", 48, 10191000, "«Реми»: возмещение"),
    ("fy25_remi_put", "IFRS_FY2025", 48, 4766102, "«Реми»: обязательство по пут-опциону 31.12.2025 (в тексте «млн руб.» — опечатка, тыс.)"),
    ("fy25_remi_lc", "IFRS_FY2025", 49, 8191000, "«Реми»: аккредитив 12.2025"),
    ("fy25_remi_escrow", "IFRS_FY2025", 49, 2000000, "«Реми»: эскроу 01.2026"),
    ("fy25_remi_rev", "IFRS_FY2025", 49, 5100086, "«Реми»: вклад в выручку с 03.12.2025"),
    ("fy25_remi_pbt", "IFRS_FY2025", 49, 107056, "«Реми»: убыток до налога с даты приобретения"),
    ("fy25_remi_pf_rev", "IFRS_FY2025", 49, 55488000, "«Реми»: выручка «если бы с начала 2025» (по смыслу — выручка сети)"),
    ("fy25_undrawn", "IFRS_FY2025", 64, 226363000, "Неиспользованный лимит 31.12.2025 (прим. 23)"),
    # --- МСФО 2024
    ("fy24_ulybka_consideration", "IFRS_FY2024", 43, 20938000, "«Улыбка радуги»: возмещение"),
    ("fy24_ulybka_net_assets", "IFRS_FY2024", 43, 22041861, "«Улыбка радуги»: чистые активы"),
    ("fy24_ulybka_gain", "IFRS_FY2024", 43, 1103861, "«Улыбка радуги»: доход от выгодного приобретения"),
    ("fy24_ulybka_loan", "IFRS_FY2024", 43, 12889000, "«Улыбка радуги»: заём, выданный продавцу (зачтён)"),
    ("fy24_ulybka_assign", "IFRS_FY2024", 44, 8045830, "«Улыбка радуги»: уступка беспроцентного займа продавцу (зачтена)"),
    ("fy24_ulybka_cash", "IFRS_FY2024", 44, 3170, "«Улыбка радуги»: доплата деньгами в 1 кв. 2025"),
    ("fy24_ulybka_rev", "IFRS_FY2024", 44, 4709172, "«Улыбка радуги»: вклад в выручку (с 01.12.2024)"),
    ("fy24_ulybka_pbt", "IFRS_FY2024", 44, 213171, "«Улыбка радуги»: вклад в прибыль до налога"),
    ("fy24_ulybka_pf_rev", "IFRS_FY2024", 44, 41566662, "«Улыбка радуги»: выручка «если бы с начала 2024» (по смыслу — выручка сети)"),
    ("fy24_ulybka_pf_pbt", "IFRS_FY2024", 44, 941016, "«Улыбка радуги»: прибыль до налога «если бы с начала 2024»"),
    ("fy24_other_gain", "IFRS_FY2024", 45, 10744, "Прочее приобретение 2024: доход от выгодной покупки"),
    ("fy24_gain_total", "IFRS_FY2024", 8, 1114605, "Прибыль от выгодного приобретения (ОДДС) 2024"),
    ("fy24_monetka_goodwill", "IFRS_FY2024", 46, 46847912, "«Монетка»: гудвил (финальное распределение)"),
    ("fy24_monetka_consideration", "IFRS_FY2024", 46, 81061371, "«Монетка»: справедливая стоимость возмещения"),
    ("fy24_monetka_cash", "IFRS_FY2024", 46, 76076345, "«Монетка»: денежное вознаграждение (2023)"),
    ("fy24_monetka_net_cf", "IFRS_FY2024", 46, 73925686, "«Монетка»: чистый отток 2023"),
    ("fy24_monetka_refund", "IFRS_FY2024", 46, 1500000, "«Монетка»: возврат корректировки цены (2024)"),
    ("fy24_monetka_seller_loan", "IFRS_FY2024", 46, 6485026, "«Монетка»: заём, выданный продавцу, в активах при покупке"),
    # --- МСФО 2023 (ред. 2)
    ("fy23_monetka_rev", "IFRS_FY2023_V2", 49, 58695756, "«Монетка» (Группа Б*): вклад в выручку с октября 2023"),
    ("fy23_monetka_pbt", "IFRS_FY2023_V2", 49, 3455490, "«Монетка»: вклад в прибыль до налога"),
    ("fy23_monetka_pf_group_rev", "IFRS_FY2023_V2", 49, 767587524, "Выручка группы 2023 «если бы с начала 2023»"),
    ("fy23_monetka_pf_group_pbt", "IFRS_FY2023_V2", 49, 10790507, "Прибыль до налога группы 2023 «если бы с начала 2023»"),
    ("fy23_utkonos_goodwill", "IFRS_FY2023_V2", 54, 1037593, "«Утконос»: гудвил при приобретении 2022 и его обесценение в том же году"),
    ("fy23_utkonos_tm_impair_2023", "IFRS_FY2023_V2", 54, 1457832, "Обесценение товарного знака «Утконос» 2023"),
    ("fy23_utkonos_tm_impair_2022", "IFRS_FY2023_V2", 54, 506384, "Обесценение товарного знака «Утконос» 2022"),
    # --- МСФО 2021 (EN)
    ("fy21_billa_cash", "IFRS_FY2021", 47, 19596144, "Billa: cash paid / fair value of purchase consideration"),
    ("fy21_billa_goodwill", "IFRS_FY2021", 47, 6934181, "Billa: goodwill"),
    ("fy21_billa_net_assets", "IFRS_FY2021", 47, 12661963, "Billa: fair value of identifiable net assets"),
    ("fy21_billa_net_cf", "IFRS_FY2021", 47, 19193096, "Billa: net cash flow on acquisition"),
    ("fy21_billa_rev", "IFRS_FY2021", 48, 9501250, "Billa: contribution to revenue (с 08.2021)"),
    ("fy21_billa_pbt", "IFRS_FY2021", 48, 1097054, "Billa: loss before income tax contribution"),
    ("fy21_semya_cash", "IFRS_FY2021", 48, 2454904, "Semya: cash consideration"),
    ("fy21_semya_goodwill", "IFRS_FY2021", 49, 1449449, "Semya: goodwill"),
    ("fy21_semya_net_cf", "IFRS_FY2021", 49, 2391055, "Semya: net cash flow on acquisition"),
    ("fy21_semya_rev", "IFRS_FY2021", 49, 4159083, "Semya: contribution to revenue (с 09.2021)"),
    ("fy21_semya_pbt", "IFRS_FY2021", 49, 54909, "Semya: profit before tax contribution"),
    ("fy21_semya_debt", "IFRS_FY2021", 49, 168748, "Semya: assumed short-term borrowings"),
    # --- «Утконос»: сущфакты
    ("sf22_utkonos_shares", "SF_2022_06_21", 1, 18399265, "Размещено акций в оплату «Утконоса»"),
    ("sf22_utkonos_price", "SF_2022_06_21", 1, 1087, "Цена размещения, ₽ за акцию"),
    ("sf22_utkonos_value", "SF_2022_06_21", 2, 20000000000, "Зачёт требований, ₽"),
    ("sf21_utkonos_base", "SF_2021_12_16", 2, 20000000000, "База цены сделки «Утконос», ₽"),
    # --- ГЗОСА 2026
    ("agm_voting_shares", "AGM2026_RESULTS", 1, 111104354, "Голосующие размещённые акции на 02.06.2026 (111 104 354 2/5)"),
]

NOTE_QUOTES = [
    ("q_okey_note5", "IFRS_1H2026", 18,
     "С даты приобретения вклад сети гипермаркетов «О’КЕЙ» в выручку Группы составил 9 359 828 тыс. руб., был получен убыток до налогообложения с даты приобретения 907 372 тыс. руб., соответственно. Если бы объединение произошло в начале 2026 года, то влияние на выручку за 2026 год составило бы 66 120 725 тыс. руб., влияние на прибыль до налогообложения составило бы 5 530 179 тыс. руб. в сторону уменьшения."),
    ("q_okey_consolidation", "IFRS_1H2026", 16, "включены в консолидированную финансовую отчетность Группы начиная со 2 июня 2026 г."),
    ("q_obi_rest", "IFRS_1H2026", 19, "Оставшаяся задолженность будет погашена в 2026 году."),
    ("q_restricted_nil", "IFRS_1H2026", 29, "задолженности перед продавцом торговой сети «Реми» – 8 191 000"),
    ("q_indemn", "IFRS_1H2026", 29, "Компенсирующий актив 2 471 493"),
    ("q_ltip", "IFRS_1H2026", 33, "прочие долгосрочные обязательства представляют собой долгосрочную часть обязательств перед персоналом по долгосрочной программе премирования руководящего персонала"),
    ("q_put_in_payables", "IFRS_1H2026", 33, "Данное обязательство на 30 июня 2026 г. отражено в составе торговой и прочей кредиторской задолженности."),
    ("q_rp_loans_nil", "IFRS_1H2026", 14, "Краткосрочные займы выданные – 11 533 698"),
    ("q_dividends_none", "IFRS_1H2026", 29, "дивиденды не объявлялись"),
    ("q_remi_put_fy25", "IFRS_FY2025", 48, "опцион «пут», не предоставляющий текущих прав на доли участия"),
    ("q_ulybka_consol", "IFRS_FY2024", 43, "включены в обобщенную консолидированную финансовую отчетность Группы начиная с 1 декабря 2024 г."),
    ("q_molniya_consol", "IFRS_FY2025", 45, "консолидированную финансовую отчетность Группы начиная с 24 июня 2025 г."),
    ("q_remi_consol", "IFRS_FY2025", 48, "Группы начиная с 3 декабря 2025 г."),
    ("q_obi_consol", "IFRS_1H2026", 18, "Группы начиная с первого квартала 2026 года."),
    ("q_billa_consol", "IFRS_FY2021", 47, "the Group’s consolidated financial statements beginning from August 2021."),
    ("q_semya_consol", "IFRS_FY2021", 48, "consolidated financial statements beginning from September 2021."),
    ("q_monetka_date", "IFRS_FY2023_V2", 47, "В октябре 2023 года Группа приобрела 99,9999% долю в Компании Б*"),
]


def verify_notes():
    out = {}
    bad = []
    for fid, doc, page, val, label in NOTE_FACTS:
        rel = doc_path(doc)
        if rel.endswith(".pdf"):
            ok = abs(int(val)) in pdf_page_numbers(rel, page)
            found = [page] if ok else find_number_pages(rel, val)
        else:
            ok = abs(int(val)) in numbers_in(text_file(rel))
            found = [page] if ok else []
        if not ok:
            bad.append((fid, doc, page, val, found))
        out[fid] = {"th": val, "v": r6(val / 1e6) if val >= 1000 else val, "doc": doc, "page": page,
                    "label": label, "verified": bool(ok), "found_on": found}
    qout = {}
    for qid, doc, page, quote in NOTE_QUOTES:
        ok = pdf_page_has_text(doc_path(doc), page, quote)
        if not ok:
            pages = pdf_pages(doc_path(doc))
            alt = [i + 1 for i in range(len(pages)) if pdf_page_has_text(doc_path(doc), i + 1, quote)]
            bad.append((qid, doc, page, quote[:40], alt))
        qout[qid] = {"doc": doc, "page": page, "quote": quote, "verified": bool(ok)}
    log(f"[notes] транскрипций чисел: {len(NOTE_FACTS)}, цитат: {len(NOTE_QUOTES)}, не найдено: {len(bad)}")
    for b in bad:
        log(f"  НЕ НАЙДЕНО: {b}")
    return out, qout, bad


# =========================================================================== датабук
PL_LINES = [
    ("Sales", "revenue"), ("Cost of sales", "cogs"), ("Gross profit", "gross_profit"),
    ("Selling, general and administrative expenses", "sga"), ("Labor costs", "staff"), ("Depreciation", "da"),
    ("Professional fees", "professional_fees"), ("Advertising", "advertising"),
    ("Utilities and communal payments", "utilities"), ("Repairs and maintenance", "repairs"), ("Cleaning", "cleaning"),
    ("Lease of premises", "lease_premises"), ("Other", "sga_other"), ("Taxes other than income tax", "taxes_other"),
    ("Security", "security"), ("Pre-opening cost", "preopening"), ("Land and equipment lease", "lease_land_equipment"),
    ("Other operating income", "other_op_income"), ("Other operating expenses", "other_op_expense"),
    ("Operating profit before impairment", "opbi"),
    ("(Impairment)/Reversal of impairment of non-financial assets", "impairment"),
    ("Operating profit / (loss)", "ebit"), ("Interest expense", "interest_expense"), ("Interest income", "interest_income"),
    ("Put option revaluation", "put_revaluation"), ("Swaps and CAPs at FV", "swaps_fv"),
    ("Other non-operating expense", "other_non_operating"), ("Foreign exchange gains/(losses)", "fx"),
    ("Profit / (loss) before income tax", "pbt"), ("Income tax benefit/(expense)", "income_tax"),
    ("Profit / (loss) for the period", "net_income"), ("EBITDA", "ebitda"),
]
BS_LINES = [
    ("Property, plant and equipment", "ppe"), ("Prepayments for construction", "prepayments_construction"),
    ("Right-of-use assets", "rou_assets"), ("Investment property", "investment_property"),
    ("Leasehold rights", "leasehold_rights"), ("Intangible assets", "intangibles"), ("Goodwill", "goodwill"),
    ("Deferred tax asset", "dta"), ("Other non-current assets", "other_nca"), ("Total non-current assets", "total_nca"),
    ("Inventories", "inventories"), ("Trade and other receivables", "receivables"),
    ("Short-term loans issued", "st_loans_issued"), ("Advances paid", "advances_paid"),
    ("Taxes recoverable", "taxes_recoverable"), ("Income tax advances", "income_tax_advances"),
    ("Prepaid expense", "prepaid"), ("Restricted cash", "restricted_cash_db_label"),
    ("Other current financial assets", "other_current_fin_assets"), ("Cash and cash equivalents", "cash"),
    ("Assets held for sale", "assets_held_for_sale"), ("Total current assets", "total_ca"),
    ("TOTAL ASSETS", "total_assets"), ("Share capital", "share_capital"), ("Additional paid-in capital", "apic"),
    ("Treasury shares", "treasury_shares"), ("Share options reserve", "options_reserve"),
    ("Non-Controlling Interest", "nci"), ("Accumulated retained earnings/(losses)", "retained_earnings"),
    ("Total equity", "total_equity"), ("Long-term borrowings", "lt_borrowings"), ("Deferred tax liability", "dtl"),
    ("Long-term lease liabilities", "lt_lease"), ("Other non-current liabilities", "other_ncl"),
    ("Total non-current liabilities", "total_ncl"),
    ("Short-term borrowings and ST portion of LT borrowings", "st_borrowings"),
    ("Short-term lease liabilities", "st_lease"), ("Put option liability to minority shareholder", "put_liability_db"),
    ("Trade and other payables", "payables"), ("Contract liabilities", "contract_liabilities"),
    ("Advances received", "advances_received"), ("Current income tax payable", "income_tax_payable"),
    ("Other taxes payable", "other_taxes_payable"), ("Total current liabilities", "total_cl"),
    ("TOTAL LIABILITIES", "total_liabilities"), ("TOTAL LIABILITIES AND EQUITY", "total_le"),
]
CF_LINES = [
    ("Profit before income tax", "pbt"), ("Interest expense", "interest_expense_addback"),
    ("Depreciation and amortization", "da"), ("Profit from purchase", "bargain_purchase_gain"),
    ("(Increase)/decrease in trade and other receivables", "d_receivables"),
    ("(Increase)/decrease in advances paid", "d_advances_paid"), ("(Increase)/decrease in prepaid expenses", "d_prepaid"),
    ("(Increase)/decrease in inventories", "d_inventories"), ("(Increase)/decrease) in trade and other payables", "d_payables"),
    ("(Increase)/decrease in advances received", "d_advances_received"),
    ("Increase/(decrease) in other taxes payable", "d_other_taxes"),
    ("Cash generated from operating activities", "cash_generated"), ("Income taxes paid", "income_tax_paid"),
    ("Interest paid", "interest_paid"), ("Interest received", "interest_received"),
    ("Net cash generated from / (used in) operating activities", "net_ocf"),
    ("Purchases of property, plant and equipment", "capex_ppe"),
    ("Acquisition of subsidiaries, net of cash acquired", "acquisitions_net"),
    ("Purchases of intangible assets other than leasehold rights", "capex_intangibles"),
    ("Purchases of leasehold rights", "capex_leasehold_rights"), ("Loans given, net of loans paid", "loans_given_net"),
    ("Proceeds from disposals and leasehold rights", "proceeds_disposals_lr"),
    ("Proceeds from sale of property, plant and equipment", "proceeds_ppe"),
    ("Transfer to restricted cash", "transfer_restricted"), ("Net cash used in investing activities", "net_icf"),
    ("Proceeds from borrowings", "borrowings_in"), ("Repayments of borrowings", "borrowings_out"),
    ("Payments for the principal portion of the lease liabilities", "lease_principal"),
    ("Repayment of obligations under financial lease", "finance_lease_repaid"),
    ("Proceeds from issue of new shares", "share_issue"), ("Repurchase of treasury shares", "buyback"),
    ("Net cash generated from / (used in) financing activities", "net_fcf"),
    ("Cash and cash equivalents at the beginning of the period", "cash_begin"),
    ("Net increase/(decrease) in cash and cash equivalents", "net_change_cash"),
    ("Cash and cash equivalents at the end of the period", "cash_end"),
]
FQ_LINES = [
    ("Total Sales", "revenue"), ("Retail Sales", "retail"), ("Hypermarkets", "seg_hyper_db"),
    ("Supermarkets", "seg_super"), ("Convenience stores", "seg_convenience"), ("Utkonos", "seg_utkonos"),
    ("Drogerie", "seg_droge"), ("Remi", "seg_remi"), ("Dom Lenta", "seg_diy"), ("Other formats", "seg_other_formats"),
    ("Wholesale", "wholesale"), ("Cost of goods sold", "cogs"), ("Gross profit", "gross_profit"),
    ("Selling, general and administrative expenses", "sga"), ("Payroll and related taxes", "staff"),
    ("Depreciationa and Amortization", "da"), ("Lease Expenses", "lease_expense"), ("Utilities and communal payments", "utilities"),
    ("Store Operations", "store_operations"), ("Professional Fees", "professional_fees"), ("Advertising", "advertising"),
    ("Other", "sga_other"), ("EBITDAR", "ebitdar"), ("EBITDA", "ebitda"),
    ("Operating profit before impairment", "opbi"), ("Resersal of impairment/(impairment )", "impairment"),
    ("Operating profit", "ebit"), ("Net Interest expense", "net_interest"), ("(Net FX loss)", "fx"),
    ("Profit Before Income Tax", "pbt"), ("Income tax expense", "income_tax"), ("Net Income", "net_income"),
    ("Movements in Working Capital", "d_nwc"), ("Cash generated from operating activities", "cash_generated"),
    ("Net Interest & Income Taxes Paid", "interest_tax_paid"),
    ("Net Cash generated from Operating Activities", "net_ocf"), ("Net cash used in Investing Activities", "net_icf"),
    ("Net cash used in financing activities", "net_fcf"),
]
HALVES = [f"{y}H{h}" for y in range(2018, 2027) for h in (1, 2)][:-1]      # 2018H1..2026H1
QUARTERS = [f"{y}Q{q}" for y in range(2020, 2027) for q in (1, 2, 3, 4)][:-2]  # 2020Q1..2026Q2
BS_DATES = {f"{y}H1": f"{y}-06-30" for y in range(2018, 2027)} | {f"FY{y}": f"{y}-12-31" for y in range(2017, 2026)}


def flows(sheet: Sheet, lines, basis: str):
    """Полугодия: H1 — столбец «1H», H2 = FY − 1H (расчёт). Плюс годы."""
    halves, fys = {}, {}
    for y in range(2018, 2027):
        for per in (f"{y}H1", f"FY{y}"):
            rec = {}
            for lab, key in lines:
                try:
                    v, ref = sheet.cell(lab, basis, per)
                except KeyError:
                    continue
                if ref is None:
                    continue
                rec[key] = {"v": r6(v), "src": ref}
            if rec:
                (halves if per.endswith("H1") else fys)[per] = rec
        h1, fy = halves.get(f"{y}H1"), fys.get(f"FY{y}")
        if h1 and fy:
            h2 = {}
            for key in fy:
                if key in h1 and fy[key]["v"] is not None and h1[key]["v"] is not None:
                    h2[key] = {"v": r6(fy[key]["v"] - h1[key]["v"]), "src": f"{fy[key]['src']} − {h1[key]['src'].split('!')[1]}", "kind": "calc"}
            halves[f"{y}H2"] = h2
    return {p: halves[p] for p in HALVES if p in halves}, {p: fys[p] for p in sorted(fys)}


def balances(sheet: Sheet, basis: str):
    out = {}
    for per, date in sorted(BS_DATES.items(), key=lambda kv: kv[1]):
        rec = {}
        for lab, key in BS_LINES:
            try:
                v, ref = sheet.cell(lab, basis, per)
            except KeyError:
                continue
            if ref is None:
                continue
            rec[key] = {"v": r6(v), "src": ref}
        if rec:
            out[date] = rec
    return out


def quarterly(sheet: Sheet, basis: str):
    out = {}
    for q in QUARTERS:
        rec = {}
        for lab, key in FQ_LINES:
            try:
                v, ref = sheet.cell(lab, basis, q)
            except KeyError:
                continue
            if ref is None:
                continue
            rec[key] = {"v": r6(v), "src": ref}
        if rec:
            out[q] = rec
    return out


def debt_sheet(ws):
    """Лист Debt: 2010–2019 в тыс. ₽, с 2020 — в млн ₽ (разрыв единиц в одном листе)."""
    from openpyxl.utils import get_column_letter
    markers = sorted((c.column, "ias17" if str(c.value).strip() == "IAS 17" else "ifrs16")
                     for c in ws[7] if str(c.value or "").strip() in ("IAS 17", "IFRS 16"))
    rows = {"Long-term debt": "lt_debt", "Short-term debt": "st_debt", "Lease Liabilities (IFRS 16)": "lease_liabilities",
            "Total Debt": "total_debt", "Cash and cash equivalents": "cash", "Net Debt": "net_debt",
            "Net Debt/Adjusted EBITDA ratio": "nd_ebitda"}
    rowidx = {}
    for r in range(9, 22):
        lab = str(ws.cell(r, 1).value or "").strip()
        if lab in rows and lab not in rowidx:
            rowidx[lab] = r
    out = {"ias17": {}, "ifrs16": {}}
    for c in ws[8]:
        if c.value is None:
            continue
        basis = None
        for col, b in markers:
            if col <= c.column:
                basis = b
        if basis is None:
            continue
        if hasattr(c.value, "year"):
            date, year = c.value.strftime("%Y-%m-%d"), c.value.year
        else:
            year = int(c.value)
            date = f"{year}-12-31"
        if year < 2016:
            continue
        rec = {}
        for lab, key in rows.items():
            r = rowidx.get(lab)
            if not r:
                continue
            v = ws.cell(r, c.column).value
            if not isinstance(v, (int, float)):
                continue
            scale = 1.0 if key == "nd_ebitda" else (1e-6 if year <= 2019 else 1e-3)
            rec[key] = {"v": r6(v * scale), "src": f"Debt!{get_column_letter(c.column)}{r}",
                        "unit_note": "тыс. ₽ в листе" if (year <= 2019 and key != "nd_ebitda") else ("млн ₽ в листе" if key != "nd_ebitda" else "×")}
        out[basis][date] = rec
    return out


def operating(ws):
    """Лист Operating Results: магазины, площадь, собственность по форматам на конец квартала."""
    from openpyxl.utils import get_column_letter
    hdr = {}
    for c in ws[7]:
        per = None
        if c.value is not None:
            from lib_primary import norm_period
            per = norm_period(c.value)
        if per:
            hdr[per] = c.column
    sections = {"Total Retail Stores, eop": "stores", "Total Selling Space, sqm, eop": "area_sqm",
                "New Selling Space, sqm, eop": "net_new_area_sqm", "Total Net Store Openings, during the period": "net_openings",
                "Total Sales, RUB millions": "sales_rub_mln", "LFL Retail Sales": "lfl_group"}
    fmt = {"Hypermarkets": "hyper_db", "Hypermarket": "hyper_db", "Supermarkets": "super", "Convenience stores": "convenience",
           "Drogerie": "droge", "Remi": "remi", "Dom Lenta": "diy", "Other formats": "other_formats",
           "Utkonos": "utkonos", "Wholesales": "wholesale", "Retail Sales": "retail"}
    out = {}
    r = 1
    maxr = ws.max_row
    while r <= maxr:
        lab = str(ws.cell(r, 1).value or "").strip()
        if lab in sections:
            sec = sections[lab]
            block = {"_total": r}
            rr = r + 1
            while rr <= maxr:
                l2 = str(ws.cell(rr, 1).value or "").strip()
                if l2 in fmt and fmt[l2] not in block:
                    block[fmt[l2]] = rr
                    rr += 1
                    continue
                break
            for q in [p for p in hdr if "Q" in p and p >= "2023Q1"] + ["FY2025", "2026H1"]:
                if q not in hdr:
                    continue
                c = hdr[q]
                rec = {}
                for k, rowi in block.items():
                    v = ws.cell(rowi, c).value
                    if isinstance(v, (int, float)):
                        rec["total" if k == "_total" else k] = {"v": r6(v), "src": f"Operating Results!{get_column_letter(c)}{rowi}"}
                out.setdefault(sec, {})[q] = rec
            r = rr
            continue
        r += 1
    # собственность площади
    own = {}
    for lab, key in (("Owned", "owned"), ("Rented", "rented")):
        for rr in range(95, 120):
            if str(ws.cell(rr, 1).value or "").strip() == lab:
                for q in [p for p in hdr if "Q" in p and p >= "2025Q1"]:
                    v = ws.cell(rr, hdr[q]).value
                    if isinstance(v, (int, float)):
                        own.setdefault(q, {})[key] = {"v": r6(v), "src": f"Operating Results!{get_column_letter(hdr[q])}{rr}"}
                break
    out["area_ownership_sqm"] = own
    return out


# =========================================================================== сверка базы МСФО 16 с PDF
def verify_ifrs16(halves16, bal16):
    """Для якорных периодов: число блока IFRS 16 датабука (тыс. ₽) обязано найтись в первичных формах."""
    checks = []
    plan = [
        ("2026H1", "IFRS_1H2026", range(6, 41), "flow"), ("2025H1", "IFRS_1H2026", range(6, 41), "flow"),
        ("2025H1", "IFRS_1H2025", range(4, 40), "flow"),
        ("2026-06-30", "IFRS_1H2026", range(6, 7), "bs"), ("2025-12-31", "IFRS_1H2026", range(6, 7), "bs"),
        ("2025-12-31", "IFRS_FY2025", range(4, 12), "bs"), ("2024-12-31", "IFRS_FY2025", range(4, 12), "bs"),
        ("2024-12-31", "IFRS_FY2024", range(4, 12), "bs"),
    ]
    for per, doc, rng, kind in plan:
        rec = (halves16.get(per) if kind == "flow" else bal16.get(per)) or {}
        for key, cell in rec.items():
            v = cell.get("v")
            if v is None or abs(v) < 0.0005 or cell.get("kind") == "calc":
                continue
            th = int(round(abs(v) * 1e6))
            pages = [p for p in rng if th in pdf_page_numbers(doc_path(doc), p)]
            checks.append({"period": per, "line": key, "th": th, "doc": doc, "found_pages": pages})
    # FY2025 потоки: годовые числа в годовой отчётности
    return checks


def manifest_gate(sources) -> list[str]:
    """Документы первички, чей sha256 не совпал с MANIFEST.md, — пути по порядку реестра.

    Непустой список останавливает сборку кодом 2 (VERIFY.md, «Сборка»): подмена первички,
    которую не покрывают ни транскрипции PDF, ни тождества (карточка бумаги ISS, история и
    операционный лист датабука), иначе уходила бы в факты с кодом 0 и одной строкой
    «ВНИМАНИЕ» в логе (внешний аудит 30.09.2026, D01).
    """
    return [s["path"] for s in sources.values() if not s["hash_matches_manifest"]]


def stop(reason: str):
    """Отказ сборки: причина — в лог и на экран, лог — в файл, код 2."""
    log("ОШИБКА: " + reason + " — сборка остановлена")
    (HERE / "out").mkdir(parents=True, exist_ok=True)
    (HERE / "out" / "build_log.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")
    sys.exit(2)


# =========================================================================== основной сбор
def main():
    P = primary_dir()
    log(f"LENTA_PRIMARY_DIR = <каталог первички> ({P.name})")
    man = manifest()

    # --- источники и хэши
    sources = OrderedDict()
    for did, (rel, desc) in DOCS.items():
        fp = P / rel
        actual = sha256_file(fp)
        m = man.get(rel)
        match = m is not None and m["sha256"] == actual
        via = None
        if m is None and rel.endswith(".txt"):
            # .txt рядом с .html: MANIFEST даёт префикс sha в описании строки .html
            mh = man.get(rel[:-4] + ".html")
            if mh and f"sha {actual[:16]}" in mh["desc"]:
                m, match, via = mh, True, rel[:-4] + ".html (префикс sha .txt в описании)"
        sources[did] = {"path": rel, "description": desc, "sha256": actual,
                        "manifest_sha256": (m["sha256"] if m and via is None else (actual[:16] + "…" if via else None)),
                        "manifest_url": m["url"] if m else None,
                        "manifest_row": via or rel if m else None,
                        "hash_matches_manifest": match,
                        "bytes": fp.stat().st_size}
        if not match:
            log(f"[sources] ВНИМАНИЕ: {rel}: manifest={m['sha256'][:16] if m else None} actual={actual[:16]}")
    log(f"[sources] документов: {len(sources)}, хэш = MANIFEST: {sum(s['hash_matches_manifest'] for s in sources.values())}")
    changed = manifest_gate(sources)
    if changed:
        stop("sha256 первички не совпал с MANIFEST.md: " + ", ".join(changed))

    notes, quotes, bad = verify_notes()
    if bad:
        stop("не все транскрипции подтверждены первичкой")

    def N(fid):
        return notes[fid]

    def nv(fid, sign=1.0):
        return round(sign * notes[fid]["th"] / 1e6, 6)

    def ref(fid):
        n = notes[fid]
        return f"{n['doc']} с. {n['page']}"

    # --- датабук
    wb = openpyxl.load_workbook(P / doc_path("DATABOOK_Q2_2026"), data_only=True)
    pl, bs, cf = Sheet(wb["PL"]), Sheet(wb["BS"]), Sheet(wb["CF"])
    fq = Sheet(wb["Financials quarterly"], scale=1e-3)
    halves, fys, bal, quarters = {}, {}, {}, {}
    for basis in ("ias17", "ifrs16"):
        h_pl, f_pl = flows(pl, PL_LINES, basis)
        h_cf, f_cf = flows(cf, CF_LINES, basis)
        # Порядок периодов — сортировкой, а не обходом множества: иначе байты
        # accounting_base.json зависели бы от PYTHONHASHSEED, и хэш производного файла в
        # sources.json давал бы ложную тревогу при каждой пересборке.
        for per in sorted(set(h_pl) | set(h_cf)):
            halves.setdefault(per, {})[basis] = {"pl": h_pl.get(per, {}), "cf": h_cf.get(per, {})}
        for per in sorted(set(f_pl) | set(f_cf)):
            fys.setdefault(per, {})[basis] = {"pl": f_pl.get(per, {}), "cf": f_cf.get(per, {})}
        for date, rec in balances(bs, basis).items():
            bal.setdefault(date, {})[basis] = rec
        for q, rec in quarterly(fq, basis).items():
            quarters.setdefault(q, {})[basis] = rec
    # производные строки IAS 17: аренда = помещения + земля/оборудование
    for coll in (halves, fys):
        for per, d in coll.items():
            x = d.get("ias17", {}).get("pl", {})
            if "lease_premises" in x and "lease_land_equipment" in x and x["lease_premises"]["v"] is not None:
                x["lease_expense"] = {"v": r6(x["lease_premises"]["v"] + x["lease_land_equipment"]["v"]),
                                      "src": f"{x['lease_premises']['src']} + {x['lease_land_equipment']['src']}", "kind": "calc"}
    halves = {p: halves[p] for p in HALVES if p in halves}
    quarters = {q: quarters[q] for q in QUARTERS if q in quarters}
    debt_hist = debt_sheet(wb["Debt"])
    ops = operating(wb["Operating Results"])

    # IFRS16 блок против PDF
    h16 = {p: {**d.get("ifrs16", {}).get("pl", {}), **{("cf_" + k): v for k, v in d.get("ifrs16", {}).get("cf", {}).items()}}
           for p, d in halves.items()}
    b16 = {dt: d.get("ifrs16", {}) for dt, d in bal.items()}
    ver = verify_ifrs16(h16, b16)
    n_found = sum(1 for c in ver if c["found_pages"])
    log(f"[ifrs16↔PDF] проверено чисел: {len(ver)}, найдено в первичных формах: {n_found}")

    # --- сегментная выручка по полугодиям (лист Financials quarterly, розница по форматам)
    def qsum(q1, q2, key, basis="ias17"):
        a = quarters.get(q1, {}).get(basis, {}).get(key)
        b = quarters.get(q2, {}).get(basis, {}).get(key)
        if not a or not b or a["v"] is None or b["v"] is None:
            return None, None
        return r6(a["v"] + b["v"]), f"{a['src']} + {b['src'].split('!')[1]}"

    seg_halves = {}
    for y in range(2020, 2027):
        for h, (q1, q2) in ((1, (f"{y}Q1", f"{y}Q2")), (2, (f"{y}Q3", f"{y}Q4"))):
            per = f"{y}H{h}"
            if per not in HALVES:
                continue
            rec = {}
            for key in ("revenue", "retail", "wholesale", "seg_hyper_db", "seg_super", "seg_convenience", "seg_utkonos",
                        "seg_droge", "seg_remi", "seg_diy", "seg_other_formats"):
                v, s = qsum(q1, q2, key)
                if v is not None:
                    rec[key] = {"v": v, "src": s, "kind": "calc"}
            if rec:
                seg_halves[per] = rec

    # --- accounting_base.json
    data_issues = [
        {"id": "DB-1", "where": "CF IAS 17 FY2019", "what": "строки «Interest paid» (+3,81) и «Interest received» (−12,87) перепутаны местами", "source": "research/02 §5.3", "impact": "проценты по аренде 2019 по разнице баз"},
        {"id": "DB-2", "where": "CF IAS 17 1П2020, FY2020, 1П2021 «Depreciation and amortization»", "what": "0,045 / 0,067 / 0,340 млрд вместо ≈7,4 / 14,7 / 7,6; верные значения — строка P&L «Depreciation»", "source": "research/02 §5.3", "impact": "D&A IAS 17 за эти периоды брать только из PL"},
        {"id": "DB-3", "where": "PL IFRS 16 FY2022 курсовые разницы", "what": "162 122 против 163 122 тыс. ₽ в КФО 2023 с. 8 (опечатка на 1 млн)", "source": "research/02 §5.3", "impact": "нет"},
        {"id": "DB-4", "where": "лист Debt", "what": "2010–2019 в тыс. ₽, с 2020 — в млн ₽ в одном листе; ND/EBITDA МСФО 16 заполнен только за 2019", "source": "research/02 §5.3; этот скрипт (debt_sheet)", "impact": "приводится к млрд"},
        {"id": "DB-5", "where": "BS строка «Restricted cash» 1П2026 = 2,471 и FY2025 = 10,662", "what": "в первичке на 30.06.2026 ограниченных денежных средств нет; 2 471 493 — «Компенсирующий актив» (признан при покупке «Реми»), на 31.12.2025 — он же + аккредитив 8 191 000", "source": "IFRS_1H2026 прим. 17 с. 29; IFRS_FY2025 прим. 8 с. 48", "impact": "строка моста D13 «ограниченные средства 2,47» — не деньги, а компенсирующий актив"},
        {"id": "DB-6", "where": "Financials quarterly, блок IFRS 16", "what": "выручка 1кв2022 = 118 237,5 (копия 3кв2021) против 132 370 в блоке IAS 17; 3–4 кв. 2021 и 2 кв. 2022 — сегменты расходятся между блоками при равной выручке", "source": "этот скрипт (check_facts.py, тождество «выручка IAS 17 = МСФО 16»)", "impact": "квартальные ряды брать из блока IAS 17"},
        {"id": "DB-7", "where": "Financials quarterly против Operating Results", "what": "выручка 4кв2024 263 761 против 263 749,4; 4кв2025 322 226 против 322 261,4; розница 3кв2024 209 274,1 против 209 277,3", "source": "этот скрипт", "impact": "≤0,04 млрд; сегменты — из Financials quarterly"},
        {"id": "DB-8", "where": "Operating Results, гипермаркеты 2кв2026", "what": "346 магазинов = 270 + 75 «О’КЕЙ» + 2 чистых открытия − 1 (расхождение в один магазин); прирост площади 486,2 тыс. м² против 478 тыс. м² «О’КЕЙ» в прим. 5", "source": "этот скрипт", "impact": "площадь сегментов hyper/okey — расчёт (лист network)"},
        {"id": "DB-9", "where": "Financials quarterly «Remi» 4кв2025", "what": "5 170 млн против вклада 5 100 086 тыс. ₽ в МСФО 2025 прим. 8 с. 49", "source": "этот скрипт", "impact": "0,07 млрд"},
        {"id": "PR-1", "where": "IFRS_FY2025 прим. 8 с. 48", "what": "пут «Реми» записан как «4 766 102 млн руб.» — опечатка, тыс. ₽ (баланс 1П2026 с. 6)", "source": "текст отчёта", "impact": "нет"},
        {"id": "PR-2", "where": "пресс-релиз 2 кв. 2026 с. 8", "what": "валовой долг МСФО 16 301 549 при сумме строк 301 849", "source": "research/04 §2.1", "impact": "нет"},
        {"id": "DB-11", "where": "Financials quarterly «Other formats» 2–3 кв. 2024", "what": "0 и 0 при 29,4 и 72,1 млн ₽ в Operating Results («Вингараж»); итог квартального листа 2023Q4, 2024Q3, 2024Q4, 2025Q4 не равен сумме розница + опт (≤0,07 млрд)", "source": "этот скрипт (check_facts.py §3)", "impact": "≤0,07 млрд; полугодовая выручка берётся из PL"},
        {"id": "DB-12", "where": "CF МСФО 16 1П2025, изменения ОК", "what": "дебиторка 1,442019 и кредиторка −18,467330 в датабуке против 1,369112 и −18,430811 в сравнительных данных МСФО 6М2026 (и не найдены в МСФО 6М2025) — датабук переклассифицировал статьи", "source": "этот скрипт (ifrs16_vs_pdf)", "impact": "≤0,07 млрд; ОК брать из баланса"},
        {"id": "DB-13", "where": "PL МСФО 16 1П2026", "what": "амортизация 26 584 059 и EBITDA 58 289 516 против 26 584 058 и 58 289 515 в МСФО (округление на 1 тыс. ₽)", "source": "этот скрипт", "impact": "нет"},
        {"id": "DB-10", "where": "BS IAS 17 против МСФО 16 на 30.06.2026", "what": "кредиторка 157,603 против 157,281; НДУ 1,089 против 0,992 — управленческая база отличается от отчётной в строках баланса", "source": "этот скрипт", "impact": "для моста — НДУ по МСФО (прим.); NWC — по одной базе на выбор листа nwc"},
    ]
    acc = OrderedDict([
        ("schema", "lenta-facts-accounting-base-v1"),
        ("generated_by", "evidence/book-1.0/facts/build_facts.py"),
        ("unit", "млрд ₽ (из тыс. ₽ датабука ×1e-6; квартальный лист — из млн ₽ ×1e-3); знак как в отчётности: расходы и оттоки отрицательные"),
        ("source_doc", {"id": "DATABOOK_Q2_2026", **sources["DATABOOK_Q2_2026"]}),
        ("src_convention", "«PL!AE10» — лист!ячейка датабука; для 2П: «FY − 1П» (kind: calc); сегменты полугодий — сумма кварталов листа Financials quarterly"),
        ("bases", {"ias17": "до МСФО 16 (управленческая с 2019; 2018 — аудированная IAS 17); база книги (D1)",
                   "ifrs16": "отчётная МСФО 16 с 2019; для 2018 блока нет (МСФО 16 применён с 01.01.2019 без пересчёта)"}),
        ("definitions", {
            "ebitda_pre16": "операционная прибыль до обесценения + амортизация (IAS 17); аренда — расход в SG&A; тождество проверяет check_facts.py (исключение FY2025: +0,039 млрд в обеих базах)",
            "lease_expense_pre16": "аренда помещений + аренда земли и оборудования (IAS 17), строки PL",
            "net_debt_pre16": "долгосрочные + краткосрочные кредиты и займы − денежные средства и эквиваленты; ограниченные средства и компенсирующий актив не вычитаются; аренда не входит",
            "capex_cash": "приобретение ОС + НМА + права аренды по ДДС (IAS 17 — строки блока IAS 17)",
            "fcf_company": "чистый ОДП + чистый ИДП (IAS 17), после M&A — определение компании",
        }),
        ("halves", halves),
        ("fy", fys),
        ("balance", {d: bal[d] for d in sorted(bal)}),
        ("quarters", quarters),
        ("segment_revenue_halves_ias17", seg_halves),
        ("debt_sheet", debt_hist),
        ("operating", ops),
        ("ifrs16_vs_pdf", {"checked": len(ver), "found": n_found, "checks": ver}),
        ("data_issues", data_issues),
    ])
    out = facts_out_dir()
    dump_json(acc, out / "accounting_base.json")

    # удобные ссылки
    def H(per, basis, part, key):
        return halves[per][basis][part][key]

    def B(date, basis, key):
        return bal[date][basis][key]

    # --- shares.json
    iss = json.loads((P / doc_path("ISS_LENT_SEC")).read_text(encoding="utf-8"))
    iss_desc = {r[0]: r for r in iss["description"]["data"]}
    cols = iss["description"]["columns"]
    iss_map = {row[cols.index("name")]: row[cols.index("value")] for row in iss["description"]["data"]}
    shares = OrderedDict([
        ("schema", "lenta-facts-shares-v1"), ("as_of", "2026-06-30"),
        ("issued", {"v": N("shares_issued")["th"], "src": ref("shares_issued") + " (прим. 18)", "kind": "fact",
                    "crosscheck": {"iss_issuesize": int(iss_map["ISSUESIZE"]), "src": "ISS_LENT_SEC description.ISSUESIZE (28.09.2026)"}}),
        ("quasi_treasury", {"v": N("shares_treasury")["th"], "src": ref("shares_treasury") + " (прим. 18)", "kind": "fact",
                            "note": "выкуп ГДР 10.2018–02.04.2019; держатель — дочерняя компания; по себестоимости 1 011 190 тыс. ₽ (баланс с. 6)"}),
        ("outstanding", {"v": N("shares_wavg")["th"], "src": ref("shares_wavg") + " (прим. 19, средневзвешенное для EPS)", "kind": "fact",
                         "identity": "issued − quasi_treasury", "shares_out_mln": round(N("shares_wavg")["th"] / 1e6, 6)}),
        ("eps_check", {"profit_parent_th": N("pl_profit_parent_2026h1")["th"], "eps_reported_th_per_share": 0.092,
                       "src": "IFRS_1H2026 с. 7 и 30", "note": "10 612 827 / 115 074 675 = 0,0922 тыс. ₽ — воспроизводит отчётный EPS 0,092; 115 985 197 дал бы 0,0915"}),
        ("authorised", {"v": N("shares_authorised")["th"], "src": ref("shares_authorised"), "unissued": N("shares_authorised")["th"] - N("shares_issued")["th"],
                        "note": "запас для допэмиссии без решения об увеличении объявленных"}),
        ("voting_at_agm_2026", {"v": "111 104 354 2/5", "src": ref("agm_voting_shares") + " (голосующие на дату фиксации 02.06.2026)",
                                "dr_program_non_voting": {"v": 4880842.6, "kind": "calc", "note": "115 985 197 − 111 104 354,4 = 4 880 842,6: акции на счёте депозитарных программ (без голоса по 114-ФЗ) — экономически существуют и остаются в знаменателе (D8)"}}),
        ("face_value_rub", {"v": 0.0912632, "src": "IFRS_1H2026 с. 29 (прим. 18)"}),
        ("isin", iss_map.get("ISIN")), ("list_level", {"v": int(iss_map["LISTLEVEL"]), "src": "ISS_LENT_SEC LISTLEVEL; третий уровень с 12.08.2026"}),
        ("dividends", {"declared_1H2026": False, "src": "IFRS_1H2026 с. 29 (прим. 18): «дивиденды не объявлялись»"}),
        ("dilutive_instruments", {"v": "нет", "note": "LTIP денежный (прим. 22: обязательство перед персоналом); опционов на акции и конвертируемых нет (research/01 §0)"}),
    ])
    dump_json(shares, out / "shares.json")

    # --- debt.json
    debt_gross = nv("bs_lt_debt_2026h1") + nv("bs_st_debt_2026h1")
    cash = nv("bs_cash_2026h1")
    fixed_principal = nv("d_st_fixed") + nv("d_lt_fixed")
    float_principal = nv("d_st_float") + nv("d_lt_float")
    bonds_carrying = nv("d_st_bonds") + nv("d_lt_bonds")
    accrued = nv("d_st_fixed_int") + nv("d_lt_fixed_int_in_st") + nv("d_st_float_int") + nv("d_st_bonds_int") + nv("d_lt_fixed_int")
    principal = fixed_principal + float_principal + bonds_carrying
    bonds = []
    for did, name, coupon, put, mat, place in (
            ("OKEY_B1P6", "О'КЕЙ БО 001Р-06", 0.155, "2027-03-17", "2034-03-07", "2024-03-29"),
            ("OKEY_B1P8", "О'КЕЙ БО 001Р-08", 0.169, "2027-02-09", "2028-07-28", "2025-08-13"),
            ("OKEY_B1P2", "О'КЕЙ БО 001Р-02", 0.100, None, "2029-04-06", "2019-04-19"),
            ("OKEY_B1P4", "О'КЕЙ БО 001Р-04", 0.099, None, "2030-10-25", "2020-11-06")):
        d = json.loads((P / doc_path(did)).read_text(encoding="utf-8"))
        desc = d["description"]
        m = {row[desc["columns"].index("name")]: row[desc["columns"].index("value")] for row in desc["data"]}
        bonds.append({"isin": m.get("ISIN") or m.get("SECID"), "name": name, "issuer": "ООО «О’КЕЙ»",
                      "iss_issuesize_units": int(float(m.get("ISSUESIZE"))) if m.get("ISSUESIZE") else None,
                      "iss_matdate": m.get("MATDATE"), "iss_issuedate": m.get("ISSUEDATE"),
                      "coupon_rate": coupon, "next_put": put, "maturity": mat, "placement": place,
                      "src": f"{did} ({doc_path(did)}); купон и оферты — research/04 §4.1 (ISS bondization)"})
    tranches = [
        {"id": "LENTA-FIXED", "what": "кредиты и займы группы «Лента» по фиксированной ставке (без периметра «О’КЕЙ»)", "rate": "fixed",
         "principal": r6(fixed_principal), "st": nv("d_st_fixed"), "lt": nv("d_lt_fixed"),
         "accrued_interest": r6(nv("d_st_fixed_int") + nv("d_lt_fixed_int_in_st") + nv("d_lt_fixed_int")),
         "includes": "займ материнской компании (Севергрупп) 30,5 млрд до 28.05.2029 — «Кредитор 3»",
         "src": "IFRS_1H2026 прим. 20 с. 31", "kind": "fact"},
        {"id": "LENTA-SEVERGROUP-2029", "what": "долгосрочный заём от материнской компании (внутри LENTA-FIXED; не суммировать повторно)", "rate": "fixed, не раскрыта",
         "principal": nv("rp_parent_loan"), "accrued_interest": nv("rp_parent_loan_interest"), "from": "2026-05-25", "to": "2029-05-28",
         "src": "IFRS_1H2026 прим. 3 с. 14 (заём и проценты 262 932); даты — отчёт эмитента 6М2026 п. 1.7.1 (Кредитор 3: 30 762 932 000 ₽)",
         "kind": "fact", "rate_note": "ставка не раскрыта; ниже рыночной — суждение листа financing (D12: ≈8,7–10,8 %)"},
        {"id": "FLOAT-ST-5000", "what": "краткосрочный кредит по плавающей ставке (заёмщик не раскрыт)", "rate": "floating",
         "principal": nv("d_st_float"), "src": "IFRS_1H2026 прим. 20 с. 31", "kind": "fact"},
        {"id": "OKEY-ALFA-2031", "what": "кредит АО «АЛЬФА-БАНК» ООО «О’КЕЙ», обеспеченный", "rate": "floating (ключевая + спред, спред не раскрыт)",
         "principal": nv("d_lt_float"), "to": "2031-12-31",
         "collateral": "имущество 23 275 427 тыс. ₽, 99 % доли ООО «О’КЕЙ», 100 % акций АО «Доринда» (прим. 20 с. 32); поручительство «Доринды» — отчёт эмитента п. 1.7.2",
         "src": "IFRS_1H2026 прим. 20 с. 31–32; ISSUER_REPORT_6M2026_EXTRACT (Альфа-Банк 40 783 420 000 ₽ всего)", "kind": "fact"},
        {"id": "OKEY-BONDS-ST", "what": "облигации «О’КЕЙ» Б1Р6 + Б1Р8 под пут-оферты 17.03.2027 и 09.02.2027", "rate": "fixed 15,5 % / 16,9 %",
         "principal": nv("d_st_bonds"), "src": "IFRS_1H2026 прим. 20 с. 31; ISS", "kind": "fact"},
        {"id": "OKEY-BONDS-LT", "what": "облигации «О’КЕЙ» Б1Р2 + Б1Р4 у третьих лиц (балансовая)", "rate": "fixed 10,0 % / 9,9 %",
         "principal": nv("d_lt_bonds"), "to": "2029-04-06 / 2030-10-25", "src": "IFRS_1H2026 прим. 20 с. 31", "kind": "fact",
         "note": "ISS ISSUESIZE 5 000 000 шт. по каждому — объём выпуска, не остаток (урок 850oa Д4)"},
    ]
    creditors_txt = text_file(doc_path("ISSUER_REPORT_6M2026_EXTRACT"))
    cred_ok = {k: (v in numbers_in(creditors_txt)) for k, v in (("creditor1", 54170424000), ("alfa", 40783420000), ("creditor3", 30762932000), ("guarantees", 56850769))}
    debt = OrderedDict([
        ("schema", "lenta-facts-debt-v1"), ("as_of", "2026-06-30"), ("unit", "млрд ₽"),
        ("basis", "до МСФО 16: только кредиты, займы и облигации; аренда — отдельно, в долг книги не входит (D1)"),
        ("reported", {"lt": nv("bs_lt_debt_2026h1"), "st": nv("bs_st_debt_2026h1"), "gross": r6(debt_gross),
                      "principal": r6(principal), "accrued_interest": r6(accrued),
                      "fixed_principal": r6(fixed_principal), "float_principal": r6(float_principal), "bonds_carrying": r6(bonds_carrying),
                      "src": "IFRS_1H2026 с. 6, прим. 20 с. 31"}),
        ("cash", {"v": cash, "src": ref("bs_cash_2026h1"), "components": {"deposits": nv("cash_deposits"), "bank": nv("cash_bank"),
                                                                        "in_transit": nv("cash_transit"), "in_hand": nv("cash_hand"),
                                                                        "src": "IFRS_1H2026 прим. 16 с. 28"}}),
        ("net_debt_pre16", {"v": r6(debt_gross - cash), "identity": "lt + st − cash",
                            "databook": debt_hist["ias17"].get("2026-06-30", {}).get("net_debt"),
                            "nd_ebitda_databook": debt_hist["ias17"].get("2026-06-30", {}).get("nd_ebitda")}),
        ("shares_of_principal", {"fixed_incl_bonds": round((fixed_principal + bonds_carrying) / principal, 4),
                                 "floating": round(float_principal / principal, 4), "kind": "calc"}),
        ("tranches", tranches),
        ("bonds_okey", bonds),
        ("undrawn_credit_lines", {"v": nv("undrawn_2026h1"), "as_of": "2026-06-30", "src": ref("undrawn_2026h1") + " (прим. 20; также прим. 2.1 с. 12)",
                                  "prior": {"2025-12-31": nv("undrawn_2025fy")}, "limit_same_date": r6(debt_gross + nv("undrawn_2026h1"))}),
        ("fair_value_note32", {"fixed": {"book": nv("bv_fixed"), "fair": nv("fv_fixed"), "fair_minus_book": r6(nv("fv_fixed") - nv("bv_fixed"))},
                               "floating": {"book": nv("bv_float"), "fair": nv("fv_float_l2"), "fair_minus_book": r6(nv("fv_float_l2") - nv("bv_float"))},
                               "total": {"book": nv("bv_total"), "fair": nv("fv_total")},
                               "levels": {"fixed_loans_l2": nv("fv_fixed_loans_l2"), "bonds_l1": nv("fv_bonds_l1")},
                               "prior_2025_12_31": {"fixed_book": nv("bv_fixed_2025"), "fixed_fair": nv("fv_fixed_2025"),
                                                    "float_book": nv("bv_float_2025"), "float_fair": nv("fv_float_2025")},
                               "src": "IFRS_1H2026 прим. 32 с. 38"}),
        ("interest_1H2026", {"loans": nv("int_loans_2026h1"), "lease": nv("int_lease_2026h1"), "put_unwind_other": nv("int_other_2026h1"),
                             "total": nv("int_total_2026h1"), "income": {"deposits": nv("inc_deposits_2026h1"), "loans_issued": nv("inc_loans_2026h1"), "other": nv("inc_other_2026h1")},
                             "pre16_databook": {"interest_expense": H("2026H1", "ias17", "pl", "interest_expense")["v"],
                                                "interest_income": H("2026H1", "ias17", "pl", "interest_income")["v"],
                                                "src": f'{H("2026H1", "ias17", "pl", "interest_expense")["src"]}, {H("2026H1", "ias17", "pl", "interest_income")["src"]}'},
                             "net_interest_pre16_clean": r6(-H("2026H1", "ias17", "pl", "interest_expense")["v"] - nv("int_other_2026h1") - H("2026H1", "ias17", "pl", "interest_income")["v"]),
                             "net_interest_pre16_clean_formula": "7,745547 (IAS 17) − 0,448072 (размотка пута) − 2,654201 (доходы IAS 17) = 4,643274",
                             "note": "очищенные чистые проценты 1П2026 до МСФО 16: «прочие» 0,448 — размотка пута «Реми», не проценты (урок 850oa Д3); доход IAS 17 меньше МСФО 16 на 0,013834 — дисконт обеспечительных депозитов аренды (прим. 7 с. 21)",
                             "src": "IFRS_1H2026 прим. 29–30 с. 36–37"}),
        ("rate_sensitivity", {"up200_bp_pnl_th": N("rate_sens_up200")["th"], "src": ref("rate_sens_up200"),
                              "note": "посчитано за июнь по кредиту «О’КЕЙ» (≈32 000 × 2 % × 30/365); знак в отчёте, вероятно, перевёрнут; годовой эффект на 37,0 плавающих ≈0,37 млрд до налога на 1 п.п. (расчёт)"}),
        ("creditors_issuer_report", {"creditor1": 54.170424, "alfa_bank": 40.78342, "creditor3_severgroup": 30.762932, "guarantees_issued": 56.850769,
                                     "verified_in_text": cred_ok, "src": "ISSUER_REPORT_6M2026_EXTRACT п. 1.7.1–1.7.2",
                                     "maturity_profile_presentation": {"2026": 0.35, "2027": 0.36, "2029": 0.30, "src": "Q2-2026-Lenta-Investor-Presentation_eng_vFF.pdf с. 25 (research/04 §5.6)",
                                                                       "reconstruction": "сходится на базе собственных кредитов без «О’КЕЙ» ≈102,8: 35,66 (≤12 мес.) / ≈36,98 (2027) / 30,5 (05.2029) — гипотеза, компания базу не называет", "kind": "calc"}}),
        ("okey_assumed_debt", {"lt": nv("okey_lt_debt"), "st": nv("okey_st_debt"), "total": r6(nv("okey_lt_debt") + nv("okey_st_debt")),
                               "seller_loan_offset": nv("okey_seller_loan_payable"), "after_offset": r6(nv("okey_lt_debt") + nv("okey_st_debt") - nv("okey_seller_loan_payable")),
                               "src": "IFRS_1H2026 прим. 5 с. 17–18"}),
        ("history_ias17", debt_hist["ias17"]),
    ])
    dump_json(debt, out / "debt.json")

    # --- bridge_balance.json
    ltip_2025 = r6(nv("bs_other_ltl_2025fy") - nv("fy25_remi_put"))
    nci_remi_track = r6(nv("fy25_remi_nci") - nv("fy25_nci_loss") + nv("pl_nci_profit_2026h1"))
    loans_recon = r6(nv("rp_assignment_2026h1") - nv("obi_offset") + (nv("inc_loans_2026h1") - nv("rp_interest_2026h1"))
                     - (nv("okey_seller_loan_payable") - nv("okey_seller_loan_offset")))
    bridge = OrderedDict([
        ("schema", "lenta-facts-bridge-balance-v1"), ("as_of", "2026-06-30"), ("unit", "млрд ₽"),
        ("purpose", "Строки баланса, относящиеся к мосту EV → капитал (D13). Только факты и прямые расчёты по ним; haircut, ставки и решения — в листе financing-bridge."),
        ("remi_put", {"amount": nv("pay_option"), "src": ref("pay_option") + " (прим. 22: строка «Кредиторская задолженность по опциону» внутри торговой и прочей кредиторки 157,281)",
                      "where": "краткосрочная, внутри «Торговая и прочая кредиторская задолженность» (прим. 22); на 31.12.2025 — в «Прочих долгосрочных обязательствах»",
                      "prior_2025_12_31": nv("fy25_remi_put"), "prior_src": ref("fy25_remi_put") + " (прим. 8)",
                      "accretion_1H2026": nv("int_other_2026h1"), "accretion_src": ref("int_other_2026h1") + " (прим. 29, «Прочие» процентные расходы)",
                      "accretion_rate_half": round(nv("int_other_2026h1") / nv("fy25_remi_put"), 6),
                      "identity": "4,766102 + 0,448072 = 5,214174", "underlying": "33 % ООО «Продукт-Эконом» («Реми»); встречный колл у группы",
                      "exercise_date": "не раскрыта; классификация «краткосрочная» на 30.06.2026 ⇒ ожидание исполнения ≤ 12 мес.",
                      "equity_side": "прочие капитальные резервы −4,766102 (баланс с. 6)", "kind": "fact"}),
        ("obi_remaining_payment", {"amount": r6(nv("obi_consideration") - nv("obi_cash_paid") - nv("obi_offset")),
                                   "identity": "17,303283 − 4,498910 − 8,734162", "src": "IFRS_1H2026 прим. 5 с. 19 (цитата «Оставшаяся задолженность будет погашена в 2026 году»)",
                                   "where": "не раскрыто отдельно; вероятно внутри «Кредиторской задолженности за ОС, НМА и приобретения бизнеса» 9,119601 (прим. 22)",
                                   "kind": "calc"}),
        ("ltip", {"amount_lt_2026h1": nv("bs_other_ltl_2026h1"), "src": ref("bs_other_ltl_2026h1") + " + прим. 22 с. 33 (прочие долгосрочные = долгосрочная часть LTIP)",
                  "amount_lt_2025fy": ltip_2025, "src_2025": "6,531216 (прочие долгосрочные 31.12.2025) − 4,766102 (пут «Реми») — расчёт",
                  "kmp_lt_accrued_1H2026": nv("rp_kmp_lt_2026h1"), "kmp_src": ref("rp_kmp_lt_2026h1") + " (прим. 3)",
                  "short_term_part": "не раскрыта; внутри «Кредиторской задолженности перед персоналом» 10,139199", "kind": "fact"}),
        ("nci", {"balance_2026h1": nv("bs_nci_2026h1"), "balance_2025fy": nv("bs_nci_2025fy"), "src": ref("bs_nci_2026h1"),
                 "remi_track": {"at_acquisition": nv("fy25_remi_nci"), "loss_share_2025": -nv("fy25_nci_loss"), "profit_share_1H2026": nv("pl_nci_profit_2026h1"),
                                "implied_2026h1": nci_remi_track, "src": "IFRS_FY2025 прим. 8 с. 48 и ОПУ с. 7; IFRS_1H2026 с. 7"},
                 "nci_ex_remi": r6(nv("bs_nci_2026h1") - nci_remi_track),
                 "note": "вся НДУ — 33 % «Реми» (0,978523 − 0,042919 + 0,056103 = 0,991707); у «Дом Ленты» НДУ в распределении цены нет (прим. 5 с. 19); строка НДУ моста без «Реми» = 0",
                 "ias17_databook_2026h1": B("2026-06-30", "ias17", "nci")["v"], "kind": "fact"}),
        ("loans_issued", {"amount": nv("bs_loans_issued_2026h1"), "src": ref("bs_loans_issued_2026h1") + " (баланс; прим. 32 с. 37 — финактив по амортизированной стоимости)",
                          "related_parties_2026h1": 0.0, "related_src": "IFRS_1H2026 прим. 3 с. 14: «Краткосрочные займы выданные – 11 533 698»",
                          "prior_2025_12_31": nv("bs_loans_issued_2025fy"),
                          "reconstruction": {"v": loans_recon,
                                             "formula": "уступленные займы под общим контролем 11,875152 − зачёт в оплату «ОБИ» 8,734162 + проценты по несвязанным займам (0,579220 − 0,341454) − проценты по займу продавцу «О’КЕЙ» (8,459326 − 8,419675)",
                                             "residual_vs_balance": r6(nv("bs_loans_issued_2026h1") - loans_recon),
                                             "reading": "остаток 3,34 — вероятно, требование к лицу, получившему уступку займов под общим контролем, после зачёта в цену «ОБИ» (схема «заём продавцу до сделки»); контрагент не раскрыт — гипотеза",
                                             "kind": "calc"}, "kind": "fact"}),
        ("restricted_cash", {"amount_2026h1": 0.0, "src": "IFRS_1H2026 прим. 17 с. 29 (аккредитив «Реми»: «–» на 30.06.2026, 8,191 на 31.12.2025 — переведён продавцу)",
                             "prior_2025_12_31": nv("restricted_lc_2025fy"), "kind": "fact"}),
        ("indemnification_asset", {"amount": nv("indemnification_asset"), "src": ref("indemnification_asset") + " (прим. 17: «Компенсирующий актив»); признан при покупке «Реми» — IFRS_FY2025 прим. 8 с. 48",
                                   "matched_liabilities_at_acq": {"other_taxes": nv("fy25_remi_tax_other"), "income_tax": nv("fy25_remi_tax_income"), "src": "IFRS_FY2025 прим. 8 с. 48"},
                                   "databook_label": "в датабуке — строка «Restricted cash» (DB-5)",
                                   "reading": "актив возмещения продавцом (МСФО 3) под налоговые обязательства «Реми»; денег нет; он погашает обязательство, которое сидит в «прочих налогах»/налоге на прибыль (внутри ОК). В мосте как деньги не считать; решение — лист financing-bridge/nwc",
                                   "kind": "fact"}),
        ("lease_liabilities", {"lt": nv("bs_lt_lease_2026h1"), "st": nv("bs_st_lease_2026h1"), "total": nv("lease_liab_2026h1"),
                               "src": "IFRS_1H2026 с. 6, прим. 7 с. 21", "treatment": "не долг книги (D1); только сверка баз"}),
        ("fixed_debt_fair_minus_book", {"amount": r6(nv("fv_fixed") - nv("bv_fixed")), "floating": r6(nv("fv_float_l2") - nv("bv_float")),
                                        "src": "IFRS_1H2026 прим. 32 с. 38", "sign": "справедливая > балансовой ⇒ требование (купоны выше рынка на 30.06.2026)", "kind": "fact"}),
        ("payables_note22", {"trade": nv("pay_trade"), "capex_and_business": nv("pay_capex_business"), "accrued_other": nv("pay_accrued"),
                             "personnel": nv("pay_personnel"), "option_put": nv("pay_option"), "factoring": nv("pay_factoring"),
                             "total": nv("bs_payables_2026h1"), "src": "IFRS_1H2026 прим. 22 с. 33",
                             "prior_2025_12_31": {"trade": nv("pay_trade_2025"), "capex_and_business": nv("pay_capex_business_2025"), "total": nv("bs_payables_2025fy")}}),
        ("undrawn_credit_lines", {"v": nv("undrawn_2026h1"), "src": ref("undrawn_2026h1")}),
        ("parent_loan", {"principal": nv("rp_parent_loan"), "interest_accrued": nv("rp_parent_loan_interest"), "src": ref("rp_parent_loan"),
                         "treatment": "это долг (внутри ЧД 117,41), не утечка (D5)"}),
        ("contingencies", {"litigation_max": nv("litigation_max"), "tax_risk_max_pct_revenue_1H": 0.051,
                           "tax_risk_max_rub": r6(0.051 * nv("pl_revenue_2026h1")), "src": "IFRS_1H2026 прим. 33 с. 40",
                           "note": "налоговый риск «без учёта возможных компенсаций от продавцов приобретаемых компаний»; не обязательство, в мост не входит"}),
        ("capex_commitments", {"v": nv("capex_commitments"), "src": ref("capex_commitments") + " (прим. 31)"}),
        ("dividends_declared_unpaid", {"v": 0.0, "src": "IFRS_1H2026 прим. 18 с. 29"}),
    ])
    dump_json(bridge, out / "bridge_balance.json")

    # --- inorganic.json
    inorganic = OrderedDict([
        ("schema", "lenta-facts-inorganic-v1"), ("unit", "млрд ₽"),
        ("purpose", "Периметр M&A 2021–2026: дата контроля, цена, принятый долг, гудвил, вклад с даты покупки и проформа, если раскрыта. Нужен для органических эталонов журнала, баз сегментов и исключений в σ/ρ A-P2u."),
        ("deals", [
            {"id": "billa", "name": "«Билла Россия» (Billa Realty LLC, Billa LLC)", "control": "2021-08 (консолидация с августа 2021)", "format": "супермаркеты, Москва",
             "consideration": nv("fy21_billa_cash"), "paid": "деньги", "assumed_debt": 0.0, "goodwill": nv("fy21_billa_goodwill"),
             "net_assets": nv("fy21_billa_net_assets"), "contribution": {"period": "08–12.2021", "revenue": nv("fy21_billa_rev"), "pbt": -nv("fy21_billa_pbt")},
             "pro_forma": "не раскрыта («not practicable»)", "src": "IFRS_FY2021 прим. 8 с. 47–48", "related_party": False},
            {"id": "semya", "name": "«Семья» (Пермь)", "control": "2021-09", "format": "гипер/супер/у дома",
             "consideration": nv("fy21_semya_cash"), "paid": "деньги", "assumed_debt": nv("fy21_semya_debt"), "goodwill": nv("fy21_semya_goodwill"),
             "contribution": {"period": "09–12.2021", "revenue": nv("fy21_semya_rev"), "pbt": nv("fy21_semya_pbt")},
             "pro_forma": "не раскрыта", "src": "IFRS_FY2021 прим. 8 с. 48–49", "related_party": False},
            {"id": "utkonos", "name": "«Утконос» (ООО «Новый импульс-50»)", "control": "2022-02 (закрытие; оплата акциями 21.06.2022)", "format": "онлайн",
             "consideration": 20.0, "paid": "18 399 265 новых акций по 1 087 ₽ (зачёт 20 млрд)", "goodwill": nv("fy23_utkonos_goodwill"),
             "goodwill_impaired_same_year": nv("fy23_utkonos_goodwill"),
             "trademark_impairment": {"2022": nv("fy23_utkonos_tm_impair_2022"), "2023": nv("fy23_utkonos_tm_impair_2023")},
             "contribution": "с 01.01.2023 в выручке гипермаркетов (датабук, сноска Operating Results)",
             "src": "IFRS_FY2023_V2 с. 54 (гудвил и товарный знак); цена и акции — SF_2022_06_21 с. 1–2 (18 399 265 × 1 087 ₽; 20 000 000 000 ₽), SF_2021_12_16 с. 2",
             "related_party": True, "related_note": "продавец — ООО «Севергрупп» (контролирующий акционер)"},
            {"id": "monetka", "name": "«Монетка» (Группа Б*, ООО «РМ-Групп»)", "control": "2023-10 (99,9999 %; 0,0001 % — ноябрь 2023)", "format": "магазины у дома",
             "consideration": nv("fy24_monetka_consideration"), "paid": "деньги 76,076 (2023), возврат корректировки 1,5 (2024)",
             "cash_net": nv("fy24_monetka_net_cf"), "refund_2024": nv("fy24_monetka_refund"), "seller_loan_in_assets": nv("fy24_monetka_seller_loan"),
             "goodwill": nv("fy24_monetka_goodwill"), "assumed_debt": 0.0,
             "contribution": {"period": "10–12.2023", "revenue": nv("fy23_monetka_rev"), "pbt": nv("fy23_monetka_pbt")},
             "pro_forma": {"group_revenue_2023": nv("fy23_monetka_pf_group_rev"), "group_pbt_2023": nv("fy23_monetka_pf_group_pbt"),
                           "implied_network_revenue_2023": {"v": r6(nv("fy23_monetka_pf_group_rev") - H("2023H1", "ias17", "pl", "revenue")["v"] - H("2023H2", "ias17", "pl", "revenue")["v"] + nv("fy23_monetka_rev")), "kind": "calc",
                                                            "formula": "проформа группы − отчётная выручка 2023 + вклад с даты покупки"}},
             "src": "IFRS_FY2023_V2 прим. 8 с. 47–49; итоговое распределение — IFRS_FY2024 прим. 8 с. 46", "related_party": False},
            {"id": "ulybka", "name": "«Улыбка радуги» (АО «ТД «Эра»)", "control": "2024-12-01", "format": "дрогери",
             "consideration": nv("fy24_ulybka_consideration"), "paid": "зачёт займа «Улыбки» прежнему владельцу 12,889 + уступка беспроцентного займа группы продавцу 8,046 + 0,003 деньгами (1 кв. 2025)",
             "goodwill": 0.0, "bargain_purchase_gain": nv("fy24_ulybka_gain"), "net_assets": nv("fy24_ulybka_net_assets"),
             "contribution": {"period": "12.2024", "revenue": nv("fy24_ulybka_rev"), "pbt": nv("fy24_ulybka_pbt")},
             "pro_forma": {"network_revenue_2024": nv("fy24_ulybka_pf_rev"), "network_pbt_2024": nv("fy24_ulybka_pf_pbt"),
                           "reading": "текст: «выручка Группы … составила бы 41 566 662» — по величине это выручка сети за год"},
             "normalisation": "доход от выгодной покупки 1,104 (+ прочая 0,011 = 1,115 по ОДДС) в прочих операционных доходах 2024 — разовая статья, из рядов маржи вычитать",
             "src": "IFRS_FY2024 прим. 8 с. 42–45; ОДДС с. 8", "related_party": True, "related_note": "компания под общим контролем конечного бенефициара (уступка займа продавцу)"},
            {"id": "molniya", "name": "«Молния – Spar» (ООО «Молл»)", "control": "2025-06-24", "format": "гипер/супер/у дома, Урал (72 магазина; переведены в «Монетку» и «Ленту»)",
             "consideration": nv("fy25_molniya_consideration"), "paid": "деньги 3,565 + зачёт дебиторки прежнего владельца 1,394",
             "assumed_debt": nv("fy25_molniya_debt"), "goodwill": nv("fy25_molniya_goodwill"),
             "contribution": {"period": "24.06–31.12.2025", "revenue": nv("fy25_molniya_rev"), "pbt": "не раскрыт (магазины переведены в форматы группы)"},
             "pro_forma": {"network_revenue_2025": nv("fy25_molniya_pf_rev")}, "src": "IFRS_FY2025 прим. 8 с. 45–47", "related_party": False},
            {"id": "remi", "name": "«Реми» (67 % ООО «Продукт-Эконом»)", "control": "2025-12-03", "format": "мультиформат, Дальний Восток (115 точек; 118 в датабуке)",
             "consideration": nv("fy25_remi_consideration"), "paid": "аккредитив 8,191 (12.2025, переведён продавцу в 1П2026) + эскроу 2,0 (01.2026)",
             "goodwill": nv("fy25_remi_goodwill"), "nci_at_acquisition": nv("fy25_remi_nci"),
             "put_call_33pct": {"2025-12-31": nv("fy25_remi_put"), "2026-06-30": nv("pay_option")},
             "indemnification_asset": nv("fy25_remi_indemn"),
             "contribution": {"period": "03–31.12.2025", "revenue": nv("fy25_remi_rev"), "pbt": -nv("fy25_remi_pbt")},
             "reported_1H2026_revenue_databook": qsum("2026Q1", "2026Q2", "seg_remi")[0],
             "pro_forma": {"network_revenue_2025": nv("fy25_remi_pf_rev")},
             "lfl": "вне LFL до 12.2026", "src": "IFRS_FY2025 прим. 8 с. 47–49; IFRS_1H2026 прим. 5 с. 19, прим. 22 с. 33", "related_party": False},
            {"id": "other_2025", "name": "прочие приобретения 4 кв. 2025 (в т. ч. «Северсталь Диджитал» — по research/01, не подтверждено)", "control": "2025-Q4",
             "paid_1H2026": nv("other_acq_paid"), "src": "IFRS_1H2026 прим. 5 с. 19", "related_party": "вероятно (research/01 §6)"},
            {"id": "obi_domlenta", "name": "«ОБИ Россия» → «Дом Лента»", "control": "2026-Q1 (поэтапно январь–март 2026)", "format": "DIY (25 магазинов, 263 тыс. м²; 26 и 270,6 тыс. м² в датабуке на 30.06.2026)",
             "consideration": nv("obi_consideration"), "paid": "деньги 4,499 (1П2026) + зачёт займа, выданного третьей стороне, 8,734 + остаток ≈4,070 в 2026",
             "goodwill": nv("obi_goodwill"), "net_assets": nv("obi_net_assets"), "assumed_debt": 0.0, "nci": 0.0,
             "contribution": {"period": "с начала 2026 (с даты консолидации) по 30.06", "revenue": nv("obi_revenue_ytd"), "pbt": -nv("obi_pbt_ytd")},
             "pro_forma": "не раскрыта", "src": "IFRS_1H2026 прим. 5 с. 18–19", "related_party": False},
            {"id": "vingarazh", "name": "«Вингараж» (ультраконвиниенс)", "control": "собственный формат с 2024; сноска презентации 2 кв. 2026 с. 28 — «поэтапное приобретение январь–март 2026»", "format": "в строке «Other formats» датабука",
             "consideration": None, "src": "Lenta_Q2-2026_Investor-Presentation_rus.pdf с. 28; research/03", "note": "противоречие не разрешено; сумма несущественна"},
            {"id": "okey", "name": "Гипермаркеты «О’КЕЙ» (100 % ООО «РБФ-Ритейл»)", "control": "2026-06-02", "format": "гипермаркеты (75, 478 тыс. м², 4 арендованных РЦ 107 тыс. м²)",
             "consideration": nv("okey_consideration"), "paid": "«без денежной составляющей»: 0,0004 за доли + 1,497 за активы магазинов; принят долг",
             "assumed_debt": {"lt": nv("okey_lt_debt"), "st": nv("okey_st_debt"), "total": r6(nv("okey_lt_debt") + nv("okey_st_debt")),
                              "seller_loan_offset": nv("okey_seller_loan_payable"), "note": "заём прежнего владельца 8,459 зачтён с займом группы продавцу 8,420"},
             "assumed_leases": r6(nv("okey_lt_lease") + nv("okey_st_lease")), "net_assets": -nv("okey_net_assets"), "goodwill": nv("okey_goodwill"),
             "working_capital_at_acq": {"inventories": nv("okey_inventories"), "receivables": nv("okey_receivables"), "payables": -nv("okey_payables"),
                                        "simple_nwc": r6(nv("okey_inventories") + nv("okey_receivables") - nv("okey_payables")), "kind": "calc"},
             "ppe_fair_value": nv("okey_ppe"), "rou": nv("okey_rou"),
             "contribution": {"period": "02.06–30.06.2026", "revenue": nv("okey_rev_since_acq"), "pbt": -nv("okey_pbt_since_acq")},
             "pro_forma": {"revenue_1H2026_full": nv("okey_rev_pro_forma"), "pbt_1H2026_full": -nv("okey_pbt_pro_forma"),
                           "reading": "обе фразы прим. 5 описывают вклад «О’КЕЙ»: с даты покупки и с 1 января (D3; рецензии Б1)",
                           "add_to_reported_revenue": r6(nv("okey_rev_pro_forma") - nv("okey_rev_since_acq")),
                           "add_to_reported_pbt": r6(-nv("okey_pbt_pro_forma") + nv("okey_pbt_since_acq")),
                           "quote": quotes["q_okey_note5"]["quote"], "quote_src": "IFRS_1H2026 прим. 5 с. 18"},
             "revenue_2025_press": {"v": 142.0, "src": "PR_OKEY_2026_06_02: «выручка которой составила 142 млрд рублей в 2025 году»"},
             "guidance_2026": "ЧД/EBITDA ≈1,0х на конец 2026, рентабельность EBITDA не менее 7 % (PR_OKEY_2026_06_02)",
             "density_k_rub_per_m2": {"okey_2025": round(142.0 / 478.0 * 1000, 1),
                                      "lenta_hyper_2025": round(sum(quarters[q]["ias17"]["seg_hyper_db"]["v"] for q in ("2025Q1", "2025Q2", "2025Q3", "2025Q4")) / (ops["area_sqm"]["2025Q4"]["hyper_db"]["v"] / 1e6), 1),
                                      "kind": "calc", "note": "выручка года / торговая площадь на конец года, тыс. ₽/м²"},
             "src": "IFRS_1H2026 прим. 5 с. 16–18, прим. 20 с. 31–32; PR_OKEY_2026_06_02", "related_party": False},
        ]),
        ("acquisition_cash_1H2026", {"okey_net": nv("okey_net_cf"), "obi_cash": nv("obi_cash_paid"), "remi_escrow": nv("remi_escrow_paid"),
                                     "other": nv("other_acq_paid"), "total": r6(nv("okey_net_cf") + nv("obi_cash_paid") + nv("remi_escrow_paid") + nv("other_acq_paid")),
                                     "cf_line": nv("cf_acq_2026h1"), "src": "IFRS_1H2026 с. 8, прим. 5 с. 17, 19"}),
        ("goodwill_bridge_1H2026", {"opening": nv("bs_goodwill_2025fy"), "okey": nv("gw_okey"), "domlenta": nv("gw_domlenta"),
                                    "closing": nv("bs_goodwill_2026h1"), "by_cgu": {k: nv("gw_" + k) for k in ("monetka", "billa", "semya", "molniya", "remi", "domlenta", "okey", "other")},
                                    "src": "IFRS_1H2026 с. 6, прим. 11 с. 26"}),
        ("mna_cash_channels", "деньги на M&A идут тремя строками: «приобретение дочерних», «займы выданные» (продавцам до закрытия, потом зачёт: «Улыбка» 12,9/8,0, «О’КЕЙ» 8,42, «ОБИ» 8,73) и «перевод в ограниченные ДС» (аккредитив «Реми» 8,19 в 2025); M&A-отток = сумма трёх (research/02 §5.2)"),
        ("perimeter_breaks_halves", {"2021H2": ["billa", "semya"], "2022H1": ["utkonos"], "2023H2": ["monetka"], "2024H2": ["ulybka"],
                                     "2025H1": ["molniya (24.06, неделя)"], "2025H2": ["molniya", "remi (декабрь)"], "2026H1": ["obi_domlenta", "okey (июнь)", "remi (полное полугодие)"],
                                     "note": "полугодия со скачком периметра — кандидаты на исключение из инноваций σ/ρ A-P2u (D9)"}),
    ])
    dump_json(inorganic, out / "inorganic.json")

    # --- anchor.json
    rev_rep = H("2026H1", "ias17", "pl", "revenue")["v"]
    ebitda_rep = H("2026H1", "ias17", "pl", "ebitda")["v"]
    rev_pf = r6(rev_rep + nv("okey_rev_pro_forma") - nv("okey_rev_since_acq"))
    pbt_pf_delta = r6(-nv("okey_pbt_pro_forma") + nv("okey_pbt_since_acq"))
    fy25 = fys["FY2025"]["ias17"]["pl"]
    h125 = H("2025H1", "ias17", "pl", "revenue")["v"]
    rev_ltm = r6(fy25["revenue"]["v"] - h125 + rev_rep)
    ebitda_ltm = r6(fy25["ebitda"]["v"] - H("2025H1", "ias17", "pl", "ebitda")["v"] + ebitda_rep)
    ebitda_ltm_q = r6(sum(quarters[q]["ias17"]["ebitda"]["v"] for q in ("2025Q3", "2025Q4", "2026Q1", "2026Q2")))
    cf17 = halves["2026H1"]["ias17"]["cf"]
    capex17 = r6(cf17["capex_ppe"]["v"] + cf17["capex_intangibles"]["v"] + cf17["capex_leasehold_rights"]["v"])
    cf16 = halves["2026H1"]["ifrs16"]["cf"]
    capex16 = r6(cf16["capex_ppe"]["v"] + cf16["capex_intangibles"]["v"] + cf16["capex_leasehold_rights"]["v"])
    da17 = H("2026H1", "ias17", "pl", "da")["v"]
    nd = r6(debt_gross - cash)
    # расчётная проформа LTM (кандидат; решение — листы margin/network)
    hyper_share_h2_2025 = sum(quarters[q]["ias17"]["seg_hyper_db"]["v"] for q in ("2025Q3", "2025Q4")) / \
        sum(quarters[q]["ias17"]["seg_hyper_db"]["v"] for q in ("2025Q1", "2025Q2", "2025Q3", "2025Q4"))
    okey_2h25 = 142.0 * hyper_share_h2_2025
    remi_jul_nov = (nv("fy25_remi_pf_rev") - nv("fy25_remi_rev")) / 11 * 5
    anchor = OrderedDict([
        ("schema", "lenta-facts-anchor-v1"), ("period", "2026H1"), ("facts_date", "2026-06-30"), ("unit", "млрд ₽"),
        ("basis", "IAS 17 (до МСФО 16), D1"),
        ("revenue", {"reported": rev_rep, "reported_src": H("2026H1", "ias17", "pl", "revenue")["src"] + "; = IFRS_1H2026 с. 7 (648 488 059)",
                     "pro_forma": rev_pf, "pro_forma_formula": "648,488059 + 66,120725 − 9,359828",
                     "pro_forma_components_src": "IFRS_1H2026 прим. 5 с. 18 (цитата в inorganic.json → okey.pro_forma.quote)",
                     "design_value_note": "DESIGN D3 печатает 705,26 (из округлённых 648,5 + 66,12 − 9,36); точное значение 705,248956",
                     "perimeter_note": "проформа только по «О’КЕЙ» (раскрыта). «Дом Лента» консолидирован поэтапно в 1 кв. 2026 (проформы нет) — недостающий кусок 1 кв. не добавлен; «Реми», «Молния» — в периметре всё полугодие",
                     "kind": "fact+calc"}),
        ("pbt_ifrs16", {"reported": H("2026H1", "ifrs16", "pl", "pbt")["v"], "pro_forma_delta": pbt_pf_delta,
                        "pro_forma": r6(H("2026H1", "ifrs16", "pl", "pbt")["v"] + pbt_pf_delta), "formula": "15,077669 + (−5,530179 + 0,907372)",
                        "note": "единственная раскрытая проформа прибыли — прибыль до налога МСФО 16, после процентов по долгу «О’КЕЙ» и аренды; это НЕ EBITDA"}),
        ("ebitda_pre16", {"reported": ebitda_rep, "reported_src": H("2026H1", "ias17", "pl", "ebitda")["src"],
                          "identity_check": {"opbi": H("2026H1", "ias17", "pl", "opbi")["v"], "da": da17, "sum": r6(H("2026H1", "ias17", "pl", "opbi")["v"] - da17)},
                          "margin_reported": round(ebitda_rep / rev_rep, 6),
                          "pro_forma": None, "pro_forma_pending": "лист evidence/book-1.0/margin — мост «прибыль до налога МСФО 16 → EBITDA IAS 17» для «О’КЕЙ» январь–май (D3)",
                          "pro_forma_candidate": {"okey_ebitda_pre16_jan_may": {"center": 0.0, "range": [-1.0, 1.0], "kind": "judgement",
                                                                                  "basis": "рецензии: EBITDA IAS 17 «О’КЕЙ» ≈0 ± 1 млрд за полугодие; Эйлер — «около безубыточности»"},
                                                  "margin_center": round(ebitda_rep / rev_pf, 6), "margin_range": [round((ebitda_rep - 1.0) / rev_pf, 6), round((ebitda_rep + 1.0) / rev_pf, 6)],
                                                  "cost_of_error": "0,1 п.п. маржи якоря ≈10–45 ₽/акция (затухающее отклонение A-C4 против сдвига цели «дна»)"}}),
        ("margin_pro_forma", None), ("margin_pro_forma_se", None),
        ("margin_pro_forma_note", "заполняет лист margin; кандидат 5,57 % (5,43–5,71 %) при EBITDA «О’КЕЙ» 0 ± 1 млрд"),
        ("da_pre16", {"reported": -da17, "src": H("2026H1", "ias17", "pl", "da")["src"], "ifrs16": nv("da_total_2026h1"),
                      "ifrs16_components": {"ppe": nv("da_ppe_2026h1"), "rou": nv("da_rou_2026h1"), "ip": nv("da_ip_2026h1"), "ia": nv("da_ia_2026h1"), "src": "IFRS_1H2026 прим. 4 с. 16"},
                      "pro_forma": None, "pro_forma_pending": "лист margin (D3)",
                      "pro_forma_candidate": {"okey_add_jan_may": {"center": 1.8, "range": [1.4, 2.2], "kind": "calc",
                                                                    "basis": "ОС+ИН «О’КЕЙ» по справедливой стоимости 31,05 × темп D&A IAS 17 группы 15,795 / средние ОС 218,8 за полугодие (7,2 %) × 5/6"}}}),
        ("capex", {"v": -capex17, "basis": "денежный, IAS 17 (ОС + НМА + права аренды)", "src": f"{cf17['capex_ppe']['src']} + {cf17['capex_intangibles']['src']} + {cf17['capex_leasehold_rights']['src']}",
                   "ifrs16": -capex16, "ifrs16_src": "IFRS_1H2026 с. 8: 18 420 039 + 3 823 825", "pct_revenue_reported": round(-capex17 / rev_rep, 6),
                   "note": "в блоке IAS 17 приобретение ОС больше на 0,238 (18,658 против 18,420); разложение разницы компания не раскрывает"}),
        ("net_debt", {"v": nd, "identity": "100,030371 + 42,553313 − 25,172516", "src": "IFRS_1H2026 с. 6; = датабук Debt (117 410)",
                      "definition": "кредиты и займы − денежные средства; без аренды, без ограниченных средств и компенсирующего актива"}),
        ("cash", {"v": cash, "src": ref("bs_cash_2026h1")}),
        ("debt_gross", {"v": r6(debt_gross), "src": "IFRS_1H2026 с. 6"}),
        ("revenue_ltm", {"reported": rev_ltm, "formula": "FY2025 1 103,662719 − 1П2025 513,933284 + 1П2026 648,488059",
                         "pro_forma": None, "pro_forma_pending": "листы network/margin: правило баз приобретённых сетей (D3)",
                         "pro_forma_candidate": {"v": r6(rev_ltm + okey_2h25 + (nv("okey_rev_pro_forma") - nv("okey_rev_since_acq")) + remi_jul_nov),
                                                 "components": {"okey_2H2025": {"v": r6(okey_2h25), "rule": "142 × доля 2П гипермаркетов «Ленты» 2025 (0,531)", "range": [73.0, 78.0]},
                                                                "okey_jan_may_2026": r6(nv("okey_rev_pro_forma") - nv("okey_rev_since_acq")),
                                                                "remi_jul_nov_2025": {"v": r6(remi_jul_nov), "rule": "(55,488 − 5,100) / 11 × 5", "range": [21.0, 25.0]},
                                                                "domlenta_missing": {"v": None, "note": "выручки «ОБИ» до консолидации в первичке нет; оценка 9–20 млрд — суждение, в кандидат не включена"}},
                                                 "kind": "calc"}}),
        ("ebitda_ltm", {"reported": ebitda_ltm, "formula": "FY2025 83,684 − 1П2025 38,531 + 1П2026 39,279", "quarters_check": ebitda_ltm_q,
                        "pro_forma": None, "pro_forma_pending": "лист margin (EBITDA IAS 17 «О’КЕЙ» 2П2025 + январь–май 2026, «Реми» июль–ноябрь 2025, «ОБИ» до консолидации)",
                        "pro_forma_candidate": {"center": ebitda_ltm, "range": [round(ebitda_ltm - 3.0, 3), round(ebitda_ltm + 3.0, 3)], "kind": "judgement",
                                                "basis": "«О’КЕЙ» ≈0 ± 2 за 11 мес., «Реми» ≈+1 за 5 мес., «ОБИ» ≈−1…−3 до консолидации — взаимно гасятся"}}),
        ("nd_ebitda_reported", {"v": round(nd / ebitda_ltm, 4), "databook": debt_hist["ias17"].get("2026-06-30", {}).get("nd_ebitda")}),
        ("undrawn_credit_lines", {"v": nv("undrawn_2026h1"), "src": ref("undrawn_2026h1")}),
        ("halves_for_book", {
            "revenue": {"reported": {p: H(p, "ias17", "pl", "revenue")["v"] for p in ("2025H1", "2025H2", "2026H1")},
                        "pro_forma": {"2025H1": None, "2025H2": None, "2026H1": rev_pf},
                        "pro_forma_note": "2025H1/2025H2 на периметре 30.06.2026 — лист network (правило D3: проформа 2026H1 × сезонный множитель формата)"},
            "ebitda_pre16": {"reported": {p: H(p, "ias17", "pl", "ebitda")["v"] for p in ("2025H1", "2025H2", "2026H1")},
                             "pro_forma": {"2025H1": None, "2025H2": None, "2026H1": None}, "pro_forma_note": "лист margin"}}),
        ("guidance_2026", {"ebitda_margin_min": 0.07, "nd_ebitda_end": 1.0, "src": "PR_OKEY_2026_06_02",
                           "implied_2H2026_margin_note": "≥7 % за год при 6,06 % в 1П ⇒ ≈7,7–7,9 % во 2П (D2; зависит от веса 2П в году)"}),
    ])
    dump_json(anchor, out / "anchor.json")

    # --- peers.json (цены 18.09.2026 — сырые ответы ISS из research/_work05)
    def iss_close(ticker, date="2026-09-18"):
        fp = research_dir() / "_work05" / f"{ticker}_TQBR.json"
        d = json.loads(fp.read_text(encoding="utf-8"))
        cols = d["columns"]
        for row in d["data"]:
            r = dict(zip(cols, row))
            if r["TRADEDATE"] == date and r.get("BOARDID", "TQBR") == "TQBR":
                return {"legal_close": r["LEGALCLOSEPRICE"], "close": r["CLOSE"], "file": f"research/_work05/{ticker}_TQBR.json",
                        "sha256": sha256_file(fp), "date": date}
        raise KeyError((ticker, date))
    px = {t: iss_close(t) for t in ("LENT", "X5", "MGNT")}
    x5 = {"shares_mln": 245.229303, "net_debt": 310.647, "dividends_after_balance": 60.081, "ebitda_ltm": 287.021, "revenue_ltm": 4878.555,
          "src": "X5 databook 2026_08 (лист Debt стр. 11; лист EBITDA стр. 22; IAS 17) — транскрипция в magnit-850oa data/facts/peers.json (as_of 30.06.2026); акции 271 572 872 × (1 − 0,097 казначейских)"}
    mg = {"shares_mln": 67.847277, "net_debt": 518.1, "ebitda_ltm": round(83.648405 + 95.986638, 6), "revenue_ltm": round(1835.98 + 1887.188, 3),
          "src": "Magnit Databook 1H 2026 (pnl.pre_ifrs16: EBITDA 2П2025 83,648405 + 1П2026 95,986638; ЧД до МСФО 16 518,1 на 30.06.2026) — транскрипция в magnit-850oa data/facts/history.json и assumptions.yaml facts"}
    peer_rows = []
    for name, t, fund in (("X5", "X5", x5), ("Магнит", "MGNT", mg)):
        mcap = px[t]["legal_close"] * fund["shares_mln"] / 1000
        ev = mcap + fund["net_debt"] + fund.get("dividends_after_balance", 0.0)
        peer_rows.append({"name": name, "ticker": t, "basis": "IAS 17", "as_of": "2026-09-18 (цена) / 2026-06-30 (баланс, LTM)",
                          "price_legal_close": px[t]["legal_close"], "shares_mln": fund["shares_mln"], "mcap": round(mcap, 3), "net_debt": fund["net_debt"],
                          "dividends_after_balance": fund.get("dividends_after_balance", 0.0), "ev": round(ev, 3),
                          "ebitda_ltm": fund["ebitda_ltm"], "revenue_ltm": fund["revenue_ltm"],
                          "ev_ebitda": round(ev / fund["ebitda_ltm"], 3), "nd_ebitda": round(fund["net_debt"] / fund["ebitda_ltm"], 3),
                          "ebitda_margin": round(fund["ebitda_ltm"] / fund["revenue_ltm"], 4), "src": fund["src"], "price_src": px[t]["file"]})
    lent_mcap = px["LENT"]["legal_close"] * N("shares_wavg")["th"] / 1e9
    lent_ev = lent_mcap + nd
    lent_row = {"name": "Лента", "ticker": "LENT", "basis": "IAS 17", "as_of": "2026-09-18 / 2026-06-30",
                "price_legal_close": px["LENT"]["legal_close"], "shares_mln": round(N("shares_wavg")["th"] / 1e6, 6), "mcap": round(lent_mcap, 3),
                "net_debt": nd, "ev": round(lent_ev, 3), "ebitda_ltm_reported": ebitda_ltm, "ev_ebitda_reported": round(lent_ev / ebitda_ltm, 3),
                "nd_ebitda_reported": round(nd / ebitda_ltm, 3), "ebitda_margin_reported": round(ebitda_ltm / rev_ltm, 4),
                "ebitda_ltm_pro_forma": None, "ev_ebitda_pro_forma": None,
                "pro_forma_pending": "EBITDA LTM на проформе — лист margin; при кандидате 84,4 ± 3 → EV/EBITDA 3,47–3,73×",
                "note": "отчётный мультипликатор завышен: долг «О’КЕЙ» в ЧД целиком, EBITDA — один месяц; выручка LTM на проформе ≈1 393 (кандидат anchor.json) против 1 238"}
    # сверка с файлами 850oa (если доступны)
    mcheck = {}
    try:
        mp = json.loads((magnit_dir() / "data" / "facts" / "peers.json").read_text(encoding="utf-8"))
        mx = mp["peers"]["X5"]
        mcheck["x5_matches_850oa"] = all(abs(mx[k] - x5[k]) < 1e-6 for k in ("shares_mln", "net_debt", "dividends_after_balance", "ebitda_ltm", "revenue_ltm"))
        hist = json.loads((magnit_dir() / "data" / "facts" / "history.json").read_text(encoding="utf-8"))
        pre = hist["pnl"]["pre_ifrs16"]
        mcheck["mgnt_ebitda_ltm_850oa"] = round((pre["2025H2"]["ebitda"] + pre["2026H1"]["ebitda"]) / 1e6, 6)
    except Exception as e:  # noqa: BLE001 — сверка справочная
        mcheck["error"] = type(e).__name__
    targets = []
    with open(research_dir() / "lent_sellside_targets.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            targets.append({"date": row["date"], "house": row["house"], "rating": row["rating"],
                            "target": float(row["target_rub"]) if row["target_rub"] else None, "status": row["status_2026_09_28"],
                            "source": row["source"]})
    latest = {}
    for t_ in sorted(targets, key=lambda x: x["date"]):
        if t_["target"] is None or not t_["status"].startswith("действует"):
            continue          # закрытые и заменённые цели не входят в агрегат (урок 850oa Д10)
        key = t_["house"].split(" (")[0]
        latest[key] = t_
    vals = sorted(v["target"] for v in latest.values())
    med = (vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2) if vals else None
    peers = OrderedDict([
        ("schema", "lenta-facts-peers-v1"), ("unit", "млрд ₽; акции — млн"),
        ("basis", "EV = капитализация по legal close 18.09.2026 (акции в обращении) + ЧД до МСФО 16 на 30.06.2026 + дивиденды, объявленные до баланса и выплаченные после; EBITDA — IAS 17 LTM на 30.06.2026 (функция 850oa peers.json)"),
        ("market_price_book", {"ticker": "LENT", "price": px["LENT"]["legal_close"], "convention": "legalclose", "date": "2026-09-18",
                               "close": px["LENT"]["close"], "src": px["LENT"]["file"], "sha256": px["LENT"]["sha256"],
                               "note": "первый день после исключения из IMOEX (D7); в primary-файле ISS истории LEGALCLOSEPRICE нет — взят сырой ответ ISS из research/_work05"}),
        ("peers", peer_rows), ("lent", lent_row), ("check_vs_850oa", mcheck),
        ("price_files", {t: {"file": px[t]["file"], "sha256": px[t]["sha256"]} for t in px}),
        ("sellside_targets", {"all": targets, "latest_by_house": list(latest.values()), "latest_median": med,
                              "latest_n": len(vals), "latest_range": [vals[0], vals[-1]] if vals else None,
                              "src": "research/lent_sellside_targets.csv (вторичные источники с датами; класс B)"}),
        ("hygiene_note", "строки X5 и MGNT — публичные отчётные данные аналогов (databook X5 2026_08, Magnit Databook 1H 2026) со ссылкой на первоисточник: в публичном репозитории допустимы (решение ведущего F33; разрешение — tests/test_public_hygiene.py); databook аналогов — в первичку с sha256 (решение ведущего A5)"),
    ])
    dump_json(peers, out / "peers.json")

    # --- sources.json
    extra = OrderedDict()
    for t in px:
        extra[f"ISS_HIST_{t}"] = {"path_research": px[t]["file"], "sha256": px[t]["sha256"], "description": f"MOEX ISS история {t} TQBR (сырой ответ, research/_work05)"}
    extra["SELLSIDE_CSV"] = {"path_research": "research/lent_sellside_targets.csv", "sha256": sha256_file(research_dir() / "lent_sellside_targets.csv"),
                             "description": "цели инвестдомов с датами (сводка research/05; первоисточники — ссылки в CSV)"}
    srcs = OrderedDict([
        ("schema", "lenta-facts-sources-v1"),
        ("principle", "путь — относительно LENTA_PRIMARY_DIR; sha256 пересчитан сборкой и сверен с MANIFEST.md; дата факта ≠ дата получения"),
        ("documents", sources), ("research_files", extra),
        ("derived_files", {}),
        ("verification", {"note_numbers": len(NOTE_FACTS), "note_quotes": len(NOTE_QUOTES), "ifrs16_databook_cells_checked": len(ver), "ifrs16_databook_cells_found": n_found}),
        ("not_collected", [
            "МСФО за 1П2019, 1П2020, 1П2022 и отдельная КФО 2022 (в файлах МКПАО нет; числа — датабук)",
            "отчётность O'KEY Group / ООО «О’КЕЙ» по полугодиям 2025 (есть только годовые 142 млрд в пресс-релизе)",
            "выручка «ОБИ Россия» до консолидации",
            "LEGALCLOSEPRICE в primary-файле ISS истории LENT (взят сырой ответ research/_work05)",
            "databook X5 (2026_08) и Magnit Databook 1H 2026 — в primary отсутствуют; строка аналогов опирается на транскрипцию 850oa",
        ]),
    ])
    for fn in ("accounting_base.json", "anchor.json", "shares.json", "debt.json", "bridge_balance.json", "inorganic.json", "peers.json"):
        srcs["derived_files"][fn] = sha256_file(out / fn)
    dump_json(srcs, out / "sources.json")

    # --- лог
    log(f"[out] facts → <каталог фактов> ({out.name})")
    for fn in list(srcs["derived_files"]) + ["sources.json"]:
        log(f"  {fn}: {(out / fn).stat().st_size} байт")
    (HERE / "out" / "build_log.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
