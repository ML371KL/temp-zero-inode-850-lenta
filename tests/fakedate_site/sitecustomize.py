# -*- coding: utf-8 -*-
"""Подмена «сегодня» в ПОДПРОЦЕССАХ прогона «в будущем».

Каталог попадает в `PYTHONPATH` только из `tests/fakedate_plugin.install()`,
то есть только при заданной `FAKE_TODAY`; без неё этот файл не читается
никогда. Интерпретатор импортирует `sitecustomize` сам при старте — до
импорта модели, — и подпроцесс (например, настоящая сборка
`ops/build_release.py`) видит ту же дату, что и тест, который его запустил.
"""
import importlib.util as _util
import os as _os
from pathlib import Path as _Path

if _os.environ.get("FAKE_TODAY", "").strip():
    _spec = _util.spec_from_file_location(
        "_fakedate_plugin", _Path(__file__).resolve().parents[1] / "fakedate_plugin.py")
    _module = _util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    _module.install()
