package ui

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/url"
	"os/exec"
	"runtime"
	"strings"
	"unicode"

	tea "github.com/charmbracelet/bubbletea"
)

type loginDialog struct {
	id              string
	verificationURL string
	userCode        string
	status          string
	err             string
	events          <-chan loginProcessEvent
	cancel          context.CancelFunc
}

type loginProcessEvent struct {
	kind            string
	verificationURL string
	userCode        string
	err             error
}

type loginProcessStartedMsg struct {
	id     string
	events <-chan loginProcessEvent
	cancel context.CancelFunc
	err    error
}

type loginProcessEventMsg struct {
	id    string
	event loginProcessEvent
}

type loginJSONEvent struct {
	Type            string `json:"type"`
	VerificationURL string `json:"verification_url"`
	UserCode        string `json:"user_code"`
}

func (m Model) startCodexLogin(id string) tea.Cmd {
	arguments, err := m.codexArguments("login", "--no-browser", "--jsonl")
	if err != nil {
		return func() tea.Msg { return loginProcessStartedMsg{id: id, err: err} }
	}
	return func() tea.Msg {
		parent := m.config.Context
		if parent == nil {
			parent = context.Background()
		}
		processContext, cancel := context.WithCancel(parent)
		command := exec.CommandContext(processContext, m.config.AgentCLI, arguments...)
		stdout, pipeErr := command.StdoutPipe()
		if pipeErr != nil {
			cancel()
			return loginProcessStartedMsg{id: id, err: pipeErr}
		}
		var stderr bytes.Buffer
		command.Stderr = &stderr
		if startErr := command.Start(); startErr != nil {
			cancel()
			return loginProcessStartedMsg{id: id, err: startErr}
		}
		events := make(chan loginProcessEvent, 4)
		go func() {
			defer close(events)
			scanner := bufio.NewScanner(stdout)
			for scanner.Scan() {
				var payload loginJSONEvent
				if decodeErr := json.Unmarshal(scanner.Bytes(), &payload); decodeErr != nil {
					events <- loginProcessEvent{kind: "error", err: fmt.Errorf("invalid login event: %w", decodeErr)}
					continue
				}
				events <- loginProcessEvent{
					kind: payload.Type, verificationURL: payload.VerificationURL, userCode: payload.UserCode,
				}
			}
			waitErr := command.Wait()
			if scanErr := scanner.Err(); scanErr != nil {
				events <- loginProcessEvent{kind: "error", err: scanErr}
				return
			}
			if waitErr != nil {
				detail := strings.TrimSpace(stderr.String())
				if detail == "" {
					detail = waitErr.Error()
				}
				events <- loginProcessEvent{kind: "error", err: fmt.Errorf("%s", detail)}
			}
		}()
		return loginProcessStartedMsg{id: id, events: events, cancel: cancel}
	}
}

func waitLoginEvent(id string, events <-chan loginProcessEvent) tea.Cmd {
	return func() tea.Msg {
		event, ok := <-events
		if !ok {
			return loginProcessEventMsg{id: id, event: loginProcessEvent{kind: "closed"}}
		}
		return loginProcessEventMsg{id: id, event: event}
	}
}

func openLoginURL(url string) tea.Cmd {
	if runtime.GOOS != "darwin" || !safeLoginURL(url) {
		return nil
	}
	return func() tea.Msg {
		_ = exec.Command("open", url).Run()
		return nil
	}
}

func safeLoginURL(value string) bool {
	parsed, err := url.Parse(value)
	return err == nil && parsed.Scheme == "https" && parsed.Hostname() == "auth.openai.com"
}

func safeUserCode(value string) bool {
	if len(value) < 4 || len(value) > 32 {
		return false
	}
	for _, character := range value {
		if character != '-' && !unicode.IsUpper(character) && !unicode.IsDigit(character) {
			return false
		}
	}
	return true
}

func (m Model) renderLoginDialog(width int) []string {
	dialog := m.loginDialog
	if dialog == nil {
		return nil
	}
	lines := []string{
		horizontalBorder(width),
		titleStyle.Render(" Login to ChatGPT / OpenAI Codex"),
		"",
	}
	if dialog.verificationURL != "" {
		link := "\x1b]8;;" + dialog.verificationURL + "\x07" + dialog.verificationURL + "\x1b]8;;\x07"
		lines = append(lines,
			" "+accentStyle.Render(link),
			" "+dimStyle.Render("Cmd+click to open"),
			"",
			" "+warningStyle.Render("Enter code: "+dialog.userCode),
			"",
		)
	}
	if dialog.err != "" {
		lines = append(lines, " "+errorStyle.Render(dialog.err))
	} else {
		lines = append(lines, " "+dimStyle.Render(dialog.status))
	}
	lines = append(lines,
		"", dimStyle.Render(" Esc cancel"), horizontalBorder(width),
		dimStyle.Render(m.statusLine(width)),
	)
	return lines
}
