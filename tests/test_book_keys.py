# -*- coding: utf-8 -*-
"""Закрытые ключи книги, обязательные ключи и строгие подмены.

Опечатка в ключе однородности чека молча выключала бы правило Р9 (у 850oa
−514 ₽ точки); книга без обязательного ключа молча посчиталась бы по
умолчанию; `with_overrides` на незнакомом последнем ключе молча создал бы
новый ключ — «чувствительность» с Δ = 0 без ошибки. Всё это — отказ. Здесь же
— проверки строк моста (выплата выводится из строки), фактов якоря и ключей-годов.
"""
from __future__ import annotations

import copy

import pytest

from model.book import REQUIRED_KEYS, BookError, Cell, validate_book
from model.core import run_cell
from model.engine import central_price, run_release, with_overrides
from tests.toy import toy_book

# Такт (ops/run.sh, TACT_TESTS): закрытая схема книги — быстрая, защищает число.
pytestmark = pytest.mark.tact


def _book() -> dict:
    return toy_book()


def test_the_book_passes():
    validate_book(toy_book())


@pytest.mark.parametrize("block,typo,key", [
    (("revenue", "ticket_lt_homogeneity"), "ramp_fromm", "ramp_from"),
    (("joint", "regime_update"), "sigma_PP", "sigma_pp"),
    (("financing", "spread_fair"), "fixd", "fixed"),
])
def test_a_typo_in_a_closed_block_is_refused(block, typo, key):
    A = _book()
    node = A[block[0]][block[1]]
    node[typo] = node.pop(key)
    with pytest.raises(BookError, match=typo):
        validate_book(A)


def test_a_typo_in_the_homogeneity_block_does_not_reach_the_release():
    """Книга в памяти, мимо `load_book`: отказ в самой сборке выпуска и в клетке."""
    A = _book()
    A["revenue"]["ticket_lt_homogeneity"]["ramp_fromm"] = A["revenue"]["ticket_lt_homogeneity"].pop("ramp_from")
    with pytest.raises(BookError, match="ramp_fromm"):
        run_release(A, gates=False)
    with pytest.raises(BookError, match="ramp_fromm"):
        central_price(A)


@pytest.mark.parametrize("path,value,match", [
    ("revenue.ticket_lt_homogeneity.reference_world", "X", "такого мира нет"),
    ("financing.spread_fair.float.stres", 0.02, "stres"),
    ("valuation.headline.method", "structural", "structural"),
    ("valuation.headline.method", "Intrinsic", "Intrinsic"),
    ("margin.season_from_period", "2027", "season_from_period"),
    ("margin.season_from_period", 2027, "season_from_period"),
    ("financing.fixed_rate_legacy_until", 2027, "fixed_rate_legacy_until"),
    ("financing.rate_baskets", [{"name": "x", "share": 1.5, "rate": {"fixed": 0.1},
                                 "until": "2027H2"}], "share"),
    ("financing.rate_baskets", [{"name": "x", "share": 1.0, "rate": {"fixed": 0.1},
                                 "until": "2027"}], "until"),
    ("financing.rate_baskets", [{"name": "x", "share": 0.6, "rate": {"fixed": 0.1},
                                 "until": "2027H2"},
                                {"name": "y", "share": 0.6, "rate": {"key_plus": 0.02},
                                 "until": "2028H2"}], "больше единицы"),
    ("financing.rate_baskets", [{"name": "x", "share": 0.5, "rate": 0.1, "until": "2027H2"}],
     "fixed"),
    ("financing.rate_baskets", [{"name": "x", "share": 0.5, "rate": {"floating": 0.02},
                                 "until": "2027H2"}], "floating"),
    ("financing.rate_baskets", [{"name": "x", "share": 0.5, "rate": {"fixed": 0.1, "key_plus": 0.0},
                                 "until": "2027H2"}], "key_plus"),
    ("financing.dividend_ladder", [], "dividend_ladder"),
    ("financing.dividend_ladder", [{"max_leverage": 1.5, "payout_max": 1.0},
                                   {"max_leverage": 1.0, "payout_max": 2.0}], "возрастанию"),
    ("financing.dividend_ladder", [{"max_leverage": None, "payout_max": 1.0},
                                   {"max_leverage": 1.0, "payout_max": 2.0}], "последней"),
    ("financing.dividend_timing", "annual", "dividend_timing"),
    ("financing.dividend_timing", True, "dividend_timing"),
    ("financing.dividend_net_debt_basis", "gross", "dividend_net_debt_basis"),
    ("facts.anchor.fcfe_ytd", 1.0, "fcfe_ytd"),         # без годового правила — ключ-пустышка
    ("margin.season_free_halves", -1, "season_free_halves"),
    ("margin.season_free_halves", 2.0, "season_free_halves"),
    ("valuation.terminal.half_rate_convention", "annual", "half_rate_convention"),
    ("valuation.headline.jump_guard.median_pct", 1.5, "jump_guard"),
    ("valuation.headline.diagnostics", "mean", "diagnostics"),
    ("facts.anchor.period", "2026H2", "facts.anchor.period"),
    ("facts.anchor.cash", None, "facts.anchor.cash"),
    ("meta.bridge_as_of", "18.09.2026", "bridge_as_of"),
])
def test_bad_values_are_refused(path, value, match):
    A = with_overrides(_book(), {path: value}, create=True)
    with pytest.raises(BookError, match=match):
        validate_book(A)


