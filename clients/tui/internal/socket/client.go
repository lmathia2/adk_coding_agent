// Package socket owns a reconnecting, bounded WebSocket transport.
package socket

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/gorilla/websocket"
	"github.com/lmathia2/adk_coding_agent/clients/tui/internal/protocol"
)

var ErrOutboundBufferFull = errors.New("websocket outbound buffer is full")

type Config struct {
	URL             string
	ClientName      string
	Headers         http.Header
	InboundBuffer   int
	OutboundBuffer  int
	MaxMessageBytes int64
	ReconnectMin    time.Duration
	ReconnectMax    time.Duration
	WriteTimeout    time.Duration
}

type EventKind int

const (
	Connected EventKind = iota
	Disconnected
	Message
	Warning
)

type Event struct {
	Kind    EventKind
	Message protocol.ServerMessage
	Err     error
}

type Client struct {
	config   Config
	outbound chan []byte
	events   chan Event
	closeMu  sync.Mutex
	current  *websocket.Conn
}

func New(config Config) *Client {
	if config.InboundBuffer < 1 {
		config.InboundBuffer = 64
	}
	if config.OutboundBuffer < 1 {
		config.OutboundBuffer = 64
	}
	if config.MaxMessageBytes < 1 {
		config.MaxMessageBytes = 1 << 20
	}
	if config.ReconnectMin <= 0 {
		config.ReconnectMin = 250 * time.Millisecond
	}
	if config.ReconnectMax < config.ReconnectMin {
		config.ReconnectMax = 10 * time.Second
	}
	if config.WriteTimeout <= 0 {
		config.WriteTimeout = 10 * time.Second
	}
	return &Client{
		config:   config,
		outbound: make(chan []byte, config.OutboundBuffer),
		events:   make(chan Event, config.InboundBuffer),
	}
}

func (c *Client) Events() <-chan Event { return c.events }

func (c *Client) Send(message protocol.ClientMessage) error {
	payload, err := protocol.EncodeClient(message)
	if err != nil {
		return err
	}
	select {
	case c.outbound <- payload:
		return nil
	default:
		return ErrOutboundBufferFull
	}
}

func (c *Client) Reconnect() {
	c.closeCurrent()
}

func (c *Client) Run(ctx context.Context) {
	defer close(c.events)
	backoff := c.config.ReconnectMin
	for ctx.Err() == nil {
		conn, _, err := websocket.DefaultDialer.DialContext(ctx, c.config.URL, c.config.Headers)
		if err != nil {
			c.publish(ctx, Event{Kind: Disconnected, Err: fmt.Errorf("connect: %w", err)})
			if !wait(ctx, backoff) {
				return
			}
			backoff = minDuration(backoff*2, c.config.ReconnectMax)
			continue
		}

		backoff = c.config.ReconnectMin
		c.setCurrent(conn)
		c.publish(ctx, Event{Kind: Connected})
		err = c.serve(ctx, conn)
		c.clearCurrent(conn)
		_ = conn.Close()
		if ctx.Err() == nil {
			c.publish(ctx, Event{Kind: Disconnected, Err: err})
			if !wait(ctx, backoff) {
				return
			}
		}
	}
}

func (c *Client) serve(ctx context.Context, conn *websocket.Conn) error {
	conn.SetReadLimit(c.config.MaxMessageBytes)
	if err := conn.SetWriteDeadline(time.Now().Add(c.config.WriteTimeout)); err != nil {
		return err
	}
	hello, err := protocol.EncodeClient(protocol.NewHello(c.config.ClientName))
	if err != nil {
		return err
	}
	if err := conn.WriteMessage(websocket.TextMessage, hello); err != nil {
		return fmt.Errorf("write hello: %w", err)
	}

	connectionDone := make(chan struct{})
	defer close(connectionDone)
	writerErrors := make(chan error, 1)
	go c.writeLoop(ctx, conn, connectionDone, writerErrors)
	go func() {
		select {
		case <-ctx.Done():
			_ = conn.Close()
		case <-connectionDone:
		}
	}()

	for {
		messageType, payload, err := conn.ReadMessage()
		if err != nil {
			select {
			case writeErr := <-writerErrors:
				return writeErr
			default:
				return fmt.Errorf("read: %w", err)
			}
		}
		if messageType != websocket.TextMessage {
			c.publish(ctx, Event{Kind: Warning, Err: errors.New("ignored non-text WebSocket frame")})
			continue
		}
		message, err := protocol.DecodeServer(payload)
		if err != nil {
			c.publish(ctx, Event{Kind: Warning, Err: err})
			continue
		}
		c.publish(ctx, Event{Kind: Message, Message: message})
	}
}

func (c *Client) writeLoop(ctx context.Context, conn *websocket.Conn, done <-chan struct{}, errors chan<- error) {
	for {
		select {
		case <-ctx.Done():
			return
		case <-done:
			return
		case payload := <-c.outbound:
			if err := conn.SetWriteDeadline(time.Now().Add(c.config.WriteTimeout)); err != nil {
				select {
				case errors <- err:
				default:
				}
				_ = conn.Close()
				return
			}
			if err := conn.WriteMessage(websocket.TextMessage, payload); err != nil {
				// Controls are idempotent, so retrying an ambiguously delivered frame is safe.
				select {
				case c.outbound <- payload:
				default:
					c.publish(ctx, Event{Kind: Warning, Err: ErrOutboundBufferFull})
				}
				select {
				case errors <- fmt.Errorf("write: %w", err):
				default:
				}
				_ = conn.Close()
				return
			}
		}
	}
}

func (c *Client) publish(ctx context.Context, event Event) {
	select {
	case c.events <- event:
	case <-ctx.Done():
	}
}

func (c *Client) setCurrent(conn *websocket.Conn) {
	c.closeMu.Lock()
	defer c.closeMu.Unlock()
	c.current = conn
}

func (c *Client) clearCurrent(conn *websocket.Conn) {
	c.closeMu.Lock()
	defer c.closeMu.Unlock()
	if c.current == conn {
		c.current = nil
	}
}

func (c *Client) closeCurrent() {
	c.closeMu.Lock()
	defer c.closeMu.Unlock()
	if c.current != nil {
		_ = c.current.Close()
	}
}

func wait(ctx context.Context, duration time.Duration) bool {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}

func minDuration(left, right time.Duration) time.Duration {
	if left < right {
		return left
	}
	return right
}
