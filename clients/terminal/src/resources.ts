import type { SessionView } from "./contracts.js";
import { Text, matchesKey } from "@earendil-works/pi-tui";
import { Selector } from "./selector.js";
import { safeText } from "./transcript.js";
import type { Dialog } from "./view.js";

/** Only server-provided metadata. No client filesystem discovery or resource bodies. */
export function resourceText(state: SessionView, detailed = false): string {
  const resources = state.resources;
  if (!resources) return "Resource inventory is not available from the server.";
  const lines: string[] = [];
  if (detailed) lines.push(`Workspace: ${state.workspace}`, `State: ${resources.stateRoot}`,
    `Configuration root: ${resources.configurationRoot}`, `Run history: ${resources.runDatabase}`,
    `Project trust: ${resources.projectTrusted ? "enabled" : "disabled"}`,
    "Available for the next turn; skill bodies load progressively, not at terminal startup.");
  for (const [kind, label] of [["instruction", "Context"], ["skill", "Skills"], ["prompt", "Prompts"], ["tool", "Tools"], ...(detailed ? [["skill_root", "Skill directories"]] : [])]) {
    const items = resources.items.filter(item => item.kind === kind);
    if (items.length) lines.push(`[${label}]\n${items.map(item => `  ${detailed && item.path ? item.path : item.name}${item.status !== "available" ? ` (${item.status})` : ""}`).join(detailed ? "\n" : ", ")}`);
  }
  if (state.selectedSkills?.length) lines.push(`[Selected for this run]\n  ${state.selectedSkills.join(", ")}`);
  if (resources.truncated) lines.push("Resource inventory truncated (128 entries maximum).");
  lines.push(...resources.warnings);
  return lines.join("\n\n");
}

export function skillPrompt(command: string, state: SessionView): string {
  const match = /^\/skill:([a-z0-9-]+)(?:\s([\s\S]*))?$/.exec(command);
  if (!match || !state.resources?.items.some(item => item.kind === "skill" && item.status === "available" && item.name === match[1])) throw new Error("Skill is not available on the server; inspect /skills");
  return `$${match[1]}${match[2] === undefined ? "" : " " + match[2]}`;
}

/** Async discovery remains inside its dialog; a late reply cannot reopen it. */
export class ResourceDialog implements Dialog {
  private hasFocus = false;
  get focused(): boolean { return this.hasFocus; }
  set focused(value: boolean) { this.hasFocus = value; if (this.selector) this.selector.focused = value; }
  private stopped = false;
  private selector?: Selector;
  private guidance = "";
  private text = "Loading server resources… Esc cancels";
  constructor(port: {refreshResources(): Promise<void>}, state: SessionView, skills: boolean,
    private readonly refresh: () => void, private readonly close: () => void,
    choose: (name: string) => void) {
    void port.refreshResources().then(() => {
      if (this.stopped) return;
      this.text = resourceText(state, true);
      if (skills) {
        this.guidance = [...(state.resources?.warnings ?? []),
          ...(state.resources?.truncated ? ["Inventory truncated; inspect /resources."] : [])].join("\n");
        this.selector = new Selector("Available skills · Enter fills the editor", (state.resources?.items ?? [])
          .filter(item => item.kind === "skill" && item.status === "available")
          .map(item => ({value: item.name, label: item.name, description: item.description})),
          item => { close(); choose(item.value); }, close);
        this.selector.focused = this.hasFocus;
      }
      refresh();
    }).catch(error => { if (!this.stopped) { this.text = error.message; refresh(); } });
  }
  handleInput(data: string): void {
    if (this.selector) this.selector.handleInput(data);
    else if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c") || matchesKey(data, "enter")) this.close();
  }
  render(width: number): string[] {
    if (!this.selector) return new Text(safeText(this.text) + "\n\nEnter or Esc returns", 1, 1).render(width);
    return [...this.selector.render(width), ...(this.guidance ? new Text(safeText(this.guidance), 1, 1).render(width) : [])];
  }
  invalidate(): void { this.selector?.invalidate(); }
  dispose(): void { this.stopped = true; }
}
