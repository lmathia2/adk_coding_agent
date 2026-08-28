// Package protocol defines the public, versioned WebSocket and AG-UI wire contract.
// It intentionally has no dependency on ADK or the server's Python implementation.
package protocol

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
)

const Version = 1

const (
	TypeServerHello   = "server.hello"
	TypeTaskAccepted  = "task.accepted"
	TypeControlResult = "control.result"
	TypeEvent         = "event"
	TypePong          = "pong"
	TypeError         = "error"
)

const (
	EventRunStarted         = "RUN_STARTED"
	EventRunFinished        = "RUN_FINISHED"
	EventRunError           = "RUN_ERROR"
	EventStepStarted        = "STEP_STARTED"
	EventStepFinished       = "STEP_FINISHED"
	EventTextMessageStart   = "TEXT_MESSAGE_START"
	EventTextMessageContent = "TEXT_MESSAGE_CONTENT"
	EventTextMessageEnd     = "TEXT_MESSAGE_END"
	EventToolCallStart      = "TOOL_CALL_START"
	EventToolCallArgs       = "TOOL_CALL_ARGS"
	EventToolCallEnd        = "TOOL_CALL_END"
	EventToolCallResult     = "TOOL_CALL_RESULT"
	EventStateSnapshot      = "STATE_SNAPSHOT"
	EventStateDelta         = "STATE_DELTA"
	EventCustom             = "CUSTOM"
)

// ClientMessage is implemented by every client-to-server control message.
type ClientMessage interface {
	clientMessage()
}

type Hello struct {
	Type             string `json:"type"`
	ProtocolVersions []int  `json:"protocol_versions"`
	ClientName       string `json:"client_name"`
}

func NewHello(clientName string) Hello {
	return Hello{Type: "client.hello", ProtocolVersions: []int{Version}, ClientName: clientName}
}

func (Hello) clientMessage() {}

type StartTask struct {
	Type            string            `json:"type"`
	ProtocolVersion int               `json:"protocol_version"`
	RequestID       string            `json:"request_id"`
	IdempotencyKey  string            `json:"idempotency_key"`
	Input           string            `json:"input"`
	ThreadID        string            `json:"thread_id,omitempty"`
	Metadata        map[string]string `json:"metadata,omitempty"`
}

func NewStartTask(requestID, idempotencyKey, input, threadID string, metadata map[string]string) StartTask {
	return StartTask{
		Type:            "task.start",
		ProtocolVersion: Version,
		RequestID:       requestID,
		IdempotencyKey:  idempotencyKey,
		Input:           input,
		ThreadID:        threadID,
		Metadata:        metadata,
	}
}

func (StartTask) clientMessage() {}

type AttachTask struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RunID           string `json:"run_id"`
	AfterSequence   int64  `json:"after_sequence"`
}

func NewAttachTask(runID string, afterSequence int64) AttachTask {
	return AttachTask{Type: "task.attach", ProtocolVersion: Version, RunID: runID, AfterSequence: afterSequence}
}

func (AttachTask) clientMessage() {}

type SteerTask struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RunID           string `json:"run_id"`
	Content         string `json:"content"`
	Priority        int    `json:"priority"`
	IdempotencyKey  string `json:"idempotency_key"`
}

func NewSteerTask(runID, content, idempotencyKey string, priority int) SteerTask {
	return SteerTask{
		Type:            "task.steer",
		ProtocolVersion: Version,
		RunID:           runID,
		Content:         content,
		Priority:        priority,
		IdempotencyKey:  idempotencyKey,
	}
}

func (SteerTask) clientMessage() {}

type PauseTask struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RunID           string `json:"run_id"`
	IdempotencyKey  string `json:"idempotency_key"`
}

func NewPauseTask(runID, idempotencyKey string) PauseTask {
	return PauseTask{Type: "task.pause", ProtocolVersion: Version, RunID: runID, IdempotencyKey: idempotencyKey}
}

func (PauseTask) clientMessage() {}

type CancelTask struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RunID           string `json:"run_id"`
	IdempotencyKey  string `json:"idempotency_key"`
}

func NewCancelTask(runID, idempotencyKey string) CancelTask {
	return CancelTask{Type: "task.cancel", ProtocolVersion: Version, RunID: runID, IdempotencyKey: idempotencyKey}
}

