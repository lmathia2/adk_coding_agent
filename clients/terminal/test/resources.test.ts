import assert from "node:assert/strict";
import { test } from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { stripTerminalSequences, visibleWidth } from "@earendil-works/pi-tui";
import { resourceText, ResourceDialog, skillPrompt } from "../src/resources.js";
import { SessionState } from "../src/session.js";

function state(): SessionState {
  const state = new SessionState();
  state.resources({workspace: "/server/project", state_root: "/server/state", configuration_root: "/server/config", run_database: "/server/state/runs.db", project_trusted: true, warnings: [], items: [
    {kind: "skill", name: "python-checks", path: "/server/skills/python-checks/SKILL.md", status: "available", description: "Check Python changes"},
    {kind: "skill", name: "disabled", status: "disabled", description: "Not enabled"},
    {kind: "instruction", name: "AGENTS.md", path: "/server/project/AGENTS.md", status: "available", description: ""},
  ]});
  return state;
}

test("inventory reports server paths and separates available skills from observed selection", () => {
  const session = state(); session.runId = "run";
  assert.equal(session.view.workspace, "/server/project");
  const text = resourceText(session.view, true);
  assert.match(text, /State: \/server\/state/);
  assert.match(text, /Available for the next turn/);
  assert.doesNotMatch(text, /Selected for this run/);
  session.envelope({run_id: "run", sequence: 1, event: {type: "STATE_DELTA", delta: [{path: "/selected_skill_names", value: ["python-checks"]}, {path: "/skill_context_text", value: "PRIVATE BODY"}]}});
  assert.match(resourceText(session.view), /Selected for this run/);
  assert.equal(session.view.entries.length, 0);
  assert.doesNotMatch(JSON.stringify(session.view), /PRIVATE BODY/);
  session.begin(); assert.deepEqual(session.view.selectedSkills, []);
});

test("skill command expansion preserves multiline specifications and rejects unavailable names", () => {
  const view = state().view;
  assert.equal(skillPrompt("/skill:python-checks Implement:\n    def hello():\n        pass", view), "$python-checks Implement:\n    def hello():\n        pass");
  for (const command of ["/skill:disabled", "/skill:unknown", "/skill:python-checks/injected"]) assert.throws(() => skillPrompt(command, view));
});

test("resource dialogs preserve width, choose enabled skills, and ignore replies after dismissal", async () => {
  const session = state(); let selected = "", closed = false;
  const dialog = new ResourceDialog({async refreshResources() {}}, session.view, true, () => {},
    () => { closed = true; dialog.dispose(); }, name => selected = name);
  await delay(5);
  for (const width of [20, 40, 80, 120]) assert.ok(dialog.render(width).every(line => visibleWidth(line) <= width));
  assert.doesNotMatch(stripTerminalSequences(dialog.render(80).join("\n")), /disabled/);
  dialog.handleInput("\r"); assert.equal(selected, "python-checks"); assert.equal(closed, true);
  let finish!: () => void;
  const pending = new ResourceDialog({refreshResources() { return new Promise(resolve => finish = resolve); }}, session.view, false,
    () => assert.fail("closed dialog redrew"), () => pending.dispose(), () => assert.fail("unexpected choice"));
  pending.handleInput("\x1b"); finish(); await delay(5);
});

test("empty skill picker explains trust and malformed inventory cannot partly change the workspace", async () => {
  const session = state();
  assert.throws(() => session.resources({workspace: "/incorrect", items: [], warnings: [42]}));
  assert.equal(session.view.workspace, "/server/project");
  session.view.resources!.items = [];
  session.view.resources!.warnings = ["Project skills require --trust-project."];
  const dialog = new ResourceDialog({async refreshResources() {}}, session.view, true, () => {}, () => {}, () => {});
  dialog.focused = true;
  await delay(5);
  assert.match(stripTerminalSequences(dialog.render(80).join("\n")), /--trust-project/);
  for (const width of [20, 40, 80]) assert.ok(dialog.render(width).every(line => visibleWidth(line) <= width));
  dialog.dispose();
});
