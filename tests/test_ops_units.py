# -*- coding: utf-8 -*-
"""Юниты systemd, потолки времени, образец env-файла и отсутствие R2.

Юниты ставятся на сервер побайтовыми копиями из `ops/systemd/`, поэтому их
свойства проверяются здесь, а не на сервере: шаблон защиты панелей владельца,
политика перезапуска (тревога — код 8 — не повторяется, провал шага —
повторяется), расписание DESIGN §2 и потолки `RuntimeMaxSec`, посчитанные из
`ops/budgets.json` по одной формуле с `LOCK_WAIT_SECONDS` в `ops/run.sh`.
"""
from __future__ import annotations

import importlib.util
import json
import math
import re
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "ops"
UNITS = OPS / "systemd"
MODES = ("collect", "daily", "rebuild")
REPO = "/srv/dash/repo-850-lenta"


def _unit(path: Path) -> dict[str, dict[str, list[str]]]:
    """Юнит как {секция: {ключ: [значения]}} — ключ может повторяться."""
    sections: dict[str, dict[str, list[str]]] = {}
    current = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
            continue
        key, _, value = line.partition("=")
        assert current is not None, f"{path.name}: {line!r} вне секции"
        current.setdefault(key.strip(), []).append(value.strip())
    return sections


def _one(unit: dict, section: str, key: str) -> str:
    values = unit.get(section, {}).get(key, [])
    assert len(values) == 1, f"{section}.{key}: {values}"
    return values[0]


def _budgets() -> dict:
    return json.loads((OPS / "budgets.json").read_text(encoding="utf-8"))


def _ceilings() -> dict[str, int]:
    """Потолки юнитов по ops/budgets.json (формула — в его поле about)."""
    b = _budgets()
    step = int(b["round_to"])

    def run(unit: str) -> int:
        total = sum(b["seconds"][k] for k in b["units"][unit])
        return math.ceil(total * b["safety"] / step) * step

    lock = run(b["lock_wait_unit"])
    return {u: run(u) + (0 if u == b["lock_wait_unit"] else lock) for u in b["units"]}


def test_there_are_exactly_three_pairs_of_units():
    names = sorted(p.name for p in UNITS.iterdir())
    assert names == sorted(f"lenta850-{m}.{kind}" for m in MODES for kind in ("service", "timer"))
    for path in UNITS.iterdir():
        assert b"\r\n" not in path.read_bytes(), f"{path.name}: CRLF"


@pytest.mark.parametrize("mode", MODES)
def test_every_service_follows_the_panel_template(mode):
    unit = _unit(UNITS / f"lenta850-{mode}.service")
    assert "Install" not in unit, "юнит запускает таймер; [Install] дал бы прогон при загрузке"
    expected = {
        "Type": "exec", "User": "dash", "Group": "dash",
        "WorkingDirectory": REPO,
        "EnvironmentFile": "/usr/local/etc/lenta-850/env",
        "Environment": "PYTHONUNBUFFERED=1",
        "ExecStart": f"/bin/bash {REPO}/ops/run.sh {mode}",
        "SyslogIdentifier": "lenta850", "StateDirectory": "lenta-850",
        "PrivateTmp": "yes", "ProtectSystem": "strict", "ProtectHome": "yes",
        "NoNewPrivileges": "yes", "ReadWritePaths": REPO,
        "MemoryHigh": "384M", "MemoryMax": "512M", "Nice": "10",
        "ExecStopPost": "-+/usr/local/sbin/dash-alert %n",
    }
    for key, value in expected.items():
        assert _one(unit, "Service", key) == value, key
    assert _one(unit, "Unit", "Wants") == "network-online.target"
    assert _one(unit, "Unit", "After") == "network-online.target"


def test_only_the_morning_tact_is_cut_off_from_the_deploy_key():
    """Утренний такт разбирает чужой HTML и ничего не публикует — ключ записи
    ему недоступен. Суточному такту и пересборке каталог нужен: они пушат."""
    for mode in MODES:
        unit = _unit(UNITS / f"lenta850-{mode}.service")
        hidden = unit["Service"].get("InaccessiblePaths", [])
        if mode == "collect":
            assert hidden == ["/srv/dash/.ssh"]
        else:
            assert not any(".ssh" in h for h in hidden), mode
            text = (UNITS / f"lenta850-{mode}.service").read_text(encoding="utf-8")
            assert not re.search(r"^(TemporaryFileSystem|ProtectHome=tmpfs).*", text, re.M)


