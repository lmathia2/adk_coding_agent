package ui

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
)

var fixtureUpgrader = websocket.Upgrader{
	CheckOrigin: func(_ *http.Request) bool { return true },
}

func TestLoopbackHelloThenStart(t *testing.T) {
	t.Parallel()
	serverErrors := make(chan error, 1)
	handlerDone := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer close(handlerDone)
		conn, err := fixtureUpgrader.Upgrade(writer, request, nil)
		if err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		defer conn.Close()
		if err := expectClientMessage(conn, "client.hello", func(value map[string]any) error {
			versions, ok := value["protocol_versions"].([]any)
			if !ok || len(versions) != 1 || versions[0] != float64(protocol.Version) {
				return fmt.Errorf("protocol_versions = %#v", value["protocol_versions"])
			}
			return nil
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeServerHello(conn); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := expectClientMessage(conn, "task.start", func(value map[string]any) error {
			if value["input"] != "fix the parser" || value["request_id"] != "request-fixed" || value["idempotency_key"] != "start-fixed" {
				return fmt.Errorf("unexpected start payload: %#v", value)
			}
			return nil
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := conn.WriteJSON(map[string]any{
			"type": "task.accepted", "protocol_version": 1,
			"request_id": "request-fixed", "run_id": "run-1", "thread_id": "thread-1", "created": true,
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeEvent(conn, 1, "run-1", map[string]any{
			"type": "RUN_STARTED", "threadId": "thread-1", "runId": "run-1",
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		_, _, _ = conn.ReadMessage()
	}))
	defer server.Close()

	client, model, cancel, clientDone := startFixtureClient(t, server, Config{
		InitialInput: "fix the parser",
		History:      20,
		ContentBytes: 4096,
		AckEvery:     10,
		ID:           func() string { return "fixed" },
	})
	pumpUntil(t, client, &model, func(model *Model) bool {
		return model.session.Cursor == 1 && model.session.RunID == "run-1"
	})
	stopFixtureClient(t, cancel, clientDone)
	waitFixtureHandler(t, handlerDone)
	assertNoFixtureError(t, serverErrors)
}

func TestLoopbackHelloThenAttachAtCursor(t *testing.T) {
	t.Parallel()
	serverErrors := make(chan error, 1)
	handlerDone := make(chan struct{})
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		defer close(handlerDone)
		conn, err := fixtureUpgrader.Upgrade(writer, request, nil)
		if err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		defer conn.Close()
		if err := expectClientMessage(conn, "client.hello", nil); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeServerHello(conn); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := expectAttach(conn, "run-attach", 17); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeEvent(conn, 18, "run-attach", map[string]any{
			"type": "STATE_SNAPSHOT", "snapshot": map[string]any{"phase": "verify"},
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		_, _, _ = conn.ReadMessage()
	}))
	defer server.Close()

	client, model, cancel, clientDone := startFixtureClient(t, server, Config{
		InitialRunID:  "run-attach",
		InitialCursor: 17,
		History:       20,
		ContentBytes:  4096,
		AckEvery:      10,
	})
	pumpUntil(t, client, &model, func(model *Model) bool { return model.session.Cursor == 18 })
	stopFixtureClient(t, cancel, clientDone)
	waitFixtureHandler(t, handlerDone)
	assertNoFixtureError(t, serverErrors)
}

func TestLoopbackReconnectResumesAfterAppliedCursor(t *testing.T) {
	t.Parallel()
	serverErrors := make(chan error, 1)
	resumes := make(chan int64, 2)
	secondHandlerDone := make(chan struct{})
	var connections atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		connection := connections.Add(1)
		conn, err := fixtureUpgrader.Upgrade(writer, request, nil)
		if err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		defer conn.Close()
		if connection == 2 {
			defer close(secondHandlerDone)
		}
		if err := expectClientMessage(conn, "client.hello", nil); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeServerHello(conn); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		var after int64
		if err := expectClientMessage(conn, "task.attach", func(value map[string]any) error {
			number, ok := value["after_sequence"].(float64)
			if !ok {
				return fmt.Errorf("after_sequence = %#v", value["after_sequence"])
			}
			after = int64(number)
			return nil
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		resumes <- after
		if connection == 1 {
			if err := writeEvent(conn, 42, "run-resume", map[string]any{
				"type": "STATE_SNAPSHOT", "snapshot": map[string]any{"phase": "code"},
			}); err != nil {
				reportFixtureError(serverErrors, err)
			}
			return
		}
		if connection == 2 {
			if err := writeEvent(conn, 43, "run-resume", map[string]any{
				"type": "STATE_SNAPSHOT", "snapshot": map[string]any{"phase": "verify"},
			}); err != nil {
				reportFixtureError(serverErrors, err)
				return
			}
			_, _, _ = conn.ReadMessage()
		}
	}))
	defer server.Close()

	client, model, cancel, clientDone := startFixtureClient(t, server, Config{
		InitialRunID:  "run-resume",
		InitialCursor: 41,
		History:       20,
		ContentBytes:  4096,
		AckEvery:      100,
	})
	pumpUntil(t, client, &model, func(model *Model) bool { return model.session.Cursor == 43 })
	if first, second := <-resumes, <-resumes; first != 41 || second != 42 {
		t.Fatalf("attach cursors = (%d, %d), want (41, 42)", first, second)
	}
	stopFixtureClient(t, cancel, clientDone)
	waitFixtureHandler(t, secondHandlerDone)
	assertNoFixtureError(t, serverErrors)
}

func TestReconnectReplaysUnconfirmedControlWithSameIdempotencyKey(t *testing.T) {
	t.Parallel()
	serverErrors := make(chan error, 1)
	secondHandlerDone := make(chan struct{})
	var connections atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		connection := connections.Add(1)
		conn, err := fixtureUpgrader.Upgrade(writer, request, nil)
		if err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		defer conn.Close()
		if connection == 2 {
			defer close(secondHandlerDone)
		}
		if err := expectClientMessage(conn, "client.hello", nil); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := writeServerHello(conn); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := expectAttach(conn, "run-control", 0); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if err := expectClientMessage(conn, "task.steer", func(value map[string]any) error {
			if value["idempotency_key"] != "control-fixed" || value["content"] != "keep going" {
				return fmt.Errorf("unexpected control: %#v", value)
			}
			return nil
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		if connection == 1 {
			return
		}
		if err := conn.WriteJSON(map[string]any{
			"type": "control.result", "protocol_version": 1, "operation": "steer",
			"run_id": "run-control", "accepted": true, "command_id": "control-fixed",
		}); err != nil {
			reportFixtureError(serverErrors, err)
			return
		}
		_, _, _ = conn.ReadMessage()
	}))
	defer server.Close()

	client, model, cancel, clientDone := startFixtureClient(t, server, Config{
		InitialRunID: "run-control", History: 20, ContentBytes: 4096, AckEvery: 10,
	})
	model.pendingControls = []pendingControl{{
		commandID: "control-fixed",
		message:   protocol.NewSteerTask("run-control", "keep going", "control-fixed", 0),
	}}
	pumpUntil(t, client, &model, func(model *Model) bool {
		return connections.Load() >= 2 && len(model.pendingControls) == 0
	})
	stopFixtureClient(t, cancel, clientDone)
	waitFixtureHandler(t, secondHandlerDone)
	assertNoFixtureError(t, serverErrors)
}

func startFixtureClient(t *testing.T, server *httptest.Server, config Config) (*ws.Client, Model, context.CancelFunc, <-chan struct{}) {
	t.Helper()
	ctx, cancel := context.WithCancel(context.Background())
	client := ws.New(ws.Config{
		URL:             "ws" + strings.TrimPrefix(server.URL, "http"),
		ClientName:      "compatibility-test",
		InboundBuffer:   32,
		OutboundBuffer:  8,
		MaxMessageBytes: 64 << 10,
		ReconnectMin:    time.Millisecond,
		ReconnectMax:    5 * time.Millisecond,
	})
	clientDone := make(chan struct{})
	go func() {
		client.Run(ctx)
		close(clientDone)
	}()
	return client, New(client, config), cancel, clientDone
}

func stopFixtureClient(t *testing.T, cancel context.CancelFunc, clientDone <-chan struct{}) {
	t.Helper()
	cancel()
	select {
	case <-clientDone:
	case <-time.After(2 * time.Second):
		t.Fatal("WebSocket transport did not stop; possible goroutine leak")
	}
}

func waitFixtureHandler(t *testing.T, handlerDone <-chan struct{}) {
	t.Helper()
	select {
	case <-handlerDone:
	case <-time.After(2 * time.Second):
		t.Fatal("loopback WebSocket handler did not stop")
	}
}

func pumpUntil(t *testing.T, client *ws.Client, model *Model, done func(*Model) bool) {
	t.Helper()
	deadline := time.NewTimer(3 * time.Second)
	defer deadline.Stop()
	for !done(model) {
		select {
		case event, ok := <-client.Events():
			if !ok {
				t.Fatal("transport stopped before fixture completed")
			}
			command := model.handleTransport(event)
			if command == nil {
				continue
			}
			message := command()
			if result, ok := message.(sendResultMsg); ok && result.err != nil {
				t.Fatalf("send command: %v", result.err)
			}
		case <-deadline.C:
			t.Fatalf("timed out; run=%s cursor=%d warning=%q", model.session.RunID, model.session.Cursor, model.warning)
		}
	}
}

func expectAttach(conn *websocket.Conn, runID string, after int64) error {
	return expectClientMessage(conn, "task.attach", func(value map[string]any) error {
		if value["run_id"] != runID || value["after_sequence"] != float64(after) {
			return fmt.Errorf("unexpected attach payload: %#v", value)
		}
		return nil
	})
}

func expectClientMessage(conn *websocket.Conn, messageType string, check func(map[string]any) error) error {
	if err := conn.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		return err
	}
	_, payload, err := conn.ReadMessage()
	if err != nil {
		return err
	}
	var value map[string]any
	if err := json.Unmarshal(payload, &value); err != nil {
		return err
	}
	if value["type"] != messageType {
		return fmt.Errorf("message type = %v, want %s", value["type"], messageType)
	}
	if check != nil {
		return check(value)
	}
	return nil
}

func writeServerHello(conn *websocket.Conn) error {
	return conn.WriteJSON(map[string]any{
		"type": "server.hello", "protocol_version": 1,
		"harness": map[string]any{
			"implementation": "fixture_harness", "api_version": 1,
			"display_name": "Fixture harness", "capabilities": []string{"streaming", "steering", "replay"},
			"protocol_versions": []int{1},
		},
	})
}

func writeEvent(conn *websocket.Conn, sequence int64, runID string, event map[string]any) error {
	return conn.WriteJSON(map[string]any{
		"type": "event", "protocol_version": 1, "sequence": sequence,
		"run_id": runID, "durable": true, "event": event,
	})
}

func reportFixtureError(channel chan<- error, err error) {
	if err == nil || errors.Is(err, context.Canceled) {
		return
	}
	select {
	case channel <- err:
	default:
	}
}

func assertNoFixtureError(t *testing.T, channel <-chan error) {
	t.Helper()
	select {
	case err := <-channel:
		t.Fatal(err)
	default:
	}
}
