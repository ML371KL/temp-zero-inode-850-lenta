"""HTTP-клиент слоя индикаторов: только стандартная библиотека.

Три правила, каждое из которых оплачено чужим опытом.

**Троттлинг по хосту, а не глобальный.** Запросы с VPS идут с одного IP на
все восемь панелей владельца; забанят — «ослепнут» все. Поэтому пауза между
обращениями к одному хосту, и никакого параллелизма внутри хоста.

**Повтор только там, где он осмыслен.** Сетевые сбои, 429 и 5xx — повторяем;
4xx — нет, это наша ошибка, и повторять её значит дразнить бан. Диагноз по
одной попытке не ставится.

**Сырой ответ сохраняется до разбора.** Парсер можно починить задним числом,
если байты сохранены; если не сохранены — точка потеряна навсегда. У
источников, отдающих только текущее состояние, истории нет вообще,
восстановить её неоткуда.

**Чужой корень — только своему хосту.** Хост, подписанный удостоверяющим
центром, которого нет в хранилище сервера (у 850oa — Росстат и корень
Минцифры), получает дополнительные сертификаты через `EXTRA_CA`, и только
он: глобально доверие не расширяется, а проверка не отключается никогда.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Контакт — адрес проекта, не личная почта владельца: заголовок уходит
# КАЖДОМУ источнику, и личный адрес оседал в чужих логах без нужды.
# Только ASCII: заголовки HTTP кодируются latin-1, кириллица роняет запрос
# UnicodeEncodeError ещё до отправки.
USER_AGENT = "tzi-850-lenta/1.0 (+https://github.com/ML371KL/temp-zero-inode-850-lenta)"
DEFAULT_TIMEOUT = 30.0
# Пауза между обращениями к ОДНОМУ хосту. Две секунды, а не одна: VPS общий
# для всех панелей владельца, а суточный бюджет запросов Ленты мал (ISS ≈7,
# lentagroup.ru ≤ 8, «Работа России» ≤ 10) — скорость здесь ничего не стоит.
DEFAULT_THROTTLE = 2.0
RETRY_STATUS = (408, 425, 429, 500, 502, 503, 504)

# Хост → файл сертификатов, которым он доверяется ВДОБАВОК к обычному
# хранилищу (файл — в `indicators/tls/`, происхождение и отпечатки — в шапке
# файла и в тесте). У 850oa так доверялся Росстат (корень Минцифры); сборщиков
# с таким хостом здесь пока нет, механизм оставлен.
TLS_DIR = Path(__file__).resolve().parent / "tls"
EXTRA_CA: dict[str, Path] = {}

_last_call: dict[str, float] = {}


def tls_context(url: str, *, verify: bool = True) -> ssl.SSLContext | None:
    """Контекст TLS для адреса; None — обычная проверка по хранилищу машины."""
    if not verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    extra = EXTRA_CA.get(urllib.parse.urlsplit(url).hostname or "")
    if extra is None:
        return None
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=str(extra))
    return context


@dataclass
class RawSink:
    """Куда класть сырой ответ СРАЗУ ПО ПОЛУЧЕНИИ, до разбора.

    Шапка этого модуля обещала «сырой ответ сохраняется до разбора» с первой
    итерации, но код обещание не исполнял: сборщик накапливал ответы в списке
    и возвращал их вместе с разобранными рядами, а складывал их вызывающий —
    ПОСЛЕ разбора. Значит, исключение на любой из 64 страниц обхода вакансий
    теряло весь день, а восстановить его нечем: у источников, отдающих только
    текущее состояние (у 850oa — вакансии), истории нет вовсе.

    Приёмник закрывает разрыв: `fetch(..., sink=..., name=...)` пишет байты на
    диск раньше, чем кто-либо попытается их разобрать. Сбой разбора после
    этого стоит одной строки ряда, а не дня наблюдений.
    """

    store: object
    source: str
    day: str | None = None
    kept: int = 0

    def keep(self, name: str, response: "Response", *, body: bytes | None = None,
             sha256: str | None = None, extra: dict | None = None) -> None:
        """Кладёт ответ на диск под именем `name`.

        `body` — положить не весь ответ, а вырезанный из него кусок (блок
        ленты раскрытия, тело сообщения): тогда контрольная сумма считается
        от ТОГО, ЧТО ЛЕГЛО В ФАЙЛ, иначе сверка «файл против меты» сравнивала
        бы разные вещи и не сходилась ни при какой порче.
        """
        body = response.body if body is None else body
        self.store.save_raw(self.source, name, body, url=response.url,
                            fetched_at=response.fetched_at,
                            sha256=sha256 or hashlib.sha256(body).hexdigest(),
                            day=self.day, extra=extra)
        self.kept += 1


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int | None, message: str):
        super().__init__(f"{url}: {message}")
        self.url, self.status = url, status


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    body: bytes
    fetched_at: str
    headers: dict[str, str]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)

    @property
    def rate_limit(self) -> dict[str, str]:
        """Заголовки X-RateLimit-*, если источник их отдаёт: лимиты проверяются
        до того, как по источнику поставят расписание."""
        return {k: v for k, v in self.headers.items() if k.lower().startswith("x-ratelimit")}


def _throttle(host: str, seconds: float) -> None:
    last = _last_call.get(host)
    if last is not None:
        wait = seconds - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
    _last_call[host] = time.monotonic()


def fetch(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    attempts: int = 3,
    throttle: float = DEFAULT_THROTTLE,
    verify_tls: bool = True,
    sink: "RawSink | None" = None,
    name: str | None = None,
) -> Response:
    """Загрузка с повторами и троттлингом по хосту.

    `verify_tls=False` допустим ТОЛЬКО для `storage.zds.zebra-group.ru`, где
    сертификат просрочен, а данные нужны. Глобально проверку не отключаем.
    Хостам из `EXTRA_CA` проверка не ослабляется, а дополняется их корнем.

    `sink` и `name` — куда и под каким именем положить сырой ответ ДО того,
    как он будет разобран. Ошибка записи не глотается: если байты не легли на
    диск, наблюдение потеряно, и узнать об этом надо сразу.
    """
    host = urllib.parse.urlsplit(url).netloc
    request_headers = {
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip",
        **(headers or {}),
    }
    context = tls_context(url, verify=verify_tls)

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        _throttle(host, throttle)
        request = urllib.request.Request(url, data=data, headers=request_headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
                body = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    body = gzip.decompress(body)
                result = Response(
                    url=url, status=response.status, body=body,
                    fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    headers={k: v for k, v in response.headers.items()},
                )
                if sink is not None and name:
                    sink.keep(name, result)
                return result
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRY_STATUS:
                raise FetchError(url, exc.code, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
        if attempt < attempts:
            time.sleep(min(2 ** attempt, 8))

    raise FetchError(url, None, f"не удалось за {attempts} попыток: {last_error}")


def post_json(url: str, payload: dict, *, headers: dict[str, str] | None = None,
              **kwargs) -> Response:
    body = json.dumps(payload).encode("utf-8")
    return fetch(url, data=body,
                 headers={"Content-Type": "application/json", **(headers or {})}, **kwargs)
