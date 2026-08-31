import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { ProcessTerminal, TuiMainScreen, type SelectItem } from "@earendil-works/pi-tui";
import { RemoteSession } from "./remote-session.js";
import { TerminalView } from "./view.js";
import { providerCommand } from "./provider-ui.js";

const {values} = parseArgs({options: {
  server: {type: "string", default: "ws://127.0.0.1:8765/v1/agent"},
  "state-root": {type: "string", default: join(homedir(), ".local/state/adk-coding-agent")},
}});
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
    ...(session.state.capabilities.has("cancel") ? [{value: "/cancel", label: "/cancel", description: "Interrupt the active run"}] : []),
    ...(session.state.capabilities.has("sessions") ? [{value: "/queue", label: "/queue", description: "Manage durable follow-ups"}] : []),
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
  const unsubscribe = session.subscribe(() => view.refresh());
  process.on("SIGTERM", quit);
  process.on("SIGINT", quit);
  tui.start(); session.connect();
} catch {
  process.stderr.write("Unable to connect the terminal. Start the harness server and check --state-root / ADK_CODING_AGENT_TOKEN.\n");
  process.exitCode = 1;
}
