/** Rendering-only acceptance fixture; never invokes a model or executes a tool. */
import { ProcessTerminal, TuiMainScreen } from "@earendil-works/pi-tui";
import { TerminalView } from "./view.js";
import type { SessionView } from "./contracts.js";

const state: SessionView = { entries: [], workspace: "synthetic workspace", model: "fixture (no model calls)", status: "ready", notice: "" };
const tui = new TuiMainScreen(new ProcessTerminal());
const view = new TerminalView(tui, state, {
  submit(text) {
    if (text.trim() === "/quit") return quit();
    if (text.trim() === "/help") {
      view.select("Commands", commands, item => view.editor.setText(item.value)); return;
    }
    state.entries.push({ kind: "user", id: crypto.randomUUID(), text });
    if (text.trim() === "/tools") state.entries.push({
      kind: "tool", id: crypto.randomUUID(), name: "read", arguments: '{"path":"README.md"}',
      result: JSON.stringify({ status: "ok", model_text: Array.from({length: 30}, (_, i) => `${i + 1} | example line`).join("\n") }), done: true,
    });
    else state.entries.push({ kind: "assistant", id: crypto.randomUUID(), text: "Hello! This is a **rendering fixture**, not a model response.\n\n```python\nprint('Hello, world!')\n```\n\nUse `/tools` to inspect a collapsed read." });
  },
  cancel() { state.notice = "No active run"; }, quit,
});
function quit(): void { view.dispose(); tui.stop(); }
const commands = [
  {value: "/help", label: "/help", description: "Search commands"},
  {value: "/tools", label: "/tools", description: "Render a compact tool card"},
  {value: "/quit", label: "/quit", description: "Exit the fixture"},
];
view.setCommands(() => commands);
tui.start();
