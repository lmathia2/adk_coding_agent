package ui

import "github.com/charmbracelet/lipgloss"

var (
	accentColor  = lipgloss.Color("#5FD7D7")
	borderColor  = lipgloss.Color("#4C7DFF")
	dimColor     = lipgloss.Color("#6C7086")
	mutedColor   = lipgloss.Color("#8A8FA3")
	warningColor = lipgloss.Color("#E5C07B")
	errorColor   = lipgloss.Color("#F38BA8")
	successColor = lipgloss.Color("#A6E3A1")
	toolColor    = lipgloss.Color("#181B24")
	userColor    = lipgloss.Color("#242735")

	accentStyle  = lipgloss.NewStyle().Foreground(accentColor)
	dimStyle     = lipgloss.NewStyle().Foreground(dimColor)
	mutedStyle   = lipgloss.NewStyle().Foreground(mutedColor)
	warningStyle = lipgloss.NewStyle().Foreground(warningColor)
	errorStyle   = lipgloss.NewStyle().Foreground(errorColor)
	successStyle = lipgloss.NewStyle().Foreground(successColor)
	titleStyle   = lipgloss.NewStyle().Foreground(accentColor).Bold(true)
	toolStyle    = lipgloss.NewStyle().Background(toolColor).Padding(0, 1)
	userStyle    = lipgloss.NewStyle().Background(userColor).Padding(0, 1)
)

func horizontalBorder(width int) string {
	return lipgloss.NewStyle().Foreground(borderColor).Render(repeatRune('─', width))
}

func repeatRune(value rune, count int) string {
	if count < 1 {
		count = 1
	}
	result := make([]rune, count)
	for index := range result {
		result[index] = value
	}
	return string(result)
}
