import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { test } from "node:test";

test("command help works without a token or server and never prints a stack trace", () => {
  const entrypoint = resolve(dirname(fileURLToPath(import.meta.url)), "../src/main.js");
  const result = spawnSync(process.execPath, [entrypoint, "--help"], {encoding: "utf8", env: {PATH: process.env.PATH ?? ""}});
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /skein-tui \[--server URL\]/);
  assert.match(result.stdout, /\/help for interactive commands/);
  assert.equal(result.stderr, "");
});
