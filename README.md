# Лента 850 — модель справедливой стоимости акции МКПАО «Лента» (LENT) и дашборд

Модель отвечает на три вопроса. **Сколько стоит акция LENT:** медиана
распределения по суждениям книги допущений с полосами 80 % и 50 %; стоимость
бизнеса считается первой, цена акции — последним шагом. **Что заложено в
текущую цену:** обратный DCF по осям книги и EV модели против рыночного V\*.
**Что покажет ближайший отчёт:** поквартальный нау-каст маржи и честный зачёт
против наивных эталонов (к цене не подключён). Логика, принципы и практики —
от модели Магнита 850oa; экономика — «Ленты».

- Витрина — https://tzi-850-lenta.pages.dev (шесть экранов, дверь данных `/api/model`).
- Данные — отдельный публичный репозиторий
  [`ML371KL/temp-zero-inode-850-lenta-data`](https://github.com/ML371KL/temp-zero-inode-850-lenta-data):
  ветка `release`, GitHub Pages
  (`https://ml371kl.github.io/temp-zero-inode-850-lenta-data/latest.json`).
  Выпуски туда пишет конвейер на сервере; код здесь, данные там (почему —
  `docs/DECISIONS.md`, запись 9).
- **Начинать с [`docs/MANUAL.md`](docs/MANUAL.md)** — справочника: суть, карта,
  конвейер, процедуры, где что править.

## Принципы

1. **Факт / допущение / расчёт разделены** — в данных, в коде и на витрине.
   Факты — `data/facts/` со ссылкой на первичку; допущения — книга
   `data/assumptions/`; расчёт — ядро `model/`. Чисел книги и фактов в коде нет
   (тест на литералы).
2. **Распределение, а не точка.** Печатается медиана по суждениям книги в их
   диапазонах; точка при центральных значениях — рядом.
3. **Стоимость бизнеса — первой.** APV по миру ставок клетки; цена акции —
   внутренняя стоимость max(V0 − D, 0)·(1 − g)/акции.
4. Не подгонять под рыночную цену, но всегда сверять с ней: EV против V\*,
   обратный DCF, аналоги на одной базе, цели инвестдомов.
5. Один согласованный мир на сценарий: инфляция, ставки и дисконт — из одной
   макротраектории (общая с 850oa запись миров 18.09.2026, побайтово).
6. `null ≠ 0`. Неизвестное остаётся неизвестным.
7. Каждая правка метода — ключом книги с умолчанием «как было»; книгу меняет
   только её новая версия.

## Что печатает модель

На входах книги 1.1 (кривая 18.09.2026, цена LEGALCLOSEPRICE <!--=headline.market r1-->1 619,5<!--/--> ₽,
отчётная база 30.06.2026, базис IAS 17) крупно — **медиана по суждениям книги
≈<!--=headline.printed_central r0-->3 150<!--/--> ₽** с полосами **80 % — <!--=headline.printed_band[0] r0-->2 600<!--/-->–<!--=headline.printed_band[1] r0-->3 650<!--/--> ₽** и **50 % — <!--=headline.printed_inner[0] r0-->2 850<!--/-->–<!--=headline.printed_inner[1] r0-->3 400<!--/--> ₽** (точные <!--=headline.central r0-->3 125<!--/-->; <!--=headline.band[0] r0-->2 604<!--/-->–<!--=headline.band[1] r0-->3 670<!--/-->; <!--=headline.inner[0] r0-->2 859<!--/-->–<!--=headline.inner[1] r0-->3 413<!--/-->);
вероятность, что центр ниже рынка, — <!--=headline.p_central_below_market p0-->0<!--/--> %. Первой строкой — **EV
медианы <!--=median_diagnostics.center_ev.center.v0 r0-->523<!--/--> млрд ₽ против рыночного V\* <!--=median_diagnostics.center_ev.center.v_star r0-->337<!--/--> млрд ₽
(<!--=median_diagnostics.center_ev.center.gap_vs_v_star sp1-->+55,2<!--/--> %)**; 1 % EV — ≈<!--=median_diagnostics.center_ev.center.rub_per_1pct_ev r0-->42<!--/--> ₽ медианы. Точка при центральных
значениях — ≈<!--=headline.printed_point r0-->3 250<!--/--> ₽ (точная <!--=fair_value.central r0-->3 271<!--/-->; ось ставок <!--=fair_value.low r0-->3 175<!--/-->–<!--=fair_value.high r0-->3 368<!--/--> ₽,
вклад собственного взгляда на инфляцию и ставки <!--=fair_value.rates_view.rub s0-->+192<!--/--> ₽).

Что заложено в цену (обратный DCF медианы, одно суждение при остальных в
центре): долгосрочная маржа <!--=median_diagnostics.reverse_dcf[name=Долгосрочный уровень маржи (сдвиг целей 2031–2035 и LT всех режимов)].value sp2n-->−1,81<!--/--> п.п., поддерживающий capex <!--=median_diagnostics.reverse_dcf[name=Поддерживающий capex (сдвиг всех уровней)].value sp2n-->+1,10<!--/--> п.п., долгий реальный LFL <!--=median_diagnostics.reverse_dcf[name=Долгий реальный LFL (сдвиг s LT)].value sp2n-->−2,76<!--/--> п.п., β_u <!--=median_diagnostics.reverse_dcf[name=Бета активов β_u].value r2n-->1,75<!--/-->,
ERP <!--=median_diagnostics.reverse_dcf[name=ERP].value p1n-->18,1<!--/--> %, дисконт за управление <!--=median_diagnostics.reverse_dcf[name=Дисконт за управление].value p1n-->56,4<!--/--> % — все за границами
диапазонов книги; ни одно суждение в своём диапазоне рыночную цену не даёт.
Модельный V0 — <!--=peer_crosscheck.model_v0_multiple.analytical r1-->6,2<!--/-->× EBITDA LTM проформы против рыночных <!--=peer_crosscheck.market_implied_ev_multiple r1-->3,7<!--/-->×.
Полосу определяют поддерживающий capex (<!--=uncertainty.contributions[axis=Поддерживающий capex (все уровни)].share p0-->27<!--/--> % разброса), долгосрочная
маржа (<!--=uncertainty.contributions[axis=Долгосрочный уровень маржи (цели 2031–2035 и LT всех режимов)].share p0-->19<!--/--> %), долгий реальный LFL (<!--=uncertainty.contributions[axis=Долгий реальный LFL (сдвиг s LT)].share p0-->11<!--/--> %), дисконт за
управление (<!--=uncertainty.contributions[axis=Дисконт за управление].share p0-->8<!--/--> %), плотность новой площади «у дома» (<!--=uncertainty.contributions[axis=Плотность новой площади «у дома»].share p0-->7<!--/--> %) и β_u
(<!--=uncertainty.contributions[axis=β_u].share p0-->7<!--/--> %). Нейтральная маржа 2П2026 (факт, при котором медиана не
меняется) — <!--=median_diagnostics.next_report_neutral.margin p2-->6,26<!--/--> %.

Числа выше — метки результатов книги (`data/assumptions/results.json`); живой
выпуск считает на сегодняшней цене и печатает свои числа — экран «Оценка» и
`/api/model`. Что печатается и почему — `docs/MANUAL.md`, раздел 1; методика —
`docs/MODEL.md`.

## Структура

| Путь | Что |
|---|---|
| `data/assumptions/` | книга допущений — единственный экземпляр (состав и порядок новой версии — `data/assumptions/README.md`): текст `ASSUMPTIONS-BOOK.md`, журналы версий `V1.1-CHANGES.md` и `V1.0-CHANGES.md`, шаблон `assumptions_template.yaml` (его правят), сборщик `build_assumptions.py`, машинная книга `assumptions.yaml`/`.json`, запись миров `worlds_source.json`/`.csv` (побайтово) и рецепт `WORLDS-RECIPE.md`/`worlds_inputs.yaml`/`worlds_recipe.py`, таблицы книги `results.json` и `run_output.txt`, справочные наборы `results_spec.yaml`, объяснения гейтов `gate_explanations.yaml`, записки `release_notes.yaml`, доказательные листы `evidence/book-1.0/` |
| `data/facts/` | факты отчётности со ссылкой на первичку (`docs/FACTS.md`): якорь, проформа, долг и реестр, мост, сделки, история IAS 17, аналоги, стратегия-2028 |
| `data/calendar.json` | календарь событий (отчёты «Ленты» с окнами, заседания ЦБ, законы, пут-оферты «О'КЕЙ») |
| `model/core.py` | ядро: полугодовой проход клетки (сегменты сети, маржа, capex и D&A, ОК, налог, долг, дивиденды), APV, терминал, мост |
| `model/book.py`, `model/book_schema.py` | чтение книги и её правил (`BookError`); закрытая схема ключей |
| `model/grid.py`, `model/mapping.py` | сетка 36 клеток, обновление вероятностей режимов (A-P2u), три слоя; отображение V0 → цена (внутренняя стоимость) |
| `model/uncertainty.py`, `model/engine.py` | полоса A-V9, печатаемая медиана, обратный DCF, диагностики медианы; движок выпуска и подмены суждений |
| `model/book_results.py` | таблицы книги (`results.json`, `run_output.txt`) |
| `model/checks.py`, `model/live.py` | инварианты и гейты правдоподобия; живые входы (цена, дата оценки, кривая как диагностика, карточка акции) |
| `model/payload.py`, `model/quarters.py`, `model/financing.py`, `model/attribution.py` | выпуск `lenta-v1` и его проверка `validate`; квартальный слой ожидания; реестр долга (отчётный слой); «что изменилось с прошлого выпуска» |
| `indicators/` | слой индикаторов (`docs/INDICATORS.md`): сборщики и их дисциплина (`sources.py`), HTTP (`http.py`), хранилище (`store.py`), периоды и отчёты (`periods.py`, `calendar.py`), эмитент (`issuer.py`), журнал (`journal.py`), нау-каст (`nowcast.py`, `quarterly.py`), разрывы периметра (`perimeter.py`), ретро-проверка (`retro.py`), проценты (`interest.py`), индекс вилок (`salary_index.py`), точка входа (`collect.py`) |
| `ops/` | конвейер (`run.sh`, юниты `systemd/`, `budgets.json`, `env.example`), сборка `build_release.py` и публикация `publish.py`, эксплуатация — `ops/README.md`; ручные инструменты `tools/`: `render_numbers.py` (числа в документах), `reanchor.py` (перезаякоривание), `refresh_worlds.py` (миры), `walk_book.py` (прогулка версий), `devserver.py` (предпросмотр) |
| `web/`, `functions/` | витрина (статика без сборки) и функции Pages: дверь данных `functions/api/model.js`, заголовки `functions/_middleware.js` (`docs/DASHBOARD.md`) |
| `tests/` | регрессия книги, правила ядра и книги, выпуск и витрина, конвейер и публикация, слой индикаторов, гигиена публичного репозитория; маркеры — `pytest.ini` |
| `docs/` | методика `MODEL.md`, витрина `DASHBOARD.md`, индикаторы `INDICATORS.md`, факты `FACTS.md`, решения `DECISIONS.md`, история `CHANGELOG.md`, справочник `MANUAL.md` |
| `.github/workflows/ci.yml` | CI: весь набор тестов, контракт выпуска, рецепт миров, гигиена истории; раз в месяц — прогон «в будущем» |

## Генерируемые файлы

- `data/assumptions/results.json`, `run_output.txt` — таблицы книги, выпуск ядра
  (`python -B -m model.book_results`); свежий расчёт обязан их воспроизвести
  (регрессия). Канон — Python 3.12 (поле `environment`).
- `data/assumptions/assumptions.yaml`, `assumptions.json` — машинная книга,
  выход `build_assumptions.py` из шаблона и записи миров.
- Числа результатов книги в документах `*.md` — метки
  `<!--=путь формат-->…<!--/-->`, их переписывает `ops/tools/render_numbers.py`.
- Выпуск `latest.json` и `releases/<sha256>.json` — в `$LENTA_STATE_DIR/release/`
  (на ноутбуке — `var/release/`); в репозиторий кода не кладутся.

## Запуск

Python 3.12 (как на сервере и в CI), из корня репозитория:

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt   # на Linux — .venv/bin/python
./.venv/Scripts/python.exe -m pytest                            # отбор pytest.ini: без network
./.venv/Scripts/python.exe -m pytest -m tact                    # тесты такта (белый список сервера)
./.venv/Scripts/python.exe -m pytest -m docs                    # тесты документов
```

Прогон «в будущем»: Git Bash — `FAKE_TODAY=2027-03-29 ./.venv/Scripts/python.exe -m pytest`;
PowerShell — `$env:FAKE_TODAY='2027-03-29'; .\.venv\Scripts\python.exe -m pytest; Remove-Item Env:FAKE_TODAY`.

Книга и её таблицы:

```bash
./.venv/Scripts/python.exe -B data/assumptions/build_assumptions.py    # шаблон + миры → машинная книга
./.venv/Scripts/python.exe -B -m model.book_results --fast             # проба: сетка, слои, точка, гейты
./.venv/Scripts/python.exe -B -m model.book_results                    # полные таблицы (полоса 2 000 прогонов)
./.venv/Scripts/python.exe -B ops/tools/render_numbers.py              # числа в документах; --check — сверка
./.venv/Scripts/python.exe -B data/assumptions/worlds_recipe.py --check
./.venv/Scripts/python.exe -B ops/tools/refresh_worlds.py --check
```

Выпуск и витрина локально (`http://127.0.0.1:8850/`):

```bash
./.venv/Scripts/python.exe -B ops/build_release.py --check --fast      # контракт выпуска, как в CI
./.venv/Scripts/python.exe -B ops/build_release.py                     # полный выпуск в var/release/
./.venv/Scripts/python.exe ops/tools/devserver.py [порт] [путь-к-выпуску]
```

Без состояния сбора у выпуска нет собранной цены и кривой (цена — книжная), и
витрина показывает плашку деградации входов; состояние задаёт
`LENTA_STATE_DIR`. Такты слоя индикаторов ходят в живые источники — с ноутбука
без нужды не запускать (`docs/INDICATORS.md`, «Вежливость»). Факт отчёта
вносится в боевой журнал только на сервере (`docs/MANUAL.md`, раздел 10.3).

## Что нельзя делать

- Смешивать базы учёта. Модель — в базисе IAS 17 (до МСФО 16): аренда — расход
  в EBITDA, обязательство по аренде в долг не входит. Сверка баз — экран
  «Деньги и долг».
- Считать `null` нулём: у сегмента «Дом Лента» (режим `level`) базы истории нет,
  и это `null`.
- Писать число книги или факта в код. Всё, что повторено в коде, однажды
  разойдётся с книгой молча.
- Править книгу мимо новой версии; менять метод без ключа книги.
- Писать число результата книги в документ руками — только меткой.
- Подставлять собранную кривую ОФЗ в оценку: миры — согласованный набор на
  одну дату; кривая — диагностика (гейт `book_update`), миры пересобираются
  рецептом новой версией книги.
- Класть в репозиторий первичку (PDF/XLSX), IP сервера, имена ключей, ID
  аккаунтов, личные почты, ФИО и контакты из ответов источников, числа и
  документы Магнита — `tests/test_public_hygiene.py`. Коммиты — явными путями,
  никогда `git add -A`.
- Давать серверу ключ записи в репозиторий кода: deploy-ключ с правом записи
  обходит правила веток личного репозитория, поэтому сервер пишет только в
  репозиторий данных.
- Собирать то, что закрыто или запрещено (hh.ru, e-disclosure, сайты за Qrator,
  Чек Индекс, СберИндекс) и обходить защиту от ботов.

## Документы

`docs/MANUAL.md` (справочник) · `docs/MODEL.md` (методика) ·
`docs/DASHBOARD.md` (витрина) · `docs/INDICATORS.md` (индикаторы и нау-каст) ·
`docs/FACTS.md` (факты) · `docs/DECISIONS.md` (решения) · `docs/CHANGELOG.md`
(история) · `ops/README.md` (эксплуатация) · `data/assumptions/README.md`
(книга и её версии).
