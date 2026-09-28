# -*- coding: utf-8 -*-
"""Цепной индекс вилок — общий модуль 850oa на панели «Работы России» (справочно).

Два дня обхода собираются из очищенных фикстур сборщика: во второй день
части вакансий поднята вилка, часть вакансий ушла и пришли новые. Индекс
обязан увидеть переоценку и не увидеть смену состава.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from indicators import issuer, salary_index, sources
from indicators.store import Store

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact

FIXTURES = Path(__file__).parent / "fixtures" / "trudvsem_vacancies"


def _day(store: Store, day: str, mutate=None) -> Path:
    directory = store.raw / sources.TRUDVSEM_SOURCE / day
    directory.mkdir(parents=True, exist_ok=True)
    for path in sorted(FIXTURES.glob("vacancies__inn_*_p0.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if mutate:
            mutate(path.name, data)
        (directory / path.name).write_text(json.dumps(data, ensure_ascii=False),
                                           encoding="utf-8")
    return directory


def _raise_every_third(name, data):
    for number, item in enumerate(data["results"]["vacancies"]):
        vacancy = item["vacancy"]
        if number % 3 == 0:
            vacancy["salary_min"] = round((vacancy.get("salary_min") or 0) * 1.05)
            vacancy["salary_max"] = round((vacancy.get("salary_max") or 0) * 1.10)


def _churn(name, data):
    """Ушла половина вакансий, пришли новые с вилкой вдвое выше — только состав."""
    vacancies = data["results"]["vacancies"]
    keep = vacancies[: len(vacancies) // 2]
    fresh = []
    for number, item in enumerate(vacancies[len(vacancies) // 2:]):
        clone = json.loads(json.dumps(item))
        clone["vacancy"]["id"] = f"new-{name}-{number}"
        clone["vacancy"]["salary_min"] = (clone["vacancy"].get("salary_min") or 0) * 2
        clone["vacancy"]["salary_max"] = (clone["vacancy"].get("salary_max") or 0) * 2
        fresh.append(clone)
    data["results"]["vacancies"] = keep + fresh


def test_a_day_is_read_from_the_cleaned_pages(tmp_path):
    store = Store(tmp_path)
    day = salary_index.read_trudvsem_day(_day(store, "2026-09-28"))
    assert len(day) > 50
    vacancy = next(iter(day.values()))
    assert vacancy.cluster.isdigit() and len(vacancy.cluster) == 13, "код региона — кластер"
    assert vacancy.employer in issuer.employer_inns()
    assert vacancy.cell == (vacancy.role, vacancy.cluster)


def test_the_chain_sees_a_repricing(tmp_path):
    store = Store(tmp_path)
    _day(store, "2026-09-27")
    _day(store, "2026-09-28", _raise_every_third)
    links = salary_index.chain(store.raw / sources.TRUDVSEM_SOURCE)
    assert len(links) == 1
    link = links[0]
    assert link.matched > 50 and link.changed > 0
    # Поднята треть вакансий: нижняя граница на +5 %, верхняя на +10 % — средние
    # около трети от этого (усечение при n ≥ 100 срезает по 1 % с краёв).
    assert 0.005 < link.low_raw < 0.03 and 0.01 < link.high_raw < 0.05
    assert link.high > link.low > 0, "границы вилки считаются раздельно"
    assert link.low_se > 0 and link.clusters > 3, "ошибка — бутстрепом по регионам"


def test_the_chain_ignores_a_change_of_composition(tmp_path):
    """Состав сменился (новые вакансии с вилкой вдвое выше), ставки — нет: индекс 0."""
    store = Store(tmp_path)
    _day(store, "2026-09-27")
    _day(store, "2026-09-28", _churn)
    link = salary_index.chain(store.raw / sources.TRUDVSEM_SOURCE)[0]
    assert link.low == pytest.approx(0.0, abs=1e-12)
    assert link.high == pytest.approx(0.0, abs=1e-12)
    assert link.changed == 0


def test_the_series_are_written_as_reference_levels(tmp_path):
    store = Store(tmp_path)
    assert "звеньев нет" in salary_index.write_series(store)
    _day(store, "2026-09-26")
    _day(store, "2026-09-27")
    _day(store, "2026-09-28", _raise_every_third)
    line = salary_index.write_series(store, now="2026-09-28T17:30:00+00:00")
    assert "дней 3" in line
    low = store.load(salary_index.CHAIN_PREFIX + "_low")
    assert [p.period for p in low.points] == ["2026-09-26", "2026-09-27", "2026-09-28"]
    assert low.points[0].value == 1.0 and low.points[1].value == pytest.approx(1.0)
    assert low.points[2].value > 1.0
    assert low.points[2].note.startswith("±"), "ошибка уровня — первой в примечании"
    assert "справочно" in low.label
    assert store.load(salary_index.CHAIN_PREFIX + "_matched").points[-1].value > 50
    cells = store.load(salary_index.CELL_PREFIX + "_high")
    assert cells is not None and cells.points[-1].value >= 1.0
    assert salary_index.CHAIN_PREFIX.startswith(issuer.series("salary."))
    # Повторный пересчёт той же истории винтажей не плодит.
    salary_index.write_series(store, now="2026-09-29T17:30:00+00:00")
    assert len(store.load(salary_index.CHAIN_PREFIX + "_low").points) == 3


def test_the_trimmed_mean_drops_one_percent_from_each_tail():
    values = [0.0] * 98 + [-1.0, 1.0]
    assert salary_index.trimmed_mean(values) == 0.0
    assert salary_index.trimmed_mean([0.0] * 50 + [1.0]) == pytest.approx(1 / 51), (
        "при n < 100 усекать нечем")
