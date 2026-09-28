"""Цепной индекс вилок по совпадающим вакансиям — общий модуль (порт 850oa).

Справочный ряд (D15): кормится панелью «Работы России»
(`sources.collect_trudvsem_vacancies`), к цене и к нау-касту НЕ подключён —
только через правило допуска журнала, как любое новое слагаемое. Модуль
общий: вакансия — это идентификатор, вилка, роль и КЛАСТЕР (у Ленты —
регион вакансии); чтение архива — отдельная функция (`read_day`), вся
арифметика индекса от источника не зависит.

Зачем цепной и почему по совпадающим идентификаторам
-----------------------------------------------------
Медиана вилок по всем вакансиям меняется от состава выборки: закрылся
распределительный центр с высокими ставками — «зарплаты упали». У 850oa
между соседними днями такой шум составлял ±4,4 п.п. при сигнале индексации
≈+2 п.п. Поэтому сравниваются ТОЛЬКО те вакансии, которые есть в обоих
срезах, и по каждой берётся её собственное изменение. Состав из индекса
уходит, остаётся переоценка.

Границы вилки считаются РАЗДЕЛЬНО: компания может поднять потолок, не трогая
пол, и сумма двух границ это спрятала бы.

Свёртка звена — УСЕЧЁННОЕ среднее отношений (без ⌊0,01·n⌋ крайних с каждой
стороны; при n < 100 — ничего): у большинства вакансий вилка за сутки не
меняется, и индекс делают хвосты (аудит 850oa, §5.4 п. 3). Простое и
геометрическое средние и n печатаются рядом, в примечании точки.

Ошибка — бутстрепом по КЛАСТЕРАМ, а не по вакансиям: индексация приходит
регионом (у 850oa — городом) целиком, и тысяча вакансий одного кластера —
одно наблюдение. Ошибка уровня — одним розыгрышем кластеров на всю цепь
(`_level_errors`): звенья с общим днём и общими кластерами не независимы.

Ячеечный индекс рядом с цепным: медианы ОДИНАКОВЫХ ячеек «роль × регион»
(Ласпейрес, веса базового периода) — свободен от короткой жизни
идентификаторов, но ловит состав внутри ячейки. Расхождение двух индексов —
мера неопределённости измерения.

Что этот индекс НЕ умеет
------------------------
Он не видит выплат: вилка в объявлении — предложение, а не средняя зарплата
персонала. Охват «Работы России» у Ленты ≈6 % вакансий собственного сайта,
выборка не случайная (research/06 §2.3) — уровень вилки смещён, пригодно
только изменение на одних и тех же id. Год к году — не раньше осени 2027.
"""

from __future__ import annotations

import json
import math
import random
import statistics
from array import array
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from indicators import issuer
from indicators.sources import TRUDVSEM_SOURCE, VACANCY_FILE
from indicators.store import Point, Store

# Розыгрышей бутстрепа: на 400 оценка ошибки сама шумела на ±6 % (аудит 850oa,
# §5.4 п. 8); тысяча — шум ≈2 %.
BOOTSTRAP_DRAWS = 1000
BOOTSTRAP_SEED = 20260921
# Доля, отбрасываемая с КАЖДОГО хвоста отношений звена (округление вниз).
TRIM_SHARE = 0.01
# Сколько вакансий должно быть в ячейке, чтобы её медиана что-то значила.
MIN_CELL_SIZE = 3
UNKNOWN_CLUSTER = "—"


@dataclass(frozen=True)
class Vacancy:
    cluster: str
    """Кластер бутстрепа и часть ключа ячейки (у Ленты — код региона)."""
    low: float
    high: float
    role: str = ""
    employer: str = ""

    @property
    def cell(self) -> tuple[str, str]:
        """Ячейка «роль × регион» — единица ячеечного индекса."""
        return (self.role, self.cluster)


