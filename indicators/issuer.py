"""Эмитент, которого считает модель: тикер, имя, префикс рядов, аналоги, облигации, юрлица.

Всё, что в коде сборщиков, журнала и выпуска 850oa было именем компании
(ряд котировки, префикс рядов эмитента, фильтр облигаций, адреса раскрытия,
ИНН работодателей), берётся отсюда. Значения по умолчанию — Ленты (МКПАО
«Лента», LENT). Набор данных может назвать своего эмитента файлом
`issuer.json` в каталоге данных (`model.paths.DATA_DIR`) с теми же ключами —
так тот же код считает другую книгу (сверка порта вне репозитория); ключ вне
списка — отказ. Отсутствующий в файле ключ берётся по умолчанию.

Ряды эмитента называются `<SERIES_PREFIX>.<имя>`: цель нау-каста маржи —
`series("ebitda_margin_pre16")`, облигации — `series("bond.<SECID>.price")`.

**Облигации — по коду эмитента ISS, а не по краткому имени.** Префикс
«Лента» совпадает с чужими бумагами, а облигации в обращении у группы — это
выпуски ООО «О'КЕЙ» (эмитент ISS 4867), купленного в 2026 году; у ООО «Лента»
(эмитент 5997) все девять выпусков `is_traded = 0` (research/06 §5, проба ISS
28.09.2026). Ключ `bond_shortname_prefixes` оставлен для наборов данных,
которые называют облигации по префиксу (сверка порта на книге 850oa): он
действует, только если список эмитентов пуст.

**Юрлица группы для «Работы России»** (`legal_entities`). У каждого — ИНН,
основание и отметка сверки. Сверка ИНН ↔ наименование сделана по двум
реестрам, до которых дотягивается конвейер: карточке эмитента ISS (у кого
есть облигации) и карточке работодателя в ответе «Работы России» (ИНН, ОГРН и
наименование, 28.09.2026). ЕГРЮЛ напрямую не открывался (D15 требует сверки
по ЕГРЮЛ — отметка `egrul_checked` честно стоит `False`). Персональных данных
здесь нет: только юрлица.
"""

from __future__ import annotations

import json

from model.paths import DATA_DIR

ISSUER_FILE = DATA_DIR / "issuer.json"
DEFAULTS = {
    "ticker": "LENT",
    "name": "Лента",
    "series_prefix": "lenta",
    # Котировки MOEX TQBR, которые собираются вместе с тикером эмитента
    # (аналоги на одной базе, справочно).
    "peer_tickers": ["X5", "MGNT"],
    # Эмитенты облигаций группы в ISS: код эмитента и ИНН (по ИНН идёт поиск
    # выпусков `securities.json?q=<ИНН>`, по коду — фильтр ответа).
    "bond_issuers": [
        {"emitter_id": 5997, "inn": "7814148471", "name": "ООО «Лента»",
         "basis": "ISS /iss/securities.json?q=7814148471: 9 выпусков, все is_traded = 0 "
                  "(проба 28.09.2026)"},
        {"emitter_id": 4867, "inn": "7826087713", "name": "ООО «О'КЕЙ»",
         "basis": "ISS /iss/securities.json?q=7826087713: 12 выпусков, 4 в обращении на TQCB "
                  "(Б1Р2, Б1Р4, Б1Р6, Б1Р8; проба 28.09.2026)"},
    ],
    # Наследие 850oa: фильтр облигаций по префиксу краткого имени. Действует,
    # только если `bond_issuers` пуст.
    "bond_shortname_prefixes": [],
    # Сайт раскрытия эмитента (шаблон Zebra: JSON `App = {...}` в странице).
    "ir_site": "https://www.lentagroup.ru",
    "disclosure_page": "/ru/investors/regulatory-filings/",
    # Путь к ленте существенных фактов внутри `App.components` (через точку).
    "disclosure_feed": "ipjsc-lenta.material-facts",
    "publications_page": "/ru/investors/publications/",
    "legal_entities": [
        {"inn": "7814148471", "name": "ООО «Лента»", "brand": "гипермаркеты и супермаркеты «Лента»",
         "verified": "ISS: эмитент 5997 с этим ИНН; «Работа России»: ИНН, ОГРН "
                     "1037832048605, наименование ООО \"ЛЕНТА\" (28.09.2026)",
         "egrul_checked": False, "collect": True},
        {"inn": "7826087713", "name": "ООО «О'КЕЙ»", "brand": "гипермаркеты «О'КЕЙ» (с 02.06.2026)",
         "verified": "ISS: эмитент 4867 с этим ИНН; ГИР БО — карточка по ИНН "
                     "(research/04 §1); «Работа России»: 6 вакансий (28.09.2026)",
         "egrul_checked": False, "collect": True},
        {"inn": "6674121179", "name": "ООО «Элемент-Трейд»", "brand": "«Монетка»",
         "verified": "агрегаторы (rusprofile, saby — research/06 §2.3); «Работа России»: "
                     "ИНН, ОГРН 1036605217252, наименование ООО \"ЭЛЕМЕНТ-ТРЕЙД\" (28.09.2026)",
         "egrul_checked": False, "collect": True},
        {"inn": "7810495210", "name": "ООО «Дрогери Ритейл»", "brand": "«Улыбка радуги»",
         "verified": "агрегатор (saby — research/06 §2.3); «Работа России»: ИНН, ОГРН "
                     "1079847078453, наименование ООО \"ДРОГЕРИ РИТЕЙЛ\" (28.09.2026)",
         "egrul_checked": False, "collect": True},
    ],
}


