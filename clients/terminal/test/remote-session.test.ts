import assert from "node:assert/strict";
import { once } from "node:events";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { WebSocketServer, type WebSocket } from "ws";
import { RemoteSession } from "../src/remote-session.js";

const token = "synthetic-local-token-" + "x".repeat(32);
async function until(predicate: () => boolean): Promise<void> {
  for (let i = 0; i < 300; i++) { if (predicate()) return; await delay(10); }
  assert.fail("condition did not complete");
}
const send = (socket: WebSocket, message: object) => socket.send(JSON.stringify({protocol_version: 1, ...message}));
test("one socket supports two turns with a stable conversation and retained transcript", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let connections = 0; const threads: string[] = [];
  server.on("connection", (socket, request) => {
    connections++;
    assert.equal(request.headers.authorization, `Bearer ${token}`);
    socket.on("message", (data) => {
      const message = JSON.parse(data.toString());
      if (message.type === "client.hello") {
        assert.equal(message.protocol_version, undefined);
        send(socket, {type: "server.hello", harness: {display_name: "Other harness", capabilities: ["streaming", "cancel", "steering"]}});
      }
      if (message.type === "task.start") {
        threads.push(message.thread_id);
        const run = `run-${threads.length}`;
        send(socket, {type: "task.accepted", request_id: message.request_id, run_id: run, thread_id: message.thread_id});
        const events = [
          {type: "RUN_STARTED", runId: run, threadId: message.thread_id},
          {type: "TEXT_MESSAGE_CONTENT", messageId: "reply", delta: `reply ${threads.length}`},
          {type: "RUN_FINISHED", runId: run, threadId: message.thread_id, result: {status: "answered"}},
        ];
        events.forEach((event, i) => send(socket, {type: "event", sequence: i + 1, run_id: run, durable: true, event}));
      }
    });
  });
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}/v1/agent`, token});
  try {
    session.connect(); await until(() => session.state.view.status === "ready");
    session.submit("hello"); await until(() => session.state.view.status === "answered");
    session.submit("follow-up"); await until(() => threads.length === 2 && session.state.view.status === "answered");
    assert.equal(connections, 1); assert.equal(threads[0], threads[1]);
    assert.equal(session.state.view.entries.length, 4);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});
test("disconnect reattaches from the contiguous cursor and does not repeat a reply", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let attachCursor = -1;
  server.on("connection", (socket) => socket.on("message", (data) => {
    const message = JSON.parse(data.toString());
    if (message.type === "client.hello") send(socket, {type: "server.hello", harness: {display_name: "Fixture", capabilities: ["streaming"]}});
    if (message.type === "task.start") {
      send(socket, {type: "task.accepted", request_id: message.request_id, thread_id: message.thread_id, run_id: "run"});
      send(socket, {type: "event", sequence: 1, run_id: "run", durable: true, event: {type: "TEXT_MESSAGE_CONTENT", messageId: "reply", delta: "Hello"}});
    }
    if (message.type === "events.ack" && message.through_sequence === 1) socket.terminate();
    if (message.type === "task.attach") {
      attachCursor = message.after_sequence;
      send(socket, {type: "event", sequence: 2, run_id: "run", durable: true, event: {type: "RUN_FINISHED", runId: "run", threadId: "thread"}});
    }
  }));
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}`, token, reconnectMs: 1});
  try {
    session.connect(); await until(() => session.state.view.status === "ready");
    session.submit("hello"); await until(() => session.state.view.status === "completed");
    assert.equal(attachCursor, 1); assert.equal(session.state.view.entries.length, 2);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});
test("local token is never sent to arbitrary hosts or URL query strings", () => {
  for (const url of ["ws://example.com", "ws://127.0.0.1?token=secret", "ws://user:pass@localhost"])
    assert.throws(() => new RemoteSession({url, token}));
});

