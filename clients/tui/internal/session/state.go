// Package session contains the deterministic client-side protocol reducer.
package session

import (
	"encoding/json"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
)

type RunStatus string

const (
	StatusIdle      RunStatus = "idle"
	StatusStarting  RunStatus = "starting"
	StatusRunning   RunStatus = "running"
	StatusPaused    RunStatus = "paused"
	StatusCompleted RunStatus = "completed"
	StatusCancelled RunStatus = "cancelled"
	StatusFailed    RunStatus = "failed"
)

type EntryKind string

const (
	EntryStatus EntryKind = "status"
	EntryText   EntryKind = "text"
	EntryTool   EntryKind = "tool"
	EntryError  EntryKind = "error"
	EntryCustom EntryKind = "custom"
)

type Entry struct {
	Kind    EntryKind
	ID      string
	Title   string
	Content string
	Done    bool
}

type State struct {
	RunID          string
	ThreadID       string
	Cursor         int64
	Acknowledged   int64
	Status         RunStatus
	Step           string
	Entries        []Entry
	StateSnapshot  json.RawMessage
	LastError      string
	LastPongNonce  string
	maxEntries     int
	maxContentByte int
	ackEvery       int64
}

type ApplyResult struct {
	Applied bool
	Gap     bool
	Ack     *protocol.Ack
}

func New(maxEntries, maxContentBytes int, ackEvery int64) State {
	if maxEntries < 1 {
		maxEntries = 1
	}
	if maxContentBytes < 256 {
		maxContentBytes = 256
	}
	if ackEvery < 1 {
		ackEvery = 1
	}
	return State{
		Status:         StatusIdle,
		maxEntries:     maxEntries,
		maxContentByte: maxContentBytes,
		ackEvery:       ackEvery,
	}
}

func (s *State) AcceptTask(message protocol.TaskAccepted) {
	s.RunID = message.RunID
	s.ThreadID = message.ThreadID
	s.Status = StatusStarting
	s.appendEntry(Entry{Kind: EntryStatus, Title: "task accepted", Content: message.RunID, Done: true})
}

func (s State) ResumeMessage() (protocol.AttachTask, bool) {
	if s.RunID == "" {
		return protocol.AttachTask{}, false
	}
	return protocol.NewAttachTask(s.RunID, s.Cursor), true
}

func (s *State) MarkAcknowledged(sequence int64) {
	if sequence > s.Acknowledged && sequence <= s.Cursor {
		s.Acknowledged = sequence
	}
}

func (s *State) Notice(title, content string) {
	s.appendEntry(Entry{Kind: EntryStatus, Title: title, Content: content, Done: true})
}

func (s *State) Fail(message string) {
	s.LastError = message
	s.appendEntry(Entry{Kind: EntryError, Title: "client", Content: message, Done: true})
}

func (s *State) Control(operation string, accepted bool, detail string) {
	if operation == "pause" && accepted {
		s.Status = StatusPaused
	}
	if operation == "cancel" && accepted {
		s.Status = StatusCancelled
	}
	status := "accepted"
	if !accepted {
		status = "rejected"
	}
	s.appendEntry(Entry{Kind: EntryStatus, Title: operation + " " + status, Content: detail, Done: true})
}

func (s *State) ApplyEnvelope(envelope protocol.EventEnvelope) ApplyResult {
	if s.RunID != "" && envelope.RunID != s.RunID {
		s.LastError = fmt.Sprintf("ignored event for run %s", envelope.RunID)
		return ApplyResult{}
	}
	if s.RunID == "" {
		s.RunID = envelope.RunID
	}
	if envelope.Sequence <= s.Cursor {
		return ApplyResult{}
	}
	if envelope.Sequence > s.Cursor+1 {
		// Never advance beyond a hole: reconnect must resume from the highest
		// contiguous sequence so the omitted durable events can still be replayed.
		return ApplyResult{Gap: true}
	}
	result := ApplyResult{Applied: true}
	s.Cursor = envelope.Sequence
	s.reduceEvent(envelope.Event)

	terminal := envelope.Event.Type == protocol.EventRunFinished || envelope.Event.Type == protocol.EventRunError
	if envelope.Durable && (s.Cursor-s.Acknowledged >= s.ackEvery || terminal) {
		ack := protocol.NewAck(s.RunID, s.Cursor)
		result.Ack = &ack
	}
	return result
}

