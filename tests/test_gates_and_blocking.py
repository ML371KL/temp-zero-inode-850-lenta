# -*- coding: utf-8 -*-
"""Гейты на всей сетке и блокировка публикации.

Аудит первой итерации: гейты считались на трёх именованных сценариях из 36
клеток, поле `explained` было литералом `[]`, а `build_release.py` возвращал
код 0 при 756 нарушенных инвариантах. Проверка, которая ничего не
останавливает, — не проверка.

Здесь закреплено обратное: гейты считаются на всех клетках со взвешиванием
вероятностью, каждый обязан иметь действующее письменное объяснение, а
сборка выпуска падает, если объяснения нет, оно просрочено или нарушен
инвариант.
"""
from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from model.checks import (ADVISORY_GATES, EXPLANATION_WARN_DAYS, gate_corridors,
                          PENDING_GATES, PENDING_UNTIL, RELEASE_LABEL, Finding, GateSummary,
                          Severity, check_pending_gates_are_dated,
                          check_worlds_are_monotone_in_rates, explanation_deadline_alarms,
                          explanation_expires_soon, explanation_is_stale,
                          load_gate_explanations, summarize_gates)
from model.engine import run_release
from tests.gate_dates import move_deadlines, quiet_day, repo_copy

ROOT = Path(__file__).resolve().parents[1]
EXPLANATIONS = ROOT / "data" / "assumptions" / "gate_explanations.yaml"


@pytest.fixture(scope="module")
def release():
    return run_release()


@pytest.mark.needs_book
def test_gates_are_checked_on_every_cell(release):
    """Гейты обязаны трогать все 36 клеток, а не три именованных сценария."""
    labels = {f.label for f in release.findings}
    cell_keys = {c.cell.key for c in release.cells}
    assert len(cell_keys) == 36
    # Хотя бы часть клеток сетки должна встретиться в находках: если гейты
    # считаются только по сценариям, меток клеток здесь не будет вовсе.
    assert labels & cell_keys, "гейты считаются не по клеткам сетки"


@pytest.mark.needs_book
def test_gate_summary_weighs_by_probability(release):
    """Три клетки с массой 2 % и три с массой 40 % — разные новости."""
    assert release.gate_summary
    for gate in release.gate_summary:
        assert gate.cells >= 1
        assert 0.0 <= gate.mass <= 1.0
    biggest = max(release.gate_summary, key=lambda g: g.mass)
    assert biggest.mass > 0.2, "самый массовый гейт должен быть заметен"


@pytest.mark.needs_book
def test_every_firing_gate_has_a_live_explanation(release):
    """Необъяснённых гейтов быть не должно — иначе сборка не пройдёт."""
    assert release.unexplained_gates == [], (
        "без объяснения: " + ", ".join(g.key for g in release.unexplained_gates))


@pytest.mark.needs_book
def test_explanations_are_not_stale(release):
    """У объяснения есть срок годности: экономика меняется, текст остаётся.

    ТЕСТ ДАННЫХ, а не механизма: он спрашивает о настоящем файле на настоящий
    день и после срока БЛОКИРУЮЩЕГО гейта ОБЯЗАН падать — в тот же день
    встанет и сборка. Правило срока одно с ней (`explanation_is_stale`:
    объяснение действует включительно по `valid_until`). До 24.09.2026 тест
    требовал `until > today`, а сборка — `until >= today`, и в день срока такт
    падал на тестах с чужой причиной (аудит, G1-exam §1.3 п. 3).

    СОВЕЩАТЕЛЬНЫЙ гейт (`ADVISORY_GATES`; у книги «Ленты» — `guidance_gap`,
    объяснение до отчёта за 3 кв. 2026, решение ведущего C20) после срока
    сборку не останавливает, а звонит тревогой каждый такт (код 3,
    `explanation_deadline_alarms`). Для него тест после срока требует ЭТУ
    ТРЕВОГУ, а не падает (решение ведущего F1): прогон «в будущем»
    (`FAKE_TODAY` + 180 дней) проходит с известным будильником, а не падением
    теста данных; продление срока — решение ведущего после отчёта.
    """
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    today = dt.date.today()
    firing = {g.key for g in release.gate_summary}
    alarms = explanation_deadline_alarms(load_gate_explanations(today=today), firing, today=today)
    for key, spec in raw.items():
        until = spec["valid_until"]
        if isinstance(until, str):
            until = dt.date.fromisoformat(until)
        assert len(spec["explanation"].split()) > 20, f"{key}: объяснение слишком короткое"
        if explanation_is_stale(until, today) and key in ADVISORY_GATES:
            assert any(line.startswith(f"объяснение гейта {key} просрочено") for line in alarms), (
                f"объяснение совещательного гейта {key} просрочено ({until}), а тревоги нет: {alarms}")
            continue
        assert not explanation_is_stale(until, today), (
            f"объяснение гейта {key} действовало по {until} включительно — просрочено")


@pytest.mark.needs_book
def test_a_stale_advisory_explanation_is_an_alarm_not_a_failure(release):
    """Механизм под предыдущим тестом, на явный день после срока `guidance_gap`
    (без `FAKE_TODAY`): совещательный гейт срабатывает, его просроченное
    объяснение не блокирует сборку и даёт строку тревоги; объяснения блокирующих
    гейтов в тот день действуют."""
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    advisory = {k: dt.date.fromisoformat(str(v["valid_until"])) for k, v in raw.items()
                if k in ADVISORY_GATES and k in {g.key for g in release.gate_summary}}
    assert advisory, "на книге нет срабатывающего совещательного гейта — проба пуста"
    key, until = min(advisory.items(), key=lambda kv: kv[1])
    day = until + dt.timedelta(days=1)
    explanations = load_gate_explanations(today=day)
    assert explanations[key]["stale"]
    alarms = explanation_deadline_alarms(explanations, {g.key for g in release.gate_summary},
                                         today=day)
    assert any(line.startswith(f"объяснение гейта {key} просрочено") for line in alarms), alarms
    summary = summarize_gates(release.findings,
                              {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0},
                              explanations, today=day)
    assert not [g.key for g in summary if g.blocking], "после срока совещательного гейта сборка стоит"


@pytest.mark.needs_book
def test_expected_mass_is_close_to_actual(release):
    """Записанная в объяснении ожидаемая масса должна соответствовать факту.

    Если гейт вдруг начал срабатывать вдвое чаще, объяснение, написанное под
    прежнюю картину, больше не описывает происходящее.
    """
    for gate in release.gate_summary:
        corridor = gate.mass_corridor
        if corridor is None:
            continue
        assert gate.mass <= corridor[1], (
            f"{gate.key}: фактическая масса {gate.mass:.1%} выше коридора объяснения "
            f"(до {corridor[1]:.1%}) — объяснение устарело")


