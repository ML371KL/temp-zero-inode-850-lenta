# Фикстуры сборщиков слоя индикаторов (этап P3)

Записаны 28.09.2026 вежливыми запросами (не больше трёх на хост за заход, пауза ≥ 2 с, `User-Agent` проекта `tzi-850-lenta/1.0 (+https://github.com/ML371KL/temp-zero-inode-850-lenta)`) и пробами исследования источников того же дня. Тесты сборщиков читают только их — в сеть тесты не ходят (живые проверки помечены `network`).

**Персональных данных здесь нет — это проверяется тестом** (`tests/test_sources_lenta.py::test_fixtures_carry_no_personal_data`): ни почт, ни телефонов, ни полей `contact_person`/`contact_list`, ни ФИО. Подписант и контакты пресс-релизов в сообщениях заменены заглушками (`ПОДПИСАНТ-ЗАГЛУШКА`, `КОНТАКТ-ЗАГЛУШКА`, `[почта-заглушка]`) — сборщик обязан вырезать и их. Вакансии прошли тот же белый список полей, что и сборщик (`sources.clean_vacancies_response`).

Страницы lentagroup.ru обрезаны: оставлен только встроенный JSON `App = {...}` с нужным компонентом в той же вёрстке (`<script> App = {...};`), остальная разметка (≈1 МБ у ленты раскрытия) снята. Числа не менялись.

| Каталог / файл | Источник | Что сделано | sha256 исходного ответа (первые 16) |
|---|---|---|---|
| `moex_quote/quotes.json` | ISS TQBR `securities.json?securities=LENT,MGNT,X5&iss.only=marketdata` | как есть | `51c009e6d3f39bdb` |
| `moex_security/security_LENT.json` | ISS `/iss/securities/LENT.json?iss.only=description` | как есть (LISTLEVEL 3, ISSUESIZE 115 985 197) | `24df81c6b2606e4e` |
| `bonds/bonds_search_7826087713.json` | ISS `/iss/securities.json?q=7826087713` (ООО «О'КЕЙ», эмитент 4867) | как есть | `7ebd9a3bbf4a786d` |
| `bonds/bonds_search_7814148471.json` | ISS `/iss/securities.json?q=7814148471` (ООО «Лента», эмитент 5997) | как есть: все выпуски `is_traded = 0` | `84d95f1e0a0c2381` |
| `bonds/bonds_tqcb.json` | ISS TQCB по четырём выпускам «О'КЕЙ» | как есть | `9fc069cd88efd107` |
| `cbr_key_rate/key_rate.xml` | ЦБ, SOAP `DailyInfo.asmx` `KeyRate` | как есть | `93410e8aa2e1e67f` |
| `lenta_disclosure/regulatory_filings.html` | lentagroup.ru `/ru/investors/regulatory-filings/`, компонент `ipjsc-lenta.material-facts` | 10 новейших сообщений + три старых (28466, 28808, 25850), остальная лента и вёрстка сняты | `cb04ecf5ec07b10e` |
| `lenta_disclosure/fact_29130.html` | сообщение 29130 (совет директоров, 16.09.2026) | компонент `regulatory-filings-detail`; подписант → заглушка | `edd03e1de86a60d9` |
| `lenta_disclosure/fact_29047.html` | сообщение 29047 (перевод в третий уровень листинга) | то же | `53191b317c238fa4` |
| `lenta_disclosure/fact_29046.html` | сообщение 29046 (пресс-релиз о листинге) | то же + блок контактов → заглушки | `a0cb63099c32d956` |
| `lenta_disclosure/fact_28466.html` | сообщение 28466 (пресс-релиз о результатах 3 кв. 2025) | подписант → заглушка | `8dc62f5b7051ef50` |
| `lenta_disclosure/fact_25850.html` | сообщение 25850 (покупка «Молнии») | подписант и контакты → заглушки | `db160f719eb1b4fc` |
| `lenta_databook/publications.html` | lentagroup.ru `/ru/investors/publications/`, компонент `publications.results` | `databook` целиком, `items` — первый | `aaaf3f18e81ab4fc` |
| `lenta_databook/financials_quarterly.json` | датабук Ленты за 2 кв. 2026, лист «Financials quarterly» (первичка лежит вне репозитория, sha256 — в файле) | выписка шести кварталов строк, которые читает сборщик; тест собирает из неё xlsx той же раскладки | — |
| `trudvsem_vacancies/vacancies__inn_<ИНН>_p0.json` | «Работа России», `opendata.trudvsem.ru/api/v1/vacancies/company/inn/<ИНН>?offset=0&limit=100` для 7826087713, 7814148471, 6674121179, 7810495210 | белый список полей сборщика; обрезано до 6 / 40 / 25 / 15 вакансий, `meta.total` приведён к числу записей | `—` (исходные ответы с персональными данными не хранятся) |

Пересборка фикстур — вручную тем же порядком; сырые ответы с персональными данными в репозиторий не кладутся никогда (DESIGN, раздел 1).
