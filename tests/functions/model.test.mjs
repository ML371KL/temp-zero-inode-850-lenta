// Поведенческий тест двери данных `functions/api/model.js` и фильтра
// `functions/_middleware.js` в Node — с подменёнными `fetch`, `caches` и часами.
//
// Регулярки по исходнику (так проверяла дверь 850oa) не видят логики: выбор
// источника, минутную метку, строгий разбор, заголовки из JSON, запасную копию,
// две причины 503, HEAD. Здесь функция ИСПОЛНЯЕТСЯ, и спрашивается ответ.
//
//     node --test tests/functions/model.test.mjs
//
// Запускает его и pytest-обёртка `tests/test_edge.py::test_the_model_function_behaves_in_node`.
import { test, afterEach } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

// Модуль функции — ES-модуль в файле .js без package.json: грузим текстом через
// data:-адрес, чтобы не зависеть от того, как версия Node угадывает тип модуля.
async function load(path) {
  const source = readFileSync(join(ROOT, path), "utf8");
  return import("data:text/javascript;base64," + Buffer.from(source).toString("base64"));
}

const { onRequest } = await load("functions/api/model.js");
const middleware = await load("functions/_middleware.js");

const PAGES = "https://ml371kl.github.io/temp-zero-inode-850-lenta-data/latest.json";
const RAW = "https://raw.githubusercontent.com/ML371KL/temp-zero-inode-850-lenta-data/release/latest.json";
const SITE = "https://tzi-850-lenta.pages.dev";
const SHA_A = "0123456789ab".padEnd(64, "c");
const SHA_B = "fedcba987654".padEnd(64, "d");
const PUBLISHED = "2026-09-28T17:31:05+00:00";
const NOW = Date.UTC(2026, 8, 28, 17, 45, 30);

const realNow = Date.now;
const realFetch = globalThis.fetch;
afterEach(() => {
  Date.now = realNow;
  globalThis.fetch = realFetch;
  delete globalThis.caches;
});

function release({ sha = SHA_A, published = PUBLISHED, schema = "lenta-v1", extra = "" } = {}) {
  return `{"schema": ${JSON.stringify(schema)}, "meta": {"payload_sha256": ${JSON.stringify(sha)}, ` +
    `"published_at": ${JSON.stringify(published)}, "generated_at": "2026-09-28T17:30:00+00:00"}, ` +
    `"fair_value": {"central": 1500${extra}}}`;
}

const ok = (body, type = "application/json; charset=utf-8") => () =>
  new Response(body, { status: 200, headers: { "content-type": type } });
const status = (code) => () => new Response("upstream says no", { status: code });
const broken = (name = "TypeError") => () => {
  const error = new Error("сбой сети");
  error.name = name;
  throw error;
};

class FakeCache {
  constructor() {
    this.store = new Map();
    this.puts = [];
  }
  async match(request) {
    const hit = this.store.get(typeof request === "string" ? request : request.url);
    return hit ? new Response(hit.body, { headers: hit.headers }) : undefined;
  }
  async put(request, response) {
    this.puts.push(request.url);
    this.store.set(request.url, {
      body: await response.text(),
      headers: Object.fromEntries(response.headers),
    });
  }
}

function world(routes, { now = NOW, cache = new FakeCache() } = {}) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    const handler = routes[String(url).split("?")[0]];
    if (!handler) throw new Error(`неожиданный подзапрос ${url}`);
    return handler(url, init);
  };
  globalThis.caches = { default: cache };
  Date.now = () => now;
  return { calls, cache };
}

async function ask(method = "GET", { path = "/api/model", headers = {} } = {}) {
  const waits = [];
  const request = new Request(SITE + path, { method, headers });
  const response = await onRequest({ request, env: {}, waitUntil: (p) => waits.push(p) });
  await Promise.all(waits);
  return response;
}

// ------------------------------------------------------------------ источник

