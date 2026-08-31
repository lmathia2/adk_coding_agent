import { Container, Editor, Text, matchesKey, type TUI } from "@earendil-works/pi-tui";
import type { SessionActions, SessionView } from "./contracts.js";
import { Transcript } from "./transcript.js";
import { editorTheme, theme } from "./theme.js";

/** Presentation depends on a tiny session port, not Pi's AgentSession or ADK. */
export class TerminalView {
  readonly editor: Editor;
  readonly transcript: Transcript;
  readonly root = new Container();
  private readonly status = new Text("", 1, 0);
  private readonly footer = new Text("", 1, 0);
  private readonly removeListener: () => void;
  constructor(readonly tui: TUI, readonly state: SessionView, private readonly actions: SessionActions) {
    this.editor = new Editor(tui, editorTheme, { paddingX: 1 });
    this.transcript = new Transcript(state);
    this.root.addChild(new Text(theme.accent("adk-agent") + theme.dim(" · Pi terminal / ADK harness"), 1, 0));
    this.root.addChild(new Text(theme.dim("esc interrupt · ctrl+c clear · ctrl+d exit · / commands · ctrl+o tools"), 1, 1));
    this.root.addChild(this.transcript);
    this.root.addChild(this.status);
    this.root.addChild(this.editor);
    this.root.addChild(this.footer);
    tui.addChild(this.root);
    tui.setFocus(this.editor);
    this.editor.onSubmit = (text) => this.submit(text, "steer");
    this.removeListener = tui.addInputListener((data) => {
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
  private submit(text: string, mode: "steer" | "followUp"): void {
    if (!text.trim()) return;
    this.editor.addToHistory(text);
    this.editor.setText("");
    this.actions.submit(text, mode);
    this.refresh();
  }
  refresh(): void {
    this.status.setText(theme.dim(this.state.notice || (this.state.status === "running" ? "Working…" : "")));
    this.footer.setText(theme.dim(`${this.state.workspace}  ·  ${this.state.model}  ·  ${this.state.status}`));
    this.tui.requestRender();
  }
  dispose(): void { this.removeListener(); }
}
