# -*- coding: utf-8 -*-
"""Конвейер `ops/run.sh` в песочнице: коды такта, порядок шагов, замок,
перезапуск новым скриптом, зависимости по отметке, пересборка и откат.

Тревоги владельцу доставляет ОДИН канал — код возврата юнита: `ExecStopPost`
зовёт общий мост /usr/local/sbin/dash-alert, и тот шлёт в Telegram смену
состояния по коду. Проверяется НЕ наличие строк в скрипте, а поведение:
настоящий `ops/run.sh` запускается с подставным питоном, который возвращает
заказанные коды, и спрашивается то, что увидит systemd, — код такта, — и то,
что увидит владелец в journalctl: причина в последней строке.

Коды (ops/run.sh, шапка): 0 — прошло; 1 — провал шага, выпуска нет; 8 —
тревога при выполненной работе; 64 / 75 / 78 — режим / замок / env.
Песочницы — git во временном каталоге; сети нет. Тесты не несут метку `tact`:
такт, который их гонял бы, уже исполняется тем самым run.sh.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_SH = ROOT / "ops" / "run.sh"
BASH = shutil.which("bash")
TACT_PYTEST_CALL = "ВЫЗОВ: -m pytest -q -m tact and not network and not docs and not ci_only"

# Подставной питон: печатает, чем его позвали, и возвращает заказанный код.
# Публикация и сверка ведут себя как `ops/publish.py` в том, что видит
# run.sh: push пишет отметку release.pushed с кодом выпуска, удачная сверка
# переносит этот код в release.commit и снимает отметку.
TACT_STUB = r"""#!/usr/bin/env bash
echo "ВЫЗОВ: $*"
state=${LENTA_STATE_DIR:?}
case "$*" in
  *"indicators.collect daily"*)    exit "${STUB_COLLECT_RC:-0}";;
  *"indicators.collect health"*)   exit "${STUB_HEALTH_RC:-0}";;
  *"indicators.collect nowcast"*)  exit "${STUB_NOWCAST_RC:-0}";;
  *build_release.py*)              exit "${STUB_BUILD_RC:-0}";;
  *"-m pip install"*)              exit "${STUB_PIP_RC:-0}";;
  *"-m pytest"*)
    if [[ -n ${FAKE_TODAY:-} ]]; then echo "FAKE_TODAY=$FAKE_TODAY"; exit "${STUB_FUTURE_RC:-0}"; fi
    exit "${STUB_PYTEST_RC:-0}";;
  *"ops/publish.py --verify"*)
    rc=${STUB_VERIFY_RC:-0}
    if [[ $rc == 0 && -f $state/release.pushed ]]; then
      sed -n 's/^ *"code_commit": *"\([0-9a-f]*\)".*/\1/p' "$state/release.pushed" > "$state/release.commit"
      rm -f "$state/release.pushed"
    fi
    exit "$rc";;
  *"ops/publish.py --prune"*)      exit "${STUB_PRUNE_RC:-0}";;
  *"ops/publish.py rollback"*)     exit "${STUB_ROLLBACK_RC:-0}";;
  *"ops/publish.py"*)
    rc=${STUB_PUBLISH_RC:-0}
    if [[ $rc == 0 ]]; then
      printf '{\n "kind": "publish",\n "code_commit": "%s"\n}\n' "$(git rev-parse HEAD)" > "$state/release.pushed"
    fi
    exit "$rc";;
esac
exit 0
"""

FLOCK_STUB = """#!/usr/bin/env bash
# Подставной flock: печатает, чем его позвали, и отвечает заказанным кодом.
echo "FLOCK: $*"
case "$1" in
  -n) exit "${STUB_FLOCK_N_RC:-0}";;
  -w) exit "${STUB_FLOCK_W_RC:-0}";;
