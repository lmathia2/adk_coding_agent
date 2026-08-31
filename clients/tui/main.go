package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/ui"
)

const version = "0.1.0"

type metadataFlag map[string]string

func (values metadataFlag) String() string {
	keys := ui.SortedMetadata(values)
	items := make([]string, 0, len(keys))
	for _, key := range keys {
		items = append(items, key+"="+values[key])
	}
	return strings.Join(items, ",")
}

func (values metadataFlag) Set(value string) error {
	key, content, ok := strings.Cut(value, "=")
	key = strings.TrimSpace(key)
	if !ok || key == "" {
		return errors.New("metadata must use KEY=VALUE")
	}
	values[key] = content
	return nil
}

func main() {
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, "adk-agent-tui:", err)
		os.Exit(1)
	}
}

func run() error {
	metadata := metadataFlag{}
	serverURL := flag.String("server", "ws://127.0.0.1:8765/v1/agent", "agent WebSocket URL")
	clientName := flag.String("client-name", "adk-agent-tui/"+version, "protocol client name")
	initialRun := flag.String("run", "", "existing run ID to attach")
	initialCursor := flag.Int64("after", 0, "last applied durable sequence when attaching")
	initialInput := flag.String("input", "", "task prompt to start after negotiation")
	threadID := flag.String("thread", "", "optional thread ID for a new task")
	stateRoot := flag.String("state-root", defaultStateRoot(), "shared harness state root")
	agentCLI := flag.String("agent-cli", "adk-coding-agent", "coding-agent management CLI")
	heartbeat := flag.Duration("heartbeat", 20*time.Second, "application heartbeat interval")
	heartbeatTimeout := flag.Duration("heartbeat-timeout", 45*time.Second, "maximum wait for a heartbeat response")
	token := flag.String("token", os.Getenv("ADK_CODING_AGENT_TOKEN"), "server bearer token (or ADK_CODING_AGENT_TOKEN)")
	reconnectMin := flag.Duration("reconnect-min", 250*time.Millisecond, "minimum reconnect delay")
	reconnectMax := flag.Duration("reconnect-max", 10*time.Second, "maximum reconnect delay")
	inboundBuffer := flag.Int("inbound-buffer", 128, "bounded inbound event buffer")
	outboundBuffer := flag.Int("outbound-buffer", 64, "bounded outbound control buffer")
	maxMessageBytes := flag.Int64("max-message-bytes", 1<<20, "maximum inbound WebSocket message size")
	history := flag.Int("history", 400, "maximum rendered event entries")
	contentBytes := flag.Int("content-bytes", 64<<10, "maximum retained bytes per entry")
	ackEvery := flag.Int64("ack-every", 16, "acknowledge after this many durable events")
	noAltScreen := flag.Bool("no-alt-screen", false, "render without the terminal alternate screen")
	showVersion := flag.Bool("version", false, "print version and exit")
	flag.Var(metadata, "metadata", "task metadata KEY=VALUE (repeatable)")
	flag.Parse()

	if *showVersion {
		fmt.Println(version)
		return nil
	}
	if *initialCursor < 0 {
		return errors.New("--after must be nonnegative")
	}
	if *initialCursor > 0 && *initialRun == "" {
		return errors.New("--after requires --run")
	}
	if *initialRun != "" && strings.TrimSpace(*initialInput) != "" {
		return errors.New("--run and --input are mutually exclusive")
	}
	if *reconnectMin <= 0 || *reconnectMax < *reconnectMin {
		return errors.New("reconnect durations must satisfy 0 < min <= max")
	}
	if *heartbeat <= 0 || *heartbeatTimeout <= 0 || *inboundBuffer < 1 || *outboundBuffer < 1 || *maxMessageBytes < 1 || *history < 1 || *contentBytes < 1 || *ackEvery < 1 {
		return errors.New("heartbeat, buffers, message limits, history, and ack interval must be positive")
	}
	headers, err := bearerHeaders(*token)
	if err != nil {
		return err
	}
	prompt := strings.TrimSpace(*initialInput)
	if prompt == "" && flag.NArg() > 0 {
		prompt = strings.Join(flag.Args(), " ")
	}
	if len([]byte(prompt)) > 50_000 {
		return errors.New("task input exceeds 50000 UTF-8 bytes")
	}

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	transport := ws.New(ws.Config{
		URL:             *serverURL,
		ClientName:      *clientName,
		Headers:         headers,
		InboundBuffer:   *inboundBuffer,
		OutboundBuffer:  *outboundBuffer,
		MaxMessageBytes: *maxMessageBytes,
		ReconnectMin:    *reconnectMin,
		ReconnectMax:    *reconnectMax,
	})
	go transport.Run(ctx)

	model := ui.New(transport, ui.Config{
		InitialInput:     prompt,
		InitialRunID:     *initialRun,
		InitialCursor:    *initialCursor,
		ThreadID:         *threadID,
		Metadata:         metadata,
		Heartbeat:        *heartbeat,
		HeartbeatTimeout: *heartbeatTimeout,
		History:          *history,
		ContentBytes:     *contentBytes,
		AckEvery:         *ackEvery,
		StateRoot:        *stateRoot,
		AgentCLI:         *agentCLI,
	})
	options := []tea.ProgramOption{tea.WithContext(ctx)}
	if !*noAltScreen {
		options = append(options, tea.WithAltScreen())
	}
	_, err = tea.NewProgram(model, options...).Run()
	cancel()
	return err
}

func defaultStateRoot() string {
	if configured := strings.TrimSpace(os.Getenv("ADK_CODING_AGENT_STATE_ROOT")); configured != "" {
		return configured
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return ""
	}
	return home + "/.local/state/adk-coding-agent"
}

func bearerHeaders(token string) (http.Header, error) {
	token = strings.TrimSpace(token)
	if len([]byte(token)) < 32 {
		return nil, errors.New("--token or ADK_CODING_AGENT_TOKEN must contain at least 32 UTF-8 bytes")
	}
	return http.Header{"Authorization": []string{"Bearer " + token}}, nil
}
