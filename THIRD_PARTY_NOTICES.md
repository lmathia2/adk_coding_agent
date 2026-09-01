# Third-party notices

This repository is designed to use development skills from
[`google/agents-cli`](https://github.com/google/agents-cli), pinned in
`.agents/skills/upstream-lock.json`. Those skills are licensed under Apache-2.0
by Google LLC. Synchronized copies retain their upstream notices and metadata.

The implementation also studies transferable patterns from the
[`long-horizon-harness`](https://github.com/google/adk-samples/tree/main/core/python/long-horizon-harness)
recipe in `google/adk-samples`, also Apache-2.0.

The locked runtime depends on
[`fff-search`](https://github.com/dmtrKovalenko/fff) version 0.10.5 for native indexed
file and content search. FFF is distributed under the MIT License; its wheel/source
distribution retains the upstream license and copyright notice.

The terminal client depends on
[`@earendil-works/pi-tui`](https://github.com/earendil-works/pi/tree/main/packages/tui)
version 0.84.4 for its editor, Markdown renderer, selectors, loader, input handling,
and terminal-width utilities. The package declares the MIT License and remains an
independent protocol client; this repository does not copy Pi's agent loop.
