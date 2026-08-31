/** UI data only: no ADK objects, provider SDKs, credentials, or workflow ledgers. */
export interface ToolEntry {
  kind: "tool";
  id: string;
  name: string;
  arguments: string;
  result?: string;
  done: boolean;
}
export interface TextEntry {
  kind: "user" | "assistant" | "error" | "notice";
  id: string;
  text: string;
}
export type TranscriptEntry = ToolEntry | TextEntry;
export interface SessionView {
  entries: TranscriptEntry[];
  status: string;
  workspace: string;
  model: string;
  notice: string;
}
export interface SessionActions {
  submit(text: string, mode: "steer" | "followUp"): void;
  cancel(): void;
  quit(): void;
}
