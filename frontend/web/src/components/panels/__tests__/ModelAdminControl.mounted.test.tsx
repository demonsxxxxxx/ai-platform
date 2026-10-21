import assert from "node:assert/strict";
import test, { after } from "node:test";
import { installSelectTestDom } from "../../common/__tests__/selectTestDom.ts";
import { createLobeTestRoot } from "../../common/__tests__/lobeUiTestRoot.tsx";
import { modelAdminApi } from "../../../services/api/modelAdmin.ts";
import type { AdminModelEntry, AdminModelState } from "../../../services/api/modelAdmin.ts";
import type { ModelAdminControlState } from "../ModelAdminControl.tsx";
import { ApiRequestError } from "../../../services/api/fetch.ts";

const dom = installSelectTestDom();
after(() => dom.close());

type ReactModule = typeof import("react");
type QueryContainer = HTMLElement;
type InputElement = HTMLInputElement;

type ModelOverrides = Partial<AdminModelEntry>;

function model(overrides: ModelOverrides = {}): AdminModelEntry {
  return {
    id: "mdl_gpt",
    value: "openai/gpt-5",
    label: "GPT-5",
    provider: "compatible",
    enabled: false,
    available: true,
    is_default: false,
    order: 1,
    access_mode: "all" as const,
    allowed_user_ids: [],
    last_seen_revision: 1,
    last_seen_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function state(overrides: Partial<AdminModelState> = {}): AdminModelState {
  return {
    connection: {
      configured: false,
      revision: null,
      base_url: "",
      key_fingerprint: "",
    },
    models: [],
    ...overrides,
  };
}

function inputByLabel(container: QueryContainer, label: string): InputElement {
  const input = Array.from(container
    .querySelectorAll("input"))
    .find((candidate) => candidate.getAttribute("aria-label") === label);
  assert.ok(input, `expected input ${label}`);
  return input as unknown as InputElement;
}

function buttonByLabel(container: QueryContainer, label: string): HTMLElement {
  const button = Array.from(container.querySelectorAll("button, [role=radio]")).find((node) => node.getAttribute("aria-label") === label);
  assert.ok(button, `expected control ${label}`);
  return button as HTMLElement;
}

function selectByLabel(container: QueryContainer, label: string): HTMLElement {
  const fieldLabel = Array.from(container.querySelectorAll("label[for]")).find((node) => node.textContent === label);
  const select = document.getElementById(fieldLabel?.getAttribute("for") ?? "");
  assert.ok(select, `expected select ${label}`);
  return select;
}

async function changeMountedSelect(React: ReactModule, trigger: HTMLElement, label: string) {
  await React.act(async () => { trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })); });
  const option = Array.from(document.querySelectorAll<HTMLElement>("[role=option]")).find((node) => node.textContent?.trim() === label);
  assert.ok(option, `expected option ${label}`);
  await React.act(async () => {
    option.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, pointerType: "mouse", button: 0 }));
    option.dispatchEvent(new PointerEvent("click", { bubbles: true, pointerType: "mouse", detail: 1 }));
  });
}

