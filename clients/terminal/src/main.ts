import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { ProcessTerminal, TuiMainScreen } from "@earendil-works/pi-tui";
import { RemoteSession } from "./remote-session.js";
import { TerminalView } from "./view.js";

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
    stopped = true; unsubscribe(); session.close(); view.dispose(); tui.stop();
  };
  const view = new TerminalView(tui, session.state.view, {
    submit(text, mode) {
      try {
        if (text === "/quit") return quit();
        if (text === "/new") return session.newConversation();
        if (text === "/cancel") return session.cancel();
        if (text.startsWith("/")) throw new Error("Available in this prototype: /new /cancel /quit");
        session.submit(text, mode);
      } catch (error) {
        session.state.view.notice = error instanceof Error ? error.message : "Request failed";
        view.editor.setText(text);
      }
    },
    cancel() { try { session.cancel(); } catch (error) { session.state.view.notice = String(error); } },
    quit,
  });
  const unsubscribe = session.subscribe(() => view.refresh());
  process.on("SIGTERM", quit);
  process.on("SIGINT", quit);
  tui.start(); session.connect();
} catch {
  process.stderr.write("Unable to connect the terminal. Start the harness server and check --state-root / ADK_CODING_AGENT_TOKEN.\n");
  process.exitCode = 1;
}
