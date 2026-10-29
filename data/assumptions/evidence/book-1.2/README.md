# Доказательные листы книги 1.2

Версия 1.2 (29.10.2026) меняет только миры ставок: общая запись 28.10.2026 после решения Банка России 23.10.2026 (`../../worlds_source.json`, sha256 `1e905cb7…`; собрана в книге-источнике 850oa рецептом 1.7, сюда — побайтово) и дату её кривых `meta.curve_as_of`. Суждения, факты, цена и мост — книги 1.1, листы тем — `../book-1.0/`. Первичка не нужна: листы считают ядром на книге из репозитория.

| Лист | Что утверждает | Вывод |
|---|---|---|
| `worlds/` | таблицы раздела 2 книги на записях 18.09 и 28.10.2026 (`worlds_tables.py`); быстрые числа версии — точка, низ и верх, цены миров на входах книги и выпуска 26.10.2026, медиана малой полосы, цена окна снимка, итоговой ОНДКП и факта продовольствия (`quick.py`) | `worlds/worlds_tables_out.txt`, `worlds/quick_out.txt` (и `.json`) |
| `walk/` | переход 1.1 → 1.2 — мир M (рынок окна и дата кривых), затем миры N и H (входы ЦБ, итоговая ОНДКП, опрос, факт полугодия); последний шаг равен прямому расчёту книги 1.2, книга после шагов — книга 1.2 целиком; `worlds-1.1.yaml` — блок миров книги 1.1 (с ним книга 1.2 — книга 1.1) | `walk/out/walk.txt`, `walk/out/walk.json` |
| `gates/` | массы гейтов книги 1.2 на каждой дате оценки с 18.09.2026 по 30.06.2027 и величины, которыми оперируют тексты `gate_explanations.yaml` (копия листа 1.1: дата кривых — `meta.curve_as_of`, процессы — `LENTA_WORKERS`) | `gates/out/gate_sweep.txt`, `gates/out/gate_sweep.json` |

## Как воспроизвести

Из корня репозитория, Python 3.12 (`.venv`).

```
# таблицы раздела 2 и быстрые числа (книга 1.1 — из git, коммит 62f858a)
python -B data/assumptions/evidence/book-1.2/worlds/worlds_tables.py
LENTA_WORKERS=2 python -B data/assumptions/evidence/book-1.2/worlds/quick.py --draws 500 --window <window_block.json листа book-1.7/worlds книги-источника>

# прогулка: стартовая книга — версия 1.1 по тегу
git show book-1.1:data/assumptions/assumptions.yaml > var/book-1.1.yaml
python -B ops/tools/walk_book.py --start var/book-1.1.yaml --end data/assumptions/assumptions.yaml \
    --steps data/assumptions/evidence/book-1.2/walk/steps.yaml --median \
    --json data/assumptions/evidence/book-1.2/walk/out/walk.json > data/assumptions/evidence/book-1.2/walk/out/walk.txt

# развёртка гейтов
python -B data/assumptions/evidence/book-1.2/gates/gate_sweep.py
```

Все расчёты детерминированы: зерно полосы — `valuation.uncertainty.seed`; быстрые числа — 500 прогонов того же зерна (одни случайные числа у всех вариантов).

## Что обязано держаться

1. Прогулка заканчивается двумя «ДА» (код 0); шаг «Версия 1.2» чисел не меняет; низ оси ставок (слой «рыночные ставки как есть» — мир M целиком) двигает только шаг мира M.
2. Книга 1.2 с блоком `walk/worlds-1.1.yaml`, без `meta.curve_as_of` и с номером 1.1 — книга 1.1: точка таблиц книги 1.1 бит в бит, медиана — в CI (`tests/test_book12_walk.py`); ключи версии 1.0 на тех же мирах — книга 1.0 (`tests/test_book11_walk.py`).
3. `meta.curve_as_of` = дата снимка записи; `zero_curve.LT` всех миров = блок `curve_lt` записи (`tests/test_book12_walk.py`).
4. На развёртке нет ни одной даты с блокирующей находкой и ни одной даты, где масса гейта выходит из коридора своего объяснения (первые две строки `gates/out/gate_sweep.txt`).
