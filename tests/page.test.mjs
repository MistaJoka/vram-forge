// Browser tests for how index.html consumes prices.json. No test touches the internet.
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { after, before, beforeEach, afterEach, test } from "node:test";
import { chromium } from "playwright";

const html = await readFile(new URL("../index.html", import.meta.url), "utf8");
let server, base, browser, context, page, errors;

function feed(overrides = {}) {
  return {
    schema: 1,
    generated_at: new Date().toISOString().slice(0, 10),
    items: {
      5090: {
        price: 3333,
        as_of: "2026-11-05",
        sources: [{ name: "Newegg", url: "https://www.newegg.com/p/1" }],
        hist: [["2025-01", 1999], ["2026-11", 3333]],
      },
    },
    signal: { generated_at: "", window_days: 30, items: {} },
    held: [],
    ...overrides,
  };
}

async function open(body, { status = 200, hash = "#prices", stored } = {}) {
  await page.route("**/prices.json*", (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: typeof body === "string" ? body : JSON.stringify(body),
    }),
  );
  if (stored) {
    await page.addInitScript((value) => {
      if (location.protocol === "http:") localStorage.setItem("vramforge_edits", value);
    }, stored);
  }
  await page.goto("about:blank"); // same-URL navigation would not reload the page
  await page.goto(base + "/index.html" + hash);
  await page.waitForFunction(() => window.feedState && window.feedState !== "loading");
}

