/** Real Pi renderers / authenticated transport / ADK public streaming and replay. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { RemoteSession } from "../src/remote-session.js";
import { Transcript } from "../src/transcript.js";

const options = {url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!};
const first = new RemoteSession(options), fresh = new RemoteSession(options);
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (!predicate()) { assert.ok(Date.now() < deadline, "stream fixture timed out"); await delay(10); }
}
const text = (session: RemoteSession) => session.state.view.entries
  .map(entry => entry.kind === "assistant" ? entry.text : "").join("");
try {
  first.connect(); await until(() => first.state.view.status === "ready");
  first.submit("hello");
  await until(() => text(first) === "Hello **streaming** reader.\n");
  assert.equal(first.state.active, true);
  const thread = first.state.threadId;
  first.close();
  fresh.connect(); await until(() => fresh.state.view.status === "ready");
  await fresh.resume(thread, new AbortController().signal);
  await until(() => text(fresh) === "Hello **streaming** reader.\n");
  assert.equal(fresh.state.active, true);
  const transcript = new Transcript(fresh.state.view);
  for (const width of [20, 40, 80, 120]) {
    const lines = transcript.render(width);
    assert.ok(lines.every(line => visibleWidth(line) <= width));
    assert.doesNotMatch(stripTerminalSequences(lines.join("\n")), /completion_claims|public_delta|"status"|run started/);
  }
  process.stdout.write(JSON.stringify({phase: "partial-resumed"}) + "\n");
  await until(() => fresh.state.view.status === "answered");
  assert.equal(text(fresh), "Hello **streaming** reader.\n");
  assert.deepEqual(fresh.state.view.entries.map(entry => entry.kind), ["user", "assistant"]);
  process.stdout.write(JSON.stringify({streamed: true, resumed: true, entries: 2}));
} finally { first.close(); fresh.close(); }