def test_the_annual_dividend_keys_are_refused_where_they_cannot_be_read():
    """Годовое правило лестницы (`financing.dividend_timing`) — только с
    лестницей; факт `facts.anchor.fcfe_ytd` обязателен, когда выплата по году
    якоря попадает в горизонт выплат (иначе ноль молча исказил бы первую
    выплату), и только числом; вне этого случая без него книга читается."""
    A = _book()
    A["financing"]["dividend_timing"] = "annual_next_h1"
    validate_book(A)                                    # якорь 2026: выплата по нему в 2027 < 2028
    B = copy.deepcopy(A)
    del B["financing"]["dividend_ladder"]
    with pytest.raises(BookError, match="dividend_ladder"):
        validate_book(B)
    C = copy.deepcopy(A)
    C["financing"]["dividends_from_year"] = int(C["facts"]["anchor"]["period"][:4]) + 1
    with pytest.raises(BookError, match="fcfe_ytd"):
        validate_book(C)
    with pytest.raises(BookError, match="fcfe_ytd"):
        run_cell(C, Cell.build(C, "H", "partial", "base"))
    C["facts"]["anchor"]["fcfe_ytd"] = "−5"
    with pytest.raises(BookError, match="fcfe_ytd"):
        validate_book(C)
    C["facts"]["anchor"]["fcfe_ytd"] = -5.0
    validate_book(C)


@pytest.mark.parametrize("path", ["joint.regime_update.sigma_pp",
                                  "joint.regime_update.max_shift_pp", "financing.spread_fair.fixed.stress",
                                  "valuation.headline.jump_guard.median_pct",
                                  "valuation.terminal.half_rate_convention",
                                  "valuation.headline.method"])
def test_a_missing_required_key_is_refused(path):
    A = _book()
    node, keys = A, path.split(".")
    for k in keys[:-1]:
        node = node[k]
    del node[keys[-1]]
    with pytest.raises(BookError, match=keys[-1]):
        validate_book(A)


# ------------------------------------------------ обязательные ключи


def _set(A: dict, dotted: str, value) -> dict:
    node, keys = A, dotted.split(".")
    for key in keys[:-1]:
        node = node[key]
    node[keys[-1]] = value
    return A


def _drop(A: dict, dotted: str) -> dict:
    node, keys = A, dotted.split(".")
    for key in keys[:-1]:
        node = node[key]
    del node[keys[-1]]
    return A


def _refused_as_missing(A: dict, dotted: str) -> None:
    """Отказ на пропуске — и у книги, и у клетки мимо `load_book`; текст
    называет ключ."""
    for call in (lambda: validate_book(A),
                 lambda: run_cell(A, Cell.build(A, "H", "partial", "base"))):
        with pytest.raises(BookError) as caught:
            call()
        assert dotted in str(caught.value), (dotted, str(caught.value))


@pytest.mark.parametrize("dotted", sorted(REQUIRED_KEYS))
def test_a_missing_required_key_or_block_is_refused(dotted):
    """Нет обязательного ключа или блока — отказ, а не умолчание."""
    _refused_as_missing(_drop(_book(), dotted), dotted)