test("Pages отвечает: 200, тело как есть, заголовки из published_at", async () => {
  const { calls, cache } = world({ [PAGES]: ok(release()) });
  const response = await ask();
  assert.equal(response.status, 200);
  assert.equal(await response.text(), release());
  assert.equal(response.headers.get("x-data-source"), "pages");
  assert.equal(response.headers.get("content-type"), "application/json; charset=utf-8");
  assert.equal(response.headers.get("cache-control"), "public, max-age=60");
  assert.equal(response.headers.get("last-modified"), "Mon, 28 Sep 2026 17:31:05 GMT");
  const seconds = Math.floor(Date.parse(PUBLISHED) / 1000);
  assert.equal(response.headers.get("etag"), `"${SHA_A.slice(0, 12)}.${seconds}"`, "ETag сильный");
  assert.equal(response.headers.get("x-data-published"), "2026-09-28T17:31:05.000Z");

  assert.equal(calls.length, 1, "запасной источник при живом Pages не трогается");
  assert.equal(calls[0].url, `${PAGES}?m=${Math.floor(NOW / 60000)}`, "минутная метка в query");
  assert.equal(calls[0].init.cf.cacheTtl, 60);
  const agent = new Headers(calls[0].init.headers).get("user-agent");
  assert.match(agent, /^[\x20-\x7e]+$/, "User-Agent только ASCII");

  assert.deepEqual(cache.puts, [`${SITE}/__edge-cache/api/model/latest.json`],
    "годная копия — в кэш края под ключом своего хоста");
});

test("Метку строит сервер: query клиента не доходит до источника", async () => {
  const { calls } = world({ [PAGES]: ok(release()) }, { now: NOW + 61_000 });
  await ask("GET", { path: "/api/model?m=1&bust=please" });
  assert.equal(calls[0].url, `${PAGES}?m=${Math.floor((NOW + 61_000) / 60000)}`);
});

test("Pages 404, raw отвечает: 200 из raw", async () => {
  const { calls } = world({ [PAGES]: status(404), [RAW]: ok(release()) });
  const response = await ask();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-data-source"), "raw");
  assert.equal(await response.text(), release());
  assert.deepEqual(calls.map((c) => c.url.split("?")[0]), [PAGES, RAW]);
  assert.equal(calls[1].url, RAW);
});

for (const [label, body, type] of [
  ["HTML вместо данных", "<!doctype html><title>404</title>", "text/html"],
  ["NaN в JSON", release({ extra: ', "x": NaN' }), undefined],
  ["битый JSON", release().slice(0, 40), undefined],
  ["чужая схема", release({ schema: "magnit-v5.1" }), undefined],
  ["без published_at", release({ published: "" }), undefined],
  ["без хэша", release({ sha: "не хэш" }), undefined],
]) {
  test(`Pages отдаёт негодное (${label}) — берётся raw`, async () => {
    world({ [PAGES]: ok(body, type), [RAW]: ok(release({ sha: SHA_B })) });
    const response = await ask();
    assert.equal(response.status, 200);
    assert.equal(response.headers.get("x-data-source"), "raw");
    assert.equal(JSON.parse(await response.text()).meta.payload_sha256, SHA_B);
  });
}

test("Оба источника 404 и копии нет: 503 not published yet", async () => {
  world({ [PAGES]: status(404), [RAW]: status(404) });
  const response = await ask();
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("cache-control"), "no-store");
  const body = JSON.parse(await response.text());
  assert.equal(body.error, "not published yet");
});

test("Отказ, не похожий на «ещё не публиковали»: 503 upstream unavailable", async () => {
  for (const [pages, raw] of [[status(500), status(404)], [status(404), broken()],
    [broken("AbortError"), broken()], [ok(release({ schema: "x" })), ok("<html>")]]) {
    world({ [PAGES]: pages, [RAW]: raw });
    const response = await ask();
    assert.equal(response.status, 503);
    const body = JSON.parse(await response.text());
    assert.equal(body.error, "upstream unavailable", JSON.stringify(body));
  }
  world({ [PAGES]: broken("AbortError"), [RAW]: status(502) });
  const body = JSON.parse(await (await ask()).text());
  assert.equal(body.pages, "timeout");
  assert.equal(body.raw, "http 502");
});