func (s *State) reduceEvent(event protocol.AGUIEvent) {
	switch event.Type {
	case protocol.EventRunStarted:
		s.Status = StatusRunning
		if event.ThreadID != "" {
			s.ThreadID = event.ThreadID
		}
		s.appendEntry(Entry{Kind: EntryStatus, Title: "run started", Content: s.RunID, Done: true})
	case protocol.EventRunFinished:
		outcome := eventResultStatus(event.Result)
		if outcome == "cancelled" {
			s.Status = StatusCancelled
		} else {
			outcome = "completed"
			s.Status = StatusCompleted
		}
		s.appendEntry(Entry{Kind: EntryStatus, Title: "run finished", Content: outcome, Done: true})
	case protocol.EventRunError:
		s.Status = StatusFailed
		s.LastError = event.Message
		s.appendEntry(Entry{Kind: EntryError, Title: event.Code, Content: event.Message, Done: true})
	case protocol.EventStepStarted:
		s.Step = event.StepName
		s.appendEntry(Entry{Kind: EntryStatus, ID: "step:" + event.StepName, Title: "step", Content: event.StepName})
	case protocol.EventStepFinished:
		s.Step = ""
		s.updateOrAppend(Entry{Kind: EntryStatus, ID: "step:" + event.StepName, Title: "step", Content: event.StepName, Done: true})
	case protocol.EventTextMessageStart:
		s.updateOrAppend(Entry{Kind: EntryText, ID: event.MessageID, Title: roleOrAssistant(event.Role)})
	case protocol.EventTextMessageContent:
		s.appendContent(EntryText, event.MessageID, roleOrAssistant(event.Role), event.DeltaText())
	case protocol.EventTextMessageEnd:
		s.markDone(EntryText, event.MessageID)
	case protocol.EventToolCallStart:
		s.updateOrAppend(Entry{Kind: EntryTool, ID: event.ToolCallID, Title: event.ToolCallName})
	case protocol.EventToolCallArgs:
		s.appendContent(EntryTool, event.ToolCallID, "tool", event.DeltaText())
	case protocol.EventToolCallEnd:
		s.markDone(EntryTool, event.ToolCallID)
	case protocol.EventToolCallResult:
		s.appendContent(EntryTool, event.ToolCallID, "tool result", event.Content)
		s.markDone(EntryTool, event.ToolCallID)
	case protocol.EventStateSnapshot:
		s.StateSnapshot = cloneRaw(event.Snapshot, s.maxContentByte)
	case protocol.EventStateDelta:
		s.appendEntry(Entry{Kind: EntryStatus, Title: "state delta", Content: bounded(string(event.Delta), s.maxContentByte), Done: true})
	case protocol.EventCustom:
		if event.Name == "coding.run.cancelled" {
			s.Status = StatusCancelled
		}
		s.appendEntry(Entry{Kind: EntryCustom, Title: event.Name, Content: bounded(string(event.Value), s.maxContentByte), Done: true})
	}
}

func eventResultStatus(result json.RawMessage) string {
	if len(result) == 0 {
		return ""
	}
	var value struct {
		Status string `json:"status"`
	}
	if json.Unmarshal(result, &value) != nil {
		return ""
	}
	return value.Status
}

func roleOrAssistant(role string) string {
	if role == "" {
		return "assistant"
	}
	return role
}

func (s *State) appendContent(kind EntryKind, id, title, content string) {
	for index := len(s.Entries) - 1; index >= 0; index-- {
		if s.Entries[index].Kind == kind && s.Entries[index].ID == id {
			s.Entries[index].Content = bounded(s.Entries[index].Content+content, s.maxContentByte)
			return
		}
	}
	s.appendEntry(Entry{Kind: kind, ID: id, Title: title, Content: bounded(content, s.maxContentByte)})
}

func (s *State) markDone(kind EntryKind, id string) {
	for index := len(s.Entries) - 1; index >= 0; index-- {
		if s.Entries[index].Kind == kind && s.Entries[index].ID == id {
			s.Entries[index].Done = true
			return
		}
	}
}

func (s *State) updateOrAppend(entry Entry) {
	if entry.ID != "" {
		for index := len(s.Entries) - 1; index >= 0; index-- {
			if s.Entries[index].Kind == entry.Kind && s.Entries[index].ID == entry.ID {
				s.Entries[index] = entry
				return
			}
		}
	}
	s.appendEntry(entry)
}

func (s *State) appendEntry(entry Entry) {
	entry.Content = bounded(entry.Content, s.maxContentByte)
	s.Entries = append(s.Entries, entry)
	if excess := len(s.Entries) - s.maxEntries; excess > 0 {
		s.Entries = append([]Entry(nil), s.Entries[excess:]...)
	}
}

func bounded(value string, limit int) string {
	if len(value) <= limit {
		return value
	}
	const marker = "\n…[truncated]"
	keep := limit - len(marker)
	if keep < 0 {
		return marker[:limit]
	}
	for keep > 0 && !utf8.ValidString(value[:keep]) {
		keep--
	}
	return value[:keep] + marker
}

func cloneRaw(value json.RawMessage, limit int) json.RawMessage {
	if len(value) == 0 {
		return nil
	}
	return json.RawMessage(strings.Clone(bounded(string(value), limit)))
}