@dataclass
class Link:
    """Одно звено цепи: изменение между двумя соседними срезами.

    `low`/`high` — усечённое среднее отношений (им живёт уровень ряда);
    `*_raw` — простое среднее, `*_geo` — геометрическое, `*_n` — сколько
    отношений у границы.
    """

    previous: str
    current: str
    matched: int = 0
    changed: int = 0
    clusters: int = 0
    low: float = 0.0
    high: float = 0.0
    low_se: float = 0.0
    high_se: float = 0.0
    by_cluster: dict[str, float] = field(default_factory=dict)
    low_raw: float = 0.0
    high_raw: float = 0.0
    low_geo: float = 0.0
    high_geo: float = 0.0
    low_n: int = 0
    high_n: int = 0
    samples: dict = field(default_factory=dict, repr=False, compare=False)


def read_trudvsem_day(directory: Path) -> dict[str, Vacancy]:
    """Срез «Работы России» за день: id → вилка, роль, регион, работодатель.

    Читает очищенные страницы сборщика (`vacancies__inn_<ИНН>_p<N>.json`).
    Вакансия без обеих границ вилки в индекс не входит. Роль —
    `typicalPosition` (справочник профессий), иначе `job-name`; регион —
    `region.region_code`.
    """
    out: dict[str, Vacancy] = {}
    if not directory.is_dir():
        return out
    for path in sorted(directory.iterdir()):
        match = VACANCY_FILE.match(path.name)
        if not match:
            continue
        try:
            data = json.loads(Store.read_raw(path))
        except (OSError, ValueError):
            continue
        results = (data.get("results") or {}) if isinstance(data, dict) else {}
        for item in results.get("vacancies") or []:
            vacancy = (item or {}).get("vacancy") or {}
            identifier = vacancy.get("id")
            low = vacancy.get("salary_min") or 0
            high = vacancy.get("salary_max") or 0
            if not identifier or not (low > 0 or high > 0):
                continue
            region = (vacancy.get("region") or {}).get("region_code") or UNKNOWN_CLUSTER
            role = (vacancy.get("typicalPosition") or vacancy.get("job-name") or "").strip()
            out[identifier] = Vacancy(str(region), float(low), float(high), role=role,
                                      employer=match.group(1))
    return out


read_day = read_trudvsem_day


def trimmed_mean(values: list[float]) -> float:
    """Усечённое среднее: без ⌊TRIM_SHARE·n⌋ крайних значений с каждой стороны."""
    ordered = sorted(values)
    cut = int(len(ordered) * TRIM_SHARE)
    return statistics.fmean(ordered[cut:len(ordered) - cut])


class _Sample:
    """Отношения одной границы звена, разложенные для быстрого бутстрепа.

    Розыгрыш — кратности кластеров. Значения сортируются ОДИН раз вместе с
    номером кластера, середина входит суммой, хвосты снимаются проходом с
    краёв с учётом кратностей — результат совпадает с усечённым средним
    размноженной выборки до округления.
    """

    def __init__(self, by_cluster: dict[str, list[float]]):
        self.clusters = sorted(by_cluster)
        index = {key: number for number, key in enumerate(self.clusters)}
        pairs = sorted((value, index[key])
                       for key, values in by_cluster.items() for value in values)
        self.values = array("d", (value for value, _ in pairs))
        self.owners = array("i", (owner for _, owner in pairs))
        self.sums = [math.fsum(by_cluster[key]) for key in self.clusters]
        self.counts = [len(by_cluster[key]) for key in self.clusters]

    def trimmed(self, weights: list[int]) -> float | None:
        """Усечённое среднее выборки, где кластер `i` взят `weights[i]` раз."""
        total = sum(w * c for w, c in zip(weights, self.counts))
        if not total:
            return None
        cut = int(total * TRIM_SHARE)
        kept = math.fsum(w * s for w, s in zip(weights, self.sums))
        values, owners = self.values, self.owners
        for order in (range(len(values)), range(len(values) - 1, -1, -1)):
            left = cut
            for position in order:
                if not left:
                    break
                weight = weights[owners[position]]
                if weight:
                    take = weight if weight < left else left
                    kept -= take * values[position]
                    left -= take
        return kept / (total - 2 * cut)


