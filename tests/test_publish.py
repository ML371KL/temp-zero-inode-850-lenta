# -*- coding: utf-8 -*-
"""Канал данных: `ops/publish.py` на локальном `git init --bare` вместо GitHub.

Сеть не нужна: «GitHub» — голый репозиторий во временном каталоге, «боевая
дверь» — подменённый `urlopen`. Проверяется поведение, а не строки исходника:
что лежит в ветке после публикации, кто автор коммита, что делается при чужом
push, пустом коммите, отказе push, откате, прореживании и сверке.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import re
import subprocess
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NAME, EMAIL = "ML371KL", "304002195+ML371KL@users.noreply.github.com"


def _load_publish():
    spec = importlib.util.spec_from_file_location("publish_under_test", ROOT / "ops" / "publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


publish = _load_publish()


def _git(*args, cwd=None) -> str:
    done = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _release(value: float, generated_at: str = "2026-09-28T17:30:00+00:00") -> dict:
    payload = {"schema": "lenta-v1",
               "meta": {"generated_at": generated_at, "engine_commit": "abc"},
               "fair_value": {"central": value},
               "changes": {"note": "первый выпуск — сравнивать не с чем"}}
    payload["meta"]["payload_sha256"] = publish.content_digest(payload)
    payload["meta"]["bytes"] = 123
    return payload


def _write_local(cfg, payload: dict) -> str:
    cfg.local_release.parent.mkdir(parents=True, exist_ok=True)
    cfg.local_release.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                                 encoding="utf-8")
    return payload["meta"]["payload_sha256"]


@pytest.fixture
def channel(tmp_path):
    """Голый «GitHub» с веткой release (создана `init`) и настройки публикатора."""
    bare = tmp_path / "github.git"
    _git("init", "--quiet", "--bare", str(bare))
    naps = []
    cfg = publish.Config(state_dir=tmp_path / "state", remote=str(bare), git_name=NAME,
                         git_email=EMAIL, public_url="https://door.test/api/model",
                         verify_timeout=0, verify_pause=0, push_backoff=(0.0,),
                         sleep=naps.append)
    cfg.state_dir.mkdir()
    assert publish.cmd_init(cfg) == 0
    return cfg, bare, naps


def _tree(bare, ref="release") -> list[str]:
    return sorted(_git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", ref).splitlines())


def _show(bare, path, ref="release") -> dict:
    return json.loads(_git("--git-dir", str(bare), "show", f"{ref}:{path}"))


def _commits(bare, ref="release") -> int:
    return int(_git("--git-dir", str(bare), "rev-list", "--count", ref))


# ------------------------------------------------------------ идентичность

@pytest.mark.parametrize("name,email", [("", EMAIL), (NAME, ""),
                                        (NAME, "someone@example.com"),
                                        (NAME, "x@users.noreply.github.com.evil.test")])
def test_publishing_refuses_a_missing_or_personal_identity(channel, name, email, capsys):
    """Без явного noreply-адреса коммита нет: git подставил бы адрес машины или
    личную почту в публичную историю, а удалить их задним числом нельзя."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    cfg.git_name, cfg.git_email = name, email
    before = _commits(bare)
    with pytest.raises(publish.PublishError):
        publish.cmd_publish(cfg)
    assert _commits(bare) == before
    assert not cfg.pushed_mark.exists()


def test_the_command_line_turns_a_refusal_into_code_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LENTA_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("LENTA_GIT_NAME", raising=False)
    monkeypatch.delenv("LENTA_GIT_EMAIL", raising=False)
    assert publish.main([]) == 1
    assert "ПУБЛИКАЦИЯ: не заданы LENTA_GIT_NAME/LENTA_GIT_EMAIL" in capsys.readouterr().err


# --------------------------------------------------------------- публикация

