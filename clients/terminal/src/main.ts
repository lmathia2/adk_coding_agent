import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { ProcessTerminal, TuiMainScreen, type SelectItem } from "@earendil-works/pi-tui";
import { RemoteSession } from "./remote-session.js";
import { TerminalView } from "./view.js";
import { providerCommand } from "./provider-ui.js";
import { ModelPicker } from "./model-picker.js";
import { HistoryDialog, SessionInfo, SessionPicker } from "./session-ui.js";
import { ResourceDialog, skillPrompt } from "./resources.js";
import { ApprovalPresenter } from "./approvals.js";

const {values} = parseArgs({options: {
  server: {type: "string", default: "ws://127.0.0.1:8765/v1/agent"},
  "state-root": {type: "string", default: join(homedir(), ".local/state/adk-coding-agent")},
  help: {type: "boolean", short: "h", default: false},
}});
if (values.help) {
  process.stdout.write(`Pi-style terminal for the ADK coding harness.

Usage:
  adk-agent-tui [--server URL] [--state-root DIR]

Options:
  --server URL      Harness WebSocket (default: ws://127.0.0.1:8765/v1/agent)
  --state-root DIR  Read the local server token from DIR/server/auth-token
  -h, --help        Show this help

Inside the terminal, use /help for interactive commands and keyboard shortcuts.
`);
  process.exit(0);
}
try {
  const token = process.env.ADK_CODING_AGENT_TOKEN || readFileSync(join(values["state-root"], "server/auth-token"), "utf8").trim();
  const session = new RemoteSession({url: values.server, token});
  const tui = new TuiMainScreen(new ProcessTerminal());
  let stopped = false;
  const quit = () => {
    if (stopped) return;
    stopped = true; unsubscribe(); view.dispose(); session.close(); tui.stop();
  };
  const commands = (): SelectItem[] => [
    {value: "/help", label: "/help", description: "Commands and keyboard hints"},
    {value: "/new", label: "/new", description: "Start a new conversation"},
    ...(session.state.capabilities.has("approvals") ? [{value: "/approvals", label: "/approvals", description: "Review exact commands waiting for authorization"}] : []),
    ...(session.state.capabilities.has("cancel") ? [{value: "/cancel", label: "/cancel", description: "Interrupt the active run"}] : []),
    ...(session.state.capabilities.has("sessions") ? [
      {value: "/queue", label: "/queue", description: "Manage durable follow-ups"},
      {value: "/session", label: "/session", description: "Show conversation and run identity"},
    ] : []),
    ...(session.state.capabilities.has("session_history") ? [
      {value: "/resume", label: "/resume", description: "Search and reopen a server conversation"},
      {value: "/history", label: "/history", description: "Browse read-only transcript pages"},
    ] : []),
    ...(session.state.capabilities.has("model_selection") ? [{value: "/model", label: "/model", description: "Select the next turn's model; Ctrl+S saves a default"}] : []),
    ...(session.state.capabilities.has("resources") ? [
      {value: "/resources", label: "/resources", description: "Inspect server resources, trust and state paths"},
      {value: "/skills", label: "/skills", description: "Select an available directory skill"},
      ...(session.state.view.resources?.items ?? []).filter(item => item.kind === "skill" && item.status === "available")
        .map(item => ({value: `/skill:${item.name}`, label: `/skill:${item.name}`, description: item.description})),
    ] : []),
    ...(session.state.capabilities.has("provider_controls") ? [
      {value: "/login", label: "/login", description: "Sign in to a provider on the server"},
      {value: "/auth", label: "/auth", description: "Inspect server authentication and credential paths"},
      {value: "/logout", label: "/logout", description: "Remove provider credentials from the server"},
    ] : []),
    {value: "/quit", label: "/quit", description: "Disconnect the terminal"},
  ];
  const background = (action: Promise<unknown>) => {
    void action.catch(error => { if (!stopped) { session.state.view.notice = error.message; view.refresh(); } });
  };
  const showQueue = async () => {
    if (session.state.runId) await session.queueStatus();
    const pending = session.state.view.pending ?? [];
    const items: SelectItem[] = pending.map(item => ({value: item.item_id, label: `Remove: ${item.preview.split("\n")[0]}`, description: "Remove this pending follow-up"}));
    if (pending.length) {
      if (!session.state.active) items.unshift({value: "continue", label: "Continue queued work", description: "Run the next pending turn"});
      items.push({value: "clear", label: "Clear all pending follow-ups"});
    }
    view.select(`Queued follow-ups (${pending.length})`, items, item => {
      if (item.value === "continue") background(session.continueQueue());
      else if (item.value === "clear") background(session.clearQueue());
      else background(session.request("remove_follow_up", {thread_id: session.state.threadId, item_id: item.value}).then(() => session.queueStatus()));
    });
  };
  const view = new TerminalView(tui, session.state.view, {
    submit(text, mode) {
      try {
        const command = text.trim();
        if (command === "/help") {
          view.select("Commands · Enter fills the editor", commands(), item => view.editor.setText(item.value)); return;
        }
        if (command === "/quit") return quit();
        if (command === "/new") return session.newConversation();
        if (command === "/cancel") return session.cancel();
        if (command === "/approvals") {
          background(session.refreshApprovals().then(() => showApproval(true))); return;
        }
        if (command === "/resources" || command === "/skills") {
          view.showDialog(close => new ResourceDialog(session, session.state.view, command === "/skills",
            () => view.refresh(), close, name => view.editor.setText(`$${name} `))); return;
        }
        if (command.startsWith("/skill:")) {
          session.submit(skillPrompt(command, session.state.view), mode); return;
        }
        if (command === "/resume") {
          if (!session.state.capabilities.has("session_history")) throw new Error("Server does not support transcript history");
          if (session.state.active) throw new Error("Finish or cancel active work before switching conversations");
          view.showDialog(close => new SessionPicker(session, () => view.refresh(), close)); return;
        }
        if (command === "/history") {
          if (!session.state.capabilities.has("session_history")) throw new Error("Server does not support transcript history");
          if (!session.state.runId) throw new Error("No saved turns yet; send a request or use /resume");
          view.showDialog(close => new HistoryDialog(session, session.state.threadId, () => view.refresh(), close)); return;
        }
        if (command === "/session") {
          view.showDialog(close => new SessionInfo(`Harness: ${session.state.view.harness || "unknown"}\nConversation: ${session.state.threadId}\nRun: ${session.state.runId || "none yet"}\nModel: ${session.state.view.model}\nStatus: ${session.state.view.status}\nQueued follow-ups: ${session.state.view.pending?.length ?? 0}\nHistory and queues are saved on the server. /resume reopens history; /queue continue starts pending work.`, close)); return;
        }
        if (command === "/model") {
          view.showDialog(close => new ModelPicker(session, () => view.refresh(), close, text => {
            if (!stopped) { session.state.view.notice = text; view.refresh(); }
          })); return;
        }
        if (command === "/login" || command === "/auth" || command === "/logout") {
          background(providerCommand(command, session, view, text => {
            if (!stopped) { session.state.view.notice = text; view.refresh(); }
          })); return;
        }
        const queueAction = command === "/queue" ? showQueue
          : command === "/queue continue" ? () => session.continueQueue()
          : command === "/queue clear" ? () => session.clearQueue() : undefined;
        if (queueAction) {
          background(queueAction()); return;
        }
        if (command.startsWith("/")) throw new Error("Unknown command. Use /help to see supported commands.");
        session.submit(text, mode);
      } catch (error) {
        session.state.view.notice = error instanceof Error ? error.message : "Request failed";
        view.editor.setText(text);
      }
    },
    cancel() { try { session.cancel(); } catch (error) { session.state.view.notice = String(error); } },
    quit,
  });
  view.setCommands(commands);
  const approvals = new ApprovalPresenter(view, session, text => {
    if (!stopped) { session.state.view.notice = text; view.refresh(); }
  });
  const showApproval = (explicit = false) => {
    if (!stopped) approvals.update(session.state.runId, session.state.active, session.state.view.approvals ?? [], explicit);
  };
  const unsubscribe = session.subscribe(() => { view.refresh(); showApproval(); });
  process.on("SIGTERM", quit);
  process.on("SIGINT", quit);
  tui.start(); session.connect();
} catch {
  process.stderr.write("Unable to connect the terminal. Start the harness server and check --state-root / ADK_CODING_AGENT_TOKEN.\n");
  process.exitCode = 1;
}
