import { Input, SelectList, Text, fuzzyFilter, matchesKey, truncateToWidth, type Component, type Focusable, type SelectItem } from "@earendil-works/pi-tui";
import { editorTheme, theme } from "./theme.js";
import { safeText } from "./transcript.js";

/** Presentation only. The caller supplies choices and performs the selected action. */
export class Selector implements Component, Focusable {
  readonly input = new Input();
  private list: SelectList;
  private items: SelectItem[];
  get focused(): boolean { return this.input.focused; }
  set focused(value: boolean) { this.input.focused = value; }
  constructor(private readonly title: string, items: SelectItem[],
    private readonly choose: (item: SelectItem, persist: boolean) => void,
    private readonly cancel: () => void, private readonly allowDefault = false) {
    this.items = items.map(item => ({...item, label: safeText(item.label), description: safeText(item.description ?? "")}));
    this.list = this.filteredList();
  }
  private filteredList(): SelectList {
    return new SelectList(fuzzyFilter(this.items, this.input.getValue(), item => `${item.label} ${item.description ?? ""}`), 8, editorTheme.selectList);
  }
  updateItems(items: SelectItem[]): void {
    const selected = this.list.getSelectedItem()?.value;
    this.items = items.map(item => ({...item, label: safeText(item.label), description: safeText(item.description ?? "")}));
    this.list = this.filteredList();
    const filtered = fuzzyFilter(this.items, this.input.getValue(), item => `${item.label} ${item.description ?? ""}`);
    const index = filtered.findIndex(item => item.value === selected);
    if (index >= 0) this.list.setSelectedIndex(index);
  }
  handleInput(data: string): void {
    if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c")) return this.cancel();
    if (matchesKey(data, "enter") || (this.allowDefault && matchesKey(data, "ctrl+s"))) {
      const item = this.list.getSelectedItem();
      if (item) this.choose(item, matchesKey(data, "ctrl+s"));
    } else if (matchesKey(data, "up") || matchesKey(data, "down")) this.list.handleInput(data);
    else {
      const before = this.input.getValue(); this.input.handleInput(data);
      if (before !== this.input.getValue()) this.list = this.filteredList();
    }
  }
  invalidate(): void { this.list.invalidate(); this.input.invalidate(); }
  render(width: number): string[] {
    const border = theme.border("─".repeat(Math.max(0, width)));
    const hint = this.allowDefault ? "Enter select · Ctrl+S default · Esc cancel" : "Enter select · Esc cancel";
    return [border, ...new Text(theme.accent(safeText(this.title)), 1, 1).render(width),
      ...this.input.render(width), "", ...(this.list.getSelectedItem() ? this.list.render(width) : new Text(theme.dim("No matches"), 1, 0).render(width)), "",
      ...new Text(theme.dim(hint), 1, 0).render(width), border]
      .map(line => truncateToWidth(line, width));
  }
}