test("model status reconciles new conversations and reconnect without replaying selection", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let connections = 0, selections = 0;
  const choices = new Map<string, string>();
  server.on("connection", socket => {
    connections++;
    socket.on("message", data => {
      const message = JSON.parse(data.toString());
      if (message.type === "client.hello") send(socket, {type: "server.hello", harness: {display_name: "Fixture", capabilities: ["model_selection"]}, coding_model: {provider: "fixture", name: "alpha"}});
      if (message.type === "model.request") {
        if (message.operation === "select") {
          selections++; choices.set(message.thread_id, message.name); socket.terminate(); return;
        }
        send(socket, {type: "model.result", request_id: message.request_id, operation: message.operation,
          data: {coding_model: {provider: "fixture", name: choices.get(message.thread_id) ?? "alpha", readiness: "configured"}}});
      }
    });
  });
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}`, token, reconnectMs: 1});
  try {
    session.connect(); await until(() => session.state.view.model === "alpha [fixture]");
    await assert.rejects(session.modelRequest("select", {provider: "fixture", name: "beta"}), /interrupted/);
    await until(() => connections === 2 && session.state.view.model === "beta [fixture]");
    session.newConversation(); await until(() => session.state.view.model === "alpha [fixture]");
    assert.equal(selections, 1);
    assert.deepEqual(session.state.view.entries, []);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});

test("resume catches up an active run without starting work; cancelling a delayed load preserves the session", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let delayed: (() => void) | undefined, attached = 0, starts = 0;
  server.on("connection", socket => socket.on("message", data => {
    const message = JSON.parse(data.toString());
    if (message.type === "client.hello") send(socket, {type: "server.hello", harness: {display_name: "Fixture", capabilities: ["sessions", "session_history"]}});
    if (message.type === "task.start") starts++;
    if (message.type === "session.request") {
      const run = {run_id: "saved-run", thread_id: message.thread_id, input: "saved request", status: "running"};
      const reply = () => send(socket, {type: "session.result", request_id: message.request_id, operation: message.operation,
        data: message.operation === "get" ? {thread_id: message.thread_id, runs: [run], latest: run, queue: [], next_before_run_id: null}
          : message.operation === "events" ? {thread_id: message.thread_id, run, high_water_sequence: 1, next_after_sequence: null,
            events: [{type: "event", protocol_version: 1, run_id: "saved-run", sequence: 1, durable: true,
              event: {type: "RUN_STARTED", runId: "saved-run", threadId: message.thread_id}}]}
          : {thread_id: message.thread_id, queue: [], latest: run}});
      if (message.thread_id === "delayed") delayed = reply; else reply();
    }
    if (message.type === "task.attach") {
      attached++; assert.equal(message.after_sequence, 1);
      send(socket, {type: "event", run_id: "saved-run", sequence: 2, durable: true, event: {type: "TEXT_MESSAGE_CONTENT", messageId: "reply", delta: "caught up"}});
      send(socket, {type: "event", run_id: "saved-run", sequence: 3, durable: true, event: {type: "RUN_FINISHED", runId: "saved-run", threadId: "saved", result: {status: "answered"}}});
    }
  }));
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}`, token});
  try {
    session.connect(); await until(() => session.state.view.status === "ready");
    const abort = new AbortController();
    const pending = session.resume("delayed", abort.signal);
    const rejected = assert.rejects(pending, {name: "AbortError"});
    await until(() => delayed !== undefined); abort.abort();
    session.newConversation(); const original = session.state.threadId;
    delayed!(); await rejected;
    assert.equal(session.state.threadId, original);
    await session.resume("saved", new AbortController().signal);
    await until(() => session.state.view.status === "answered");
    assert.equal(session.state.threadId, "saved"); assert.equal(session.state.cursor, 3);
    assert.equal(session.state.view.entries.length, 2);
    assert.equal(attached, 1); assert.equal(starts, 0);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});

test("provider controls are correlated, stay out of chat, and mutations are not replayed after disconnect", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let connections = 0, logins = 0;
  server.on("connection", socket => {
    connections++;
    socket.on("message", data => {
      const message = JSON.parse(data.toString());
      if (message.type === "client.hello") send(socket, {type: "server.hello", harness: {display_name: "Fixture", capabilities: ["provider_controls"]}});
      if (message.type === "provider.request" && message.operation === "status") send(socket, {
        type: "provider.result", request_id: message.request_id, operation: "status", data: {providers: []},
      });
      if (message.type === "provider.request" && message.operation === "login") { logins++; socket.terminate(); }
    });
  });
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}`, token, reconnectMs: 1});
  try {
    await assert.rejects(session.providerRequest("login"), /not available/);
    session.connect(); await until(() => session.state.view.status === "ready");
    assert.deepEqual(await session.providerRequest("status"), {providers: []});
    await assert.rejects(session.providerRequest("login", {provider: "openai_codex"}), /interrupted/);
    await until(() => connections === 2);
    await delay(20);
    assert.equal(logins, 1);
    assert.deepEqual(session.state.view.entries, []);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});

test("acknowledged steering is not retried after reconnect and cancellation finishes quietly", async () => {
  const server = new WebSocketServer({host: "127.0.0.1", port: 0});
  await once(server, "listening");
  const address = server.address(); assert.ok(address && typeof address === "object");
  let steers = 0, attaches = 0;
  server.on("connection", socket => socket.on("message", data => {
    const message = JSON.parse(data.toString());
    if (message.type === "client.hello") send(socket, {type: "server.hello", harness: {display_name: "Fixture", capabilities: ["streaming", "steering", "cancel"]}});
    if (message.type === "task.start") {
      send(socket, {type: "task.accepted", request_id: message.request_id, thread_id: message.thread_id, run_id: "run"});
      send(socket, {type: "event", sequence: 1, run_id: "run", durable: true, event: {type: "RUN_STARTED", runId: "run", threadId: message.thread_id}});
    }
    if (message.type === "task.attach") attaches++;
    if (message.type === "task.steer") {
      steers++;
      send(socket, {type: "control.result", run_id: "run", command_id: message.idempotency_key, operation: "steer", accepted: true, detail: "queued for next boundary"});
    }
    if (message.type === "task.cancel") {
      send(socket, {type: "event", sequence: 2, run_id: "run", durable: true, event: {type: "RUN_FINISHED", runId: "run", threadId: "thread", result: {status: "cancelled"}}});
      send(socket, {type: "control.result", run_id: "run", command_id: message.idempotency_key, operation: "cancel", accepted: true});
    }
  }));
  const session = new RemoteSession({url: `ws://127.0.0.1:${address.port}`, token, reconnectMs: 1});
  let updates = 0;
  const unsubscribe = session.subscribe(() => updates++);
  try {
    session.connect(); await until(() => session.state.view.status === "ready");
    session.submit("work"); await until(() => session.state.view.status === "running");
    session.submit("new direction"); await until(() => session.state.view.notice === "queued for next boundary");
    for (const socket of server.clients) socket.terminate();
    await until(() => attaches === 1);
    session.cancel(); await until(() => session.state.view.status === "cancelled");
    assert.equal(steers, 1);
    assert.equal(session.state.view.notice, "");
    unsubscribe(); session.close();
    const count = updates; await delay(20); assert.equal(updates, count);
  } finally { session.close(); for (const socket of server.clients) socket.terminate(); server.close(); await once(server, "close"); }
});
