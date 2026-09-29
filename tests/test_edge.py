# -*- coding: utf-8 -*-
"""Край: дверь `/api/model`, фильтр `/api/`, маршруты функций, CSP, 404.

Поведение двери проверяет Node-тест `tests/functions/model.test.mjs` (функция
исполняется с подменёнными `fetch` и `caches`); здесь — его запуск из pytest и
статические свойства файлов, которые Cloudflare читает сам (`_routes.json`,
`_headers`, `404.html`).
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
FUNCTIONS = ROOT / "functions"

# Статика витрины и functions/ — CI (такт их не гоняет: витрина выкладывается с
# ноутбука, а на сервере Node может и не быть).
pytestmark = pytest.mark.ci_only


def test_the_model_function_behaves_in_node():
    """Настоящее исполнение функции: источники, 503, копия, HEAD, 405, ETag."""
    node = shutil.which("node")
    assert node, ("нет node в PATH: поведенческий тест двери данных без него не запустить "
                  "(Node 22+; в CI — actions/setup-node)")
    # Формат отчёта задан явно: без терминала Node 22 печатает TAP, а Node 26 —
    # spec, и разбор «ℹ fail 0» молча зависел от версии (CI 29.09.2026 упал при
    # 18 зелёных из 18).
    done = subprocess.run([node, "--test", "--test-reporter=tap",
                           str(Path("tests") / "functions" / "model.test.mjs")],
                          cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=300)
    out = done.stdout + done.stderr
    assert done.returncode == 0, out[-4000:]
    passed = re.search(r"^# pass (\d+)$", out, re.M)
    assert re.search(r"^# fail 0$", out, re.M) and passed and int(passed.group(1)) > 0, out[-2000:]


def test_api_allowlist_matches_functions():
    """Список имён в фильтре и файлы functions/api/ — один набор: разойдутся —
    новая ручка отдаст 404 при живом коде."""
    middleware = (FUNCTIONS / "_middleware.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'"(\w+)"', re.search(
        r"ALLOWED_API = new Set\(\[([^\]]*)\]\)", middleware).group(1)))
    actual = {p.stem for p in (FUNCTIONS / "api").glob("*.js")}
    assert listed == actual == {"model"}, f"allowlist {sorted(listed)} против файлов {sorted(actual)}"


def test_routes_limit_functions_to_the_api():
    """Функции — только на /api/*: без `_routes.json` каждый просмотр витрины
    стоил бы пяти вызовов воркера из общего лимита аккаунта. `exclude: ["/*"]`
    отключает функции ЦЕЛИКОМ (/api/model уходит в статику и отдаёт 404) —
    на этом уже падали, поэтому `exclude` обязан быть пустым."""
    routes = json.loads((WEB / "_routes.json").read_text(encoding="utf-8"))
    assert routes == {"version": 1, "include": ["/api/*"], "exclude": []}


def _csp_from_headers() -> str:
    text = (WEB / "_headers").read_text(encoding="utf-8")
    found = re.findall(r"^\s+Content-Security-Policy: (.+)$", text, re.M)
    assert len(found) == 1, found
    return found[0].strip()


def _csp_from_middleware() -> str:
    src = (FUNCTIONS / "_middleware.js").read_text(encoding="utf-8")
    theme = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    parts = re.search(r"const CSP = \[(.*?)\]\.join\(\"; \"\)", src, re.S).group(1)
    items = re.findall(r'"([^"]+)"|`([^`]+)`', parts)
    return "; ".join((a or b).replace("${THEME_SCRIPT_HASH}", theme) for a, b in items)


def test_csp_is_the_same_in_headers_and_middleware():
    """Статика получает CSP из `web/_headers`, ответы функций — из фильтра.
    Политика одна; правка одной копии без другой — две разные политики."""
    assert _csp_from_headers() == _csp_from_middleware()


def test_csp_has_no_unsafe_inline_scripts():
    script_src = re.search(r"script-src ([^;]+)", _csp_from_headers()).group(1)
    assert "unsafe-inline" not in script_src and "unsafe-eval" not in script_src
    assert re.search(r"'sha256-[A-Za-z0-9+/=]{44}'", script_src), "скрипт темы — только по хэшу"


def test_404_is_a_real_page():
    """Без 404.html Pages на любой мусорный путь отдаёт 200 и главную — сторож
    свежести и поисковик считают панель живой."""
    text = (WEB / "404.html").read_text(encoding="utf-8")
    assert "<html" in text.lower() and "404" in text
    assert text != (WEB / "index.html").read_text(encoding="utf-8")
    assert "Лента" in text, "страница — про эту панель"
    assert not re.search(r"магнит|magnit", text, re.I), "в 404 осталась чужая панель"
    assert "<script" not in text.lower(), "инлайн-скрипт без хэша в CSP не исполнится"


def test_the_edge_carries_no_r2_and_no_foreign_keys():
    """Канал R2 снят: ни биндинга, ни ключа запасной копии чужой панели."""
    for path in [*FUNCTIONS.rglob("*.js"), WEB / "_routes.json", WEB / "_headers",
                 ROOT / "wrangler.toml"]:
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"magnit|850oa", text, re.I), path
        assert not re.search(r"\bR2\b|r2_buckets|env\.DATA|bucket", text), path


def test_the_door_reads_the_release_branch_of_this_repository():
    src = (FUNCTIONS / "api" / "model.js").read_text(encoding="utf-8")
    assert 'PRIMARY = "https://ml371kl.github.io/temp-zero-inode-850-lenta/latest.json"' in src
    assert ('FALLBACK = "https://raw.githubusercontent.com/ML371KL/temp-zero-inode-850-lenta/'
            'release/latest.json"') in src
    assert 'SCHEMA = "lenta-v1"' in src
    from model.payload import SCHEMA

    assert SCHEMA == "lenta-v1", "схема выпуска и схема, которую ждёт дверь, — одна"
