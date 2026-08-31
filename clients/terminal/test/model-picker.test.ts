import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { ModelPicker, type ModelPort } from "../src/model-picker.js";
import type { WireObject } from "../src/protocol.js";

const choice = (name: string) => ({provider: "fixture", name, reasoning: "low"});
const data = (refreshing = false) => ({selected: choice("alpha"), default: choice("alpha"), refreshing,
  models: ["alpha", "beta"].map(name => ({choice: choice(name), display_name: name.toUpperCase()}))});
async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await delay(5); }
  assert.fail("picker did not settle");
}

test("model picker keeps query while catalog refreshes and Enter selects without changing default", async () => {
  let reads = 0, closed = false;
  const selections: WireObject[] = [], notices: string[] = [];
  const port: ModelPort = {async modelRequest(operation, parameters) {
    if (operation === "catalog") return data(++reads === 1);
    selections.push(parameters!); return {selected: choice("beta"), default_path: "/server/auth/model-selection.json"};
  }};
  const picker = new ModelPicker(port, () => {}, () => { closed = true; picker.dispose(); }, text => notices.push(text), 5);
  try {
    picker.handleInput("beta");
    await until(() => reads > 1);
    for (const width of [20, 40, 80, 120]) assert.ok(picker.render(width).every(line => visibleWidth(line) <= width));
    assert.doesNotMatch(stripTerminalSequences(picker.render(80).join("\n")), /alpha \[fixture\]/);
    picker.handleInput("\r"); await until(() => closed);
    assert.deepEqual(selections, [{provider: "fixture", name: "beta", persist: false}]);
    assert.match(notices[0], /Next turn: beta/);
  } finally { picker.dispose(); }
});

test("Ctrl+S saves default; Escape while loading has no model mutation or late redraw", async () => {
  const selected: WireObject[] = [];
  const picker = new ModelPicker({async modelRequest(operation, parameters) {
    if (operation === "catalog") return data();
    selected.push(parameters!); return {selected: choice("alpha"), default_path: "/server/default"};
  }}, () => {}, () => picker.dispose(), () => {});
  try {
    await until(() => stripTerminalSequences(picker.render(80).join("\n")).includes("alpha [fixture]"));
    picker.handleInput("\x13");
    await until(() => selected.length === 1);
    assert.equal(selected[0].persist, true);
  } finally { picker.dispose(); }
  let resolve!: (data: WireObject) => void;
  let closed = false;
  const delayed = new ModelPicker({modelRequest() { return new Promise(done => resolve = done); }},
    () => assert.fail("dismissed picker redrew"), () => { closed = true; delayed.dispose(); }, () => assert.fail("unexpected mutation"));
  delayed.handleInput("\x1b"); resolve(data()); await delay(10);
  assert.equal(closed, true);
});

test("dismissing an in-flight selection reports uncertainty rather than claiming rollback", async () => {
  let finish!: (value: WireObject) => void;
  const notices: string[] = [];
  const picker = new ModelPicker({modelRequest(operation) {
    return operation === "catalog" ? Promise.resolve(data()) : new Promise(resolve => finish = resolve);
  }}, () => {}, () => picker.dispose(), text => notices.push(text));
  await until(() => stripTerminalSequences(picker.render(80).join("\n")).includes("alpha [fixture]"));
  picker.handleInput("\r"); picker.handleInput("\x1b");
  assert.match(notices[0], /may still finish/);
  finish({selected: choice("alpha")}); await delay(5);
  assert.equal(notices.length, 1);
});