@pytest.mark.needs_book
def test_unexplained_gate_blocks_the_release(tmp_path):
    """Главное свойство: без объяснения сборка ПАДАЕТ.

    Проверяется не флагом, а запуском настоящей сборки с урезанным файлом
    объяснений — так же, как это произойдёт на сервере.

    Объяснения читаются на «тихий день» (все прочие действуют): тест проверяет
    механизм, и истёкший срок чужого объяснения не должен ни ронять его, ни
    подменять ответ. До 24.09.2026 с 01.02.2027 первым заблокированным
    оказывался просроченный `creditor_loss`, а не убранный ключ.
    """
    trimmed = tmp_path / "gate_explanations.yaml"
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    release = run_release()
    firing = {g.key for g in release.gate_summary}
    key = next(k for k in raw if k in firing and k not in ADVISORY_GATES)
    del raw[key]
    trimmed.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")

    explanations = load_gate_explanations(trimmed, today=quiet_day())
    summary = summarize_gates(
        release.findings, {c.cell.key: c.probability for c in release.cells}, explanations)
    blocked = [g.key for g in summary if g.blocking]
    assert blocked == [key], f"заблокированы {blocked}, а убран только {key}"
    assert all(isinstance(g, GateSummary) for g in summary)


@pytest.mark.needs_book
def test_build_release_exits_nonzero_on_a_broken_invariant(tmp_path):
    """Сборка обязана возвращать ненулевой код, а не печатать и продолжать."""
    script = tmp_path / "probe.py"
    script.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from model.checks import Severity, Finding\n"
        "import model.engine as engine\n"
        "original = engine.check_invariants\n"
        "engine.check_invariants = lambda result, label='': [\n"
        "    Finding(key='probe', severity=Severity.INVARIANT, message='проба', label=label)]\n"
        "release = engine.run_release()\n"
        "print(len(release.blocking))\n"
        "sys.exit(0 if release.blocking else 1)\n",
        encoding="utf-8", newline="\n")
    proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr[-500:]
    assert int(proc.stdout.strip()) > 0, "подменённый инвариант не попал в блокирующие"


def test_pipeline_runs_tests_before_building(tmp_path):
    """`run.sh` обязан прогонять тесты ДО сборки выпуска — в каждом такте, который его собирает.

    Проверяется поведением, а не местом строк в тексте: настоящий `ops/run.sh`
    с подставным питоном (песочница из tests/test_payload.py). Сборка и
    публикация — общая функция суточного такта и пересборки, и её текст стоит в
    файле выше вызова тестов; прежнее сравнение позиций это сломало бы, хотя
    порядок исполнения верен.
    """
    from tests.test_payload import TACT_PYTEST_CALL, _run_tact

    for mode in ("daily", "rebuild"):
        code, log = _run_tact(tmp_path / mode, mode)
        assert code == 0, log[-2000:]
        assert TACT_PYTEST_CALL + "\n" in log, f"{mode}: не тот отбор тестов такта"
        tests_at = log.index(TACT_PYTEST_CALL)
        build_at = log.index("ВЫЗОВ: ops/build_release.py")
        assert tests_at < build_at, f"{mode}: тесты гоняются после сборки — это бессмысленно"


# --------------------------------------------- четыре гейта третьей итерации


def gate(release, key: str):
    for item in release.gate_summary:
        if item.key == key:
            return item
    raise AssertionError(f"гейт {key} не сработал вовсе")


@pytest.mark.needs_book
def test_the_creditor_loss_gate_is_gone_with_its_explanation(release):
    """Гейта `creditor_loss` у «Ленты» нет (D4): заголовок — внутренняя
    стоимость max(V0 − D, 0)·(1 − g)/акции, структурного колла и потерь
    кредиторов против номинала нет. Существенность ограниченной
    ответственности сторожит совещательный `limited_liability`; объяснение
    снятого гейта было бы мёртвой записью (аудит 850oa G1-exam §1.4).
    """
    assert not any(f.key in ("creditor_loss", "sigma_calibration") for f in release.findings)
    A = release.book
    shares = A["facts"]["shares_out_mln"] / 1000.0
    g = A["valuation"]["governance_discount"]
    for name, layer in release.layers.items():
        assert layer.intrinsic == pytest.approx(max(layer.v0 - layer.claims, 0.0) * (1 - g) / shares,
                                                rel=1e-12), name
        assert layer.v0 > layer.claims, f"слой {name}: V0 ≤ D — метод intrinsic пересмотреть"
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    assert "creditor_loss" not in raw, "объяснение снятого гейта — мёртвая запись"
    assert "limited_liability" in ADVISORY_GATES and "sigma_calibration" not in ADVISORY_GATES

@pytest.mark.needs_book
def test_the_credit_lines_gate_fires_on_exactly_the_flagged_cells(release):
    """Лимит линий — гейт сборки ровно тогда, когда книга считает его
    ограничением пути долга (`valuation.distress.credit_limit_trigger`).

    Клетка, где валовой долг перерастает раскрытые линии, физически означает
    допэмиссию или реструктуризацию — если лимит ограничение. У «Ленты» с F1 —
    нет: номинальная сумма 30.06.2026 при выручке, растущей в 2,5 раза, не
    ограничивает путь, неустойчивость ловит порог ЧД/EBITDA; гейт молчит, а
    клетка за номинальным лимитом остаётся флагом витрины (справка). С
    триггером в книге гейт срабатывает ровно на клетках за лимитом.
    """
    import copy

    from model.financing import credit_limit
    from model.payload import _grid_block

    assert release.book["valuation"]["distress"]["credit_limit_trigger"] is False
    limit = credit_limit(release.book)
    want = {c.cell.key for c in release.cells if c.result.max_gross_debt > limit}
    assert want, "на книге нет клетки за номинальным лимитом — проба пуста"
    assert not any(f.key == "credit_lines" for f in release.findings)
    flagged = {f"{c['world']}|{c['regime']}|{c['capex']}" for c in _grid_block(release.cells, limit)
               if c["over_credit_limit"]}
    assert flagged == want, "клетка за лимитом обязана остаться флагом витрины"

    A = copy.deepcopy(release.book)
    A["valuation"]["distress"]["credit_limit_trigger"] = True
    on = run_release(A)
    want_on = {c.cell.key for c in on.cells if c.result.max_gross_debt > limit}
    got = {f.label for f in on.findings if f.key == "credit_lines"}
    assert want_on and got == want_on, f"гейт сработал на {got}, а превышают лимит {want_on}"
    item = gate(on, "credit_lines")
    assert item.mass == pytest.approx(
        sum(c.probability for c in on.cells if c.cell.key in want_on), rel=1e-9)


