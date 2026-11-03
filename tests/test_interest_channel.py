# -*- coding: utf-8 -*-
"""Канал процентов после первой сверки с фактом (отчёт за 3 кв. 2026).

Прогноз 3 кв. был 4,16 млрд ₽ при факте 5,09 (строка «Net Interest expense»
датабука, IAS 17). Две части промаха — ошибки канала, а не новые факты:

* путь чистого долга внутри полугодия шёл линейно, а ядро катит его
  квартальными весами книги (`nwc.quarter_share`: 3 кв. — отток оборотного
  капитала, 4 кв. — приток) — касса 3 кв. и доход на неё были завышены;
* в строке датабука сидит размотка пута «Реми» (строка моста с
  `accrete_rate_half`), а канал её не считал — прогноз и факт были разными
  величинами.

Остаток (≈0,45 млрд) — ставка кредитов «О'КЕЙ» до рефинансирования в 3 кв.:
это факт, а не ошибка канала; его тест не держит.
"""
from __future__ import annotations

import copy
import datetime as dt

import pytest

from indicators import periods
from indicators.nowcast import bridge_accretion, half_progress, interest_nowcast
from indicators.store import Store
from model.book import book
from model.core import accreted, nwc_quarter_weight
from model.book import bridge_items

pytestmark = pytest.mark.tact


@pytest.fixture(scope="module")
def A():
    return copy.deepcopy(book())


def _days(half: str):
    start, end = periods.bounds(half)
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)


def test_the_net_debt_path_inside_the_half_is_the_rule_of_the_core(A):
    """Тот же w, что у ядра (перекат чистого долга на дату оценки), — на каждый день полугодия."""
    half = A["meta"]["first_period"]
    for day in _days(half):
        assert half_progress(A, half, day) == pytest.approx(nwc_quarter_weight(A, 0, day), abs=1e-15)
    # в конце 3 кв. долг ещё растёт: доля 3 кв. в книге отрицательна
    assert half_progress(A, half, dt.date(int(half[:4]), 10, 1)) == pytest.approx(
        A["nwc"]["quarter_share"]["Q3"])


def test_without_quarter_weights_and_outside_the_grid_the_path_is_linear(A):
    B = copy.deepcopy(A)
    B["nwc"].pop("quarter_share")
    half = B["meta"]["first_period"]
    start, end = periods.bounds(half)
    mid = start + (end - start) / 2
    assert half_progress(B, half, mid) == pytest.approx((mid - start).days / (end - start).days)
    before = periods.previous_period(half) if hasattr(periods, "previous_period") else "2026H1"
    s0, e0 = periods.bounds(before)
    assert half_progress(A, before, s0) == 0.0
    assert half_progress(A, before, e0 + dt.timedelta(days=1)) == 1.0


def test_the_accretion_is_the_unwinding_of_the_bridge_rows(A):
    """Размотка за квартал — разность наращенной суммы строки моста; после расчёта — ноль."""
    put = next(item for item in bridge_items(A) if item.accrete_rate_half)
    q3 = (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    expected = accreted(A, put, dt.date(2026, 10, 1)) - accreted(A, put, q3[0])
    assert bridge_accretion(A, *q3) == pytest.approx(expected, rel=1e-12)
    assert 0.2 < expected < 0.3  # 9,4 % за полугодие на 5,2 млрд: ≈0,24 за квартал
    settle_end = periods.bounds(put.settle_period)[1]
    after = settle_end + dt.timedelta(days=1)
    assert bridge_accretion(A, after, after + dt.timedelta(days=90)) == 0.0


def test_an_accreting_asset_is_income_net_of_its_haircut(A):
    B = copy.deepcopy(A)
    rows = B["bridge"]["items"]
    put = next(row for row in rows if row.get("accrete_rate_half"))
    rows.append(dict(put, id="probe_asset", kind="asset", haircut=0.25))
    q3 = (dt.date(2026, 7, 1), dt.date(2026, 9, 30))
    claim_only = bridge_accretion(A, *q3)
    assert bridge_accretion(B, *q3) == pytest.approx(claim_only * (1 - 0.75), rel=1e-12)


def test_the_nowcast_is_debt_interest_less_cash_income_plus_accretion(A):
    now = interest_nowcast(Store(), A, "2026Q3")
    assert now.net_interest == pytest.approx(
        now.debt_interest - now.cash_income + now.bridge_accretion, rel=1e-12)
    assert now.bridge_accretion == pytest.approx(
        bridge_accretion(A, dt.date(2026, 7, 1), dt.date(2026, 9, 30)), rel=1e-12)
