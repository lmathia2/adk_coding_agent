/** Pi picker → production WebSocket → actual ADK model configuration. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { ModelPicker } from "../src/model-picker.js";
import { TerminalView } from "../src/view.js";
import { SessionPicker, HistoryDialog } from "../src/session-ui.js";
import { ResourceDialog, skillPrompt } from "../src/resources.js";

const session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
let input!: (data: string) => unknown;
const tui = {terminal: {rows: 30, columns: 100}, addChild() {}, setFocus() {}, requestRender() {},
  addInputListener(callback: (data: string) => unknown) { input = callback; return () => {}; },
} as unknown as TUI;
const view = new TerminalView(tui, session.state.view, {submit() {}, cancel() {}, quit() {}});
const screen = () => stripTerminalSequences(view.root.render(100).join("\n"));
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!predicate()) { assert.ok(Date.now() < deadline, screen()); await delay(10); }
}
function openPicker(): void {
  view.showDialog(close => new ModelPicker(session, () => view.refresh(), close,
    text => { session.state.view.notice = text; view.refresh(); }, 10));
}
try {
  session.connect(); await until(() => session.state.view.status === "ready");
  const original = session.state.threadId;
  await session.refreshResources();
  assert.ok(session.state.view.workspace.endsWith("/workspace"));
  view.showDialog(close => new ResourceDialog(session, session.state.view, false, () => view.refresh(), close, () => {}));
  await until(() => screen().includes("Available for the next turn"));
  assert.match(screen(), /AGENTS.md/); assert.doesNotMatch(screen(), /PRIVATE_SKILL_BODY|PRIVATE_PROJECT_INSTRUCTION/);
  input("\x1b");
  session.submit(skillPrompt("/skill:python-checks First fixture turn", session.state.view));
  await until(() => session.state.view.status === "running");
  await until(() => session.state.view.selectedSkills?.includes("python-checks") === true);
  view.editor.setText("preserved draft");
  openPicker();
  await until(() => screen().includes("beta [scripted]"));
  input("beta");
  for (const width of [20, 40, 80, 120]) assert.ok(view.root.render(width).every(line => visibleWidth(line) <= width));
  input("\r");
  await until(() => session.state.view.notice.includes("Next turn: beta"));
  assert.equal(view.editor.getText(), "preserved draft");
  assert.equal(session.state.view.model, "alpha [scripted]");
  session.submit("Next fixture turn", "followUp");
  await until(() => session.state.view.status === "answered" && session.state.view.entries.length === 4);
  assert.equal(session.state.view.model, "beta [scripted]");
  openPicker(); await until(() => screen().includes("gamma [scripted]"));
  input("gamma"); input("\x13");
  await until(() => session.state.view.notice.includes("Default saved on server:"));
  session.newConversation();
  await until(() => session.state.view.model === "gamma [scripted]");
  session.submit("New conversation with saved model");
  await until(() => session.state.view.status === "answered");
  assert.deepEqual(session.state.view.entries.map(entry => entry.kind), ["user", "assistant"]);
  view.showDialog(close => new SessionPicker(session, () => view.refresh(), close));
  await until(() => screen().includes("Next fixture turn"));
  input("Next fixture turn"); input("\r");
  await until(() => session.state.threadId === original && session.state.view.notice.includes("Resumed conversation"));
  assert.equal(view.editor.getText(), "preserved draft");
  assert.deepEqual(session.state.view.entries.map(entry => entry.kind), ["user", "assistant", "user", "assistant"]);
  const cursor = session.state.cursor;
  const run = session.state.runId;
  view.showDialog(close => new HistoryDialog(session, original, () => view.refresh(), close));
  await until(() => screen().includes("2 recent turns"));
  for (const width of [20, 40, 80, 120]) assert.ok(view.root.render(width).every(line => visibleWidth(line) <= width));
  input("\x1b"); await delay(50);
  assert.equal(session.state.runId, run); assert.equal(session.state.cursor, cursor);
  assert.equal(session.state.view.entries.length, 4);
  const fresh = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
  try {
    fresh.connect(); await until(() => fresh.state.view.status === "ready");
    await fresh.resume(original, new AbortController().signal);
    assert.equal(fresh.state.view.entries.length, 4);
    assert.equal(fresh.state.runId, run);
    assert.equal(fresh.state.cursor, cursor);
  } finally { fresh.close(); }
  process.stdout.write(JSON.stringify({turns: 3, active_model_preserved: true, default: "gamma", resumed: true}));
} finally { view.dispose(); session.close(); }
