package ui

import (
	"testing"
	"time"

	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/session"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
)

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