def _level_errors(links: list[Link]) -> list[tuple[float, float]]:
    """Ошибка УРОВНЯ цепи после каждого звена: (низ, верх), бутстрепом по кластерам.

    Один набор кластеров с кратностями на ВСЮ цепь; кластеры сортируются —
    иначе порядок зависел бы от рандомизации хэшей процесса.
    """
    universe = sorted({key for item in links for sample in item.samples.values()
                       for key in sample.clusters})
    size = len(universe)
    if size < 2:
        return [(0.0, 0.0) for _ in links]
    position = {key: number for number, key in enumerate(universe)}
    places = [{bound: [position[key] for key in sample.clusters]
               for bound, sample in item.samples.items()} for item in links]
    points = []
    level = {"low": 1.0, "high": 1.0}
    for item in links:
        for bound in level:
            level[bound] *= 1 + getattr(item, bound)
        points.append(dict(level))
    squares = [{"low": 0.0, "high": 0.0} for _ in links]
    rng = random.Random(BOOTSTRAP_SEED)
    for _ in range(BOOTSTRAP_DRAWS):
        weights = [0] * size
        for _ in range(size):
            weights[rng.randrange(size)] += 1
        level = {"low": 1.0, "high": 1.0}
        for number, item in enumerate(links):
            for bound in level:
                sample = item.samples.get(bound)
                value = (sample.trimmed([weights[i] for i in places[number][bound]])
                         if sample is not None else None)
                level[bound] *= 1 + (getattr(item, bound) if value is None else value)
                squares[number][bound] += (level[bound] - points[number][bound]) ** 2
    return [((row["low"] / (BOOTSTRAP_DRAWS - 1)) ** 0.5,
             (row["high"] / (BOOTSTRAP_DRAWS - 1)) ** 0.5) for row in squares]


def link(previous: dict[str, Vacancy], current: dict[str, Vacancy],
         *, previous_day: str = "", current_day: str = "", errors: bool = True) -> Link:
    """Звено цепи между двумя срезами: только общие id, границы раздельно."""
    result = Link(previous=previous_day, current=current_day)
    low_by: dict[str, list[float]] = defaultdict(list)
    high_by: dict[str, list[float]] = defaultdict(list)
    mid_by: dict[str, list[float]] = defaultdict(list)

    for identifier in sorted(previous.keys() & current.keys()):
        before, after = previous[identifier], current[identifier]
        changes = []
        if before.low > 0 and after.low > 0:
            change = after.low / before.low - 1
            low_by[before.cluster].append(change)
            changes.append(change)
        if before.high > 0 and after.high > 0:
            change = after.high / before.high - 1
            high_by[before.cluster].append(change)
            changes.append(change)
        if not changes:
            continue
        result.matched += 1
        result.changed += 1 if any(abs(c) > 1e-9 for c in changes) else 0
        mid_by[before.cluster].append(statistics.fmean(changes))

    for bound, by in (("low", low_by), ("high", high_by)):
        flat = [v for values in by.values() for v in values]
        if not flat:
            continue
        setattr(result, bound, trimmed_mean(flat))
        setattr(result, f"{bound}_raw", statistics.fmean(flat))
        setattr(result, f"{bound}_geo",
                math.exp(statistics.fmean(math.log1p(v) for v in flat)) - 1)
        setattr(result, f"{bound}_n", len(flat))
        result.samples[bound] = _Sample(by)
    if errors:
        result.low_se, result.high_se = _level_errors([result])[0]
    result.clusters = len(mid_by)
    result.by_cluster = {key: statistics.fmean(values) for key, values in mid_by.items()}
    return result


def cell_medians(day: dict[str, Vacancy], bound: str, *,
                 role: str | None = None) -> dict[tuple[str, str], tuple[float, int]]:
    """Медианы вилки по ячейкам «роль × регион» (ячейки от `MIN_CELL_SIZE` вакансий)."""
    grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
    for vacancy in day.values():
        if role is not None and vacancy.role != role:
            continue
        value = getattr(vacancy, bound)
        if value > 0:
            grouped[vacancy.cell].append(value)
    return {key: (statistics.median(values), len(values))
            for key, values in grouped.items() if len(values) >= MIN_CELL_SIZE}


@dataclass(frozen=True)
class CellIndex:
    """Звено ячеечного индекса: изменение ставки при постоянном составе ячеек."""

    value: float
    cells: int
    weight: int


