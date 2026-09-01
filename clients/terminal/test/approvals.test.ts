import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth, type TUI } from "@earendil-works/pi-tui";
import { ApprovalDialog, ApprovalPresenter } from "../src/approvals.js";
import { TerminalView } from "../src/view.js";
import type { WireObject } from "../src/protocol.js";

const request = {request_id: "approval", fingerprint: "f".repeat(64), operation: "printf 'safe command'",
  risk: "unknown", reason: "Explicit authorization required", wait_deadline: "2030-01-01T00:00:00Z"};

test("approval defaults to denial, preserves widths, and never duplicates an in-flight decision", async () => {
  const calls: WireObject[] = [], notices: string[] = [];
  let finish!: (value: WireObject) => void;
  const dialog = new ApprovalDialog({approvalRequest(operation, args) {
    assert.equal(operation, "decide"); calls.push(args); return new Promise(resolve => finish = resolve);
  }}, "run", request, () => {}, () => dialog.dispose(), text => notices.push(text));
  for (const width of [20, 40, 80, 120]) assert.ok(dialog.render(width).every(line => visibleWidth(line) <= width));
  assert.match(stripTerminalSequences(dialog.render(80).join("\n")), /Command \(not executed\)/);
  dialog.handleInput("\r"); dialog.handleInput("\r");
  assert.equal(calls.length, 1); assert.equal(calls[0].decision, "denied");
  finish({request: {status: "denied"}}); await delay(5);
  assert.deepEqual(notices, ["Approval decision recorded: denied. Execution is reported separately."]);
});

test("closing an in-flight approval discloses uncertainty and late replies never reopen it", async () => {
  const notices: string[] = []; let fail!: (error: Error) => void;
  const dialog = new ApprovalDialog({approvalRequest() { return new Promise((_, reject) => fail = reject); }},
    "run", request, () => {}, () => dialog.dispose(), text => notices.push(text));
  dialog.handleInput("\x1b[B"); dialog.handleInput("\r"); dialog.handleInput("\x1b");
  assert.match(notices[0], /does not revoke/);
  fail(new Error("connection lost")); await delay(5);
  assert.match(notices[1], /not confirmed/);
});

test("approval has unambiguous keyboard shortcuts", async () => {
  const calls: WireObject[] = [];
  const dialog = new ApprovalDialog({async approvalRequest(_, args) { calls.push(args); return {}; }},
    "run", request, () => {}, () => {}, () => {});
  assert.match(stripTerminalSequences(dialog.render(80).join("\n")), /A\/Y approve · D\/N deny/);
  dialog.handleInput("a"); await delay(5);
  assert.equal(calls[0].decision, "approved");
});

test("approval presenter preserves drafts, defers without reopening, and closes stale requests", () => {
  let input!: (text: string) => unknown;
  const tui = {terminal: {rows: 30, columns: 100}, addChild() {}, setFocus() {}, requestRender() {},
    addInputListener(fn: (text: string) => unknown) { input = fn; return () => {}; }} as unknown as TUI;
  const view = new TerminalView(tui, {entries: [], status: "running", workspace: ".", model: "fixture", notice: ""},
    {submit() {}, cancel() {}, quit() {}});
  const presenter = new ApprovalPresenter(view, {async approvalRequest() { assert.fail("unexpected decision"); }}, () => {});
  view.editor.setText("preserved draft");
  view.select("Other dialog", [], () => {});
  presenter.update("run", true, [request]);
  assert.doesNotMatch(stripTerminalSequences(view.root.render(100).join("\n")), /Command approval/);
  input("\x1b"); presenter.update("run", true, [request]); assert.equal(view.hasDialog, true);
  input("\x1b"); presenter.update("run", true, [request]); assert.equal(view.hasDialog, false);
  presenter.update("run", true, [request], true); assert.equal(view.hasDialog, true);
  presenter.update("run", false, []); assert.equal(view.hasDialog, false);
  assert.equal(view.editor.getText(), "preserved draft"); view.dispose();
});

test("oversized commands cannot be approved from a truncated preview", () => {
  const calls: WireObject[] = [];
  const dialog = new ApprovalDialog({async approvalRequest(_, args) { calls.push(args); return {}; }}, "run",
    {...request, operation: "x".repeat(16_001)}, () => {}, () => {}, () => {});
  const rendered = stripTerminalSequences(dialog.render(80).join("\n"));
  assert.match(rendered, /Too large to approve/); assert.doesNotMatch(rendered, /Approve exact/);
  dialog.handleInput("\x1b[B"); dialog.handleInput("\r"); assert.equal(calls[0].decision, "denied");
});
