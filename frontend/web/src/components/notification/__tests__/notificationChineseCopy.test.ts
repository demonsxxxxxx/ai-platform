import assert from "node:assert/strict";
import test from "node:test";

import {
  CHINESE_NOTIFICATION_CONTENT_FALLBACK,
  CHINESE_NOTIFICATION_TITLE_FALLBACK,
  resolveChineseNotificationText,
} from "../notificationChineseCopy.ts";

test("uses Chinese structured notification text when it is available", () => {
  const localized = { zh: "系统维护通知", en: "Maintenance" };

  assert.equal(
    resolveChineseNotificationText(localized),
    "系统维护通知",
  );
});

test("never exposes English structured notification text when Chinese is absent", () => {
  const titleWithoutChinese = { en: "Maintenance" };
  const contentWithoutChinese = { en: "Details" };

  assert.equal(
    resolveChineseNotificationText(titleWithoutChinese),
    CHINESE_NOTIFICATION_TITLE_FALLBACK,
  );
  assert.equal(
    resolveChineseNotificationText(
      contentWithoutChinese,
      CHINESE_NOTIFICATION_CONTENT_FALLBACK,
    ),
    CHINESE_NOTIFICATION_CONTENT_FALLBACK,
  );
});
