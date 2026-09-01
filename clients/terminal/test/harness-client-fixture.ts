/** Proves one protocol-only terminal can drive a separately registered ADK harness. */
import assert from "node:assert/strict";
import { setTimeout as delay } from "node:timers/promises";
import { RemoteSession } from "../src/remote-session.js";

const session = new RemoteSession({url: process.env.ADK_TEST_URL!, token: process.env.ADK_TEST_TOKEN!});
async function until(predicate: () => boolean): Promise<void> {
  const deadline = Date.now() + 15_000;
  while (!predicate()) { assert.ok(Date.now() < deadline, JSON.stringify(session.state.view)); await delay(10); }
}
try {
  session.connect();
  await until(() => session.state.view.status === "ready");
  assert.equal(session.state.view.harness, "Alternate fixture harness");
  session.submit("Hello from the shared terminal");
  await until(() => session.state.view.status === "completed");
  const reply = session.state.view.entries.find(entry => entry.kind === "assistant");
  assert.ok(reply?.kind === "assistant");
  process.stdout.write(JSON.stringify({harness: session.state.view.harness, reply: reply.text}));
} finally { session.close(); }
