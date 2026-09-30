# -*- coding: utf-8 -*-
"""Гигиена публичного репозитория (DESIGN §1, блокирующее правило).

Репозиторий публичный: всё, что попало в дерево или в метаданные коммитов,
видно всем и навсегда. Здесь — запреты по регулярке на каждом файле дерева
(отслеживаемом и ещё не добавленном, кроме игнорируемых):

* IPv4-адреса (кроме петли), почтовые адреса вне разрешённых, пути профилей
  Windows (диск, затем Users) и `/home/<имя>/`, почта на gmail;
* имена SSH-ключей и личные учётные имена, префикс ID аккаунта Cloudflare —
  по sha256 (сам запрещённый текст в репозиторий не кладётся и здесь);
* в данных (`data/`, `tests/fixtures/`): факты Магнита (дочерние общества,
  контрольные числа его книги — по sha256), ФИО и контакты из ответов
  источников (`contact_person`, телефоны);
* во всех текстах: суммы книги Магнита рядом с упоминанием 850oa — по sha256
  (сравнения с 850oa только методические, без чисел книги-источника);
* первичка (PDF/XLSX/DOCX) вне фикстур, состояние и выходы (`var/`,
  `.wrangler/`, `*.sqlite`, `payload.json`, `release/`, `port-check/`);
* адреса авторов и коммиттеров всей видимой истории — только из
  `ops/commit-emails.allow` (тот же список сверяет шаг CI «Гигиена истории»).

Исключение — только строкой в `ALLOW`: файл (маска), правило, разрешённое
совпадение и причина. Гоняется в такте и в CI (метка `tact`): быстрый.

Проверку ВЫПУСКА (почты, телефоны, IP и пути внутри `latest.json`, код 1 у
сборки) делает `model/payload.validate()` — её владелец этап P4b.
"""
from __future__ import annotations

import fnmatch
import hashlib
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = pytest.mark.tact

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_EMAILS_FILE = ROOT / "ops" / "commit-emails.allow"

# Запрещённые целые слова (имена ключей, учётные имена владельца) — по первым
# 24 знакам sha256: сам текст в публичный репозиторий не попадает и отсюда.
FORBIDDEN_TOKENS = {
    "c2dbd494d21519b3c630c4ea": "имя SSH-ключа владельца",
    "2d5752a3c71382d4ecff5421": "имя SSH-ключа владельца",
    "421977ef38955ea9309c54a3": "имя SSH-ключа владельца",
    "7e1bb5d90c1bb3a8608565d3": "имя deploy-ключа (в репозитории — только алиас gh-850-lenta)",
    "d9c1ecc2218d604c80aaa4a6": "личное учётное имя владельца",
}
# Префикс ID аккаунта Cloudflare: первые 8 знаков любой шестнадцатеричной серии.
FORBIDDEN_HEX_PREFIX = {"e5ac341221cf41a5faec8c45": "ID аккаунта Cloudflare"}
# Контрольные числа книги Магнита (акции в обращении, выручка и EBITDA
# полугодий, площадь, D&A) — по sha256: их появление в data/ значит, что туда
# попала книга или факты Магнита.
MAGNIT_NUMBERS = {
    "7aaabbe95f6f095923adaa4d", "683f3c758828e87cd458e159", "61c2e62d6a039477587de053",
    "2ec2ddb876acea6a5f87bf64", "fa47739e92028e973069b52d", "c693d0791590123c73cb6664",
    "e797cf9a40d28d32bfbc6862", "ae63de46100e119bb234fd7c", "ab1611b096f18e44795408d0",
}

# Суммы книги Магнита (строки моста 850oa), которых не должно быть рядом с
# упоминанием 850oa ни в одном тексте (решение ведущего по проверке пакета
# аудита, п. 1.5; книга §16: сравнения с 850oa — только методические, без чисел
# книги-источника). Ключ — sha256 записи «<число> млрд» (запятая — десятичный
# знак, один пробел): сам запрещённый текст в репозиторий не кладётся и здесь.
MAGNIT_AMOUNTS_NEAR_850OA = {
    "10ce52b9e0668e7b2474a112": "строка LTIP моста книги 850oa",
}
NEAR_850OA = 200                        # «рядом» — в пределах 200 знаков от упоминания
AMOUNT = re.compile(r"(?<![\d,.])(\d+(?:[,.]\d+)?)[\s\u00a0\u202f]*млрд")
MENTION_850OA = re.compile(r"850oa", re.I)

OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
RULES_ALL = {
    "ipv4": re.compile(rf"(?<![\w.]){OCTET}\.{OCTET}\.{OCTET}\.{OCTET}(?![\w.])"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"),
    "gmail": re.compile(r"@g[m]ail\b", re.I),
    "user_path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/[c]/Users/|/home/[a-z_][a-z0-9_-]*/", re.I),
}
# Только в данных: книга, факты, фикстуры источников.
DATA_GLOBS = ("data/*", "tests/fixtures/*")
RULES_DATA = {
    "magnit_fact": re.compile(r"т[а]ндер|t[a]nder|с[а]мбери|s[a]mberi|д[и]кси|d[i]xy|"
                              r"книга допущений mgnt", re.I),
    "contact": re.compile(r"contact_?person|contact_?list|contact_?phone|contact_?email|"
                          r"contactperson", re.I),
    "phone": re.compile(r"\+7[\s(-]*\d{3}[\s)-]*\d{3}[\s-]*\d{2}[\s-]*\d{2}(?!\d)|"
                        r"(?<![\d.])8[\s-]?\(\d{3}\)[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"),
}

# Исключения: (маска файла, правило, разрешённое совпадение — регулярка, причина).
ALLOW = [
    ("*", "ipv4", r"127\.0\.0\.1|0\.0\.0\.0", "петля и «все интерфейсы» — не адрес сервера"),
    ("*", "ipv4", r"(?:192\.0\.2|198\.51\.100|203\.0\.113)\.\d+",
     "документационные сети RFC 5737 — примеры в тестах и документах"),
    ("*", "email", r"noreply@anthropic\.com", "строка соавторства коммитов Claude"),
    ("*", "email", r"[^@\s]+@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:test|invalid|example))",
     "зарезервированные домены RFC 2606 — адреса-заглушки в тестах"),
    ("tests/fixtures/SOURCES.md", "contact", r"contact_person|contact_list",
     "описание фикстур называет поля, которые сборщик отбрасывает; самих полей в фикстурах "
     "нет (tests/test_sources_lenta.py::test_fixtures_carry_no_personal_data)"),
    ("data/assumptions/*", "magnit_fact", r"(?i)д[и]кси|d[i]xy|с[а]мбери|s[a]mberi",
     "публичные данные аналога: интеграции Магнит — «Д.» 2021 и «Азбука вкуса»/«С.» 2025 в "
     "классе интеграций A-P2 книги «Ленты» (отчёты эмитента; решение ведущего F33) — это не "
     "книга и не оценка Магнита"),
]
# Контрольные числа книги Магнита, которые совпадают с его ПУБЛИЧНОЙ отчётностью
# (databook): строка аналога «Магнит» — публичные данные аналога со ссылкой на
# первоисточник (решение ведущего F33). Исключение — только файлом с причиной.
MAGNIT_NUMBER_ALLOW = [
    ("data/assumptions/evidence/book-1.0/facts/build_facts.py",
     "транскрипция Magnit Databook 1H 2026 для строки аналога (EBITDA и выручка полугодий, акции)"),
]

# Первичка и состояние в дереве запрещены целиком.
FORBIDDEN_PATHS = [
    (r"(?i)\.(pdf|xlsx|xls|xlsm|docx|doc)$", "первичка (PDF/XLSX/DOCX) живёт вне репозитория; "
     "в нём — хэши и выписки чисел со ссылкой на страницу"),
    (r"^\.wrangler/", "каталог wrangler хранит ID аккаунта и почту"),
    (r"^var/(?!\.gitkeep$)", "состояние и выходы тестов"),
    (r"(?i)\.sqlite3?$", "база состояния (журнал нау-каста)"),
    (r"(^|/)payload\.json$", "локальный выпуск"),
    (r"^release/", "локальные выпуски"),
    (r"(^|/)port-check/", "песочница переноса с материалами Магнита"),
]
FIXTURE_BINARIES = "tests/fixtures/"


