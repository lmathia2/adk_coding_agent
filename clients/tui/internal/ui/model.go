// Package ui implements a Bubble Tea presentation over the public protocol client.
package ui

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/session"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
)

type Config struct {
	InitialInput     string
	InitialRunID     string
	InitialCursor    int64
	ThreadID         string
	Metadata         map[string]string
	Heartbeat        time.Duration
	HeartbeatTimeout time.Duration
	History          int
	ContentBytes     int
	AckEvery         int64
	ID               func() string
}

type Model struct {
	transport *ws.Client
	session   session.State
	config    Config

	connected       bool
	negotiated      bool
	harness         protocol.HarnessDescriptor
	input           []rune
	width           int
	height          int
	warning         string
	pending         *protocol.StartTask
	pendingControls []pendingControl
	pingCount       uint64
	outstandingPing string
}

type pendingControl struct {
	commandID string
	message   protocol.ClientMessage
}

type transportMsg ws.Event
type transportClosedMsg struct{}
type heartbeatMsg time.Time
type heartbeatTimeoutMsg string
type sendResultMsg struct{ err error }

func New(transport *ws.Client, config Config) Model {
	if config.Heartbeat <= 0 {
		config.Heartbeat = 20 * time.Second
	}
	if config.HeartbeatTimeout <= 0 {
		config.HeartbeatTimeout = 45 * time.Second
	}
	if config.ID == nil {
		config.ID = randomID
	}
	state := session.New(config.History, config.ContentBytes, config.AckEvery)
	if config.InitialRunID != "" {
		state.RunID = config.InitialRunID
		state.Cursor = config.InitialCursor
	}
	model := Model{transport: transport, session: state, config: config}
	if strings.TrimSpace(config.InitialInput) != "" {
		start := model.newStart(config.InitialInput)
		model.pending = &start
	}
	return model
}

func (m Model) Init() tea.Cmd {
	return tea.Batch(waitTransport(m.transport.Events()), heartbeat(m.config.Heartbeat))
}

func (m Model) Update(message tea.Msg) (tea.Model, tea.Cmd) {
	switch message := message.(type) {
	case tea.WindowSizeMsg:
		m.width, m.height = message.Width, message.Height
		return m, nil
	case transportClosedMsg:
		m.connected, m.negotiated = false, false
		m.warning = "transport stopped"
		return m, nil
	case transportMsg:
		command := m.handleTransport(ws.Event(message))
		return m, tea.Batch(command, waitTransport(m.transport.Events()))
	case heartbeatMsg:
		commands := []tea.Cmd{heartbeat(m.config.Heartbeat)}
		if m.negotiated && m.outstandingPing == "" {
			m.pingCount++
			m.outstandingPing = fmt.Sprintf("tui-%d", m.pingCount)
			commands = append(commands,
				m.send(protocol.NewPing(m.outstandingPing)),
				heartbeatTimeout(m.config.HeartbeatTimeout, m.outstandingPing),
			)
		}
		return m, tea.Batch(commands...)
	case heartbeatTimeoutMsg:
		if m.negotiated && m.outstandingPing == string(message) {
			m.connected, m.negotiated = false, false
			m.warning = "heartbeat timed out; reconnecting"
			m.outstandingPing = ""
			m.transport.Reconnect()
		}
		return m, nil
	case sendResultMsg:
		if message.err != nil {
			m.warning = message.err.Error()
		}
		return m, nil
	case tea.KeyMsg:
		switch message.Type {
		case tea.KeyCtrlD:
			return m, tea.Quit
		case tea.KeyCtrlC:
			if m.session.RunID != "" && !terminalStatus(m.session.Status) {
				return m.cancel()
			}
			return m, tea.Quit
		case tea.KeyEnter:
			return m.submit()
		case tea.KeyBackspace, tea.KeyDelete:
			if len(m.input) > 0 {
				m.input = m.input[:len(m.input)-1]
			}
		case tea.KeyRunes:
			m.input = append(m.input, message.Runes...)
		case tea.KeySpace:
			m.input = append(m.input, ' ')
		}
	}
	return m, nil
}