test("Оба источника отказали: последняя годная копия из кэша края", async () => {
  const cache = new FakeCache();
  world({ [PAGES]: ok(release()) }, { cache });
  await ask();

  world({ [PAGES]: broken(), [RAW]: status(503) }, { cache });
  const response = await ask();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-data-source"), "edge-cache");
  assert.equal(await response.text(), release());
  assert.equal(response.headers.get("last-modified"), "Mon, 28 Sep 2026 17:31:05 GMT",
    "возраст копии виден по Last-Modified");
  assert.equal(cache.puts.length, 1, "копия из кэша обратно не пишется");

  const stored = cache.store.get(`${SITE}/__edge-cache/api/model/latest.json`);
  assert.match(stored.headers["cache-control"], /max-age=(\d{6,})/, "копия живёт дольше минуты");

  world({ [PAGES]: status(404), [RAW]: status(404) }, { cache });
  assert.equal((await ask()).headers.get("x-data-source"), "edge-cache",
    "пропавшая ветка — копия лучше пустоты; сторож увидит возраст");
});

test("Испорченная копия в кэше не отдаётся", async () => {
  const cache = new FakeCache();
  await cache.put(new Request(`${SITE}/__edge-cache/api/model/latest.json`), new Response("<html>"));
  world({ [PAGES]: broken(), [RAW]: broken() }, { cache });
  assert.equal((await ask()).status, 503);
});

// ------------------------------------------------------------ методы и кэш

test("HEAD: те же заголовки, без тела", async () => {
  world({ [PAGES]: ok(release()) });
  const response = await ask("HEAD");
  assert.equal(response.status, 200);
  assert.equal(await response.text(), "");
  assert.equal(response.headers.get("last-modified"), "Mon, 28 Sep 2026 17:31:05 GMT");
  assert.equal(response.headers.get("x-data-source"), "pages");
});

test("Прочие методы: 405 с Allow и без подзапросов", async () => {
  const { calls } = world({ [PAGES]: ok(release()) });
  for (const method of ["POST", "PUT", "DELETE", "PATCH"]) {
    const response = await ask(method);
    assert.equal(response.status, 405);
    assert.equal(response.headers.get("allow"), "GET, HEAD");
  }
  assert.equal(calls.length, 0);
});

test("If-None-Match: 304 и для сильного, и для слабого (W/) ETag", async () => {
  world({ [PAGES]: ok(release()) });
  const etag = (await ask()).headers.get("etag");
  for (const tag of [etag, `W/${etag}`, `"zzz", W/${etag}`]) {
    const response = await ask("GET", { headers: { "if-none-match": tag } });
    assert.equal(response.status, 304, tag);
    assert.equal(response.headers.get("etag"), etag);
  }
  const other = await ask("GET", { headers: { "if-none-match": '"0123456789ab.1"' } });
  assert.equal(other.status, 200);
});

test("Тот же выпуск, опубликованный заново, — другой ETag и Last-Modified", async () => {
  world({ [PAGES]: ok(release()) });
  const first = await ask();
  world({ [PAGES]: ok(release({ published: "2026-09-29T17:31:05Z" })) });
  const second = await ask();
  assert.notEqual(first.headers.get("etag"), second.headers.get("etag"));
  assert.equal(second.headers.get("last-modified"), "Tue, 29 Sep 2026 17:31:05 GMT");
});

// ------------------------------------------------------------------ фильтр

test("Фильтр: чужие пути под /api — JSON-404, канонический — дальше с заголовками", async () => {
  const next = async () => new Response('{"ok":1}', { status: 200 });
  for (const path of ["/api/foo", "/api//model", "/api/model/", "/api/Model", "/api/model.json", "/api"]) {
    const response = await middleware.onRequest({ request: new Request(SITE + path), next });
    assert.equal(response.status, 404, path);
    assert.equal(JSON.parse(await response.text()).error, "not found");
    assert.ok(response.headers.get("content-security-policy"));
  }
  const response = await middleware.onRequest({ request: new Request(SITE + "/api/model"), next });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("x-content-type-options"), "nosniff");
  assert.equal(response.headers.get("referrer-policy"), "no-referrer");
  assert.match(response.headers.get("content-security-policy"), /script-src 'self' 'sha256-/);
});
