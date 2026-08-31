import { fuzzyFilter, type AutocompleteItem, type AutocompleteProvider } from "@earendil-works/pi-tui";

/** Slash commands only: never scans the client's filesystem for server resources. */
export class CommandCompletion implements AutocompleteProvider {
  readonly triggerCharacters = ["/"];
  constructor(private readonly commands: () => AutocompleteItem[]) {}
  async getSuggestions(lines: string[], row: number, col: number) {
    if (row !== 0 || lines.length !== 1) return null;
    const prefix = lines[0].slice(0, col);
    if (!prefix.startsWith("/")) return null;
    const items = fuzzyFilter(this.commands(), prefix.slice(1), item => item.value.slice(1));
    return items.length ? {items, prefix} : null;
  }
  applyCompletion(lines: string[], row: number, col: number, item: AutocompleteItem, prefix: string) {
    const result = [...lines], value = item.value + " ";
    const start = col - prefix.length;
    result[row] = result[row].slice(0, start) + value + result[row].slice(col);
    return {lines: result, cursorLine: row, cursorCol: start + value.length};
  }
}