func (CancelTask) clientMessage() {}

type Ack struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RunID           string `json:"run_id"`
	ThroughSequence int64  `json:"through_sequence"`
}

func NewAck(runID string, throughSequence int64) Ack {
	return Ack{Type: "events.ack", ProtocolVersion: Version, RunID: runID, ThroughSequence: throughSequence}
}

func (Ack) clientMessage() {}

type Ping struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
}

func NewPing(nonce string) Ping {
	return Ping{Type: "ping", ProtocolVersion: Version, Nonce: nonce}
}

func (Ping) clientMessage() {}

func EncodeClient(message ClientMessage) ([]byte, error) {
	if message == nil {
		return nil, errors.New("protocol: nil client message")
	}
	return json.Marshal(message)
}

type HarnessDescriptor struct {
	Implementation   string   `json:"implementation"`
	APIVersion       int      `json:"api_version"`
	DisplayName      string   `json:"display_name"`
	Capabilities     []string `json:"capabilities"`
	ProtocolVersions []int    `json:"protocol_versions"`
}

type ServerHello struct {
	Type            string            `json:"type"`
	ProtocolVersion int               `json:"protocol_version"`
	Harness         HarnessDescriptor `json:"harness"`
}

type TaskAccepted struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	RequestID       string `json:"request_id"`
	RunID           string `json:"run_id"`
	ThreadID        string `json:"thread_id"`
	Created         bool   `json:"created"`
}

type ControlResult struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	Operation       string `json:"operation"`
	RunID           string `json:"run_id"`
	Accepted        bool   `json:"accepted"`
	CommandID       string `json:"command_id"`
	Detail          string `json:"detail,omitempty"`
}

type Pong struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	Nonce           string `json:"nonce"`
}

type ServerError struct {
	Type            string `json:"type"`
	ProtocolVersion int    `json:"protocol_version"`
	Code            string `json:"code"`
	Message         string `json:"message"`
	RequestID       string `json:"request_id,omitempty"`
	RunID           string `json:"run_id,omitempty"`
	Retryable       bool   `json:"retryable"`
}

// AGUIEvent uses the canonical AG-UI camelCase field names. Dynamic values stay as
// bounded raw JSON so new namespaced coding events do not require a TUI release.
type AGUIEvent struct {
	Type         string          `json:"type"`
	Timestamp    int64           `json:"timestamp,omitempty"`
	Metadata     json.RawMessage `json:"metadata,omitempty"`
	ThreadID     string          `json:"threadId,omitempty"`
	RunID        string          `json:"runId,omitempty"`
	MessageID    string          `json:"messageId,omitempty"`
	Role         string          `json:"role,omitempty"`
	ToolCallID   string          `json:"toolCallId,omitempty"`
	ToolCallName string          `json:"toolCallName,omitempty"`
	Delta        json.RawMessage `json:"delta,omitempty"`
	Content      string          `json:"content,omitempty"`
	Name         string          `json:"name,omitempty"`
	Value        json.RawMessage `json:"value,omitempty"`
	Snapshot     json.RawMessage `json:"snapshot,omitempty"`
	Message      string          `json:"message,omitempty"`
	Code         string          `json:"code,omitempty"`
	Result       json.RawMessage `json:"result,omitempty"`
	StepName     string          `json:"stepName,omitempty"`
}

func (e AGUIEvent) DeltaText() string {
	if len(e.Delta) == 0 {
		return ""
	}
	var value string
	if json.Unmarshal(e.Delta, &value) == nil {
		return value
	}
	return string(e.Delta)
}

type EventEnvelope struct {
	Type            string    `json:"type"`
	ProtocolVersion int       `json:"protocol_version"`
	Sequence        int64     `json:"sequence"`
	RunID           string    `json:"run_id"`
	SessionID       string    `json:"session_id,omitempty"`
	InvocationID    string    `json:"invocation_id,omitempty"`
	Durable         bool      `json:"durable"`
	Event           AGUIEvent `json:"event"`
}

