package ui

import "strings"

type commandSpec struct {
	name        string
	description string
	takesInput  bool
}

var commandCatalog = []commandSpec{
	{name: "/login", description: "Authenticate a ChatGPT subscription"},
	{name: "/logout", description: "Remove the saved subscription login"},
	{name: "/auth", description: "Show redacted authentication status"},
	{name: "/model", description: "Search and select a coding model"},
	{name: "/models", description: "Print the available model catalog"},
	{name: "/benchmark", description: "Find the fastest available model", takesInput: true},
	{name: "/start", description: "Start a new task", takesInput: true},
	{name: "/attach", description: "Attach to a durable run", takesInput: true},
	{name: "/pause", description: "Pause the active run"},
	{name: "/cancel", description: "Cancel the active run"},
	{name: "/reconnect", description: "Reconnect and replay durable events"},
	{name: "/clear", description: "Clear the local transcript"},
	{name: "/help", description: "Show command and keyboard help"},
	{name: "/quit", description: "Exit the TUI"},
}

func commandSuggestions(input []rune) []commandSpec {
	value := strings.ToLower(string(input))
	if !strings.HasPrefix(value, "/") || strings.ContainsAny(value, " \n\t") {
		return nil
	}
	var matches []commandSpec
	for _, command := range commandCatalog {
		if strings.HasPrefix(command.name, value) || fuzzyContains(command.name, value) {
			matches = append(matches, command)
		}
	}
	return matches
}
