import { Container, Editor, Text, matchesKey, type TUI, type SelectItem, type Component, type Focusable } from "@earendil-works/pi-tui";
import type { SessionActions, SessionView } from "./contracts.js";
import { Transcript, safeText } from "./transcript.js";
import { editorTheme, theme } from "./theme.js";
import { CommandCompletion } from "./commands.js";
import { Selector } from "./selector.js";
import { resourceText } from "./resources.js";

export interface Dialog extends Component, Focusable { handleInput(data: string): void; dispose?(): void; }

/** Presentation depends on a tiny session port, not Pi's AgentSession or ADK. */
export class TerminalView {
  readonly editor: Editor;
  readonly transcript: Transcript;
  readonly root = new Container();
  private readonly status = new Text("", 1, 0);
  private readonly queue = new Text("", 1, 0);
  private readonly footer = new Text("", 1, 0);
  private readonly resources = new Text("", 1, 0);
  private readonly inputRegion = new Container();
  private dialog?: Dialog;
  private disposed = false;
  get hasDialog(): boolean { return this.dialog !== undefined; }
  private readonly removeListener: () => void;
  constructor(readonly tui: TUI, readonly state: SessionView, private readonly actions: SessionActions) {
    this.editor = new Editor(tui, editorTheme, { paddingX: 1 });
    this.transcript = new Transcript(state);
    this.root.addChild(new Text(theme.accent("adk-agent") + theme.dim(" · Pi terminal / ADK harness"), 1, 0));
    this.root.addChild(new Text(theme.dim("esc interrupt · ctrl+c clear · ctrl+d exit · / commands · ctrl+o more"), 1, 1));
    this.root.addChild(this.resources);
    this.root.addChild(this.transcript);
    this.root.addChild(this.queue);
    this.root.addChild(this.status);
    this.inputRegion.addChild(this.editor);
    this.root.addChild(this.inputRegion);
    this.root.addChild(this.footer);
    tui.addChild(this.root);
    tui.setFocus(this.editor);
    this.editor.onSubmit = (text) => this.submit(text, "steer");
    this.removeListener = tui.addInputListener((data) => {
      if (this.dialog) {
        this.dialog.handleInput(data); this.refresh(); return {consume: true};
      }
      if (matchesKey(data, "ctrl+o")) {
        this.transcript.expanded = !this.transcript.expanded;
      } else if (matchesKey(data, "escape")) {
        actions.cancel();
      } else if (matchesKey(data, "ctrl+c")) {
        this.editor.setText("");
      } else if (matchesKey(data, "ctrl+d") && !this.editor.getText()) {
        actions.quit();
      } else if (matchesKey(data, "alt+enter")) {
        this.submit(this.editor.getExpandedText(), "followUp");
      } else { return; }
      this.refresh();
      return { consume: true };
    });
    this.refresh();
  }
  setCommands(commands: () => SelectItem[]): void {
    this.editor.setAutocompleteProvider(new CommandCompletion(commands));
  }
  select(title: string, items: SelectItem[], choose: (item: SelectItem, persist: boolean) => void, allowDefault = false): void {
    this.showDialog(close => new Selector(title, items, (item, persist) => { close(); choose(item, persist); }, close, allowDefault));
  }
  showDialog(create: (close: () => void) => Dialog): () => void {
    if (this.disposed) return () => {};
    this.dialog?.dispose?.();
    const close = () => {
      if (this.dialog !== dialog) return;
      dialog.dispose?.();
      this.dialog = undefined; this.inputRegion.children = [this.editor];
      this.tui.setFocus(this.editor); this.refresh();
    };
    const dialog = create(close);
    this.dialog = dialog;
    this.inputRegion.children = [dialog]; this.tui.setFocus(dialog); this.refresh();
    return close;
  }
  private submit(text: string, mode: "steer" | "followUp"): void {
    if (!text.trim()) return;
    this.editor.addToHistory(text);
    this.editor.setText("");
    this.actions.submit(text, mode);
    this.refresh();
  }
  refresh(): void {
    if (this.disposed) return;
    const pending = this.state.pending ?? [];
    this.resources.setText(this.transcript.expanded ? theme.dim(safeText(resourceText(this.state))) : "");
    const lines = pending.slice(0, 3).map(item => `↳ queued: ${safeText(item.preview).split("\n")[0].slice(0, 120)}`);
    if (pending.length > 3) lines.push(`… ${pending.length - 3} more queued follow-ups`);
    this.queue.setText(theme.dim(lines.join("\n")));
    const approval = this.state.approvals?.length ? `Approval required (${this.state.approvals.length}) · /approvals · Esc interrupts\n` : "";
    this.status.setText(theme.dim(safeText(approval + (this.state.notice || (this.state.status === "running" ? "Working…" : "")))));
    this.footer.setText(theme.dim(safeText(`${this.state.workspace}  ·  ${this.state.model}  ·  ${this.state.status}`)));
    this.tui.requestRender();
  }
  dispose(): void { this.disposed = true; this.dialog?.dispose?.(); this.removeListener(); }
}