@pytest.mark.needs_book
def test_the_margin_corridor_is_the_one_the_brief_names(release):
    """Коридор маржи «Ленты» — 4,5–8,0 % (лист «Маржа»), данными гейта (E20).

    На книге 1.0 гейт молчит: маржа полугодий всех клеток — 5,2–7,9 %
    (объяснение `margin_range`). Мощность — на сдвинутой книге: цели «стресса»
    на 1 п.п. ниже выводят за нижний край только стресс-клетки.
    """
    import copy

    assert gate_corridors()["margin_range"] == (0.045, 0.080)   # данные гейта (E20)
    lo, hi = gate_corridors()["margin_range"]
    margins = [r.margin for c in release.cells for r in c.result.rows]
    assert lo <= min(margins) and max(margins) <= hi
    assert not any(g.key == "margin_range" for g in release.gate_summary)
    A = copy.deepcopy(release.book)
    target = A["margin"]["regimes"]["stress"]["target"]
    for node in list(target):
        if node != "2026H1":                     # ключ якоря — один у всех режимов (C21)
            target[node] = target[node] - 0.01
    item = gate(run_release(A), "margin_range")
    assert {key.split("|")[1] for key in item.labels} == {"stress"}, item.labels


@pytest.mark.needs_book
def test_rate_monotonicity_is_a_build_gate_now(release, monkeypatch):
    """Монотонность по стоимости денег проверяется СБОРКОЙ, а не только тестом.

    Две половины. Первая: на книге гейт молчит, а при сдвиге кривой ВНИЗ та же
    проверка видит рост стоимости — значит, она умеет срабатывать, а не просто
    возвращает пустой список. Вторая: сборка её действительно зовёт (подменой
    функции в движке), потому что до третьей итерации она существовала, но в
    сборке не участвовала.
    """
    A = release.book
    assert check_worlds_are_monotone_in_rates(A) == [], "на книге миры согласованы"
    cheaper = check_worlds_are_monotone_in_rates(A, shift=-0.02)
    assert len(cheaper) == len(A["joint"]["world_prob"]), (
        "при более дешёвых деньгах стоимость обязана вырасти во всех мирах")

    import model.engine as engine

    probe = Finding("ev_grows_with_rates", Severity.GATE, "проба", RELEASE_LABEL)
    monkeypatch.setattr(engine, "check_worlds_are_monotone_in_rates", lambda A, **kw: [probe])
    assert any(f is probe for f in run_release().findings), "сборка не зовёт проверку миров"


@pytest.mark.tact
def test_pending_gates_is_empty_by_literal():
    """`PENDING_GATES` пуст ЛИТЕРАЛОМ — вернуть ключ нельзя молча.

    Аудит третьей итерации, B4: пока список был непустым, любой гейт можно
    было внести в него и снять его текст, не уронив ни одного теста, — то
    есть выключить проверку правкой одной строки. Тексты трёх ожидавших
    гейтов вписаны аудитором 22.09.2026, и список опустел; эта проверка
    держит его пустым, а механизм ожидания — законной, но ВИДИМОЙ дверью.
    """
    assert PENDING_GATES == frozenset(), (
        "механизм ожидания снова кого-то ждёт: "
        f"{sorted(PENDING_GATES)}. Это допустимо только вместе с правкой "
        "этого теста и записью, до какой даты гейт ждёт текста.")
    text = (Path(__file__).resolve().parents[1] / "model" / "checks.py").read_text(encoding="utf-8")
    assert "PENDING_GATES: frozenset[str] = frozenset()" in text, (
        "пустота обязана быть литералом в коде, а не результатом вычисления")


@pytest.mark.needs_book
def test_a_waiting_gate_is_visible_and_does_not_block_but_a_stale_one_does(release):
    """Механизм ожидания проверяется НА ДЕЙСТВУЮЩЕМ ГЕЙТЕ, а не на пустом списке.

    Прежняя версия этого теста брала `pending = [g for g in gate_summary if
    g.pending ...]` и сверяла его с `PENDING_GATES`. С 22.09.2026 список пуст,
    и обе части равенства — пустые множества: тест стал ВАКУУМНЫМ и проходил
    бы, даже если механизм ожидания сломан целиком (аудит третьей итерации,
    B4; T0 оставил указание на это в докстроке).

    Здесь ожидание включается на настоящем гейте (`ev_ebitda`, масса
    ≈49 % на книге «Ленты» 1.0) подменой константы, а объяснение у него
    отбирается — и проверяется ровно то, чего от механизма ждут:

    * гейт ВИДЕН в сводке (`pending`) и не блокирует;
    * тот же гейт без объяснения и БЕЗ ожидания — блокирует;
    * ожидающий гейт с ПРОСРОЧЕННЫМ текстом блокирует всё равно.
    """
    import model.checks as checks

    key = "ev_ebitda"
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    assert key in raw, "проба опирается на существующее объяснение"
    without = {k: v for k, v in raw.items() if k != key}
    explanations = {k: dict(explanation=v["explanation"].strip(),
                            expected_mass=v.get("expected_mass"), stale=False)
                    for k, v in without.items()}
    mass = {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0}

    # (1) без ожидания необъяснённый гейт блокирует
    plain = summarize_gates(release.findings, mass, explanations)
    blocked = {g.key for g in plain if g.blocking}
    assert key in blocked, "необъяснённый гейт обязан блокировать"

    # (2) с ожиданием он виден и не блокирует
    original_gates, original_until = checks.PENDING_GATES, checks.PENDING_UNTIL
    try:
        checks.PENDING_GATES = frozenset({key})
        checks.PENDING_UNTIL = {key: "2027-06-30"}
        waiting = summarize_gates(release.findings, mass, explanations)
        item = next(g for g in waiting if g.key == key)
        assert item.pending, "ожидающий гейт обязан быть помечен в сводке"
        assert not item.explained and not item.blocking, (
            "ожидающий гейт без текста блокировать не должен")
        assert item.mass > 0.2, (
            f"проба выбрала гейт массой {item.mass:.1%} — на таком не видно, "
            "работает ли механизм")
        assert [g.key for g in waiting if g.blocking] == [], (
            "кроме ожидающего, блокировать было некому")

        # (3) просроченный текст блокирует даже ожидающий гейт
        expired = dict(explanations)
        expired[key] = dict(explanation=raw[key]["explanation"].strip(),
                            expected_mass=raw[key].get("expected_mass"), stale=True)
        late = summarize_gates(release.findings, mass, expired)
        item = next(g for g in late if g.key == key)
        assert item.pending and item.explained and item.stale
        assert item.blocking, (
            "«ожидает согласования» и «согласовано когда-то давно» — разные "
            "состояния, и второе обязано блокировать")
    finally:
        checks.PENDING_GATES, checks.PENDING_UNTIL = original_gates, original_until