func (m *Model) handleTransport(event ws.Event) tea.Cmd {
	switch event.Kind {
	case ws.Connected:
		m.connected = true
		m.negotiated = false
		m.warning = "negotiating protocol"
	case ws.Disconnected:
		m.connected = false
		m.negotiated = false
		m.outstandingPing = ""
		if event.Err != nil {
			m.warning = "reconnecting: " + event.Err.Error()
		}
	case ws.Warning:
		if event.Err != nil {
			m.warning = event.Err.Error()
		}
	case ws.Message:
		return m.handleProtocol(event.Message)
	}
	return nil
}

func (m *Model) handleProtocol(message protocol.ServerMessage) tea.Cmd {
	switch {
	case message.Hello != nil:
		m.negotiated = true
		m.harness = message.Hello.Harness
		m.warning = ""
		var messages []protocol.ClientMessage
		if m.pending != nil {
			messages = append(messages, *m.pending)
		} else if attach, ok := m.session.ResumeMessage(); ok {
			messages = append(messages, attach)
			if m.session.Cursor > 0 {
				messages = append(messages, protocol.NewAck(m.session.RunID, m.session.Cursor))
			}
			for _, control := range m.pendingControls {
				messages = append(messages, control.message)
			}
		}
		return m.sendMany(messages...)
	case message.TaskAccepted != nil:
		m.session.AcceptTask(*message.TaskAccepted)
		m.pending = nil
	case message.ControlResult != nil:
		m.confirmControl(message.ControlResult.CommandID)
		m.session.Control(message.ControlResult.Operation, message.ControlResult.Accepted, message.ControlResult.Detail)
	case message.Envelope != nil:
		result := m.session.ApplyEnvelope(*message.Envelope)
		if result.Gap {
			m.connected, m.negotiated = false, false
			m.warning = fmt.Sprintf("event sequence gap before %d; reconnecting to replay", message.Envelope.Sequence)
			m.transport.Reconnect()
			return nil
		}
		if result.Ack != nil {
			m.session.MarkAcknowledged(result.Ack.ThroughSequence)
			return m.send(*result.Ack)
		}
	case message.Pong != nil:
		m.session.LastPongNonce = message.Pong.Nonce
		if message.Pong.Nonce == m.outstandingPing {
			m.outstandingPing = ""
		}
	case message.Error != nil:
		m.session.Fail(message.Error.Code + ": " + message.Error.Message)
		m.warning = message.Error.Message
	}
	return nil
}

func (m Model) View() string {
	width := m.width
	if width < 20 {
		width = 80
	}
	height := m.height
	if height < 8 {
		height = 24
	}
	var output []string
	connection := "disconnected"
	if m.connected {
		connection = "connected"
	}
	if m.negotiated {
		connection = "ready"
	}
	harness := m.harness.DisplayName
	if harness == "" {
		harness = "waiting for server"
	}
	header := fmt.Sprintf("adk-agent  %s  harness=%s  run=%s  status=%s  seq=%d",
		connection, harness, valueOr(m.session.RunID, "-"), m.session.Status, m.session.Cursor)
	output = append(output,
		clip(header, width),
		clip(codingModelLine(m.session), width),
		strings.Repeat("─", min(width, utf8.RuneCountInString(header)+8)),
	)

	for _, entry := range m.session.Entries {
		marker := entryMarker(entry.Kind)
		title := strings.TrimSpace(entry.Title)
		content := strings.TrimSpace(entry.Content)
		line := marker + " " + title
		if content != "" {
			line += ": " + content
		}
		output = append(output, wrap(line, width)...)
	}
	if m.warning != "" {
		output = append(output, clip("! "+m.warning, width))
	}

	footerLines := 3
	available := height - footerLines
	const fixedHeaderLines = 3
	if available < fixedHeaderLines {
		available = fixedHeaderLines
	}
	if len(output) > available {
		output = append(output[:fixedHeaderLines], output[len(output)-(available-fixedHeaderLines):]...)
	}
	prompt := "start> "
	if m.session.RunID != "" && !terminalStatus(m.session.Status) {
		prompt = "steer> "
	}
	output = append(output,
		strings.Repeat("─", width),
		clip(prompt+string(m.input)+"█", width),
		clip("enter send · /start /attach /pause /cancel /reconnect /help · ctrl+d quit", width),
	)
	return strings.Join(output, "\n")
}

