/** Invoked by pytest against the real Python WebSocket/ADK stack, not a fake socket. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { Transcript } from "../src/transcript.js";

const session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (!predicate()) {
    assert.ok(Date.now() < deadline, JSON.stringify(session.state.view));
    await delay(10);
  }
}
try {
  session.connect();
  await until(() => session.state.view.status === "ready");
  const thread = session.state.threadId;
  session.submit("Remember bridge-marker-731");
  await until(() => session.state.view.status === "running");
  session.submit("Explain README.md without changing files", "followUp");
  session.submit("Give a final queued reply", "followUp");
  await until(() => session.state.view.status === "answered" && session.state.view.entries.length === 7);
  assert.equal(session.state.threadId, thread);
  assert.deepEqual(session.state.view.entries.map(e => e.kind), ["user", "assistant", "user", "tool", "assistant", "user", "assistant"]);
  const transcript = new Transcript(session.state.view);
  for (const width of [40, 80, 120]) {
    const lines = transcript.render(width);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    const text = stripTerminalSequences(lines.join("\n"));
    assert.match(text, /read README.md/);
    assert.doesNotMatch(text, /model_text|completion_claims|checkpoint_id|run started/);
  }
  process.stdout.write(JSON.stringify({turns: 3, entries: session.state.view.entries.length, status: session.state.view.status}));
} finally { session.close(); }
