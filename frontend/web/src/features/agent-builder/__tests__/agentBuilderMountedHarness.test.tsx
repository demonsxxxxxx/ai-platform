import assert from "node:assert/strict";
import test from "node:test";

import React from "react";
import { installSelectTestDom } from "../../../components/common/__tests__/selectTestDom.ts";

import type { AgentProfileAdminProjection, PublicSkillResponse } from "../../../types";
import type { AgentBuilderWorkbenchCatalog } from "../AgentBuilderWorkbench";

function installDom() {
  const { document } = installSelectTestDom();
  window.matchMedia = () => ({
    matches: false, media: "", onchange: null,
    addEventListener() {}, removeEventListener() {},
    addListener() {}, removeListener() {}, dispatchEvent() { return false; },
  });
  return document;
}

function profile(
  overrides: Partial<AgentProfileAdminProjection> = {},
): AgentProfileAdminProjection {
  return {
    agent_id: "agt_support",
    revision: 4,
    published_revision: null,
    status: "draft",
    name: "支持助手",
    description: "在授权范围内处理企业支持请求。",
    starter_prompts: ["帮我处理支持请求"],
    avatar_ref: "builtin:assistant",
    avatar_seed: "agt-support",
    market_tags: ["支持"],
    visibility: "tenant",
    allowed_department_ids: [],
    allowed_roles: [],
    allowed_user_ids: [],
    instructions: "仅回答公司支持范围内的问题。",
    skill_set: [{ skill_id: "support-skill" }],
    mcp_tool_ids: ["mcp:support:search"],
    content_hash: "b".repeat(64),
    created_at: "2026-08-01T00:00:00Z",
    published_at: null,
    ...overrides,
  };
}

function publicSkill(): PublicSkillResponse {
  return {
    name: "support-skill",
    expected_version: "2026.07.28",
    input_modes: [],
    requires_file: false,
    description: "Handle support requests.",
    tags: [],
    enabled: true,
    source: "manual",
    files: {},
    file_count: 0,
    installed_from: "manual",
    is_published: false,
    marketplace_is_active: false,
  };
}

function catalog(
  overrides: Partial<AgentBuilderWorkbenchCatalog> = {},
): AgentBuilderWorkbenchCatalog {
  return {
    skills: [publicSkill()],
    tools: [
      {
        id: "mcp:support:search",
        label: "支持知识检索",
        description: "检索授权支持知识。",
      },
    ],
    skillsResolved: true,
    mcpToolsResolved: true,
    effectivePermissionsKnown: true,
    isLoading: false,
    error: null,
    retry: () => {},
    ...overrides,
  };
}

function reactProps(element: Element): Record<string, (...args: never[]) => unknown> {
  const key = Object.keys(element).find((name) => name.startsWith("__reactProps$"));
  assert.ok(key, `React props are attached to <${element.tagName}>`);
  return (element as unknown as Record<string, Record<string, (...args: never[]) => unknown>>)[key];
}

