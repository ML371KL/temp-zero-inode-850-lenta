"""Публикация выпуска в ветку `release` и сверка того, что увидит браузер.

    python ops/publish.py                    опубликовать собранный выпуск
    python ops/publish.py --verify           сверка через боевую дверь (ждёт до VERIFY_TIMEOUT)
    python ops/publish.py --verify --once    одна попытка: дожим сверки прошлого такта
    python ops/publish.py --prune            проредить старые выпуски (только после сверки)
    python ops/publish.py rollback <sha>     вернуть на витрину прошлый выпуск
    python ops/publish.py init               создать осиротевшую ветку `release` (один раз)

КАНАЛ (DESIGN §2). Сборка на сервере → этот скрипт в одноразовом клоне ветки
`release` (`$LENTA_STATE_DIR/release-repo`) → GitHub Pages этой ветки →
Pages Function `functions/api/model.js` на своём адресе → браузер. В ветке
живут только `.nojekyll`, `latest.json` и `releases/<sha256>.json`; `.github/`
там не бывает никогда — иначе push данных будил бы workflow из самой ветки.

АДРЕСАЦИЯ СОДЕРЖИМЫМ. `releases/<sha256>.json` пишется один раз и не
переписывается; `latest.json` — указатель. Хэш содержания считается без
`meta.generated_at`, `meta.payload_sha256`, `meta.bytes`, `meta.published_at` и
без блока `changes` (правила 850oa, `model/payload.py::content_digest`), поэтому
повтор сборки на тех же входах даёт то же имя файла.

`meta.published_at` — момент ПУБЛИКАЦИИ (UTC, ISO, секунды). Вписывается при
каждой публикации и при каждом откате и в хэш не входит. Из него функция
ставит `Last-Modified` (по нему живёт сторож `dash-watch`) и `ETag`; сверка
ждёт совпадения и хэша, и `published_at` — иначе пересборка на тех же входах
«сверялась» бы мгновенно, даже если новый коммит до Pages не доехал.

КЛОН ОДНОРАЗОВЫЙ. В начале каждой записи — `fetch` и `reset --hard
origin/release` (+ `clean`): откат с ноутбука или чужой push не превращают
каждый следующий такт в non-fast-forward. Файлы пишутся заново из входов,
коммит — только если `git diff --cached --quiet` видит разницу, push — с
повтором (три попытки, пауза растёт), каждая попытка начинается с чистого
клона. `gc.autoDetach=false`: systemd убивает фоновый `git gc` в конце юнита
и оставляет `*.lock`, на котором падает следующий коммит.

ИДЕНТИЧНОСТЬ КОММИТА — только из окружения (`LENTA_GIT_NAME`,
`LENTA_GIT_EMAIL`) и только адрес `…@users.noreply.github.com`: репозиторий
публичный, метаданные коммита читает любой, и удалить их задним числом нельзя.
Пустое значение или другой адрес — отказ (код 1).

ОТМЕТКИ в `$LENTA_STATE_DIR`:
    release.pushed   push прошёл, сверки ещё не было (JSON: хэш, published_at,
                     коммит кода, коммит ветки, вид — publish|rollback)
    release.commit   коммит кода, чей выпуск ПОДТВЕРЖДЁН сверкой (по нему
                     `run.sh rebuild` решает, есть ли что пересобирать);
                     пишется только после успешной сверки; откат его не трогает

КОДЫ: 0 — сделано; 1 — не сделано (выпуска в ветке нет или он не тот);
3 — только у сверки: выпуск в ветке, но дверь его не отдала за отведённое
время (или отдаёт запасным источником) — тревога такта, а не остановка;
следующие такты дожимают сверку (`--verify --once`).

Окружение (`/usr/local/etc/lenta-850/env`):
    LENTA_STATE_DIR        каталог состояния (по умолчанию var/ репозитория)
    LENTA_RELEASE_REMOTE   адрес репозитория для клона ветки release
                           (на сервере — ssh-алиас deploy-ключа, например
                           gh-850-lenta:ML371KL/temp-zero-inode-850-lenta.git)
    LENTA_GIT_NAME         имя автора коммитов ветки release
    LENTA_GIT_EMAIL        адрес автора (только noreply GitHub)
    LENTA_PUBLIC_URL       боевая дверь (https://tzi-850-lenta.pages.dev/api/model)
    LENTA_VERIFY_TIMEOUT   сколько ждать сверку, с (600)
    LENTA_VERIFY_PAUSE     пауза между попытками сверки, с (30)
    LENTA_PUSH_BACKOFF     паузы между попытками push, с (10,30)
"""