def test_the_restart_policy_repeats_failures_and_not_alarms():
    """Суточный такт: провал шага (1) повторяется — моргнувший push лечится
    повтором; тревога (8) — нет. Утренний повторяет и тревогу: повтор добирает
    невосполнимый источник. Пересборка не повторяется вовсе (rebuild.tried)."""
    daily = _unit(UNITS / "lenta850-daily.service")["Service"]
    assert daily["Restart"] == ["on-failure"] and daily["RestartSec"] == ["180"]
    prevent = daily["RestartPreventExitStatus"][0].split()
    assert "8" in prevent and "1" not in prevent and set(prevent) == {"8", "64", "78"}

    collect = _unit(UNITS / "lenta850-collect.service")["Service"]
    assert collect["Restart"] == ["on-failure"]
    assert set(collect["RestartPreventExitStatus"][0].split()) == {"64", "78"}

    rebuild = _unit(UNITS / "lenta850-rebuild.service")["Service"]
    assert "Restart" not in rebuild and "RestartPreventExitStatus" not in rebuild

    for mode in ("collect", "daily"):
        unit = _unit(UNITS / f"lenta850-{mode}.service")["Unit"]
        assert unit["StartLimitIntervalSec"] == ["1800"] and unit["StartLimitBurst"] == ["3"]


def test_run_sh_ends_alarms_with_the_code_the_units_do_not_repeat():
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    finish = re.search(r"^finish\(\) \{(.*?)^\}", script, re.M | re.S).group(1)
    assert "exit 8" in finish and "ТРЕВОГА (код 8)" in finish


@pytest.mark.parametrize("mode,calendar,persistent", [
    ("collect", "*-*-* 04:20:00 UTC", "true"),
    ("daily", "Mon..Fri *-*-* 17:25:00 UTC", "true"),
    ("rebuild", "*:07,22,37,52", None),
])
def test_the_timers_follow_the_design_schedule(mode, calendar, persistent):
    timer = _unit(UNITS / f"lenta850-{mode}.timer")
    assert _one(timer, "Timer", "OnCalendar") == calendar
    assert _one(timer, "Timer", "Unit") == f"lenta850-{mode}.service"
    assert timer["Timer"].get("Persistent", [None]) == [persistent]
    assert _one(timer, "Install", "WantedBy") == "timers.target"
    if mode == "daily":
        assert _one(timer, "Timer", "RandomizedDelaySec") == "300"


def test_runtime_ceilings_are_computed_from_the_budget_file():
    """Потолок юнита — из замера (ops/budgets.json), а не на глаз: потолок ниже
    настоящего времени убил бы такт на середине (урок 850oa, 1 200 с)."""
    ceilings = _ceilings()
    for mode in MODES:
        unit = _unit(UNITS / f"lenta850-{mode}.service")
        assert int(_one(unit, "Service", "RuntimeMaxSec")) == ceilings[mode], (
            f"lenta850-{mode}.service: RuntimeMaxSec обязан быть {ceilings[mode]} "
            "по ops/budgets.json")


def test_the_lock_wait_equals_the_rebuild_ceiling():
    """Плановый такт ждёт замок столько, сколько пересборка может его держать."""
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    found = re.findall(r"^LOCK_WAIT_SECONDS=\$\{LOCK_WAIT_SECONDS:-(\d+)\}$", script, re.M)
    assert found == [str(_ceilings()[_budgets()["lock_wait_unit"]])]


def test_the_budget_file_is_honest_about_itself():
    b = _budgets()
    assert b["status"] in ("estimate", "measured")
    if b["status"] == "measured":
        date.fromisoformat(b["measured_on"])
    else:
        assert b["measured_on"] is None
    assert set(b["units"]) == set(MODES) and b["lock_wait_unit"] == "rebuild"
    for unit, steps in b["units"].items():
        assert steps and all(s in b["seconds"] for s in steps), unit
    assert b["seconds"]["tact_tests"] <= b["tact_tests_target"] == 180, (
        "тесты такта дольше 3 минут: сократить белый список (метка tact)")
    spec = importlib.util.spec_from_file_location("publish_for_budget", OPS / "publish.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    assert b["seconds"]["verify_timeout"] == publish.VERIFY_TIMEOUT, (
        "окно сверки в потолке и в publish.py — одно")
    # Каждый такт в потолке учитывает установку зависимостей по отметке.
    assert all("pip" in steps for steps in b["units"].values())


