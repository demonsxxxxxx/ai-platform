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
  assert.match(selector, /<ModalRoot open=\{isOpen\} onOpenChange=\{setIsOpen\}/);
  assert.match(selector, /<ModalPopup/);
  assert.match(selector, /aria-labelledby=\{titleId\}/);
  assert.match(selector, /initialFocus=\{searchInputRef\}/);
  assert.match(selector, /finalFocus=\{externalOnOpenChange \? true : triggerRef\}/);
  assert.match(selector, /aria-pressed=\{selected\}/);
  assert.match(selector, /data-composer-skill-selection-summary/);
  assert.match(selector, /reconfirm/i);
  assert.doesNotMatch(selector, /data-composer-skill-version|shortVersion\(/);
  assert.doesNotMatch(selector, /current Skill version/);
  assert.match(selector, /size="large"/);
  assert.doesNotMatch(selector, /onToggleAll|onToggleCategory|Checkbox/);
  assert.match(inputSelectors, /onSelectSkill/);
  assert.doesNotMatch(inputSelectors, /onToggleAllSkills/);
});

test("task-specific selected Skill chip shows only public task details", () => {
  const chips = read("src/components/chat/ComposerChips.tsx");
  const input = read("src/components/chat/ChatInput.tsx");
  const sharedChips = read("src/components/chat/assistant-ui/elements/composer-chips.tsx");

  assert.match(input, /visibleDetails:/);
  assert.match(chips, /data-composer-skill-visible-detail/);
  assert.match(chips, /selection\.visibleDetails/);
  assert.match(chips, /id=\{selection\.id\}/);
  assert.match(chips, /label=\{selection\.label\}/);
  assert.match(sharedChips, /data-composer-chip-reference=\{referenceId \?\? id\}/);
  assert.match(sharedChips, /title=\{description \?\? label\}/);
  assert.doesNotMatch(input, /expected_version\.slice/);
  assert.doesNotMatch(input, /v\$\{selectedSkill\.expected_version/);
  assert.match(sharedChips, /data-task-selected-skill-remove/);
  assert.match(sharedChips, /width: 44, height: 44/);
  assert.match(chips, /skillSelector\.removeSelectedSkill/);
  assert.match(sharedChips, /aria-label=\{removeLabel\}/);
  assert.doesNotMatch(sharedChips, /visibleDetails|data-composer-skill-visible-detail/);
});

test("ordinary Skill copy hides release internals and describes tenant-scoped removal", () => {
  const zh = JSON.parse(read("src/i18n/locales/zh.json"));

  assert.equal(zh.skills.available.title, "可用技能");
  assert.equal(zh.skillSelector.viewSkills, "查看技能");
  assert.equal(zh.skillSelector.taskReconfirm, "所选技能已更新，请重新选择。");
  assert.equal(
    zh.skillSelector.staleSelection,
    "所选技能已更新，请重新选择后再提交。",
  );

  assert.match(zh.skills.confirmDeleteMessage, /活跃使用/);
  assert.match(zh.skills.confirmDeleteMessage, /历史/);
  assert.doesNotMatch(zh.skills.confirmDeleteMessage, /永久|不可撤销/);
  assert.equal(zh.adminMarketplace.confirmDeleteMessage, undefined);
  assert.equal(zh.marketplace, undefined);
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
  assert.match(inputTypes, /initialDraft\?:\s*string/);
  assert.doesNotMatch(inputTypes, /onDraftChange/);
  assert.doesNotMatch(chatView, /const \[composerDraft, setComposerDraft\]/);
  assert.doesNotMatch(chatView, /pendingComposerInput/);
  assert.match(chatView, /draftSnapshotRef:\s*composerDraftSnapshotRef/);
  assert.match(chatView, /draftScopeKey:\s*sessionId/);
  assert.match(
    input,
    /const \[input, setLocalInput\] = useState\(inputRef\.current\)/,
  );
  const submission = read("src/components/chat/assistant-ui/composerSubmission.ts");
  assert.match(input, /submitComposerMessage/);
  assert.match(submission, /await onSend/);
  assert.match(submission, /outcome\.status === "accepted"/);
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
