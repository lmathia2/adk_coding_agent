import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { loadHistory, type HistoryPort } from "../src/history.js";
import { HistoryDialog, SessionPicker } from "../src/session-ui.js";
import type { WireObject } from "../src/protocol.js";

const envelope = (run: string, sequence: number, event: WireObject) => ({type: "event", protocol_version: 1, run_id: run, sequence, durable: true, event});
const run = (id: string) => ({run_id: id, input: `User ${id}`, status: "completed", thread_id: "thread", updated_at: "fixture"});
const requests: WireObject[] = [];
const port: HistoryPort = {async request(operation, parameters = {}) {
  requests.push({operation, ...parameters});
  if (operation === "get") return {thread_id: "thread", runs: parameters.before_run_id ? [run("old")] : [run("two"), run("one")], next_before_run_id: parameters.before_run_id ? null : "one"};
  const id = String(parameters.run_id), cursor = Number(parameters.after_sequence);
  const event = cursor === 0 ? {type: "TEXT_MESSAGE_CONTENT", messageId: "reply", delta: `Reply ${id}`} : {type: "RUN_FINISHED", runId: id, threadId: "thread", result: {status: "answered"}};
  return {thread_id: "thread", run: run(id), events: [envelope(id, cursor + 1, event)], high_water_sequence: 2, next_after_sequence: cursor === 0 ? 1 : null};
}};
async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 100; i++) { if (predicate()) return; await delay(5); }
  assert.fail("history did not settle");
}

test("history replays chronological turns with snapshot cursors and no effectful commands", async () => {
  requests.length = 0;
  const page = await loadHistory(port, "thread", new AbortController().signal);
  assert.deepEqual(page.state.view.entries.map(entry => "text" in entry ? entry.text : "tool"), ["User one", "Reply one", "User two", "Reply two"]);
  assert.equal(page.state.view.status, "answered"); assert.equal(page.state.runId, "two");
  assert.equal(page.older, "one");
  assert.ok(requests.every(request => request.operation === "get" || request.operation === "events"));
  assert.ok(requests.filter(request => request.after_sequence === 1).every(request => request.high_water_sequence === 2));
});

test("gaps, changed snapshots and non-progressing pages fail closed", async () => {
  for (const broken of [{sequence: 2}, {next_after_sequence: 0}, {high_water_sequence: 0}]) {
    const bad: HistoryPort = {async request(operation, parameters) {
      const result = await port.request(operation, parameters);
      if (operation === "events") {
        if (broken.sequence) (result.events as WireObject[])[0].sequence = broken.sequence;
        else Object.assign(result, broken);
      }
      return result;
    }};
    await assert.rejects(loadHistory(bad, "thread", new AbortController().signal), /sequence|cursor|snapshot/);
  }
});

test("history dialog browses earlier pages without resuming and Escape cancels delayed reads", async () => {
  const history = new HistoryDialog(port, "thread", () => {}, () => history.dispose());
  const text = () => stripTerminalSequences(history.render(80).join("\n"));
  await until(() => text().includes("2 recent turns"));
  for (const width of [20, 40, 80, 120]) assert.ok(history.render(width).every(line => visibleWidth(line) <= width));
  history.handleInput("\r"); await until(() => text().includes("User old"));
  assert.match(text(), /Beginning of conversation/);
  history.handleInput("\x1b");
  let resolve!: (value: WireObject) => void;
  let rendered = 0;
  const delayed = new HistoryDialog({request() { return new Promise(done => resolve = done); }}, "thread", () => rendered++, () => delayed.dispose());
  delayed.handleInput("\x1b"); resolve({thread_id: "thread", runs: []}); await delay(5);
  assert.equal(rendered, 0);
});

test("resume picker filters server summaries and dismissal aborts restoration", async () => {
  let resumed: string | undefined, signal: AbortSignal | undefined;
  let finish!: () => void;
  const picker = new SessionPicker({async request() { return {conversations: [run("one"), {...run("two"), thread_id: "other"}]}; },
    resume(thread, cancellation) { resumed = thread; signal = cancellation; return new Promise(done => finish = done); }},
    () => {}, () => picker.dispose());
  await until(() => stripTerminalSequences(picker.render(80).join("\n")).includes("User two"));
  picker.handleInput("User two"); picker.handleInput("\r");
  assert.equal(resumed, "other"); picker.handleInput("\x1b");
  assert.equal(signal?.aborted, true); finish(); await delay(5);
});
