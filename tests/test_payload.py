# -*- coding: utf-8 -*-
"""Контракт «модель → витрина».

Витрина не считает модель: она рисует payload. Поэтому ломается она не там,
где ошибка, а на экране у владельца и молча — `undefined` в JS не падает, он
рисуется пустотой. Здесь проверяется то, что браузер проверить не успеет:
состав блоков, конечность чисел, совпадение хэша инлайн-скрипта темы с CSP,
граница `/api/` и вес фронта.
"""
from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from indicators import issuer

from model.engine import run_release
from model.payload import (REQUIRED_TOP_LEVEL, SCHEMA, build_payload,
                           content_digest, engine_commit, validate)
# Песочница такта `ops/run.sh` переехала в tests/test_run_sh.py; прежние имена
# остаются здесь для тестов других модулей (test_gates_and_blocking).
from tests.test_run_sh import TACT_PYTEST_CALL, run_tact as _run_tact  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FUNCTIONS = ROOT / "functions"

FRONT_BUDGET = 300_000   # вес витрины без данных; брифом задан предел 300 КБ
PAYLOAD_BUDGET = 500_000


@pytest.fixture(scope="module")
def release():
    return run_release()


@pytest.fixture(scope="module")
def payload(release):
    """Быстрая сборка: всё, кроме полосы, обратного DCF и суждений."""
    return build_payload(release, with_slow=False)


@pytest.fixture(scope="module")
def full_payload(release, parallel_band):
    """Полный выпуск на книге — только в CI: в такте его собирает и проверяет
    той же `validate` сама сборка (`ops/build_release.py`). Полоса — пулом."""
    with parallel_band():
        return build_payload(release)


# --------------------------------------------------------------- контракт

@pytest.mark.needs_book
@pytest.mark.ci_only
def test_contract_holds(release, full_payload):
    assert full_payload["schema"] == SCHEMA
    assert validate(full_payload, release=release) == []


@pytest.mark.needs_book
@pytest.mark.ci_only
def test_size_budget(full_payload):
    size = full_payload["meta"]["bytes"]
    assert size < PAYLOAD_BUDGET, f"payload {size / 1000:.0f} КБ"


@pytest.mark.needs_book
def test_fast_path_keeps_contract(release):
    """`with_slow=False` пропускает тяжёлые блоки, но не роняет контракт:
    конвейер собирает им черновик, когда обратный DCF считать незачем."""
    fast = build_payload(release, with_slow=False)
    assert validate(fast) == []
    assert fast["reverse_dcf"] == {} and fast["judgements"] == []


@pytest.mark.tact
def test_frontend_reads_only_declared_blocks():
    """Всё, что читает app.js, обязано быть в REQUIRED_TOP_LEVEL.

    Обратное неверно: `changes` объявлен, но рисовать его нечем до второго
    выпуска. А вот блок, который фронт читает и который не объявлен, однажды
    исчезнет молча — validate() его пропустит. Сверяет код модели с витриной,
    поэтому в такте: пересборка не опубликует выпуск без блока витрины.
    """
    app = (WEB / "app.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bd\.([a-z_]+)", app))
    missing = sorted(used - set(REQUIRED_TOP_LEVEL))
    assert not missing, f"фронт читает необъявленные блоки: {missing}"
    # Проверка не пустая: соглашение «`d` в app.js — выпуск» живо, и витрина
    # читает заголовок. Переименуй витрина переменную выпуска — регулярка не
    # нашла бы ничего, и тест зеленел бы вхолостую.
    assert {"meta", "fair_value", "schema"} <= used, sorted(used)
    # Блоки «Ленты» объявлены: витрина может читать их (D16), и их пропажа
    # остановит сборку, а не браузер.
    assert {"network", "perimeter", "strategy", "dividends", "governance",
            "bases", "mixes"} <= set(REQUIRED_TOP_LEVEL)


@pytest.mark.needs_book
def test_numbers_are_finite(payload):
    """NaN и Infinity json.dumps печатает без кавычек, а JSON.parse на них
    падает: витрина не покажет ничего вообще. Ловим на сборке."""
    bad = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, float) and not math.isfinite(node):
            bad.append(f"{path} = {node}")

    walk(payload, "")
    assert not bad, bad


@pytest.mark.needs_book
def test_payload_is_strict_json(payload):
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    assert json.loads(text)["schema"] == SCHEMA


@pytest.mark.needs_book
def test_the_contract_refuses_non_finite_numbers(payload):
    """NaN в любом блоке — отказ сборки с путём до числа, а не выпуск,
    который витрина не сможет прочесть."""
    broken = copy.deepcopy(payload)
    broken["scenarios"][1]["annual"][2]["ebitda"] = float("nan")
    broken["grid"][4]["ev"] = float("inf")
    problems = [p for p in validate(broken) if "нечисловые значения" in p]
    assert problems, validate(broken)
    assert "$.scenarios[1].annual[2].ebitda = nan" in problems[0]
    assert "$.grid[4].ev = inf" in problems[0]


def _ops_module(name: str):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ROOT / "ops" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.needs_book
def test_a_non_finite_tile_stops_the_build(release, payload, monkeypatch, tmp_path, capsys):
    """NaN в плитке индикатора: сборка — код 1 и ни одного файла, то есть в R2
    остаётся прежний выпуск, а такт падает и поднимает тревогу."""
    build = _ops_module("build_release")
    broken = copy.deepcopy(payload)
    broken["indicators"][0]["value"] = float("nan")
    monkeypatch.setattr(build, "run_release", lambda live: release)
    monkeypatch.setattr(build, "build_payload", lambda _release, with_slow: broken)
    monkeypatch.setattr(build, "OUT", tmp_path / "release")
    monkeypatch.setattr(sys, "argv", ["build_release.py", "--fast"])
    assert build.main() == 1
    assert not (tmp_path / "release").exists()
    assert "$.indicators[0].value = nan" in capsys.readouterr().err