@pytest.mark.tact
def test_the_waiting_door_is_dated_and_the_build_refuses_an_undated_one():
    """Ключ, ждущий текста, обязан ждать до НАЗВАННОЙ даты.

    Аудит третьей итерации, B4: `PENDING_GATES` был константой кода без срока
    и без сторожа состава — любой гейт можно было внести в список и снять его
    текст, не уронив ни одного теста. Решение четвёртой итерации: дверь
    остаётся (следующий новый гейт без текста аудитора неизбежен), но
    датируется принудительно — `check_pending_gates_are_dated` возвращает
    ИНВАРИАНТ, то есть сборка не выходит.

    Проверяются все три состояния двери, а не только пустое.
    """
    import model.checks as checks

    assert check_pending_gates_are_dated() == [], (
        "на пустом списке сторож обязан молчать")

    original_gates, original_until = checks.PENDING_GATES, checks.PENDING_UNTIL
    try:
        checks.PENDING_GATES = frozenset({"margin_range"})
        checks.PENDING_UNTIL = {}
        undated = check_pending_gates_are_dated()
        assert [f.key for f in undated] == ["pending_gate_undated"]
        assert all(f.blocking for f in undated), "дверь без срока обязана блокировать"

        checks.PENDING_UNTIL = {"margin_range": "2026-01-31"}
        expired = check_pending_gates_are_dated(today=dt.date(2026, 9, 22))
        assert [f.key for f in expired] == ["pending_gate_expired"]
        assert all(f.blocking for f in expired), "истёкший срок обязан блокировать"

        checks.PENDING_UNTIL = {"margin_range": "2027-06-30"}
        assert check_pending_gates_are_dated(today=dt.date(2026, 9, 22)) == [], (
            "живая датированная дверь ничего ронять не должна")

        checks.PENDING_GATES = frozenset()
        forgotten = check_pending_gates_are_dated()
        assert [f.key for f in forgotten] == ["pending_gate_stale_date"], (
            "забытая запись о сроке обязана быть названа")
        assert all(f.blocking for f in forgotten)
    finally:
        checks.PENDING_GATES, checks.PENDING_UNTIL = original_gates, original_until


@pytest.mark.tact
def test_pending_until_agrees_with_pending_gates():
    """Обе константы пусты и обязаны оставаться согласованными."""
    assert set(PENDING_UNTIL) == set(PENDING_GATES), (
        f"ожидание {sorted(PENDING_GATES)} против сроков {sorted(PENDING_UNTIL)}")
    assert not PENDING_UNTIL, "список сроков непуст при пустом ожидании"


def _build(where: Path, *extra: str) -> subprocess.CompletedProcess:
    """Сборка в СВОЁМ каталоге состояния — иначе два плеча писали бы в один.

    Без своего `LENTA_STATE_DIR` оба выпуска уходили бы в общий каталог
    фикстур, и «выпуск не записан» было бы утверждением ни о чём.
    """
    import os

    env = dict(os.environ, LENTA_STATE_DIR=str(where / "state"),
               PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, "-B", "ops/build_release.py", "--book-only", "--fast", *extra],
        cwd=str(where), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=600, env=env)


@pytest.mark.needs_book
def test_a_firing_gate_without_an_explanation_stops_the_real_build(tmp_path):
    """Сработавший гейт без объяснения роняет НАСТОЯЩУЮ СБОРКУ.

    Разница с соседним `test_unexplained_gate_blocks_the_release` существенна:
    тот считает `summarize_gates` в процессе и смотрит на флаг `blocking`, то
    есть проверяет свойство объекта. Здесь запускается `ops/build_release.py`
    — то самое, что зовёт `ops/run.sh` на сервере, — и проверяется КОД
    ВЫХОДА. Флаг может быть верным, а сборка при этом всё равно
    опубликуется: в первой итерации ровно так и было — 756 нарушенных
    инвариантов при коде 0.

    Два плеча обязательны. Без контрольного («полная копия собирается»)
    падение ничего не доказывало бы: копия могла бы падать по любой другой
    причине.

    Сроки объяснений в обеих копиях отодвинуты (`repo_copy`): тест проверяет
    механизм, и с 01.02.2027 (срок `creditor_loss`) контрольное плечо падало
    бы по чужой причине, а за 30 дней до срока давало бы код тревоги 3.
    """
    good = repo_copy(tmp_path / "good")
    control = _build(good)
    assert control.returncode == 0, (
        "контрольное плечо: неизменённая копия обязана собираться\n"
        + control.stderr[-1500:])
    written = good / "state" / "release" / "latest.json"
    assert written.exists(), (
        "контрольное плечо вышло с кодом 0, но выпуска не записало — значит, "
        "проба смотрит не туда, где живёт выпуск")

    bad = repo_copy(tmp_path / "bad")
    path = bad / "data" / "assumptions" / "gate_explanations.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    key = "terminal_share"          # срабатывает на книге и без коридора в записи
    assert key in raw, "проба опирается на существующее объяснение"
    del raw[key]
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8", newline="\n")

    broken = _build(bad)
    assert broken.returncode != 0, (
        "объяснение гейта убрано, а сборка вышла с кодом 0 — значит, гейт "
        "ничего не останавливает:\n" + broken.stdout[-1500:])
    assert f"ГЕЙТ: {key}" in broken.stderr, (
        "сборка упала, но причину не назвала:\n" + broken.stderr[-1500:])
    assert "выпуск НЕ собран" in broken.stderr
    assert not (bad / "state" / "release" / "latest.json").exists(), (
        "сборка вернула ненулевой код, но выпуск всё равно записала — "
        "в R2 ушло бы то, что публиковать нельзя")


@pytest.mark.needs_book
def test_expected_mass_is_compared_at_build_time(release):
    """Сверка ожидаемой массы едет в выпуск, а не живёт только в тесте.

    Коридор двусторонний: гейт, который ПЕРЕСТАЛ срабатывать, тоже означает,
    что объяснение описывает не то, что происходит.
    """
    from model.payload import build_payload

    payload = build_payload(release, with_slow=False)
    keys = {g["key"]: g for g in payload["gates"]}
    assert keys, "блок гейтов в выпуске пуст"
    for item in release.gate_summary:
        published = keys[item.key]
        assert published["pending"] == item.pending
        assert published["mass_off"] == item.mass_mismatch
        assert published["mass_expected"] == item.expected_mass
        assert not item.mass_mismatch, (
            f"{item.key}: масса {item.mass:.1%} против ожидаемой {item.expected_mass}")

    drifted = GateSummary(key="ev_ebitda", cells=1, mass=0.60, labels=["x"],
                          explained=True, explanation="текст", expected_mass=0.18)
    assert drifted.mass_mismatch, "втрое большая масса обязана считаться расхождением"
    vanished = GateSummary(key="ev_ebitda", cells=0, mass=0.0, labels=[],
                           explained=True, explanation="текст", expected_mass=0.18)
    assert vanished.mass_mismatch, "исчезнувшее срабатывание — тоже расхождение"


