import { Text, type SelectItem } from "@earendil-works/pi-tui";
import { Selector } from "./selector.js";
import { object, string, type WireObject } from "./protocol.js";
import { safeText } from "./transcript.js";
import { theme } from "./theme.js";
import type { Dialog } from "./view.js";

export interface ModelPort {
  modelRequest(operation: "status" | "catalog" | "select", parameters?: WireObject): Promise<WireObject>;
}

/** Incrementally refreshed catalog; selection is a server action, not a footer edit. */
export class ModelPicker implements Dialog {
  private readonly selector: Selector;
  private notice = "Loading models…";
  private choices = new Map<string, WireObject>();
  private stopped = false;
  private selecting = false;
  private timer?: NodeJS.Timeout;
  get focused(): boolean { return this.selector.focused; }
  set focused(value: boolean) { this.selector.focused = value; }
  constructor(private readonly port: ModelPort, private readonly refresh: () => void,
    private readonly close: () => void, private readonly notify: (message: string) => void,
    private readonly pollMs = 500) {
    this.selector = new Selector("Select model", [], (item, persist) => void this.choose(item, persist), close, true);
    void this.load();
  }
  private async load(): Promise<void> {
    try {
      const data = await this.port.modelRequest("catalog");
      if (this.stopped) return;
      const selected = object(data.selected), saved = object(data.default);
      const entries = Array.isArray(data.models) ? data.models.map(object) : [];
      this.choices.clear();
      this.selector.updateItems(entries.map(entry => {
        const choice = object(entry.choice), provider = string(choice.provider), name = string(choice.name);
        const id = JSON.stringify([provider, name]); this.choices.set(id, choice);
        return {value: id, label: `${name} [${provider}]${selected.provider === provider && selected.name === name ? " ✓" : ""}`,
          description: `${saved.provider === provider && saved.name === name ? "default · " : ""}${string(entry.display_name)}`};
      }));
      this.notice = data.refreshing ? "Refreshing model catalog…" : typeof data.error === "string" ? data.error : typeof data.warning === "string" ? data.warning : "Changes apply to the next turn. Use /login to add provider access.";
      if (data.refreshing) this.timer = setTimeout(() => void this.load(), this.pollMs);
    } catch (error) {
      if (this.stopped) return;
      this.notice = error instanceof Error ? error.message : "Model catalog unavailable";
    }
    if (!this.stopped) this.refresh();
  }
  private async choose(item: SelectItem, persist: boolean): Promise<void> {
    const choice = this.choices.get(item.value);
    if (!choice || this.selecting) return;
    this.selecting = true; this.notice = "Saving model choice…"; this.refresh();
    try {
      const result = await this.port.modelRequest("select", {provider: choice.provider, name: choice.name, persist});
      if (this.stopped) return;
      const selected = object(result.selected);
      this.selecting = false;
      this.notify(`Next turn: ${selected.name} [${selected.provider}]${persist ? `. Default saved on server: ${result.default_path}` : ""}`);
      this.close();
    } catch (error) {
      if (!this.stopped) { this.notice = error instanceof Error ? error.message : "Model selection failed"; this.refresh(); }
    } finally { this.selecting = false; }
  }
  handleInput(data: string): void { this.selector.handleInput(data); }
  render(width: number): string[] { return [...new Text(theme.dim(safeText(this.notice)), 1, 1).render(width), ...this.selector.render(width)]; }
  invalidate(): void { this.selector.invalidate(); }
  dispose(): void {
    if (!this.stopped && this.selecting) this.notify("Model selection may still finish on the server; reopen /model to confirm.");
    this.stopped = true; clearTimeout(this.timer);
  }
}
