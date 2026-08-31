/** Pi picker → production WebSocket → actual ADK model configuration. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { ModelPicker } from "../src/model-picker.js";
import { TerminalView } from "../src/view.js";

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
  session.submit("First fixture turn");
  await until(() => session.state.view.status === "running");
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
  process.stdout.write(JSON.stringify({turns: 3, active_model_preserved: true, default: "gamma"}));
} finally { view.dispose(); session.close(); }
