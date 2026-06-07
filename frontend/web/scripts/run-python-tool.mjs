#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const toolArgs = process.argv.slice(2);

if (toolArgs.length === 0) {
  console.error("Usage: run-python-tool.mjs <script.py> [args...]");
  process.exit(2);
}

const candidates = [];
if (process.env.PYTHON) {
  candidates.push([process.env.PYTHON, []]);
}
if (process.platform === "win32") {
  candidates.push(["python", []], ["py", ["-3"]]);
} else {
  candidates.push(["python3", []], ["python", []]);
}

for (const [command, prefixArgs] of candidates) {
  const result = spawnSync(command, [...prefixArgs, ...toolArgs], {
    stdio: "inherit",
  });
  if (result.error?.code === "ENOENT") {
    continue;
  }
  if (result.error) {
    console.error(`Failed to run ${command}: ${result.error.message}`);
    process.exit(1);
  }
  process.exit(result.status ?? (result.signal ? 1 : 0));
}

console.error("No Python interpreter found. Set PYTHON or install python/python3.");
process.exit(1);
