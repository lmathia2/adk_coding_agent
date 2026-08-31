import { Markdown, Text, stripTerminalSequences, type Component } from "@earendil-works/pi-tui";
import type { SessionView, ToolEntry } from "./contracts.js";
import { markdownTheme, theme } from "./theme.js";

export const safeText = (text: string): string => stripTerminalSequences(text)
  .replace(/[\x00-\x08\x0b-\x1f\x7f]/g, "");

function object(text: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value)
      ? value as Record<string, unknown> : {};
  } catch { return {}; }
}

/** Pi-style presentation: successful reads collapse completely, bash previews five lines. */
export function renderTool(entry: ToolEntry, width: number, expanded: boolean): string[] {
  const args = object(entry.arguments);
  const result = object(entry.result ?? "");
  const detail = args.path ?? args.command ?? "";
  const error = ["error", "blocked", "timeout"].includes(String(result.status)) || result.isError === true;
  const label = safeText(`${entry.name} ${detail}`).trim();
  const heading = error ? theme.error(label) : theme.accent(label);
  const lines = new Text(heading, 1, 0, theme.tool).render(width);
  if (entry.result === undefined) {
    lines.push(...new Text(theme.dim(entry.done ? "Run ended without a recorded tool result" : "running…"), 1, 0, theme.tool).render(width));
    return lines;
  }
  const text = typeof result.model_text === "string" ? result.model_text : entry.result;
  if (!expanded && entry.name === "read" && !error) return lines;
  const body = safeText(text).split("\n");
  const limit = expanded ? body.length : error ? 10 : entry.name === "bash" ? 5 : 3;
  lines.push(...new Text(body.slice(0, limit).join("\n"), 1, 0, theme.tool).render(width));
  if (body.length > limit) {
    lines.push(...new Text(theme.dim(`… ${body.length - limit} more lines (ctrl+o to expand)`), 1, 0, theme.tool).render(width));
  }
  return lines;
}

export class Transcript implements Component {
  expanded = false;
  private cache = new Map<string, { text: string; component: Markdown | Text }>();
  constructor(private readonly state: SessionView) {}
  invalidate(): void { this.cache.clear(); }
  render(width: number): string[] {
    const lines: string[] = [];
    const active = new Set(this.state.entries.map((entry) => entry.id));
    for (const id of this.cache.keys()) if (!active.has(id)) this.cache.delete(id);
    for (const entry of this.state.entries) {
      if (entry.kind === "tool") {
        lines.push(...renderTool(entry, width, this.expanded), "");
        continue;
      }
      const text = safeText(entry.text);
      let cached = this.cache.get(entry.id);
      if (!cached || cached.text !== text) {
        cached = { text, component: entry.kind === "assistant"
          ? new Markdown(text, 1, 0, markdownTheme)
          : new Text(entry.kind === "error" ? theme.error(text) : text, 1, 0,
            entry.kind === "user" ? theme.user : undefined) };
        this.cache.set(entry.id, cached);
      }
      lines.push(...cached.component.render(width), "");
    }
    return lines;
  }
}
