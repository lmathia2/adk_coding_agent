package session

import (
	"encoding/json"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
)

func envelope(sequence int64, event protocol.AGUIEvent) protocol.EventEnvelope {
	return protocol.EventEnvelope{
		Type:            protocol.TypeEvent,
		ProtocolVersion: protocol.Version,
		Sequence:        sequence,
		RunID:           "run-1",
		Durable:         true,
		Event:           event,
	}
}

func TestReducerStreamsTextAndTools(t *testing.T) {
	t.Parallel()
	state := New(20, 4096, 10)
	state.AcceptTask(protocol.TaskAccepted{RunID: "run-1", ThreadID: "thread-1", Created: true})
	events := []protocol.AGUIEvent{
		{Type: protocol.EventRunStarted, RunID: "run-1", ThreadID: "thread-1"},
		{Type: protocol.EventTextMessageStart, MessageID: "message-1", Role: "assistant"},
		{Type: protocol.EventTextMessageContent, MessageID: "message-1", Delta: json.RawMessage(`"hel"`)},
		{Type: protocol.EventTextMessageContent, MessageID: "message-1", Delta: json.RawMessage(`"lo"`)},
		{Type: protocol.EventTextMessageEnd, MessageID: "message-1"},
		{Type: protocol.EventToolCallStart, ToolCallID: "tool-1", ToolCallName: "read"},
		{Type: protocol.EventToolCallArgs, ToolCallID: "tool-1", Delta: json.RawMessage(`"{\"path\":\"README.md\"}"`)},
		{Type: protocol.EventToolCallEnd, ToolCallID: "tool-1"},
		{Type: protocol.EventToolCallResult, ToolCallID: "tool-1", Content: "ok"},
	}
	for index, event := range events {
		result := state.ApplyEnvelope(envelope(int64(index+1), event))
		if !result.Applied {
			t.Fatalf("event %d was not applied", index+1)
		}
	}
	if state.Status != StatusRunning || state.Cursor != 9 {
		t.Fatalf("status=%s cursor=%d", state.Status, state.Cursor)
	}
	var text, tool *Entry
	for index := range state.Entries {
		switch state.Entries[index].Kind {
		case EntryText:
			text = &state.Entries[index]
		case EntryTool:
			tool = &state.Entries[index]
		}
	}
	if text == nil || text.Content != "hello" || !text.Done {
		t.Fatalf("text entry = %#v", text)
	}
	if tool == nil || tool.Title != "read" || tool.Content != `{"path":"README.md"}ok` || !tool.Done {
		t.Fatalf("tool entry = %#v", tool)
	}
}

func TestReducerDeduplicatesReplayAndAcknowledgesBatches(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 2)
	first := state.ApplyEnvelope(envelope(1, protocol.AGUIEvent{Type: protocol.EventTextMessageContent, MessageID: "m", Delta: json.RawMessage(`"a"`)}))
	second := state.ApplyEnvelope(envelope(2, protocol.AGUIEvent{Type: protocol.EventTextMessageContent, MessageID: "m", Delta: json.RawMessage(`"b"`)}))
	replayed := state.ApplyEnvelope(envelope(2, protocol.AGUIEvent{Type: protocol.EventTextMessageContent, MessageID: "m", Delta: json.RawMessage(`"b"`)}))
	if first.Ack != nil || second.Ack == nil || second.Ack.ThroughSequence != 2 {
		t.Fatalf("unexpected ack progression: first=%#v second=%#v", first.Ack, second.Ack)
	}
	if replayed.Applied {
		t.Fatal("replayed event was applied twice")
	}
	if got := state.Entries[0].Content; got != "ab" {
		t.Fatalf("stream content = %q", got)
	}
}

func TestReconnectUsesHighestAppliedCursor(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 5)
	state.RunID = "run-1"
	state.Cursor = 41
	state.ApplyEnvelope(envelope(42, protocol.AGUIEvent{Type: protocol.EventStateSnapshot, Snapshot: json.RawMessage(`{"phase":"verify"}`)}))

	attach, ok := state.ResumeMessage()
	if !ok {
		t.Fatal("expected reconnect attachment")
	}
	if attach.RunID != "run-1" || attach.AfterSequence != 42 {
		t.Fatalf("resume = %#v", attach)
	}
	if replay := state.ApplyEnvelope(envelope(42, protocol.AGUIEvent{Type: protocol.EventRunError, Message: "duplicate"})); replay.Applied {
		t.Fatal("cursor replay was not deduplicated")
	}
	if state.Status == StatusFailed {
		t.Fatal("duplicate replay mutated state")
	}
}

func TestReducerDoesNotAdvanceAcrossSequenceGap(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 5)
	state.RunID = "run-1"
	state.Cursor = 7

	result := state.ApplyEnvelope(envelope(9, protocol.AGUIEvent{
		Type:    protocol.EventRunError,
		Message: "must not be applied",
	}))
	if !result.Gap || result.Applied {
		t.Fatalf("gap result = %#v", result)
	}
	if state.Cursor != 7 || state.Status == StatusFailed {
		t.Fatalf("gap mutated state: cursor=%d status=%s", state.Cursor, state.Status)
	}
	attach, ok := state.ResumeMessage()
	if !ok || attach.AfterSequence != 7 {
		t.Fatalf("resume = %#v, ok=%v", attach, ok)
	}
}

