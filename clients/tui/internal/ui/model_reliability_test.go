package ui

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/session"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
)

func TestPiStyleAuthCommandsUseSharedStateWithoutShellExpansion(t *testing.T) {
	t.Parallel()
	temporary := t.TempDir()
	cli := filepath.Join(temporary, "fake-agent-cli")
	if err := os.WriteFile(cli, []byte("#!/bin/sh\nprintf 'arg=%s\\n' \"$@\"\n"), 0o700); err != nil {
		t.Fatal(err)
	}
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{
		History: 10, ContentBytes: 4096, AckEvery: 5,
		StateRoot: filepath.Join(temporary, "state with spaces"), AgentCLI: cli,
	})

	next, command := model.command("/auth")
	model = next.(Model)
	if command == nil {
		t.Fatal("/auth returned no command")
	}
	message := command()
	commandResult, ok := message.(localCommandMsg)
	if !ok {
		t.Fatalf("/auth message = %T", message)
	}
	if commandResult.err != nil {
		t.Fatal(commandResult.err)
	}
	if !strings.Contains(commandResult.output, "arg=codex") ||
		!strings.Contains(commandResult.output, "arg="+model.config.StateRoot) ||
		!strings.Contains(commandResult.output, "arg=status") {
		t.Fatalf("unexpected CLI arguments:\n%s", commandResult.output)
	}

	next, command = model.command("/login")
	model = next.(Model)
	if command == nil || model.warning != "" {
		t.Fatalf("/login command=%v warning=%q", command != nil, model.warning)
	}
}

func TestPiStyleCommandsExplainMissingSharedState(t *testing.T) {
	t.Parallel()
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{History: 10, ContentBytes: 1024, AckEvery: 5})
	model.config.StateRoot = ""

	next, command := model.command("/login")
	model = next.(Model)
	if command != nil || !strings.Contains(model.warning, "state root") {
		t.Fatalf("command=%v warning=%q", command != nil, model.warning)
	}
}

func TestPendingControlLivesUntilMatchingConfirmation(t *testing.T) {
	t.Parallel()
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{History: 10, ContentBytes: 1024, AckEvery: 5})
	model.session.RunID = "run-1"
	model.session.Status = session.StatusRunning

	next, _ := model.queueControl("command-1", protocol.NewSteerTask("run-1", "continue", "command-1", 0))
	model = next.(Model)
	if len(model.pendingControls) != 1 {
		t.Fatalf("pending controls = %d", len(model.pendingControls))
	}
	model.handleProtocol(protocol.ServerMessage{ControlResult: &protocol.ControlResult{
		Type: "control.result", ProtocolVersion: 1, Operation: "steer", RunID: "run-1",
		Accepted: true, CommandID: "another-command",
	}})
	if len(model.pendingControls) != 1 {
		t.Fatal("unrelated confirmation removed pending control")
	}
	model.handleProtocol(protocol.ServerMessage{ControlResult: &protocol.ControlResult{
		Type: "control.result", ProtocolVersion: 1, Operation: "steer", RunID: "run-1",
		Accepted: true, CommandID: "command-1",
	}})
	if len(model.pendingControls) != 0 {
		t.Fatal("matching confirmation did not remove pending control")
	}
}

func TestHeartbeatTimeoutForcesReconnectState(t *testing.T) {
	t.Parallel()
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{Heartbeat: time.Second, HeartbeatTimeout: 2 * time.Second})
	model.connected, model.negotiated = true, true

	next, _ := model.Update(heartbeatMsg(time.Now()))
	model = next.(Model)
	if model.outstandingPing == "" {
		t.Fatal("heartbeat did not record outstanding nonce")
	}
	next, _ = model.Update(heartbeatTimeoutMsg(model.outstandingPing))
	model = next.(Model)
	if model.connected || model.negotiated || model.outstandingPing != "" {
		t.Fatalf("timeout state = connected:%v negotiated:%v nonce:%q", model.connected, model.negotiated, model.outstandingPing)
	}
}

