// Synthetic follow-up-suggestion evidence: real ChatView/preview fixture in Chrome.
// Every API answer comes from scripts/fixtures/generic-chat-preview.tsx; no model runs.
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import {
  captureScreenshot, findChrome, redact, sleep, startBrowser, withTimeout,
} from "./browser-smoke-harness.mjs";

const baseUrl = (process.env.AI_PLATFORM_FRONTEND_URL || "http://127.0.0.1:5181").replace(/\/+$/, "");
const outputDir = resolve(process.env.AI_PLATFORM_FOLLOW_UP_SMOKE_DIR || "follow-up-smoke");
const timeoutMs = Number(process.env.AI_PLATFORM_FOLLOW_UP_TIMEOUT_MS || 45_000);

const report = { scenarios: [], checks: [], screenshots: [], failures: [] };

function check(name, passed, detail) {
  report.checks.push({ name, passed: Boolean(passed), ...(detail === undefined ? {} : { detail }) });
  if (!passed) report.failures.push(name);
}

async function openPreview({ width, height, theme, mobile = false }) {
  const browser = await startBrowser({
    viewport: { width, height, mobile },
    timeoutMs,
    profilePrefix: "ai-platform-follow-up-smoke-",
  });
  const url = `${baseUrl}/scripts/fixtures/generic-chat-preview.html?theme=${theme}`;
  await browser.client.send("Page.navigate", { url });
  return browser;
}

async function readState(client) {
  return client.evaluate(`(() => {
    const banner = document.querySelector("[data-synthetic-preview-banner]");
    const host = document.querySelector("[data-follow-up-suggestions-host]");
    const buttons = Array.from(document.querySelectorAll("[data-follow-up-suggestion]"));
    const transcript = document.querySelector("[data-chat-transcript]");
    return {
      banner: banner ? banner.textContent : null,
      hostVisible: Boolean(host),
      hostLabel: host ? host.querySelector("p")?.textContent ?? null : null,
      suggestions: buttons.map((button) => button.textContent?.trim() ?? ""),
      suggestionGeometry: buttons.map((button) => {
        const rect = button.getBoundingClientRect();
        const style = getComputedStyle(button);
        return { width: Math.round(rect.width), height: Math.round(rect.height), color: style.color, background: style.backgroundColor };
      }),
      maskImage: host ? getComputedStyle(host.querySelector("[data-follow-up-suggestions]") ?? host).maskImage : null,
      messageCount: transcript ? transcript.querySelectorAll("[data-assistant-ui-message]").length : 0,
      text: transcript ? transcript.innerText.slice(0, 4000) : "",
      loggedFollowUpRequests: (window.__log ?? []).filter((line) => line.includes("follow-up-suggestions")),
      loggedSubmitRequests: (window.__log ?? []).filter((line) => line.includes("/api/chat/stream")),
    };
  })()`);
}

async function sendMessage(client, text) {
  await client.evaluate(`(() => {
    const area = document.querySelector('textarea');
    if (!area) return false;
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
    setter.call(area, ${JSON.stringify(text)});
    area.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  })()`);
  await sleep(150);
  await client.evaluate(`(() => {
    const button = document.querySelector('[data-slot="composer-send"]');
    if (button) { button.click(); return "clicked"; }
    return "missing";
  })()`);
}