function findButton(container: HTMLElement, text: string): HTMLButtonElement {
  const button = Array.from(container.querySelectorAll("button")).find((entry) => (entry.textContent ?? "") === text);
  assert.ok(button, `button ${text} should render`);
  return button;
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test("mounted workbench hydrates, refreshes, and creates only an explicit local form", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  const responses = [
    [
      profile(),
      ...Array.from(
        { length: 20 },
        (_, index) => profile({
          agent_id: `agt_support_${index + 2}`,
          name: `支持助手 ${index + 2}`,
        }),
      ),
    ],
    [profile({ revision: 5, name: "支持助手新版" })],
  ];
  let listCalls = 0;
  let catalogRetryCalls = 0;
  agentProfileApi.listAdmin = async () => {
    listCalls += 1;
    return { agent_profiles: responses.shift() ?? [] };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog({ retry: () => { catalogRetryCalls += 1; } }),
        canManageProfiles: true,
      }));
      await flush();
    });
    assert.equal(listCalls, 1);
    assert.match((container.textContent ?? ""), /支持助手/);
    const nameInput = container.querySelector<HTMLInputElement>('[aria-label="专家名称"]');
    const descriptionInput = container.querySelector<HTMLInputElement>('[aria-label="专家说明"]');
    const instructionsInput = container.querySelector<HTMLInputElement>('[aria-label="Agent.md 初始指令"]');
    assert.equal(nameInput?.value, "支持助手");
    assert.ok(descriptionInput);
    assert.ok(instructionsInput);
    assert.equal((reactProps(descriptionInput) as unknown as { value: string }).value, "在授权范围内处理企业支持请求。");
    assert.equal((reactProps(instructionsInput) as unknown as { value: string }).value, "仅回答公司支持范围内的问题。");
    assert.equal(container.querySelector('[aria-label="专家模型"]'), null);
    assert.match((container.textContent ?? ""), /support-skill/);
    assert.doesNotMatch((container.textContent ?? ""), /support-skill2026\.07\.28/);
    assert.match((container.textContent ?? ""), /支持知识检索/);

    const pageTwo = findButton(container, "2");
    await React.act(async () => {
      await reactProps(pageTwo).onClick?.({} as never);
      await Promise.resolve();
    });
    const directoryProfiles = Array.from(container.querySelectorAll("button")).filter(
      (button) => button.getAttribute("aria-label")?.startsWith("编辑专家 "),
    );
    assert.equal(directoryProfiles.length, 1);
    assert.match(directoryProfiles[0].getAttribute("aria-label") ?? "", /支持助手 21/);

    const refreshButton = container.querySelector('[aria-label="刷新专家与授权目录"]');
    assert.ok(refreshButton);
    await React.act(async () => {
      await reactProps(refreshButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(listCalls, 2);
    assert.equal(catalogRetryCalls, 1);
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "支持助手新版");

    await React.act(async () => {
      await reactProps(findButton(container, "新建专家")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "");
    assert.match((container.textContent ?? ""), /本地未保存/);
    assert.equal((container.textContent ?? "").includes("local-draft-1"), false);
    assert.equal((container.textContent ?? "").includes("local-draft-2"), false);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted delete confirms retained evidence and removes a draft profile", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  const retireCalls: unknown[][] = [];
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  agentProfileApi.retire = async (...args) => {
    retireCalls.push(args);
    return { agent_id: "agt_support", audit_id: "audit-retire" };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });

    await React.act(async () => {
      await reactProps(findButton(container, "删除")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match((document.body.textContent ?? ""), /专家 ID 不可复用/);
    assert.match((document.body.textContent ?? ""), /历史运行、会话和审计记录仍会保留/);
    const confirmation = document.body.querySelector('[role="dialog"]');
    assert.equal(
      confirmation?.getAttribute("aria-describedby"),
      "agent-profile-retire-warning",
    );
    assert.ok(document.body.querySelector('[id="agent-profile-retire-warning"]'));

    await React.act(async () => {
      await reactProps(findButton(document.body, "确认删除")).onClick?.({} as never);
      await flush();
    });

    assert.deepEqual(retireCalls, [["agt_support", 4]]);
    assert.match((container.textContent ?? ""), /当前没有服务端专家/);
    assert.match((container.textContent ?? ""), /专家已删除，历史运行和审计记录仍保留/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted starter prompts preserve separators while editing and normalize on blur", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });

    const starterPrompts = container.querySelector<HTMLTextAreaElement>('[aria-label="启动问题（可选）"]');
    assert.ok(starterPrompts);
    await React.act(async () => {
      reactProps(starterPrompts).onFocus?.({ target: starterPrompts } as never);
      starterPrompts.value = "问题一,问题二";
      reactProps(starterPrompts).onChange?.({ target: starterPrompts } as never);
      await Promise.resolve();
    });
    assert.equal(starterPrompts.value, "问题一,问题二");

    await React.act(async () => {
      reactProps(starterPrompts).onBlur?.({ target: starterPrompts } as never);
      await Promise.resolve();
    });
    assert.equal(starterPrompts.value, "问题一\n问题二");
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("market tag combobox shows existing tags, filters them, and accepts custom input", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({
    agent_profiles: [
      profile({ market_tags: ["客户支持"] }),
      profile({
        agent_id: "agt_hr",
        market_tags: ["人力资源"],
        name: "人事助手",
      }),
    ],
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });

    const marketTag = container.querySelector<HTMLInputElement>('[aria-label="市场标签"]');
    assert.ok(marketTag);
    await React.act(async () => {
      reactProps(marketTag).onFocus?.({ target: marketTag } as never);
      await Promise.resolve();
    });
    const options = () => Array.from(container.querySelector('[role="listbox"]')?.querySelectorAll('[role="option"]') ?? []);
    assert.deepEqual(
      options().map((option) => (option.textContent ?? "")),
      ["人力资源"],
    );

    await React.act(async () => {
      marketTag.value = "人力";
      reactProps(marketTag).onChange?.({ target: marketTag } as never);
      await Promise.resolve();
    });
    assert.deepEqual(options().map((option) => (option.textContent ?? "")), ["人力资源"]);

    await React.act(async () => {
      reactProps(marketTag).onKeyDown?.({
        key: "ArrowDown",
        preventDefault() {},
      } as never);
      await Promise.resolve();
    });
    await React.act(async () => {
      reactProps(marketTag).onKeyDown?.({
        key: "Enter",
        preventDefault() {},
      } as never);
      await Promise.resolve();
    });
    assert.equal(marketTag.value, "");
    assert.ok(container.querySelector('[aria-label="移除标签 人力资源"]'));

    await React.act(async () => {
      marketTag.value = "自定义能力";
      reactProps(marketTag).onChange?.({ target: marketTag } as never);
      await Promise.resolve();
    });
    assert.equal(marketTag.value, "自定义能力");
    assert.match(container.querySelector('[role="listbox"]')?.textContent ?? "", /按回车添加/);
    await React.act(async () => {
      reactProps(marketTag).onKeyDown?.({
        key: "Enter",
        preventDefault() {},
      } as never);
      await Promise.resolve();
    });
    assert.equal(marketTag.value, "");
    assert.ok(container.querySelector('[aria-label="移除标签 自定义能力"]'));
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted edit disables publish until save materializes a revision, then adopts publish status", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  const saveCalls: unknown[][] = [];
  const publishCalls: unknown[][] = [];
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  agentProfileApi.saveDraft = async (...args) => {
    saveCalls.push(args);
    return {
      agent_profile: profile({ revision: 5, name: "支持助手已编辑" }),
      audit_id: "audit-save",
    };
  };
  agentProfileApi.publish = async (...args) => {
    publishCalls.push(args);
    return {
      agent_profile: profile({ revision: 6, status: "published", name: "支持助手已编辑" }),
      audit_id: "audit-publish",
    };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector<HTMLInputElement>('[aria-label="专家名称"]');
    assert.ok(nameInput);
    await React.act(async () => {
      nameInput.value = "支持助手已编辑";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });
    const publishBeforeSave = findButton(container, "发布");
    assert.equal(publishBeforeSave.disabled || publishBeforeSave.hasAttribute("disabled"), true);
    assert.match((container.textContent ?? ""), /有未保存的更改/);

    const saveButton = findButton(container, "保存草稿");
    assert.equal(saveButton.disabled || saveButton.hasAttribute("disabled"), false);
    await React.act(async () => {
      await reactProps(saveButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveCalls.length, 1);
    assert.equal((saveCalls[0][0] as { expected_draft_revision: number }).expected_draft_revision, 4);
    assert.equal(saveCalls[0][1], "agt_support");
    assert.match((container.textContent ?? ""), /草稿已保存，当前发布版本未改变/);
    const publishAfterSave = findButton(container, "发布");
    assert.equal(publishAfterSave.disabled || publishAfterSave.hasAttribute("disabled"), false);

    await React.act(async () => {
      await reactProps(publishAfterSave).onClick?.({} as never);
      await flush();
    });
    assert.deepEqual(publishCalls, [["agt_support", 5]]);
    assert.match((container.textContent ?? ""), /已发布/);
    assert.match((container.textContent ?? ""), /发布成功，发布版本已更新/);
    const publishedButton = findButton(container, "发布");
    assert.equal(publishedButton.disabled || publishedButton.hasAttribute("disabled"), true);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted dirty editor requires confirmation before switching profiles", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({
    agent_profiles: [
      profile(),
      profile({ agent_id: "agt_other", name: "其他助手" }),
    ],
  });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector<HTMLInputElement>('[aria-label="专家名称"]');
    const otherProfile = container.querySelector('[aria-label="编辑专家 其他助手，草稿"]');
    assert.ok(nameInput);
    assert.ok(otherProfile);
    await React.act(async () => {
      nameInput.value = "未保存名称";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });
    await React.act(async () => {
      await reactProps(otherProfile).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match((document.body.textContent ?? ""), /放弃未保存更改/);
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "未保存名称");

    await React.act(async () => {
      await reactProps(findButton(document.body, "继续编辑")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.doesNotMatch((document.body.textContent ?? ""), /放弃未保存更改/);
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "未保存名称");

    const otherProfileAfterCancel = container.querySelector('[aria-label="编辑专家 其他助手，草稿"]');
    assert.ok(otherProfileAfterCancel);
    await React.act(async () => {
      await reactProps(otherProfileAfterCancel).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match((document.body.textContent ?? ""), /放弃未保存更改/);
    await React.act(async () => {
      await reactProps(findButton(document.body, "放弃并切换")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "其他助手");
    await React.act(async () => {
      await reactProps(findButton(container, "新建专家")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "");
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted unresolved catalogs preserve server Skill names without stale or empty claims", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  agentProfileApi.listAdmin = async () => ({ agent_profiles: [profile()] });
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    const unresolved = catalog({
      skills: [],
      tools: [],
      skillsResolved: false,
      mcpToolsResolved: false,
      effectivePermissionsKnown: false,
      isLoading: true,
    });
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: unresolved,
        canManageProfiles: true,
      }));
      await flush();
    });

    assert.match((container.textContent ?? ""), /support-skill/);
    assert.doesNotMatch((container.textContent ?? ""), /support-skill2026\.07\.28/);
    assert.match((container.textContent ?? ""), /已保留服务端工具身份/);
    assert.doesNotMatch((container.textContent ?? ""), /当前不可用|没有这一精确版本|需要明确移除/);

    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog({ skills: [], tools: [] }),
        canManageProfiles: true,
      }));
      await Promise.resolve();
    });
    assert.match((container.textContent ?? ""), /当前授权目录中不可用/);
    assert.match((container.textContent ?? ""), /需要明确移除/);
    const saveButton = findButton(container, "保存草稿");
    const publishButton = findButton(container, "发布");
    assert.equal(saveButton.disabled || saveButton.hasAttribute("disabled"), true);
    assert.equal(publishButton.disabled || publishButton.hasAttribute("disabled"), true);
    const removeTool = container.querySelector('[aria-label="移除 MCP 工具 mcp:support:search"]');
    assert.ok(removeTool);
    await React.act(async () => {
      await reactProps(removeTool).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.doesNotMatch((container.textContent ?? ""), /需要明确移除/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted save conflict is safe, explicitly recoverable, and retryable", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { ApiRequestError } = await import("../../../services/api/fetch.ts");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  let saveAttempts = 0;
  let listCalls = 0;
  agentProfileApi.listAdmin = async () => {
    listCalls += 1;
    return {
      agent_profiles: [listCalls === 1
        ? profile()
        : profile({ revision: 5, name: "服务端最新名称" })],
    };
  };
  agentProfileApi.saveDraft = async (draft) => {
    saveAttempts += 1;
    if (saveAttempts === 1) {
      throw new ApiRequestError(
        "raw database and private payload",
        409,
        "agent_profile_revision_stale",
      );
    }
    return {
      agent_profile: profile({ revision: 6, name: draft.name }),
      audit_id: "audit-save-retry",
    };
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await flush();
    });
    const nameInput = container.querySelector<HTMLInputElement>('[aria-label="专家名称"]');
    assert.ok(nameInput);
    await React.act(async () => {
      nameInput.value = "重试后的名称";
      reactProps(nameInput).onChange?.({ target: nameInput } as never);
      await Promise.resolve();
    });

    await React.act(async () => {
      await reactProps(findButton(container, "保存草稿")).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveAttempts, 1);
    assert.match((container.textContent ?? ""), /HTTP 409/);
    assert.match((container.textContent ?? ""), /agent_profile_revision_stale/);
    assert.doesNotMatch((container.textContent ?? ""), /raw database|private payload/);
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "重试后的名称");

    await React.act(async () => {
      await reactProps(findButton(container, "加载服务端版本")).onClick?.({} as never);
      await Promise.resolve();
    });
    assert.match((document.body.textContent ?? ""), /放弃本地更改并刷新/);
    await React.act(async () => {
      await reactProps(findButton(document.body, "放弃本地更改并加载服务端版本")).onClick?.({} as never);
      await flush();
    });
    assert.equal(listCalls, 2);
    assert.equal(container.querySelector<HTMLInputElement>('[aria-label="专家名称"]')?.value, "服务端最新名称");
    assert.doesNotMatch((container.textContent ?? ""), /agent_profile_revision_stale/);

    const recoveredNameInput = container.querySelector<HTMLInputElement>('[aria-label="专家名称"]');
    assert.ok(recoveredNameInput);
    await React.act(async () => {
      recoveredNameInput.value = "恢复后再次编辑";
      reactProps(recoveredNameInput).onChange?.({ target: recoveredNameInput } as never);
      await reactProps(findButton(container, "保存草稿")).onClick?.({} as never);
      await flush();
    });
    assert.equal(saveAttempts, 2);
    assert.match((container.textContent ?? ""), /草稿已保存，当前发布版本未改变/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});

test("mounted loading, empty, error, and non-admin states are explicit and safe", async () => {
  const document = installDom();
  const ReactDOM = await import("react-dom/client");
  const { ApiRequestError } = await import("../../../services/api/fetch.ts");
  const { agentProfileApi } = await import("../../../services/api/agentProfile.ts");
  const { AgentBuilderWorkbench } = await import("../AgentBuilderWorkbench.tsx");
  const originals = { ...agentProfileApi };
  let resolveList: ((value: { agent_profiles: AgentProfileAdminProjection[] }) => void) | undefined;
  let mode: "pending" | "error" = "pending";
  let calls = 0;
  agentProfileApi.listAdmin = () => {
    calls += 1;
    if (mode === "error") {
      return Promise.reject(new ApiRequestError("raw private payload", 409, "agent_profile_revision_stale"));
    }
    return new Promise((resolve) => {
      resolveList = resolve;
    });
  };
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root = ReactDOM.createRoot(container as never);
  try {
    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: true,
      }));
      await Promise.resolve();
    });
    assert.match((container.textContent ?? ""), /正在加载专家/);
    await React.act(async () => {
      resolveList?.({ agent_profiles: [] });
      await flush();
    });
    assert.match((container.textContent ?? ""), /当前没有服务端专家/);

    mode = "error";
    const refreshButton = container.querySelector('[aria-label="刷新专家与授权目录"]');
    assert.ok(refreshButton);
    await React.act(async () => {
      await reactProps(refreshButton).onClick?.({} as never);
      await flush();
    });
    assert.equal(calls, 2);
    assert.match((container.textContent ?? ""), /HTTP 409/);
    assert.match((container.textContent ?? ""), /agent_profile_revision_stale/);
    assert.doesNotMatch((container.textContent ?? ""), /raw private payload/);

    await React.act(async () => {
      root.render(React.createElement(AgentBuilderWorkbench, {
        catalog: catalog(),
        canManageProfiles: false,
      }));
      await Promise.resolve();
    });
    assert.ok(container.querySelector("[data-agent-builder-access-denied]"));
    assert.match((container.textContent ?? ""), /仅管理员可访问/);
  } finally {
    Object.assign(agentProfileApi, originals);
    await React.act(async () => root.unmount());
  }
});
