package protocol

import (
	"encoding/json"
	"reflect"
	"testing"
)

func TestEncodeClientGoldenMessages(t *testing.T) {
	t.Parallel()
	tests := []struct {
		name    string
		message ClientMessage
		want    string
	}{
		{
			name:    "hello",
			message: NewHello("test-tui"),
			want:    `{"type":"client.hello","protocol_versions":[1],"client_name":"test-tui"}`,
		},
		{
			name:    "attach",
			message: NewAttachTask("run-1", 17),
			want:    `{"type":"task.attach","protocol_version":1,"run_id":"run-1","after_sequence":17}`,
		},
		{
			name:    "steer",
			message: NewSteerTask("run-1", "keep the API stable", "steer-1", 3),
			want:    `{"type":"task.steer","protocol_version":1,"run_id":"run-1","content":"keep the API stable","priority":3,"idempotency_key":"steer-1"}`,
		},
		{
			name:    "cancel",
			message: NewCancelTask("run-1", "cancel-1"),
			want:    `{"type":"task.cancel","protocol_version":1,"run_id":"run-1","idempotency_key":"cancel-1"}`,
		},
		{
			name:    "ack",
			message: NewAck("run-1", 17),
			want:    `{"type":"events.ack","protocol_version":1,"run_id":"run-1","through_sequence":17}`,
		},
	}
	for _, test := range tests {
		test := test
		t.Run(test.name, func(t *testing.T) {
			t.Parallel()
			got, err := EncodeClient(test.message)
			if err != nil {
				t.Fatal(err)
			}
			if string(got) != test.want {
				t.Fatalf("wire JSON mismatch\n got: %s\nwant: %s", got, test.want)
			}
		})
	}
}

func TestStartTaskMetadataAndOptionalThread(t *testing.T) {
	t.Parallel()
	message := NewStartTask("request-1", "start-1", "fix it", "thread-1", map[string]string{"repo": "demo"})
	payload, err := EncodeClient(message)
	if err != nil {
		t.Fatal(err)
	}
	var got map[string]any
	if err := json.Unmarshal(payload, &got); err != nil {
		t.Fatal(err)
	}
	if got["thread_id"] != "thread-1" {
		t.Fatalf("thread_id = %v", got["thread_id"])
	}
	if !reflect.DeepEqual(got["metadata"], map[string]any{"repo": "demo"}) {
		t.Fatalf("metadata = %#v", got["metadata"])
	}
}

func TestDecodeCanonicalAGUIEnvelope(t *testing.T) {
	t.Parallel()
	payload := []byte(`{
		"type":"event","protocol_version":1,"sequence":23,"run_id":"run-1","durable":true,
		"event":{"type":"TOOL_CALL_START","toolCallId":"call-1","toolCallName":"read"}
	}`)
	message, err := DecodeServer(payload)
	if err != nil {
		t.Fatal(err)
	}
	if message.Envelope == nil {
		t.Fatal("expected event envelope")
	}
	if message.Envelope.Sequence != 23 || message.Envelope.Event.ToolCallID != "call-1" || message.Envelope.Event.ToolCallName != "read" {
		t.Fatalf("unexpected envelope: %#v", message.Envelope)
	}
}

func TestDecodeRejectsUnknownTypeVersionAndFields(t *testing.T) {
	t.Parallel()
	for _, payload := range []string{
		`{"type":"unknown","protocol_version":1}`,
		`{"type":"pong","protocol_version":2,"nonce":"n"}`,
		`{"type":"pong","protocol_version":1,"nonce":"n","extra":true}`,
		`{"type":"pong","protocol_version":1,"nonce":""}`,
		`{"type":"pong","protocol_version":1,"nonce":"n"}{}`,
		`{"type":"event","protocol_version":1,"sequence":3,"run_id":"run-1","durable":true,"event":{"type":"TEXT_MESSAGE_CONTENT","messageId":"m","delta":[]}}`,
		`{"type":"event","protocol_version":1,"sequence":3,"run_id":"run-1","durable":true,"event":{"type":"CUSTOM","name":"foreign.event","value":{}}}`,
	} {
		if _, err := DecodeServer([]byte(payload)); err == nil {
			t.Fatalf("expected failure for %s", payload)
		}
	}
}

func TestDeltaTextHandlesStringsAndStructuredDeltas(t *testing.T) {
	t.Parallel()
	if got := (AGUIEvent{Delta: json.RawMessage(`"hello"`)}).DeltaText(); got != "hello" {
		t.Fatalf("string delta = %q", got)
	}
	if got := (AGUIEvent{Delta: json.RawMessage(`[{"op":"add"}]`)}).DeltaText(); got != `[{"op":"add"}]` {
		t.Fatalf("structured delta = %q", got)
	}
}

func TestCodingModelStatusAcceptsRunMetadataAndCustomUpdates(t *testing.T) {
	t.Parallel()
	want := CodingModelStatus{
		Role:      "coding",
		Provider:  "openai_compatible",
		Name:      "qwen/local-coder",
		Readiness: ModelAdapterInitialized,
	}
	started := AGUIEvent{
		Type:     EventRunStarted,
		Metadata: json.RawMessage(`{"coding.model":{"role":"coding","provider":"openai_compatible","name":"qwen/local-coder","readiness":"adapter_initialized"}}`),
	}
	if got, ok := started.CodingModelStatus(); !ok || got != want {
		t.Fatalf("RUN_STARTED status = %#v, ok=%v", got, ok)
	}

	responding := AGUIEvent{
		Type: EventCustom,
		Name: CodingModelStatusEventName,
		Value: json.RawMessage(
			`{"role":"coding","provider":"openai_compatible","name":"qwen/local-coder","readiness":"responding"}`,
		),
	}
	want.Readiness = ModelResponding
	if got, ok := responding.CodingModelStatus(); !ok || got != want {
		t.Fatalf("CUSTOM status = %#v, ok=%v", got, ok)
	}
}

func TestCodingModelStatusIgnoresMalformedOrExtendedPayloads(t *testing.T) {
	t.Parallel()
	for _, payload := range []string{
		`null`,
		`{"role":"review","provider":"openai_compatible","name":"coder","readiness":"responding"}`,
		`{"role":"coding","provider":"openai_compatible","name":"coder","readiness":"loaded"}`,
		`{"role":"coding","provider":"openai_compatible","name":"coder","readiness":"responding","api_key":"ghp_abcdefghijklmnopqrstuvwxyz123456"}`,
		`{"role":"coding","provider":"bad\nprovider","name":"coder","readiness":"responding"}`,
	} {
		event := AGUIEvent{Type: EventCustom, Name: CodingModelStatusEventName, Value: json.RawMessage(payload)}
		if status, ok := event.CodingModelStatus(); ok {
			t.Fatalf("accepted unsafe status %#v from %s", status, payload)
		}
	}
}

func TestDecodeLegacyRunStartedWithoutModelStatus(t *testing.T) {
	t.Parallel()
	payload := []byte(`{
		"type":"event","protocol_version":1,"sequence":1,"run_id":"run-1","durable":true,
		"event":{"type":"RUN_STARTED","threadId":"thread-1","runId":"run-1"}
	}`)
	message, err := DecodeServer(payload)
	if err != nil {
		t.Fatal(err)
	}
	if _, ok := message.Envelope.Event.CodingModelStatus(); ok {
		t.Fatal("legacy RUN_STARTED unexpectedly reported a model")
	}
}