func TestViewAlwaysShowsCodingModelLifecycleOnDedicatedLine(t *testing.T) {
	t.Parallel()
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{History: 10, ContentBytes: 1024, AckEvery: 5})
	model.width, model.height = 140, 24
	if view := model.View(); !strings.Contains(view, "\ncoding-model  waiting for server\n") {
		t.Fatalf("idle model status missing:\n%s", view)
	}

	hello := protocol.ServerHello{
		Harness: protocol.HarnessDescriptor{Implementation: "fixture", DisplayName: "Fixture"},
		CodingModel: &protocol.CodingModelStatus{
			Role: "coding", Provider: "openai_codex", Name: "gpt-5.3-codex-spark",
			Readiness: protocol.ModelAuthenticationRequired,
		},
	}
	model.handleProtocol(protocol.ServerMessage{Hello: &hello})
	if view := model.View(); !strings.Contains(view, "openai_codex/gpt-5.3-codex-spark  readiness=authentication_required") {
		t.Fatalf("startup model status missing:\n%s", view)
	}
	if !strings.Contains(model.warning, "/login") {
		t.Fatalf("login guidance missing: %q", model.warning)
	}

	model.session.AcceptTask(protocol.TaskAccepted{RunID: "run-1", ThreadID: "thread-1", Created: true})
	if view := model.View(); !strings.Contains(view, "\ncoding-model  initializing\n") {
		t.Fatalf("initializing model status missing:\n%s", view)
	}

	model.session.ApplyEnvelope(protocol.EventEnvelope{
		Type: protocol.TypeEvent, ProtocolVersion: protocol.Version, Sequence: 1,
		RunID: "run-1", Durable: true,
		Event: protocol.AGUIEvent{Type: protocol.EventRunStarted, RunID: "run-1", ThreadID: "thread-1"},
	})
	if view := model.View(); !strings.Contains(view, "\ncoding-model  openai_codex/gpt-5.3-codex-spark  readiness=authentication_required\n") {
		t.Fatalf("startup model status was not retained:\n%s", view)
	}

	model.session.ApplyEnvelope(protocol.EventEnvelope{
		Type: protocol.TypeEvent, ProtocolVersion: protocol.Version, Sequence: 2,
		RunID: "run-1", Durable: true,
		Event: protocol.AGUIEvent{
			Type: protocol.EventCustom, Name: protocol.CodingModelStatusEventName,
			Value: json.RawMessage(`{"role":"coding","provider":"magnitude","name":"qwen-coder","readiness":"responding"}`),
		},
	})
	if view := model.View(); !strings.Contains(view, "\ncoding-model  magnitude/qwen-coder  readiness=responding\n") {
		t.Fatalf("reported model status missing:\n%s", view)
	}
}

func TestViewNeverRendersExtendedModelStatusSecrets(t *testing.T) {
	t.Parallel()
	transport := ws.New(ws.Config{OutboundBuffer: 8})
	model := New(transport, Config{History: 10, ContentBytes: 1024, AckEvery: 5})
	model.width, model.height = 80, 24
	model.session.RunID = "run-1"
	model.session.Status = session.StatusRunning
	secret := "ghp_abcdefghijklmnopqrstuvwxyz123456"
	model.session.ApplyEnvelope(protocol.EventEnvelope{
		Type: protocol.TypeEvent, ProtocolVersion: protocol.Version, Sequence: 1,
		RunID: "run-1", Durable: true,
		Event: protocol.AGUIEvent{
			Type: protocol.EventCustom, Name: protocol.CodingModelStatusEventName,
			Value: json.RawMessage(`{"role":"coding","provider":"magnitude","name":"coder","readiness":"responding","api_key":"` + secret + `"}`),
		},
	})
	view := model.View()
	if strings.Contains(view, secret) || strings.Contains(view, "api_key") {
		t.Fatalf("unsafe model status reached view:\n%s", view)
	}
	if !strings.Contains(view, "coding-model  unknown (server did not report)") {
		t.Fatalf("invalid status did not fall back safely:\n%s", view)
	}
}
