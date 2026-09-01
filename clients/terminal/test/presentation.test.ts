import assert from "node:assert/strict";
import { test } from "node:test";
import { Editor, TuiMainScreen, stripTerminalSequences, visibleWidth, type Terminal } from "@earendil-works/pi-tui";
import { renderTool, Transcript } from "../src/transcript.js";
import { editorTheme } from "../src/theme.js";
import type { SessionView, ToolEntry } from "../src/contracts.js";
import { SessionFooter, SessionHeader, TerminalView } from "../src/view.js";
import type { TUI } from "@earendil-works/pi-tui";

const read: ToolEntry = { kind: "tool", id: "read-1", name: "read", arguments: '{"path":"README.md"}',
  result: JSON.stringify({status: "ok", model_text: Array.from({length: 30}, (_, i) => `${i+1} | example line`).join("\n")}), done: true };
test("successful read collapses to its header, expands to all 30 lines", () => {
  assert.deepEqual(renderTool(read, 80, false).map((line) => stripTerminalSequences(line).trim()), ["read README.md"]);
  assert.match(stripTerminalSequences(renderTool(read, 80, true).join("\n")), /30 \| example line/);
});
test("bash previews five lines while errors remain visible", () => {
  const bash = {...read, name: "bash", arguments: '{"command":"pytest"}'};
  const text = stripTerminalSequences(renderTool(bash, 80, false).join("\n"));
  assert.match(text, /5 \| example line/);
  assert.doesNotMatch(text, /6 \| example line/);
  assert.match(text, /25 more lines/);
  assert.match(stripTerminalSequences(renderTool({...read, result: '{"status":"error","model_text":"Permission denied"}'}, 80, false).join("\n")), /Permission denied/);
});
test("Markdown and tool blocks respect terminal-cell widths and strip terminal controls", () => {
  const state: SessionView = {entries: [read, {kind: "assistant", id: "m1", text: "**Hello** 世界🙂\n\n```python\nprint('hello')\n```\n\x1b]52;c;ZXZpbA==\x07"}], status: "ready", model: "fixture", workspace: ".", notice: ""};
  for (const width of [20, 40, 80, 120]) {
    const lines = new Transcript(state).render(width);
    assert.ok(lines.every((line) => visibleWidth(line) <= width));
    assert.doesNotMatch(lines.join("\n"), /52;c;/);
    assert.doesNotMatch(stripTerminalSequences(lines.join("\n")), /\*\*Hello\*\*/);
  }
});
test("the actual Pi editor supports history, multiline input, and paste", () => {
  const terminal = {columns: 80, rows: 24} as Terminal;
  const editor = new Editor(new TuiMainScreen(terminal), editorTheme);
  editor.addToHistory("previous prompt");
  editor.handleInput("\x1b[A");
  assert.equal(editor.getText(), "previous prompt");
  editor.setText("");
  editor.handleInput("\x1b[200~line one\nline two\x1b[201~");
  assert.equal(editor.getExpandedText(), "line one\nline two");
});

test("footer keeps two bounded rows for long paths, Unicode models, and narrow terminals", () => {
  const state: SessionView = {entries: [], status: "running", notice: "", model: "模型🙂 [provider]".repeat(10),
    workspace: "/long/workspace/".repeat(20) + "\n\x1b]52;c;ZXZpbA==\x07"};
  for (const width of [1, 2, 10, 20, 40, 80, 120]) {
    const lines = new SessionFooter(state).render(width);
    assert.equal(lines.length, 2);
    assert.ok(lines.every(line => visibleWidth(line) <= width && !line.includes("\n")));
    assert.doesNotMatch(lines.join("\n"), /52;c;/);
    if (width >= 10) assert.ok(stripTerminalSequences(lines[1]).endsWith("running"));
  }
});

test("header shows and bounds the server-selected harness identity", () => {
  const state: SessionView = {entries: [], status: "ready", notice: "", model: "fixture", workspace: ".",
    harness: "Alternate harness 世界🙂\x1b]52;c;ZXZpbA==\x07"};
  for (const width of [10, 20, 40, 80]) {
    const lines = new SessionHeader(state).render(width);
    assert.equal(lines.length, 1); assert.ok(visibleWidth(lines[0]) <= width);
    assert.doesNotMatch(lines[0], /52;c;/);
  }
  assert.match(stripTerminalSequences(new SessionHeader(state).render(80)[0]), /Alternate harness/);
});

test("Pi loader animates without transcript entries and stops while paused, disconnected, awaiting approval or disposed", t => {
  t.mock.timers.enable({apis: ["setInterval"]});
  let renders = 0;
  const tui = {terminal: {rows: 24, columns: 80}, addChild() {}, setFocus() {}, requestRender() { renders++; },
    addInputListener() { return () => {}; }} as unknown as TUI;
  const state: SessionView = {entries: [], status: "running", workspace: ".", model: "fixture", notice: ""};
  const view = new TerminalView(tui, state, {submit() {}, cancel() {}, quit() {}});
  const text = () => stripTerminalSequences(view.root.render(80).join("\n"));
  try {
    const initial = text(); const before = renders;
    t.mock.timers.tick(80);
    assert.ok(renders > before); assert.notEqual(text(), initial);
    assert.deepEqual(state.entries, []);
    // Informational notices do not hide ongoing work.
    state.notice = "Steering accepted"; view.refresh(); assert.match(text(), /Working/);
    for (const update of [{status: "paused"}, {status: "running", connected: false},
      {connected: true, approvals: [{request_id: "1", fingerprint: "f", operation: "test", risk: "unknown", reason: "", wait_deadline: ""}]},
      {approvals: [], status: "completed"}]) {
      Object.assign(state, update); view.refresh();
      const stopped = renders; t.mock.timers.tick(160);
      assert.equal(renders, stopped); assert.doesNotMatch(text(), /Working/);
    }
    state.status = "running"; view.refresh(); assert.match(text(), /Working/);
  } finally { view.dispose(); }
  const stopped = renders; t.mock.timers.tick(160); assert.equal(renders, stopped);
});