def _allowed_emails() -> set[str]:
    return {line.strip() for line in ALLOWED_EMAILS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


@lru_cache(maxsize=1)
def _tree() -> tuple[str, ...]:
    """Файлы дерева: отслеживаемые и ещё не добавленные, кроме игнорируемых."""
    done = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                          cwd=str(ROOT), capture_output=True)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    names = [n for n in done.stdout.decode("utf-8").split("\0") if n]
    return tuple(n for n in names if (ROOT / n).is_file())


def _text(name: str) -> str | None:
    data = (ROOT / name).read_bytes()
    if b"\0" in data[:8192]:
        return None                      # двоичный файл (картинка) — не текст
    return data.decode("utf-8", errors="replace")


@lru_cache(maxsize=None)
def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]


def _is_allowed(name: str, rule: str, match: str) -> bool:
    if rule == "email" and match in _allowed_emails():
        return True
    return any(fnmatch.fnmatch(name, mask) and rule == r and re.fullmatch(ok, match)
               for mask, r, ok, _ in ALLOW)


def _magnit_amounts_near_850oa(name: str, text: str) -> list[str]:
    """Суммы из `MAGNIT_AMOUNTS_NEAR_850OA` не дальше `NEAR_850OA` знаков от «850oa»:
    «файл:строка: что это» — без самого текста."""
    mentions = [m.start() for m in MENTION_850OA.finditer(text)]
    if not mentions:
        return []
    bad = []
    for hit in AMOUNT.finditer(text):
        what = MAGNIT_AMOUNTS_NEAR_850OA.get(_h(hit.group(1).replace(".", ",") + " млрд"))
        if what and any(abs(hit.start() - at) <= NEAR_850OA for at in mentions):
            bad.append(f"{name}:{text.count(chr(10), 0, hit.start()) + 1}: {what} рядом с 850oa")
    return bad


def _findings() -> list[str]:
    bad = []
    for name in _tree():
        text = _text(name)
        if text is None:
            continue
        rules = dict(RULES_ALL)
        bad += _magnit_amounts_near_850oa(name, text)
        in_data = any(fnmatch.fnmatch(name, g) for g in DATA_GLOBS)
        if in_data:
            rules.update(RULES_DATA)
        for rule, pattern in rules.items():
            for hit in pattern.finditer(text):
                if not _is_allowed(name, rule, hit.group(0)):
                    line = text.count("\n", 0, hit.start()) + 1
                    bad.append(f"{name}:{line}: {rule}")
        for token in set(re.findall(r"[A-Za-z0-9_-]+", text)):
            if _h(token) in FORBIDDEN_TOKENS:
                bad.append(f"{name}: {FORBIDDEN_TOKENS[_h(token)]}")
        for run in set(re.findall(r"[0-9A-Fa-f]{8,}", text)):
            if _h(run[:8].lower()) in FORBIDDEN_HEX_PREFIX:
                bad.append(f"{name}: {FORBIDDEN_HEX_PREFIX[_h(run[:8].lower())]}")
        if in_data and not any(fnmatch.fnmatch(name, mask) for mask, _ in MAGNIT_NUMBER_ALLOW):
            for number in set(re.findall(r"(?<![\d.])\d+\.\d+(?![\d.])", text)):
                if _h(number) in MAGNIT_NUMBERS:
                    bad.append(f"{name}: контрольное число книги Магнита")
    return bad


def test_the_tree_carries_no_addresses_paths_keys_or_foreign_data():
    """Совпадение печатается файлом, строкой и правилом — без самого текста:
    вывод теста тоже бывает публичным (журнал CI)."""
    bad = _findings()
    assert not bad, "нарушена гигиена публичного репозитория:\n" + "\n".join(sorted(set(bad)))


