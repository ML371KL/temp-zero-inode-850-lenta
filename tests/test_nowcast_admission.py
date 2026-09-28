# -*- coding: utf-8 -*-
"""Правило допуска нау-каста к правилу A-P2u — квартальное (D15).

У 850oa правило считало полугодия (4 отчёта). У Ленты отчётность квартальная,
и D15 задаёт: допуск — не раньше чем после 8 отчётных кварталов вне выборки
при отношении MSE уравнения к ЛУЧШЕМУ эталону (включая ожидание модели без
индикаторов) не больше 0,8; понижение до справочного — если на последних 4
отношение больше 1,0. Допуск — не подключение: решение остаётся за
владельцем. Кварталы, где главный эталон сломан разрывом периметра
(«О'КЕЙ» нет в базе прошлого года и т. п.), в счёт допуска не идут.

Даты в тестах — от плановых дат отчётов (`journal.report_date`), а не
«сегодня»: табло сравнивает прогноз и эталон на момент «отчёт − 45 дней».
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from indicators import issuer, perimeter

from indicators.journal import (
    ADMISSION_MAX_MSE_RATIO,
    ADMISSION_MIN_EVENTS,
    DEMOTION_MSE_RATIO,
    DEMOTION_WINDOW,
    MAIN_BENCHMARK,
    MODEL_EXPECTATION,
    Journal,
    JournalError,
    admission_from_rows,
    admission_rule_text,
    mse_against_best,
    report_date,
)

# Такт (ops/run.sh, TACT_TESTS): слой индикаторов на фикстурах и двойниках —
# быстрый, в сеть не ходит (тесты `network` такт исключает выражением).
pytestmark = pytest.mark.tact

MARGIN = issuer.series("ebitda_margin_pre16")
FAR = 1e6
"""Ожидание модели, заведомо худшее любого эталона: тесты порогов меряют
уравнение против наивных эталонов, а у маржи строка без ожидания модели не
зачитывается вовсе."""


def _row(forecast, actual, **naive):
    """Строка табло в том виде, в каком её отдаёт `Journal.scoreboard`."""
    naive.setdefault(MODEL_EXPECTATION, actual + FAR)
    row = dict(forecast=forecast, actual=actual, version="lenta-margin-v1")
    for method, value in naive.items():
        row["naive_" + method] = value
        row["naive_" + method + "_error"] = abs(value - actual)
    return row


def test_the_rule_is_the_one_the_design_chose():
    """D15: 8 кварталов, порог 0,8, понижение при > 1,0 на последних 4."""
    assert ADMISSION_MIN_EVENTS == 8
    assert DEMOTION_WINDOW == 4
    assert ADMISSION_MAX_MSE_RATIO == pytest.approx(0.8)
    assert DEMOTION_MSE_RATIO == pytest.approx(1.0)
    text = admission_rule_text()
    assert "8 отчётных кварталов вне выборки" in text
    assert "разрывом периметра" in text
    assert "не больше 0,8" in text and "больше 1,0" in text
    assert "последних 4 отчётах" in text
    assert "решение владельца" in text


def test_fewer_than_eight_reports_only_accumulate():
    """Семь блестящих кварталов — ещё не трек-рекорд: ни допуска, ни понижения."""
    rows = [_row(0.070, 0.070, yoy_plus_shift=0.065) for _ in range(7)]
    status = admission_from_rows(rows, target=MARGIN, version="lenta-margin-v1")
    assert status.status == "accumulating"
    assert not status.admitted and not status.demoted
    assert status.events == 7 and status.events_needed == 1
    assert "ещё 1 отчёт" in status.reason
    assert status.recent_mse_ratio is None
    empty = admission_from_rows([], target=MARGIN)
    assert empty.events_needed == 8 and "ещё 8 отчётов" in empty.reason


def test_the_mse_ratio_is_computed_against_the_best_benchmark_by_hand():
    """Контрольный пересчёт: MSE прогноза / MSE ЛУЧШЕГО эталона на тех же отчётах.

    Сезонный наивный здесь точнее главного, и планка — он.
    """
    base = [
        (0.0700, 0.0710, 0.0680, 0.0700),
        (0.0670, 0.0660, 0.0690, 0.0670),
        (0.0720, 0.0715, 0.0690, 0.0705),
        (0.0680, 0.0690, 0.0710, 0.0700),
    ]
    rows = [_row(f, a, yoy_plus_shift=m, seasonal_naive=s) for f, a, m, s in base * 2]
    forecast = (0.0010 ** 2 + 0.0010 ** 2 + 0.0005 ** 2 + 0.0010 ** 2) / 4
    main = (0.0030 ** 2 + 0.0030 ** 2 + 0.0025 ** 2 + 0.0020 ** 2) / 4
    seasonal = 0.0010 ** 2
    ratio, best = mse_against_best(rows)
    assert best == "seasonal_naive"
    assert ratio == pytest.approx(forecast / min(main, seasonal))
    status = admission_from_rows(rows, target=MARGIN)
    # 0,8125 > 0,8: уравнение лучше сезонного, но недостаточно.
    assert status.status == "not_admitted" and not status.admitted and not status.demoted
    assert "сезонный наивный" in status.reason


def test_a_benchmark_missing_on_one_report_is_not_the_bar():
    """Эталон, которого на части отчётов не было, сравнивать не с чем."""
    rows = [_row(0.07, 0.07, yoy_plus_shift=0.069, seasonal_naive=0.07),
            _row(0.07, 0.07, yoy_plus_shift=0.069)]
    for row in rows:
        del row["naive_" + MODEL_EXPECTATION]
    ratio, best = mse_against_best(rows)
    assert best == "yoy_plus_shift" and ratio == 0.0


def test_a_tie_without_errors_is_not_an_admission():
    """Прогноз, ожидание модели и факт совпали на всех отчётах: MSE 0/0 — 1,0."""
    rows = [_row(0.07, 0.07, **{MODEL_EXPECTATION: 0.07}) for _ in range(8)]
    assert mse_against_best(rows) == (1.0, MODEL_EXPECTATION)
    status = admission_from_rows(rows, target=MARGIN)
    assert status.mse_ratio == 1.0
    assert status.status == "not_admitted" and not status.admitted and not status.demoted


@pytest.mark.parametrize("errors, scale, status", [
    ((8, 4, 0, 0) * 2, 0.80, "admitted"),        # ровно на пороге — допуск (≤)
    ((9, 0, 0, 0) * 2, 0.81, "not_admitted"),    # между 0,8 и 1,0
    ((10, 0, 0, 0) * 2, 1.00, "not_admitted"),   # ровно 1,0 — ещё не понижение (>)
    ((10, 1, 0, 0) * 2, 1.01, "demoted"),
])
def test_the_thresholds(errors, scale, status):
    """Пороги: допуск при ≤ 0,8, понижение при > 1,0 (на последних 4), между — «не допущено».

    Ошибки целые, чтобы отношение на пороге было ровно 0,8 и 1,0 в двоичной
    арифметике: эталон ошибается на 5 в каждом отчёте (MSE 25).
    """
    rows = [_row(float(error), 0.0, yoy_plus_shift=5.0) for error in errors]
    result = admission_from_rows(rows, target=MARGIN)
    assert result.mse_ratio == pytest.approx(scale, abs=1e-15)
    assert result.status == status
    assert result.admitted is (status == "admitted")
    assert result.demoted is (status == "demoted")


def test_a_bad_recent_record_demotes_despite_a_good_past():
    """Понижение смотрит на ПОСЛЕДНИЕ четыре отчёта и сильнее допуска."""
    good = [_row(0.070, 0.070, yoy_plus_shift=0.066) for _ in range(4)]
    bad = [_row(0.073, 0.070, yoy_plus_shift=0.072) for _ in range(4)]
    status = admission_from_rows(good + bad, target=MARGIN)
    assert status.mse_ratio <= ADMISSION_MAX_MSE_RATIO
    assert status.recent_mse_ratio > DEMOTION_MSE_RATIO
    assert status.status == "demoted" and status.demoted and not status.admitted


def test_admitted_is_not_connected():
    """«Допущено» — право владельца решить, а не подключение к цене."""
    rows = [_row(0.070, 0.070, yoy_plus_shift=0.066) for _ in range(8)]
    status = admission_from_rows(rows, target=MARGIN)
    assert status.admitted
    assert "автоматически не подключается" in status.reason
    assert set(status.as_dict()) >= {"status", "title", "reason", "events_needed",
                                     "mse_ratio", "best_benchmark", "rule"}


# ----------------------------------------------------- через журнал и версии


def _insert(journal, made: date, *, period, value=None, version="lenta-margin-v1",
            naive=None):
    """Запись задним числом: `made_at` подставляется при вставке (триггеры
    запрещают UPDATE). Только для тестов."""
    stamp = made.isoformat() + "T12:00:00+00:00"
    with sqlite3.connect(journal.path) as db:
        if value is not None:
            db.execute(
                "INSERT INTO forecasts (made_at, target, period, value, std_error, equation,"
                " version, inputs_sha, inputs, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (stamp, MARGIN, period, value, 0.01, "eq", version, "sha", "{}", ""))
        for method, item in (naive or {}).items():
            db.execute("INSERT INTO naive (made_at, target, period, method, value)"
                       " VALUES (?,?,?,?,?)", (stamp, MARGIN, period, method, item))


def _clean_quarters(start: str, count: int) -> list[str]:
    """Кварталы подряд, главный эталон которых НЕ сломан ни одной сделкой."""
    from indicators import periods

    out, period = [], start
    while len(out) < count:
        if not perimeter.broken_by(period, MAIN_BENCHMARK[MARGIN]):
            out.append(period)
        period = periods.next_period(period)
    return out


def test_only_reports_scored_by_the_current_version_count(tmp_path):
    """Трек-рекорд прежней формулы новой версии не принадлежит.

    Восемь чистых кварталов: первые четыре зачтены прогнозами прежней версии,
    последние четыре — текущей. Для текущей зачтено четыре, до решения — ещё
    четыре; без фильтра по версии — восемь, и отношение к лучшему эталону
    (ожиданию модели) — (0,05/0,20)².
    """
    journal = Journal(tmp_path / "j.sqlite")
    quarters = _clean_quarters("2027Q4", 8)
    for number, period in enumerate(quarters):
        published = report_date(period)
        version = "lenta-margin-v0" if number < 4 else "lenta-margin-v1"
        _insert(journal, published - timedelta(days=60), period=period, value=0.0705,
                version=version,
                naive={"yoy_plus_shift": 0.0680, MODEL_EXPECTATION: 0.0690})
        journal.record_actual(MARGIN, period, 0.0700, reported_on=published)

    current = journal.admission(MARGIN, version="lenta-margin-v1")
    assert current.events == 4 and current.events_needed == 4
    assert current.status == "accumulating"
    assert "(версия lenta-margin-v1)" in current.reason

    anyone = journal.admission(MARGIN)
    assert anyone.events == 8 and anyone.status == "admitted"
    assert anyone.mse_ratio == pytest.approx((0.0005 / 0.0010) ** 2)
    assert anyone.best_benchmark == MODEL_EXPECTATION
    assert journal.demoted(MARGIN) is False


def test_quarters_with_a_broken_perimeter_are_not_counted(tmp_path):
    """3 кв. 2026 – 3 кв. 2027: «О'КЕЙ» нет в базе прошлого года (D15).

    Такое событие печатается на табло с причиной, но в счёт допуска не идёт:
    главный эталон без приобретённой сети смещён, и уравнение «обгоняло» бы
    его арифметикой консолидации.
    """
    journal = Journal(tmp_path / "j.sqlite")
    for period in ("2026Q3", "2027Q2", "2027Q3", "2027Q4"):
        published = report_date(period)
        _insert(journal, published - timedelta(days=60), period=period, value=0.0705,
                naive={"yoy_plus_shift": 0.0680, MODEL_EXPECTATION: 0.0690})
        journal.record_actual(MARGIN, period, 0.0700, reported_on=published)
    board = {row["period"]: row for row in journal.scoreboard(MARGIN)}
    for period in ("2026Q3", "2027Q2", "2027Q3"):
        assert board[period]["admission_excluded"] is True, period
        assert "okey" in board[period]["perimeter_breaks"], period
        assert "в счёт допуска не идёт" in board[period]["note"]
    assert board["2027Q4"]["admission_excluded"] is False
    assert journal.admission(MARGIN).events == 1


# ------------------------------------ эталон «ожидание модели»


def test_the_best_benchmark_includes_the_model_expectation():
    """Лучший эталон выбирается ВМЕСТЕ с ожиданием модели без индикаторов.

    Уравнение, почти равное своей базе, наивные эталоны обгоняет «за счёт
    книги». Против базы оно не выигрывает ничего: отношение ≈ 1.
    """
    actuals = [0.0710, 0.0660, 0.0715, 0.0690] * 2
    rows = [_row(a + 0.0010 + 0.00003, a, yoy_plus_shift=a + 0.0040,
                 seasonal_naive=a - 0.0035, model_expectation=a + 0.0010)
            for a in actuals]
    naive_only = [dict(r) for r in rows]
    for row in naive_only:
        del row["naive_" + MODEL_EXPECTATION]
    ratio, best = mse_against_best(naive_only)
    assert best == "seasonal_naive" and ratio < ADMISSION_MAX_MSE_RATIO

    status = admission_from_rows(rows, target=MARGIN)
    assert status.best_benchmark == MODEL_EXPECTATION
    assert status.mse_ratio > 1.0 and not status.admitted
    assert "ожидание модели без индикаторов" in status.reason


def test_a_report_without_the_model_expectation_is_not_scored():
    """Отчёт, где базы уравнения на момент зачёта нет, в счёт допуска не идёт."""
    rows = [_row(0.07, 0.07, yoy_plus_shift=0.066) for _ in range(8)]
    del rows[1]["naive_" + MODEL_EXPECTATION]
    status = admission_from_rows(rows, target=MARGIN)
    assert status.events == 7 and status.status == "accumulating"
    # У величин без обязательного эталона правило прежнее.
    assert admission_from_rows(rows, target=issuer.series("net_interest")).events == 8


def test_an_empty_equation_equal_to_the_prior_cannot_pass():
    """Симуляция: уравнение, равное своей базе, не проходит порог никогда.

    Восемь кварталов; факт = цель книги + шум σ квартала ≈1,0 п.п.; наивные
    эталоны — свой шум (RMSE главного на истории Ленты ≈1,5 п.п.); «пустое»
    уравнение — база книги ± 0,003 п.п.
    """
    import random

    rng = random.Random(20260928)
    passed_naive = passed = 0
    trials = 2000
    for _ in range(trials):
        rows = []
        for _ in range(ADMISSION_MIN_EVENTS):
            prior = 0.070 + rng.gauss(0.0, 0.003)
            actual = prior + rng.gauss(0.0, 0.010)
            rows.append(_row(prior + rng.uniform(-0.00003, 0.00003), actual,
                             yoy_plus_shift=actual + rng.gauss(0.0, 0.015),
                             seasonal_naive=actual + rng.gauss(0.0, 0.020),
                             model_expectation=prior))
        passed += admission_from_rows(rows, target=MARGIN).admitted
        naive_only = [{k: v for k, v in r.items()
                       if not k.startswith("naive_" + MODEL_EXPECTATION)} for r in rows]
        ratio, _ = mse_against_best(naive_only)
        passed_naive += ratio is not None and ratio <= ADMISSION_MAX_MSE_RATIO
    assert passed == 0, f"пустое уравнение допущено в {passed} из {trials}"
    assert passed_naive > trials * 0.15, (
        f"без базы пустое уравнение прошло лишь {passed_naive} из {trials}: "
        "проба не воспроизводит находку")


def test_the_model_expectation_and_guidance_benchmarks_are_not_frozen(tmp_path):
    """Ожидание модели и гайденс — ряды во времени, эталоны из закрытых периодов
    заморожены на первой записи."""
    journal = Journal(tmp_path / "j.sqlite")
    assert journal.record_naive(MARGIN, "2026Q3", {"yoy_plus_shift": 0.0712,
                                                  MODEL_EXPECTATION: 0.0690,
                                                  "guidance": 0.0772}) == 3
    assert journal.record_naive(MARGIN, "2026Q3", {MODEL_EXPECTATION: 0.0701,
                                                  "guidance": 0.0768}) == 2
    assert journal.naive(MARGIN, "2026Q3")[MODEL_EXPECTATION] == pytest.approx(0.0701)
    with pytest.raises(JournalError):
        journal.record_naive(MARGIN, "2026Q3", {"yoy_plus_shift": 0.0690})