before(async () => {
  server = createServer((request, response) => {
    response.setHeader("content-type", "text/html");
    response.end(html);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch();
});
after(async () => {
  await browser?.close();
  server.closeAllConnections();
  server.close();
});
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  page = await context.newPage();
  page.setDefaultTimeout(4000);
  errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("dialog", (dialog) => {
    errors.push("dialog opened: " + dialog.message());
    dialog.dismiss();
  });
  await page.route(/^https:\/\//, (route) => route.abort());
});
afterEach(async () => {
  await context.close();
  assert.deepEqual(errors, []);
});

const priceOf = (id) => page.evaluate((key) => HW.find((h) => h.id === key).price, id);

test("feed price replaces the built-in snapshot everywhere", async () => {
  await open(feed());
  assert.equal(await priceOf("5090"), 3333);
  assert.match(await page.locator("#snap").innerText(), /\$3,333/);
  assert.match(await page.locator("#ticker").innerText(), /\$3,333/);
});

test("item missing from the feed keeps its snapshot price", async () => {
  await open(feed());
  assert.equal(await priceOf("3090"), 1463);
});

test("header says when prices were updated, not 'snapshot'", async () => {
  await open(feed());
  const stamp = await page.locator(".stamp").innerText();
  assert.match(stamp, /updated/i);
  assert.doesNotMatch(stamp, /snapshot/i);
  assert.doesNotMatch(await page.locator("#v-prices .warn").innerText(), /cannot reach/i);
});

test("missing feed falls back to the labeled snapshot", async () => {
  await open("nope", { status: 404 });
  assert.equal(await priceOf("5090"), 4930);
  assert.match(await page.locator(".stamp").innerText(), /snapshot/i);
});

test("malformed feed falls back to the labeled snapshot", async () => {
  await open("{ not json");
  assert.equal(await priceOf("5090"), 4930);
  assert.match(await page.locator(".stamp").innerText(), /snapshot/i);
});

test("a feed several days old is labeled stale", async () => {
  await open(feed({ generated_at: "2026-01-01" }));
  assert.match(await page.locator(".stamp").innerText(), /days ago/i);
});

test("bad price values are ignored", async () => {
  const bad = feed();
  bad.items["5090"].price = "<img src=x onerror=alert(1)>";
  bad.items["3090"] = { price: -5, as_of: "2026-11-05", sources: [], hist: [] };
  await open(bad);
  assert.equal(await priceOf("5090"), 4930);
  assert.equal(await priceOf("3090"), 1463);
});

test("hostile feed text renders as text, never as HTML", async () => {
  const hostile = feed();
  hostile.items["5090"].as_of = "<img src=x onerror=alert(1)>";
  hostile.items["5090"].sources = [
    { name: "<img src=x onerror=alert(2)>", url: "https://www.newegg.com/p/1" },
  ];
  hostile.held = [{ id: "5090", reason: "<img src=x onerror=alert(3)>" }];
  hostile.signal.items = {
    5090: {
      used: {
        median: 3000, low: 2900, high: 3100, n: 3, last_seen: "<img src=x onerror=alert(4)>",
        samples: [{ price: 3000, date: "2026-11-01", url: "https://www.reddit.com/r/x/comments/a/" }],
      },
    },
  };
  await open(hostile);
  assert.equal(await page.locator("#v-prices img, #ticker img").count(), 0);
});

test("non-https source links are not rendered as links", async () => {
  const hostile = feed();
  hostile.items["5090"].sources = [{ name: "Click", url: "javascript:alert(1)" }];
  await open(hostile);
  assert.equal(await page.locator('a[href^="javascript"]').count(), 0);
});

test("non-Reddit signal sample links are not rendered", async () => {
  const hostile = feed();
  hostile.signal.items = {
    5090: { used: { median: 3000, low: 2900, high: 3100, n: 1, last_seen: "2026-11-01",
      samples: [{ price: 3000, date: "2026-11-01", url: "https://evil.example/x" }] } },
  };
  await open(hostile);
  assert.equal(await page.locator('a[href*="evil.example"]').count(), 0);
});

test("snapshot row shows the item's date and links its source", async () => {
  await open(feed());
  const row = page.locator("#snap tr", { hasText: "RTX 5090" });
  assert.match(await row.innerText(), /2026-11-05/);
  assert.equal(await row.locator('a[href="https://www.newegg.com/p/1"]').count(), 1);
});

test("used-market signal shows median, range and sample count", async () => {
  const withSignal = feed();
  withSignal.signal.items = {
    3090: { used: { median: 1500, low: 1400, high: 1900, n: 3, last_seen: "2026-11-04",
      samples: [{ price: 1500, date: "2026-11-04", url: "https://www.reddit.com/r/hardwareswap/comments/a/" }] } },
  };
  await open(withSignal);
  const text = await page.locator("#signal").innerText();
  assert.match(text, /RTX 3090/);
  assert.match(text, /\$1,500/);
  assert.match(text, /\$1,400.*\$1,900/);
  assert.match(text, /asking/i);
  assert.equal(await page.locator('#signal a[href^="https://www.reddit.com/r/hardwareswap"]').count(), 1);
});

test("signal never changes the headline price", async () => {
  const withSignal = feed();
  withSignal.signal.items = {
    3090: { used: { median: 999, low: 999, high: 999, n: 1, last_seen: "2026-11-04", samples: [] } },
  };
  await open(withSignal);
  assert.equal(await priceOf("3090"), 1463);
});

test("empty signal says it has no samples yet", async () => {
  await open(feed());
  assert.match(await page.locator("#signal").innerText(), /no .*samples/i);
});

test("held-back items are shown with their reason", async () => {
  await open(feed({ held: [{ id: "5090", reason: "45% move needs 2 hosts agreeing, found 1" }] }));
  assert.match(await page.locator("#held").innerText(), /RTX 5090.*45% move/s);
});

test("a saved price edit survives the feed, other items still follow it", async () => {
  await open(feed(), { stored: JSON.stringify({ 5090: { price: 2500 } }) });
  assert.equal(await priceOf("5090"), 2500);
  const untouched = feed();
  untouched.items["3090"] = { price: 1600, as_of: "2026-11-05", sources: [], hist: [["2026-11", 1600]] };
  await page.unroute("**/prices.json*");
  await open(untouched);
  assert.equal(await priceOf("3090"), 1600);
  assert.equal(await priceOf("5090"), 2500);
});

test("editing one price does not freeze the other items at today's feed", async () => {
  await open(feed());
  await page.goto(base + "/index.html#hardware");
  await page.waitForFunction(() => window.feedState === "live");
  const input = page.locator('#spec input[data-id="5090"][data-f="price"]');
  await input.fill("2500");
  await input.dispatchEvent("input");
  const stored = JSON.parse(await page.evaluate(() => localStorage.getItem("vramforge_edits")));
  assert.deepEqual(Object.keys(stored), ["5090"]);
  assert.deepEqual(Object.keys(stored["5090"]), ["price"]);
});

test("reset returns to the feed price, not the old snapshot", async () => {
  await open(feed(), { stored: JSON.stringify({ 5090: { price: 2500 } }), hash: "#hardware" });
  await page.locator("#resetHW").click();
  assert.equal(await priceOf("5090"), 3333);
});

test("chart reaches the newest feed month past the old fixed axis", async () => {
  const later = feed();
  later.items["5090"].hist = [["2025-01", 1999], ["2027-03", 3333]];
  await open(later);
  await page.evaluate(() => {
    sel.clear();
    sel.add("5090");
    drawChart(1);
  });
  const newest = await page.evaluate(() => hoverPts[hoverPts.length - 1]);
  const width = await page.evaluate(() => CR.width);
  assert.ok(newest.x <= width, `newest point at x=${newest.x} is past the chart width ${width}`);
  assert.equal(newest.v, 3333);
});
