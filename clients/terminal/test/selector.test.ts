import assert from "node:assert/strict";
import { test } from "node:test";
import { visibleWidth, stripTerminalSequences, type TUI } from "@earendil-works/pi-tui";
import { Selector } from "../src/selector.js";
import { CommandCompletion } from "../src/commands.js";
import { TerminalView } from "../src/view.js";

const choices = [
  {value: "opaque-a", label: "Alpha model", description: "first provider"},
  {value: "opaque-b", label: "Beta model 世界🙂", description: "second provider"},
];
test("selector searches labels, supports Enter vs save-default, and cancels", () => {
  const selected: {value: string; persist: boolean}[] = []; let cancelled = 0;
  const selector = new Selector("Models", choices, (item, persist) => selected.push({value: item.value, persist}), () => cancelled++, true);
  selector.handleInput("beta"); selector.handleInput("\r"); selector.handleInput("\x13");
  assert.deepEqual(selected, [{value: "opaque-b", persist: false}, {value: "opaque-b", persist: true}]);
  selector.handleInput("\x1b"); assert.equal(cancelled, 1);
});
test("selector arrow navigation, empty results, Unicode and control sanitization", () => {
  let selected = "";
  const selector = new Selector("Choices\x1b]52;c;ZXZpbA==\x07", choices, item => selected = item.value, () => {});
  selector.handleInput("\x1b[B"); selector.handleInput("\r"); assert.equal(selected, "opaque-b");
  for (const width of [10, 20, 40, 80, 120]) {
    const lines = selector.render(width);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    assert.doesNotMatch(stripTerminalSequences(lines.join("\n")), /52;c;/);
  }
  selector.handleInput("no-such-choice"); selector.handleInput("\r"); assert.equal(selected, "opaque-b");
});
test("command completion uses only currently supported commands and preserves the draft tail", async () => {
  let available = [{value: "/help", label: "/help"}];
  const completion = new CommandCompletion(() => available);
  assert.equal(await completion.getSuggestions(["ordinary text"], 0, 13), null);
  assert.equal(await completion.getSuggestions(["/model"], 0, 6), null);
  available = [...available, {value: "/model", label: "/model"}];
  const match = await completion.getSuggestions(["/mo"], 0, 3);
  assert.equal(match?.items[0].value, "/model");
  assert.deepEqual(completion.applyCompletion(["/moargument"], 0, 3, match!.items[0], "/mo"),
    {lines: ["/model argument"], cursorLine: 0, cursorCol: 7});
});

test("closing a selector restores the draft and does not cancel the active run", () => {
  let listener: ((data: string) => unknown) | undefined;
  let cancelled = 0;
  const tui = {terminal: {rows: 24, columns: 80}, addChild() {}, setFocus() {}, requestRender() {},
    addInputListener(value: (data: string) => unknown) { listener = value; return () => { listener = undefined; }; },
  } as unknown as TUI;
  const view = new TerminalView(tui, {entries: [], status: "running", workspace: ".", model: "fixture", notice: ""},
    {submit() {}, cancel() { cancelled++; }, quit() {}});
  try {
    view.editor.setText("a preserved draft");
    view.select("Choices", choices, () => {});
    listener!("\x1b");
    assert.equal(cancelled, 0);
    assert.equal(view.editor.getText(), "a preserved draft");
    listener!("\x1b");
    assert.equal(cancelled, 1);
  } finally { view.dispose(); }
  assert.equal(listener, undefined);
});