function changeMountedInput(input: InputElement, value: string): void {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function nodeText(node: unknown): string {
  const candidate = node as {
    childNodes?: unknown[];
    data?: unknown;
    textContent?: unknown;
  };
  if (typeof candidate.data === "string") return candidate.data;
  if (typeof candidate.textContent === "string" && candidate.textContent) {
    return candidate.textContent;
  }
  return (candidate.childNodes ?? []).map(nodeText).join("");
}

function renderedParagraphText(container: QueryContainer): string {
  return Array.from(container.querySelectorAll("p")).map(nodeText).join(" ");
}

async function waitFor(
  React: ReactModule,
  predicate: () => boolean,
  description: string,
): Promise<void> {
  for (let attempt = 0; attempt < 40 && !predicate(); attempt += 1) {
    await React.act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
  assert.ok(predicate(), description);
}

test("Model admin discovery is a draft and only publication changes the active catalog", async () => {
  const React = await import("react");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");

  const original = {
    get: modelAdminApi.get,
    discover: modelAdminApi.discover,
    publish: modelAdminApi.publish,
  };
  const controlStates: ModelAdminControlState[] = [];
  const onStateChange = (next: ModelAdminControlState) => controlStates.push(next);
  const calls = {
    get: 0,
    discover: [] as Array<{ baseUrl: string; credential?: string }>,
    publish: [] as Array<{
      baseUrl: string; credential?: string; expectedRevision: number | null;
      models: AdminModelEntry[];
    }>,
  };
  const candidate = model({ label: "GPT-5" });
  const published = state({
    connection: {
      configured: true,
      revision: 4,
      base_url: "https://gateway.example",
      key_fingerprint: "0123456789abcdef",
    },
    models: [model({
      enabled: true, is_default: true,
      max_input_tokens: 32000, max_output_tokens: 2048,
      last_seen_revision: 4,
    })],
  });

  modelAdminApi.get = async () => {
    calls.get += 1;
    return published;
  };
  modelAdminApi.discover = async (baseUrl, credential) => {
    calls.discover.push({ baseUrl, credential });
    return {
      connection: {
        configured: true,
        revision: 4,
        base_url: "https://gateway.example",
        key_fingerprint: "fedcba9876543210",
      },
      base_url: baseUrl,
      models: [candidate],
    };
  };
  modelAdminApi.publish = async (baseUrl, credential, expectedRevision, models) => {
    calls.publish.push({ baseUrl, credential, expectedRevision, models });
    return published;
  };

  const container = dom.document.createElement("div");
  const root = await createLobeTestRoot(container);
  try {
    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, {
        canManage: false,
        onStateChange,
      }));
    });
    assert.equal(container.querySelectorAll("[data-model-admin-control]").length, 0);
    assert.equal(calls.get, 0, "non-admin projection must not call the admin API");

    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, {
        canManage: true,
        onStateChange,
      }));
    });
    await waitFor(
      React,
      () => container.querySelectorAll("[data-model-admin-control]").length === 1,
      "admin control should mount after the initial projection loads",
    );
    assert.equal(calls.get, 1);
    assert.deepEqual(controlStates, ["loading", "ready"]);
    assert.match(
      container.querySelectorAll("[data-model-admin-control]")[0].getAttribute("class") ?? "",
      /p-4/,
      "admin controls should keep page-edge padding",
    );
    assert.doesNotMatch(
      nodeText(container),
      /全员模型配置|先获取候选模型|公开模型投影|管理写操作/,
      "admin view should omit explanatory and duplicate catalog surfaces",
    );

    const publishButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.getAttribute("data-model-admin-publish") !== null);
    assert.ok(publishButton);
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输入 Token"), "64000");
    });
    await React.act(async () => {
      publishButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.publish.length === 1,
      "a loaded published catalog should accept token edits without redundant discovery",
    );
    assert.equal(calls.discover.length, 0);
    assert.equal(calls.publish[0].expectedRevision, 4);
    assert.equal(calls.publish[0].credential, undefined);
    assert.equal(calls.publish[0].models[0].max_input_tokens, 64000);

    const keyInput = inputByLabel(container, "模型 API Key");
    await React.act(async () => {
      changeMountedInput(keyInput, "super-secret-key");
    });
    const publishPropsKey = Object.keys(publishButton)
      .find((key) => key.startsWith("__reactProps$"));
    assert.ok(publishPropsKey);
    const publishProps = (publishButton as unknown as Record<string, unknown>)[publishPropsKey] as {
      disabled?: boolean;
    };
    assert.equal(
      publishProps.disabled,
      true,
      "changing the connection must require discovery before publication",
    );
    const discoverButton = Array.from(container.querySelectorAll("button"))
      .find((button) => button.getAttribute("data-model-admin-discover") !== null);
    assert.ok(discoverButton);
    await React.act(async () => {
      discoverButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.discover.length === 1
        && inputByLabel(container, "openai/gpt-5 平台显示名称").value === "GPT-5",
      "discovery should populate only the editable draft",
    );
    assert.deepEqual(calls.discover, [
      { baseUrl: "https://gateway.example", credential: "super-secret-key" },
    ]);
    assert.equal(calls.publish.length, 1);
    assert.equal(inputByLabel(container, "模型 API Key").value, "super-secret-key");

    // Discovery keeps existing selections and user edits for the same model.
    assert.equal(inputByLabel(container, "openai/gpt-5 最大输入 Token").value, "32000");
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输入 Token"), "");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "");
      buttonByLabel(container, "启用 GPT-5").click();
    });
    const statusSelect = selectByLabel(container, "筛选模型状态");
    await changeMountedSelect(React, statusSelect, "状态：上游缺失");
    assert.match(nodeText(container), /没有匹配的模型/);
    await changeMountedSelect(React, statusSelect, "状态：全部");

    const actionBar = container.querySelectorAll("[data-model-admin-action-bar]")[0];
    assert.ok(actionBar);
    assert.match(nodeText(actionBar), /已启用 0 \/ 1/);
    await React.act(async () => { buttonByLabel(container, "启用 GPT-5").click(); });
    await React.act(async () => { buttonByLabel(container, "设为默认 GPT-5").click(); });
    assert.match(nodeText(actionBar), /已启用 1 \/ 1/);
    for (const label of ["openai/gpt-5 最大输入 Token", "openai/gpt-5 最大输出 Token"]) {
      const field = Array.from(container.querySelectorAll("input"))
        .find((candidate) => candidate.getAttribute("aria-label") === label);
      assert.equal(field?.getAttribute("placeholder"), "必填");
      assert.equal(field?.getAttribute("aria-required"), "true");
    }
    await React.act(async () => {
      publishButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    assert.equal(calls.publish.length, 1, "missing limits must not reach the publish API");
    assert.match(nodeText(actionBar), /请填写 GPT-5 的最大输入和输出 Token/);
    assert.equal(container.querySelectorAll('[role="alert"]').length, 1);
    await React.act(async () => {
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输入 Token"), "32000");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "2048");
    });
    assert.equal(container.querySelectorAll('[role="alert"]').length, 0);
    assert.match(nodeText(actionBar), /已启用 1 \/ 1/);
    assert.match(nodeText(actionBar), /有未保存的修改/);
    assert.match(nodeText(publishButton), /保存并生效/);
    await React.act(async () => {
      publishButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.publish.length === 2 && inputByLabel(container, "模型 API Key").value === "",
      "publication should apply the whole draft and clear the write-only key",
    );
    assert.equal(calls.publish[1].expectedRevision, 4);
    assert.equal(calls.publish[1].models[0].enabled, true);
    assert.equal(calls.publish[1].models[0].is_default, true);
    assert.equal(calls.publish[1].models[0].max_input_tokens, 32000);
    assert.equal(calls.publish[1].models[0].max_output_tokens, 2048);
    assert.match(nodeText(container), /已生效配置/);
    assert.doesNotMatch(renderedParagraphText(container), /super-secret-key/);

    modelAdminApi.discover = async () => {
      throw new ApiRequestError(
        "加载会话失败",
        422,
        "model_connection_endpoint_forbidden",
      );
    };
    await React.act(async () => {
      discoverButton.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => renderedParagraphText(container).includes("该 API 地址未获平台网络策略授权。"),
      "model errors should use model-admin copy",
    );
    assert.doesNotMatch(renderedParagraphText(container), /加载会话失败/);

    modelAdminApi.get = async () => {
      throw new ApiRequestError("模型服务不可用", 503);
    };
    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, {
        canManage: false,
        onStateChange,
      }));
    });
    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, {
        canManage: true,
        onStateChange,
      }));
    });
    await waitFor(
      React,
      () => controlStates[controlStates.length - 1] === "degraded",
      "admin control should report a degraded initial projection",
    );
    assert.deepEqual(controlStates.slice(-2), ["loading", "degraded"]);
  } finally {
    await React.act(async () => {
      root.unmount();
    });
    modelAdminApi.get = original.get;
    modelAdminApi.discover = original.discover;
    modelAdminApi.publish = original.publish;
  }
});
