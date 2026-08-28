package main

import "testing"

func TestBearerHeaders(t *testing.T) {
	t.Parallel()
	token := "0123456789abcdef0123456789abcdef"
	headers, err := bearerHeaders(token)
	if err != nil {
		t.Fatal(err)
	}
	if got := headers.Get("Authorization"); got != "Bearer "+token {
		t.Fatalf("authorization = %q", got)
	}
	if got := headers.Get("Origin"); got != "" {
		t.Fatalf("origin = %q", got)
	}
	if _, err := bearerHeaders("too-short"); err == nil {
		t.Fatal("expected short token rejection")
	}
}
