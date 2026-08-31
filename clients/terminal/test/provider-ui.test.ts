import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { LoginDialog, type ProviderPort } from "../src/provider-ui.js";
import type { WireObject } from "../src/protocol.js";

async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await delay(5); }
  assert.fail("dialog did not settle");
}
const text = (dialog: LoginDialog, width = 80) => stripTerminalSequences(dialog.render(width).join("\n"));

test("login displays safe instructions, polls server, and closes without logging out", async () => {
  const calls: string[] = [], notices: string[] = [];
  let complete = false, closed = false;
  const port: ProviderPort = {async providerRequest(operation) {
    calls.push(operation);
    return {credential_path: "/synthetic/state/auth/openai-codex.json", login: {login_id: "one",
      status: complete ? "authenticated" : "waiting", user_code: "TEST-CODE", verification_url: "https://example.invalid/fixture"}};
  }};
  const dialog = new LoginDialog(port, "openai_codex", () => {}, () => { closed = true; dialog.dispose(); }, value => notices.push(value), 5);
  try {
    await until(() => text(dialog).includes("TEST-CODE"));
    for (const width of [10, 20, 40, 80, 120]) assert.ok(dialog.render(width).every(line => visibleWidth(line) <= width));
    complete = true;
    await until(() => text(dialog).includes("Signed in"));
    assert.match(text(dialog), /\/synthetic\/state\/auth/);
    dialog.handleInput("\r");
    assert.equal(closed, true);
    assert.equal(calls.filter(operation => operation === "login").length, 1);
    assert.equal(calls.includes("cancel_login"), false);
    assert.equal(notices.length, 1);
  } finally { dialog.dispose(); }
});

test("Escape before login response still cancels the server attempt exactly once", async () => {
  let resolveStart!: (data: WireObject) => void;
  const calls: string[] = [], notices: string[] = [];
  const port: ProviderPort = {providerRequest(operation) {
    calls.push(operation);
    if (operation === "login") return new Promise(resolve => resolveStart = resolve);
    return Promise.resolve({login: {login_id: "one", status: "cancelled"}});
  }};
  const dialog = new LoginDialog(port, "openai_codex", () => assert.fail("closed dialog redrew"),
    () => dialog.dispose(), value => notices.push(value), 5);
  dialog.handleInput("\x1b");
  resolveStart({login: {login_id: "one", status: "starting"}});
  await until(() => notices.length === 1);
  dialog.dispose();
  assert.deepEqual(calls, ["login", "cancel_login"]);
  assert.deepEqual(notices, ["Login cancelled"]);
});

test("cancellation while status is in flight neither revives the dialog nor repeats cancellation", async () => {
  let resolveStatus!: (data: WireObject) => void;
  let polling = false;
  const calls: string[] = [];
  const port: ProviderPort = {providerRequest(operation) {
    calls.push(operation);
    if (operation === "status") { polling = true; return new Promise(resolve => resolveStatus = resolve); }
    return Promise.resolve({login: {login_id: "one", status: operation === "login" ? "starting" : "cancelled"}});
  }};
  const dialog = new LoginDialog(port, "openai_codex", () => {}, () => dialog.dispose(), () => {}, 1);
  try {
    await until(() => polling);
    dialog.handleInput("\x1b");
    resolveStatus({login: {login_id: "one", status: "waiting", user_code: "STALE", verification_url: "https://example.invalid"}});
    await delay(20);
    assert.equal(calls.filter(operation => operation === "cancel_login").length, 1);
    assert.doesNotMatch(text(dialog), /STALE/);
  } finally { dialog.dispose(); }
});

test("unconfirmed login gives reconciliation guidance without pretending cancellation succeeded", async () => {
  const notices: string[] = [];
  const dialog = new LoginDialog({async providerRequest(operation) {
    if (operation === "login") return {login: {login_id: "one", status: "starting"}};
    throw new Error("Server disconnected");
  }}, "openai_codex", () => {}, () => dialog.dispose(), value => notices.push(value), 1);
  try {
    await until(() => text(dialog).includes("unconfirmed"));
    assert.match(text(dialog), /Check \/auth/);
    dialog.handleInput("\x1b");
    await until(() => notices.length === 1);
    assert.match(notices[0], /not confirmed/);
  } finally { dialog.dispose(); }
});
