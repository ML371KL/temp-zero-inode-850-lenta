# -*- coding: utf-8 -*-
"""Исходники витрины для текстовых проверок: `web/app.js` и `web/styles.css` без комментариев.

Вынесено из `tests/test_screens.py`, чтобы проверки выпуска, читающие витрину,
не зависели от модуля экранов.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


def without_comments(text: str, *, line_comments: bool) -> str:
    """Код без комментариев: объяснение в комментарии не должно выдавать себя за код."""
    out = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    if line_comments:
        out = re.sub(r"(?m)^\s*//.*$", " ", out)
    return out


APP_CODE = without_comments(APP, line_comments=True)
CSS_CODE = without_comments(CSS, line_comments=False)


def function_body(name: str) -> str:
    """Тело функции `app.js` без комментариев — до следующего объявления."""
    begin = APP_CODE.index(f"function {name}(")
    tail = re.search(r"(?m)^(?:function |async function |const |let )", APP_CODE[begin + 1:])
    return APP_CODE[begin:begin + 1 + tail.start()] if tail else APP_CODE[begin:]
