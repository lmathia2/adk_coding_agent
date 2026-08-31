/** Real remote session and real Pi dialogs, driven against pytest's production server. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { providerCommand } from "../src/provider-ui.js";
import { TerminalView } from "../src/view.js";
import { object } from "../src/protocol.js";

const session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
let input!: (data: string) => unknown;
const tui = {terminal: {rows: 30, columns: 100}, addChild() {}, setFocus() {}, requestRender() {},
  addInputListener(callback: (data: string) => unknown) { input = callback; return () => {}; },
} as unknown as TUI;
const view = new TerminalView(tui, session.state.view, {submit() {}, cancel() {}, quit() {}});
const notice = (text: string) => { session.state.view.notice = text; view.refresh(); };
const screen = () => stripTerminalSequences(view.root.render(100).join("\n"));
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!predicate()) { assert.ok(Date.now() < deadline, screen()); await delay(10); }
}
try {
  session.connect(); await until(() => session.state.view.status === "ready");
  view.editor.setText("preserved draft");
  await providerCommand("/login", session, view, notice);
  input("\r");
  await until(() => screen().includes("TEST-CODE"));
  for (const width of [20, 40, 80, 120]) assert.ok(view.root.render(width).every(line => visibleWidth(line) <= width));
  input("\x1b");
  await until(() => session.state.view.notice === "Login cancelled");
  assert.equal(view.editor.getText(), "preserved draft");
  assert.equal((await session.providerRequest("status", {provider: "openai_codex"})).authenticated, false);
  await providerCommand("/login", session, view, notice);
  input("\r");
  await until(() => screen().includes("Signed in."));
  input("\r");
  const status = await session.providerRequest("status", {provider: "openai_codex"});
  assert.equal(status.authenticated, true);
  assert.equal(typeof status.credential_path, "string");
  await providerCommand("/logout", session, view, notice);
  input("\r"); input("\x1b[B"); input("\r");
  await until(() => session.state.view.notice.includes("credentials removed"));
  const providers = (await session.providerRequest("status")).providers as unknown[];
  assert.equal(object(providers[0]).authenticated, false);
  assert.deepEqual(session.state.view.entries, []);
  assert.doesNotMatch(screen(), /access_token|refresh_token|refresh-secret|private-device/);
  process.stdout.write(JSON.stringify({login: true, cancel: true, logout: true, transcript_entries: 0}));
} finally { view.dispose(); session.close(); }