// ServerMessage is a discriminated decoded server frame.
type ServerMessage struct {
	Type          string
	Hello         *ServerHello
	TaskAccepted  *TaskAccepted
	ControlResult *ControlResult
	Envelope      *EventEnvelope
	Pong          *Pong
	Error         *ServerError
}

func DecodeServer(data []byte) (ServerMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	var discriminator struct {
		Type string `json:"type"`
	}
	if err := json.Unmarshal(data, &discriminator); err != nil {
		return ServerMessage{}, fmt.Errorf("protocol: invalid JSON: %w", err)
	}
	if discriminator.Type == "" {
		return ServerMessage{}, errors.New("protocol: missing message type")
	}

	result := ServerMessage{Type: discriminator.Type}
	var target any
	switch discriminator.Type {
	case TypeServerHello:
		result.Hello = &ServerHello{}
		target = result.Hello
	case TypeTaskAccepted:
		result.TaskAccepted = &TaskAccepted{}
		target = result.TaskAccepted
	case TypeControlResult:
		result.ControlResult = &ControlResult{}
		target = result.ControlResult
	case TypeEvent:
		result.Envelope = &EventEnvelope{}
		target = result.Envelope
	case TypePong:
		result.Pong = &Pong{}
		target = result.Pong
	case TypeError:
		result.Error = &ServerError{}
		target = result.Error
	default:
		return ServerMessage{}, fmt.Errorf("protocol: unsupported server message type %q", discriminator.Type)
	}

	if err := decoder.Decode(target); err != nil {
		return ServerMessage{}, fmt.Errorf("protocol: decode %s: %w", discriminator.Type, err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			return ServerMessage{}, errors.New("protocol: multiple JSON values in one frame")
		}
		return ServerMessage{}, fmt.Errorf("protocol: trailing JSON: %w", err)
	}
	if version := serverVersion(result); version != Version {
		return ServerMessage{}, fmt.Errorf("protocol: unsupported server version %d", version)
	}
	if err := validateServer(result); err != nil {
		return ServerMessage{}, err
	}
	return result, nil
}

func validateServer(message ServerMessage) error {
	switch {
	case message.Hello != nil:
		harness := message.Hello.Harness
		if !validImplementation(harness.Implementation) || !validString(harness.DisplayName, 128) || harness.APIVersion < 1 {
			return errors.New("protocol: invalid harness descriptor")
		}
		foundVersion := false
		for _, version := range harness.ProtocolVersions {
			foundVersion = foundVersion || version == Version
		}
		if !foundVersion {
			return errors.New("protocol: harness does not advertise negotiated version")
		}
	case message.TaskAccepted != nil:
		value := message.TaskAccepted
		if !validString(value.RequestID, 256) || !validString(value.RunID, 256) || !validString(value.ThreadID, 256) {
			return errors.New("protocol: invalid task acceptance")
		}
	case message.ControlResult != nil:
		value := message.ControlResult
		if (value.Operation != "steer" && value.Operation != "pause" && value.Operation != "cancel") ||
			!validString(value.RunID, 256) || !validString(value.CommandID, 256) || len(value.Detail) > 2048 {
			return errors.New("protocol: invalid control result")
		}
	case message.Envelope != nil:
		value := message.Envelope
		if value.Sequence < 1 || !validString(value.RunID, 256) || len(value.SessionID) > 256 || len(value.InvocationID) > 256 {
			return errors.New("protocol: invalid event envelope")
		}
		if err := validateEvent(value.Event); err != nil {
			return err
		}
	case message.Pong != nil:
		if !validString(message.Pong.Nonce, 256) {
			return errors.New("protocol: invalid pong nonce")
		}
	case message.Error != nil:
		value := message.Error
		if !validString(value.Code, 256) || !validString(value.Message, 4096) || len(value.RequestID) > 256 || len(value.RunID) > 256 {
			return errors.New("protocol: invalid server error")
		}
	default:
		return errors.New("protocol: empty server message")
	}
	return nil
}