esac
exit 0
"""

REQUIREMENTS = "pytest==9.1.1\n"


def _quoted(path) -> str:
    # Косые черты вперёд и кавычки: env-файл читается шеллом через `.`, и в
    # windows-пути обратная косая была бы экранированием.
    return "'" + str(path).replace("\\", "/") + "'"


def _git(*args, repo=None) -> str:
    cmd = ["git"] + (["-C", str(repo)] if repo else []) + [
        "-c", "user.email=t@t", "-c", "user.name=t", *args]
    done = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class Box:
    """Песочница такта: `origin` (туда «пушит» владелец), клон сервера `work`
    с настоящим ops/run.sh, каталог состояния, venv и подставной питон вне дерева."""

    def __init__(self, tmp_path: Path, files: dict[str, str] | None = None):
        assert BASH, "ops/run.sh — боевой конвейер панели; без bash его нечем проверить"
        self.tmp = tmp_path
        self.origin, self.work = tmp_path / "origin", tmp_path / "work"
        self.state, self.venv = tmp_path / "state", tmp_path / "venv"
        self.state.mkdir(parents=True)
        self.venv.mkdir()
        files = {"ops/run.sh": RUN_SH.read_text(encoding="utf-8"),
                 "requirements.txt": REQUIREMENTS,
                 "model/core.py": "x = 1\n", "docs/MANUAL.md": "справочник\n",
                 **(files or {})}
        for name, text in files.items():
            (self.origin / name).parent.mkdir(parents=True, exist_ok=True)
            (self.origin / name).write_text(text, encoding="utf-8", newline="\n")
        _git("init", "-q", "-b", "main", str(self.origin))
        _git("add", *files, repo=self.origin)
        _git("commit", "-q", "-m", "начало", repo=self.origin)
        _git("clone", "-q", "-b", "main", str(self.origin), str(self.work))
        self.stub = tmp_path / "stub-python"
        self.stub.write_text(TACT_STUB, encoding="utf-8", newline="\n")
        self.stub.chmod(0o755)
        self.env_file = tmp_path / "env"
        self.env_file.write_text(
            f"LENTA_REPO_DIR={_quoted(self.work)}\nLENTA_STATE_DIR={_quoted(self.state)}\n"
            f"LENTA_VENV={_quoted(self.venv)}\nLENTA_PYTHON={_quoted(self.stub)}\n",
            encoding="utf-8", newline="\n")

    def commit(self, path: str, text: str) -> str:
        (self.origin / path).parent.mkdir(parents=True, exist_ok=True)
        (self.origin / path).write_text(text, encoding="utf-8", newline="\n")
        _git("add", path, repo=self.origin)
        _git("commit", "-q", "-m", f"правка {path}", repo=self.origin)
        return self.head()

    def head(self) -> str:
        return _git("rev-parse", "HEAD", repo=self.origin)

    def work_head(self) -> str:
        return _git("rev-parse", "HEAD", repo=self.work)

    def mark(self, name: str) -> str:
        path = self.state / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def run(self, *args, script: Path | None = None, **stub_env):
        """Такт глазами systemd: (код, stdout+stderr, журнал в файле)."""
        # Окружение прогона тестов (conftest ставит LENTA_STATE_DIR) в песочницу
        # не протекает: такт видит только свой env-файл и заказанные коды.
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("LENTA_", "STUB_", "FAKE_TODAY"))}
        env["ENV_FILE"] = str(self.env_file)
        env.update({k: str(v) for k, v in stub_env.items()})
        done = subprocess.run([BASH, str(script or self.work / "ops" / "run.sh"), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, cwd=str(self.tmp))
        files = "".join(p.read_text(encoding="utf-8", errors="replace")
                        for p in sorted((self.state / "logs").glob("*.log")))
        return done.returncode, (done.stdout or "") + (done.stderr or ""), files


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def run_tact(tmp_path: Path, mode: str, **stub_env) -> tuple[int, str]:
    """Один такт в свежей песочнице: (код, stdout+stderr и журнал в файле).
    Вход для тестов других модулей (tests/test_gates_and_blocking.py)."""
    code, out, log = Box(tmp_path).run(mode, **stub_env)
    return code, out + log


def _calls(out: str) -> list[str]:
    return [line[len("ВЫЗОВ: "):] for line in out.splitlines() if line.startswith("ВЫЗОВ: ")]


def _last_line(out: str) -> str:
    return [line for line in out.splitlines() if line.strip()][-1]


# ------------------------------------------------------------ коды такта


def test_a_quiet_tact_returns_zero(tmp_path):
    """Спокойный такт обязан возвращать 0: иначе мост шлёт тревогу каждый день,
    а тревога, которая приходит всегда, не приходит вовсе."""
    for mode in ("collect", "daily"):
        box = Box(tmp_path / mode)
        code, out, log = box.run(mode, LENTA_FUTURE_WEEKDAY=9)
        assert code == 0, out[-3000:]
        assert "ТРЕВОГА" not in out and "ПРОВАЛ" not in out
        assert log.count("готово:") == 1, "журнал такта обязан лечь и в файл"


def test_the_daily_tact_runs_its_steps_in_order(box):
    """Сбор → нау-каст → здоровье → тесты такта → сборка → публикация →
    сверка → прореживание → объём. Тесты — ДО сборки, прореживание — после сверки."""
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    calls = [c for c in _calls(out) if not c.startswith("-m pip")]
    assert calls == [
        "-m indicators.collect daily --retry",
        "-m indicators.collect nowcast",
        "-m indicators.collect health",
        TACT_PYTEST_CALL[len("ВЫЗОВ: "):],
        "ops/build_release.py",
        "ops/publish.py",
        "ops/publish.py --verify",
        "ops/publish.py --prune",
        "-m indicators.collect status",
    ], calls


def test_an_irrecoverable_source_makes_the_collect_unit_raise_the_alarm(tmp_path):
    """Отказ невосполнимого источника — код 8 у юнита и названная причина.

    22.09.2026 у 850oa обход вакансий упал с HTTP 503 при коде юнита 0. Мост
    читает код, а не вывод, — и владелец не узнал ничего.
    """
    box = Box(tmp_path)
    code, out, _ = box.run("collect", STUB_COLLECT_RC=3, LENTA_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert _last_line(out).startswith("ТРЕВОГА (код 8): невосполнимый источник не собран")
    # Шаги после сбора обязаны выполниться: код — В КОНЦЕ, а не вместо работы.
    assert "indicators.collect status" in out and "indicators.collect health" in out


def test_unhealthy_state_makes_the_collect_unit_raise_the_alarm(tmp_path):
    box = Box(tmp_path)
    code, out, _ = box.run("collect", STUB_HEALTH_RC=1, LENTA_FUTURE_WEEKDAY=9)
    assert code == 8, out[-3000:]
    assert "ТРЕВОГА (код 8): состояние не в порядке" in _last_line(out)


def test_the_daily_tact_publishes_and_only_then_reports_the_alarm(box):
    """Невосполнимый пропуск не отменяет выпуск — прежний выпуск не станет
    свежее от того, что новый не вышел. Сборка, публикация и сверка проходят,
    код 8 — в самом конце."""
    code, out, _ = box.run("daily", STUB_COLLECT_RC=3)
    assert code == 8, out[-3000:]
    for call in ("ops/build_release.py", "ops/publish.py", "ops/publish.py --verify"):
        assert call in _calls(out), call
    assert out.index("ВЫЗОВ: ops/publish.py") < out.index("ТРЕВОГА (код 8)")
    assert "indicators.collect daily --retry" in out


def test_the_gate_alarm_of_the_build_reaches_the_unit_code(box):
    """Тревога сборки (код 3) — тревога юнита, а не остановка; строка называет
    обе возможные причины."""
    code, out, _ = box.run("daily", STUB_BUILD_RC=3)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert line.startswith("ТРЕВОГА (код 8): сборка подняла тревогу"), line
    assert "масса гейта разошлась с ожидаемой" in line and "срок объяснения гейта истекает" in line
    assert "ops/publish.py" in _calls(out), "выпуск собран — публикация обязана пройти"


def test_the_tact_survives_a_nowcast_that_cannot_record(box):
    code, out, _ = box.run("daily", STUB_NOWCAST_RC=3)
    assert code == 8, out[-3000:]
    assert "ТРЕВОГА (код 8): нау-каст не записан" in _last_line(out)
    assert "ops/publish.py --verify" in _calls(out)


def test_several_alarms_are_named_together(box):
    code, out, _ = box.run("daily", STUB_COLLECT_RC=3, STUB_BUILD_RC=3)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert "невосполнимый источник" in line and "сборка подняла тревогу" in line


def test_a_critical_source_failure_stops_the_tact_with_code_one(box):
    """Отказ КРИТИЧЕСКОГО источника (код 1 сбора) роняет такт: при неполных
    входах на витрину не уходит ничего."""
    code, out, _ = box.run("daily", STUB_COLLECT_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «сбор источников» (код 1)" in out
    assert "build_release.py" not in out, "выпуск не имел права собираться"


@pytest.mark.parametrize("stub,step", [
    ({"STUB_BUILD_RC": 2}, "сборка выпуска"),
    ({"STUB_BUILD_RC": 8}, "сборка выпуска"),
    ({"STUB_NOWCAST_RC": 137}, "нау-каст ближайшего отчёта"),
    ({"STUB_PYTEST_RC": 1}, "тесты такта"),
])
def test_any_failed_step_is_code_one_with_its_own_code_in_the_journal(box, stub, step):
    """Упавший шаг — всегда код 1: например, 8 от упавшей сборки мост иначе
    принял бы за «тревогу при опубликованном выпуске»."""
    code, out, _ = box.run("daily", **stub)
    rc = next(iter(stub.values()))
    assert code == 1, out[-3000:]
    assert f"ПРОВАЛ на шаге «{step}» (код {rc})" in out, out[-3000:]
    assert "ops/publish.py" not in _calls(out)
    assert box.mark("release.commit") == ""


def test_a_failed_publish_is_a_failed_step_and_is_not_verified(box):
    """Отказ push (после повторов внутри publish.py) — код 1: у суточного такта
    systemd повторит его через 3 минуты (`RestartPreventExitStatus=8` его не держит)."""
    code, out, _ = box.run("daily", STUB_PUBLISH_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «публикация в ветку release» (код 1)" in out
    assert "ops/publish.py --verify" not in _calls(out)


def test_an_unconfirmed_release_is_an_alarm_and_postpones_the_prune(box):
    """Сверка не дождалась GitHub Pages (код 3): выпуск уже в ветке — тревога,
    а не провал; прореживания нет, release.commit не пишется, отметка остаётся."""
    code, out, _ = box.run("daily", STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert "боевая дверь не подтвердила" in _last_line(out)
    assert "ops/publish.py --prune" not in _calls(out)
    assert box.mark("release.commit") == ""
    assert box.work_head() in box.mark("release.pushed")
    # Шаги после выпуска идут: такт доводится до конца.
    assert "-m indicators.collect status" in _calls(out)


def test_a_failed_prune_does_not_pretend_there_is_no_release(box):
    code, out, _ = box.run("daily", STUB_PRUNE_RC=1)
    assert code == 8, out[-3000:]
    assert "прореживание ветки release не прошло" in _last_line(out)
    assert box.mark("release.commit") == box.work_head(), "выпуск подтверждён сверкой"


def test_a_selection_without_tact_tests_is_a_failure(box):
    """Белый список, который никого не пускает (pytest: 5 — «нечего гонять»),
    выключил бы проверку перед выпуском — это провал, а не успех."""
    code, out, _ = box.run("daily", STUB_PYTEST_RC=5)
    assert code == 1, out[-3000:]
    assert "ни одного теста с меткой tact" in out
    assert "build_release.py" not in out


def test_the_daily_tact_remembers_the_code_of_the_release_it_confirmed(tmp_path):
    """release.commit — код выпуска, прошедшего сверку; упавший такт его не пишет."""
    ok = Box(tmp_path / "ok")
    code, out, _ = ok.run("daily")
    assert code == 0, out[-3000:]
    assert ok.mark("release.commit") == ok.work_head()
    assert ok.mark("release.pushed") == ""

    failed = Box(tmp_path / "fail")
    code, out, _ = failed.run("daily", STUB_BUILD_RC=2)
    assert code == 1, out[-3000:]
    assert failed.mark("release.commit") == ""


def test_an_alarm_probe_with_a_foreign_source_is_code_64(tmp_path):
    """Проба тревоги с источником не из такта (сбор отвечает 64) — не тихий ноль."""
    box = Box(tmp_path)
    code, out, _ = box.run("collect", STUB_COLLECT_RC=64, LENTA_SIMULATE_FAILURE="нет_такого",
                           LENTA_FUTURE_WEEKDAY=9)
    assert code == 64, out[-3000:]
    assert "ПРОБА ТРЕВОГИ" in out
    assert "--simulate-failure нет_такого" in out


@pytest.mark.parametrize("args,expected", [(("weekly",), 64), (("rollback",), 64),
                                           (("rollback", "zzz"), 64), (("rollback", "abc123"), 64)])
def test_a_wrong_mode_is_code_64(box, args, expected):
    code, out, _ = box.run(*args)
    assert code == expected, out[-3000:]
    assert "ВЫЗОВ:" not in out


def test_a_missing_env_file_or_variable_is_code_78(box):
    code, out, _ = box.run("daily", ENV_FILE=str(box.tmp / "нет-такого-файла"))
    assert code == 78 and "нет доступа" in out, out[-3000:]
    for dropped in ("LENTA_REPO_DIR", "LENTA_STATE_DIR"):
        text = "".join(line + "\n" for line in box.env_file.read_text(encoding="utf-8").splitlines()
                       if not line.startswith(dropped + "="))
        partial = box.tmp / f"env-{dropped}"
        partial.write_text(text, encoding="utf-8", newline="\n")
        code, out, _ = box.run("daily", ENV_FILE=str(partial))
        assert code == 78 and dropped in out, out[-3000:]
        assert "ВЫЗОВ:" not in out


# ------------------------------------------------ утренний такт и «будущее»


def _shell_today() -> tuple[str, int]:
    """«Сегодня» часов шелла (UTC), которыми живёт run.sh: дата и день недели.
    Не часы питона: прогон «в будущем» (FAKE_TODAY) подменяет их в этом
    процессе, а часы шелла — нет."""
    done = subprocess.run([BASH, "-c", "date -u '+%F %u'"], capture_output=True, text=True)
    day, weekday = done.stdout.split()
    return day, int(weekday)


def test_the_morning_tact_checks_the_future_once_a_week(tmp_path):
    """В день прогона «в будущем» утренний такт гоняет тесты такта с
    FAKE_TODAY = сегодня + 60 дней; в другие дни — нет."""
    today, weekday = _shell_today()
    box = Box(tmp_path / "day")
    code, out, _ = box.run("collect", LENTA_FUTURE_WEEKDAY=weekday)
    assert code == 0, out[-3000:]
    seen = re.findall(r"^FAKE_TODAY=(\d{4}-\d{2}-\d{2})$", out, re.M)
    assert len(seen) == 1, out[-3000:]
    ahead = (date.fromisoformat(seen[0]) - date.fromisoformat(today)).days
    assert ahead in (60, 61), ahead           # 61 — если полночь UTC пришлась на прогон
    assert "tact and not network" in out
    # Сегодняшние тесты утренний такт не гоняет: их гоняет вечерний перед сборкой.
    assert out.count("ВЫЗОВ: -m pytest") == 1

    other = Box(tmp_path / "other")
    code, out, _ = other.run("collect", LENTA_FUTURE_WEEKDAY=9)
    assert code == 0 and "-m pytest" not in out, out[-3000:]


def test_a_red_future_is_an_alarm_not_a_stop(tmp_path):
    box = Box(tmp_path)
    _, weekday = _shell_today()
    code, out, _ = box.run("collect", LENTA_FUTURE_WEEKDAY=weekday, STUB_FUTURE_RC=1)
    assert code == 8, out[-3000:]
    line = _last_line(out)
    assert "тесты такта с FAKE_TODAY=" in line and "через 60 дней суточный такт остановится" in line
    assert "indicators.collect health" in out, "работа такта идёт дальше"

    # Повтор юнита через 3 минуты (Restart=on-failure добирает источник) тесты
    # заново не гоняет, но итог дня повторяет: тревога не исчезает.
    code, out, _ = box.run("collect", LENTA_FUTURE_WEEKDAY=weekday)
    assert code == 8, out[-3000:]
    assert "-m pytest" not in out and "уже проверено сегодня (код 1)" in out
    assert "тесты такта с FAKE_TODAY=" in _last_line(out)


# ----------------------------------------------------- зависимости по отметке


def test_dependencies_are_installed_by_the_requirements_mark(box):
    """pip — только когда sha256(requirements.txt) разошёлся с отметкой в venv,
    и отметка пишется только после удачной установки."""
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert out.count("ВЫЗОВ: -m pip install") == 1
    mark = (box.venv / ".requirements.sha256").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"[0-9a-f]{64}", mark)

    code, out, _ = box.run("daily")
    assert code == 0 and "-m pip install" not in out and "на месте" in out, out[-3000:]

    box.commit("requirements.txt", REQUIREMENTS + "PyYAML==6.0.3\n")
    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert out.count("ВЫЗОВ: -m pip install") == 1
    assert (box.venv / ".requirements.sha256").read_text(encoding="utf-8").strip() != mark


def test_a_failed_install_stops_the_tact_and_is_retried_next_time(box):
    """Дыра 850oa: pip после `reset --hard` без повтора. Здесь неудача — провал
    такта, отметки нет, и следующий такт ставит снова, хотя HEAD уже не менялся."""
    code, out, _ = box.run("daily", STUB_PIP_RC=1)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «зависимости» (код 1)" in out
    assert not (box.venv / ".requirements.sha256").exists()
    assert "build_release.py" not in out

    code, out, _ = box.run("daily")
    assert code == 0, out[-3000:]
    assert "HEAD не менялся" in out and out.count("ВЫЗОВ: -m pip install") == 1


# ------------------------------------ перезапуск такта новым run.sh после обновления
#
# bash читает скрипт по ходу исполнения из уже открытого файла, а `git reset
# --hard` на шаге «обновление кода» кладёт на диск НОВЫЙ файл: без `exec` такт
# доигрывал бы ПРЕЖНИЙ run.sh (у 850oa — журнал такта 23.09.2026).

NEW_SCRIPT_MARK = "МЕТКА: исполняется НОВЫЙ ops/run.sh"
CHILD_SEES_MARK = "МЕТКА ПЕРЕЗАПУСКА У ПИТОНА: "


def _updating_box(tmp_path) -> Box:
    box = Box(tmp_path)
    script = RUN_SH.read_text(encoding="utf-8")
    anchor = "set -Eeuo pipefail\n"
    assert script.count(anchor) == 1
    box.commit("ops/run.sh", script.replace(anchor, anchor + f'echo "{NEW_SCRIPT_MARK}"\n', 1))
    # Подставной питон печатает, видит ли он метку перезапуска: она нужна только
    # самому run.sh, питон и тесты такта наследовать её не должны.
    call = 'echo "ВЫЗОВ: $*"\n'
    assert TACT_STUB.count(call) == 1, "песочница такта сменила вид подставного питона"
    box.stub.write_text(TACT_STUB.replace(
        call, call + f'echo "{CHILD_SEES_MARK}${{LENTA_REEXEC:-нет}}"\n', 1),
        encoding="utf-8", newline="\n")
    return box


def test_a_code_update_restarts_the_tact_with_the_new_run_sh(tmp_path):
    box = _updating_box(tmp_path)
    code, out, log_file = box.run("daily")
    assert code == 0, out[-3000:]
    assert "код обновился" in out and "перезапускаю такт новым ops/run.sh" in out, out[-3000:]
    assert out.count(NEW_SCRIPT_MARK) == 1, "новый run.sh исполняется, и ровно один раз"
    assert "перезапуск тем же тактом" in out
    assert "HEAD не менялся" in out, "перезапущенный такт находит код уже обновлённым"
    # Работа такта — один раз и уже новым файлом.
    assert out.count("ВЫЗОВ: ops/build_release.py") == 1, out[-3000:]
    assert out.count("ВЫЗОВ: -m pip install") == 1
    assert (out.index("перезапускаю такт") < out.index(NEW_SCRIPT_MARK)
            < out.index("ВЫЗОВ: -m pip install") < out.index("ВЫЗОВ: ops/build_release.py"))
    # Журнал в файле — без удвоенных строк: перезапуск пишет в тот же `tee`.
    assert log_file.count(NEW_SCRIPT_MARK) == 1, log_file[-3000:]
    assert log_file.count("готово:") == 1
    seen = re.findall(re.escape(CHILD_SEES_MARK) + r"(\S+)", out)
    assert len(seen) == out.count("ВЫЗОВ: ") and set(seen) == {"нет"}, seen
    assert box.work_head() == box.head()


def test_the_restart_happens_once_per_tact(tmp_path):
    """Защита от петли: такт, уже перезапущенный (LENTA_REEXEC задан), снова
    видит обновление — сбрасывает дерево, но `exec` не повторяет."""
    box = _updating_box(tmp_path)
    code, out, _ = box.run("daily", LENTA_REEXEC="0" * 40)
    assert code == 0, out[-3000:]
    assert "перезапускаю такт новым ops/run.sh" not in out
    assert "код обновился повторно за один такт" in out, out[-3000:]
    assert out.count("ВЫЗОВ: ops/build_release.py") == 1
    seen = re.findall(re.escape(CHILD_SEES_MARK) + r"(\S+)", out)
    assert seen and set(seen) == {"нет"}, seen


def test_an_unreadable_github_does_not_cost_the_morning_collection(box):
    """`git fetch` не прошёл — утренний сбор идёт на текущем коде: невосполнимый
    день источника дороже свежести кода."""
    _git("remote", "set-url", "origin", str(box.tmp / "нет-такого-репозитория"), repo=box.work)
    code, out, _ = box.run("collect", LENTA_FUTURE_WEEKDAY=9)
    assert code == 0, out[-3000:]
    assert "main на GitHub не читается" in out
    assert "-m indicators.collect daily" in _calls(out)


# ------------------------------------- пересборка при новом коде (таймер rebuild)


def test_the_rebuild_is_silent_until_the_counting_code_changes(box):
    """Коммит вне кода, который считает число, — тишина; правка `model/` — выпуск."""
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")

    box.commit("docs/MANUAL.md", "справочник, правка\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert out.strip() == "" and log_file == "", (
        "проверка раз в 15 минут обязана быть тихой:\n" + out[-2000:] + log_file[-2000:])

    # Документ внутри каталога кода (ops/README.md) числа не меняет.
    box.commit("ops/README.md", "эксплуатация\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0 and out.strip() == "" and log_file == "", out[-3000:]

    fresh = box.commit("model/core.py", "x = 2\n")
    code, out, log_file = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert "Лента 850 · такт rebuild" in out and "перезапускаю такт новым ops/run.sh" in out
    for call in (TACT_PYTEST_CALL, "ВЫЗОВ: ops/build_release.py", "ВЫЗОВ: ops/publish.py",
                 "ВЫЗОВ: ops/publish.py --verify", "ВЫЗОВ: ops/publish.py --prune"):
        assert out.count(call + "\n") == 1, (call, out[-3000:])
    # Без сбора и нау-каста: входы те, что собрал последний такт.
    assert "indicators.collect" not in out, out[-3000:]
    assert box.mark("release.commit") == fresh, "выпуск помнит код, которым собран"
    assert log_file.count("готово:") == 1

    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", "собранный код второй раз не пересобирается"


def test_a_new_requirement_wakes_the_rebuild(box):
    """requirements.txt — тоже код: новая версия зависимости может сдвинуть число."""
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    fresh = box.commit("requirements.txt", REQUIREMENTS + "PyYAML==6.0.3\n")
    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert "ВЫЗОВ: -m pip install" in out and "ВЫЗОВ: ops/build_release.py" in out
    assert box.mark("release.commit") == fresh


def test_a_failed_rebuild_is_not_repeated_on_the_same_commit(box):
    """Упавшая пересборка ждёт нового коммита: красный набор не крутится каждые 15 минут."""
    code, out, _ = box.run("rebuild", STUB_BUILD_RC=2)
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ на шаге «сборка выпуска» (код 2)" in out
    assert box.mark("release.commit") == ""
    assert box.mark("rebuild.tried") == box.head()

    code, out, _ = box.run("rebuild")
    assert code == 1, out[-3000:]
    assert "ПРОВАЛ (код 1): пересборка на коммите" in out and "уже запускалась" in out
    assert "ВЫЗОВ:" not in out, "на том же коммите — без тестов и сборки"

    # Суточный такт собрал выпуск на этом коде — провал снимается сам.
    (box.state / "release.commit").write_text(box.head() + "\n", encoding="utf-8")
    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", out[-3000:]

    fresh = box.commit("model/core.py", "x = 3\n")
    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert box.mark("release.commit") == fresh


def test_the_rebuild_finishes_the_verify_of_the_previous_tact(box):
    """Сверку, не дождавшуюся GitHub Pages, дожимает тихая проверка пересборки:
    одна попытка; прошла — release.commit появляется, пересобирать нечего."""
    code, out, _ = box.run("daily", STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert box.mark("release.commit") == "" and box.mark("release.pushed")

    # GitHub Pages всё ещё молчит: выпуск на этом коде уже в ветке — не
    # пересобирается и не пушится снова, одна строка в журнал, код 0.
    code, out, log_file = box.run("rebuild", STUB_VERIFY_RC=3)
    assert code == 0, out[-3000:]
    assert _calls(out) == ["ops/publish.py --verify --once"], out[-3000:]
    assert "ждёт сверки — не пересобираю" in out
    assert box.mark("release.commit") == "" and box.mark("rebuild.tried") == ""

    # Pages ожил: сверка проходит, release.commit пишется, пересобирать нечего.
    code, out, _ = box.run("rebuild")
    assert code == 0, out[-3000:]
    assert _calls(out) == ["ops/publish.py --verify --once"], out[-3000:]
    assert box.mark("release.commit") == box.work_head() and box.mark("release.pushed") == ""

    code, out, _ = box.run("rebuild")
    assert code == 0 and out.strip() == "", out[-3000:]


def test_a_pending_verify_does_not_hold_back_new_code(box):
    """Сверка висит, а в main пришёл новый код — пересборка идёт на новом коде."""
    code, out, _ = box.run("daily", STUB_VERIFY_RC=3)
    assert code == 8
    fresh = box.commit("model/core.py", "x = 4\n")
    code, out, _ = box.run("rebuild", STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert "ВЫЗОВ: ops/build_release.py" in out
    assert fresh in box.mark("release.pushed")


def test_the_mark_of_the_publisher_is_read_by_the_rebuild(tmp_path):
    """run.sh читает код выпуска из отметки `release.pushed` выражением sed;
    отметку пишет `ops/publish.py` — выражение обязано понимать её настоящий вид."""
    spec = importlib.util.spec_from_file_location("publish_for_mark", ROOT / "ops" / "publish.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    repo = tmp_path / "repo"
    _git("init", "-q", "-b", "main", str(repo))
    _git("commit", "-q", "--allow-empty", "-m", "код", repo=repo)
    cfg = publish.Config(state_dir=tmp_path / "state", code_root=repo)
    cfg.state_dir.mkdir()
    publish.write_pushed_mark(cfg, kind="publish", digest="a" * 64,
                              published_at="2026-09-28T17:30:00+00:00", clone=repo)
    expr = re.search(r"pending_code=\$\(sed -n '([^']+)'", RUN_SH.read_text(encoding="utf-8")).group(1)
    stub_expr = re.search(r"sed -n '([^']+)' \"\$state/release.pushed\"", TACT_STUB).group(1)
    assert expr == stub_expr, "подставной питон читает отметку не так, как run.sh"
    done = subprocess.run([BASH, "-c", f"sed -n '{expr}' \"$1\"", "sed", str(cfg.pushed_mark)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.stdout.strip() == _git("rev-parse", "HEAD", repo=repo)
    assert json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["code_commit"] == done.stdout.strip()


# ------------------------------------------------------------------ откат


def test_a_rollback_repoints_and_verifies_under_the_lock(box):
    sha = "ab" * 32
    code, out, log_file = box.run("rollback", sha[:12])
    assert code == 0, out[-3000:]
    assert _calls(out) == [f"ops/publish.py rollback {sha[:12]}", "ops/publish.py --verify"], out
    assert "обновление кода" not in out, "откат делает ровно одно и кода не трогает"
    assert log_file.count("готово:") == 1

    code, out, _ = box.run("rollback", sha, STUB_VERIFY_RC=3)
    assert code == 8, out[-3000:]
    assert "откат отправлен в ветку release, но боевая дверь его не подтвердила" in _last_line(out)

    code, out, _ = box.run("rollback", sha, STUB_ROLLBACK_RC=1)
    assert code == 1 and "ПРОВАЛ на шаге «откат указателя" in out, out[-3000:]
    assert "ops/publish.py --verify" not in _calls(out)


# ------------------------------------------------------------------- замок


def test_a_busy_lock_turns_the_rebuild_away_and_makes_a_tact_wait(tmp_path):
    """Замок занят: пересборка молча уступает, плановый такт ждёт, а не падает.
    Настоящего flock в Git Bash нет, поэтому здесь — подставной в PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "flock").write_text(FLOCK_STUB, encoding="utf-8", newline="\n")
    (bin_dir / "flock").chmod(0o755)
    path = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
    wait = re.search(r"^LOCK_WAIT_SECONDS=\$\{LOCK_WAIT_SECONDS:-(\d+)\}$",
                     RUN_SH.read_text(encoding="utf-8"), re.M).group(1)

    code, out, _ = Box(tmp_path / "rebuild").run("rebuild", PATH=path, STUB_FLOCK_N_RC=1)
    assert code == 0, out[-2000:]
    assert "ВЫЗОВ:" not in out and "такт rebuild" not in out, out[-2000:]

    code, out, _ = Box(tmp_path / "wait").run("collect", PATH=path, STUB_FLOCK_N_RC=1,
                                              LENTA_FUTURE_WEEKDAY=9)
    assert code == 0, out[-2000:]
    assert f"жду до {wait} с" in out and f"FLOCK: -w {wait} 9" in out, out[-2000:]
    assert "ВЫЗОВ: -m indicators.collect daily" in out, "дождавшись замка, такт работает"

    code, out, _ = Box(tmp_path / "gone").run("collect", PATH=path, STUB_FLOCK_N_RC=1,
                                              STUB_FLOCK_W_RC=1)
    assert code == 75, out[-2000:]
    assert "прогон уже идёт, выхожу" in out and "ВЫЗОВ:" not in out

    code, out, _ = Box(tmp_path / "rollback").run("rollback", "cd" * 6, PATH=path,
                                                  STUB_FLOCK_N_RC=1, STUB_FLOCK_W_RC=1)
    assert code == 75 and "ВЫЗОВ:" not in out, "откат ждёт тот же замок, что и такты"


# ------------------------------------------------------- согласие с кодом выпуска


def test_run_sh_code_dirs_are_the_release_code_dirs():
    """Каталоги, правка которых будит пересборку, — ровно `CODE_DIRS` выпуска
    (model/payload.py: `meta.engine_commit` и пометка «грязное»), включая
    requirements.txt в обоих списках (P4b). Разойдись списки — выпуск назвал бы
    код изменившимся, а пересборка промолчала бы (или наоборот)."""
    from model.payload import CODE_DIRS

    found = re.findall(r"^CODE_DIRS=\(([^)]*)\)$", RUN_SH.read_text(encoding="utf-8"), re.M)
    assert len(found) == 1, found
    assert found[0].split() == list(CODE_DIRS)
    assert "requirements.txt" in CODE_DIRS


def test_the_script_is_valid_bash_with_unix_line_endings():
    assert b"\r\n" not in RUN_SH.read_bytes(), "CRLF: на сервере «bad interpreter»"
    done = subprocess.run([BASH, "-n", str(RUN_SH)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
