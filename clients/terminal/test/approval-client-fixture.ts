/** Actual Pi dialogs, authenticated Python server, ADK tool, and local shell. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { ApprovalPresenter } from "../src/approvals.js";
import { TerminalView } from "../src/view.js";

let session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
let input!: (data: string) => unknown;
const tui = {terminal: {rows: 30, columns: 100}, addChild() {}, setFocus() {}, requestRender() {},
  addInputListener(fn: (data: string) => unknown) { input = fn; return () => {}; }} as unknown as TUI;
let view!: TerminalView, presenter!: ApprovalPresenter, unsubscribe!: () => void;
function mount(): void {
  view = new TerminalView(tui, session.state.view, {submit() {}, cancel() { session.cancel(); }, quit() {}});
  presenter = new ApprovalPresenter(view, session, text => { session.state.view.notice = text; });
  unsubscribe = session.subscribe(() => { view.refresh(); presenter.update(session.state.runId, session.state.active, session.state.view.approvals ?? []); });
}
const screen = () => stripTerminalSequences(view.root.render(100).join("\n"));
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 12_000;
  while (!predicate()) { assert.ok(Date.now() < deadline, screen()); await delay(10); }
}
mount();
try {
  session.connect(); await until(() => session.state.view.status === "ready");
  session.submit("Approval fixture: first command");
  await until(() => screen().includes("Command approval"));
  const thread = session.state.threadId, run = session.state.runId;
  // A disconnected terminal does not authorize or restart the waiting command.
  unsubscribe(); view.dispose(); session.close();
  session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
  mount(); session.connect(); await until(() => session.state.view.status === "ready");
  await session.resume(thread, new AbortController().signal);
  view.editor.setText("preserved draft");
  await until(() => screen().includes("Command approval"));
  assert.equal(session.state.runId, run);
  assert.match(screen(), /printf fixture-approved/);
  for (const width of [20, 40, 80, 120]) assert.ok(view.root.render(width).every(line => visibleWidth(line) <= width));
  input("a");
  await until(() => session.state.view.status === "blocked");
  assert.equal(view.hasDialog, false);
  assert.equal(view.editor.getText(), "preserved draft");
  assert.ok(session.state.view.entries.some(item => item.kind === "tool" && item.result?.includes("fixture-approved")));
  session.submit("Approval fixture: deny second command");
  await until(() => screen().includes("Command approval"));
  input("\r"); // default is Deny, not approval
  await until(() => session.state.view.status === "blocked");
  assert.ok(session.state.view.entries.some(item => item.kind === "tool" && item.result?.includes("approval denied")));
  session.submit("Approval fixture: cancel third command");
  await until(() => screen().includes("Command approval"));
  input("\x1b"); assert.equal(view.hasDialog, false); // defer, then interrupt
  input("\x1b"); await until(() => session.state.view.status === "cancelled");
  assert.equal(view.hasDialog, false);
  session.submit(JSON.stringify({goal: "Approval fixture: verify the final result", verification_requirements: ["command printf fixture-verification"]}));
  await until(() => screen().includes("Command approval"));
  assert.match(screen(), /printf fixture-verification/);
  input("a");
  await until(() => session.state.view.status === "complete");
  assert.match(session.state.view.notice, /Verification passed/);
  process.stdout.write(JSON.stringify({approved: true, denied: true, cancelled: true, resumed: true, verified: true}));
} finally { unsubscribe!(); view!.dispose(); session.close(); }
