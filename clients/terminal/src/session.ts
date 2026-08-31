import { randomUUID } from "node:crypto";
import type { SessionView, ToolEntry, TranscriptEntry } from "./contracts.js";
import { object, string, type WireObject } from "./protocol.js";

const bounded = (text: string): string => Buffer.byteLength(text) <= 65_536 ? text
  : Buffer.from(text).subarray(0, 65_480).toString("utf8") + "\n[display truncated]";

/** Deterministic view reducer. Audit/state events are deliberately not chat entries. */
export class SessionState {
  readonly view: SessionView = {entries: [], status: "connecting", workspace: "server workspace", model: "waiting for server", notice: ""};
  threadId: string = randomUUID();
  runId = "";
  cursor = 0;
  capabilities = new Set<string>();
  get active(): boolean { return ["starting", "running", "paused"].includes(this.view.status); }
  private append(entry: TranscriptEntry): void {
    this.view.entries.push(entry);
    if (this.view.entries.length > 400) this.view.entries.splice(0, this.view.entries.length - 400);
  }
  user(id: string, text: string): void { this.append({kind: "user", id, text: bounded(text)}); }
  error(text: string): void { this.append({kind: "error", id: randomUUID(), text: bounded(text)}); }
  begin(): void { this.runId = ""; this.cursor = 0; this.view.status = "starting"; this.view.notice = ""; this.view.selectedSkills = []; this.view.approvals = []; }
  restore(history: SessionState): void {
    this.threadId = history.threadId; this.runId = history.runId; this.cursor = history.cursor;
    Object.assign(this.view, history.view, {workspace: this.view.workspace, connected: this.view.connected});
  }
  newConversation(): void {
    if (this.active) throw new Error("Cancel active work before starting a new conversation");
    this.threadId = randomUUID(); this.runId = ""; this.cursor = 0;
    this.view.entries = []; this.view.status = "ready"; this.view.notice = "";
    this.view.pending = [];
    this.view.selectedSkills = [];
    this.view.approvals = [];
  }
  resources(data: WireObject): void {
    const workspace = string(data.workspace);
    if (!Array.isArray(data.items) || !Array.isArray(data.warnings)) throw new Error("Invalid resource inventory");
    const resources = {stateRoot: string(data.state_root), configurationRoot: string(data.configuration_root),
      runDatabase: string(data.run_database), projectTrusted: data.project_trusted === true,
      warnings: data.warnings.map(string), truncated: data.truncated === true,
      items: data.items.map(value => { const item = object(value); return {kind: string(item.kind), name: string(item.name),
        path: typeof item.path === "string" ? item.path : undefined, description: string(item.description), status: string(item.status)}; })};
    this.view.workspace = workspace;
    this.view.resources = resources;
  }
  model(value: unknown): void {
    if (!value || typeof value !== "object") return;
    const model = object(value);
    if (typeof model.provider === "string" && typeof model.name === "string") {
      this.view.model = `${model.name} [${model.provider}]`;
      if (model.readiness === "authentication_required") this.view.notice = "Authentication required — use /login";
      else if (this.view.notice === "Authentication required — use /login") this.view.notice = "";
    }
  }
  private tool(id: string): ToolEntry {
    const key = `${this.runId}:tool:${id}`;
    const existing = this.view.entries.find((entry) => entry.id === key);
    if (existing?.kind === "tool") return existing;
    const entry: ToolEntry = {kind: "tool", id: key, name: "tool", arguments: "", done: false};
    this.append(entry);
    return entry;
  }
  private closeTools(): void {
    for (const entry of this.view.entries) {
      if (entry.kind === "tool" && entry.id.startsWith(`${this.runId}:tool:`)) entry.done = true;
    }
  }
  envelope(message: WireObject): "applied" | "duplicate" | "gap" {
    if (message.run_id !== this.runId) return "duplicate";
    const sequence = Number(message.sequence);
    if (sequence <= this.cursor) return "duplicate";
    if (sequence !== this.cursor + 1) return "gap";
    const event = object(message.event);
    const type = string(event.type);
    switch (type) {
      case "RUN_STARTED":
        this.view.status = "running";
        if (event.metadata) this.model(object(event.metadata)["coding.model"]);
        break;
      case "RUN_FINISHED": {
        this.closeTools();
        this.view.approvals = [];
        const result = event.result && typeof event.result === "object" ? object(event.result) : {};
        this.view.status = typeof result.status === "string" ? result.status : "completed";
        this.view.notice = result.verified === true ? "Verification passed" : "";
        break;
      }
      case "RUN_ERROR": this.closeTools(); this.view.approvals = []; this.view.status = "failed"; this.error(string(event.message)); break;
      case "TEXT_MESSAGE_START": break;
      case "TEXT_MESSAGE_CONTENT": {
        const id = `${this.runId}:message:${string(event.messageId)}`;
        let entry = this.view.entries.find((item) => item.id === id);
        if (!entry) { entry = {kind: "assistant", id, text: ""}; this.append(entry); }
        if (entry.kind === "assistant") entry.text = bounded(entry.text + string(event.delta));
        break;
      }
      case "TOOL_CALL_START": this.tool(string(event.toolCallId)).name = string(event.toolCallName); break;
      case "TOOL_CALL_ARGS": {
        const tool = this.tool(string(event.toolCallId));
        tool.arguments = bounded(tool.arguments + string(event.delta)); break;
      }
      case "TOOL_CALL_RESULT": {
        const tool = this.tool(string(event.toolCallId));
        tool.result = bounded(string(event.content)); tool.done = true; break;
      }
      case "CUSTOM":
        if (event.name === "coding.model.status") this.model(event.value);
        break;
      case "STATE_DELTA":
        if (Array.isArray(event.delta)) for (const change of event.delta.map(object)) {
          if (change.path === "/selected_skill_names" && Array.isArray(change.value)) this.view.selectedSkills = change.value.map(string).slice(0, 32);
        }
        break;
      // End-of-arguments is not end-of-execution. Other lifecycle/state/custom
      // events remain in server replay storage, not the conversational transcript.
    }
    this.cursor = sequence;
    return "applied";
  }
}
