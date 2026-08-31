import { Text, matchesKey } from "@earendil-works/pi-tui";
import { loadHistory, type HistoryPort } from "./history.js";
import { object, string } from "./protocol.js";
import { Selector } from "./selector.js";
import { safeText, Transcript } from "./transcript.js";
import { theme } from "./theme.js";
import type { Dialog } from "./view.js";

export interface SessionPort extends HistoryPort { resume(thread: string, signal: AbortSignal): Promise<void>; }

/** Server-scoped recent conversations; dismissing a load never switches sessions. */
export class SessionPicker implements Dialog {
  private readonly selector: Selector;
  private readonly abort = new AbortController();
  private notice = "Loading recent conversations…";
  private loading = false;
  get focused(): boolean { return this.selector.focused; }
  set focused(value: boolean) { this.selector.focused = value; }
  constructor(private readonly port: SessionPort, private readonly refresh: () => void, private readonly close: () => void) {
    this.selector = new Selector("Resume conversation", [], item => void this.choose(item.value), close);
    void this.load();
  }
  private async load(): Promise<void> {
    try {
      const data = await this.port.request("list");
      if (this.abort.signal.aborted) return;
      if (!Array.isArray(data.conversations)) throw new Error("Invalid conversation list");
      this.selector.updateItems(data.conversations.map(value => {
        const item = object(value);
        return {value: string(item.thread_id), label: string(item.input).split("\n")[0] || "Untitled conversation",
          description: `${item.status} · ${item.updated_at} · ${item.thread_id}`};
      }));
      this.notice = "Recent conversations for this server workspace and harness. Enter restores history, not stopped work.";
    } catch (error) { this.notice = error instanceof Error ? error.message : "Unable to list conversations"; }
    if (!this.abort.signal.aborted) this.refresh();
  }
  private async choose(thread: string): Promise<void> {
    if (this.loading) return;
    this.loading = true; this.notice = "Restoring conversation… Esc cancels"; this.refresh();
    try {
      await this.port.resume(thread, this.abort.signal);
      if (!this.abort.signal.aborted) this.close();
    } catch (error) {
      this.notice = error instanceof Error ? error.message : "Unable to restore conversation";
    } finally { this.loading = false; if (!this.abort.signal.aborted) this.refresh(); }
  }
  handleInput(data: string): void { this.selector.handleInput(data); }
  render(width: number): string[] { return [...new Text(theme.dim(safeText(this.notice)), 1, 1).render(width), ...this.selector.render(width)]; }
  invalidate(): void { this.selector.invalidate(); }
  dispose(): void { this.abort.abort(); }
}

/** Older pages are a read-only view; Escape returns to the live session and draft. */
export class HistoryDialog implements Dialog {
  focused = false;
  private readonly abort = new AbortController();
  private transcript?: Transcript;
  private older?: string;
  private loading = false;
  private notice = "Loading history…";
  constructor(private readonly port: HistoryPort, private readonly thread: string,
    private readonly refresh: () => void, private readonly close: () => void) { void this.load(); }
  private async load(before?: string): Promise<void> {
    if (this.loading) return;
    this.loading = true;
    try {
      const page = await loadHistory(this.port, this.thread, this.abort.signal, before);
      if (this.abort.signal.aborted) return;
      this.transcript = new Transcript(page.state.view); this.older = page.older;
      this.notice = `${page.turns} ${before ? "earlier" : "recent"} turns · bounded display · read-only`;
    } catch (error) { this.notice = error instanceof Error ? error.message : "Unable to load history"; }
    finally { this.loading = false; if (!this.abort.signal.aborted) this.refresh(); }
  }
  handleInput(data: string): void {
    if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c")) this.close();
    else if (matchesKey(data, "enter") && this.older) void this.load(this.older);
    else if (matchesKey(data, "ctrl+o") && this.transcript) this.transcript.expanded = !this.transcript.expanded;
  }
  render(width: number): string[] {
    return [...new Text(theme.accent("Conversation history") + "\n" + theme.dim(safeText(this.notice)), 1, 1).render(width),
      ...(this.transcript?.render(width) ?? []),
      ...new Text(theme.dim(`${this.loading ? "Loading… · " : this.older ? "Enter earlier turns · " : "Beginning of conversation · "}Ctrl+O tools · Esc return`), 1, 1).render(width)];
  }
  invalidate(): void { this.transcript?.invalidate(); }
  dispose(): void { this.abort.abort(); }
}

export class SessionInfo implements Dialog {
  focused = false;
  constructor(private readonly text: string, private readonly close: () => void) {}
  handleInput(data: string): void { if (matchesKey(data, "escape") || matchesKey(data, "enter") || matchesKey(data, "ctrl+c")) this.close(); }
  render(width: number): string[] { return new Text(safeText(this.text) + "\n\nEnter or Esc returns", 1, 1).render(width); }
  invalidate(): void {}
}