@pytest.mark.needs_book
def test_the_explanations_match_the_facts_of_the_book(release):
    """Факты, на которых стоят объяснения книги «Ленты» 1.0, закреплены тестом.

    Урок 850oa: тексты дважды оказывались неверны по существу (режимы, которых
    нет в проверке; «доля стремится к единице» при отрицательной доле). Здесь
    проверяются САМИ ФАКТЫ каждого текста первого прогона (29.09.2026), а не
    наличие слов: разойдётся картина — упадёт тест, и текст перепишут вместе с
    ней.
    """
    from model.financing import credit_limit

    def cells(key):
        return {f.label for f in release.findings if f.key == key}

    by_key = {c.cell.key: c for c in release.cells}
    # ev_ebitda: «дно» и «частичная» всех миров — 18 клеток из 18 (с F1 — и «дно»
    # с высоким capex миров N и H: издержки неустойчивости лимита сняты).
    ev = cells("ev_ebitda")
    both = {f"{w}|{r}|{c}" for w in "NHM" for r in ("floor", "partial") for c in ("low", "base", "high")}
    assert ev == both, sorted(both - ev)
    observed = {f.label: f.observed for f in release.findings if f.key == "ev_ebitda"}
    assert min(observed[label] for label in ev if label.startswith("N|")) > 6.0
    assert min(observed[label] for label in ev if not label.startswith("N|")) > 4.5

    # terminal_share: только мир N — полная сходимость при любом capex, «дно» при
    # базовом и высоком, «частичная» при высоком; ниже пола 10 % никого.
    shares = {f.label: f.observed for f in release.findings if f.key == "terminal_share"}
    assert set(shares) == ({f"N|full|{c}" for c in ("low", "base", "high")}
                           | {"N|floor|base", "N|floor|high", "N|partial|high"}), sorted(shares)
    assert all(share > 0.55 for share in shares.values()), shares
    assert min(c.result.terminal_share for c in release.cells) > 0.10

    # credit_lines: номинальный лимит — не триггер (F1): гейта нет, издержек
    # неустойчивости нет ни в одной клетке (рычаг пути далеко от 4,0×); за
    # номинальным лимитом — справкой — одна клетка, в 2036 г.
    limit = credit_limit(release.book)
    trigger = release.book["valuation"]["distress"]["net_leverage_trigger"]
    assert not cells("credit_lines")
    assert all(c.result.distress_cost == 0 for c in release.cells)
    assert max(c.result.max_leverage for c in release.cells) < trigger / 2
    over = {k for k, c in by_key.items() if c.result.max_gross_debt > limit}
    assert over == {"M|full|high"}, sorted(over)
    assert by_key["M|full|high"].result.max_gross_debt_period.startswith("2036")

    # Молчат на книге (в заголовке файла: записи сняты, коридоры оставлены).
    silent = {"equity_sign", "interest_cover", "zero_openings", "real_rate", "ev_grows_with_rates",
              "margin_range", "capex_range", "credit_lines"}
    assert not silent & {f.key for f in release.findings}
    assert min(c.result.equity for c in release.cells) > 0

    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    assert not (silent - {"margin_range", "capex_range"}) & set(raw), "запись молчащего гейта без коридора"
    assert "18 клетках" in raw["ev_ebitda"]["explanation"]
    assert "мира N" in raw["terminal_share"]["explanation"]
    for key in ("ev_ebitda", "terminal_share", "guidance_gap"):
        assert "⟨ядро⟩" not in raw[key]["explanation"], key