def _env_example() -> dict[str, str]:
    values = {}
    for line in (OPS / "env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            key, sep, value = line.partition("=")
            assert sep and key == key.strip() and re.fullmatch(r"[A-Z0-9_]+", key), line
            values[key] = value
    return values


def test_the_env_example_names_every_setting_and_no_secret():
    env = _env_example()
    for key in ("LENTA_REPO_DIR", "LENTA_STATE_DIR", "LENTA_RELEASE_REMOTE",
                "LENTA_GIT_NAME", "LENTA_GIT_EMAIL", "LENTA_PUBLIC_URL"):
        assert env.get(key), key
    assert env["LENTA_REPO_DIR"] == REPO and env["LENTA_STATE_DIR"] == "/var/lib/lenta-850"
    assert not [k for k in env if re.search(r"TOKEN|SECRET|PASSWORD|ACCESS_KEY|R2|RCLONE", k)]
    assert env["LENTA_RELEASE_REMOTE"].startswith("gh-850-lenta:"), "только алиас ssh"
    allowed = [line.strip() for line in (OPS / "commit-emails.allow").read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.startswith("#")]
    assert env["LENTA_GIT_EMAIL"] in allowed
    spec = importlib.util.spec_from_file_location("publish_for_env", OPS / "publish.py")
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    assert env["LENTA_PUBLIC_URL"] == publish.DEFAULT_PUBLIC_URL
    # Всё, без чего run.sh выходит с кодом 78, в образце есть.
    script = (OPS / "run.sh").read_text(encoding="utf-8")
    required = set(re.findall(r'\[\[ -n \$\{(LENTA_[A-Z_]+):-\} \]\] \|\| \{ echo "в \$ENV_FILE не задан', script))
    assert required == {"LENTA_REPO_DIR", "LENTA_STATE_DIR"} and required <= set(env)


def test_no_r2_is_left_in_ops_functions_and_pages_config():
    """Канал R2 снят целиком: ни публикации, ни ключей rclone, ни биндинга."""
    paths = [p for p in [*OPS.rglob("*"), *(ROOT / "functions").rglob("*"), ROOT / "wrangler.toml"]
             if p.is_file() and "__pycache__" not in p.parts]
    bad = []
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        for hit in re.finditer(r"\bR2\b|rclone|r2_buckets|bind_r2|R2_[A-Z]|wrangler r2", text):
            bad.append(f"{path.relative_to(ROOT)}: {hit.group(0)}")
    assert not bad, bad


@pytest.mark.parametrize("path,ignored", [
    ("payload.json", True), ("release/latest.json", True), ("out/report.json", True),
    ("port-check/work/x.py", True), ("indicators/journal.sqlite", True),
    ("report.pdf", True), ("data/facts/databook.xlsx", True), (".wrangler/cache/x", True),
    ("var/test-state/x.json", True), ("var/.gitkeep", False),
    ("tests/fixtures/databook/synthetic.xlsx", False), ("ops/run.sh", False),
])
def test_the_gitignore_keeps_state_primary_documents_and_wrangler_out(path, ignored):
    """Первичка, состояние и каталог wrangler (ID аккаунта, почта) не попадают в
    дерево даже по `git add` каталога; синтетические фикстуры — попадают."""
    import subprocess

    done = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=str(ROOT))
    assert done.returncode in (0, 1), done
    assert (done.returncode == 0) == ignored, path


@pytest.mark.docs
def test_the_ops_readme_names_codes_units_settings_and_commands():
    """ops/README.md — рабочий документ: коды юнита, юниты, настройки из
    образца и команды отката и выкладки совпадают с кодом."""
    text = (OPS / "README.md").read_text(encoding="utf-8")
    for code in ("0", "1", "8", "64", "75", "78"):
        assert re.search(rf"^\| {code} \|", text, re.M), f"код {code} не описан"
    for mode in MODES:
        assert f"lenta850-{mode}" in text
    for key in _env_example():
        assert key in text, f"{key} из ops/env.example не описан"
    assert "ops/run.sh rollback <sha" in text
    wrangler = re.search(r"npx wrangler@([\d.]+) pages deploy web --project-name tzi-850-lenta --branch main",
                         (ROOT / "wrangler.toml").read_text(encoding="utf-8")).group(1)
    assert f"npx wrangler@{wrangler} pages deploy web --project-name tzi-850-lenta --branch main" in text
