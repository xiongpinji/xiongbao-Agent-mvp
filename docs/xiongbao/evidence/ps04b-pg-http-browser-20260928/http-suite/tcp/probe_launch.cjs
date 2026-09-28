"use strict";
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require(path.resolve(__dirname, "../../../../pw-local/npm-cache/_npx/31e32ef8478fbf80/node_modules/playwright-core"));
const report = { state: "STARTING", error: null, context_closed: false, browser_closed: false };
const output = path.join(__dirname, "launch-normal-environment.json");
if (fs.existsSync(output)) throw new Error("Never overwrite a launch probe result");
async function main() {
  let browser, context;
  try {
    browser = await chromium.launch({ executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, timeout: 20000 });
    context = await browser.newContext();
    const page = await context.newPage();
    await page.goto("about:blank");
    report.chrome_version = browser.version();
    report.url = page.url();
    report.state = "LAUNCHED";
  } catch (error) {
    report.state = "FAILED";
    report.error = String(error.message || error);
  } finally {
    if (context) { await context.close(); report.context_closed = true; }
    if (browser) { await browser.close(); report.browser_closed = true; }
    if (report.state === "LAUNCHED" && report.context_closed && report.browser_closed) report.state = "PASS";
    fs.writeFileSync(output, JSON.stringify(report, null, 2) + "\n", "utf8");
  }
  console.log(JSON.stringify({ state: report.state, chrome_version: report.chrome_version }));
  return report.state === "PASS" ? 0 : 1;
}
main().then((code) => { process.exitCode = code; }).catch((error) => { console.error(String(error)); process.exitCode = 2; });
