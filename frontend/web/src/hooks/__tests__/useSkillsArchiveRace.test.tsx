import assert from "node:assert/strict";
import test from "node:test";

import type { SkillsResponse, UserSkill } from "../../types/skill.ts";
import { skillApi } from "../../services/api/skill.ts";
import { installTestDom } from "../useAgent/__tests__/testDom.ts";

const dom = installTestDom();

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

function userSkill(name: string): UserSkill {
  return {
    skill_name: name,
    expected_version: `version-${name}`,
    input_modes: ["chat"],
    requires_file: false,
    description: name,
    tags: [],
    files: ["SKILL.md"],
    enabled: true,
    file_count: 1,
    installed_from: "marketplace",
    is_published: true,
    marketplace_is_active: true,
  };
}

function catalogResponse(names: string[]): SkillsResponse {
  return {
    skills: names.map(userSkill),
    total: names.length,
    skip: 0,
    limit: 200,
    available_tags: [],
    effective_permissions: ["skill:read", "skill:delete"],
    effective_permissions_known: true,
    catalog_read_resolved: true,
  };
}

test("archive mutations reject stale catalog reads for single and batch results", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { useSkills } = await import("../useSkills.ts");
  type HookSnapshot = ReturnType<typeof useSkills>;

  const originalListAllAuthorized = skillApi.listAllAuthorized;
  const originalDelete = skillApi.delete;
  const originalBatchDelete = skillApi.batchDelete;
  let serverNames = ["skill-a", "skill-b", "skill-c"];
  let heldCatalogRead: ReturnType<typeof deferred<SkillsResponse>> | null = null;
  const singleArchive = deferred<void>();
  const batchArchive = deferred<void>();

  skillApi.listAllAuthorized = async () => {
    if (heldCatalogRead) {
      const held = heldCatalogRead;
      heldCatalogRead = null;
      return held.promise;
    }
    return catalogResponse(serverNames);
  };
  skillApi.delete = async (name) => {
    await singleArchive.promise;
    serverNames = serverNames.filter((candidate) => candidate !== name);
    return { message: "archived" };
  };
  skillApi.batchDelete = async (names) => {
    await batchArchive.promise;
    serverNames = serverNames.filter((candidate) => !names.includes(candidate));
    return { deleted: names, errors: [] };
  };

  const container = dom.document.createElement("div");
  const root = createRoot(container as never);
  let snapshot: HookSnapshot | null = null;

  function Probe() {
    snapshot = useSkills({ allAuthorizedCatalog: true });
    return null;
  }

  const current = () => {
    if (!snapshot) throw new Error("useSkills probe did not render");
    return snapshot;
  };

  try {
    await React.act(async () => {
      root.render(React.createElement(Probe));
    });
    assert.deepEqual(
      current().skills.map((skill) => skill.name),
      ["skill-a", "skill-b", "skill-c"],
    );

    const singleCatalogRead = deferred<SkillsResponse>();
    heldCatalogRead = singleCatalogRead;
    const staleSingleResponse = catalogResponse(serverNames);
    let staleSingleRead!: Promise<boolean>;
    await React.act(async () => {
      staleSingleRead = current().fetchSkills();
      await Promise.resolve();
    });

    let singleResult!: Promise<boolean>;
    await React.act(async () => {
      singleResult = current().deleteSkill("skill-a");
      await Promise.resolve();
    });
    assert.equal(current().skills.some((skill) => skill.name === "skill-a"), false);

    await React.act(async () => {
      singleCatalogRead.resolve(staleSingleResponse);
      await staleSingleRead;
    });
    assert.equal(current().skills.some((skill) => skill.name === "skill-a"), false);

    await React.act(async () => {
      singleArchive.resolve();
      assert.equal(await singleResult, true);
    });
    assert.deepEqual(
      current().skills.map((skill) => skill.name),
      ["skill-b", "skill-c"],
    );
    assert.equal(current().total, 2);

    const batchCatalogRead = deferred<SkillsResponse>();
    heldCatalogRead = batchCatalogRead;
    const staleBatchResponse = catalogResponse(serverNames);
    let staleBatchRead!: Promise<boolean>;
    await React.act(async () => {
      staleBatchRead = current().fetchSkills();
      await Promise.resolve();
    });

    let batchResult!: Promise<string[]>;
    await React.act(async () => {
      batchResult = current().batchDeleteSkills(["skill-b"]);
      await Promise.resolve();
    });
    await React.act(async () => {
      batchCatalogRead.resolve(staleBatchResponse);
      await staleBatchRead;
    });

    await React.act(async () => {
      batchArchive.resolve();
      assert.deepEqual(await batchResult, ["skill-b"]);
    });
    assert.deepEqual(
      current().skills.map((skill) => skill.name),
      ["skill-c"],
    );
    assert.equal(current().total, 1);
  } finally {
    await React.act(async () => root.unmount());
    skillApi.listAllAuthorized = originalListAllAuthorized;
    skillApi.delete = originalDelete;
    skillApi.batchDelete = originalBatchDelete;
  }
});

test("full authorized catalog does not reload for local list parameters", async () => {
  const React = await import("react");
  const { createRoot } = await import("react-dom/client");
  const { useSkills } = await import("../useSkills.ts");
  const originalListAllAuthorized = skillApi.listAllAuthorized;
  let catalogRequests = 0;

  skillApi.listAllAuthorized = async () => {
    catalogRequests += 1;
    return catalogResponse(["skill-a"]);
  };

  const container = dom.document.createElement("div");
  const root = createRoot(container as never);
  function Probe({ query }: { query: string }) {
    useSkills({ allAuthorizedCatalog: true, listParams: { q: query } });
    return null;
  }

  try {
    await React.act(async () => {
      root.render(React.createElement(Probe, { query: "first" }));
    });
    assert.equal(catalogRequests, 1);

    await React.act(async () => {
      root.render(React.createElement(Probe, { query: "second" }));
    });
    assert.equal(catalogRequests, 1);
  } finally {
    await React.act(async () => root.unmount());
    skillApi.listAllAuthorized = originalListAllAuthorized;
  }
});
