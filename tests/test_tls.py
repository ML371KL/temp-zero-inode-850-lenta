"""Доверие удостоверяющему центру Минцифры — только ISS Мосбиржи.

С 23.11.2026 iss.moex.com подписан «Russian Trusted Sub CA», корня которого нет в
хранилище Ubuntu: 24.11.2026 утренний сбор на сервере упал на
`CERTIFICATE_VERIFY_FAILED`. Корень добавляется ТОЛЬКО этому хосту — вдобавок к
обычному хранилищу, не вместо него и не для всех (правило семейства 850).

Проверяется три вещи: в файле ровно два сертификата с закреплёнными отпечатками
(подмена файла роняет набор); `fetch` отдаёт их только ISS; прочим хостам —
обычная проверка без добавок.
"""

from __future__ import annotations

import hashlib
import re
import ssl

import pytest

from indicators import http

pytestmark = pytest.mark.tact

# SHA-256 DER-кодировки; совпадают с копиями в хранилище Windows владельца и в 850oa.
PINNED_SHA256 = {
    "Russian Trusted Sub CA":
        "21557850 36c900db b5f1bb2a 1569c80c 55595bd6 bf94867a 29bbddbc 7d88a3f2",
    "Russian Trusted Root CA":
        "d26d2d02 31b7c39f 92cc7385 12ba5410 3519e440 5d68b5bd 703e9788 ca8ecf31",
}
ISS = "https://iss.moex.com/iss/engines/stock/zcyc.json?iss.meta=off"


def _common_name(cert: dict) -> str:
    return dict(pair[0] for pair in cert["subject"]).get("commonName", "")


def test_the_ministry_file_holds_exactly_the_pinned_pair():
    """Два сертификата с закреплёнными отпечатками — и ничего сверх."""
    path = http.EXTRA_CA["iss.moex.com"]
    pem = path.read_text(encoding="utf-8")
    blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)
    assert len(blocks) == 2, "в файле доверия ровно промежуточный и корень"
    assert "PRIVATE KEY" not in pem

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)     # без хранилища машины
    context.load_verify_locations(cafile=str(path))
    got = {_common_name(cert): hashlib.sha256(raw).hexdigest()
           for cert, raw in zip(context.get_ca_certs(), context.get_ca_certs(binary_form=True))}
    want = {name: digest.replace(" ", "") for name, digest in PINNED_SHA256.items()}
    assert got == want


class _Answer:
    """Ответ `urlopen` без сети."""

    status = 200
    headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return b"{}"


def test_only_iss_gets_the_ministry_root(monkeypatch):
    """ISS — обычное хранилище плюс файл; прочие хосты — без добавок."""
    assert set(http.EXTRA_CA) == {"iss.moex.com"}
    seen = []

    def urlopen(request, timeout=None, context=None):
        seen.append(context)
        return _Answer()

    monkeypatch.setattr(http.urllib.request, "urlopen", urlopen)
    for url in (ISS, "https://www.moex.com/ru/index/IMOEX", "https://www.cbr.ru/scripts/XML_daily.asp"):
        http.fetch(url, throttle=0, attempts=1)
    iss, www_moex, cbr = seen
    assert www_moex is None and cbr is None, "чужим хостам доверие не расширяется"
    assert isinstance(iss, ssl.SSLContext)
    assert iss.verify_mode == ssl.CERT_REQUIRED and iss.check_hostname, (
        "проверка не ослабляется — только дополняется корнем")
    assert set(PINNED_SHA256) <= {_common_name(cert) for cert in iss.get_ca_certs()}


def test_the_extra_root_is_matched_by_host_not_by_substring():
    """Поддомен-самозванец и хост в пути не меняют решения."""
    assert http.tls_context("https://iss.moex.com.example.org/") is None
    assert http.tls_context("https://example.org/iss.moex.com") is None
    assert http.tls_context("https://ISS.moex.com:443/iss") is not None
