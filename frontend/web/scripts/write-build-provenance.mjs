import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(import.meta.url);
const scriptsRoot = path.dirname(scriptPath);
const frontendRoot = path.resolve(scriptsRoot, "..");
const repoRoot = path.resolve(frontendRoot, "..", "..");
const distRoot = path.join(frontendRoot, "dist");
const provenancePath = path.join(distRoot, "ai-platform-build-provenance.json");

function gitValue(...args) {
  try {
    return execFileSync("git", args, {
      cwd: repoRoot,
      encoding: "utf8",
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
  } catch {
    return null;
  }
}

function envValue(name) {
  const value = process.env[name]?.trim();
  return value ? value : null;
}

function envDirtyValue() {
  const value = envValue("AI_PLATFORM_BUILD_DIRTY");
  if (value === null) {
    return null;
  }
  const normalized = value.toLowerCase();
  if (["1", "true", "yes", "dirty"].includes(normalized)) {
    return true;
  }
  if (["0", "false", "no", "clean"].includes(normalized)) {
    return false;
  }
  return null;
}

function sha256(relativePath) {
  const filePath = path.join(frontendRoot, relativePath);
  return createHash("sha256").update(readFileSync(filePath)).digest("hex");
}

const gitCommit = envValue("AI_PLATFORM_BUILD_COMMIT") || gitValue("rev-parse", "HEAD") || "unknown";
const dirtyOutput = gitValue("status", "--porcelain");
const dirty = envDirtyValue() ?? (dirtyOutput === null ? null : dirtyOutput.length > 0);

const provenance = {
  schema_version: "ai-platform.frontend-build-provenance.v1",
  frontend_path: "frontend/web",
  git: {
    commit: gitCommit,
    dirty,
  },
  source_hashes: {
    package_json_sha256: sha256("package.json"),
    pnpm_lock_sha256: sha256("pnpm-lock.yaml"),
  },
  generated_by: "frontend/web/scripts/write-build-provenance.mjs",
  release_policy: "dist_manifest_must_match_current_git_commit_and_frontend_source_hashes",
};

mkdirSync(distRoot, { recursive: true });
writeFileSync(provenancePath, `${JSON.stringify(provenance, null, 2)}\n`, "utf8");
console.log(`wrote ${path.relative(repoRoot, provenancePath).replaceAll(path.sep, "/")}`);