# Без `from __future__ import annotations`: dataclass ниже с отложенными
# аннотациями не грузится через importlib.util.spec_from_file_location (так
# модуль загружают тесты), а Python 3.12 понимает `X | None` и сам.
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Локальный выпуск, который собрал `ops/build_release.py`. Путь следует за
# LENTA_STATE_DIR так же, как у сборки (сверяет test_state_paths_follow_state_directory).
RELEASE = ((Path(os.environ["LENTA_STATE_DIR"]) / "release" / "latest.json")
           if os.environ.get("LENTA_STATE_DIR")
           else ROOT / "var" / "release" / "latest.json")

SCHEMA = "lenta-v1"
BRANCH = "release"
POINTER = "latest.json"
RELEASES_DIR = "releases"
NOJEKYLL = ".nojekyll"
DEFAULT_PUBLIC_URL = "https://tzi-850-lenta.pages.dev/api/model"
NOREPLY_SUFFIX = "@users.noreply.github.com"

# Поля meta, которые в содержание выпуска не входят (см. докстроку модуля).
NOT_CONTENT_META = ("generated_at", "payload_sha256", "bytes", "published_at")

KEEP_DAYS = 90          # моложе — хранить все выпуски; старше — только понедельничные
PUSH_ATTEMPTS = 3
# Сверка ждёт сборку GitHub Pages (обычно 1–3 минуты, в инциденты Actions —
# десятки) и кэш края в 60 с. Окно обязано перекрывать кэш функции
# (CACHE_SECONDS в functions/api/model.js) — сверяет тест.
VERIFY_TIMEOUT = 600
VERIFY_PAUSE = 30

# Заголовки HTTP кодируются latin-1: кириллица в User-Agent роняет запрос
# UnicodeEncodeError ещё до отправки (стоило первого боевого прогона 850oa:
# данные легли верно, а сверка упала). Только ASCII. Без своего UA край
# Cloudflare отвечает 403.
HEADERS = {"accept": "application/json", "user-agent": "lenta-850-publish/1.0"}

SHA_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_PATH_RE = re.compile(r"^releases/([0-9a-f]{64})\.json$")


class PublishError(RuntimeError):
    """Отказ, после которого выпуска в ветке нет (или он не тот): код 1."""


# ------------------------------------------------------------------ настройки

@dataclass
class Config:
    state_dir: Path
    remote: str = ""
    git_name: str = ""
    git_email: str = ""
    public_url: str = DEFAULT_PUBLIC_URL
    verify_timeout: float = VERIFY_TIMEOUT
    verify_pause: float = VERIFY_PAUSE
    push_backoff: tuple[float, ...] = (10.0, 30.0)
    code_root: Path = ROOT
    release_file: Path | None = None
    sleep: object = field(default=time.sleep, repr=False)

    @property
    def local_release(self) -> Path:
        return self.release_file or self.state_dir / "release" / POINTER

    @property
    def clone(self) -> Path:
        return self.state_dir / "release-repo"

    @property
    def pushed_mark(self) -> Path:
        return self.state_dir / "release.pushed"

    @property
    def built_mark(self) -> Path:
        return self.state_dir / "release.commit"

    @classmethod
    def from_env(cls, env=None) -> "Config":
        env = os.environ if env is None else env
        state = Path(env["LENTA_STATE_DIR"]) if env.get("LENTA_STATE_DIR") else ROOT / "var"
        backoff = tuple(float(x) for x in
                        (env.get("LENTA_PUSH_BACKOFF") or "10,30").split(",") if x.strip())
        return cls(state_dir=state,
                   remote=env.get("LENTA_RELEASE_REMOTE", "").strip(),
                   git_name=env.get("LENTA_GIT_NAME", "").strip(),
                   git_email=env.get("LENTA_GIT_EMAIL", "").strip(),
                   public_url=env.get("LENTA_PUBLIC_URL", "").strip() or DEFAULT_PUBLIC_URL,
                   verify_timeout=float(env.get("LENTA_VERIFY_TIMEOUT") or VERIFY_TIMEOUT),
                   verify_pause=float(env.get("LENTA_VERIFY_PAUSE") or VERIFY_PAUSE),
                   push_backoff=backoff or (0.0,))


