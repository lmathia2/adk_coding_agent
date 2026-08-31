import { Text, matchesKey, truncateToWidth, type SelectItem } from "@earendil-works/pi-tui";
import { object, string, type WireObject } from "./protocol.js";
import { safeText } from "./transcript.js";
import { theme } from "./theme.js";
import type { Dialog, TerminalView } from "./view.js";

export interface ProviderPort {
  providerRequest(operation: "status" | "login" | "cancel_login" | "logout", parameters?: WireObject): Promise<WireObject>;
}

/** The server owns OAuth. This component displays safe instructions and controls it. */
export class LoginDialog implements Dialog {
  focused = true;
  private status = "starting";
  private detail = "Requesting a login code from the server…";
  private loginId = "";
  private stopped = false;
  private terminal = false;
  private cancellationSent = false;
  private timer?: NodeJS.Timeout;
  constructor(private readonly port: ProviderPort, private readonly provider: string,
    private readonly refresh: () => void, private readonly close: () => void,
    private readonly notice: (text: string) => void, private readonly pollMs = 700) {
    void this.request(true);
  }
  private async request(start: boolean): Promise<void> {
    try {
      const data = await this.port.providerRequest(start ? "login" : "status", {
        provider: this.provider, ...(start ? {} : {login_id: this.loginId}),
      });
      const login = object(data.login);
      this.loginId = string(login.login_id);
      if (this.stopped) { await this.cancel(); return; }
      this.status = string(login.status);
      this.terminal = !["starting", "waiting"].includes(this.status);
      if (this.status === "waiting") {
        this.detail = `Open this URL in your browser:\n${string(login.verification_url)}\n\nEnter code: ${string(login.user_code)}\n\nWaiting for browser authorization…`;
      } else if (this.status === "authenticated") {
        this.detail = `Signed in. Credentials are stored on the server:\n${typeof data.credential_path === "string" ? data.credential_path : "Use /auth to inspect the credential path."}\n\nNo provider tokens are stored by this terminal.`;
        this.notice("Signed in — provider credentials saved on the server");
      } else if (this.status === "failed") this.detail = typeof login.error === "string" ? login.error : "Login failed. Use /login to retry.";
      else if (this.status === "cancelled") this.detail = "Login cancelled.";
      if (!this.terminal) this.timer = setTimeout(() => void this.request(false), this.pollMs);
    } catch (error) {
      if (this.stopped) return;
      this.status = "unconfirmed";
      this.detail = `${error instanceof Error ? error.message : "Login status unavailable"}\n\nCheck /auth after reconnecting; /login can reattach to a pending login.`;
    }
    if (!this.stopped) this.refresh();
  }
  private async cancel(): Promise<void> {
    if (!this.loginId || this.terminal || this.cancellationSent) return;
    const id = this.loginId; this.cancellationSent = true;
    try {
      const result = await this.port.providerRequest("cancel_login", {provider: this.provider, login_id: id});
      const status = string(object(result.login).status);
      this.notice(status === "cancelled" ? "Login cancelled" : `Login already ${status}; use /auth to inspect credentials`);
    } catch { this.notice("Login cancellation not confirmed. Reconnect and use /auth or /logout."); }
  }
  handleInput(data: string): void {
    if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c") || matchesKey(data, "ctrl+d")
      || (this.terminal && matchesKey(data, "enter"))) this.close();
  }
  invalidate(): void {}
  render(width: number): string[] {
    return [theme.border("─".repeat(Math.max(0, width))),
      ...new Text(theme.accent(`Login · ${safeText(this.provider)} · ${safeText(this.status)}`), 1, 1).render(width),
      ...new Text(safeText(this.detail), 1, 0).render(width), "",
      ...new Text(theme.dim(this.terminal ? "Enter / Esc close" : "Esc cancel login"), 1, 0).render(width),
      theme.border("─".repeat(Math.max(0, width)))].map(line => truncateToWidth(line, width));
  }
  dispose(): void {
    if (this.stopped) return;
    this.stopped = true; clearTimeout(this.timer);
    void this.cancel();
  }
}

export async function providerCommand(command: "/login" | "/auth" | "/logout", port: ProviderPort,
  view: TerminalView, notice: (text: string) => void): Promise<void> {
  const data = await port.providerRequest("status");
  const providers = (Array.isArray(data.providers) ? data.providers.map(object) : [])
    .filter(provider => command !== "/login" || provider.supports_login === true);
  const items: SelectItem[] = providers.map(provider => ({value: string(provider.provider),
    label: `${string(provider.display_name)} · ${provider.authenticated ? "credentials saved" : "not signed in"}`,
    description: `${provider.login ? "Login pending · " : ""}${typeof provider.error === "string" ? provider.error + " · " : ""}${typeof provider.credential_path === "string" ? provider.credential_path : ""}`}));
  const login = (provider: string) => view.showDialog(close => new LoginDialog(port, provider, () => view.refresh(), close, notice));
  view.select(command === "/auth" ? "Authentication · credentials live on the server" : command === "/login" ? "Log in to a provider" : "Log out of a provider", items, item => {
    if (command === "/login") { login(item.value); return; }
    if (command === "/auth") { notice(`${item.label}. ${item.description}. Saved credentials are not a live model-response check.`); return; }
    view.select("Remove saved credentials and cancel pending login? Active model requests may finish.",
      [{value: "keep", label: "Keep credentials"}, {value: "remove", label: "Remove credentials from this server"}], choice => {
        if (choice.value !== "remove") return;
        void port.providerRequest("logout", {provider: item.value})
          .then(() => notice("Signed out — server credentials removed. This does not revoke the remote account session."))
          .catch(error => notice(error.message));
      });
  });
}