@pytest.mark.needs_book
def test_the_book_explanations_hold_without_a_daily_alarm_until_their_deadline(release):
    """Книга без изменений до срока объяснений не даёт ежедневной тревоги.

    Тревога такта кодом 3 — это масса гейта вне коридора своего объяснения
    или срок объяснения на исходе. Развёртка по датам оценки (все 286 дат с
    18.09.2026 по 30.06.2027) показала на книге 1.4 две законно дрейфующие
    массы — `ev_ebitda` и `terminal_share`, на книге 1.5 одну — `ev_ebitda`;
    её диапазон вписан в файл. Дата кривых — дата книги, как в выпуске.
    Здесь та же проверка на опорных датах: дата книги, каждые 30 дней, день
    после закрытия первого прогнозного полугодия и последний день срока.
    Тишина обязана быть на всех, кроме окна будильника (30 дней до срока), и
    блокировать не должен никто.
    """
    import copy

    from model.book import periods

    A0 = release.book
    start = dt.date.fromisoformat(A0["meta"]["valuation_date"])
    deadline = min(dt.date.fromisoformat(str(spec["valid_until"])) for spec in
                   yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8")).values())
    first = periods(A0["meta"]["first_period"], A0["meta"]["last_period"])[0]
    first_close = dt.date(int(first[:4]) + (first[5] == "2"), 1 if first[5] == "2" else 7, 1)
    days = {start, first_close, deadline, deadline - dt.timedelta(days=EXPLANATION_WARN_DAYS + 1)}
    days |= {start + dt.timedelta(days=30 * n) for n in range(1, 10)
             if start + dt.timedelta(days=30 * n) < deadline}
    for day in sorted(days):
        A = copy.deepcopy(A0)
        A["meta"]["valuation_date"] = day.isoformat()
        A["meta"].setdefault("curve_as_of", A0["meta"]["valuation_date"])
        moved = run_release(A)
        explanations = load_gate_explanations(today=day)
        mass = {c.cell.key: c.probability for c in moved.cells} | {RELEASE_LABEL: 1.0}
        summary = summarize_gates(moved.findings, mass, explanations)
        assert [g.key for g in summary if g.blocking] == [], day
        off = [f"{g.key} {g.mass:.4f}" for g in summary if g.mass_mismatch]
        assert off == [], f"{day}: масса вне коридора объяснения — ежедневная тревога: {off}"
        alarms = explanation_deadline_alarms(explanations, {g.key for g in summary}, today=day)
        if (deadline - day).days > EXPLANATION_WARN_DAYS:
            assert alarms == [], f"{day}: будильник раньше окна предупреждения: {alarms}"
        else:
            assert alarms, f"{day}: в окне предупреждения будильник обязан звонить"


# ------------------------------------ сроки объяснений: правило и будильник


def _firing_deadlines(release) -> dict[str, dt.date]:
    """Сроки объяснений СРАБАТЫВАЮЩИХ на книге гейтов: ключ → `valid_until`.

    Пробы ниже берут ключ и срок отсюда, а не литералом: книга 1.4 перепишет
    сроки (и, возможно, состав гейтов), и тест механизма не должен падать
    оттого, что интегратор продлил объяснение.
    """
    raw = yaml.safe_load(EXPLANATIONS.read_text(encoding="utf-8"))
    # Совещательные гейты (`guidance_gap`) не блокируют и после срока — у них
    # будильник без остановки (`test_a_stale_explanation_of_a_firing_advisory_gate_alarms`).
    firing = {g.key for g in release.gate_summary if not g.advisory}
    out = {key: dt.date.fromisoformat(str(spec["valid_until"]))
           for key, spec in raw.items() if key in firing and spec.get("valid_until")}
    assert out, "проба пуста: ни у одного срабатывающего гейта нет срока"
    return out


@pytest.mark.needs_book
def test_an_explanation_is_valid_through_its_last_day(release):
    """Объяснение действует ВКЛЮЧИТЕЛЬНО по `valid_until` — и в тесте, и в сборке.

    До 24.09.2026 тест требовал `until > today`, а сборка считала просроченным
    `until < today`: в день срока суточный такт падал на pytest — на день
    раньше даты в файле и с причиной «тест упал» вместо «объяснение
    просрочено» (аудит, G1-exam §1.3 п. 3). Правило теперь одна функция, и
    здесь закреплены обе стороны границы.
    """
    until = dt.date(2027, 1, 31)
    assert not explanation_is_stale(until, until), "в последний день объяснение действует"
    assert explanation_is_stale(until, until + dt.timedelta(days=1)), "со следующего — нет"
    assert not explanation_is_stale(None, until), "без срока — не просрочено (срок требует тест)"

    # Сборка читает то же правило: сводка на последний день не блокирует,
    # на следующий — блокирует ровно гейты с этим сроком.
    deadlines = _firing_deadlines(release)
    last_day = min(deadlines.values())
    mass = {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0}
    on_the_day = summarize_gates(release.findings, mass,
                                 load_gate_explanations(today=last_day))
    assert [g.key for g in on_the_day if g.blocking] == []
    after = summarize_gates(release.findings, mass,
                            load_gate_explanations(today=last_day + dt.timedelta(days=1)))
    expired = {key for key, until in deadlines.items() if until == last_day}
    assert {g.key for g in after if g.blocking} == expired


@pytest.mark.needs_book
def test_the_deadline_warns_thirty_days_ahead_and_does_not_block(release):
    """За 30 дней до срока — тревога с понятной строкой, но НЕ блокировка.

    Будильник звонил сразу остановкой: первое, что владелец узнавал о сроке,
    был несвежий выпуск (аудит, G1-exam §1.3 п. 4). Теперь окно предупреждения
    — `EXPLANATION_WARN_DAYS` дней включительно, и гейт в нём объяснён.
    """
    until = dt.date(2027, 3, 31)
    assert EXPLANATION_WARN_DAYS == 30
    early = until - dt.timedelta(days=EXPLANATION_WARN_DAYS + 1)
    assert not explanation_expires_soon(until, early), "31 день — ещё тихо"
    assert explanation_expires_soon(until, until - dt.timedelta(days=30)), "30 дней — тревога"
    assert explanation_expires_soon(until, until), "последний день — тревога"
    assert not explanation_expires_soon(until, until + dt.timedelta(days=1)), (
        "после срока это уже не «истекает», а «просрочено»")

    key, until = min(_firing_deadlines(release).items(), key=lambda item: (item[1], item[0]))
    today = until - dt.timedelta(days=10)
    explanations = load_gate_explanations(today=today)
    assert explanations[key]["expiring"] and not explanations[key]["stale"]

    mass = {c.cell.key: c.probability for c in release.cells} | {RELEASE_LABEL: 1.0}
    summary = {g.key: g for g in summarize_gates(release.findings, mass, explanations)}
    assert summary[key].expiring and not summary[key].blocking, (
        "истекающее объяснение — тревога, а не остановка")

    lines = explanation_deadline_alarms(explanations, summary, today=today)
    line = next(item for item in lines if f"гейта {key} " in item)
    assert f"истекает {until:%d.%m.%Y}: продлить или переписать" in line, line
    assert "осталось 10 дн" in line and "гейт сейчас срабатывает" in line, line

    last = explanation_deadline_alarms(load_gate_explanations(today=until), summary, today=until)
    assert any(f"гейта {key} " in item and "сегодня последний день" in item for item in last)

    quiet = quiet_day()
    assert explanation_deadline_alarms(load_gate_explanations(today=quiet), summary,
                                       today=quiet) == [], (
        "в тихий день будильник молчит — иначе тревога приходила бы всегда")


@pytest.mark.tact
def test_a_dead_explanation_of_a_silent_gate_is_named_but_does_not_block():
    """Просроченная запись гейта, который НЕ срабатывает, — тревога, не остановка.

    Блокировать нечего: объяснять нечего. Но запись мёртвая, и промолчать о
    ней значит дождаться, пока гейт сработает снова и сборка встанет.
    """
    explanations = {"terminal_share": dict(explanation="текст", valid_until=dt.date(2027, 6, 30),
                                           stale=True, expiring=False)}
    lines = explanation_deadline_alarms(explanations, firing=(), today=dt.date(2027, 7, 1))
    assert lines == ["объяснение гейта terminal_share просрочено: срок был 30.06.2027 "
                     "(гейт сейчас не срабатывает): продлить, переписать или удалить "
                     "запись в data/assumptions/gate_explanations.yaml"]
    # Срабатывающий гейт с просроченным текстом здесь не повторяется: он
    # блокирует сборку, и об этом говорит строка «ГЕЙТ: …».
    assert explanation_deadline_alarms(explanations, firing=("terminal_share",),
                                       today=dt.date(2027, 7, 1)) == []


@pytest.mark.tact
def test_a_stale_explanation_of_a_firing_advisory_gate_alarms():
    """Совещательный гейт не блокирует никогда — поэтому его просроченный текст
    обязан звонить тревогой, даже когда гейт срабатывает.

    Книга «Ленты» (решение ведущего C20): `guidance_gap` срабатывает на всей
    сетке, объяснение — до отчёта за 3 кв. 2026. Правило 850oa молчало о
    просроченном тексте срабатывающего гейта («он блокирует сборку») — у
    совещательного гейта это означало тишину после срока.
    """
    assert "guidance_gap" in ADVISORY_GATES
    until = dt.date(2026, 11, 3)
    stale = {"guidance_gap": dict(explanation="текст", valid_until=until, stale=True, expiring=False)}
    lines = explanation_deadline_alarms(stale, firing=("guidance_gap",), today=dt.date(2026, 11, 4))
    assert lines == ["объяснение гейта guidance_gap просрочено: срок был 03.11.2026 (гейт сейчас "
                     "срабатывает, совещательный — публикацию не блокирует): продлить, переписать "
                     "или удалить запись в data/assumptions/gate_explanations.yaml"]
    summary = GateSummary(key="guidance_gap", cells=36, mass=1.0, labels=["x"], explained=True,
                          explanation="текст", stale=True, advisory=True)
    assert not summary.blocking, "совещательный гейт публикацию не блокирует и с просроченным текстом"
    soon = {"guidance_gap": dict(explanation="текст", valid_until=until, stale=False, expiring=True)}
    line, = explanation_deadline_alarms(soon, firing=("guidance_gap",), today=dt.date(2026, 10, 24))
    assert "осталось 10 дн" in line and "тревога каждый такт" in line and "сборка встанет" not in line


@pytest.mark.needs_book
def test_the_real_build_warns_before_the_deadline_and_stops_after_it(tmp_path, release):
    """НАСТОЯЩАЯ сборка: за 10 дней до срока — код 3 и выпуск записан; в
    последний день — тоже код 3; на следующий день — код 1 и выпуска нет.

    «Сегодня» подпроцесса сборки здесь не подменяется (им управляет только
    прогон «в будущем»), поэтому двигаются сроки в КОПИИ репозитория: срок
    одного объяснения ставится относительно сегодняшнего дня, прочие
    отодвинуты.
    """
    today = dt.date.today()
    key = sorted(_firing_deadlines(release))[0]
    for name, until, want_rc, written in (
            ("warn", today + dt.timedelta(days=10), 3, True),
            ("last", today, 3, True),
            ("after", today - dt.timedelta(days=1), 1, False)):
        where = repo_copy(tmp_path / name)
        move_deadlines(where / "data" / "assumptions" / "gate_explanations.yaml",
                       until.isoformat(), only=key)
        done = _build(where)
        assert done.returncode == want_rc, (name, done.stderr[-1500:])
        latest = where / "state" / "release" / "latest.json"
        assert latest.exists() is written, name
        if want_rc == 3:
            assert (f"ТРЕВОГА: объяснение гейта {key} истекает {until:%d.%m.%Y}: "
                    "продлить или переписать") in done.stderr, done.stderr[-1500:]
            assert "ТРЕВОГА (код 3): сроки объяснений гейтов: 1" in done.stderr
        else:
            assert (f"ГЕЙТ: {key} — объяснение просрочено (действовало по "
                    f"{until:%d.%m.%Y} включительно)") in done.stderr, done.stderr[-1500:]


@pytest.mark.needs_book
def test_the_contract_check_prints_tact_alarms_but_passes(tmp_path, release):
    """`--check` (шаг CI «Контракт выпуска») — проверка контракта, не такт.

    Тревоги такта (срок объяснения на исходе, дрейф массы на дате прогона)
    печатаются, но код — 0: иначе CI краснел бы за 30 дней до каждого срока
    по календарю, а не по коду. Настоящая сборка на той же копии — код 3.
    """
    where = repo_copy(tmp_path / "check")
    until = dt.date.today() + dt.timedelta(days=10)
    key = sorted(_firing_deadlines(release))[-1]
    move_deadlines(where / "data" / "assumptions" / "gate_explanations.yaml",
                   until.isoformat(), only=key)
    checked = _build(where, "--check")
    assert checked.returncode == 0, checked.stderr[-1500:]
    assert f"ТРЕВОГА: объяснение гейта {key} истекает" in checked.stderr
    assert "контракт цел" in checked.stderr
    assert not (where / "state" / "release" / "latest.json").exists(), "--check не пишет"
    assert _build(where).returncode == 3


# ----------------------------------- диапазон ожидаемой массы (дрейф гейта)


@pytest.mark.tact
def test_a_mass_range_keeps_the_old_tolerance_around_its_bounds():
    """`expected_mass_range: [lo, hi]` — тот же допуск, но вокруг границ.

    Сценарий из аудита (G1-exam §1.3 п. 1): масса `terminal_share` при
    перекате даты оценки идёт 0,012 → 0,049 (08.10.2026) → 0,076
    (01.01.2027). Коридор от точки 0,015 — до 4,25 %: с 08.10.2026 каждый
    будний такт давал бы тревогу при неизменной экономике.
    """
    common = dict(key="terminal_share", cells=3, labels=["x"], explained=True,
                  explanation="текст", expected_mass=0.015)
    point = GateSummary(mass=0.0486, **common)
    assert point.mass_corridor == pytest.approx((0.015 * 0.4 - 0.02, 0.015 * 1.5 + 0.02))
    assert point.mass_mismatch, "от одной точки дрейф — тревога каждый день"

    ranged = dict(common, expected_mass_range=(0.012, 0.08))
    for mass in (0.0, 0.0122, 0.0486, 0.0759, 0.08 * 1.5 + 0.02):
        assert not GateSummary(mass=mass, **ranged).mass_mismatch, mass
    assert GateSummary(mass=0.08 * 1.5 + 0.021, **ranged).mass_mismatch, (
        "выше диапазона с допуском — тревога, как и прежде")
    assert GateSummary(mass=0.30, **ranged).mass_corridor == pytest.approx(
        (0.012 * 0.4 - 0.02, 0.08 * 1.5 + 0.02))

    # Нижняя граница тоже работает: гейт, который ПЕРЕСТАЛ срабатывать при
    # диапазоне «от 20 %», — расхождение.
    high = dict(common, expected_mass_range=(0.20, 0.30))
    assert GateSummary(mass=0.0, **high).mass_mismatch


@pytest.mark.tact
def test_the_mass_range_is_read_from_the_file_and_the_point_still_works(tmp_path):
    """Формат файла: диапазон необязателен, точка работает как раньше, мусор —
    ошибка сборки с названным ключом (молча откатиться к точке значило бы
    вернуть ложные тревоги, ради которых диапазон вписан)."""
    path = tmp_path / "gate_explanations.yaml"
    path.write_text(
        "old_style:\n  explanation: текст\n  expected_mass: 0.18\n  valid_until: 2027-06-30\n"
        "drifting:\n  explanation: текст\n  expected_mass: 0.015\n"
        "  expected_mass_range: [0.012, 0.08]\n  valid_until: 2027-06-30\n"
        "range_only:\n  explanation: текст\n  expected_mass_range: [0.1, 0.2]\n"
        "  valid_until: 2027-06-30\n", encoding="utf-8")
    loaded = load_gate_explanations(path, today=dt.date(2026, 9, 24))
    assert loaded["old_style"]["expected_mass_range"] is None
    assert loaded["old_style"]["expected_mass"] == 0.18
    assert loaded["drifting"]["expected_mass_range"] == (0.012, 0.08)
    assert loaded["range_only"]["expected_mass"] is None

    # Одна и та же масса 0,05 — три разных ответа: точка 0,18 (коридор от
    # 5,2 %) её не держит, диапазоны держат.
    findings = [Finding(key, Severity.GATE, "м", "c") for key in loaded]
    summary = {g.key: g for g in summarize_gates(findings, {"c": 0.05}, loaded)}
    assert summary["old_style"].mass_mismatch is True
    assert summary["drifting"].mass_mismatch is False
    assert summary["range_only"].mass_mismatch is False

    for bad in ("[0.08, 0.012]", "[0.1]", "0.05", "[0.1, 1.5]", "[a, b]"):
        path.write_text(f"g:\n  explanation: текст\n  expected_mass_range: {bad}\n"
                        "  valid_until: 2027-06-30\n", encoding="utf-8")
        with pytest.raises(ValueError, match="g: expected_mass_range"):
            load_gate_explanations(path)


@pytest.mark.needs_book
def test_a_drifting_gate_is_quiet_with_a_range(release):
    """Сквозная проба на настоящем движке: перекат даты оценки.

    Точка, записанная на дату книги (масса гейта в этот день), через
    несколько недель переката уходит из своего коридора — тревога кодом 3
    каждый будний такт (на книге 1.3.1: `terminal_share` 0,012 → 0,049 к
    08.10.2026, аудит G1-exam §1.3 п. 1). Диапазон, покрывающий перекат, её
    держит. Объяснения здесь — ФИКСТУРА теста, не файл книги: значения в
    `gate_explanations.yaml` выставит интегратор, и тест от них не зависит.

    Какой гейт дрейфует — свойство книги, а не этого кода: на книгах 1.3.1 и
    1.4 это была доля терминала (вверх), на книге 1.5 — EV/EBITDA (вниз с
    01.01.2027, когда знаменатель переходит на EBITDA за 12 месяцев с
    прогнозом закрытого полугодия). Проба берёт первый из них, чья масса
    сдвинулась, шагом 20 дней от даты книги.
    """
    import copy

    def gate_mass(A, key):
        moved = run_release(A)
        mass = {c.cell.key: c.probability for c in moved.cells} | {RELEASE_LABEL: 1.0}
        found = [f for f in moved.findings if f.key == key]
        return moved.findings, mass, sum(mass.get(f.label, 0.0) for f in found)

    from model.checks import MASS_TOLERANCE_HIGH, MASS_TOLERANCE_LOW

    start = dt.date.fromisoformat(release.book["meta"]["valuation_date"])
    probe = None
    for step in range(1, 11):
        A = copy.deepcopy(release.book)
        A["meta"]["valuation_date"] = (start + dt.timedelta(days=20 * step)).isoformat()
        for key in ("terminal_share", "ev_ebitda"):
            at_book = gate_mass(release.book, key)[2]
            findings, mass, drifted = gate_mass(A, key)
            if abs(drifted - at_book) > 1e-9:
                probe = key, at_book, findings, mass, drifted
                break
        if probe:
            break
    else:
        raise AssertionError("за 200 дней переката масса ни одного гейта не сдвинулась — "
                             "дрейфа нет, сценарий пробы надо пересмотреть")
    key, at_book, findings, mass, drifted = probe

    # Точка пробы — такая, чей коридор ещё держит массу даты книги, но уже не
    # держит сдвинутую (на книге 1.3.1 такой точкой и была записанная 0,015:
    # 0,012 держала, 0,049 — нет). Механизм проверяется тот же.
    if drifted > at_book:
        point_mass = (drifted - MASS_TOLERANCE_HIGH[1]) / MASS_TOLERANCE_HIGH[0] - 1e-6
    else:
        point_mass = (drifted + MASS_TOLERANCE_LOW[1]) / MASS_TOLERANCE_LOW[0] + 1e-6
    fixture = dict(explanation="текст", expected_mass=point_mass, expected_mass_range=None,
                   valid_until=None, stale=False, expiring=False)
    assert (point_mass * MASS_TOLERANCE_LOW[0] - MASS_TOLERANCE_LOW[1] <= at_book
            <= point_mass * MASS_TOLERANCE_HIGH[0] + MASS_TOLERANCE_HIGH[1]), (
        f"{key}: точка {point_mass:.4f} не держит массу даты книги {at_book:.4f}")

    point = {g.key: g for g in summarize_gates(findings, mass, {key: fixture})}
    assert point[key].mass == pytest.approx(drifted)
    assert point[key].mass_mismatch, "точка от даты книги — тревога"

    ranged_fixture = dict(fixture, expected_mass_range=(min(at_book, drifted), max(at_book, drifted)))
    ranged = {g.key: g for g in summarize_gates(findings, mass, {key: ranged_fixture})}
    assert not ranged[key].mass_mismatch, "диапазон переката — тишина"


@pytest.mark.needs_book
def test_the_worlds_tool_prints_the_range_it_checks_against():
    """Отчёт `ops/tools/refresh_worlds.py` печатает то, с чем идёт сверка.

    Аудитор подписывает кандидата книги по таблице гейтов этого отчёта. Когда
    у гейта задан `expected_mass_range`, статус выносится по диапазону — и в
    столбце «ожидаемая масса» обязан стоять он, а не точка `expected_mass`
    (иначе рядом с «объяснён» стояло бы число, от которого масса ушла втрое),
    и не прочерк, если точки нет вовсе. Без диапазона столбец прежний.
    """
    import importlib.util

    from model.live import LiveReport

    spec = importlib.util.spec_from_file_location(
        "refresh_worlds_range_probe", ROOT / "ops" / "tools" / "refresh_worlds.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)

    common = dict(cells=3, labels=["x"], explained=True, explanation="текст", mass=0.0486)
    gates = [GateSummary(key="terminal_share", expected_mass=0.015,
                         expected_mass_range=(0.012, 0.08), **common),
             GateSummary(key="range_only", expected_mass_range=(0.03, 0.06), **common),
             GateSummary(key="point_only", expected_mass=0.05, **common)]
    live = LiveReport(curve_shift={"5": 0.001, "10": 0.002}, book_date="2026-09-18",
                      book_age_days=10)
    lines = tool.gates_block({"gates": gates, "invariants": [], "book_update": (live, False)},
                             {"gates": []})
    rows = {line.split("|")[1].strip(): line for line in lines if line.startswith("| ")}
    assert "| 0.012–0.080 | объяснён |" in rows["terminal_share"], rows["terminal_share"]
    assert "| 0.030–0.060 | объяснён |" in rows["range_only"], rows["range_only"]
    assert "| 0.050 | объяснён |" in rows["point_only"], rows["point_only"]


@pytest.mark.tact
def test_build_log_rounds_gate_mass_like_the_dashboard():
    """Масса гейта в журнале сборки — тем же округлением, что на витрине.

    `:.1%` округляет двоичное значение: 0,2975 (EV/EBITDA книги 1.5)
    печаталось 29,7 %, а витрина (`fmt.pct`) и текст объяснения — 29,8 %.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_release_pct", Path(__file__).resolve().parents[1] / "ops" / "build_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.pct(0.2975) == "29.8%"
    assert module.pct(0.29749) == "29.8%", "как витрина: масса выпуска — round(масса, 4)"
    assert module.pct(0.2203) == "22.0%"
    assert module.pct(0.0065) == "0.7%"
    assert module.pct(0.0) == "0.0%"
