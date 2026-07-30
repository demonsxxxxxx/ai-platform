import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";

const root = process.cwd();

function read(path: string): string {
  return readFileSync(join(root, path), "utf8");
}

test("the standalone Files workbench route, navigation, and exclusive client are retired", () => {
  for (const source of [
    read("src/App.tsx"),
    read("src/appRouteManifest.ts"),
    read("src/components/layout/AppContent/TabContent.tsx"),
    read("src/components/panels/SidebarParts/SessionListContent.tsx"),
    read("src/components/panels/SidebarParts/SidebarRail.tsx"),
    read("src/components/panels/SidebarParts/navigationState.ts"),
  ]) {
    assert.doesNotMatch(source, /"\/files"/);
    assert.doesNotMatch(source, /RevealedFilesWorkbenchPanel/);
  }

  assert.equal(
    existsSync(
      join(root, "src/components/fileLibrary/RevealedFilesWorkbenchPanel.tsx"),
    ),
    false,
  );
  assert.equal(existsSync(join(root, "src/hooks/useRevealedFiles.ts")), false);
  assert.equal(existsSync(join(root, "src/services/api/revealedFile.ts")), false);
});

test("Chat attachment previews and Skill package file APIs remain available", () => {
  const chatView = read("src/components/layout/AppContent/ChatView.tsx");
  const sessionApi = read("src/services/api/session.ts");
  const skillApi = read("src/services/api/skill.ts");

  assert.match(chatView, /sessionApi\s*\.\s*getInputFiles/);
  assert.match(chatView, /openAttachmentPreview/);
  assert.match(sessionApi, /getInputFiles/);
  assert.match(sessionApi, /\/api\/ai\/chat\/sessions\/\$\{encodeURIComponent\(sessionId\)\}\/files/);
  assert.match(skillApi, /\/files/);
});