async function runScenario({ name, width, height, theme, mobile = false }) {
  const scenario = { name, width, height, theme, steps: [] };
  const browser = await openPreview({ width, height, theme, mobile });
  try {
    const { client } = browser;
    await client.waitFor(`!!document.querySelector("[data-synthetic-preview-banner]")`, "synthetic_banner");
    await client.waitFor(`!!document.querySelector("textarea")`, "composer_ready");
    await client.waitFor(`document.querySelectorAll("[data-assistant-ui-message]").length >= 2`, "restored_history");

    const restored = await readState(client);
    scenario.steps.push({ step: "restored_history", state: restored });
    check(`${name}:restored_history_no_follow_up_request`, restored.loggedFollowUpRequests.length === 0,
      { requests: restored.loggedFollowUpRequests });
    check(`${name}:restored_history_hides_row`, restored.hostVisible === false);
    check(`${name}:synthetic_marker_visible`, (restored.banner ?? "").includes("合成预览"));

    scenario.screenshots = scenario.screenshots ?? [];
    report.screenshots.push(await captureScreenshot(client, outputDir, `${name}-01-restored`));

    await sendMessage(client, "再帮我把清单排成一周计划");
    await client.waitFor(`!!document.querySelector("[data-follow-up-suggestion]")`, "follow_up_row");

    const afterAnswer = await readState(client);
    scenario.steps.push({ step: "after_answer", state: afterAnswer });
    check(`${name}:one_follow_up_request`, afterAnswer.loggedFollowUpRequests.length === 1,
      { requests: afterAnswer.loggedFollowUpRequests });
    check(`${name}:three_suggestions`, afterAnswer.suggestions.length === 3, { suggestions: afterAnswer.suggestions });
    check(`${name}:host_label`, afterAnswer.hostLabel === "后续建议", { label: afterAnswer.hostLabel });
    check(`${name}:row_present`, afterAnswer.hostVisible === true);
    // 40px is the documented minimum touch target; assert it, not a smaller guess.
    check(`${name}:touch_target_geometry`, afterAnswer.suggestionGeometry.every((item) => item.width > 40 && item.height >= 40),
      { geometry: afterAnswer.suggestionGeometry });
    check(`${name}:row_within_viewport`, afterAnswer.suggestionGeometry.every((item) => item.width <= width));
    report.screenshots.push(await captureScreenshot(client, outputDir, `${name}-02-suggestions`));

    // One click sends exactly that question through the existing composer transaction.
    const firstSuggestion = afterAnswer.suggestions[0];
    await client.evaluate(`(() => {
      const button = document.querySelector("[data-follow-up-suggestion]");
      if (!button) return false;
      button.click();
      return true;
    })()`);
    await client.waitFor(
      `(document.querySelector("[data-chat-transcript]")?.innerText ?? "").includes(${JSON.stringify(firstSuggestion)})`,
      "next_turn_arrived",
    );
    const afterClick = await readState(client);
    scenario.steps.push({ step: "after_click", state: afterClick });
    check(`${name}:click_added_one_submit`, afterClick.loggedSubmitRequests.length === 2,
      { submits: afterClick.loggedSubmitRequests });
    check(`${name}:click_sent_suggestion_text`, afterClick.text.includes(firstSuggestion),
      { expectation: firstSuggestion });
    // The same labelled set must not belong to the previous turn any more.
    check(
      `${name}:new_turn_owns_new_suggestions`,
      afterClick.suggestions.length === 3 &&
        afterClick.suggestions.every((item) => item.includes("第3轮")) &&
        afterClick.suggestions.some((item) => item.includes("第3轮")),
      { suggestions: afterClick.suggestions },
    );
    check(
      `${name}:old_suggestions_replaced`,
      !afterClick.suggestions.includes(firstSuggestion),
      { previous: firstSuggestion, current: afterClick.suggestions },
    );
    report.screenshots.push(await captureScreenshot(client, outputDir, `${name}-03-next-turn`));

    // A non-empty draft must hide the row instead of swallowing the user's text.
    await client.waitFor(`!!document.querySelector("[data-follow-up-suggestion]")`, "second_row");
    await client.evaluate(`(() => {
      const area = document.querySelector('textarea');
      const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, "value").set;
      setter.call(area, "我自己的草稿");
      area.dispatchEvent(new Event("input", { bubbles: true }));
      return true;
    })()`);
    await sleep(250);
    const withDraft = await readState(client);
    scenario.steps.push({ step: "draft", state: withDraft });
    check(`${name}:draft_hides_row`, withDraft.hostVisible === false);
    check(`${name}:draft_preserved`, withDraft.text.includes("我自己的草稿") === false,
      "the draft lives in the composer, not the transcript");
    report.screenshots.push(await captureScreenshot(client, outputDir, `${name}-04-draft`));
  } catch (error) {
    report.failures.push(`${name}:${redact(String(error))}`);
    scenario.error = redact(String(error));
    try {
      report.screenshots.push(await captureScreenshot(browser.client, outputDir, `${name}-99-failure`));
    } catch {
      // A failed diagnostic capture is already reported by the scenario failure.
    }
  } finally {
    await browser.close();
    report.scenarios.push(scenario);
  }
}

async function main() {
  if (!findChrome()) {
    console.log("follow_up_smoke=skipped chrome_not_found");
    writeFileSync(join(outputDir, "report.json"), JSON.stringify({ ...report, skipped: "chrome_not_found" }, null, 2));
    process.exitCode = 2;
    return;
  }
  mkdirSync(outputDir, { recursive: true });
  await withTimeout(runScenario({ name: "desktop-light", width: 1440, height: 900, theme: "light" }), 240_000, "desktop-light");
  await withTimeout(runScenario({ name: "desktop-dark", width: 1440, height: 900, theme: "dark" }), 240_000, "desktop-dark");
  await withTimeout(runScenario({ name: "narrow-390", width: 390, height: 844, theme: "light", mobile: true }), 240_000, "narrow-390");

  writeFileSync(join(outputDir, "report.json"), JSON.stringify(report, null, 2));
  console.log(`follow_up_smoke checks=${report.checks.length} failures=${report.failures.length} screenshots=${report.screenshots.length}`);
  for (const failure of report.failures) console.log(`failure: ${failure}`);
  process.exitCode = report.failures.length ? 1 : 0;
}

await main();