def _load() -> dict:
    config = json.loads(json.dumps(DEFAULTS))       # глубокая копия
    if ISSUER_FILE.exists():
        raw = json.loads(ISSUER_FILE.read_text(encoding="utf-8"))
        unknown = sorted(set(raw) - set(DEFAULTS))
        if unknown:
            raise ValueError(f"{ISSUER_FILE.name}: незнакомые ключи {', '.join(unknown)}")
        config.update(raw)
        # Набор данных, назвавший облигации префиксом, говорит о ДРУГОМ
        # эмитенте: эмитенты Ленты по умолчанию ему не принадлежат.
        if "bond_shortname_prefixes" in raw and "bond_issuers" not in raw:
            config["bond_issuers"] = []
    return config


_CONFIG = _load()
TICKER: str = _CONFIG["ticker"]
NAME: str = _CONFIG["name"]
SERIES_PREFIX: str = _CONFIG["series_prefix"]
PEER_TICKERS: tuple[str, ...] = tuple(_CONFIG["peer_tickers"])
BOND_ISSUERS: tuple[dict, ...] = tuple(_CONFIG["bond_issuers"])
BOND_EMITTER_IDS: frozenset[int] = frozenset(int(i["emitter_id"]) for i in BOND_ISSUERS)
BOND_SHORTNAME_PREFIXES: tuple[str, ...] = tuple(_CONFIG["bond_shortname_prefixes"])
IR_SITE: str = _CONFIG["ir_site"].rstrip("/")
DISCLOSURE_URL: str = IR_SITE + _CONFIG["disclosure_page"]
DISCLOSURE_FEED: tuple[str, ...] = tuple(_CONFIG["disclosure_feed"].split("."))
PUBLICATIONS_URL: str = IR_SITE + _CONFIG["publications_page"]
LEGAL_ENTITIES: tuple[dict, ...] = tuple(_CONFIG["legal_entities"])
PRICE_SERIES = f"moex.price.{TICKER}"


def series(name: str) -> str:
    """Идентификатор ряда эмитента: `<SERIES_PREFIX>.<name>`."""
    return f"{SERIES_PREFIX}.{name}"


def employer_inns() -> tuple[str, ...]:
    """ИНН юрлиц, по которым собираются вакансии «Работы России» (`collect: true`)."""
    return tuple(str(e["inn"]) for e in LEGAL_ENTITIES if e.get("collect"))
