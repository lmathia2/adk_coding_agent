import { Text, matchesKey } from "@earendil-works/pi-tui";
import type { ApprovalView } from "./contracts.js";
import type { WireObject } from "./protocol.js";
import { Selector } from "./selector.js";
import { safeText } from "./transcript.js";
import type { Dialog, TerminalView } from "./view.js";

export interface ApprovalPort {
  approvalRequest(operation: "list" | "decide", parameters: WireObject): Promise<WireObject>;
}

/** Human decisions only; the server owns waiting, policy and command execution. */
export class ApprovalDialog implements Dialog {
  private selector: Selector;
  private stopped = false;
  private sending = false;
  private status = "Esc defers · cancel the run to interrupt · secrets may be redacted";
  private readonly submit: (decision: "approved" | "denied") => void;
  get focused(): boolean { return this.selector.focused; }
  set focused(value: boolean) { this.selector.focused = value; }
  constructor(port: ApprovalPort, readonly runId: string, readonly request: ApprovalView,
    private readonly refresh: () => void, private readonly close: () => void,
    private readonly notice: (text: string) => void) {
    const reviewable = request.operation.length <= 16_000;
    this.submit = decision => {
      if (this.sending || (decision === "approved" && !reviewable)) return;
      this.sending = true; this.status = "Submitting decision…"; refresh();
      void port.approvalRequest("decide", {run_id: runId, approval_id: request.request_id,
        fingerprint: request.fingerprint, decision}).then(() => {
        this.sending = false;
        notice(`Approval decision recorded: ${decision}. Execution is reported separately.`);
        if (!this.stopped) close();
      }).catch(error => {
        this.status = `Decision not confirmed: ${error.message}. Check /approvals and the run status.`;
        notice(this.status); if (!this.stopped) refresh();
      });
    };
    this.selector = new Selector("Command approval · Deny is the default", [
      {value: "denied", label: "Deny command", description: "Do not execute this command"},
      ...(reviewable ? [{value: "approved", label: "Approve exact command for this task", description: "Does not approve other commands or tasks"}] : []),
    ], item => this.submit(item.value as "approved" | "denied"), close);
  }
  handleInput(data: string): void {
    if (this.sending) {
      if (matchesKey(data, "escape") || matchesKey(data, "ctrl+c")) this.close();
    } else if (data.toLowerCase() === "a" || data.toLowerCase() === "y") this.submit("approved");
    else if (data.toLowerCase() === "d" || data.toLowerCase() === "n") this.submit("denied");
    else this.selector.handleInput(data);
  }
  render(width: number): string[] {
    const operation = this.request.operation.length > 16_000 ? this.request.operation.slice(0, 16_000) + "\n[Too large to approve here. Deny or inspect through the server approval CLI.]" : this.request.operation;
    return [...new Text(safeText(`Command (not executed):\n${operation}\n\nRisk: ${this.request.risk}\n${this.request.reason}\nWait deadline: ${this.request.wait_deadline}\nFingerprint: ${this.request.fingerprint}`), 1, 1).render(width),
      ...this.selector.render(width), ...new Text(safeText(`A/Y approve · D/N deny · ${this.status}`), 1, 1).render(width)];
  }
  invalidate(): void { this.selector.invalidate(); }
  dispose(): void {
    this.stopped = true;
    if (this.sending) this.notice("Decision may already be recorded; inspect /approvals and the run status. Closing the dialog does not revoke approval.");
  }
}

/** Opens once per pending request without stealing another dialog's focus. */
export class ApprovalPresenter {
  private seen = new Set<string>();
  private run = "";
  private current?: {id: string; close: () => void};
  constructor(private readonly view: TerminalView, private readonly port: ApprovalPort,
    private readonly notice: (text: string) => void) {}
  update(run: string, active: boolean, pending: ApprovalView[], explicit = false): void {
    if (this.current && (!active || run !== this.run || !pending.some(item => item.request_id === this.current?.id))) {
      this.current.close(); this.current = undefined;
    }
    if (run !== this.run) { this.seen.clear(); this.run = run; }
    const request = pending.find(item => explicit || !this.seen.has(item.request_id));
    if (active && request && (explicit || !this.view.hasDialog)) {
      this.seen.add(request.request_id);
      const close = this.view.showDialog(done => new ApprovalDialog(this.port, run, request,
        () => this.view.refresh(), () => { this.current = undefined; done(); }, this.notice));
      this.current = {id: request.request_id, close};
    } else if (explicit && !pending.length) this.notice("No commands are currently waiting for approval.");
  }
}