def test_the_version_machine_is_gone():
    """Машины версий 850oa (переключатели «книги 1.5») в ядре нет: методы в
    коде — действующие, ключ-переключатель заводится только при смене метода."""
    from model import book as B
    from model.book_schema import SCHEMA

    for name in ("SWITCHES_15", "REQUIRED_15", "refuse_pre_15", "PRE_15"):
        assert not hasattr(B, name), name
    # `valuation.terminal` вернулся с ОДНИМ ключом — соглашением полугодовой
    # ставки терминала (решение ведущего, P2): у Ленты оно другое, чем у 850oa.
    assert SCHEMA["valuation.terminal"] == {"half_rate_convention"}
    for block, key in (("valuation", "roll_along_forwards"),
                       ("capex", "da_method"), ("financing", "cash_carry"),
                       ("financing", "gross_debt_net_of_opc_growth"),
                       ("revenue", "other_growth_in_terminal")):
        assert key not in SCHEMA[block], f"{block}.{key}"
    assert "enabled" not in SCHEMA["revenue.ticket_lt_homogeneity"]
    assert "shrink_by_se" not in SCHEMA["joint.regime_update"]
    assert "noncredit_spread_to_equity" not in SCHEMA["valuation.headline"]


# ------------------------------------------- строки моста и выплаты по ним


def _item(A: dict, item_id: str) -> dict:
    return next(row for row in A["bridge"]["items"] if row["id"] == item_id)


def test_a_separate_payment_list_is_gone_and_a_settlement_must_land_in_the_horizon():
    """Отдельного списка выплат больше нет: выплата выводится из строки моста
    (`settle_period`, `settle_amount`). Период расчёта вне горизонта сетки —
    отказ: строка снялась бы, а выплата не попала бы в путь долга; сумма
    расчёта без периода — отказ."""
    from model.book_schema import SCHEMA

    assert "scheduled_payments" not in SCHEMA["financing"]
    A = _book()
    settled = next(row for row in A["bridge"]["items"] if row.get("settle_period"))
    settled["settle_period"] = "2040H1"
    with pytest.raises(BookError, match="вне горизонта"):
        validate_book(A)
    B = _book()
    row = next(r for r in B["bridge"]["items"] if not r.get("settle_period"))
    row["settle_amount"] = 1.0
    with pytest.raises(BookError, match="settle_amount без settle_period"):
        validate_book(B)
    C = _book()
    C["financing"]["scheduled_payments"] = []
    with pytest.raises(BookError, match="scheduled_payments"):
        validate_book(C)


@pytest.mark.parametrize("field,value,match", [
    ("kind", "liability", "kind"),
    ("amount", "27.6", "amount"),
    ("accrete_rate_half", -1.0, "accrete_rate_half"),
    ("accrete_rate_annual", 0.1, "accrete_rate_annual"),
    ("settle_amount", "38.2", "settle_amount"),
    ("settle_period", "2028", "settle_period"),
    ("as_of", "2026/09/18", "as_of"),
    ("haircut", 0.1, "haircut"),
    ("amoun", 1.0, "amoun"),
])
def test_bad_bridge_rows_are_refused(field, value, match):
    A = _book()
    row = next(r for r in A["bridge"]["items"] if r["kind"] == "claim")
    row[field] = value
    with pytest.raises(BookError, match=match):
        validate_book(A)


def test_asset_haircut_is_a_share_and_ids_are_unique():
    A = _book()
    asset = next(r for r in A["bridge"]["items"] if r["kind"] == "asset")
    asset["haircut"] = 1.5
    with pytest.raises(BookError, match="haircut"):
        validate_book(A)
    B = _book()
    B["bridge"]["items"].append(dict(B["bridge"]["items"][0]))
    with pytest.raises(BookError, match="уникальное"):
        validate_book(B)
    C = _book()
    del C["bridge"]["items"][0]["as_of"]
    with pytest.raises(BookError, match="as_of"):
        validate_book(C)