def test_a_publish_puts_the_frozen_release_and_the_pointer_in_one_commit(channel):
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(1000.0))
    assert publish.cmd_publish(cfg) == 0

    assert _tree(bare) == [".nojekyll", "latest.json", f"releases/{digest}.json"]
    changed = _git("--git-dir", str(bare), "show", "--name-only", "--format=", "release").splitlines()
    assert sorted(changed) == ["latest.json", f"releases/{digest}.json"], "один коммит на оба файла"

    latest, frozen = _show(bare, "latest.json"), _show(bare, f"releases/{digest}.json")
    stamp = latest["meta"]["published_at"]
    assert publish.parse_stamp(stamp) is not None and stamp.endswith("+00:00")
    assert frozen == latest, "при первой публикации файл выпуска и указатель совпадают"
    assert latest["meta"]["payload_sha256"] == digest
    assert publish.content_digest(latest) == digest, "published_at в хэш не входит"

    who = _git("--git-dir", str(bare), "log", "-1", "--format=%an|%ae|%cn|%ce", "release")
    assert who == f"{NAME}|{EMAIL}|{NAME}|{EMAIL}"

    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "publish" and mark["payload_sha256"] == digest
    assert mark["published_at"] == stamp
    assert mark["code_commit"] == _git("rev-parse", "HEAD", cwd=ROOT)
    assert mark["release_commit"] == _git("--git-dir", str(bare), "rev-parse", "release")
    assert _git("config", "gc.autoDetach", cwd=cfg.clone) == "false"
    assert not cfg.built_mark.exists(), "release.commit пишет только сверка"


def test_a_republished_release_keeps_its_file_and_moves_the_pointer(channel, monkeypatch):
    """Те же входы — тот же файл выпуска; указатель получает новый момент."""
    cfg, bare, _ = channel
    digest = _write_local(cfg, _release(1000.0))
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-29T17:31:00+00:00")
    publish.cmd_publish(cfg)
    assert _show(bare, f"releases/{digest}.json")["meta"]["published_at"] == "2026-09-28T17:31:00+00:00"
    assert _show(bare, "latest.json")["meta"]["published_at"] == "2026-09-29T17:31:00+00:00"
    assert _commits(bare) == 3


def test_publishing_the_same_moment_twice_makes_no_empty_commit(channel, monkeypatch, capsys):
    """Повтор после удачного коммита не падает на «nothing to commit»."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    assert publish.cmd_publish(cfg) == 0
    before = _commits(bare)
    assert publish.cmd_publish(cfg) == 0
    assert _commits(bare) == before
    assert "коммит не нужен" in capsys.readouterr().out


def test_a_foreign_push_does_not_block_the_next_publish(channel, tmp_path):
    """Откат с ноутбука или чужой push: клон одноразовый, non-fast-forward не бывает.
    Заодно посторонние пути и `.github/` из ветки данных снимаются."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)

    laptop = tmp_path / "laptop"
    _git("clone", "--quiet", "--branch", "release", str(bare), str(laptop))
    (laptop / ".github" / "workflows").mkdir(parents=True)
    (laptop / ".github" / "workflows" / "x.yml").write_text("on: push\n", encoding="utf-8")
    (laptop / "notes.txt").write_text("заметка\n", encoding="utf-8")
    (laptop / "latest.json").write_text('{"schema": "lenta-v1", "meta": {}}\n', encoding="utf-8")
    _git("add", "-A", cwd=laptop)
    _git("-c", "user.name=t", "-c", f"user.email={EMAIL}", "commit", "-q", "-m", "чужое", cwd=laptop)
    _git("push", "-q", "origin", "release", cwd=laptop)

    digest = _write_local(cfg, _release(1100.0))
    assert publish.cmd_publish(cfg) == 0
    tree = _tree(bare)
    assert ".github/workflows/x.yml" not in tree and "notes.txt" not in tree
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest
    assert all(p in (".nojekyll", "latest.json") or re.fullmatch(r"releases/[0-9a-f]{64}\.json", p)
               for p in tree), tree


def test_a_failed_push_is_retried_from_a_clean_clone(channel, tmp_path):
    cfg, bare, naps = channel
    digest = _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)          # клон уже есть
    hidden = tmp_path / "away.git"
    bare.rename(hidden)

    def come_back(seconds):
        naps.append(seconds)
        if hidden.exists():
            hidden.rename(bare)

    cfg.sleep = come_back
    digest = _write_local(cfg, _release(1200.0))
    assert publish.cmd_publish(cfg) == 0
    assert naps == [0.0], "одна пауза между первой и второй попыткой"
    assert _show(bare, "latest.json")["meta"]["payload_sha256"] == digest