def cell_index(previous: dict[str, Vacancy], current: dict[str, Vacancy],
               *, bound: str, role: str | None = None) -> CellIndex:
    """Ячеечный индекс с весами БАЗОВОГО периода (Ласпейрес, геометрический)."""
    base = cell_medians(previous, bound, role=role)
    now = cell_medians(current, bound, role=role)
    common = [key for key in base if key in now]
    weight = sum(base[key][1] for key in common)
    if not weight:
        return CellIndex(value=1.0, cells=0, weight=0)
    log = sum(base[key][1] * math.log(now[key][0] / base[key][0]) for key in common)
    return CellIndex(value=math.exp(log / weight), cells=len(common), weight=weight)


def archive_days(root: Path, days: list[str] | None = None, *, reader=read_trudvsem_day):
    """Дни архива подряд: (день, срез). День без вакансий с вилкой пропускается.

    Пустой список дней — «ни одного дня», а не «все».
    """
    if not Path(root).is_dir():
        return
    directories = {d.name: d for d in Path(root).iterdir() if d.is_dir()}
    wanted = sorted(directories) if days is None else days
    for day in [d for d in wanted if d in directories]:
        current = reader(directories[day])
        if current:
            yield day, current


@dataclass
class Walk:
    """Один проход по архиву: цепь по id и ячеечная цепь `cells[роль]`."""

    links: list[Link] = field(default_factory=list)
    cells: dict[str | None, list[tuple[str, float, float, int]]] = field(default_factory=dict)


def walk(root: Path, days: list[str] | None = None, *, roles=(None,), links: bool = True,
         errors: bool = True, reader=read_trudvsem_day) -> Walk:
    """Цепь по id и ячеечные цепи — за ОДИН проход по дням архива.

    Звено ячеечной цепи без общих ячеек не пишется, и день не становится
    базой следующего звена: уровень «ячеек 0» — не наблюдение.
    """
    out = Walk(cells={role: [] for role in roles})
    levels = {role: (1.0, 1.0) for role in roles}
    bases: dict = {}
    previous_day = previous = None
    for day, current in archive_days(root, days, reader=reader):
        if links and previous is not None:
            out.links.append(link(previous, current, previous_day=previous_day,
                                  current_day=day, errors=errors))
        previous_day, previous = day, current
        for role in roles:
            if role not in bases:
                bases[role] = current
                out.cells[role].append((day, 1.0, 1.0, 0))
                continue
            low = cell_index(bases[role], current, bound="low", role=role)
            high = cell_index(bases[role], current, bound="high", role=role)
            if not low.cells or not high.cells:
                continue
            levels[role] = (levels[role][0] * low.value, levels[role][1] * high.value)
            out.cells[role].append((day, *levels[role], low.cells))
            bases[role] = current
    for role in roles:
        if len(out.cells[role]) < 2:
            out.cells[role] = []
    return out


def chain(root: Path, days: list[str] | None = None, *, reader=read_trudvsem_day) -> list[Link]:
    """Цепь звеньев по последовательным (имеющимся) дням архива."""
    return walk(root, days, roles=(), reader=reader).links


class Level(NamedTuple):
    """Точка ряда индекса: уровень на день и звено, которое к нему привело."""

    day: str
    low: float
    high: float
    low_se: float
    high_se: float
    matched: int
    link: Link | None = None


def index_series(links: list[Link]) -> list[Level]:
    """Уровень индекса по дням (база — единица на первом дне цепи) с ошибкой уровня."""
    if not links:
        return []
    if all(item.samples for item in links):
        errors = _level_errors(links)
    else:
        low_var = high_var = 0.0
        errors = []
        for item in links:
            low_se, high_se = (_level_errors([item])[0] if item.samples
                               else (item.low_se, item.high_se))
            low_var += low_se ** 2
            high_var += high_se ** 2
            errors.append((low_var ** 0.5, high_var ** 0.5))
    out = [Level(links[0].previous, 1.0, 1.0, 0.0, 0.0, 0)]
    low = high = 1.0
    for item, (low_se, high_se) in zip(links, errors):
        low *= 1 + item.low
        high *= 1 + item.high
        out.append(Level(item.current, low, high, low_se, high_se, item.matched, item))
    return out


def _percent(value: float, *, sign: bool = True) -> str:
    return (f"{value * 100:+.2f}" if sign else f"{value * 100:.2f}").replace(".", ",")