@pytest.mark.needs_book
def test_the_printed_point_is_checked_against_the_engine(release, payload):
    """Печатаемая точка — округление шагом книги НЕокруглённого числа ядра:
    сборка сверяет её с `release.fair_value`, а не с рублями выпуска."""
    assert validate(payload, release=release) == []
    step = release.book["valuation"]["headline"]["print_step"]
    for field, shift in (("printed_central", step), ("central", 1.0)):
        broken = copy.deepcopy(payload)
        broken["fair_value"][field] += shift
        assert any(p.startswith("точка:") for p in validate(broken, release=release)), field


@pytest.mark.needs_book
def test_the_contract_checks_the_grid_and_the_layer_cells(payload):
    """Сетка — все клетки по разу с вероятностями в сумме 1; веса слоёв — на её
    клетках; веса сценариев — в сумме 1 до округления печати."""
    for change, mark in (
            (lambda p: p["grid"].pop(), "сетка:"),
            (lambda p: p["scenarios"][0].update(weight=p["scenarios"][0]["weight"] + 0.001),
             "сценарии: веса"),
            (lambda p: p["layers"]["macro_neutral"]["cell_weights"].update({"Z|stress|low": 1e-9}),
             "вне сетки")):
        broken = copy.deepcopy(payload)
        change(broken)
        assert any(mark in p for p in validate(broken)), mark


@pytest.mark.needs_book
def test_payload_matches_engine(release, payload):
    """Слой payload только округляет. Если он начнёт считать сам — разойдётся
    с ядром, и витрина покажет не ту цену, что проверена тестами ядра."""
    fv = release.fair_value
    assert payload["fair_value"]["central"] == round(fv.central, 0)
    assert payload["fair_value"]["low"] == round(fv.low, 0)
    assert payload["fair_value"]["high"] == round(fv.high, 0)
    assert len(payload["grid"]) == len(release.cells) == 36
    weights = sum(cell["probability"] for cell in payload["grid"])
    assert weights == pytest.approx(1.0, abs=5e-3)


@pytest.mark.needs_book
def test_scenario_weights_sum_to_one(payload):
    total = sum(s["weight"] for s in payload["scenarios"])
    assert total == pytest.approx(1.0, abs=1e-6)


@pytest.mark.needs_book
def test_gate_explanations_reach_the_release_whole(payload):
    """Объяснение гейта в выпуске — текст `gate_explanations.yaml` целиком:
    обрезка по числу знаков обрывала его на витрине на полуслове."""
    import yaml

    written = yaml.safe_load(
        (ROOT / "data" / "assumptions" / "gate_explanations.yaml").read_text(encoding="utf-8"))
    shown = {g["key"]: g["explanation"] for g in payload["gates"] if g["explained"]}
    assert shown, "ни одного объяснённого гейта — проверка пуста"
    for key, text in shown.items():
        assert text == str(written[key]["explanation"]).strip(), f"{key}: объяснение не целиком"


# ------------------------------------------------------------------ фронт

def _theme_script(html: str) -> str:
    match = re.search(r"<script>(.*?)</script>", html, re.S)
    assert match, "инлайн-скрипт темы не найден в index.html"
    return match.group(1)


