/** Rendering-only acceptance fixture; never invokes a model or executes a tool. */
import { ProcessTerminal, TuiMainScreen } from "@earendil-works/pi-tui";
import { TerminalView } from "./view.js";
import type { SessionView } from "./contracts.js";
import { providerCommand, type ProviderPort } from "./provider-ui.js";
import { ModelPicker, type ModelPort } from "./model-picker.js";

const state: SessionView = { entries: [], workspace: "synthetic workspace", model: "fixture (no model calls)", status: "ready", notice: "" };
const tui = new TuiMainScreen(new ProcessTerminal());
const view = new TerminalView(tui, state, {
  submit(text) {
    if (text.trim() === "/quit") return quit();
    if (text.trim() === "/help") {
      view.select("Commands", commands, item => view.editor.setText(item.value)); return;
    }
    if (text.trim() === "/login") {
      void providerCommand("/login", fixtureProvider, view, notice => { state.notice = notice; view.refresh(); }); return;
    }
    if (text.trim() === "/model") {
      view.showDialog(close => new ModelPicker(fixtureModels, () => view.refresh(), close, notice => { state.notice = notice; view.refresh(); })); return;
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
  {value: "/login", label: "/login", description: "Synthetic login dialog (no real authentication)"},
  {value: "/model", label: "/model", description: "Synthetic model picker (no model calls)"},
  {value: "/quit", label: "/quit", description: "Exit the fixture"},
];
const fixtureProvider: ProviderPort = {async providerRequest(operation, parameters) {
  if (operation === "status" && !parameters?.login_id) return {providers: [{provider: "fixture", display_name: "Fixture provider (no network)", supports_login: true, authenticated: false, credential_path: "/synthetic/server/auth.json"}]};
  return {login: {login_id: "synthetic", status: operation === "cancel_login" ? "cancelled" : "waiting",
    verification_url: "https://example.invalid/rendering-only", user_code: "TEST-CODE"}};
}};
const fixtureModels: ModelPort = {async modelRequest(operation, parameters) {
  const choice = (name: string) => ({provider: "fixture", name});
  if (operation === "catalog") return {selected: choice("alpha"), default: choice("alpha"),
    models: ["alpha", "beta", "gamma"].map(name => ({choice: choice(name), display_name: "Rendering fixture"}))};
  return {selected: choice(String(parameters?.name)), default_path: "/synthetic/server/model-selection.json"};
}};
view.setCommands(() => commands);
tui.start();