def test_a_bridge_row_is_addressed_by_its_id():
    """Путь `bridge.items[<id>].amount` — подмена строки моста по id; незнакомый
    id — отказ, а не новая строка."""
    A = _book()
    row = A["bridge"]["items"][0]
    B = with_overrides(A, {f"bridge.items[{row['id']}].amount": row["amount"] + 1.0})
    assert _item(B, row["id"])["amount"] == row["amount"] + 1.0
    assert central_price(B) != central_price(A)
    with pytest.raises(BookError, match="нет строки"):
        with_overrides(A, {"bridge.items[no_such_row].amount": 1.0})


def _network_segment(A: dict) -> str:
    """Первый сегмент сети с площадью (`yoy`/`level`) — id из книги, не литерал."""
    return next(sid for sid, seg in A["revenue"]["segments"].items() if seg["mode"] != "revenue")


@pytest.mark.parametrize("dotted,value", [
    ("capex.disposal_proceeds_pct", 0.0), ("capex.maintenance_area_share", 0.0),
    ("revenue.segments.<seg>.new_space_density", 1.0), ("valuation.distress.cost_pct_ev", 0.0),
])
def test_zero_and_unit_numbers_are_numbers_not_old_books(dotted, value):
    """Числа правил в своих границах законны и тогда, когда совпадают со
    значением прежней книги: прогулка от старой модели и проверки правил
    ставят их числами."""
    A = _book()
    dotted = dotted.replace("<seg>", _network_segment(A))
    A = _set(A, dotted, value)
    validate_book(A)
    assert central_price(A) != central_price(toy_book())


def test_an_override_on_an_unknown_path_is_refused():
    A = toy_book()
    with pytest.raises(BookError, match="valuation.beta_uu"):
        with_overrides(A, {"valuation.beta_uu": 0.9})
    with pytest.raises(BookError, match="valuaton"):
        with_overrides(A, {"valuaton.beta_u": 0.9})
    with pytest.raises(BookError, match="ramp_fromm"):
        with_overrides(A, {"revenue.ticket_lt_homogeneity.ramp_fromm": 2027})
    with pytest.raises(BookError, match="не блок"):
        with_overrides(A, {"valuation.beta_u.x": 0.9})
    # Сдвиг несуществующей траектории — отказ и при create=True: сдвигать нечего.
    with pytest.raises(BookError, match="capex.maintenance_pct.basee"):
        with_overrides(A, {"capex.maintenance_pct.basee": {"__shift__": 0.001}}, create=True)


def test_create_is_explicit_and_builds_missing_blocks():
    A = toy_book()
    B = with_overrides(A, {"valuation.new_block.flag": True, "capex.new_key": 0.0}, create=True)
    assert B["valuation"]["new_block"] == {"flag": True} and B["capex"]["new_key"] == 0.0
    assert "new_block" not in A["valuation"], "подмена утекла в кэшированную книгу"
    # Существующий путь — как прежде, с create и без.
    assert with_overrides(A, {"valuation.beta_u": 0.62}) == with_overrides(A, {"valuation.beta_u": 0.62}, create=True)


# --------------------------------------------------------- закрытая схема книги