func codingModelLine(state session.State) string {
	if state.RunID == "" {
		return "coding-model  waiting for task"
	}
	if state.Status == session.StatusStarting {
		return "coding-model  initializing"
	}
	if state.CodingModel == nil {
		return "coding-model  unknown (server did not report)"
	}
	return fmt.Sprintf("coding-model  %s/%s  readiness=%s",
		state.CodingModel.Provider, state.CodingModel.Name, state.CodingModel.Readiness)
}

func (m Model) submit() (tea.Model, tea.Cmd) {
	input := strings.TrimSpace(string(m.input))
	m.input = nil
	if input == "" {
		return m, nil
	}
	if strings.HasPrefix(input, "/") {
		return m.command(input)
	}
	if !m.negotiated {
		m.warning = "server is not ready; input was not sent"
		return m, nil
	}
	if m.session.RunID != "" && !terminalStatus(m.session.Status) {
		if len([]byte(input)) > 4096 {
			m.warning = "steering input exceeds 4096 UTF-8 bytes"
			return m, nil
		}
		commandID := m.config.ID()
		message := protocol.NewSteerTask(m.session.RunID, input, commandID, 0)
		m.session.Notice("steering queued", input)
		return m.queueControl(commandID, message)
	}
	return m.beginStart(input)
}

func (m Model) command(input string) (tea.Model, tea.Cmd) {
	name, argument, _ := strings.Cut(input, " ")
	argument = strings.TrimSpace(argument)
	switch name {
	case "/quit", "/q":
		return m, tea.Quit
	case "/help":
		m.session.Notice("commands", "/start PROMPT · /attach RUN [CURSOR] · /pause · /cancel · /reconnect · /quit")
		return m, nil
	case "/reconnect":
		m.connected, m.negotiated = false, false
		m.outstandingPing = ""
		m.warning = "reconnecting"
		m.transport.Reconnect()
		return m, nil
	case "/start":
		if argument == "" {
			m.warning = "/start requires a prompt"
			return m, nil
		}
		if !m.negotiated {
			m.warning = "server is not ready"
			return m, nil
		}
		return m.beginStart(argument)
	case "/attach":
		parts := strings.Fields(argument)
		if len(parts) == 0 || len(parts) > 2 {
			m.warning = "usage: /attach RUN_ID [AFTER_SEQUENCE]"
			return m, nil
		}
		cursor := int64(0)
		if len(parts) == 2 {
			parsed, err := strconv.ParseInt(parts[1], 10, 64)
			if err != nil || parsed < 0 {
				m.warning = "attach cursor must be a nonnegative integer"
				return m, nil
			}
			cursor = parsed
		}
		hadRun := m.session.RunID != ""
		m.pending = nil
		m.pendingControls = nil
		m.session = session.New(m.config.History, m.config.ContentBytes, m.config.AckEvery)
		m.session.RunID, m.session.Cursor = parts[0], cursor
		if hadRun {
			m.connected, m.negotiated = false, false
			m.warning = "reconnecting to attach another run"
			m.transport.Reconnect()
			return m, nil
		}
		return m, m.send(protocol.NewAttachTask(parts[0], cursor))
	case "/pause":
		if m.session.RunID == "" {
			m.warning = "no active run"
			return m, nil
		}
		commandID := m.config.ID()
		return m.queueControl(commandID, protocol.NewPauseTask(m.session.RunID, commandID))
	case "/cancel":
		return m.cancel()
	default:
		m.warning = "unknown command " + name
		return m, nil
	}
}

func (m Model) beginStart(input string) (tea.Model, tea.Cmd) {
	if len([]byte(input)) > 50_000 {
		m.warning = "task input exceeds 50000 UTF-8 bytes"
		return m, nil
	}
	hadRun := m.session.RunID != ""
	m.session = session.New(m.config.History, m.config.ContentBytes, m.config.AckEvery)
	m.pendingControls = nil
	start := m.newStart(input)
	m.pending = &start
	if hadRun {
		m.connected, m.negotiated = false, false
		m.warning = "reconnecting to start another run"
		m.transport.Reconnect()
		return m, nil
	}
	return m, m.send(start)
}

