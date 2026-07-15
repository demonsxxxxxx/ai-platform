import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("public Skill DTO fields flow into the ordinary composer without alternate authority", () => {
  const types = read("src/types/skill.ts");
  const useSkills = read("src/hooks/useSkills.ts");
  const chatApp = read("src/components/layout/AppContent/ChatAppContent.tsx");

  for (const field of ["expected_version", "input_modes", "requires_file"]) {
    assert.match(types, new RegExp(field));
    assert.match(useSkills, new RegExp(field));
  }
  assert.match(chatApp, /useSelectedSkillTask/);
  assert.match(chatApp, /allAuthorizedCatalog:\s*true/);
  assert.match(useSkills, /skillApi\.listAllAuthorized/);
  assert.match(useSkills, /resolveSkillsAfterListFailure/);
  assert.doesNotMatch(chatApp, /marketplaceApi|adminSkill|updated_at.*expected_version/);
});

test("composer Skill selector keeps the concurrency token without exposing it", () => {
  const selector = read("src/components/selectors/SkillSelector.tsx");
  const inputSelectors = read("src/components/chat/ChatInputSelectors.tsx");

  assert.match(selector, /onSelectSkill/);
  assert.match(selector, /selectedSkill/);
  assert.match(
    selector,
    /selectedSkill\?\.expected_version\s*===\s*skill\.expected_version/,
  );
  assert.match(selector, /expected_version/);
  assert.match(selector, /requires_file/);
  assert.match(selector, /data-composer-skill-requires-file/);
  assert.match(selector, /role="dialog"/);
  assert.match(selector, /aria-modal="true"/);
  assert.match(selector, /event\.key === "Escape"/);
  assert.match(selector, /aria-pressed=\{selected\}/);
  assert.match(selector, /data-composer-skill-selection-summary/);
  assert.match(selector, /reconfirm/i);
  assert.doesNotMatch(selector, /data-composer-skill-version|shortVersion\(/);
  assert.doesNotMatch(selector, /current Skill version/);
  assert.match(selector, /min-h-11|size-11/);
  assert.doesNotMatch(selector, /onToggleAll|onToggleCategory|Checkbox/);
  assert.match(inputSelectors, /onSelectSkill/);
  assert.doesNotMatch(inputSelectors, /onToggleAllSkills/);
});

test("task-specific selected Skill chip shows only public task details", () => {
  const chips = read("src/components/chat/ComposerChips.tsx");
  const input = read("src/components/chat/ChatInput.tsx");
  const sharedChips = read("src/librechat-ui/Chips.tsx");

  assert.match(input, /visibleDetails:/);
  assert.match(chips, /data-composer-skill-visible-detail/);
  assert.match(chips, /selection\.visibleDetails/);
  assert.match(chips, /data-composer-chip-reference=\{selection\.id\}/);
  assert.match(chips, /title=\{selection\.label\}/);
  assert.doesNotMatch(input, /expected_version\.slice/);
  assert.doesNotMatch(input, /v\$\{selectedSkill\.expected_version/);
  assert.match(chips, /data-task-selected-skill-remove/);
  assert.match(chips, /size-11/);
  assert.match(chips, /Remove selected Skill/);
  assert.doesNotMatch(sharedChips, /visibleDetails|data-composer-skill-visible-detail/);
});

test("ordinary Skill copy hides release internals and describes tenant-scoped removal", () => {
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));
  const en = JSON.parse(read("src/i18n/locales/en.json"));

  assert.equal(zh.skills.available.title, "可用技能");
  assert.equal(zh.skillSelector.viewSkills, "查看技能");
  assert.equal(zh.skillSelector.taskReconfirm, "所选技能已更新，请重新选择。");
  assert.equal(
    zh.skillSelector.staleSelection,
    "所选技能已更新，请重新选择后再提交。",
  );
  assert.equal(en.skills.available.title, "Available skills");
  assert.equal(en.skillSelector.viewSkills, "View Skills");
  assert.equal(en.skillSelector.taskReconfirm, "This Skill was updated. Select it again.");
  assert.equal(
    en.skillSelector.staleSelection,
    "This Skill was updated. Select it again before submitting.",
  );

  for (const locale of [zh, en]) {
    for (const namespace of [
      locale.skills,
      locale.marketplace,
      locale.adminMarketplace,
    ]) {
      assert.match(namespace.confirmDeleteMessage, /active use|活跃使用/);
      assert.match(namespace.confirmDeleteMessage, /Historical|历史/);
      assert.doesNotMatch(namespace.confirmDeleteMessage, /permanent|永久|cannot be undone|不可撤销/i);
    }
  }
});

test("Skill navigation labels use the established AI-admin policy", () => {
  const selector = read("src/components/selectors/SkillSelector.tsx");
  const rail = read("src/components/panels/SidebarParts/SidebarRail.tsx");
  const sidebar = read("src/components/panels/SidebarParts/SessionListContent.tsx");

  for (const source of [rail, sidebar]) {
    assert.match(source, /isAiAdminUser\(/);
    assert.match(source, /roles: user\.roles \?\? \[\]/);
    assert.match(source, /skills\.available\.title/);
    assert.match(source, /nav\.skillManagement/);
  }
  assert.match(selector, /isAiAdminUser\(user\)/);
  assert.match(selector, /nav\.skillManagement/);
  assert.match(selector, /skillSelector\.viewSkills/);
});

test("browser harness checks executable candidates with the real Node filesystem API", () => {
  const smoke = read("scripts/authorized-skill-browser-smoke.mjs");
  assert.match(smoke, /existsSync/);
  assert.doesNotMatch(smoke, /__nodeFs/);
});

test("selected Skill state is owned by one focused hook with explicit recovery", () => {
  const hookPath = join(root, "src/hooks/useSelectedSkillTask.ts");
  assert.equal(existsSync(hookPath), true);
  const hook = read("src/hooks/useSelectedSkillTask.ts");

  assert.match(hook, /skill_selection_stale/);
  assert.match(hook, /capability_not_authorized/);
  assert.match(hook, /file_required_for_skill/);
  assert.match(hook, /reconfirm/i);
  assert.match(hook, /refresh/i);
  assert.doesNotMatch(hook, /multi.?agent|agent.?graph|control.?plane/i);
});

test("composer preserves prompt and attachments until submission is accepted", () => {
  const input = read("src/components/chat/ChatInput.tsx");
  const inputTypes = read("src/components/chat/chatInputTypes.ts");
  const chatView = read("src/components/layout/AppContent/ChatView.tsx");

  assert.match(inputTypes, /SubmissionOutcome/);
  assert.match(inputTypes, /draft\?:\s*string/);
  assert.match(chatView, /composerDraft/);
  assert.match(chatView, /draft:\s*composerDraft/);
  assert.match(input, /await onSend/);
  assert.match(input, /outcome\.status === "accepted"/);
  assert.doesNotMatch(
    input,
    /onSend\([^;]+;\s*pushHistory\([^;]+;\s*setInput\(""\)/s,
  );
});

test("authorized Skill task loop adds no multi-agent product authority", () => {
  const files = [
    "src/hooks/useSelectedSkillTask.ts",
    "src/components/selectors/SkillSelector.tsx",
    "src/components/chat/ChatInput.tsx",
    "src/services/api/session.ts",
  ].filter((path) => existsSync(join(root, path)));

  for (const path of files) {
    assert.doesNotMatch(
      read(path),
      /multi_agent|subagent|agent_graph|dispatch_child|control_plane/i,
      path,
    );
  }
});
