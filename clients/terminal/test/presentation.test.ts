import assert from "node:assert/strict";
import { test } from "node:test";
import { Editor, TuiMainScreen, stripTerminalSequences, visibleWidth, type Terminal } from "@earendil-works/pi-tui";
import { renderTool, Transcript } from "../src/transcript.js";
import { editorTheme } from "../src/theme.js";
import type { SessionView, ToolEntry } from "../src/contracts.js";

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
