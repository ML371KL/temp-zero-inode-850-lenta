# -*- coding: utf-8 -*-
"""Разложение изменения стоимости между выпусками.

Аудит: «атрибуция ΔEV между выпусками — заглушка». Без неё любое движение
числа выглядит как шум модели, и доверие падает быстрее, чем растёт от
точности.
"""
from __future__ import annotations

import copy

import pytest

from model.attribution import attribute, inputs_snapshot, total
from model.book import book
from model.engine import run_release

# Каждый тест модуля читает книгу допущений (в репозитории до книги 1.0 её нет).
pytestmark = pytest.mark.needs_book


def run(book_dict):
    release = run_release(book_dict, gates=False)
    return release.layers["macro_neutral"].v0, release.fair_value.central


@pytest.fixture(scope="module")
def A():
    return book()


def test_snapshot_captures_what_changes_between_releases(A):
    snap = inputs_snapshot(A)
    assert snap["book_version"] == str(A["meta"]["version"]) == "1.1"
    assert snap["curve"], "кривая обязана попасть в снимок"
    assert "market_price" in snap and "observations" in snap


def test_no_snapshot_means_no_attribution(A):
    assert attribute({"layers": {}, "fair_value": {}}, A, run=run) == []


def test_curve_move_is_attributed_to_rates(A):
    """Сдвиг кривой на процентный пункт обязан лечь в шаг «ставки».

    Проверяется знак и порядок: при росте ставок стоимость активов падает,
    и весь сдвиг относится к ставкам, а не размазывается по допущениям.
    """
    world = A["joint"]["macro_neutral_world"]
    old_curve = {str(k): v for k, v in A["worlds"][world]["zero_curve"].items()}
    previous = {
        "inputs": {**inputs_snapshot(A), "curve": {k: v - 0.01 for k, v in old_curve.items()}},
        "layers": {"macro_neutral": {"v0": 0.0}},
        "fair_value": {"central": 0.0},
        "meta": {"payload_sha256": "prev"},
    }
    # V₀ прошлого выпуска подставим настоящим, чтобы первый шаг был нулевым.
    lower = copy.deepcopy(A)
    lower["worlds"][world]["zero_curve"] = {
        k: v - 0.01 for k, v in A["worlds"][world]["zero_curve"].items()}
    was_v0, was_price = run(lower)
    previous["layers"]["macro_neutral"]["v0"] = was_v0
    previous["fair_value"]["central"] = was_price

    steps = attribute(previous, A, run=run)
    by_key = {s.key: s for s in steps}
    assert abs(by_key["residual"].delta_v0) < 0.5, "шаг допущений обязан быть пустым"
    assert abs(by_key["observations"].delta_v0) < 0.5, "наблюдения не менялись"
    assert by_key["curve"].delta_v0 < -5, "рост ставок обязан снижать стоимость активов"


def test_steps_sum_to_the_total_change(A):
    world = A["joint"]["macro_neutral_world"]
    lower = copy.deepcopy(A)
    lower["worlds"][world]["zero_curve"] = {
        k: v - 0.005 for k, v in A["worlds"][world]["zero_curve"].items()}
    was_v0, was_price = run(lower)
    previous = {
        "inputs": {**inputs_snapshot(A),
                   "curve": {str(k): v for k, v in lower["worlds"][world]["zero_curve"].items()}},
        "layers": {"macro_neutral": {"v0": was_v0}},
        "fair_value": {"central": was_price},
        "meta": {"payload_sha256": "prev"},
    }
    steps = attribute(previous, A, run=run)
    now_v0, now_price = run(A)
    d_v0, d_price = total(steps)
    assert d_v0 == pytest.approx(now_v0 - was_v0, abs=0.5)
    assert d_price == pytest.approx(now_price - was_price, abs=2.0)


def test_payload_carries_the_snapshot():
    from model.payload import build_payload

    payload = build_payload(run_release(gates=False), with_slow=False)
    assert payload["inputs"]["book_version"]
    assert "changes" in payload
