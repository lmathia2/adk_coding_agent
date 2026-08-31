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
export interface ResourceView {
  stateRoot: string;
  configurationRoot: string;
  runDatabase: string;
  projectTrusted: boolean;
  items: {kind: string; name: string; path?: string; description: string; status: string}[];
  warnings: string[];
  truncated: boolean;
}
export interface SessionView {
  entries: TranscriptEntry[];
  status: string;
  workspace: string;
  model: string;
  notice: string;
  resources?: ResourceView;
  selectedSkills?: string[];
  pending?: {item_id: string; preview: string}[];
}
export interface SessionActions {
  submit(text: string, mode: "steer" | "followUp"): void;
  cancel(): void;
  quit(): void;
}
