# Agents Inbox

> The shared handoff layer for coding agents.

`agents-inbox` gives Codex, Claude Code, and other terminal agents one local place to coordinate work without a daemon, cloud relay, or a human acting as copy-paste middleware.

```text
   /\    ____   _____ _   _ _____ ____   ____     ___ _   _ ____   _____  __
  /  \  / ___| | ____| \ | |_   _/ ___| |  _ \   |_ _| \ | | __ ) / _ \ \/ /
 / /\ \| |  _  |  _| |  \| | | | \___ \ | | | |   | ||  \| |  _ \| | | \  / 
/ ____ \ |_| | | |___| |\  | | |  ___) || |_| |   | || |\  | |_) | |_| /  \ 
/_/    \_\____| |_____|_| \_| |_| |____/ |____/   |___|_| \_|____/ \___/_/\_\
```

## What It Is

- Shared inbox for agent-to-agent handoffs
- Lightweight claim board for file and scope ownership
- Terminal-first workflow for two or more agents in one repo
- Local-only by default: state lives in `~/.agents-inbox`

## Why It Exists

Most multi-agent coding workflows fail on coordination, not generation:

- no durable handoff stream
- no clear ownership of files or scopes
- no quick way to see who is doing what
- too much manual relaying between terminals

`agents-inbox` solves that with two files:

- `~/.agents-inbox/inbox.jsonl`
- `~/.agents-inbox/claims.json`

## Quick Start

```bash
cd agent-inbox
./install.sh
```

That installs the CLI, creates the local state directory, seeds bootstrap messages, and adds helper wrappers under `~/.local/bin`.

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

## Roadmap

- interactive TUI
- file change awareness
- repo-aware claim suggestions
- git worktree helpers
- notifications
- optional HTTP/WebSocket relay

## License

MIT