func TestReducerBoundsHistoryAndEntryContent(t *testing.T) {
	t.Parallel()
	state := New(2, 256, 100)
	for sequence := int64(1); sequence <= 3; sequence++ {
		state.ApplyEnvelope(envelope(sequence, protocol.AGUIEvent{
			Type:      protocol.EventCustom,
			Name:      "coding.test",
			Value:     json.RawMessage(`"` + string(make([]byte, 400)) + `"`),
			MessageID: "unused",
		}))
	}
	if len(state.Entries) != 2 {
		t.Fatalf("history length = %d", len(state.Entries))
	}
	if len(state.Entries[0].Content) > 256 {
		t.Fatalf("entry bytes = %d", len(state.Entries[0].Content))
	}
}

func TestContentTruncationPreservesUTF8(t *testing.T) {
	t.Parallel()
	state := New(2, 256, 100)
	delta, err := json.Marshal(strings.Repeat("界", 200))
	if err != nil {
		t.Fatal(err)
	}
	state.ApplyEnvelope(envelope(1, protocol.AGUIEvent{
		Type:      protocol.EventTextMessageContent,
		MessageID: "message-1",
		Delta:     delta,
	}))
	if !utf8.ValidString(state.Entries[0].Content) {
		t.Fatal("bounded content is not valid UTF-8")
	}
}

func TestTerminalEventForcesAcknowledgement(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 100)
	result := state.ApplyEnvelope(envelope(1, protocol.AGUIEvent{Type: protocol.EventRunFinished, ThreadID: "thread-1", RunID: "run-1"}))
	if state.Status != StatusCompleted || result.Ack == nil || result.Ack.ThroughSequence != 1 {
		t.Fatalf("state=%s result=%#v", state.Status, result)
	}
}

func TestCancelledRunFinishDoesNotRenderCompleted(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 100)
	state.ApplyEnvelope(envelope(1, protocol.AGUIEvent{
		Type:   protocol.EventRunFinished,
		RunID:  "run-1",
		Result: json.RawMessage(`{"status":"cancelled"}`),
	}))
	if state.Status != StatusCancelled {
		t.Fatalf("status = %s", state.Status)
	}
	if got := state.Entries[len(state.Entries)-1].Content; got != "cancelled" {
		t.Fatalf("terminal content = %q", got)
	}
}

func TestReducerTracksCodingModelWithoutAddingStatusTranscriptEntries(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 100)
	state.AcceptTask(protocol.TaskAccepted{RunID: "run-1", ThreadID: "thread-1", Created: true})
	started := protocol.AGUIEvent{
		Type:     protocol.EventRunStarted,
		RunID:    "run-1",
		ThreadID: "thread-1",
		Metadata: json.RawMessage(`{"coding.model":{"role":"coding","provider":"openai_compatible","name":"qwen/local-coder","readiness":"adapter_initialized"}}`),
	}
	state.ApplyEnvelope(envelope(1, started))
	if state.CodingModel == nil || state.CodingModel.Name != "qwen/local-coder" || state.CodingModel.Readiness != protocol.ModelAdapterInitialized {
		t.Fatalf("initial coding model = %#v", state.CodingModel)
	}
	entriesAfterStart := len(state.Entries)

	state.ApplyEnvelope(envelope(2, protocol.AGUIEvent{
		Type: protocol.EventCustom,
		Name: protocol.CodingModelStatusEventName,
		Value: json.RawMessage(
			`{"role":"coding","provider":"openai_compatible","name":"qwen/local-coder","readiness":"responding"}`,
		),
	}))
	if state.CodingModel == nil || state.CodingModel.Readiness != protocol.ModelResponding {
		t.Fatalf("updated coding model = %#v", state.CodingModel)
	}
	if len(state.Entries) != entriesAfterStart {
		t.Fatal("model status custom event was rendered as a transcript entry")
	}
}

func TestReducerIgnoresUnsafeModelStatusAndReplay(t *testing.T) {
	t.Parallel()
	state := New(10, 1024, 100)
	state.RunID = "run-1"
	state.ApplyEnvelope(envelope(1, protocol.AGUIEvent{
		Type:     protocol.EventRunStarted,
		RunID:    "run-1",
		ThreadID: "thread-1",
	}))
	unsafe := protocol.AGUIEvent{
		Type: protocol.EventCustom,
		Name: protocol.CodingModelStatusEventName,
		Value: json.RawMessage(
			`{"role":"coding","provider":"openai_compatible","name":"coder","readiness":"responding","api_key":"ghp_abcdefghijklmnopqrstuvwxyz123456"}`,
		),
	}
	state.ApplyEnvelope(envelope(2, unsafe))
	if state.CodingModel != nil {
		t.Fatalf("unsafe model status was retained: %#v", state.CodingModel)
	}
	if len(state.Entries) != 1 {
		t.Fatalf("unsafe model event changed transcript: %#v", state.Entries)
	}

	valid := unsafe
	valid.Value = json.RawMessage(`{"role":"coding","provider":"openai_compatible","name":"coder","readiness":"responding"}`)
	if replay := state.ApplyEnvelope(envelope(2, valid)); replay.Applied {
		t.Fatal("replayed sequence changed model status")
	}
	if state.CodingModel != nil {
		t.Fatal("replayed model status mutated state")
	}
}