def test_three_failed_attempts_leave_no_mark(channel, tmp_path):
    cfg, bare, naps = channel
    _write_local(cfg, _release(1000.0))
    bare.rename(tmp_path / "gone.git")
    with pytest.raises(publish.PublishError, match="3 попытки не удались"):
        publish.cmd_publish(cfg)
    assert len(naps) == 2
    assert not cfg.pushed_mark.exists()


@pytest.mark.parametrize("damage,reason", [
    (lambda text: text.replace('"central": 1000.0', '"central": 1001.0'), "не совпадает со своим хэшем"),
    (lambda text: text.replace('"central": 1000.0', '"central": NaN'), "не строгий JSON"),
    (lambda text: text.replace('"lenta-v1"', '"magnit-v5.1"'), "схема"),
])
def test_a_damaged_local_release_is_not_published(channel, damage, reason):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    text = cfg.local_release.read_text(encoding="utf-8")
    cfg.local_release.write_text(damage(text), encoding="utf-8")
    with pytest.raises(publish.PublishError, match=reason):
        publish.cmd_publish(cfg)
    assert _tree(bare) == [".nojekyll"]


def test_the_digest_matches_the_engine_and_ignores_the_moment_of_publication():
    from model.payload import content_digest as engine_digest

    payload = _release(1000.0)
    assert publish.content_digest(payload) == engine_digest(payload)
    stamped = dict(payload, meta=dict(payload["meta"], published_at="2026-09-28T17:31:00+00:00"))
    assert publish.content_digest(stamped) == publish.content_digest(payload)
    changed = dict(payload, fair_value={"central": 1000.5})
    assert publish.content_digest(changed) != publish.content_digest(payload)


def test_init_creates_an_orphan_branch_once(tmp_path):
    bare = tmp_path / "empty.git"
    _git("init", "--quiet", "--bare", str(bare))
    cfg = publish.Config(state_dir=tmp_path / "s", remote=str(bare), git_name=NAME, git_email=EMAIL)
    assert publish.cmd_init(cfg) == 0
    assert _tree(bare) == [".nojekyll"] and _commits(bare) == 1
    assert publish.cmd_init(cfg) == 0 and _commits(bare) == 1, "второй init ничего не делает"


# ------------------------------------------------------------------ сверка