@pytest.mark.ci_only
def test_theme_script_hash_matches_csp():
    """CSP разрешает инлайн-скрипт темы по хэшу. Любая правка скрипта — даже
    запятая в комментарии — меняет хэш, браузер молча отказывается его
    исполнять, и ночью панель моргает светлой. Хэш считается по LF-версии:
    в git файл лежит с LF, рабочая копия на Windows может быть с CRLF."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    body = _theme_script(html).replace("\r\n", "\n")
    digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
    expect = f"sha256-{digest}"

    middleware = (FUNCTIONS / "_middleware.js").read_text(encoding="utf-8")
    found = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', middleware)
    assert found, "в _middleware.js нет THEME_SCRIPT_HASH"
    assert found.group(1) == expect, (
        "хэш скрипта темы разошёлся с CSP. Строка на замену в functions/_middleware.js:\n"
        f'const THEME_SCRIPT_HASH = "{expect}";'
    )


@pytest.mark.ci_only
def test_theme_rule_lives_in_one_place():
    """Вечернее правило (20:00–07:00) описано в index.html и раздаётся через
    window.__theme. Копия в app.js разъехалась бы на первой же правке."""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "window.__theme" in app
    assert not re.search(r"\bgetHours\b", app), "app.js повторяет правило темы"


@pytest.mark.ci_only
def test_front_weight_within_budget():
    files = {p.name: p.stat().st_size for p in WEB.rglob("*") if p.is_file()}
    total = sum(files.values())
    assert total < FRONT_BUDGET, f"витрина весит {total / 1000:.0f} КБ: {files}"


@pytest.mark.ci_only
def test_front_sources_carry_no_control_characters():
    """Управляющий символ в исходнике витрины — след порчи экранирования при
    записи файла: `\\b` регулярного выражения однажды превратился в байт 0x08,
    и даты ISO в тексте выпуска перестали переводиться в «24.09.2026» молча —
    выражение с таким байтом просто ничего не находит."""
    bad = {}
    for path in [*WEB.rglob("*"), *FUNCTIONS.rglob("*")]:
        if path.is_file() and path.suffix in {".js", ".css", ".html", ".svg", ".json"}:
            found = re.findall(r"[\x00-\x08\x0b-\x1f\x7f]", path.read_text(encoding="utf-8"))
            if found:
                bad[path.relative_to(ROOT).as_posix()] = sorted({hex(ord(c)) for c in found})
    assert not bad, bad



# ------------------------------------------------------- состояние на проде

@pytest.mark.tact
def test_state_paths_follow_state_directory(tmp_path):
    """Ряды, журнал, выпуск и публикация обязаны смотреть в ОДИН корень.

    Разъезд здесь не падает, а тихо расщепляет систему: прогноз пишется в
    журнал репозитория, сверяется с журналом состояния, и ошибка прогноза
    навсегда остаётся неизмеренной. На сервере это уже случилось один раз.

    Проверка идёт в ОТДЕЛЬНОМ процессе: перечитывать модули через
    importlib.reload внутри прогона нельзя — после перезагрузки `Store` это
    уже другой объект класса, и соседние тесты, импортировавшие прежний,
    ломаются на ровном месте.
    """
    probe = textwrap.dedent("""
        import importlib.util, json, sys
        from pathlib import Path
        root = Path(sys.argv[1])
        sys.path.insert(0, str(root))
        from indicators import store, journal
        def load(name):
            spec = importlib.util.spec_from_file_location(name, root / "ops" / (name + ".py"))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        print(json.dumps({
            "store": str(store.DEFAULT_ROOT),
            "journal": str(journal.DEFAULT_PATH),
            "build": str(load("build_release").OUT),
            "publish": str(load("publish").RELEASE),
        }))
    """)
    env = dict(os.environ, LENTA_STATE_DIR=str(tmp_path), PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-c", probe, str(ROOT)],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr[-800:]
    paths = json.loads(result.stdout)
    assert len(paths) == 4
    for name, value in paths.items():
        assert value.startswith(str(tmp_path)), f"{name} = {value}"


@pytest.mark.needs_book
def test_three_builds_in_a_row_give_one_release(tmp_path):
    """Три сборки на одинаковых входах — ОДИН выпуск, а не три.

    Аудит второй итерации, D1: блок `changes` со ссылкой на собственный
    прошлый выпуск входил в хэш, поэтому каждая следующая сборка отличалась
    от предыдущей ровно тем, что ссылалась на неё. Три прогона давали три
    релиза, а «Что изменилось» после повтора показывало нули — потому что
    прошлым выпуском оказывалась копия себя.

    Проверяется настоящей сборкой через `ops/build_release.py`: хэш считает
    ядро, а имя объекта в бакете берёт скрипт, и расходиться им нельзя.

    Сборка идёт на КОПИИ репозитория с отодвинутыми сроками объяснений
    гейтов: тест про хэш, и истёкший (или истекающий в пределах 30 дней —
    код 3) срок в файле не должен ни ронять его, ни подменять ответ.
    """
    import subprocess
    import sys

    from tests.gate_dates import repo_copy

    where = repo_copy(tmp_path / "repo")
    state = tmp_path / "state"
    env = dict(os.environ, LENTA_STATE_DIR=str(state), PYTHONIOENCODING="utf-8")
    digests, changes = [], []
    for _ in range(3):
        result = subprocess.run(
            [sys.executable, "-B", "ops/build_release.py", "--fast", "--book-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(where), env=env)
        assert result.returncode == 0, result.stderr[-800:]
        payload = json.loads((state / "release" / "latest.json").read_text(encoding="utf-8"))
        digests.append(payload["meta"]["payload_sha256"])
        changes.append(payload["changes"].get("note", ""))

    assert len(set(digests)) == 1, f"три разных выпуска на одних входах: {digests}"
    written = {p.name for p in (state / "release").glob("*.json")}
    assert written == {"latest.json", digests[0] + ".json"}, written
    assert changes[1] == changes[0], (
        "после повтора «Что изменилось» обязано остаться тем же текстом, а не "
        f"обнулиться: {changes}")


@pytest.mark.needs_book
def test_the_release_hash_ignores_the_changes_block():
    """Блок `changes` — про ПРОШЛЫЙ выпуск, и в содержание этого не входит."""
    import copy

    from model.payload import content_digest

    payload = build_payload(run_release(gates=False), with_slow=False)
    mutated = copy.deepcopy(payload)
    mutated["changes"] = dict(note="совсем другой текст", items=[], total=None)
    assert content_digest(payload) == content_digest(mutated)

    # А вот содержательное поле хэш менять обязано — иначе он ничего не хранит.
    mutated = copy.deepcopy(payload)
    mutated["fair_value"]["central"] += 1.0
    assert content_digest(payload) != content_digest(mutated)


@pytest.mark.tact
def test_a_secondary_collector_failure_does_not_stop_the_release(tmp_path, monkeypatch):
    """Отказ второстепенного источника — флаг и тревога, а не остановка.

    Аудит, D1: `collect.py` возвращал 1 при падении ЛЮБОГО сборщика, а
    `run.sh` работает под `set -e`. Недоступность Чек Индекса останавливала
    публикацию оценки, которая от Чек Индекса не зависит вовсе.
    """
    from indicators import collect, sources
    from indicators.store import Store

    def boom():
        raise RuntimeError("источник недоступен")

    registry = dict(sources.COLLECTORS)
    registry["checkindex"] = (boom, "еженедельно", 3)
    registry["moex_quote"] = (boom, "ежедневно", 1)
    monkeypatch.setattr(sources, "COLLECTORS", registry)

    store = Store(tmp_path)
    assert collect.cmd_collect(["checkindex"], store) == 0, (
        "отказ второстепенного сборщика не может останавливать выпуск")
    assert collect.cmd_collect(["moex_quote"], store) == 1, (
        "отказ критического сборщика обязан останавливать выпуск")
    assert "bonds" not in sources.CRITICAL
    assert "moex_quote" in sources.CRITICAL


# --------------------------------------- флаг деградации в выпуске (A4(4,5))


@pytest.fixture
def issuer_feed(monkeypatch):
    """Подставной НЕВОСПОЛНИМЫЙ источник эмитента (у 850oa — панель вакансий):
    множество `IRRECOVERABLE` у Ленты пока пустое, а механизм тревоги
    проверяется на нём."""
    from indicators import collect, sources

    names = frozenset({"issuer_feed", "issuer_feed_weekly"})
    monkeypatch.setattr(sources, "IRRECOVERABLE", sources.IRRECOVERABLE | names)
    monkeypatch.setattr(collect, "IRRECOVERABLE", collect.IRRECOVERABLE | names)
    monkeypatch.setitem(collect.SOURCE_TITLES, "issuer_feed", "лента эмитента")
    monkeypatch.setitem(collect.SOURCE_TITLES, "issuer_feed_weekly", "недельная лента эмитента")
    return names


def _registry_with(monkeypatch, name, collector):
    """Подмена одного сборщика в реестре: остальные в тесте не зовутся."""
    from indicators import sources

    registry = dict(sources.COLLECTORS)
    registry[name] = (collector, "ежедневно", 2)
    monkeypatch.setattr(sources, "COLLECTORS", registry)


@pytest.mark.tact
def test_an_irrecoverable_failure_becomes_the_alarm_code_and_a_human_line(tmp_path, monkeypatch, issuer_feed):
    """Отказ невосполнимого источника: код `ALARM_EXIT` и строка для человека.

    Аудит третьей итерации, D1: отказ печатался в журнал при коде 0, а мост
    `dash-alert` читает код юнита и вывода не видит. Заодно проверяется, что
    отчёт сбора доживает до выпуска: сбор и сборка выпуска — разные процессы.
    """
    from indicators import collect, sources
    from indicators.store import Store

    def boom(store=None):
        raise RuntimeError("HTTPError: 503 Service Unavailable")

    _registry_with(monkeypatch, "issuer_feed", boom)
    assert "issuer_feed" in sources.IRRECOVERABLE
    store = Store(tmp_path)
    assert collect.cmd_collect(["issuer_feed"], store) == collect.ALARM_EXIT, (
        "невосполнимый пропуск обязан дойти кодом, а не строкой в журнале")

    saved = collect.read_collector_report(store)
    assert saved["sources"]["issuer_feed"]["irrecoverable"] is True
    notes = collect.degradation_notes(saved)
    assert len(notes) == 1, notes
    assert "лента эмитента" in notes[0], "плашку читает человек"
    assert "невосполним" in notes[0] and "503" in notes[0], notes[0]


@pytest.mark.tact
def test_a_successful_collection_clears_the_flag_but_a_skip_does_not(tmp_path, issuer_feed):
    """«Ок» снимает отказ, «пропущен» — не снимает.

    Иначе вечерний такт, получив «обход за эту дату уже собран», снимал бы
    тревогу об обходе, которого не было: страницы за дату есть — от упавшего
    на середине утреннего обхода.
    """
    from indicators import collect
    from indicators.store import Store

    store = Store(tmp_path)
    collect.write_collector_report(store, {"issuer_feed": "ОШИБКА: HTTP 503"})
    collect.write_collector_report(
        store, {"issuer_feed": "пропущен: обход за эту дату уже собран"})
    assert collect.degradation_notes(collect.read_collector_report(store)), (
        "«пропущен» не является подтверждением сбора")
    collect.write_collector_report(store, {"issuer_feed": "ок: рядов 3, точек 150"})
    assert collect.degradation_notes(collect.read_collector_report(store)) == []


@pytest.mark.tact
def test_the_report_of_one_tact_does_not_erase_another(tmp_path, issuer_feed):
    """Суточный такт не имеет права стирать отказ недельной панели.

    Недельная панель собирается только по четвергам и только тактом `weekly`:
    переписанный целиком отчёт потерял бы ровно тот пропуск, который
    невосполним.
    """
    from indicators import collect
    from indicators.store import Store

    store = Store(tmp_path)
    collect.write_collector_report(store, {"issuer_feed_weekly": "ОШИБКА: HTTP 503"})
    collect.write_collector_report(store, {"moex_quote": "ок: рядов 1, точек 1"})
    notes = collect.degradation_notes(collect.read_collector_report(store))
    assert len(notes) == 1 and "issuer_feed_weekly" in notes[0], notes


@pytest.mark.needs_book
def test_the_release_carries_the_flag_of_a_failed_collector(tmp_path, monkeypatch, issuer_feed):
    """Поле выпуска, а не обещание поля.

    Аудит третьей итерации, A4(4): сбор печатал «выпуск выйдет с флагом
    деградации», `live.degraded` заполнялся только ценой и кривой, а приёмкой
    служил код возврата — то есть флага не было ни в выпуске, ни в проверке.
    Фронт рисует плашку по `live.degraded_flag` (web/app.js).
    """
    from indicators import collect
    from indicators.store import Store

    def boom(store=None):
        raise RuntimeError("HTTPError: 503 Service Unavailable")

    _registry_with(monkeypatch, "issuer_feed", boom)
    store = Store(tmp_path)
    collect.cmd_collect(["issuer_feed"], store)

    payload = build_payload(run_release(gates=False, live=True, store=store),
                            with_slow=False)
    live = payload["live"]
    assert live["degraded_flag"] is True
    assert any("issuer_feed" in note for note in live["degraded"]), live["degraded"]


@pytest.mark.needs_book
def test_an_unrecorded_fact_degrades_the_nowcast_and_not_the_release(tmp_path, monkeypatch):
    """Дисциплина журнала не имеет права останавливать выпуск (B4(2), C6).

    У «Ленты» событие журнала — квартальный отчёт (D2, D15): со дня после
    конца окна отчёта за 3 кв. 2026 (03.11.2026) `journal.record` отказывает
    КАЖДЫЙ вечер, пока владелец не внесёт факт руками. Версия 850oa возвращала
    на этом 2, а `ops/run.sh` работает под `set -Eeuo`: такт обрывался, и не
    выходил выпуск, который от нау-каста не зависит вовсе (к цене нау-каст не
    подключён).

    Здесь спрашивается всё, что обещано правкой: код такта — `ALARM_EXIT`,
    строка — человеческая, флаг доходит до ВЫПУСКА, и внесённый факт снимает
    его тем же каналом.
    """
    from indicators import collect
    from indicators import journal as journal_module
    from indicators.journal import Journal, report_date, report_deadline
    from indicators.store import Store
    from model.book import book
    from model.live import apply_live_inputs

    monkeypatch.setattr(journal_module, "DEFAULT_PATH", tmp_path / "journal.sqlite")
    store = Store(tmp_path / "state")
    quarter = "2026Q3"
    planned = report_date(quarter)
    late = report_deadline(quarter) + dt.timedelta(days=1)
    assert Journal().actual(collect.MARGIN_TARGET, quarter) is None

    assert collect.cmd_nowcast(store, today=late) == collect.ALARM_EXIT, (
        "код 2 останавливал такт, код 0 промолчал бы: тревога обязана уйти "
        "кодом юнита, а такт — дойти до публикации")

    notes = collect.degradation_notes(collect.read_collector_report(store), today=late)
    assert len(notes) == 1, notes
    assert "нау-каст" in notes[0] and "прогноз не записан" in notes[0], notes[0]
    assert "невосполним" not in notes[0], (
        "этот пропуск как раз восполним — его закрывает `record-actual`")

    # Флаг — в выпуске, а не в журнале прогона: плашку на витрине рисует
    # `live.degraded_flag`, и заполняет его тот же отчёт сборщиков.
    _, live = apply_live_inputs(book(), store, today=late)
    assert live.is_degraded and any("нау-каст" in note for note in live.degraded), (
        live.degraded)

    # И обратный ход: внесённый факт снимает флаг сам, без ручной уборки. Отчёт
    # за квартал — одно событие для маржи и выручки: вносятся оба факта.
    Journal().record_actual(collect.MARGIN_TARGET, quarter, 0.0609,
                            reported_on=planned, source="проба")
    Journal().record_actual(collect.REVENUE_TARGET, quarter, 355.1,
                            reported_on=report_date(quarter, "revenue"), source="проба")
    assert collect.cmd_nowcast(store, today=late) == 0
    assert collect.degradation_notes(collect.read_collector_report(store),
                                     today=late) == []


@pytest.mark.tact
def test_the_daily_tact_retries_an_irrecoverable_source_once(tmp_path, monkeypatch, issuer_feed):
    """Повтор — одна попытка, и только по невосполнимым источникам.

    Утренний отказ 22.09.2026 был бы потерей дня, если бы вечерний такт не
    попробовал ещё раз; третий запрос к лежащему источнику бесполезен.
    """
    from indicators import collect
    from indicators.sources import Collected
    from indicators.store import Point, Store

    attempts = []

    def flaky(store=None):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("HTTPError: 503 Service Unavailable")
        return Collected(source="issuer_feed", channel=2, raw=[],
                         series={"issuer.feed.openings": [
                             Point(period="2026-09-22", value=12000.0,
                                   fetched_at="2026-09-22T16:45:00+00:00")]})

    _registry_with(monkeypatch, "issuer_feed", flaky)
    store = Store(tmp_path)
    assert collect.cmd_collect(["issuer_feed"], store, retry=True, pause=0.0) == 0
    assert len(attempts) == 2, attempts
    assert collect.degradation_notes(collect.read_collector_report(store)) == []

    attempts.clear()
    dead = Store(tmp_path / "dead")

    def down(store=None):
        attempts.append(1)
        raise RuntimeError("HTTPError: 503 Service Unavailable")

    _registry_with(monkeypatch, "issuer_feed", down)
    assert collect.cmd_collect(["issuer_feed"], dead,
                               retry=True, pause=0.0) == collect.ALARM_EXIT
    assert len(attempts) == 2, "повтор ровно один"
    assert "повтор" in collect.read_collector_report(dead)["sources"]["issuer_feed"]["status"]


@pytest.mark.tact
def test_the_alarm_probe_does_not_touch_the_archive(tmp_path, monkeypatch, issuer_feed):
    """`--simulate-failure` — проба доставки, а не порча состояния.

    Ею интегратор роняет источник на сервере и смотрит, пришло ли сообщение в
    Telegram. Поэтому источник не опрашивается вовсе: ни сырого файла, ни
    ложной точки в ряду появиться не должно, а строка обязана быть очевидно
    тестовой — иначе проба останется в выпуске как настоящий отказ.
    """
    from indicators import collect
    from indicators.store import Store

    called = []

    def collector(store=None):
        called.append(1)
        raise AssertionError("объявленный отказавшим источник не опрашивается")

    _registry_with(monkeypatch, "issuer_feed", collector)
    store = Store(tmp_path)
    assert collect.cmd_collect(["issuer_feed"], store,
                               simulate=("issuer_feed",)) == collect.ALARM_EXIT
    assert called == []
    assert store.all_series() == [] and not (store.raw / "issuer_feed").exists()
    saved = collect.read_collector_report(store)
    assert saved["sources"]["issuer_feed"]["simulated"] is True
    assert "ПРОБА ТРЕВОГИ" in collect.degradation_notes(saved)[0]


@pytest.mark.tact
def test_the_probe_flag_refuses_a_source_outside_the_tact(tmp_path, monkeypatch, issuer_feed):
    """Проба обязана падать громко: тихий ноль — это и есть чинимая болезнь."""
    from indicators import collect
    from indicators import store as store_module

    monkeypatch.setattr(store_module, "DEFAULT_ROOT", tmp_path)
    assert collect.main(["daily", "--simulate-failure", "checkindex"]) == 64
    assert collect.main(["status", "--simulate-failure", "issuer_feed"]) == 64


@pytest.mark.needs_book
def test_the_gate_mass_mismatch_is_an_alarm_after_the_release_is_written(tmp_path, monkeypatch):
    """Расхождение массы гейта: выпуск записан, код 3, публикация продолжается.

    Аудит третьей итерации, D1: расхождение печаталось в журнал при коде 0.
    Останавливать публикацию из-за него нельзя — оценка верна, неверно
    объяснение, — поэтому код отличается и от 0, и от 1.

    Объяснения читаются на «тихий день» (все действуют, ни одно не истекает):
    тест про массу, и срок в файле не должен ни ронять его (с 01.02.2027 —
    код 1), ни подменять ответ (за 30 дней до срока — код 3 и без
    расхождения массы).
    """
    import importlib.util

    from tests.gate_dates import explanations_as_of, quiet_day

    spec = importlib.util.spec_from_file_location(
        "build_release_probe", ROOT / "ops" / "build_release.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUT = tmp_path / "release"

    with explanations_as_of(quiet_day(), mod):
        _mass_alarm_probe(mod, monkeypatch)


def _mass_alarm_probe(mod, monkeypatch):
    """Тело пробы: спокойная сборка — 0, сдвинутая ожидаемая масса — 3."""
    import dataclasses

    honest = run_release(gates=True)
    assert honest.gate_summary, "без гейтов проверять нечего"
    assert not any(g.mass_mismatch for g in honest.gate_summary)

    # Сдвигается ТОЧКА ожидаемой массы и снимается диапазон: у дрейфующего гейта
    # (`expected_mass_range`) коридор строится по диапазону, и сдвиг одной точки
    # его бы не тронул — проба ничего не проверила бы.
    first = honest.gate_summary[0]
    drifted = dataclasses.replace(honest, gate_summary=(
        [dataclasses.replace(first, expected_mass=first.mass * 4 + 0.2, expected_mass_range=None)]
        + list(honest.gate_summary[1:])))
    assert drifted.gate_summary[0].mass_mismatch

    monkeypatch.setattr(sys, "argv", ["build_release.py", "--fast"])
    monkeypatch.setattr(mod, "run_release", lambda **kwargs: honest)
    assert mod.main() == 0, "спокойная сборка обязана давать ноль"

    monkeypatch.setattr(mod, "run_release", lambda **kwargs: drifted)
    assert mod.main() == mod.ALARM_EXIT
    assert (mod.OUT / "latest.json").exists(), (
        "выпуск обязан быть записан: код 3 — тревога, а не отказ публиковать")


# --------------------------------------- период нау-каста в выпуске (A5)


class _October2026(dt.datetime):
    """Часы, переведённые на 15.10.2026.

    Переводятся ЧАСЫ, а не ответ: `current_quarter()` остаётся прежней функцией и
    честно говорит «2026Q4». Подменять сам ответ нельзя — тогда тест проверял
    бы подмену, а не код.
    """

    @classmethod
    def now(cls, tz=None):
        return dt.datetime(2026, 10, 15, 12, 0, tzinfo=tz)


@pytest.mark.needs_book
def test_the_release_nowcasts_the_period_the_journal_forecasts(tmp_path, monkeypatch):
    """Выпуск печатает период ЖУРНАЛА, а не календаря (A5).

    Отчётность «Ленты» квартальная: факт за 3 кв. 2026 выходит около 29.10.2026
    (окно 26.10–03.11). Поэтому в середине октября календарный квартал — уже
    2026Q4, а прогнозировать слой обязан по-прежнему 2026Q3. В 850oa
    `payload._nowcast_block` звал `margin_nowcast` без периода, тот брал
    календарь, и экран «Ближайший отчёт» показал бы прогноз не за тот период,
    что журнал, главные эталоны и табло (аудит 850oa, C3).

    Первые два утверждения — не украшение: без них тест не различал бы два
    ответа вовсе.
    """
    from indicators import collect
    from indicators import journal as journal_module
    from indicators.journal import Journal
    from indicators.store import Store

    monkeypatch.setattr(journal_module, "datetime", _October2026)
    monkeypatch.setattr(journal_module, "DEFAULT_PATH", tmp_path / "journal.sqlite")
    assert journal_module.current_quarter() == "2026Q4", (
        "проба бессмысленна, пока календарь не ушёл вперёд")
    assert Journal().actual(collect.MARGIN_TARGET, "2026Q3") is None, (
        "факт за 3 кв. к середине октября не вышел — иначе период закрыт по праву")

    # Журнал наполняет тот же такт, что и на сервере: это ЕГО прогноз обязан
    # оказаться на экране.
    assert collect.cmd_nowcast(Store(), today=dt.date(2026, 10, 15)) == 0

    block = build_payload(run_release(gates=False), with_slow=False)["nowcast"]
    assert block["margin"]["period"] == "2026Q3", block["margin"]["period"]
    assert block["interest"]["period"] == "2026Q3", block["interest"]["period"]

    # И то же ЧИСЛО, что журнал зафиксировал до отчёта, а не пересчёт за другой
    # квартал: экран и журнал обязаны говорить об одном и том же.
    recorded = [f for f in block["journal"]
                if f["target"] == collect.MARGIN_TARGET and f["period"] == "2026Q3"]
    assert len(recorded) == 1, block["journal"]
    assert recorded[0]["value"] == pytest.approx(block["margin"]["value"], abs=1e-5)

    # Главный эталон — за период нау-каста: витрина берёт его по ключу
    # «величина период» (`web/app.js`, `mainBenchmark`). Рядом — эталоны его
    # полугодия (ожидание модели и гайденс для правила A-P2u, D2).
    keys = [k for k in block["naive"] if k.startswith(issuer.series("ebitda_margin"))]
    assert set(keys) == {f"{collect.MARGIN_TARGET} 2026Q3", f"{collect.MARGIN_TARGET} 2026H2"}, keys
    assert block["naive"][f"{collect.MARGIN_TARGET} 2026Q3"]["main"], "главного эталона квартала нет"

    # Рядом с наивными эталонами такт пишет базу самого уравнения — ожидание
    # модели без индикаторов (итоговый аудит, п. 5.9): правило допуска меряет
    # прибавку индикаторов к приору книги.
    naive = block["naive"][f"{collect.MARGIN_TARGET} 2026Q3"]["naive"]
    assert naive["model_expectation"] == pytest.approx(
        block["margin"]["expectation"], abs=1e-5)


# ------------------------------ атрибуция «Что изменилось» (A5, пункт B5(3))


def _attribution_run(book_dict):
    result = run_release(book_dict, gates=False)
    return result.layers["macro_neutral"].v0, result.fair_value.central


def _previous(snapshot: dict, v0: float, price: float, commit: str) -> dict:
    """Прошлый выпуск в том виде, в каком его читает `attribute`."""
    return {"inputs": snapshot,
            "layers": {"macro_neutral": {"v0": v0}},
            "fair_value": {"central": price},
            "meta": {"payload_sha256": "p" * 64, "engine_commit": commit}}


@pytest.mark.needs_book
def test_the_release_names_the_code_that_built_it():
    """`meta.engine_commit` — версия КОДА выпуска (A5).

    Книга называла свою версию, факты — свою дату, а код не называл ничего.
    Поэтому правка ядра печаталась как «прочее … версии совпадают».
    """
    import re

    # `engine_commit` и `build_payload` берутся из ОДНОГО модуля (импорт в
    # шапке файла): `tests/mutations.py` подменяет `model.*` в `sys.modules`,
    # и повторный импорт внутри теста дал бы второй экземпляр модуля со своим
    # кэшем — сравнивались бы два независимых замера (аудит, B3(4)).
    commit = engine_commit()
    assert re.fullmatch(r"[0-9a-f]{40}(\+грязное)?", commit), commit

    payload = build_payload(run_release(gates=False), with_slow=False)
    assert payload["meta"]["engine_commit"] == commit

    # Хэш выпуска зависит от кода: выпуск, собранный другим кодом, — другой
    # выпуск, даже если числа совпали. Иначе провенанс терялся бы в R2.
    other = json.loads(json.dumps(payload))
    other["meta"]["engine_commit"] = "0" * 40
    assert content_digest(other) != content_digest(payload)


@pytest.mark.needs_book
def test_the_attribution_names_a_code_change_instead_of_calling_it_other(monkeypatch):
    """+32 ₽ от правки ядра обязаны называться правкой ядра (A5).

    Аудит третьей итерации: приведение терминала к книге дало +32 ₽, и
    атрибуция напечатала «прочее (взаимодействие шагов) · версии совпадают —
    остаток расчёта». Формально верно — книга и факты те же; по существу
    бесполезно: владелец видел «прочее» там, где произошла названная правка.
    """
    from model.attribution import attribute, inputs_snapshot
    from model.book import book as _book

    A = _book()
    snapshot = inputs_snapshot(A)
    base_v0, base_price = _attribution_run(A)

    # Тот же снимок входов, та же книга, те же факты — другой КОММИТ и другое
    # число. Ровно случай A5.
    previous = _previous(snapshot, base_v0 - 1.5, base_price - 32.0, "a" * 40)
    steps = {s.key: s for s in attribute(previous, A, run=_attribution_run,
                                         engine_commit="b" * 40)}
    assert "engine" in steps and "residual" not in steps, list(steps)
    assert steps["engine"].title == "изменение ядра/методики"
    assert steps["engine"].delta_price == pytest.approx(32.0, abs=0.01)
    assert "код aaaaaaaaaaaa → bbbbbbbbbbbb" in steps["engine"].note

    # Неизвестный коммит прошлого выпуска — НЕ повод объявить код изменившимся:
    # выпуски до этой правки поля не знали вовсе.
    older = _previous(snapshot, base_v0 - 1.5, base_price - 32.0, "")
    keys = {s.key for s in attribute(older, A, run=_attribution_run,
                                     engine_commit="b" * 40)}
    assert "residual" in keys and "engine" not in keys

    # А новая книга объясняет и сопровождавшую её правку кода: один сдвиг —
    # одна причина, иначе сумма шагов перестала бы сходиться.
    B = copy.deepcopy(A)
    # Версия берётся ОТ ТЕКУЩЕЙ книги, а не литералом: после выпуска книги
    # 1.3 литерал «1.3» перестал быть НОВОЙ версией, и шаг «допущения» не
    # возникал вовсе — тест падал на порте книги, ничего при этом не проверив.
    # Версия — не десятичная дробь (1.3.1), поэтому растёт последний компонент.
    was = str(A["meta"]["version"])
    head, _, last = was.rpartition(".")
    B["meta"]["version"] = became = f"{head}.{int(last) + 1}"
    both = {s.key: s for s in attribute(
        _previous(snapshot, base_v0 - 1.5, base_price - 32.0, "a" * 40), B,
        run=_attribution_run, engine_commit="b" * 40)}
    assert "assumptions" in both and "engine" not in both
    assert f"книга {was} → {became}" in both["assumptions"].note
    assert "код aaaaaaaaaaaa → bbbbbbbbbbbb" in both["assumptions"].note


@pytest.mark.needs_book
def test_the_attribution_separates_the_valuation_date_roll():
    """Дрейф даты оценки — свой шаг, а не «прочее» (A5).

    Дата оценки равна дате цены и едет каждый день; при замороженной книге
    оценка от этого растёт (обобщённое время, ≈ +70 ₽ за квартал). Раньше весь
    дрейф уходил в остаток, и «прочее» росло само собой.
    """
    from model.attribution import attribute, inputs_snapshot, total
    from model.book import book as _book

    A = _book()
    snapshot = {**inputs_snapshot(A), "valuation_date": "2026-08-19"}
    # V₀ прошлого выпуска — ровно та же книга на ВЧЕРАШНЮЮ дату: тогда остаток
    # обязан быть нулевым, и весь сдвиг лежит в перекате даты.
    from model.attribution import _apply

    was_v0, was_price = _attribution_run(_apply(A, snapshot))
    previous = _previous(snapshot, was_v0, was_price, "a" * 40)

    steps = {s.key: s for s in attribute(previous, A, run=_attribution_run,
                                         engine_commit="a" * 40)}
    assert "valuation_date" in steps
    roll = steps["valuation_date"]
    assert roll.title == "перекат даты оценки"
    assert "2026-08-19 → 2026-09-18, 30 дн" in roll.note
    assert roll.delta_price > 1.0, "за тридцать дней оценка обязана заметно подрасти"
    assert abs(steps["residual"].delta_price) < 0.01, (
        "остаток обязан опустеть: перекат даты больше не прячется в нём")

    # И сумма шагов по-прежнему равна полному изменению.
    now_v0, now_price = _attribution_run(A)
    d_v0, d_price = total(list(attribute(previous, A, run=_attribution_run,
                                         engine_commit="a" * 40)))
    assert d_v0 == pytest.approx(now_v0 - was_v0, abs=0.01)
    assert d_price == pytest.approx(now_price - was_price, abs=0.01)


# --------------------------------- веса клеток слоёв в выпуске (B3(1))


@pytest.mark.needs_book
def test_the_release_carries_the_cell_weights_of_every_layer(payload):
    """Веса клеток слоя — в выпуске, а не только внутри ядра (B3(1)).

    Сверка весов с контрольной моделью сравнивала пересчёт формулы книги с
    пересчётом формулы книги: `Layer` веса не хранит, наружу ядро их не
    отдавало, и «веса совпадают» было утверждением о двух копиях одной
    формулы. Теперь сверять есть что.
    """
    layers = payload["layers"]
    assert set(layers) == {"analytical", "market_implied", "macro_neutral"}

    keys = {f"{c['world']}|{c['regime']}|{c['capex']}" for c in payload["grid"]}
    for name, layer in layers.items():
        weights = layer["cell_weights"]
        assert weights, name
        assert set(weights) <= keys, (name, sorted(set(weights) - keys))
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-6), name
        assert all(v > 0 for v in weights.values()), name

    # Слой «рыночные ставки как есть» — один мир, значит 12 клеток из 36.
    assert len(layers["macro_neutral"]["cell_weights"]) == 12
    assert {k.split("|")[0] for k in layers["macro_neutral"]["cell_weights"]} == {"M"}
    assert len(layers["analytical"]["cell_weights"]) == 36
    assert len(layers["market_implied"]["cell_weights"]) == 36

    # Аналитический слой — это и есть вероятности сетки: два пути к одному
    # числу, и они обязаны совпасть.
    from_grid = {f"{c['world']}|{c['regime']}|{c['capex']}": c["probability"]
                 for c in payload["grid"]}
    for key, value in layers["analytical"]["cell_weights"].items():
        assert value == pytest.approx(from_grid[key], abs=1e-5), key


@pytest.mark.needs_book
def test_published_weights_reproduce_the_layers_of_the_engine(release, payload):
    """Опубликованные веса — ТЕ САМЫЕ, которыми считало ядро.

    Это главное утверждение пункта: напечатать веса легко, трудно доказать,
    что оценка считалась по ним. Слой пересобирается из опубликованных весов
    тем же `grid.layer_stats`, которым его собрало ядро, и сравниваются все
    шестнадцать чисел слоя. Подобрать другой набор весов, дающий те же
    шестнадцать сумм на 36 клетках, нельзя иначе как случайно.
    """
    from model.payload import LAYER_WEIGHTS_TOLERANCE, layer_cell_weights

    for name, layer in payload["layers"].items():
        assert layer["cell_weights_gap"] <= LAYER_WEIGHTS_TOLERANCE, name
    assert validate(payload) == []

    # И проверка ловит подмену: веса, сдвинутые на сотую долю, ломают сверку.
    computed = layer_cell_weights(release)
    victim = "analytical"
    weights = dict(computed[victim]["weights"])
    first, second = sorted(weights)[:2]
    weights[first] += 0.01
    weights[second] -= 0.01
    from model.grid import GridCell, layer_stats

    by_key = {c.cell.key: c for c in release.cells}
    rebuilt = layer_stats(release.book, [GridCell(by_key[k].cell, w, by_key[k].result)
                                         for k, w in weights.items()], victim)
    from model.payload import _layer_gap

    assert _layer_gap(rebuilt, release.layers[victim]) > LAYER_WEIGHTS_TOLERANCE, (
        "сдвиг веса на 1 п.п. обязан быть замечен сверкой")

    # А выпуск с подменёнными весами обязан не пройти контракт.
    broken = json.loads(json.dumps(payload))
    broken["layers"][victim]["cell_weights_gap"] = 1e-3
    assert any("не воспроизводят слой ядра" in item for item in validate(broken))
    broken["layers"][victim].pop("cell_weights")
    assert any("нет весов клеток" in item for item in validate(broken))


# ------------------------------- нау-каст: книга 1.4 (ожидание, допуск, ретро)


@pytest.mark.needs_book
def test_the_nowcast_block_carries_expectation_admission_and_retro(payload):
    """Блок `nowcast` книги 1.4: ожидание и модальная цель, статус допуска, ретро.

    Экран «Ближайший отчёт» читает эти поля; пропавшее поле в JS не падает, а
    рисуется пустотой, поэтому контракт проверяется здесь.
    """
    from indicators.collect import MARGIN_TARGET
    from indicators.nowcast import MARGIN_VERSION

    block = payload["nowcast"]
    margin = block["margin"]
    assert margin["version"] == MARGIN_VERSION
    # Ожидание модели на квартал — маржа полугодия ядра + поправка квартала книги (D2).
    assert (margin["components"]["base"] + margin["components"]["quarter_offset"]
            == pytest.approx(margin["expectation"], abs=1e-5))     # выпуск округляет до 5 знаков
    assert margin["deviation"] == pytest.approx(margin["value"] - margin["expectation"], abs=2e-5)
    # Модальная цель — поле выпуска 850oa; у квартального нау-каста «Ленты» его нет
    # (`indicators.nowcast.MarginNowcast`), и в выпуске «Ленты» полей модальной
    # цели нет вовсе (P4b: поля «Магнита» сняты, `FORBIDDEN_FIELDS`).
    assert not {"modal_target", "modal_regime", "modal_title"} & set(margin)

    # Только маржа: правило A-P2u — о режимах маржи.
    assert set(block["admission"]) == {MARGIN_TARGET}
    status = block["admission"][MARGIN_TARGET]
    assert status["version"] == MARGIN_VERSION
    assert status["status"] in {"accumulating", "admitted", "not_admitted", "demoted"}
    assert status["reason"] and status["rule"] and status["title"]
    from indicators.journal import ADMISSION_MIN_EVENTS

    assert 0 <= status["events_needed"] <= ADMISSION_MIN_EVENTS     # у «Ленты» — 8 кварталов (D15)

    retro = block["retro"]
    assert "у уравнения нау-каста истории нет" in retro["note"]
    for key in ("margin", "revenue"):
        rows = retro[key]["benchmarks"]
        assert rows and sum(r["main"] for r in rows) == 1, key
        assert all(r["all"]["n"] > 0 and r["all"]["rmse"] > 0 for r in rows), key
    assert "available" in retro["interest"]


@pytest.mark.tact
def test_the_release_goes_without_the_interest_channel_until_there_is_a_register(tmp_path, monkeypatch):
    """Канал процентов Ленты ждёт реестра долга и корзин ставок (D12): без них
    блок процентов выпуска — период и класс причины, а сборка не падает (как
    такт нау-каста `collect.cmd_nowcast`). Текста ошибки в выпуске нет — в нём
    бывают пути сервера. Ошибка кода (не данных) по-прежнему роняет сборку."""
    import indicators.nowcast as nowcast
    from indicators.store import Store
    from model.payload import _interest_nowcast_block

    def missing_register(*_args, **_kwargs):
        raise FileNotFoundError("реестр долга: файла нет")

    monkeypatch.setattr(nowcast, "interest_nowcast", missing_register)
    assert _interest_nowcast_block(Store(tmp_path), {}, "2026Q3") == {
        "period": "2026Q3", "unavailable": "FileNotFoundError"}

    def broken_code(*_args, **_kwargs):
        raise TypeError("ошибка кода")

    monkeypatch.setattr(nowcast, "interest_nowcast", broken_code)
    with pytest.raises(TypeError):
        _interest_nowcast_block(Store(tmp_path), {}, "2026Q3")
