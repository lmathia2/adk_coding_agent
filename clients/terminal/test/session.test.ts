import assert from "node:assert/strict";
import { test } from "node:test";
import { decode, type WireObject } from "../src/protocol.js";
import { SessionState } from "../src/session.js";
import { Transcript } from "../src/transcript.js";

const envelope = (sequence: number, event: WireObject) => ({type: "event", protocol_version: 1, sequence, run_id: "run", durable: true, event});
test("reducer is quiet, keeps tool args/results separate, and deduplicates replay", () => {
  const state = new SessionState(); state.runId = "run";
  const stream = [
    {type: "RUN_STARTED", threadId: "thread", runId: "run"},
    {type: "TOOL_CALL_START", toolCallId: "tool", toolCallName: "read"},
    {type: "TOOL_CALL_ARGS", toolCallId: "tool", delta: '{"path":"README.md"}'},
    {type: "TOOL_CALL_END", toolCallId: "tool"},
    {type: "TOOL_CALL_RESULT", toolCallId: "tool", content: '{"status":"ok","model_text":"hello"}'},
    {type: "STATE_DELTA", delta: [{path: "/checkpoint_id", value: "private"}]},
    {type: "CUSTOM", name: "coding.workflow.output", value: {private: "state"}},
    {type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "Hello"},
    {type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: " there"},
    {type: "RUN_FINISHED", threadId: "thread", runId: "run", result: {status: "answered", verified: false}},
  ];
  stream.forEach((event, i) => {
    const wire = decode(JSON.stringify(envelope(i + 1, event)));
    assert.equal(state.envelope(wire), "applied");
    assert.equal(state.envelope(wire), "duplicate");
  });
  assert.equal(state.view.entries.length, 2);
  const tool = state.view.entries[0];
  assert.equal(tool.kind, "tool");
  if (tool.kind === "tool") { assert.equal(tool.arguments, stream[2].delta); assert.equal(tool.result, stream[4].content); }
  assert.equal(state.view.status, "answered");
  assert.doesNotMatch(JSON.stringify(state.view.entries), /checkpoint|private|run started/);
  state.begin(); state.user("next", "Follow up");
  assert.equal(state.view.entries.length, 3);
});
test("sequence gaps never advance the replay cursor", () => {
  const state = new SessionState(); state.runId = "run";
  assert.equal(state.envelope(envelope(2, {type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: "lost?"})), "gap");
  assert.equal(state.cursor, 0);
});
test("unknown/malformed events fail before reduction", () => {
  for (const event of [{type: "BOGUS"}, {type: "TEXT_MESSAGE_CONTENT", messageId: "m", delta: 3}]) {
    assert.throws(() => decode(JSON.stringify(envelope(1, event))));
  }
});

test("stopped runs show missing tool results without claiming the tool is still running", () => {
  for (const ending of [
    {type: "RUN_ERROR", message: "Server restarted"},
    {type: "RUN_FINISHED", runId: "run", threadId: "thread", result: {status: "cancelled"}},
  ]) {
    const state = new SessionState(); state.runId = "run";
    state.envelope(envelope(1, {type: "TOOL_CALL_START", toolCallId: "tool", toolCallName: "bash"}));
    state.envelope(envelope(2, ending));
    const rendered = new Transcript(state.view).render(80).join("\n");
    assert.match(rendered, /without a recorded tool result/);
    assert.doesNotMatch(rendered, /running…/);
  }
});
