# -*- coding: utf-8 -*-
"""Полоса A-V9: оси на общем пути книги (внешний аудит 30.09.2026, находка A01).

Правило — ключом книги `valuation.uncertainty.axis_merge` (схема
`model/book_schema.py` тем же коммитом): `add` — подмены двух осей на равном
пути складываются (сдвиг + сдвиг, множитель × множитель); без ключа — прежний
словарь по пути бит в бит (поздняя ось заменяет раннюю, в своём центре —
нулевым сдвигом). Несоставимые оси на одном пути — отказ при загрузке книги,
с ключом и без. Книга — синтетическая (`tests/fixtures/toy_book`); проверка на
книге «Ленты» — под маркером `needs_book`.
"""
from __future__ import annotations

import copy

import pytest

from model.book import BookError, get_path, validate_book
from model.engine import with_overrides
from model.uncertainty import (axis_overrides, axis_spec, band_chunk, draw_overrides, evaluate,
                               merge_axis_overrides)
from tests.toy import toy_book

pytestmark = pytest.mark.tact


# ================================================== A01: оси полосы на общем пути


def _overlapping_book(merge: str | None = None) -> dict:
    """Синтетическая книга с двумя осями-сдвигами на двух общих путях: ось 0 —
    LT всех четырёх режимов, новая ось 1 — LT «частичного» и «полного»."""
    A = toy_book()
    U = A["valuation"]["uncertainty"]
    U["axes"].insert(1, {"name": "Сходимость приобретённого периметра",
                         "paths": ["margin.regimes.partial.target.LT",
                                   "margin.regimes.full.target.LT"],
                         "shift_low": -0.004, "shift_high": 0.003})
    if merge is not None:
        U["axis_merge"] = merge
    validate_book(A)
    return A


def _axes(A: dict) -> list[dict]:
    return [axis_spec(a) for a in A["valuation"]["uncertainty"]["axes"]]


def test_shifts_on_a_shared_path_add_scales_multiply_and_values_refuse():
    ov: dict = {}
    merge_axis_overrides(ov, {"x": {"__shift__": -0.006}}, "a")
    merge_axis_overrides(ov, {"x": {"__shift__": 0.0}}, "b")
    assert ov == {"x": {"__shift__": -0.006}}
    merge_axis_overrides(ov, {"x": {"__shift__": 0.002}, "z": 5.0}, "c")
    assert ov["x"]["__shift__"] == pytest.approx(-0.004, abs=1e-15) and ov["z"] == 5.0
    merge_axis_overrides(ov, {"y": {"__scale__": 1.1}}, "a")
    merge_axis_overrides(ov, {"y": {"__scale__": 0.9}}, "b")
    assert ov["y"]["__scale__"] == pytest.approx(0.99)
    for clash in ({"x": 0.07}, {"y": {"__shift__": 0.01}}, {"x": {"__scale__": 1.2}},
                  {"z": 6.0}):
        with pytest.raises(BookError, match="уже подменён"):
            merge_axis_overrides(ov, clash, "d")


def test_without_the_key_the_later_axis_replaces_the_earlier_as_before():
    """Нет ключа (и `replace` явно) — словарь по пути, как у 850oa: поздняя ось
    заменяет раннюю, в своём центре — нулевым сдвигом."""
    A, explicit = _overlapping_book(), _overlapping_book("replace")
    axes = _axes(A)
    s = [-1.0, 0.0] + [0.0] * (len(axes) - 2)
    legacy: dict = {}
    for ax, sj in zip(axes, s):
        legacy.update(axis_overrides(A, ax, sj))
    assert draw_overrides(A, axes, s) == legacy == draw_overrides(explicit, axes, s)
    assert legacy["margin.regimes.partial.target.LT"] == {"__shift__": 0.0}
    assert legacy["margin.regimes.stress.target.LT"] == {"__shift__": -0.006}


