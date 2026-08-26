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

test("Model admin control gates non-admins and refreshes mounted mutations without rendering the key", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { ModelAdminControl } = await import("../ModelAdminControl.tsx");

  const original = {
    get: modelAdminApi.get,
    configure: modelAdminApi.configure,
    sync: modelAdminApi.sync,
    patch: modelAdminApi.patch,
  };
  const calls = {
    get: 0,
    configure: [] as Array<{ baseUrl: string; credential?: string }>,
    sync: 0,
    patch: [] as Array<{ modelId: string; patch: Record<string, unknown> }>,
  };
  const configured = state({
    connection: {
      configured: true,
      revision: 2,
      base_url: "https://gateway.example",
      key_fingerprint: "0123456789abcdef",
    },
    models: [model({ label: "Configured GPT-5" })],
  });
  const synced = state({
    connection: configured.connection,
    models: [model({ label: "Synced GPT-5", last_seen_revision: 2 })],
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
  modelAdminApi.configure = async (baseUrl, credential) => {
    calls.configure.push({ baseUrl, credential });
    return configured;
  };
  modelAdminApi.sync = async () => {
    calls.sync += 1;
    return synced;
  };
  modelAdminApi.patch = async (modelId, patch) => {
    calls.patch.push({ modelId, patch });
    const current = synced.models[0];
    if (patch.enabled === true) {
      return { ...current, label: "Enabled GPT-5", enabled: true };
    }
    if (patch.is_default === true) {
      return { ...current, label: "Default GPT-5", enabled: true, is_default: true };
    }
    return current;
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
    assert.equal(keyInput.value, "");
    await React.act(async () => {
      changeMountedInput(keyInput, "super-secret-key");
    });
    const configureButton = container
      .querySelectorAll("button")
      .find((button) => button.hasAttribute("data-model-admin-configure"));
    assert.ok(configureButton);
    await React.act(async () => {
      configureButton.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => calls.configure.length === 1 && inputByLabel(container, "模型 API Key").value === "",
      "configure should refresh state and clear the write-only key field",
    );
    assert.deepEqual(calls.configure, [
      { baseUrl: "https://gateway.example", credential: "super-secret-key" },
    ]);
    assert.equal(inputByLabel(container, "模型 API Key").value, "");
    const connectionText = renderedParagraphText(container);
    assert.match(connectionText, /当前 revision 2/);
    assert.match(connectionText, /0123456789abcdef/);
    assert.equal(
      inputByLabel(container, "openai/gpt-5 显示名称").value,
      "Configured GPT-5",
    );
    assert.doesNotMatch(renderedParagraphText(container), /super-secret-key/);

    const syncButton = container
      .querySelectorAll("button")
      .find((button) => button.hasAttribute("data-model-admin-sync"));
    assert.ok(syncButton);
    await React.act(async () => {
      syncButton.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => inputByLabel(container, "openai/gpt-5 显示名称").value === "Synced GPT-5",
      "sync should refresh the mounted catalog projection",
    );
    assert.equal(calls.sync, 1);

    const enabled = inputByLabel(container, "启用 Synced GPT-5");
    enabled.checked = true;
    await React.act(async () => {
      enabled.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => inputByLabel(container, "启用 Enabled GPT-5").checked,
      "enable response should refresh the checked model projection",
    );
    assert.equal(
      inputByLabel(container, "openai/gpt-5 显示名称").value,
      "Enabled GPT-5",
    );
    assert.deepEqual(calls.patch[0], {
      modelId: "mdl_gpt",
      patch: { enabled: true },
    });

    const defaultInput = inputByLabel(container, "设为默认 Enabled GPT-5");
    defaultInput.checked = true;
    await React.act(async () => {
      defaultInput.dispatchEvent({ type: "click", bubbles: true });
      await Promise.resolve();
    });
    await waitFor(
      React,
      () => inputByLabel(container, "设为默认 Default GPT-5").checked,
      "default response should refresh the selected model projection",
    );
    assert.equal(inputByLabel(container, "启用 Default GPT-5").checked, true);
    assert.equal(
      inputByLabel(container, "openai/gpt-5 显示名称").value,
      "Default GPT-5",
    );
    assert.deepEqual(calls.patch[1], {
      modelId: "mdl_gpt",
      patch: { is_default: true },
    });
  } finally {
    await React.act(async () => {
      root.unmount();
    });
    modelAdminApi.get = original.get;
    modelAdminApi.configure = original.configure;
    modelAdminApi.sync = original.sync;
    modelAdminApi.patch = original.patch;
  }
});
