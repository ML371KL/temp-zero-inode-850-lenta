# -*- coding: utf-8 -*-
"""Какие тесты где идут: CI и суточный такт.

Репозиторий публичный, минуты CI бесплатны: полный набор идёт на каждый push
в main и рабочие ветки (не в `release`) и на каждый PR, без исключений для
документов (отдельного облегчённого прогона нет). Тесты, читающие документы
`*.md`, носят метку `docs`: суточный такт их не гоняет. Здесь — сторожа
ci.yml (триггеры, закреплённые действия, гигиена истории, замер такта,
контракт выпуска), сторож, что метка не забыта, и сторож, что полный выпуск
не вернулся в такт.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

# CI-файлы и метки тестов, а не выпуск. Гоняется в CI.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

# Тесты, где имя документа встречается, но настоящий документ не читается:
# песочница git (пересборку не будят файлы документов) — имена там условные.
NOT_A_DOCUMENT_READ = {
    ("test_run_sh.py", "test_the_rebuild_is_silent_until_the_counting_code_changes"),
}
# Отчёты инструментов, которые тест сам пишет во временный каталог.
TOOL_REPORTS = {"CANDIDATE", "REANCHOR"}
MD = "." + "md"          # без литерала «…md»: сторож читает и этот файл


def _names_a_document(value: str) -> bool:
    if not value.endswith(MD) or value.startswith("**"):      # «**.md» — маска путей CI
        return False
    return Path(value).stem not in TOOL_REPORTS


def _ci() -> dict:
    return yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))


def _runs(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_every_push_and_pull_request_runs_the_full_suite():
    """Каждый push в main и рабочие ветки и каждый PR — полный набор: без
    фильтров путей и без облегчённого прогона; ветка данных `release` CI не
    будит; раннер закреплён версией."""
    doc = _ci()
    on = doc[True]
    assert on["push"] == {"branches-ignore": ["release"]}, "push: только release вне CI"
    assert not on["pull_request"], "pull_request: фильтр веток или путей"
    assert "workflow_dispatch" in on and "future_days" in on["workflow_dispatch"]["inputs"]
    assert "pull_request_target" not in on
    assert sorted(p.name for p in WORKFLOWS.glob("*.yml")) == ["ci.yml"], "лишний workflow"
    job = doc["jobs"]["tests"]
    assert job["runs-on"] == "ubuntu-24.04"
    assert doc["permissions"] == {"contents": "read"}
    assert all("permissions" not in j for j in doc["jobs"].values()), "права только на чтение"
    assert 'pytest -q -m "not network" --strict-markers' in _runs(job)
    assert "needs_book" not in _runs(job), "книга 1.0 в data/: тесты needs_book идут в общем наборе"


def test_ci_checks_the_world_canon_and_the_numbers_of_the_book_text():
    """Решение ведущего F34: оба пути канона миров; числа текста книги — results.json."""
    runs = _runs(_ci()["jobs"]["tests"])
    for command in ("(cd data/assumptions && python -B worlds_recipe.py --check)",
                    "python -B ops/tools/refresh_worlds.py --check",
                    "python -B ops/tools/render_numbers.py --check"):
        assert command in runs, command


def test_every_action_is_pinned_by_a_full_commit_sha():
    """Тег действия можно передвинуть задним числом, коммит — нет."""
    for name, job in _ci()["jobs"].items():
        assert job["runs-on"] == "ubuntu-24.04", name
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "uses:" in line:
            assert re.search(r"# v\d+\.\d+\.\d+$", line), f"нет тега в комментарии: {line}"


def test_the_future_run_has_its_own_group_and_dates():
    doc = _ci()
    assert "future" in doc["concurrency"]["group"] and "schedule" in doc["concurrency"]["group"]
    assert doc[True]["schedule"] == [{"cron": "17 5 1 * *"}]
    runs = _runs(doc["jobs"]["tests"])
    assert "FAKE_TODAY=$(date -u -d \"+${FUTURE_DAYS} days\" +%F)" in runs
    assert "'180'" in doc["jobs"]["tests"]["env"]["FUTURE_DAYS"]


def test_the_history_hygiene_step_reads_the_allowlist():
    """Шаг истории видит ВСЕ ветки (fetch-depth 0) и сверяет адреса со списком,
    который читает и tests/test_public_hygiene.py."""
    job = _ci()["jobs"]["tests"]
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    runs = _runs(job)
    assert "git log --all --format='%H %ae %ce'" in runs and "ops/commit-emails.allow" in runs
    assert (ROOT / "ops" / "commit-emails.allow").exists()


def test_the_tact_timing_step_uses_the_servers_selection():
    """CI меряет ровно тот белый список, что сервер гоняет перед сборкой."""
    runs = _runs(_ci()["jobs"]["tests"])
    assert r"""sed -n 's/^TACT_TESTS="\(.*\)"$/\1/p' ops/run.sh""" in runs
    assert "--durations=0" in runs
    tact = re.search(r'^TACT_TESTS="([^"]+)"$', (ROOT / "ops" / "run.sh").read_text(encoding="utf-8"),
                     re.M).group(1)
    assert tact.startswith("tact and "), "такт — белый список по метке tact"


def test_the_door_is_tested_in_node_in_ci():
    job = _ci()["jobs"]["tests"]
    assert any(s.get("uses", "").startswith("actions/setup-node@") for s in job["steps"])
    assert "node --test tests/functions/model.test.mjs" in _runs(job)
    assert (ROOT / "tests" / "functions" / "model.test.mjs").exists()


def test_the_release_contract_is_skipped_only_while_the_repo_declares_no_book():
    """Контракт выпуска — отдельное задание; пропуск только пока pytest.ini
    исключает needs_book, и громко (предупреждение), иначе — красное задание."""
    job = _ci()["jobs"]["release-contract"]
    runs = _runs(job)
    from model.book import BOOK_YAML

    rel = BOOK_YAML.relative_to(ROOT).as_posix()
    assert f"[ ! -f {rel} ]" in runs, "задание ищет книгу там, где её читает ядро"
    assert "grep -q 'not needs_book' pytest.ini" in runs
    assert "::warning" in runs and "::error::" in runs and "exit 1" in runs
    assert runs.rstrip().endswith("python ops/build_release.py --check --fast")


def test_no_test_is_both_tact_and_ci_only():
    """Противоречивая пара меток: такт такой тест не возьмёт (`not ci_only`),
    а автор будет думать, что взял."""
    both = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_marks = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                    for t in node.targets):
                module_marks |= set(re.findall(r"mark\.(\w+)", ast.unparse(node.value)))
        for fn in tree.body:
            if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test"):
                marks = module_marks | {m for d in fn.decorator_list
                                        for m in re.findall(r"mark\.(\w+)", ast.unparse(d))}
                if {"tact", "ci_only"} <= marks:
                    both.append(f"{path.name}::{fn.name}")
    assert not both, both


def test_every_test_that_reads_a_document_is_marked_docs():
    """Тест, читающий документ `*.md` репозитория, обязан нести метку `docs`.

    Иначе облегчённый прогон его не увидит, и правка документа, которую он
    ловит, дойдёт до `v5` непроверенной. Документ узнаётся по строке-имени
    файла `*.md` в теле теста или по константе модуля, где такие имена
    лежат.
    """
    missing = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        constants = set()
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(c, ast.Constant) and isinstance(c.value, str) and _names_a_document(c.value)
                       for c in ast.walk(node)):
                    constants |= {t.id for t in targets if isinstance(t, ast.Name)}
        for fn in tree.body:
            if not (isinstance(fn, ast.FunctionDef) and fn.name.startswith("test")):
                continue
            if (path.name, fn.name) in NOT_A_DOCUMENT_READ:
                continue
            body = fn.body[1:] if ast.get_docstring(fn) else fn.body
            nodes = [n for stmt in body for n in ast.walk(stmt)]
            reads = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                        and (_names_a_document(n.value) or n.value == "*" + MD) for n in nodes)
            reads = reads or any(isinstance(n, ast.Name) and n.id in constants for n in nodes)
            marked = any("mark.docs" in ast.unparse(d) for d in fn.decorator_list)
            if reads and not marked:
                missing.append(f"{path.name}::{fn.name}")
    assert not missing, "тесты читают документы без метки docs: " + ", ".join(missing)


@pytest.mark.needs_book
def test_the_tact_refuses_a_full_release_in_a_test(tmp_path):
    """Тест, собирающий полный выпуск без метки `ci_only`, в прогоне такта падает.

    Прогон — отдельным pytest с выражением такта из `ops/run.sh`; проба —
    быстрый выпуск (проходит) и полный (падает с причиной). Свой `basetemp`:
    общий каталог временных файлов прогон очищает при старте.
    """
    import re
    import subprocess
    import sys
    import textwrap

    tact = re.search(r'^TACT_TESTS="([^"]+)"$', (ROOT / "ops" / "run.sh").read_text(encoding="utf-8"),
                     re.M).group(1)
    assert tact.startswith("tact and ") and "not ci_only" in tact
    assert "archive" not in tact and "needs_book" not in tact
    # Такт — белый список (метка `tact`), поэтому оба пробных теста помечены:
    # сторож обязан поймать непомеченный `ci_only` полный выпуск ВНУТРИ такта.
    probe = tmp_path / "test_probe.py"
    probe.write_text(textwrap.dedent("""
        import pytest

        from model.engine import run_release
        from model.payload import build_payload


        @pytest.mark.tact
        def test_fast():
            assert build_payload(run_release(gates=False), with_slow=False)['judgements'] == []


        @pytest.mark.tact
        def test_full():
            build_payload(run_release(gates=False))
    """), encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", tact, "-p", "tests.conftest",
         "-p", "no:cacheprovider", f"--basetemp={tmp_path / 'bt'}", "--rootdir", str(ROOT),
         "-c", str(ROOT / "pytest.ini"), str(probe)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    out = done.stdout + done.stderr
    assert done.returncode == 1, out[-3000:]
    assert "1 failed, 1 passed" in out, out[-3000:]
    assert "полный выпуск" in out and "в прогоне без `ci_only`" in out, out[-3000:]