def test_with_add_a_band_point_on_one_axis_is_that_axis_alone_in_any_order():
    """`axis_merge: add`: прогон полосы, где сдвинута одна ось, — строка
    чувствительности этой оси; перестановка осей прогон не меняет."""
    A = _overlapping_book("add")
    axes = _axes(A)
    for j in (0, 1):
        for side in (-1.0, 1.0):
            s = [0.0] * len(axes)
            s[j] = side
            band = band_chunk(A, axes, [s])[0]["central"]
            alone = evaluate(A, axis_overrides(A, axes[j], side))[3].central
            assert band == pytest.approx(alone, rel=1e-12), (axes[j]["name"], side)
            trial = with_overrides(A, draw_overrides(A, axes, s))
            single = with_overrides(A, axis_overrides(A, axes[j], side))
            for path in axes[j]["paths"]:
                assert get_path(trial, path) == pytest.approx(get_path(single, path), abs=1e-15)
    s = [0.0] * len(axes)
    s[0], s[1] = -1.0, 0.5
    order = list(range(len(axes)))
    order[0], order[1] = 1, 0
    a = band_chunk(A, axes, [s])[0]["central"]
    b = band_chunk(A, [axes[i] for i in order], [[s[i] for i in order]])[0]["central"]
    assert a == pytest.approx(b, rel=1e-12)
    # и это не прежний ответ: с `replace` ось 0 на общих путях не работает
    old = band_chunk(_overlapping_book(), axes, [[-1.0] + [0.0] * (len(axes) - 1)])[0]["central"]
    new = band_chunk(A, axes, [[-1.0] + [0.0] * (len(axes) - 1)])[0]["central"]
    assert abs(new - old) > 1.0


@pytest.mark.parametrize("axis,match", [
    ({"name": "значение на пути сдвига", "path": "margin.regimes.partial.target.LT",
      "low": 0.05, "high": 0.07}, "несоставимо"),
    ({"name": "множитель на пути сдвига", "paths": ["margin.regimes.full.target.LT"],
      "kind": "scale", "low": 0.9, "high": 1.1}, "несоставимо"),
    ({"name": "значение на пути-предке", "path": "margin.regimes.stress.target",
      "low": 0.03, "high": 0.05}, "несоставимо"),
    ({"name": "значение на пути значения", "path": "valuation.beta_u",
      "low": 0.5, "high": 0.6}, "несоставимо"),
])
def test_two_axes_on_one_path_must_be_composable(axis, match):
    """Значение, выбор или множитель на пути (или на пути-предке), который
    сдвигает другая ось, — отказ книги: порядок осей решал бы исход."""
    A = toy_book()
    A["valuation"]["uncertainty"]["axes"].append(axis)
    with pytest.raises(BookError, match=match):
        validate_book(A)


def test_composable_axes_on_one_path_pass_the_guard():
    """Сдвиг со сдвигом (в том числе путь-предок с потомком — их ядро складывает
    само, применяя по очереди) и множитель с множителем книгу не останавливают."""
    A = _overlapping_book()
    U = A["valuation"]["uncertainty"]["axes"]
    U.append({"name": "сдвиг всей траектории", "paths": ["margin.regimes.stress.target"],
              "shift_low": -0.001, "shift_high": 0.001})
    U.append({"name": "множитель 1", "path": "capex.infra_capex_per_net_m2", "kind": "scale",
              "low": 0.9, "high": 1.1})
    U.append({"name": "множитель 2", "path": "capex.infra_capex_per_net_m2", "kind": "scale",
              "low": 0.8, "high": 1.2})
    validate_book(A)


@pytest.mark.needs_book
def test_on_the_book_every_axis_keeps_its_full_effect_with_the_others_at_centre():
    """Книга «Ленты»: оси A-C0 и A-C1s делят цели «частичного» и «полного» 2031+.
    Со сложением каждая ось на концах при прочих в центре меняет книгу так же, как
    одна, — на всех своих путях; без сложения ось A-C0 на общих путях молчит."""
    from collections import Counter

    from model.book import book

    A = copy.deepcopy(book())
    A["valuation"]["uncertainty"]["axis_merge"] = "add"
    axes = _axes(A)
    shared = {p for p, c in Counter(p for ax in axes for p in ax["paths"]).items() if c > 1}
    assert shared, "в книге нет общих путей осей — тест ничего не проверяет"
    for j, ax in enumerate(axes):
        if not shared & set(ax["paths"]):
            continue
        for side in (-1.0, 1.0):
            s = [0.0] * len(axes)
            s[j] = side
            trial = with_overrides(A, draw_overrides(A, axes, s))
            single = with_overrides(A, axis_overrides(A, ax, side))
            for path in ax["paths"]:
                assert get_path(trial, path) == pytest.approx(get_path(single, path), abs=1e-15), (
                    ax["name"], side, path)
            band = band_chunk(A, axes, [s])[0]["central"]
            alone = evaluate(A, axis_overrides(A, ax, side))[3].central
            assert band == pytest.approx(alone, rel=1e-9), (ax["name"], side)
    B = copy.deepcopy(A)
    B["valuation"]["uncertainty"]["axis_merge"] = "replace"
    first = next(j for j, ax in enumerate(axes) if shared & set(ax["paths"]))
    s = [0.0] * len(axes)
    s[first] = -1.0
    assert abs(band_chunk(B, axes, [s])[0]["central"] - band_chunk(A, axes, [s])[0]["central"]) > 50
