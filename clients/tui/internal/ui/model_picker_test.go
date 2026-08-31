package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
	ws "github.com/lmathia2/adk_coding_agent/clients/tui/internal/socket"
)

func TestModelCommandOpensSearchableInlinePicker(t *testing.T) {
	t.Parallel()
	temporary := t.TempDir()
	cli := filepath.Join(temporary, "fake-agent-cli")
	script := `#!/bin/sh
case "$*" in
  *" models") printf '%s\n' '{"models":[{"id":"gpt-slow","display_name":"GPT Slow"},{"id":"gpt-fast","display_name":"GPT Fast"}],"selected_model":"gpt-fast"}' ;;
  *" select "*) printf '%s\n' '{"restart_required":true}' ;;
  *) exit 2 ;;
esac
`
	if err := os.WriteFile(cli, []byte(script), 0o700); err != nil {
		t.Fatal(err)
	}
	model := New(ws.New(ws.Config{OutboundBuffer: 8}), Config{
		History: 10, ContentBytes: 4096, AckEvery: 5,
		StateRoot: filepath.Join(temporary, "state with spaces"), AgentCLI: cli,
	})
	model.width, model.height = 100, 30
	model.session.CodingModel = &protocol.CodingModelStatus{
		Provider: "openai_codex", Name: "gpt-fast", Readiness: protocol.ModelResponding,
	}

	next, command := model.command("/model")
	model = next.(Model)
	if model.modelPicker == nil || command == nil {
		t.Fatal("/model did not open and load an inline picker")
	}
	loaded, ok := command().(modelCatalogLoadedMsg)
	if !ok || loaded.err != nil {
		t.Fatalf("catalog result = %#v", loaded)
	}
	next, _ = model.Update(loaded)
	model = next.(Model)
	view := model.View()
	for _, expected := range []string{
		"Only showing models from configured providers",
		"gpt-fast", "[openai-codex]", "current", "default ✓",
		"Model catalogs refreshed", "Enter select for next server", "Esc cancel",
	} {
		if !strings.Contains(view, expected) {
			t.Fatalf("picker view missing %q:\n%s", expected, view)
		}
	}

	next, _ = model.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("slow")})
	model = next.(Model)
	if matches := model.modelPicker.filtered(); len(matches) != 1 || matches[0].ID != "gpt-slow" {
		t.Fatalf("filtered models = %#v", matches)
	}
	next, command = model.Update(tea.KeyMsg{Type: tea.KeyEnter})
	model = next.(Model)
	if command == nil || !model.modelPicker.saving {
		t.Fatal("enter did not start deterministic model selection")
	}
	selected, ok := command().(modelSelectionFinishedMsg)
	if !ok || selected.err != nil || selected.model != "gpt-slow" {
		t.Fatalf("selection result = %#v", selected)
	}
	next, _ = model.Update(selected)
	model = next.(Model)
	if model.modelPicker != nil || !strings.Contains(model.warning, "restart the server") {
		t.Fatalf("picker=%v warning=%q", model.modelPicker != nil, model.warning)
	}
}

func TestModelPickerWrapsAndEscapeRestoresComposer(t *testing.T) {
	t.Parallel()
	model := New(ws.New(ws.Config{OutboundBuffer: 8}), Config{History: 10, ContentBytes: 1024, AckEvery: 5})
	model.modelPicker = &modelPicker{models: []codexCatalogModel{{ID: "a"}, {ID: "b"}}}

	next, _ := model.Update(tea.KeyMsg{Type: tea.KeyUp})
	model = next.(Model)
	if model.modelPicker.selected != 1 {
		t.Fatalf("up did not wrap: %d", model.modelPicker.selected)
	}
	next, _ = model.Update(tea.KeyMsg{Type: tea.KeyEsc})
	model = next.(Model)
	if model.modelPicker != nil || !strings.Contains(model.View(), "enter send") {
		t.Fatal("escape did not restore the main composer")
	}
}

func TestComposerEditsAtCursorAndShowsPiStyleShell(t *testing.T) {
	t.Parallel()
	model := New(ws.New(ws.Config{OutboundBuffer: 8}), Config{
		AppVersion: "test", History: 10, ContentBytes: 1024, AckEvery: 5,
	})
	model.width, model.height = 100, 30
	model.input = []rune("ac")
	model.inputCursor = 1
	next, _ := model.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune("b")})
	model = next.(Model)
	if got := string(model.input); got != "abc" {
		t.Fatalf("input = %q", got)
	}
	view := model.View()
	for _, expected := range []string{"adk-agent vtest", "ctrl+o more", "› ab█c", "coding-model  waiting for server"} {
		if !strings.Contains(view, expected) {
			t.Fatalf("shell missing %q:\n%s", expected, view)
		}
	}
}