class _Door:
    """Подменённая боевая дверь: отдаёт по очереди заказанные ответы."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        answer = self.answers[min(self.calls, len(self.answers)) - 1]
        if callable(answer):
            answer = answer()          # новое исключение на каждый вызов: тело читается один раз
        if isinstance(answer, Exception):
            raise answer
        body, headers = answer

        class Response:
            def __enter__(self_):
                return self_

            def __exit__(self_, *exc):
                return False

            def read(self_):
                return body.encode("utf-8")

        response = Response()
        response.headers = headers
        return response


def _served(bare, source="pages", **extra_headers):
    body = _git("--git-dir", str(bare), "show", "release:latest.json")
    headers = {"Content-Type": "application/json; charset=utf-8",
               "Last-Modified": "Mon, 28 Sep 2026 17:31:00 GMT", "x-data-source": source}
    headers.update(extra_headers)
    return body, {k: v for k, v in headers.items() if v is not None}


def test_verify_confirms_the_pushed_release_and_remembers_its_code(channel, monkeypatch):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    want_code = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))["code_commit"]
    door = _Door(_served(bare))
    monkeypatch.setattr(publish.urllib.request, "urlopen", door)
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.read_text(encoding="utf-8").strip() == want_code
    assert not cfg.pushed_mark.exists()
    assert publish.cmd_verify(cfg) == 0, "повторная сверка без публикации — нечего сверять"


def test_verify_waits_for_the_new_moment_not_just_the_same_content(channel, monkeypatch, capsys):
    """Пересборка на тех же входах даёт тот же хэш: без `published_at` сверка
    прошла бы сразу, даже если новый коммит до Pages не доехал."""
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-28T17:31:00+00:00")
    publish.cmd_publish(cfg)
    old = _served(bare)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-29T17:31:00+00:00")
    publish.cmd_publish(cfg)
    new = _served(bare)

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(old))
    assert publish.cmd_verify(cfg) == 3
    err = capsys.readouterr().err
    assert "ту же сборку прошлой публикации" in err and "дожмут следующие такты" in err
    assert cfg.pushed_mark.exists() and not cfg.built_mark.exists()

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(new))
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.exists() and not cfg.pushed_mark.exists()


def test_verify_times_out_with_code_three_and_keeps_the_mark(channel, monkeypatch, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    cfg.verify_timeout, cfg.verify_pause = 0.3, 0.1
    napped = []
    cfg.sleep = napped.append
    def not_yet():
        return urllib.error.HTTPError(cfg.public_url, 503, "Service Unavailable", {},
                                      io.BytesIO(b'{"error":"upstream unavailable"}'))

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(not_yet))
    assert publish.cmd_verify(cfg) == 3
    err = capsys.readouterr().err
    assert "дверь ответила 503" in err and "upstream unavailable" in err
    assert cfg.pushed_mark.exists() and not cfg.built_mark.exists()
    assert napped and all(n <= 0.1 for n in napped)


def test_verify_once_is_a_single_request(channel, monkeypatch):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    cfg.verify_timeout = 600
    door = _Door(urllib.error.URLError("нет сети"))
    monkeypatch.setattr(publish.urllib.request, "urlopen", door)
    assert publish.cmd_verify(cfg, once=True) == 3
    assert door.calls == 1


@pytest.mark.parametrize("source", ["raw", "edge-cache"])
def test_a_release_served_by_the_fallback_counts_but_raises_the_alarm(channel, monkeypatch, capsys, source):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare, source)))
    assert publish.cmd_verify(cfg) == 3
    assert cfg.built_mark.exists() and not cfg.pushed_mark.exists()
    assert f"запасной источник «{source}»" in capsys.readouterr().err


@pytest.mark.parametrize("headers,reason", [
    ({"Last-Modified": None}, "Last-Modified"),
    ({"Content-Type": "text/html"}, "content-type"),
])
def test_verify_demands_the_headers_the_watchman_needs(channel, monkeypatch, capsys, headers, reason):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare, **headers)))
    assert publish.cmd_verify(cfg) == 3
    assert reason in capsys.readouterr().err


def test_verify_reads_as_strictly_as_the_browser(channel, monkeypatch, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    body, headers = _served(bare)
    monkeypatch.setattr(publish.urllib.request, "urlopen",
                        _Door((body.replace('"central": 1000.0', '"central": NaN'), headers)))
    assert publish.cmd_verify(cfg) == 3
    assert "строгий JSON" in capsys.readouterr().err
    with pytest.raises(ValueError):
        publish.strict_json('{"a": -Infinity}')


def test_the_verify_window_outlasts_the_edge_cache():
    source = (ROOT / "functions" / "api" / "model.js").read_text(encoding="utf-8")
    cache_seconds = int(re.search(r"CACHE_SECONDS\s*=\s*(\d+)", source).group(1))
    assert publish.VERIFY_TIMEOUT >= 5 * cache_seconds
    assert publish.VERIFY_PAUSE < cache_seconds


def test_outgoing_http_headers_are_latin1():
    """Кириллица в User-Agent роняет запрос ещё до отправки — проверено боем."""
    for name, value in publish.HEADERS.items():
        name.encode("latin-1")
        value.encode("latin-1")


# ------------------------------------------------------------------- откат

def test_rollback_repoints_with_a_new_moment_and_leaves_the_code_mark(channel, monkeypatch):
    cfg, bare, naps = channel
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-01T17:31:00+00:00")
    good = _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-02T17:31:00+00:00")
    _write_local(cfg, _release(1500.0))
    publish.cmd_publish(cfg)
    cfg.pushed_mark.unlink()
    cfg.built_mark.write_text("код-бага\n", encoding="utf-8")

    monkeypatch.setattr(publish, "now_stamp", lambda: "2026-09-03T08:00:00+00:00")
    assert publish.cmd_rollback(cfg, good[:12]) == 0
    latest = _show(bare, "latest.json")
    assert latest["meta"]["payload_sha256"] == good
    assert latest["meta"]["published_at"] == "2026-09-03T08:00:00+00:00", "у отката — новый момент"
    assert latest["fair_value"] == {"central": 1000.0}
    mark = json.loads(cfg.pushed_mark.read_text(encoding="utf-8"))
    assert mark["kind"] == "rollback" and mark["payload_sha256"] == good

    monkeypatch.setattr(publish.urllib.request, "urlopen", _Door(_served(bare)))
    assert publish.cmd_verify(cfg) == 0
    assert cfg.built_mark.read_text(encoding="utf-8") == "код-бага\n", (
        "откат не трогает release.commit: иначе пересборка тут же вернула бы откатываемое")


def test_rollback_to_an_unknown_release_is_refused_without_retries(channel):
    cfg, bare, naps = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    with pytest.raises(publish.PublishError, match="не найден"):
        publish.cmd_rollback(cfg, "0" * 12)
    with pytest.raises(publish.PublishError, match="от 12"):
        publish.cmd_rollback(cfg, "abc")
    assert naps == []


# ------------------------------------------------------------- прореживание

def _dated(stamp: str, value: float) -> dict:
    payload = _release(value, generated_at=stamp)
    payload["meta"]["published_at"] = stamp
    return payload


def test_prune_keeps_ninety_days_then_mondays_by_the_date_inside(channel, monkeypatch):
    """Дата — из содержания, не из mtime: после переклонирования mtime у всех
    файлов одинаковый, а правило обязано значить то же самое."""
    cfg, bare, _ = channel
    today = date(2026, 12, 31)
    old_monday = "2026-08-31T17:30:00+00:00"          # понедельник, 122 дня назад
    old_tuesday = "2026-09-01T17:30:00+00:00"         # вторник, 121 день назад — под нож
    recent = "2026-12-01T17:30:00+00:00"
    pointed_old = "2026-08-26T17:30:00+00:00"         # среда, но на неё смотрит указатель
    clone = publish.sync(cfg)
    shas = {}
    for label, stamp, value in (("monday", old_monday, 1.0), ("tuesday", old_tuesday, 2.0),
                                ("recent", recent, 3.0), ("pointed", pointed_old, 4.0)):
        payload = _dated(stamp, value)
        shas[label] = payload["meta"]["payload_sha256"]
        publish._write_atomic(clone / "releases" / f"{shas[label]}.json", publish.dump_release(payload))
    (clone / "releases" / ("f" * 64 + ".json")).write_text('{"meta": {}}', encoding="utf-8")
    publish._write_atomic(clone / "latest.json", publish.dump_release(_dated(pointed_old, 4.0)))
    _git("add", "-A", cwd=clone)
    _git("-c", f"user.name={NAME}", "-c", f"user.email={EMAIL}", "commit", "-q", "-m", "засев", cwd=clone)
    _git("push", "-q", "origin", "HEAD:refs/heads/release", cwd=clone)

    local = cfg.local_release.parent
    local.mkdir(parents=True)
    for label in ("tuesday", "recent"):
        stamp = old_tuesday if label == "tuesday" else recent
        (local / f"{shas[label]}.json").write_text(json.dumps(_dated(stamp, 0)), encoding="utf-8")
    (local / "latest.json").write_text(json.dumps(_dated(recent, 3.0)), encoding="utf-8")

    assert publish.cmd_prune(cfg, today=today) == 0
    tree = _tree(bare)
    assert f"releases/{shas['tuesday']}.json" not in tree
    for label in ("monday", "recent", "pointed"):
        assert f"releases/{shas[label]}.json" in tree, label
    assert f"releases/{'f' * 64}.json" in tree, "без даты в содержании — не трогать"
    assert "latest.json" in tree
    assert sorted(p.name for p in local.glob("*.json")) == sorted(
        ["latest.json", f"{shas['recent']}.json"])


def test_prune_waits_for_a_confirmed_verify(channel, capsys):
    cfg, bare, _ = channel
    _write_local(cfg, _release(1000.0))
    publish.cmd_publish(cfg)
    before = _commits(bare)
    assert publish.cmd_prune(cfg, today=date(2027, 12, 31)) == 0
    assert _commits(bare) == before
    assert "отложено" in capsys.readouterr().out


def test_state_marks_follow_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("LENTA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("LENTA_PUSH_BACKOFF", "0")
    cfg = publish.Config.from_env()
    for path in (cfg.local_release, cfg.clone, cfg.pushed_mark, cfg.built_mark):
        assert str(path).startswith(str(tmp_path)), path
    assert cfg.push_backoff == (0.0,)
    assert cfg.public_url == publish.DEFAULT_PUBLIC_URL