func (m Model) cancel() (tea.Model, tea.Cmd) {
	if m.session.RunID == "" {
		return m, func() tea.Msg { return sendResultMsg{err: fmt.Errorf("no active run")} }
	}
	commandID := m.config.ID()
	return m.queueControl(commandID, protocol.NewCancelTask(m.session.RunID, commandID))
}

func (m Model) queueControl(commandID string, message protocol.ClientMessage) (tea.Model, tea.Cmd) {
	m.pendingControls = append(m.pendingControls, pendingControl{commandID: commandID, message: message})
	return m, m.send(message)
}

func (m *Model) confirmControl(commandID string) {
	for index := range m.pendingControls {
		if m.pendingControls[index].commandID == commandID {
			m.pendingControls = append(m.pendingControls[:index], m.pendingControls[index+1:]...)
			return
		}
	}
}

func (m Model) newStart(input string) protocol.StartTask {
	id := m.config.ID()
	return protocol.NewStartTask("request-"+id, "start-"+id, input, m.config.ThreadID, cloneMetadata(m.config.Metadata))
}

func (m Model) send(message protocol.ClientMessage) tea.Cmd {
	return m.sendMany(message)
}

func (m Model) sendMany(messages ...protocol.ClientMessage) tea.Cmd {
	if len(messages) == 0 {
		return nil
	}
	return func() tea.Msg {
		for _, message := range messages {
			if err := m.transport.Send(message); err != nil {
				m.transport.Reconnect()
				return sendResultMsg{err: err}
			}
		}
		return sendResultMsg{}
	}
}

func waitTransport(events <-chan ws.Event) tea.Cmd {
	return func() tea.Msg {
		event, ok := <-events
		if !ok {
			return transportClosedMsg{}
		}
		return transportMsg(event)
	}
}

func heartbeat(interval time.Duration) tea.Cmd {
	return tea.Tick(interval, func(now time.Time) tea.Msg { return heartbeatMsg(now) })
}

func heartbeatTimeout(timeout time.Duration, nonce string) tea.Cmd {
	return tea.Tick(timeout, func(time.Time) tea.Msg { return heartbeatTimeoutMsg(nonce) })
}

func randomID() string {
	var value [12]byte
	if _, err := rand.Read(value[:]); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(value[:])
}

func cloneMetadata(input map[string]string) map[string]string {
	if len(input) == 0 {
		return nil
	}
	output := make(map[string]string, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}

func SortedMetadata(metadata map[string]string) []string {
	keys := make([]string, 0, len(metadata))
	for key := range metadata {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	return keys
}

func entryMarker(kind session.EntryKind) string {
	switch kind {
	case session.EntryText:
		return "◆"
	case session.EntryTool:
		return "⚙"
	case session.EntryError:
		return "✗"
	case session.EntryCustom:
		return "◇"
	default:
		return "·"
	}
}

func clip(value string, width int) string {
	runes := []rune(strings.ReplaceAll(value, "\n", " ↵ "))
	if len(runes) <= width {
		return string(runes)
	}
	if width <= 1 {
		return "…"
	}
	return string(runes[:width-1]) + "…"
}

func wrap(value string, width int) []string {
	value = strings.ReplaceAll(value, "\t", "  ")
	var output []string
	for _, sourceLine := range strings.Split(value, "\n") {
		runes := []rune(sourceLine)
		if len(runes) == 0 {
			output = append(output, "")
			continue
		}
		for len(runes) > width {
			output = append(output, string(runes[:width]))
			runes = runes[width:]
		}
		output = append(output, string(runes))
	}
	return output
}

func valueOr(value, fallback string) string {
	if value == "" {
		return fallback
	}
	return value
}

func min(left, right int) int {
	if left < right {
		return left
	}
	return right
}

func terminalStatus(status session.RunStatus) bool {
	return status == session.StatusCompleted || status == session.StatusFailed || status == session.StatusCancelled
}
