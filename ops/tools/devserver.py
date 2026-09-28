"""Локальный предпросмотр панели без Cloudflare.

Отдаёт `web/` как статику и подменяет `/api/model` последним собранным
выпуском из `var/release/latest.json`. Нужен только для проверки вёрстки:
в проде `/api/model` обслуживает функция Pages, читающая ветку `release`
(GitHub Pages, запасной путь — raw).

CSP берётся ИЗ `functions/_middleware.js`, а не пишется здесь второй раз.
Без неё предпросмотр врёт в самом дорогом месте: инлайн-скрипт темы
разрешён по хэшу, и устаревший хэш ломает тему только в проде — локально
всё выглядело бы прекрасно.

    python ops/tools/devserver.py [порт] [путь-к-выпуску]

Второй аргумент подменяет выпуск: так проверяется вёрстка состояний, которых
на бою сейчас нет (плашка отказов, устаревший выпуск). Раньше для этого
рядом заводилась копия всего файла с одной изменённой строкой — копия жила
своей жизнью и врала про CSP.
"""

from __future__ import annotations

import json
import re
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
RELEASE = ROOT / "var" / "release" / "latest.json"
MIDDLEWARE = ROOT / "functions" / "_middleware.js"


def read_csp() -> str:
    """Собирает ту же строку CSP, что соберёт воркер в проде."""
    src = MIDDLEWARE.read_text(encoding="utf-8")
    hash_ = re.search(r'THEME_SCRIPT_HASH = "([^"]+)"', src).group(1)
    body = re.search(r"const CSP = \[(.*?)\]\.join", src, re.S).group(1)
    parts = [m.group(1) for m in re.finditer(r"[`\"]([^`\"]+)[`\"]", body)]
    return "; ".join(p.replace("${THEME_SCRIPT_HASH}", hash_) for p in parts)


CSP = read_csp()


class Handler(SimpleHTTPRequestHandler):
    release = RELEASE

    def do_GET(self):  # noqa: N802
        if self.path.split("?")[0] == "/api/model":
            if not self.release.exists():
                return self._json(503, {"error": "not published yet"})
            return self._raw(200, self.release.read_bytes())
        if self.path.split("?")[0] == "/api/quote":
            return self._json(200, {"quotes": {}, "delayed_minutes": 15})
        if self.path.startswith("/api"):
            return self._json(404, {"error": "not found"})
        return super().do_GET()

    def _json(self, status, body):
        self._raw(status, json.dumps(body, ensure_ascii=False).encode("utf-8"))

    def _raw(self, status, body: bytes):
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.send_header("cache-control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):  # noqa: N802
        # Предпросмотр НИЧЕГО не кэширует. Иначе правка стилей не доезжает до
        # браузера, а проверка вёрстки идёт по вчерашнему файлу — на этом
        # уже потерян час: медиазапрос был в файле на диске и отсутствовал в
        # загруженной таблице стилей.
        self.send_header("cache-control", "no-store, must-revalidate")
        # Те же заголовки, что ставит functions/_middleware.js на все ответы.
        self.send_header("content-security-policy", CSP)
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        super().end_headers()

    def log_message(self, *args):  # тише
        pass


def main() -> int:
    # Консоль Windows по умолчанию в cp1252 — печать по-русски её роняет.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8850
    release = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else RELEASE
    handler = partial(Handler, directory=str(WEB))
    Handler.release = release
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"предпросмотр: http://127.0.0.1:{port}/")
        print(f"выпуск: {release}")
        print(f"CSP: {CSP}")
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