func validateEvent(event AGUIEvent) error {
	if event.Timestamp < 0 || len(event.ThreadID) > 256 || len(event.RunID) > 256 || len(event.MessageID) > 256 ||
		len(event.ToolCallID) > 256 || len(event.ToolCallName) > 128 || len(event.Name) > 256 || len(event.Code) > 256 || len(event.StepName) > 256 {
		return errors.New("protocol: invalid AG-UI event field")
	}
	if event.Role != "" && event.Role != "developer" && event.Role != "system" && event.Role != "assistant" &&
		event.Role != "user" && event.Role != "tool" {
		return errors.New("protocol: invalid AG-UI role")
	}
	require := func(value, field string) error {
		if value == "" {
			return fmt.Errorf("protocol: AG-UI event requires %s", field)
		}
		return nil
	}
	switch event.Type {
	case EventRunStarted, EventRunFinished:
		if err := require(event.ThreadID, "threadId"); err != nil {
			return err
		}
		return require(event.RunID, "runId")
	case EventRunError:
		if event.Message == "" {
			return errors.New("protocol: RUN_ERROR requires message")
		}
	case EventStepStarted, EventStepFinished:
		return require(event.StepName, "stepName")
	case EventTextMessageStart:
		if event.Role == "tool" {
			return errors.New("protocol: text message role cannot be tool")
		}
		return require(event.MessageID, "messageId")
	case EventTextMessageContent:
		if err := require(event.MessageID, "messageId"); err != nil {
			return err
		}
		if _, ok := rawString(event.Delta); !ok {
			return errors.New("protocol: text content delta must be a JSON string")
		}
	case EventTextMessageEnd:
		return require(event.MessageID, "messageId")
	case EventToolCallStart:
		if err := require(event.ToolCallID, "toolCallId"); err != nil {
			return err
		}
		return require(event.ToolCallName, "toolCallName")
	case EventToolCallArgs:
		if err := require(event.ToolCallID, "toolCallId"); err != nil {
			return err
		}
		if _, ok := rawString(event.Delta); !ok {
			return errors.New("protocol: tool args delta must be a JSON string")
		}
	case EventToolCallEnd:
		return require(event.ToolCallID, "toolCallId")
	case EventToolCallResult:
		if err := require(event.MessageID, "messageId"); err != nil {
			return err
		}
		if err := require(event.ToolCallID, "toolCallId"); err != nil {
			return err
		}
		if event.Role != "" && event.Role != "tool" {
			return errors.New("protocol: tool result role must be tool")
		}
	case EventStateSnapshot:
		var snapshot map[string]any
		if len(event.Snapshot) == 0 || json.Unmarshal(event.Snapshot, &snapshot) != nil || snapshot == nil {
			return errors.New("protocol: state snapshot must be a JSON object")
		}
	case EventStateDelta:
		var delta []map[string]any
		if len(event.Delta) == 0 || json.Unmarshal(event.Delta, &delta) != nil || delta == nil {
			return errors.New("protocol: state delta must be a JSON array")
		}
	case EventCustom:
		if !strings.HasPrefix(event.Name, "coding.") || len(event.Value) == 0 || bytes.Equal(bytes.TrimSpace(event.Value), []byte("null")) {
			return errors.New("protocol: invalid coding custom event")
		}
	default:
		return fmt.Errorf("protocol: unsupported AG-UI event type %q", event.Type)
	}
	return nil
}

func rawString(value json.RawMessage) (string, bool) {
	if len(value) == 0 {
		return "", false
	}
	var decoded string
	if json.Unmarshal(value, &decoded) != nil {
		return "", false
	}
	return decoded, true
}

func validString(value string, max int) bool {
	return value != "" && len(value) <= max
}

func validImplementation(value string) bool {
	if len(value) < 3 || len(value) > 64 || value[0] < 'a' || value[0] > 'z' {
		return false
	}
	for _, char := range value[1:] {
		if (char < 'a' || char > 'z') && (char < '0' || char > '9') && char != '_' {
			return false
		}
	}
	return true
}

func serverVersion(message ServerMessage) int {
	switch {
	case message.Hello != nil:
		return message.Hello.ProtocolVersion
	case message.TaskAccepted != nil:
		return message.TaskAccepted.ProtocolVersion
	case message.ControlResult != nil:
		return message.ControlResult.ProtocolVersion
	case message.Envelope != nil:
		return message.Envelope.ProtocolVersion
	case message.Pong != nil:
		return message.Pong.ProtocolVersion
	case message.Error != nil:
		return message.Error.ProtocolVersion
	default:
		return 0
	}
}
