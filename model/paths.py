"""Где лежат данные модели: один источник путей для ядра, выпуска и индикаторов.

Каталог данных по умолчанию — `data/` рядом с кодом. Переменная
`LENTA_DATA_DIR` подменяет его целиком (книга, факты, календарь, проверки):
так тесты и сверки гоняют тот же код на другой книге, не копируя дерево.
Ядро (`model.core.run_cell`) диск не читает вовсе — оно считает словарь книги;
пути нужны только загрузчикам.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ["LENTA_DATA_DIR"]) if os.environ.get("LENTA_DATA_DIR") else ROOT / "data"
BOOK_DIR = DATA_DIR / "assumptions"
FACTS_DIR = DATA_DIR / "facts"
CHECKS_DIR = DATA_DIR / "checks"
CALENDAR_FILE = DATA_DIR / "calendar.json"