def test_primary_documents_and_state_are_not_in_the_tree():
    bad = []
    for name in _tree():
        for pattern, reason in FORBIDDEN_PATHS:
            if re.search(pattern, name) and not (name.startswith(FIXTURE_BINARIES)
                                                 and pattern.startswith("(?i)\\.(pdf")):
                bad.append(f"{name}: {reason}")
    assert not bad, bad


def test_every_author_and_committer_is_on_the_allowlist():
    """Метаданные коммитов не удаляются задним числом: адрес автора и
    коммиттера каждого видимого коммита — из ops/commit-emails.allow. В CI
    видна вся история всех веток (fetch-depth 0), на сервере — клон main."""
    done = subprocess.run(["git", "log", "--all", "--format=%H %ae %ce"], cwd=str(ROOT),
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    allowed = _allowed_emails()
    bad = [line.split()[0][:12] for line in done.stdout.splitlines()
           if not set(line.split()[1:]) <= allowed]
    assert done.stdout.strip(), "история не видна — проверять нечего"
    assert not bad, f"адреса вне ops/commit-emails.allow в коммитах: {', '.join(bad[:20])}"


def test_the_allowlist_holds_only_noreply_addresses():
    for email in _allowed_emails():
        assert email.endswith(("@users.noreply.github.com", "noreply@github.com")), email


# ------------------------------------------------ правила сами ловят то, что должны

@pytest.mark.parametrize("rule,text", [
    ("ipv4", "сервер 203.0.113.7 отвечает"),
    ("email", "пишите на person@" + "mail.ru"),
    ("gmail", "адрес someone@" + "gmail.com"),
    ("user_path", "лежит в C:" + "\\Users\\someone\\x"),
    ("user_path", "лежит в /home/" + "someone/x"),
])
def test_each_rule_catches_its_case(rule, text):
    assert RULES_ALL[rule].search(text), (rule, text)


@pytest.mark.parametrize("rule,text", [
    ("contact", '{"contact_person": "…"}'),
    ("phone", "тел. +7 (495) 123-45-67"),
    ("phone", "тел. 8 (800) 555-35-35"),
    ("magnit_fact", "АО " + "Та" + "ндер"),
])
def test_each_data_rule_catches_its_case(rule, text):
    assert RULES_DATA[rule].search(text), (rule, text)


def test_the_rules_leave_ordinary_numbers_alone():
    text = "версия 4.135.0, цена 1619.5, выручка 1 234 567, дата 2026-09-28, 1.2.3"
    for rule in ("ipv4", "email", "user_path"):
        assert not RULES_ALL[rule].search(text), rule
    assert not RULES_DATA["phone"].search("сумма 81234567890 руб. и 89.1234567")
    assert _is_allowed("tests/x.py", "ipv4", "127.0.0.1")
    assert _is_allowed("tests/x.py", "email", "someone@example.com")
    assert not _is_allowed("tests/x.py", "email", "someone@" + "mail.ru")


def test_a_magnit_amount_near_850oa_is_caught_and_only_there():
    """Сумма книги Магнита рядом с «850oa» ловится (и с точкой, и с неразрывным
    пробелом); та же сумма далеко от упоминания и другое число с теми же
    последними знаками («+2…») — нет. Литерал собирается здесь по частям."""
    amount = "3," + "6"
    assert _magnit_amounts_near_850oa("x.txt", f"та же конвенция у 850oa ({amount} млрд)")
    assert _magnit_amounts_near_850oa("x.txt", f"850oa: {amount.replace(',', '.')}\u00a0млрд")
    assert not _magnit_amounts_near_850oa("x.txt", f"{amount} млрд" + " " * (NEAR_850OA + 1) + "850oa")
    assert not _magnit_amounts_near_850oa("x.txt", f"поток +2{amount} млрд ₽, как у 850oa")
    assert not _magnit_amounts_near_850oa("x.txt", f"{amount} млрд без упоминания книги-источника")


def test_the_hashed_lists_are_well_formed():
    for table in (FORBIDDEN_TOKENS, FORBIDDEN_HEX_PREFIX, MAGNIT_NUMBERS, MAGNIT_AMOUNTS_NEAR_850OA):
        assert all(re.fullmatch(r"[0-9a-f]{24}", h) for h in table)
