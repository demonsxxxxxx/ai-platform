import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const locales = ["en", "zh", "ja", "ko", "ru"] as const;
const testDirectory = fileURLToPath(new URL(".", import.meta.url));

test("run-control actions and truthful statuses are localized distinctly", () => {
  for (const locale of locales) {
    const contents = JSON.parse(
      readFileSync(resolve(testDirectory, `../locales/${locale}.json`), "utf8"),
    ) as {
      runPlayback?: {
        actions?: Record<string, string>;
        actionStatus?: Record<string, string>;
      };
    };
    const actions = contents.runPlayback?.actions;
    const statuses = contents.runPlayback?.actionStatus;
    assert.ok(actions, `${locale} must provide run-control action names`);
    assert.notEqual(actions.cancel, actions.retryRun, `${locale} cancel and retry differ`);
    assert.notEqual(actions.retryRun, actions.resume, `${locale} retry and resume differ`);
    assert.ok(statuses?.cancelRequested, `${locale} explains cancel acknowledgement`);
    assert.ok(statuses?.unconfirmed, `${locale} explains unknown mutation result`);
    assert.ok(statuses?.createdUnopened, `${locale} explains GET-only reopen`);
  }
});
