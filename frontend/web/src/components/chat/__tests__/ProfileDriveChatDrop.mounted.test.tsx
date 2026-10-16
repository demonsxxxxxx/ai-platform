import assert from "node:assert/strict";
import test from "node:test";

import { installSelectTestDom } from "../../common/__tests__/selectTestDom";
import { PROFILE_DRIVE_DRAG_TYPE } from "../../workbench/profileDriveDrag";

async function flush() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

test("drops a ProfileDrive path into the Composer without treating it as a local upload", async () => {
  const fixture = installSelectTestDom();
  const dom = { window };
  document.body.innerHTML = '<div id="root"></div>';
  const [{ act, createElement }, { createLobeTestRoot }, { ChatInput }, { AuthProvider }] =
    await Promise.all([
      import("react"),
      import("../../common/__tests__/lobeUiTestRoot"),
      import("../ChatInput"),
      import("../../../hooks/useAuth"),
    ]);
  const container = dom.window.document.getElementById("root");
  assert.ok(container);
  const root = await createLobeTestRoot(container);
  const droppedPaths: Array<{ source_id: string; path: string }> = [];

  try {
    await act(async () => {
      root.render(
        createElement(
          AuthProvider,
          null,
          createElement(ChatInput, {
            onSend: async () => ({ status: "accepted" as const }),
            onStop: async () => "unavailable" as const,
            isLoading: false,
            attachments: [],
            onAttachmentsChange: () => undefined,
            acceptedFileTypes: [],
            disableSlashCommands: true,
            tools: [],
            skills: [],
            enableSkills: false,
            agentOptionValues: {},
            availableModels: [],
            onProfileDriveFileDrop: async (reference) => {
              droppedPaths.push(reference);
            },
          }),
        ),
      );
      await flush();
    });

    const composer = container.querySelector<HTMLElement>(
      '[data-disable-global-file-drop="true"]',
    );
    assert.ok(composer);
    const drop = new dom.window.Event("drop", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(drop, "dataTransfer", {
      value: {
        types: [PROFILE_DRIVE_DRAG_TYPE],
        getData: (type: string) =>
          type === PROFILE_DRIVE_DRAG_TYPE
            ? "Documents/report.pdf"
            : "",
      },
    });

    await act(async () => {
      composer.dispatchEvent(drop);
      await flush();
    });

    assert.equal(drop.defaultPrevented, true);
    assert.deepEqual(droppedPaths, [
      { source_id: "profile", path: "Documents/report.pdf" },
    ]);

    const publicDrop = new dom.window.Event("drop", {
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(publicDrop, "dataTransfer", {
      value: {
        types: [PROFILE_DRIVE_DRAG_TYPE],
        getData: (type: string) =>
          type === PROFILE_DRIVE_DRAG_TYPE
            ? JSON.stringify({
                source_id: "public",
                path: "01-研发部/report.pdf",
              })
            : "",
      },
    });
    await act(async () => {
      composer.dispatchEvent(publicDrop);
      await flush();
    });
    assert.deepEqual(droppedPaths[1], {
      source_id: "public",
      path: "01-研发部/report.pdf",
    });
  } finally {
    await act(async () => root.unmount());
    fixture.close();
  }
});
