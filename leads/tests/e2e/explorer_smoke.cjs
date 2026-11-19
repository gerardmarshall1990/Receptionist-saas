// Browser smoke test for the lead explorer. Run by tests/test_explorer_e2e.py:
//   node explorer_smoke.cjs <base-url> <screenshot-dir|"">
// Exits non-zero (with a message) on the first failed check or any page error.
const { chromium } = require("playwright");

const base = process.argv[2];
const shots = process.argv[3] || "";

function check(cond, msg) {
  if (!cond) { console.error("FAIL: " + msg); process.exit(1); }
  console.log("ok: " + msg);
}

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: shots ? 2 : 1 });
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  await page.goto(base);
  await page.waitForSelector("#rows tr");
  check((await page.locator("#rows tr").count()) === 9, "list shows all 9 leads");
  check((await page.textContent("#count")).includes("9 leads"), "exact count shown");
  if (shots) await page.screenshot({ path: `${shots}/explorer-list.png` });

  await page.locator("#f-category label", { hasText: "Dental practices" }).locator("input").check();
  await page.waitForFunction(() => document.querySelector("#count").textContent.includes("5 leads"));
  check(page.url().includes("category=dental"), "filter is in the URL");
  check(await page.isVisible("#g-subcategory"), "subcategory filter appears for chosen category");

  await page.locator("#f-flags .chip", { hasText: "Multiple locations" }).click();
  await page.waitForFunction(() => document.querySelector("#count").textContent.includes("3 leads"));
  check(true, "flag filter narrows to the multi-location group");

  await page.click("#reset");
  await page.fill("#q", "100%");
  await page.waitForFunction(() => document.querySelector("#count").textContent.includes("1 leads"));
  check((await page.locator("#rows tr .name").first().textContent()).includes("Bravo"), "search treats % literally");

  await page.click("#reset");
  await page.waitForFunction(() => document.querySelector("#count").textContent.includes("9 leads"));
  await page.locator("#rows tr", { hasText: "Alpha Plumbing" }).click();
  await page.waitForSelector("#d-body .why");
  check((await page.textContent("#d-name")) === "Alpha Plumbing", "detail opens");
  check((await page.textContent(".why")).includes("No evidence collected yet"), "why-this-lead shows honest empty state");
  check((await page.textContent("#d-body")).includes("not exportable"), "source-listed email marked not exportable");
  if (shots) await page.screenshot({ path: `${shots}/explorer-detail.png` });

  // Suppress through the dialog.
  await page.click("text=Suppress (opt-out)…");
  await page.fill("#suppress-reason", "Owner asked not to be called");
  await page.click("#suppress-confirm");
  await page.waitForSelector(".banner-bad");
  check((await page.textContent(".banner-bad")).includes("Never contact"), "suppressed banner shown");
  await page.waitForFunction(() => document.querySelector("#count").textContent.includes("8 leads"));
  check(true, "suppressed lead leaves the default list");

  // Deep link: reload keeps the open lead.
  await page.reload();
  await page.waitForSelector("#d-body .why");
  check((await page.textContent("#d-name")) === "Alpha Plumbing", "lead deep link survives reload");
  await page.keyboard.press("Escape");
  check(await page.isHidden("#detail"), "Esc closes the detail panel");

  // Phone width: no horizontal page scroll.
  const m = await browser.newPage({ viewport: { width: 390, height: 844 } });
  m.on("pageerror", (e) => errors.push(e.message));
  await m.goto(base);
  await m.waitForSelector("#rows tr");
  const width = await m.evaluate(() => document.documentElement.scrollWidth);
  check(width <= 390, `no horizontal scroll on a phone (scrollWidth ${width})`);
  if (shots) await m.screenshot({ path: `${shots}/explorer-mobile.png` });

  check(errors.length === 0, `no page errors ${JSON.stringify(errors)}`);
  await browser.close();
})().catch((e) => { console.error("FAIL: " + e.message); process.exit(1); });