def level_note(level: Level, bound: str) -> str:
    """Примечание точки: ошибка уровня ПЕРВОЙ, затем звено дня (свёртки и n)."""
    se = level.low_se if bound == "low" else level.high_se
    head = f"±{_percent(se, sign=False)} п.п."
    item = level.link
    if item is None:
        return f"{head}; база цепи"
    return (f"{head}; звено: усеч. {_percent(getattr(item, bound))} %, "
            f"сырое {_percent(getattr(item, f'{bound}_raw'))} %, "
            f"геом. {_percent(getattr(item, f'{bound}_geo'))} %, "
            f"n {getattr(item, f'{bound}_n')}; совпадений {level.matched}")


def recount_moment(now: datetime | None = None) -> str:
    """Момент пересчёта в UTC — винтаж точек, которые пишет пересчёт."""
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def write_recount(store, series_id: str, rows, *, now: str, **meta) -> None:
    """Пишет пересчитанный ряд: `period` — день обхода, `fetched_at` — момент пересчёта.

    Винтаж — момент ПЕРЕСЧЁТА, а не день обхода (аудит 850oa, §5.4 п. 6):
    иначе бэктест по `value_as_of` видел бы уровень раньше, чем его увидел
    конвейер. Точка, равная соседней по времени, не пишется (`Store.upsert`).
    """
    store.upsert(series_id, [Point(period=day, value=value, note=note, fetched_at=now)
                             for day, value, note in rows], **meta)


# Ряды индекса. Имя кончается на `_low`/`_high`/`_matched`, префикс —
# `<префикс>.salary.` (P4 объявляет его уровнем с базой 1 — `INDEX_PREFIXES`).
SERIES_PREFIX = issuer.series("salary.")
CHAIN_PREFIX = issuer.series("salary.vacancy_chain")
CELL_PREFIX = issuer.series("salary.cell_chain")


def write_series(store, *, now: str | None = None, source: str = TRUDVSEM_SOURCE) -> str:
    """Пересчитывает цепной и ячеечный индексы по архиву и ПИШЕТ справочные ряды.

    Отказа не бывает: пока второго дня с вилками нет, звеньев нет — это не
    ошибка, а «ещё нечего сравнивать». Возвращает строку для журнала такта.
    """
    now = now or recount_moment()
    result = walk(store.raw / source, roles=(None,), errors=False)
    rows = index_series(result.links)
    if not rows:
        return "зарплатный индекс: звеньев нет — нужны два дня обхода с вилками"
    for suffix, label in (("low", "вилки «Работы России»: нижняя граница, уровень (справочно)"),
                          ("high", "вилки «Работы России»: верхняя граница, уровень (справочно)")):
        write_recount(store, f"{CHAIN_PREFIX}_{suffix}",
                      [(row.day, getattr(row, suffix), level_note(row, suffix)) for row in rows],
                      now=now, channel=2, cadence="ежедневно", label=label)
    write_recount(store, f"{CHAIN_PREFIX}_matched",
                  [(row.day, float(row.matched), "") for row in rows],
                  now=now, channel=2, cadence="ежедневно",
                  label="вилки «Работы России»: совпавших вакансий в звене")
    cells = result.cells.get(None) or []
    for position, suffix, word in ((1, "low", "нижняя"), (2, "high", "верхняя")):
        if cells:
            write_recount(store, f"{CELL_PREFIX}_{suffix}",
                          [(item[0], item[position], f"ячеек {item[3]}") for item in cells],
                          now=now, channel=2, cadence="ежедневно",
                          label=f"вилки «Работы России»: ячеечный индекс «роль × регион», "
                                f"{word} граница (справочно)")
    last = rows[-1]
    return (f"зарплатный индекс: дней {len(rows)}, {rows[0].day} → {last.day}, "
            f"низ {(last.low - 1) * 100:+.2f} %, верх {(last.high - 1) * 100:+.2f} %, "
            f"совпадений {last.matched}")


def cumulative(links: list[Link]) -> tuple[float, float]:
    """Накопленный индекс по цепи: отдельно нижняя и верхняя граница."""
    low = high = 1.0
    for item in links:
        low *= 1 + item.low
        high *= 1 + item.high
    return low - 1, high - 1
