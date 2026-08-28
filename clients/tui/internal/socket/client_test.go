package socket

import (
	"errors"
	"testing"

	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
)

func TestOutboundQueueIsBounded(t *testing.T) {
	t.Parallel()
	client := New(Config{URL: "ws://127.0.0.1:1", ClientName: "test", OutboundBuffer: 1})
	if err := client.Send(protocol.NewPing("first")); err != nil {
		t.Fatal(err)
	}
	if err := client.Send(protocol.NewPing("second")); !errors.Is(err, ErrOutboundBufferFull) {
		t.Fatalf("second send error = %v", err)
	}
}