def _key_paths(node, trail=()):
    """Каждый ключ книги: (путь до словаря, ключ). Элемент списка — индексом."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield trail, key
            yield from _key_paths(value, trail + (key,))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _key_paths(value, trail + (i,))


def _renamed(A: dict, trail: tuple, key) -> dict:
    B = copy.deepcopy(A)
    node = B
    for step in trail:
        node = node[step]
    node[f"{key}_typo"] = node.pop(key)
    return B


def test_every_key_of_the_book_with_a_typo_is_refused():
    """Опечатка в имени ЛЮБОГО ключа книги — блока, ключа, периода траектории,
    срока кривой — отказ, а не молчаливое умолчание (`headline_typo` без схемы
    включал бы прежний метод: +1 005 ₽ точки)."""
    A = _book()
    keys = list(_key_paths(A))
    assert len(keys) > 300
    passed = []
    for trail, key in keys:
        try:
            validate_book(_renamed(A, trail, key))
        except BookError:
            continue
        passed.append(".".join(map(str, trail + (key,))))
    assert not passed, f"опечатка прошла молча ({len(passed)}): {passed[:10]}"


def test_the_schema_names_the_path_of_the_typo():
    A = _book()
    A["valuation"]["headline_typo"] = A["valuation"].pop("headline")
    with pytest.raises(BookError, match=r"valuation: незнакомый ключ headline_typo"):
        validate_book(A)
    A = _book()
    A["margin"]["regimes"]["partial"]["target"]["LT_typo"] = (
        A["margin"]["regimes"]["partial"]["target"].pop("LT"))
    with pytest.raises(BookError, match=r"margin\.regimes\.partial\.target: в траектории ключи не периоды: LT_typo"):
        validate_book(A)


def test_the_keys_written_by_the_tools_are_in_the_schema():
    """Перезаякоривание, живые входы и пересборка миров дописывают в книгу свои
    ключи — они в схеме; наблюдение A-P2u с `sd` вместо `se` — отказ (иначе
    наблюдению молча досталась бы нулевая ошибка)."""
    from model.book_schema import unknown_keys

    A = _book()
    A["meta"].update(curve_as_of="2026-09-18")
    A["market"]["price_date"] = "2026-09-25"
    A["capex"]["maintenance_area_base"] = 11500.0
    A["facts"].update(da_straight_line={"legacy": 40.0, "legacy_halves": 2, "vintages": []})
    A["facts"]["segments"][_network_segment(A)].update(new_area_dense_cohorts=2,
                                                        eff_area_end=11600.0)
    A["joint"]["regime_update"]["observations"] = {
        "2026H2": 0.047, "2027H1": {"value": 0.05, "se": 0.003}}
    assert unknown_keys(A) == []

    A["joint"]["regime_update"]["observations"]["2027H1"] = {"value": 0.05, "sd": 0.003}
    with pytest.raises(BookError, match="observations.*sd"):
        validate_book(A)


def test_the_closed_blocks_agree_with_the_schema():
    """Два объявления одних ключей не расходятся: закрытые блоки `model/book.py`
    и схема книги."""
    from model import book as B
    from model.book_schema import SCHEMA

    for block, key, known in B._CLOSED_BLOCKS:
        assert SCHEMA[f"{block}.{key}"] == known, f"{block}.{key}"
    for where in ("valuation.uncertainty.axes[]", "sensitivities[]"):
        assert SCHEMA[where] == B.AXIS_KEYS, where
    assert SCHEMA["reverse_dcf[]"] == B.REVERSE_DCF_KEYS


# Ключи правил диагностик медианы и их значения.
KEYS_151 = (("valuation.uncertainty", "reverse_bounds", "follow_center"),
            ("valuation.uncertainty", "median_refine", 1))


def _with_keys_151() -> dict:
    A = _book()
    for block, key, value in KEYS_151:
        _node(A, block)[key] = value
    return A


def _node(A: dict, dotted: str) -> dict:
    for key in dotted.split("."):
        A = A[key]
    return A


def test_the_keys_of_the_mapping_and_median_rules_are_in_the_schema():
    """Ключи границ оси обратного DCF и уточнения медианы — в схеме; их
    опечатка — отказ."""
    A = _with_keys_151()
    validate_book(A)
    for block, key, _ in KEYS_151:
        with pytest.raises(BookError, match=f"{key}_typo"):
            validate_book(_renamed(A, tuple(block.split(".")), key))


@pytest.mark.parametrize("block,key,bad", [
    ("valuation.uncertainty", "reverse_bounds", "follow"),
    ("valuation.uncertainty", "reverse_bounds", False),
    ("valuation.uncertainty", "median_refine", True),
    ("valuation.uncertainty", "median_refine", 2),
    ("valuation.uncertainty", "median_refine", 1.0),
    ("valuation.uncertainty", "median_refine", "1"),
])
def test_an_unknown_value_of_the_rule_keys_is_refused(block, key, bad):
    A = _book()
    _node(A, block)[key] = bad
    with pytest.raises(BookError, match=key):
        validate_book(A)


@pytest.mark.parametrize("block,key,default", [
    ("valuation.uncertainty", "reverse_bounds", "fixed"),
    ("valuation.uncertainty", "median_refine", 0),
])
def test_the_rule_keys_name_their_default(block, key, default):
    """Без ключа — значение по умолчанию; записанное явно оно читается так же."""
    from model import book as B

    reader = getattr(B, key)
    A = _book()
    del _node(A, block)[key]
    assert reader(A) == default
    _node(A, block)[key] = default
    validate_book(A)
    assert reader(A) == default
