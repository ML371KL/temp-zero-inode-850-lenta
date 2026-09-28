# -*- coding: utf-8 -*-
"""Набор тестов не зависит от того, КАКОГО числа его гоняют.

Суточный такт гоняет тесты перед сборкой (`ops/run.sh`), поэтому тест,
сравнивающий литерал периода или даты с настоящим «сегодня», в такте —
таймер остановки витрины. Независимый аудит 24.09.2026 (G1-exam §1.4) нашёл
два таких теста на 01.01.2027 и двадцать один на 03.05.2027.

Здесь закреплён инструмент, которым это ловится: подмена «сегодня»
(`tests/fakedate_plugin.py`) должна быть инертна без переменной окружения и
доходить до подпроцессов с ней. Сам прогон «в будущем» — это весь набор при
`FAKE_TODAY` (CI по кнопке и раз в месяц, `.github/workflows/ci.yml`).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Инфраструктура тестов и CI-файлы, а не выпуск. Гоняется в CI.
pytestmark = pytest.mark.ci_only

ROOT = Path(__file__).resolve().parents[1]

PROBE = (
    "import datetime, subprocess, sys\n"
    "from tests import fakedate_plugin as p\n"
    "print('install', p.install())\n"
    "print('today', datetime.date.today().isoformat())\n"
    "print('now', datetime.datetime.now(datetime.timezone.utc).date().isoformat())\n"
    "from datetime import date\n"
    "print('imported', date.today().isoformat())\n"
    "child = subprocess.run([sys.executable, '-c', 'import datetime; "
    "print(datetime.date.today().isoformat())'], capture_output=True, text=True)\n"
    "print('child', child.stdout.strip())\n"
)


def _probe(env_update: dict, drop: tuple[str, ...] = ()) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in drop}
    # Прогон сам может идти «в будущем»: каталог sitecustomize из родителя
    # не должен протечь в пробу «без переменной».
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in env.get("PYTHONPATH", "").split(os.pathsep)
        if p and "fakedate_site" not in p)
    env.update(env_update, PYTHONIOENCODING="utf-8")
    done = subprocess.run([sys.executable, "-c", PROBE], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert done.returncode == 0, done.stderr[-1500:]
    return dict(line.split(" ", 1) for line in done.stdout.strip().splitlines())


def test_the_fake_today_is_inert_without_the_variable():
    """Без `FAKE_TODAY` подмена не делает НИЧЕГО — ни в процессе, ни в детях."""
    import datetime as real

    seen = _probe({}, drop=("FAKE_TODAY",))
    assert seen["install"] == "None"
    # Настоящий класс даты: сам этот прогон может идти «в будущем».
    clock = getattr(real.date, "_real_class", real.date)
    assert seen["today"] == seen["imported"] == clock.today().isoformat()
    assert seen["child"] == seen["today"]
    stamp = getattr(real.datetime, "_real_class", real.datetime)
    assert seen["now"] == stamp.now(real.timezone.utc).date().isoformat()


def test_the_fake_today_reaches_the_process_and_its_subprocesses():
    """С `FAKE_TODAY` дату видят `date.today`, `datetime.now` и подпроцесс.

    Подпроцесс — не формальность: настоящая сборка выпуска в тестах идёт
    отдельным процессом, и именно в ней истекают объяснения гейтов. Прогон «в
    будущем», который проверял бы сборку на сегодняшней дате, молчал бы ровно
    там, где такт упадёт.
    """
    seen = _probe({"FAKE_TODAY": "2027-05-03"})
    assert seen == {"install": "2027-05-03", "today": "2027-05-03", "now": "2027-05-03",
                    "imported": "2027-05-03", "child": "2027-05-03"}, seen


def test_ci_runs_the_suite_in_the_future_by_button_and_monthly():
    """CI умеет гонять весь набор «в будущем»: по кнопке и раз в месяц.

    После заморозки кода пушей нет, и первый сигнал о тесте-таймере был бы
    провалом боевого такта. Ежемесячная репетиция на 180 дней вперёд ловит
    его за полгода. Обычный push-прогон при этом не меняется: тот же набор,
    те же маркеры, прогон «в будущем» его не дублирует.
    """
    import yaml

    doc = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    on = doc[True]          # PyYAML читает ключ `on` как логическое True
    assert on["push"] == {"branches-ignore": ["release"]}, (
        "push-прогон — на любую ветку, кроме ветки данных release, без фильтров путей")
    future = on["workflow_dispatch"]["inputs"]["future_days"]
    assert future["default"] == 0 and future["type"] == "number"
    assert [item["cron"].split()[2:] for item in on["schedule"]] == [["1", "*", "*"]], (
        "расписание — раз в месяц, 1-го числа")

    job = doc["jobs"]["tests"]
    assert "'schedule' && '180'" in job["env"]["FUTURE_DAYS"]
    steps = {step.get("name"): step for step in job["steps"]}
    normal = steps["Тесты"]
    assert normal["if"] == "env.FUTURE_DAYS == '0'"
    assert 'pytest -q -m "not network" --strict-markers' in normal["run"]
    ahead = next(step for step in job["steps"] if "FAKE_TODAY" in step.get("run", ""))
    assert ahead["if"] == "env.FUTURE_DAYS != '0'"
    assert 'pytest -q -m "not network" --strict-markers' in ahead["run"]
    assert "+${FUTURE_DAYS} days" in ahead["run"]
