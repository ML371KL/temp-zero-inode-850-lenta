# Книга допущений «Ленты»

Единственный экземпляр книги допущений: код читает её отсюда (`model/book.py`, `BOOK_DIR`; каталог подменяется переменной `LENTA_DATA_DIR`). Версии помечаются тегами git `book-<версия>` (`git show book-1.0:data/assumptions/assumptions.yaml`). Книгу меняет только её новая версия; решает о ней владелец или ведущий.

## Что здесь

| Файл | Что |
|---|---|
| `ASSUMPTIONS-BOOK.md` | текст книги: суждения, обоснования, диапазоны, цена ошибки (идентификаторы A-xx — те же, что в машинном файле) |
| `V1.0-CHANGES.md` | журнал версии 1.0: откуда числа, что взято у 850oa без изменений и почему, что своё у «Ленты», сведение листов (R1–R32), решения ведущего по книге 1.0 и установка в ядро — каждый ключ поддержан ядром или снят с причиной |
| `assumptions_template.yaml` | машинная книга без миров — её правят |
| `WORLDS-RECIPE.md`, `worlds_inputs.yaml`, `worlds_recipe.py` | рецепт макро-миров (книга-источник 850oa): механика, входы записи, исполняемая спецификация |
| `worlds_source.json`, `worlds_source.csv` | общая запись миров 18.09.2026 — **побайтово** (DESIGN D6; sha256 `worlds_source.json` начинается с `7296567d`, закреплено тестом на хранимых байтах; `.gitattributes`: `data/assumptions/worlds_source.* -text`) |
| `build_assumptions.py` | сборщик: шаблон + запись миров → машинная книга |
| `assumptions.yaml`, `assumptions.json` | машинная книга — выход сборщика, её считает ядро (закрытая схема `model/book_schema.py`); комментарии YAML идут в выпуск колонкой «источник» |
| `results.json`, `run_output.txt` | таблицы книги — выпуск ядра (`python -B -m model.book_results`; первый прогон 29.09.2026, Python 3.12 — поле `environment`); регрессия — `tests/test_book_regression.py`, числа в текстах — метки `ops/tools/render_numbers.py` |
| `results_spec.yaml` | справочные наборы таблиц книги — не суждения: контрольные наблюдения правила A-P2u, строки «что оправдывает рыночную цену», справочная P(полная сходимость) |
| `evidence/` | доказательные листы версий (`evidence/README.md`) |

Не книга, а эксплуатация (правятся без новой версии): `gate_explanations.yaml` — объяснения сработавших гейтов и **коридоры** гейтов `margin_range`, `capex_range`, `ev_ebitda` (поле `corridor`; `model/checks.py::gate_corridors`), `release_notes.yaml` — записки о задуманных скачках заголовка, и этот `README.md`.

## Как собирается книга

Из корня репозитория, Python 3.12 (`.venv`):

1. Правка `assumptions_template.yaml` (и текста `ASSUMPTIONS-BOOK.md`, журнала `V<версия>-CHANGES.md`, листов `evidence/book-<версия>/`). Миры пересобираются только вместе с книгой-источником — рецептом (`WORLDS-RECIPE.md`, раздел 6; инструмент `ops/tools/refresh_worlds.py`); `python -B data/assumptions/worlds_recipe.py --check` обязан воспроизводить `worlds_source.json`.
2. `python -B data/assumptions/build_assumptions.py` — пишет `assumptions.yaml` и `assumptions.json`.
3. `python -B -m model.book_results --fast` — проба: книга грузится ядром (закрытая схема, все правила книги) и считается — сетка 36 клеток, слои, точка, сценарии, гейты; файлов не пишет.
4. `python -B -m model.book_results` — полные таблицы книги (`results.json`, `run_output.txt`: полоса A-V9, суждения, диагностики медианы), затем `python -B ops/tools/render_numbers.py` — числа ядра в текст книги и журнал версии (метки `<!--=путь формат-->…<!--/-->`; `--check` — сверка).
5. Проверки: полный `pytest` (тесты книги — `tests/test_book10_book.py`; миры — `tests/test_book10_book.py::test_the_world_record_is_stored_byte_for_byte`).
6. Тег `book-<версия>` на коммите версии.

## Версии

| Версия | Дата | Что |
|---|---|---|
| 1.0 | 28.09.2026 | первая книга «Ленты» (DESIGN v2, решения ведущего LD-1.0); до выпуска в неё же вошли правки F1 после первого прогона (29.09.2026, журнал §4.6) и пакет аудита с правками по его проверке (30.09.2026, журнал §6 и §6.6); журнал — `V1.0-CHANGES.md`, решения — `docs/DECISIONS.md` |