def identity(cfg: Config) -> tuple[str, str]:
    """Имя и адрес автора коммитов ветки release — из окружения, иначе отказ."""
    if not cfg.git_name or not cfg.git_email:
        raise PublishError(
            "не заданы LENTA_GIT_NAME/LENTA_GIT_EMAIL: без явной идентичности git "
            "подставил бы адрес машины или личную почту в публичную историю")
    if not cfg.git_email.endswith(NOREPLY_SUFFIX) or any(c.isspace() for c in cfg.git_email):
        raise PublishError(
            f"LENTA_GIT_EMAIL должен быть адресом …{NOREPLY_SUFFIX}: репозиторий "
            "публичный, и адрес автора коммита виден всем навсегда")
    return cfg.git_name, cfg.git_email


# ------------------------------------------------------------ JSON и хэш

def _refuse_constant(token: str):
    raise ValueError(f"{token} — не строгий JSON: витрина такой ответ не разберёт")


def strict_json(text: str):
    """Разбор, как у `JSON.parse` браузера: NaN и ±Infinity — ошибка."""
    return json.loads(text, parse_constant=_refuse_constant)


def dump_release(payload: dict) -> str:
    """Тот же вид, что у `ops/build_release.py`: строгий JSON, UTF-8, отступ 1."""
    return json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False) + "\n"


