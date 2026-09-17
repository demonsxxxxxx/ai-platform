import assert from "node:assert/strict";
import test from "node:test";
import { installTestDom } from "../../../hooks/useAgent/__tests__/testDom.ts";
import { modelAdminApi } from "../../../services/api/modelAdmin.ts";
import type { AdminModelEntry, AdminModelState } from "../../../services/api/modelAdmin.ts";

const dom = installTestDom();

type ReactModule = typeof import("react");
type QueryNode = {
  childNodes?: unknown[];
  getAttribute(name: string): string | null;
  textContent?: string | null;
};
type QueryContainer = {
  textContent: string | null;
  querySelectorAll(selector: string): Array<QueryNode>;
};
type InputElement = {
  checked: boolean;
  value: string;
  dispatchEvent(event: { type: string; bubbles?: boolean }): boolean;
};

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
  const input = container
    .querySelectorAll("input")
    .find((candidate) => candidate.getAttribute("aria-label") === label);
  assert.ok(input, `expected input ${label}`);
  return input as unknown as InputElement;
}

function changeMountedInput(input: InputElement, value: string): void {
  input.value = value;
  const propsKey = Object.keys(input).find((key) => key.startsWith("__reactProps$"));
  assert.ok(propsKey, "expected mounted React input props");
  const props = (input as unknown as Record<string, unknown>)[propsKey] as {
    onChange?: (event: { target: InputElement }) => void;
  };
  assert.ok(props.onChange, "expected mounted input change handler");
  props.onChange({ target: input });
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
  return container.querySelectorAll("p").map(nodeText).join(" ");
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
  const { createRoot } = await import("react-dom/client");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");

  const original = {
    get: modelAdminApi.get,
    discover: modelAdminApi.discover,
    publish: modelAdminApi.publish,
  };
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
    return state({
      connection: {
        configured: false,
        revision: null,
        base_url: "https://gateway.example",
        key_fingerprint: "",
      },
    });
  };
  modelAdminApi.discover = async (baseUrl, credential) => {
    calls.discover.push({ baseUrl, credential });
    return {
      connection: {
        configured: true,
        revision: 3,
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
  const root = createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, { canManage: false }));
    });
    assert.equal(container.querySelectorAll("[data-model-admin-control]").length, 0);
    assert.equal(calls.get, 0, "non-admin projection must not call the admin API");

    await React.act(async () => {
      root.render(React.createElement(ModelAdminControl, { canManage: true }));
    });
    await waitFor(
      React,
      () => container.querySelectorAll("[data-model-admin-control]").length === 1,
      "admin control should mount after the initial projection loads",
    );
    assert.equal(calls.get, 1);

    const keyInput = inputByLabel(container, "模型 API Key");
    await React.act(async () => {
      changeMountedInput(keyInput, "super-secret-key");
    });
    const discoverButton = container.querySelectorAll("button")
      .find((button) => button.getAttribute("data-model-admin-discover") !== null);
    assert.ok(discoverButton);
    await React.act(async () => {
      discoverButton.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.discover.length === 1
        && inputByLabel(container, "openai/gpt-5 显示名称").value === "GPT-5",
      "discovery should populate only the editable draft",
    );
    assert.deepEqual(calls.discover, [
      { baseUrl: "https://gateway.example", credential: "super-secret-key" },
    ]);
    assert.equal(calls.publish.length, 0);
    assert.equal(inputByLabel(container, "模型 API Key").value, "super-secret-key");

    const enabled = inputByLabel(container, "启用 GPT-5");
    enabled.checked = true;
    const defaultInput = inputByLabel(container, "设为默认 GPT-5");
    defaultInput.checked = true;
    await React.act(async () => {
      changeMountedInput(enabled, enabled.value);
      changeMountedInput(defaultInput, defaultInput.value);
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输入 Token"), "32000");
      changeMountedInput(inputByLabel(container, "openai/gpt-5 最大输出 Token"), "2048");
    });
    const publishButton = container.querySelectorAll("button")
      .find((button) => button.getAttribute("data-model-admin-publish") !== null);
    assert.ok(publishButton);
    await React.act(async () => {
      publishButton.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.publish.length === 1 && inputByLabel(container, "模型 API Key").value === "",
      "publication should apply the whole draft and clear the write-only key",
    );
    assert.equal(calls.publish[0].expectedRevision, 3);
    assert.equal(calls.publish[0].models[0].enabled, true);
    assert.equal(calls.publish[0].models[0].is_default, true);
    assert.equal(calls.publish[0].models[0].max_input_tokens, 32000);
    assert.equal(calls.publish[0].models[0].max_output_tokens, 2048);
    assert.match(renderedParagraphText(container), /当前发布版本 4/);
    assert.doesNotMatch(renderedParagraphText(container), /super-secret-key/);
  } finally {
    await React.act(async () => {
      root.unmount();
    });
    modelAdminApi.get = original.get;
    modelAdminApi.discover = original.discover;
    modelAdminApi.publish = original.publish;
  }
});
