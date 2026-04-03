<p align="center">
  <img src="assets/agents-inbox-mark.svg" alt="Agents Inbox" width="96" height="96" />
</p>

<h1 align="center">Agents Inbox</h1>

<p align="center"><strong>Professional coordination for coding agents.</strong></p>

<p align="center">
  <em>A local-first shared inbox, handoff stream, and claim board for Codex, Claude Code, and other terminal agents.</em>
</p>

`agents-inbox` gives multiple coding agents one professional coordination layer inside a shared repo: clear handoffs, explicit ownership, and a durable local record of who is doing what.

It is built for teams running:

- Codex + Claude Code
- two or more agent terminals on one machine
- one shared codebase
- a zero-infrastructure workflow

## Why Teams Need It

Most multi-agent coding workflows break on coordination, not model quality:

- no durable handoff stream
- no clear ownership of files or scopes
- no quick way to see who is doing what
- too much manual relaying between terminals

`agents-inbox` fixes that with a small, explicit local state model:

- `~/.agents-inbox/inbox.jsonl`
- `~/.agents-inbox/claims.json`

## What It Includes

- Shared inbox for agent-to-agent handoffs
- Lightweight claim board for file and scope ownership
- Terminal-first workflow for two or more agents in one repo
- Local-first state with no daemon required
- Agent-specific wrapper commands for faster day-to-day use

## Quick Start

```bash
cd agent-inbox
./install.sh
```

That installs the CLI, creates the local state directory, seeds bootstrap messages, and installs helper wrappers under `~/.local/bin`.

Try a disposable end-to-end demo:

```bash
./examples/demo-session.sh
```

## First Run

```bash
agents-inbox doctor
agents-inbox status
```

Start two terminals:

```bash
agents-inbox-codex-watch
agents-inbox-claude-watch
```

Send a handoff:

```bash
agents-inbox-codex-send \
  --summary "Need a realism pass" \
  --details "Own MeshGraph.tsx only" \
  --files "frontend/src/components/MeshGraph.tsx"
```

Claim a scope:

```bash
agents-inbox-claude-claim \
  --scope meshgraph-pass \
  --summary "Owning cinematic sphere polish" \
  --files "frontend/src/components/MeshGraph.tsx"
```

## Core Commands

```bash
agents-inbox init --install-wrappers --seed
agents-inbox doctor
agents-inbox read --agent codex
agents-inbox watch --agent claude
agents-inbox latest --agent claude
agents-inbox status
agents-inbox claims
```

## Demo

`examples/demo-session.sh` runs a safe local session in `/tmp`:

- initializes state
- creates wrappers
- records a claim
- sends a handoff
- prints status

## Wrapper Commands

Installed by `agents-inbox init --install-wrappers`:

- `agents-inbox-codex-watch`
- `agents-inbox-claude-watch`
- `agents-inbox-codex-read`
- `agents-inbox-claude-read`
- `agents-inbox-codex-send`
- `agents-inbox-claude-send`
- `agents-inbox-codex-claim`
- `agents-inbox-claude-claim`
- `agents-inbox-codex-release`
- `agents-inbox-claude-release`

## Typical Workflow

1. Agent A claims a bounded scope.
2. Agent B claims a non-overlapping scope.
3. Both agents keep `watch` running.
4. Each agent sends concise handoffs after bounded work.
5. Use `agents-inbox status` to inspect latest handoffs and open claims.

## Configuration

- Default state directory: `~/.agents-inbox`
- Override with `AGENTS_INBOX_HOME`
- Default wrapper directory: `~/.local/bin`

## Positioning

`agents-inbox` is intentionally narrow:

- not a cloud orchestration platform
- not a task router with hidden state
- not a heavyweight agent framework

It is the coordination layer you add when multiple coding agents already exist and need to collaborate reliably in one repo.

## Roadmap

- interactive TUI
- file change awareness
- repo-aware claim suggestions
- git worktree helpers
- notifications
- optional HTTP/WebSocket relay

## License

MIT