def content_digest(payload: dict) -> str:
    """sha256 СОДЕРЖАНИЯ выпуска — зеркало `model.payload.content_digest`,
    дополнительно без `published_at` (его вписывает эта публикация). Равенство
    с ядром на выпуске без `published_at` сверяет тест."""
    body = json.dumps(
        {k: v for k, v in payload.items() if k not in ("meta", "changes")}
        | {"meta": {k: v for k, v in payload["meta"].items() if k not in NOT_CONTENT_META}},
        ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def now_stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_stamp(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def load_local_release(cfg: Config) -> dict:
    """Собранный выпуск — строго, со своей схемой и с честным хэшем."""
    path = cfg.local_release
    if not path.exists():
        raise PublishError(f"нет выпуска {path} — сначала ops/build_release.py")
    try:
        payload = strict_json(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PublishError(f"выпуск {path} не публикуется: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise PublishError(f"выпуск {path}: схема {payload.get('schema') if isinstance(payload, dict) else '?'!r}, "
                           f"ожидается {SCHEMA!r}")
    meta = payload.get("meta")
    digest = meta.get("payload_sha256") if isinstance(meta, dict) else None
    if not isinstance(digest, str) or not SHA_RE.match(digest):
        raise PublishError(f"выпуск {path}: нет meta.payload_sha256")
    actual = content_digest(payload)
    if actual != digest:
        raise PublishError(f"выпуск {path}: содержимое не совпадает со своим хэшем "
                           f"({actual[:12]} против {digest[:12]}) — файл правили после сборки")
    return payload


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def read_mark(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        mark = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise PublishError(f"отметка {path.name} не читается: {exc}") from exc
    if not isinstance(mark, dict) or not SHA_RE.match(str(mark.get("payload_sha256", ""))) \
            or parse_stamp(mark.get("published_at")) is None:
        raise PublishError(f"отметка {path.name} испорчена: {mark!r}"[:300])
    return mark


# ------------------------------------------------------------------- git

def _git_env() -> dict:
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    # Под systemd терминала нет: вопрос ssh про ключ хоста или пароль превратил
    # бы такт в зависание до RuntimeMaxSec. Алиас и ключ ssh берёт из ~/.ssh/config.
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes -o ConnectTimeout=30")
    return env


def git(*args: str, cwd: Path | None = None, check: bool = True,
        timeout: float = 300) -> subprocess.CompletedProcess:
    cmd = ["git", *args]
    try:
        done = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=_git_env(), timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise PublishError(f"git {args[0]}: не ответил за {timeout:.0f} с") from exc
    if check and done.returncode != 0:
        raise PublishError(f"git {' '.join(args[:2])} → код {done.returncode}: "
                           f"{(done.stderr or done.stdout).strip()[:600]}")
    return done


def ensure_clone(cfg: Config) -> Path:
    """Клон ветки release в состоянии; создаётся при первом обращении."""
    clone = cfg.clone
    if not (clone / ".git").exists():
        if not cfg.remote:
            raise PublishError("клона ветки release нет, а LENTA_RELEASE_REMOTE не задан")
        if clone.exists():
            shutil.rmtree(clone)
        clone.parent.mkdir(parents=True, exist_ok=True)
        done = git("clone", "--quiet", "--single-branch", "--branch", BRANCH, "--no-tags",
                   cfg.remote, str(clone), check=False)
        if done.returncode != 0:
            raise PublishError(
                f"клон ветки {BRANCH} не удался (код {done.returncode}): "
                f"{done.stderr.strip()[:400]}. Если ветки ещё нет — `python ops/publish.py init`")
        print(f"  клон ветки {BRANCH} создан: {clone}")
    elif cfg.remote:
        current = git("remote", "get-url", "origin", cwd=clone, check=False).stdout.strip()
        if current != cfg.remote:
            git("remote", "set-url", "origin", cfg.remote, cwd=clone)
    # Каждый раз, а не только при клоне: клон мог создать кто-то руками.
    git("config", "gc.autoDetach", "false", cwd=clone)
    git("config", "core.autocrlf", "false", cwd=clone)
    return clone


def sync(cfg: Config) -> Path:
    """Клон = origin/release, без локальных хвостов прошлых попыток."""
    clone = ensure_clone(cfg)
    git("fetch", "--quiet", "--no-tags", "origin",
        f"+refs/heads/{BRANCH}:refs/remotes/origin/{BRANCH}", cwd=clone)
    git("checkout", "--quiet", "-B", BRANCH, f"origin/{BRANCH}", cwd=clone)
    git("reset", "--quiet", "--hard", f"origin/{BRANCH}", cwd=clone)
    git("clean", "-ffdxq", cwd=clone)
    return clone


def tidy_tree(clone: Path) -> list[str]:
    """В ветке — только `.nojekyll`, `latest.json` и `releases/<sha256>.json`.

    Всё прочее снимается с громкой строкой: `.github/` в ветке данных будил бы
    workflow при каждом push выпуска, а посторонний файл на GitHub Pages — это
    публикация, которую никто не заказывал.
    """
    tracked = git("ls-files", "-z", cwd=clone).stdout.split("\0")
    removed = []
    for path in filter(None, tracked):
        if path in (NOJEKYLL, POINTER) or RELEASE_PATH_RE.match(path):
            continue
        git("rm", "--quiet", "-r", "--", path, cwd=clone)
        removed.append(path)
        kind = "workflow в ветке данных" if path.startswith(".github/") else "посторонний путь"
        print(f"  ВНИМАНИЕ: в ветке {BRANCH} {kind} {path} — убран", file=sys.stderr)
    nojekyll = clone / NOJEKYLL
    if not nojekyll.exists():
        nojekyll.write_text("", encoding="utf-8")
    git("add", "--", NOJEKYLL, cwd=clone)
    return removed


def commit_if_changed(cfg: Config, clone: Path, message: str) -> bool:
    if git("diff", "--cached", "--quiet", cwd=clone, check=False).returncode == 0:
        return False
    name, email = identity(cfg)
    git("-c", f"user.name={name}", "-c", f"user.email={email}",
        "commit", "--quiet", "--no-verify", "-m", message, cwd=clone)
    return True


def push(clone: Path) -> None:
    git("push", "--quiet", "origin", f"HEAD:refs/heads/{BRANCH}", cwd=clone)


class _Fatal(PublishError):
    """Отказ, который повтором не лечится (нет такого выпуска)."""


def with_retries(cfg: Config, what: str, attempt_fn):
    """Попытка = чистый клон → запись → коммит → push. Три попытки, пауза растёт."""
    last = None
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            return attempt_fn()
        except _Fatal:
            raise
        except PublishError as exc:
            last = exc
            if attempt == PUSH_ATTEMPTS:
                break
            pause = cfg.push_backoff[min(attempt - 1, len(cfg.push_backoff) - 1)]
            print(f"  {what}: попытка {attempt} не удалась ({exc}); повтор через {pause:.0f} с",
                  file=sys.stderr)
            cfg.sleep(pause)
    raise PublishError(f"{what}: {PUSH_ATTEMPTS} попытки не удались — {last}")


def code_commit(cfg: Config) -> str:
    done = git("rev-parse", "HEAD", cwd=cfg.code_root, check=False)
    return done.stdout.strip() if done.returncode == 0 else ""


def write_pushed_mark(cfg: Config, *, kind: str, digest: str, published_at: str,
                      clone: Path) -> None:
    mark = dict(kind=kind, payload_sha256=digest, published_at=published_at,
                code_commit=code_commit(cfg) if kind == "publish" else "",
                release_commit=git("rev-parse", "HEAD", cwd=clone).stdout.strip(),
                pushed_at=now_stamp())
    _write_atomic(cfg.pushed_mark, json.dumps(mark, ensure_ascii=False, indent=1) + "\n")


# --------------------------------------------------------------- публикация

def cmd_publish(cfg: Config) -> int:
    identity(cfg)
    payload = load_local_release(cfg)
    digest = payload["meta"]["payload_sha256"]
    published_at = now_stamp()
    print(f"выпуск {digest[:12]} · published_at {published_at} · ветка {BRANCH}")

    def attempt():
        clone = sync(cfg)
        tidy_tree(clone)
        # Сначала неизменяемый файл, потом указатель — в одном коммите: ветка
        # никогда не показывает указатель на файл, которого в ней нет.
        frozen = clone / RELEASES_DIR / f"{digest}.json"
        if frozen.exists():
            print(f"  releases/{digest[:12]}….json уже в ветке — не переписываю")
        else:
            first = dict(payload, meta=dict(payload["meta"], published_at=published_at))
            _write_atomic(frozen, dump_release(first))
        latest = dict(payload, meta=dict(payload["meta"], published_at=published_at))
        _write_atomic(clone / POINTER, dump_release(latest))
        git("add", "--", f"{RELEASES_DIR}/{digest}.json", POINTER, cwd=clone)
        if commit_if_changed(cfg, clone, f"выпуск {digest[:12]} · {published_at}"):
            push(clone)
            print(f"  отправлено в {BRANCH}: {git('rev-parse', '--short', 'HEAD', cwd=clone).stdout.strip()}")
        else:
            print(f"  ветка {BRANCH} уже содержит ровно это — коммит не нужен")
        return clone

    clone = with_retries(cfg, "публикация", attempt)
    write_pushed_mark(cfg, kind="publish", digest=digest, published_at=published_at, clone=clone)
    print(f"  отметка release.pushed: {digest[:12]} · {published_at}")
    return 0


def cmd_rollback(cfg: Config, prefix: str) -> int:
    """Откат — перезапись указателя прошлым выпуском с НОВЫМ `published_at`.

    Новый момент публикации нужен сторожу: со старым `Last-Modified` откат на
    выпуск старше 74 ч сразу давал бы «данные перестали обновляться».
    `release.commit` откат не трогает: иначе ближайшая пересборка увидела бы
    «код изменился» и тут же вернула бы то, от чего откатывались.
    """
    identity(cfg)
    prefix = (prefix or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{12,64}", prefix):
        raise PublishError(f"откат: ожидается хэш выпуска (от 12 шестнадцатеричных знаков), получено {prefix!r}")
    published_at = now_stamp()
    found = {}

    def attempt():
        clone = sync(cfg)
        tidy_tree(clone)
        matches = sorted(p for p in (clone / RELEASES_DIR).glob("*.json")
                         if p.stem.startswith(prefix) and SHA_RE.match(p.stem))
        if len(matches) != 1:
            names = ", ".join(p.stem[:12] for p in matches) or "нет"
            raise _Fatal(f"откат: выпуск {prefix[:12]} в ветке не найден однозначно (совпадения: {names})")
        source = strict_json(matches[0].read_text(encoding="utf-8"))
        digest = matches[0].stem
        if source.get("schema") != SCHEMA or content_digest(source) != digest:
            raise _Fatal(f"откат: releases/{digest[:12]}….json испорчен (схема или хэш)")
        latest = dict(source, meta=dict(source["meta"], published_at=published_at))
        _write_atomic(clone / POINTER, dump_release(latest))
        git("add", "--", POINTER, cwd=clone)
        if commit_if_changed(cfg, clone, f"откат на {digest[:12]} · {published_at}"):
            push(clone)
        found["digest"] = digest
        return clone

    try:
        clone = with_retries(cfg, "откат", attempt)
    except _Fatal as exc:
        raise PublishError(str(exc)) from None
    write_pushed_mark(cfg, kind="rollback", digest=found["digest"], published_at=published_at,
                      clone=clone)
    print(f"откат: указатель смотрит на {found['digest'][:12]} · published_at {published_at}")
    return 0


def cmd_init(cfg: Config) -> int:
    """Осиротевшая ветка `release` с одним `.nojekyll` — если её ещё нет."""
    name, email = identity(cfg)
    if not cfg.remote:
        raise PublishError("init: LENTA_RELEASE_REMOTE не задан")
    heads = git("ls-remote", "--heads", cfg.remote, BRANCH).stdout
    if f"refs/heads/{BRANCH}" in heads:
        print(f"ветка {BRANCH} уже есть — создавать нечего")
        return 0
    work = cfg.state_dir / "release-init"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        git("init", "--quiet", "-b", BRANCH, str(work))
        git("config", "core.autocrlf", "false", cwd=work)
        (work / NOJEKYLL).write_text("", encoding="utf-8")
        git("add", "--", NOJEKYLL, cwd=work)
        git("-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "--quiet",
            "--no-verify", "-m", "ветка данных: только выпуски для GitHub Pages", cwd=work)
        git("push", "--quiet", cfg.remote, f"{BRANCH}:refs/heads/{BRANCH}", cwd=work)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print(f"ветка {BRANCH} создана: один коммит с {NOJEKYLL}")
    return 0


# ------------------------------------------------------------------ сверка

def _fetch_door(cfg: Config):
    request = urllib.request.Request(cfg.public_url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return (strict_json(response.read().decode("utf-8")),
                {k.lower(): v for k, v in response.headers.items()})


def _served_problems(served, headers: dict, want_sha: str, want_pub: str) -> list[str]:
    meta = served.get("meta", {}) if isinstance(served, dict) else {}
    problems = []
    got_sha = str(meta.get("payload_sha256", "—"))
    got_pub = str(meta.get("published_at", "—"))
    if got_sha != want_sha:
        problems.append(f"дверь отдаёт другой выпуск: {got_sha[:12]} вместо {want_sha[:12]}")
    elif got_pub != want_pub:
        problems.append(f"дверь отдаёт ту же сборку прошлой публикации: published_at {got_pub} "
                        f"вместо {want_pub}")
    if not headers.get("last-modified"):
        problems.append("нет заголовка Last-Modified — сторож объявит панель мёртвой")
    if "json" not in headers.get("content-type", ""):
        problems.append(f"content-type {headers.get('content-type', '')!r}: вместо данных отдаётся страница")
    return problems


def _finish_verify(cfg: Config, mark: dict) -> None:
    if mark.get("kind") == "publish" and mark.get("code_commit"):
        _write_atomic(cfg.built_mark, mark["code_commit"] + "\n")
    cfg.pushed_mark.unlink(missing_ok=True)


def cmd_verify(cfg: Config, *, once: bool = False) -> int:
    """Читает опубликованное ЧЕРЕЗ ТУ ЖЕ дверь, что и браузер.

    Ждёт совпадения хэша И `published_at` из `release.pushed`. Источник ответа
    (`x-data-source`) — `pages` у здорового канала; `raw`/`edge-cache` значат,
    что GitHub Pages отказывает, а витрина живёт на запасном пути: выпуск
    засчитывается, но это тревога (код 3). Не дождались — код 3, отметка
    `release.pushed` остаётся, `release.commit` не пишется; дожимают следующие
    такты. Обходить кэш края параметром нельзя: проверялось бы не то, что
    видит браузер.
    """
    mark = read_mark(cfg.pushed_mark)
    if mark is None:
        print("  сверять нечего: незавершённой публикации нет")
        return 0
    want_sha, want_pub = mark["payload_sha256"], mark["published_at"]
    deadline = time.monotonic() + (0 if once else cfg.verify_timeout)
    attempt, problems, degraded = 0, [], None
    while True:
        attempt += 1
        try:
            served, headers = _fetch_door(cfg)
        except urllib.error.HTTPError as exc:
            body = exc.read()[:200].decode("utf-8", "replace") if exc.fp else ""
            problems = [f"дверь ответила {exc.code}: {body}".strip()]
        except ValueError as exc:
            problems = [f"ответ не разбирается как строгий JSON: {exc}"]
        except (urllib.error.URLError, OSError) as exc:
            problems = [f"дверь не отвечает: {type(exc).__name__}"]
        else:
            problems = _served_problems(served, headers, want_sha, want_pub)
            if not problems:
                source = headers.get("x-data-source", "")
                if source == "pages":
                    _finish_verify(cfg, mark)
                    print(f"  сверка пройдена с попытки {attempt}: {cfg.public_url} отдаёт "
                          f"{want_sha[:12]} · {want_pub}, Last-Modified {headers.get('last-modified')}")
                    return 0
                degraded = source or "неизвестный"
                problems = [f"выпуск отдаётся запасным источником «{degraded}»: GitHub Pages "
                            "ветки release не отвечает или отдаёт негодное"]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        print(f"  попытка {attempt}: {problems[0]}; жду {cfg.verify_pause:.0f} с")
        cfg.sleep(min(cfg.verify_pause, max(remaining, 0)))

    if degraded:
        _finish_verify(cfg, mark)
        print(f"СВЕРКА: выпуск {want_sha[:12]} на витрине, но через запасной источник "
              f"«{degraded}» — GitHub Pages не отдаёт ветку release (настройки Pages, "
              "сборка pages-build-deployment)", file=sys.stderr)
        return 3
    for problem in problems:
        print(f"СВЕРКА: {problem}", file=sys.stderr)
    waited = "одна попытка" if once else f"{cfg.verify_timeout:g} с"
    print(f"СВЕРКА: выпуск {want_sha[:12]} в ветке {BRANCH} с {mark.get('pushed_at', '?')}, "
          f"но дверь его не отдала ({waited}); сверку дожмут следующие такты", file=sys.stderr)
    return 3


# ------------------------------------------------------------- прореживание

def _keep(day: date, today: date) -> bool:
    """Моложе 90 дней — хранить всё; старше — только понедельники."""
    return (today - day).days <= KEEP_DAYS or day.weekday() == 0


def release_day(path: Path) -> date | None:
    """День выпуска — из СОДЕРЖАНИЯ (`published_at`, иначе `generated_at`).

    Не mtime: в рабочем дереве git mtime — время checkout, и после
    переклонирования правило «90 дней, затем понедельники» не значило бы ничего.
    """
    try:
        meta = json.loads(path.read_text(encoding="utf-8")).get("meta", {})
    except (OSError, ValueError, AttributeError):
        return None
    stamp = parse_stamp(meta.get("published_at")) or parse_stamp(meta.get("generated_at"))
    return stamp.date() if stamp else None


def _doomed(paths, today: date, keep_sha: str | None) -> tuple[list[Path], list[Path]]:
    doomed, undated = [], []
    for path in paths:
        if keep_sha and path.stem == keep_sha:
            continue
        day = release_day(path)
        if day is None:
            undated.append(path)
        elif not _keep(day, today):
            doomed.append(path)
    return doomed, undated


def prune_local(cfg: Config, today: date) -> int:
    """Локальные выпуски сборки — по тому же правилу; указатель не трогается."""
    directory = cfg.local_release.parent
    if not directory.exists():
        return 0
    pointer = directory / POINTER
    keep = None
    if pointer.exists():
        try:
            keep = json.loads(pointer.read_text(encoding="utf-8"))["meta"]["payload_sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            keep = None
    doomed, _ = _doomed((p for p in directory.glob("*.json") if p.name != POINTER), today, keep)
    for path in doomed:
        path.unlink()
    return len(doomed)


def cmd_prune(cfg: Config, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    if cfg.pushed_mark.exists():
        print("  прореживание отложено: последняя публикация ещё не подтверждена сверкой")
        return 0
    print(f"  локальных выпусков удалено: {prune_local(cfg, today)}")
    identity(cfg)
    result = {}

    def attempt():
        clone = sync(cfg)
        tidy_tree(clone)
        pointer = clone / POINTER
        keep = None
        if pointer.exists():
            keep = strict_json(pointer.read_text(encoding="utf-8")).get("meta", {}).get("payload_sha256")
        listing = sorted((clone / RELEASES_DIR).glob("*.json"))
        doomed, undated = _doomed(listing, today, keep)
        for path in undated:
            print(f"  ВНИМАНИЕ: у {path.name[:16]}… нет даты в содержании — не трогаю", file=sys.stderr)
        for path in doomed:
            git("rm", "--quiet", "--", f"{RELEASES_DIR}/{path.name}", cwd=clone)
        if commit_if_changed(cfg, clone, f"прореживание: удалено выпусков {len(doomed)}"):
            push(clone)
        result.update(total=len(listing), removed=len(doomed))
        return clone

    with_retries(cfg, "прореживание", attempt)
    if result["removed"]:
        print(f"  прорежено: {result['removed']} из {result['total']} (история остаётся в git)")
    else:
        print(f"  прореживать нечего: {result['total']} выпусков")
    return 0


# -------------------------------------------------------------------- вход

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Публикация выпуска в ветку release")
    parser.add_argument("command", nargs="?", default="publish",
                        choices=("publish", "verify", "prune", "rollback", "init"))
    parser.add_argument("sha", nargs="?", help="хэш выпуска для rollback")
    parser.add_argument("--verify", action="store_true", help="сверка через боевую дверь")
    parser.add_argument("--once", action="store_true", help="сверка: одна попытка, без ожидания")
    parser.add_argument("--prune", action="store_true", help="проредить старые выпуски")
    args = parser.parse_args(argv)
    command = "verify" if args.verify else "prune" if args.prune else args.command
    if command == "rollback" and not args.sha:
        parser.error("rollback: нужен хэш выпуска")
    cfg = Config.from_env()
    try:
        if command == "verify":
            return cmd_verify(cfg, once=args.once)
        if command == "prune":
            return cmd_prune(cfg)
        if command == "rollback":
            return cmd_rollback(cfg, args.sha)
        if command == "init":
            return cmd_init(cfg)
        return cmd_publish(cfg)
    except PublishError as exc:
        print(f"ПУБЛИКАЦИЯ: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
