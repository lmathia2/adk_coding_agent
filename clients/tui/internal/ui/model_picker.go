package ui

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

type codexCatalogModel struct {
	ID            string  `json:"id"`
	DisplayName   string  `json:"display_name"`
	ClientVersion *string `json:"client_version"`
}

type codexCatalog struct {
	Models        []codexCatalogModel `json:"models"`
	SelectedModel string              `json:"selected_model"`
}

type modelCatalogLoadedMsg struct {
	models        []codexCatalogModel
	selectedModel string
	err           error
}

type modelSelectionFinishedMsg struct {
	model  string
	output string
	err    error
}

type modelPicker struct {
	loading      bool
	models       []codexCatalogModel
	query        []rune
	selected     int
	saving       bool
	defaultModel string
	err          string
}

func newModelPicker() *modelPicker {
	return &modelPicker{loading: true}
}

func (picker *modelPicker) filtered() []codexCatalogModel {
	query := strings.ToLower(strings.TrimSpace(string(picker.query)))
	if query == "" {
		return picker.models
	}
	var matches []codexCatalogModel
	for _, model := range picker.models {
		haystack := strings.ToLower(model.ID + " " + model.DisplayName + " openai-codex")
		if fuzzyContains(haystack, query) {
			matches = append(matches, model)
		}
	}
	return matches
}

func fuzzyContains(value, query string) bool {
	if strings.Contains(value, query) {
		return true
	}
	valueRunes := []rune(value)
	queryRunes := []rune(query)
	position := 0
	for _, character := range valueRunes {
		if position < len(queryRunes) && character == queryRunes[position] {
			position++
		}
	}
	return position == len(queryRunes)
}

func (picker *modelPicker) clamp() {
	count := len(picker.filtered())
	if count == 0 {
		picker.selected = 0
	} else if picker.selected >= count {
		picker.selected = count - 1
	}
}

func (m Model) loadCodexCatalog() tea.Cmd {
	values, err := m.codexArguments("models")
	if err != nil {
		return func() tea.Msg { return modelCatalogLoadedMsg{err: err} }
	}
	return func() tea.Msg {
		output, commandErr := exec.Command(m.config.AgentCLI, values...).CombinedOutput()
		if commandErr != nil {
			detail := strings.TrimSpace(string(output))
			if detail == "" {
				detail = commandErr.Error()
			}
			return modelCatalogLoadedMsg{err: fmt.Errorf("%s", detail)}
		}
		var catalog codexCatalog
		if err := json.Unmarshal(output, &catalog); err != nil {
			return modelCatalogLoadedMsg{err: fmt.Errorf("invalid model catalog: %w", err)}
		}
		return modelCatalogLoadedMsg{
			models: catalog.Models, selectedModel: catalog.SelectedModel,
		}
	}
}

func (m Model) selectCodexModel(model string) tea.Cmd {
	values, err := m.codexArguments("select", model)
	if err != nil {
		return func() tea.Msg { return modelSelectionFinishedMsg{model: model, err: err} }
	}
	return func() tea.Msg {
		output, commandErr := exec.Command(m.config.AgentCLI, values...).CombinedOutput()
		return modelSelectionFinishedMsg{
			model: model, output: strings.TrimSpace(string(output)), err: commandErr,
		}
	}
}

func (m Model) updateModelPicker(key tea.KeyMsg) (tea.Model, tea.Cmd) {
	picker := m.modelPicker
	if picker == nil {
		return m, nil
	}
	if picker.saving {
		if key.Type == tea.KeyEsc {
			m.modelPicker = nil
		}
		return m, nil
	}
	switch key.Type {
	case tea.KeyEsc, tea.KeyCtrlC:
		m.modelPicker = nil
		return m, nil
	case tea.KeyUp:
		count := len(picker.filtered())
		if count > 0 {
			picker.selected = (picker.selected - 1 + count) % count
		}
	case tea.KeyDown:
		count := len(picker.filtered())
		if count > 0 {
			picker.selected = (picker.selected + 1) % count
		}
	case tea.KeyBackspace, tea.KeyDelete:
		if len(picker.query) > 0 {
			picker.query = picker.query[:len(picker.query)-1]
			picker.selected = 0
		}
	case tea.KeySpace:
		picker.query = append(picker.query, ' ')
		picker.selected = 0
	case tea.KeyRunes:
		picker.query = append(picker.query, key.Runes...)
		picker.selected = 0
	case tea.KeyEnter, tea.KeyCtrlS:
		models := picker.filtered()
		if len(models) == 0 || picker.loading || picker.err != "" {
			return m, nil
		}
		picker.saving = true
		return m, m.selectCodexModel(models[picker.selected].ID)
	}
	picker.clamp()
	return m, nil
}

func (m Model) renderModelPicker(width int) []string {
	picker := m.modelPicker
	if picker == nil {
		return nil
	}
	lines := []string{
		horizontalBorder(width),
		warningStyle.Render("Only showing models from configured providers. Use /login to add providers."),
		"",
		accentStyle.Render("› ") + string(picker.query) + "█",
	}
	if picker.loading {
		lines = append(lines, "", dimStyle.Render("Loading model catalog…"))
	} else if picker.err != "" {
		lines = append(lines, "", errorStyle.Render("Model catalog failed: "+picker.err))
	} else {
		models := picker.filtered()
		if len(models) == 0 {
			lines = append(lines, "", dimStyle.Render("No matching models"))
		} else {
			start := 0
			const pageSize = 10
			if picker.selected >= pageSize {
				start = picker.selected - pageSize + 1
			}
			end := min(len(models), start+pageSize)
			for index := start; index < end; index++ {
				model := models[index]
				prefix := "  "
				if index == picker.selected {
					prefix = accentStyle.Render("› ")
				}
				badges := []string{"[openai-codex]"}
				if m.session.CodingModel != nil && model.ID == m.session.CodingModel.Name {
					badges = append(badges, "current")
				}
				if model.ID == picker.defaultModel {
					badges = append(badges, "default ✓")
				}
				line := prefix + model.ID + " " + dimStyle.Render(strings.Join(badges, " · "))
				lines = append(lines, clip(line, width))
			}
			selected := models[picker.selected]
			lines = append(lines, "", mutedStyle.Render("Model Name: ")+selected.DisplayName)
		}
		if picker.saving {
			lines = append(lines, "", warningStyle.Render("Saving model selection…"))
		} else {
			lines = append(lines, "", successStyle.Render("Model catalogs refreshed."))
		}
	}
	lines = append(lines,
		"",
		dimStyle.Render("Enter select for next server · Ctrl+S set as default · Esc cancel"),
		horizontalBorder(width),
		dimStyle.Render(m.statusLine(width)),
	)
	return lines
}

func currentModelFirst(models []codexCatalogModel, current string) []codexCatalogModel {
	if current == "" {
		return models
	}
	result := append([]codexCatalogModel(nil), models...)
	for index := range result {
		if result[index].ID == current {
			selected := result[index]
			copy(result[1:index+1], result[0:index])
			result[0] = selected
			break
		}
	}
	return result
}
