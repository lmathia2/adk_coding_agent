import { randomUUID } from "node:crypto";
import WebSocket from "ws";
import { decode, object, string, type WireObject } from "./protocol.js";
import { SessionState } from "./session.js";

export interface RemoteOptions {
  url: string;
  token: string;
  reconnectMs?: number;
}

/** Authenticated local transport; no model calls or tool execution in this process. */
export class RemoteSession {
  readonly state = new SessionState();
  private socket?: WebSocket;
  private stopped = true;
  private negotiated = false;
  private timer?: NodeJS.Timeout;
  private heartbeat?: NodeJS.Timeout;
  private helloTimer?: NodeJS.Timeout;
  private sessionPoll?: NodeJS.Timeout;
  private refreshing = false;
  private refreshAgain = false;
  private latestSnapshot?: WireObject;
  private lastPong = 0;
  private attempts = 0;
  private pendingStart?: WireObject;
  private controls = new Map<string, WireObject>();
  private requests = new Map<string, {message: WireObject; resolve: (data: WireObject) => void; reject: (error: Error) => void; timer: NodeJS.Timeout}>();
  private listeners = new Set<() => void>();
  constructor(private readonly options: RemoteOptions) {
    const url = new URL(options.url);
    if (url.protocol !== "ws:" || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      || url.username || url.password || url.search || url.hash) throw new Error("Use a loopback ws:// server URL without credentials or query parameters");
    if (Buffer.byteLength(options.token) < 32) throw new Error("Local server token must contain at least 32 bytes");
  }
  subscribe(listener: () => void): () => void { this.listeners.add(listener); return () => this.listeners.delete(listener); }
  private changed(): void { for (const listener of this.listeners) listener(); }
  connect(): void { if (this.stopped) { this.stopped = false; this.open(); } }
  private open(): void {
    if (this.stopped) return;
    const socket = new WebSocket(this.options.url, {
      headers: {Authorization: `Bearer ${this.options.token}`}, maxPayload: 1_048_576,
      handshakeTimeout: 5_000,
    });
    this.socket = socket;
    this.negotiated = false;
    socket.on("open", () => {
      if (this.stopped || this.socket !== socket) return;
      this.send({type: "client.hello", protocol_versions: [1], client_name: "pi-adk-terminal"});
      this.helloTimer = setTimeout(() => socket.terminate(), 5_000);
    });
    socket.on("message", (data) => {
      if (this.stopped || this.socket !== socket) return;
      try { this.receive(decode(data.toString())); }
      catch { this.state.error("Invalid server protocol message; connection stopped"); this.close(); }
      this.changed();
    });
    socket.on("error", () => {
      if (this.stopped || this.socket !== socket) return;
      this.state.view.notice = "Server unavailable — reconnecting…"; this.changed();
    });
    socket.on("close", (code) => {
      if (this.stopped || this.socket !== socket) return;
      this.negotiated = false;
      clearInterval(this.heartbeat); clearTimeout(this.helloTimer); clearInterval(this.sessionPoll);
      for (const [id, request] of this.requests) {
        if (request.message.type !== "provider.request" && request.message.type !== "model.request") continue;
        clearTimeout(request.timer); this.requests.delete(id);
        request.reject(new Error(`Management request interrupted. Reconnect and check ${request.message.type === "model.request" ? "/model" : "/auth"} before retrying.`));
      }
      if (code === 1008) {
        this.stopped = true;
        this.state.view.status = "disconnected";
        this.state.error("Server authentication rejected. Check the server token and state directory.");
      }
      if (!this.stopped) {
        this.state.view.notice = "Disconnected — reconnecting and replaying…";
        this.timer = setTimeout(() => this.open(), Math.min(10_000, (this.options.reconnectMs ?? 250) * 2 ** this.attempts++));
      }
      this.changed();
    });
  }
  private send(message: WireObject): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return;
    if (this.socket.bufferedAmount > 1_048_576) { this.socket.terminate(); return; }
    this.socket.send(JSON.stringify(message.type === "client.hello" ? message : {protocol_version: 1, ...message}));
  }
  private receive(message: WireObject): void {
    switch (message.type) {
      case "server.hello": {
        clearTimeout(this.helloTimer);
        this.negotiated = true; this.attempts = 0;
        this.state.view.notice = "";
        if (!this.state.active) this.state.view.status = "ready";
        const capabilities = object(message.harness).capabilities;
        this.state.capabilities = new Set(Array.isArray(capabilities) ? capabilities.filter((item): item is string => typeof item === "string") : []);
        if (!this.state.active) this.state.model(message.coding_model);
        this.refreshModel();
        if (this.pendingStart) this.send(this.pendingStart);
        else if (this.state.runId) this.send({type: "task.attach", run_id: this.state.runId, after_sequence: this.state.cursor});
        for (const control of this.controls.values()) this.send(control);
        for (const request of this.requests.values()) this.send(request.message);
        clearInterval(this.sessionPoll);
        this.sessionPoll = setInterval(() => {
          if (this.state.active || this.state.view.pending?.length) this.refreshConversation();
        }, 1_000);
        this.refreshConversation();
        this.lastPong = Date.now();
        clearInterval(this.heartbeat);
        this.heartbeat = setInterval(() => {
          if (Date.now() - this.lastPong > 30_000) this.socket?.terminate();
          else this.send({type: "ping", nonce: randomUUID()});
        }, 10_000);
        break;
      }
      case "task.accepted":
        if (message.request_id !== this.pendingStart?.request_id) break;
        this.state.runId = string(message.run_id); this.state.threadId = string(message.thread_id);
        this.pendingStart = undefined; break;
      case "event": {
        const outcome = this.state.envelope(message);
        if (outcome === "gap") { this.socket?.terminate(); break; }
        if (outcome === "applied" && message.durable) this.send({type: "events.ack", run_id: this.state.runId, through_sequence: this.state.cursor});
        if (outcome === "applied" && !this.state.active) {
          this.controls.clear();
          if (this.latestSnapshot) this.acceptSnapshot(this.latestSnapshot);
          this.refreshConversation();
        }
        break;
      }
      case "control.result":
        if (message.run_id !== this.state.runId || !this.controls.has(string(message.command_id))) break;
        this.controls.delete(string(message.command_id));
        this.state.view.notice = typeof message.detail === "string" ? message.detail
          : `${message.operation} ${message.accepted ? "accepted" : "rejected"}`;
        break;
      case "error":
        if (typeof message.request_id === "string" && this.requests.has(message.request_id)) {
          const request = this.requests.get(message.request_id)!;
          clearTimeout(request.timer); this.requests.delete(message.request_id);
          request.reject(new Error(string(message.message))); break;
        }
        this.state.error(string(message.message));
        if (message.request_id === this.pendingStart?.request_id) {
          this.pendingStart = undefined; this.state.view.status = "failed";
        }
        break;
      case "session.result":
      case "provider.result":
      case "model.result": {
        const id = string(message.request_id), request = this.requests.get(id);
        if (!request) break;
        if (message.type !== String(request.message.type).replace(".request", ".result") || message.operation !== request.message.operation) throw new Error("Mismatched response");
        clearTimeout(request.timer); this.requests.delete(id);
        request.resolve(object(message.data)); break;
      }
      case "pong": this.lastPong = Date.now(); break;
    }
  }
  submit(text: string, mode: "steer" | "followUp" = "steer"): void {
    if (!text.trim()) return;
    if (!this.negotiated) throw new Error("Wait for the server connection before sending");
    if (this.pendingStart) throw new Error("Waiting for the current request to be accepted");
    const id = randomUUID();
    if (this.state.active) {
      if (mode === "followUp") {
        if (Buffer.byteLength(text) > 50_000) throw new Error("Follow-up exceeds 50000 UTF-8 bytes");
        this.request("follow_up", {thread_id: this.state.threadId, content: text})
          .then(data => { this.acceptSnapshot(data); this.notice("Follow-up accepted"); })
          .catch(error => this.notice(`Follow-up not confirmed: ${error.message}. Draft: ${text}`));
        this.notice("Queueing follow-up…"); return;
      }
      if (Buffer.byteLength(text) > 4096) throw new Error("Steering is limited to 4096 UTF-8 bytes");
      this.control("steer", {content: text, priority: 0}, id);
    } else {
      if (Buffer.byteLength(text) > 50_000) throw new Error("Request exceeds 50000 UTF-8 bytes");
      this.state.begin();
      this.pendingStart = {type: "task.start", request_id: id, idempotency_key: id, thread_id: this.state.threadId, input: text};
      this.send(this.pendingStart);
    }
    this.state.user(id, text); this.changed();
  }
  private control(operation: "steer" | "cancel", extra: WireObject = {}, id = randomUUID()): void {
    const capability = operation === "steer" ? "steering" : "cancel";
    if (!this.state.capabilities.has(capability)) throw new Error(`Harness does not support ${operation}`);
    if (this.controls.size >= 32) throw new Error("Waiting for outstanding control requests");
    const message = {type: `task.${operation}`, run_id: this.state.runId, idempotency_key: id, ...extra};
    this.controls.set(id, message); this.send(message);
    this.state.view.notice = `${operation} requested`; this.changed();
  }
  cancel(): void { if (this.state.active && this.state.runId) this.control("cancel"); }
  request(operation: string, parameters: WireObject = {}): Promise<WireObject> {
    return this.managementRequest("session", "sessions", operation, parameters);
  }
  providerRequest(operation: "status" | "login" | "cancel_login" | "logout", parameters: WireObject = {}): Promise<WireObject> {
    return this.managementRequest("provider", "provider_controls", operation, parameters);
  }
  async modelRequest(operation: "status" | "catalog" | "select", parameters: WireObject = {}): Promise<WireObject> {
    const thread = this.state.threadId;
    const data = await this.managementRequest("model", "model_selection", operation, {...parameters, thread_id: thread});
    if (thread === this.state.threadId && !this.state.active && !this.stopped) {
      this.state.model(data.coding_model); this.changed();
    }
    return data;
  }
  private refreshModel(): void {
    if (this.negotiated && !this.state.active && this.state.capabilities.has("model_selection")) {
      void this.modelRequest("status").catch(error => this.notice(error.message));
    }
  }
  private managementRequest(domain: string, capability: string, operation: string, parameters: WireObject): Promise<WireObject> {
    if (!this.negotiated || !this.state.capabilities.has(capability)) return Promise.reject(new Error(`${domain} controls are not available`));
    if (this.requests.size >= 32) return Promise.reject(new Error("Waiting for outstanding requests"));
    const id = randomUUID(), message = {...parameters, type: `${domain}.request`, request_id: id, operation};
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.requests.delete(id); reject(new Error(`${domain} request timed out; check ${domain === "provider" ? "/auth" : domain === "model" ? "/model" : "/queue"} before retrying`)); }, 15_000);
      this.requests.set(id, {message, resolve, reject, timer}); this.send(message);
    });
  }
  private notice(text: string): void { if (!this.stopped) { this.state.view.notice = text; this.changed(); } }
  private acceptSnapshot(data: WireObject): void {
    if (this.stopped || data.thread_id !== this.state.threadId) return;
    this.latestSnapshot = data;
    this.state.view.pending = Array.isArray(data.queue) ? data.queue.map(item => {
      const value = object(item); return {item_id: string(value.item_id), preview: string(value.preview)};
    }) : [];
    const next = data.next && typeof data.next === "object" ? object(data.next) : undefined;
    if (next && data.after_run_id === this.state.runId && !this.state.active && !this.pendingStart) {
      const runId = string(next.run_id);
      this.state.begin(); this.state.runId = runId;
      this.state.user(`${runId}:user`, string(next.input));
      this.send({type: "task.attach", run_id: runId, after_sequence: 0});
    }
    if (data.after_run_id === null && object(data.latest).run_id !== this.state.runId) this.refreshConversation();
    this.changed();
  }
  private refreshConversation(): void {
    if (!this.negotiated || !this.state.runId || !this.state.capabilities.has("sessions")) return;
    if (this.refreshing) { this.refreshAgain = true; return; }
    this.refreshing = true;
    this.request("state", {thread_id: this.state.threadId, after_run_id: this.state.runId}).then(data => this.acceptSnapshot(data))
      .catch(error => this.notice(error.message)).finally(() => {
        this.refreshing = false;
        if (this.refreshAgain) { this.refreshAgain = false; this.refreshConversation(); }
      });
  }
  async continueQueue(): Promise<void> {
    this.acceptSnapshot(await this.request("continue", {thread_id: this.state.threadId}));
  }
  async clearQueue(): Promise<void> {
    const thread = this.state.threadId;
    const snapshot = await this.request("state", {thread_id: thread});
    for (const item of snapshot.queue as WireObject[]) {
      await this.request("remove_follow_up", {thread_id: thread, item_id: item.item_id});
    }
    this.refreshConversation();
  }
  async queueStatus(): Promise<void> {
    this.acceptSnapshot(await this.request("state", {thread_id: this.state.threadId}));
    this.notice("Alt+Enter queues · /queue continue resumes · /queue clear removes pending follow-ups");
  }
  newConversation(): void {
    this.state.newConversation(); this.latestSnapshot = undefined;
    this.refreshModel(); this.changed();
  }
  close(): void {
    this.stopped = true; this.negotiated = false;
    clearTimeout(this.timer); clearInterval(this.heartbeat); clearTimeout(this.helloTimer); clearInterval(this.sessionPoll);
    for (const request of this.requests.values()) { clearTimeout(request.timer); request.reject(new Error("Terminal disconnected")); }
    this.requests.clear();
    this.socket?.close();
  }
}
