# -*- coding: utf-8 -*-
"""Факты и их первичка: хэши производных файлов и шлюз sha256 сборки фактов.

Внешний аудит 30.09.2026, находка D01:

* лист фактов (`data/assumptions/evidence/book-1.0/facts/build_facts.py`) при
  несовпадении sha256 документа первички с MANIFEST.md только печатал
  «ВНИМАНИЕ» и собирал факты с кодом 0, хотя VERIFY.md обещает код 2: подмена
  первички, не покрытая транскрипциями PDF и тождествами (карточка бумаги ISS,
  история датабука), уходила в факты молча;
* хэши производных файлов в `data/facts/sources.json` устарели у двух файлов из
  семи: `anchor.json` переписывает шаг канона после листа фактов, `peers.json`
  правили без пересборки манифеста; порядок периодов `accounting_base.json`
  зависел от PYTHONHASHSEED.

Первичка в репозиторий не кладётся: шлюз проверяется на подставном каталоге
первички — до него сборка читает только байты документов и MANIFEST.md.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FACTS = ROOT / "data" / "facts"
SHEET = ROOT / "data" / "assumptions" / "evidence" / "book-1.0" / "facts"

pytestmark = pytest.mark.tact


def test_derived_file_hashes_match_disk():
    """Каждый производный файл фактов — те байты, sha256 которых записан в
    `sources.json` (перенос теста 850oa на схему «Ленты»)."""
    derived = json.loads((FACTS / "sources.json").read_text(encoding="utf-8"))["derived_files"]
    assert set(derived) == {"accounting_base.json", "anchor.json", "shares.json", "debt.json",
                            "bridge_balance.json", "inorganic.json", "peers.json"}
    stale = {name: sha for name, sha in derived.items()
             if hashlib.sha256((FACTS / name).read_bytes()).hexdigest() != sha}
    assert not stale, f"хэш в sources.json не от файла на диске: {sorted(stale)}"


def test_the_accounting_base_is_written_in_a_reproducible_order():
    """Периоды истории — по порядку: байты файла не зависят от обхода множества."""
    base = json.loads((FACTS / "accounting_base.json").read_text(encoding="utf-8"))
    for block in ("halves", "fy", "quarters"):
        assert list(base[block]) == sorted(base[block]), block


@pytest.fixture
def sheet(monkeypatch, tmp_path):
    """Лист фактов модулем: каталог первички и каталог лога — временные."""
    monkeypatch.syspath_prepend(str(SHEET))
    for name in ("lib_primary", "build_facts"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    primary = tmp_path / "primary"
    primary.mkdir()
    monkeypatch.setenv("LENTA_PRIMARY_DIR", str(primary))
    monkeypatch.setenv("LENTA_FACTS_OUT", str(tmp_path / "facts"))
    spec = importlib.util.spec_from_file_location("build_facts", SHEET / "build_facts.py")
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "build_facts", module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "HERE", tmp_path / "sheet")
    monkeypatch.setattr(module, "LOG", [])
    return module, primary


def _fake_primary(module, primary: Path, *, tampered: str | None = None) -> None:
    """Документы реестра листа — подставными байтами — и MANIFEST.md с их sha256;
    у документа `tampered` в манифесте записан другой хэш."""
    lines = ["| файл | адрес | дата | sha256 | описание |", "|---|---|---|---|---|"]
    for rel, _ in module.DOCS.values():
        path = primary / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("документ " + rel).encode("utf-8"))
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if rel == tampered:
            sha = hashlib.sha256(b"other bytes").hexdigest()
        lines.append(f"| {rel} | https://example.invalid/{rel} | 2026-09-28 | {sha} | — |")
    (primary / "MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_the_manifest_gate_names_the_documents_that_changed(sheet):
    module, _ = sheet
    sources = {"A": {"path": "a.pdf", "hash_matches_manifest": True},
               "B": {"path": "iss/b.json", "hash_matches_manifest": False},
               "C": {"path": "c.xlsx", "hash_matches_manifest": False}}
    assert module.manifest_gate(sources) == ["iss/b.json", "c.xlsx"]
    assert module.manifest_gate({"A": sources["A"]}) == []


def test_a_changed_primary_document_stops_the_facts_build_with_code_2(sheet, capsys):
    """Байты карточки бумаги ISS не те, что в MANIFEST.md: сборка останавливается
    кодом 2 до разбора документов, причина — в логе, фактов нет."""
    module, primary = sheet
    tampered = module.DOCS["ISS_LENT_SEC"][0]
    _fake_primary(module, primary, tampered=tampered)
    with pytest.raises(SystemExit) as stopped:
        module.main()
    assert stopped.value.code == 2
    log = (module.HERE / "out" / "build_log.txt").read_text(encoding="utf-8")
    assert "ОШИБКА: sha256 первички не совпал с MANIFEST.md: " + tampered in log
    assert f"хэш = MANIFEST: {len(module.DOCS) - 1}" in log
    assert not (primary.parent / "facts").exists(), "факты не записаны"
    assert "ВНИМАНИЕ" in capsys.readouterr().out


def test_an_untouched_primary_passes_the_gate(sheet):
    """Контроль: при совпавших хэшах шлюз молчит — сборка идёт дальше, к сверке
    транскрипций (на подставных документах она и останавливает сборку, но уже
    своей причиной, а не хэшами)."""
    module, primary = sheet
    _fake_primary(module, primary)
    with pytest.raises(BaseException) as stopped:
        module.main()
    log = "\n".join(module.LOG)
    assert f"хэш = MANIFEST: {len(module.DOCS)}" in log
    assert "sha256 первички не совпал" not in log
    assert not isinstance(stopped.value, SystemExit) or "транскрипции" in log
