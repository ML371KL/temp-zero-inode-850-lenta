# -*- coding: utf-8 -*-
"""Справочные наборы таблиц книги (`results_spec.yaml`) и блок «что оправдывает
рыночную цену» (`model/book_results.py`, этап I1c).

Блок перенесён из 850oa, где точка стояла НИЖЕ рынка: он брал конец строки с
большим центром и мерил «≥ рынка». У «Ленты» точка выше рынка, и каждая строка
«достигала» рынка, уходя от него. Теперь направление — к рынку, по знаку
разрыва точки и цены; оба случая проверяются на заглушке выпуска.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import model.book_results as br
from model.book import BookError

PX = 1600.0
ROWS = [dict(name="строка A", low=0.1, high=0.3, central_low=2500.0, central_high=3500.0),
        dict(name="строка B", low=-1.0, high=1.0, central_low=1500.0, central_high=3100.0),
        dict(name="строка C", low=5.0, high=9.0, central_low=1900.0, central_high=900.0)]
SPEC = dict(control_cases=[], reference_regime_tables=[],
            justification_ends=[{"name": n, "row": n} for n in ("строка A", "строка B", "строка C")],
            full_return_reference=dict(name="P(полная)", value=0.0, note="справка"))


def _book() -> dict:
    probs = {"stress": 0.4, "floor": 0.3, "partial": 0.2, "full": 0.1}
    return {"market": {"price": PX}, "facts": {"anchor": {"ebitda_ltm": 100.0}},
            "joint": {"world_prob": {"N": 0.5, "M": 0.5},
                      "regime_given_world": {"N": dict(probs), "M": dict(probs)}}}


def _release(central: float) -> SimpleNamespace:
    return SimpleNamespace(
        layers={"analytical": SimpleNamespace(v0=500.0)},
        fair_value=SimpleNamespace(central=central,
                                   ev_comparison={"analytical": SimpleNamespace(v_star=400.0)}))


@pytest.fixture
def stubs(monkeypatch):
    """Центр при справочной таблице режимов и смесь слоёв — заглушки: блок
    проверяется на выборе концов, а не на ядре."""
    seen = {}

    def evaluate(A, overrides):
        seen["table"] = overrides["joint.regime_given_world"]
        return None, None, None, SimpleNamespace(central=1700.0)

    monkeypatch.setattr(br, "evaluate", evaluate)
    monkeypatch.setattr(br, "layer_mix", lambda A, layers, attr: {"low": 2800.0, "central": 3000.0,
                                                                  "high": 3200.0})
    return seen


def test_above_the_market_the_rows_take_the_end_toward_it(stubs):
    out = br.market_price_justification(_book(), _release(3000.0), ROWS, SPEC)
    assert out["direction"] == "down"
    cases = {c["assumption"]: c for c in out["cases"]}
    assert (cases["строка A"]["value"], cases["строка A"]["central"]) == (0.1, 2500.0)
    assert not cases["строка A"]["reaches_market"]
    assert (cases["строка B"]["value"], cases["строка B"]["central"]) == (-1.0, 1500.0)
    assert cases["строка B"]["reaches_market"], "центр 1 500 ниже рынка 1 600 — рынок достигнут"
    assert (cases["строка C"]["value"], cases["строка C"]["central"]) == (9.0, 900.0)
    lam = cases["A-P1c вес собственного макро-взгляда"]
    assert (lam["value"], lam["central"]) == ("λ = 0", 2800.0) and "нижней" in lam["note"]
    full = cases["P(полная)"]
    assert full["central"] == 1700.0 and not full["reaches_market"]
    assert all(t["full"] == 0.0 for t in stubs["table"].values())
    assert out["any_single_change_reaches_market"]


def test_below_the_market_the_rows_take_the_upper_end_as_in_850oa(stubs):
    out = br.market_price_justification(_book(), _release(1000.0), ROWS, SPEC)
    assert out["direction"] == "up"
    cases = {c["assumption"]: c for c in out["cases"]}
    assert (cases["строка A"]["value"], cases["строка A"]["central"]) == (0.3, 3500.0)
    assert cases["строка A"]["reaches_market"]
    assert (cases["строка C"]["value"], cases["строка C"]["central"]) == (5.0, 1900.0)
    lam = cases["A-P1c вес собственного макро-взгляда"]
    assert (lam["value"], lam["central"]) == ("λ = 1", 3200.0) and "верхней" in lam["note"]
    assert cases["P(полная)"]["reaches_market"], "1 700 ≥ 1 600 при точке ниже рынка"


def test_a_justification_row_missing_from_the_book_is_refused(stubs):
    spec = dict(SPEC, justification_ends=[{"name": "нет", "row": "строки нет"}])
    with pytest.raises(BookError, match="строки чувствительности «строки нет»"):
        br.market_price_justification(_book(), _release(3000.0), ROWS, spec)


def test_the_spec_file_has_exactly_its_keys(tmp_path):
    bad = tmp_path / "results_spec.yaml"
    bad.write_text("control_cases: []\njustification_ends: []\nfull_return_upper: {}\n"
                   "reference_regime_tables: []\n", encoding="utf-8")
    with pytest.raises(BookError, match="ожидаются ровно ключи"):
        br.results_spec(bad)


@pytest.mark.needs_book
def test_the_lenta_spec_names_rows_and_periods_of_the_book():
    """Строки «что оправдывает цену» — строки чувствительности книги; контрольные
    наблюдения A-P2u — полугодия отчёта и дальше, по одному числу или {value, se}."""
    from model.book import book
    from model.book import period_index

    A = book()
    spec = br.results_spec()
    names = {s["name"] for s in A["sensitivities"]}
    assert {e["row"] for e in spec["justification_ends"]} <= names
    first = period_index(A["joint"]["regime_update"]["demo_period"])
    for case in spec["control_cases"]:
        for period, obs in case["observations"].items():
            assert period_index(period) >= first, (case["label"], period)
            value = obs["value"] if isinstance(obs, dict) else obs
            assert 0.03 < value < 0.10 and (not isinstance(obs, dict) or obs["se"] > 0)
    assert 0.0 <= spec["full_return_reference"]["value"] <= 1.0
