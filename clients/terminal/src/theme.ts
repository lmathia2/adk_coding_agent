import type { EditorTheme, MarkdownTheme } from "@earendil-works/pi-tui";

const color = (code: string) => (text: string): string => `\x1b[${code}m${text}\x1b[0m`;
export const theme = {
  accent: color("38;5;80"), dim: color("38;5;243"), border: color("38;5;69"),
  error: color("38;5;210"), warning: color("38;5;180"), success: color("38;5;149"),
  user: color("48;5;236"), tool: color("48;5;234"),
};
export const editorTheme: EditorTheme = {
  borderColor: theme.border,
  selectList: {
    selectedPrefix: theme.accent, selectedText: theme.accent,
    description: theme.dim, scrollInfo: theme.dim, noMatch: theme.warning,
  },
};
export const markdownTheme: MarkdownTheme = {
  heading: theme.accent, link: theme.accent, linkUrl: theme.dim,
  code: theme.accent, codeBlock: (text) => text, codeBlockBorder: theme.dim,
  quote: theme.dim, quoteBorder: theme.dim, hr: theme.border, listBullet: theme.accent,
  bold: color("1"), italic: color("3"), strikethrough: color("9"), underline: color("4"),
};
