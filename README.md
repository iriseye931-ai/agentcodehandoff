<p align="center">
  <img src="assets/agentcodehandoff-mark.svg" alt="AgentCodeHandoff" width="96" height="96" />
</p>

<h1 align="center">AgentCodeHandoff</h1>

<p align="center"><strong>Private coordination for coding agents.</strong></p>

<p align="center">
  <em>A local-first shared handoff stream and claim board for Codex, Hermes, and other terminal agents.</em>
</p>

`agentcodehandoff` gives multiple coding agents one coordination layer inside a shared repo: clear handoffs, explicit ownership, and a durable local record of who is doing what.

It is built for teams running:

- Codex + Hermes
- two or more agent terminals on one machine
- one shared codebase
- a zero-infrastructure workflow

## Why Teams Need It

Most multi-agent coding workflows break on coordination, not model quality:

- no durable handoff stream
- no clear ownership of files or scopes
- no quick way to see who is doing what
- too much manual relaying between terminals

`agentcodehandoff` fixes that with a small, explicit local state model:

- `~/.agentcodehandoff/inbox.jsonl`
- `~/.agentcodehandoff/claims.json`

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
agentcodehandoff doctor
agentcodehandoff status
```

Start two terminals:

```bash
agentcodehandoff-codex-watch
agentcodehandoff-hermes-watch
```

Start auto-reply bridges in real terminals:

```bash
agentcodehandoff-codex-auto --repo /Users/iris/Projects/agent-inbox
agentcodehandoff-hermes-auto --repo /Users/iris/Projects/agent-inbox
```

Enable automatic file claims from bridge replies:

```bash
agentcodehandoff-hermes-auto --repo /Users/iris/Projects/agent-inbox --claim-on-files
```

Check whether the bridges appear alive:

```bash
agentcodehandoff auto-status
```

Send a handoff:

```bash
agentcodehandoff-codex-send \
  --summary "Need a realism pass" \
  --details "Own MeshGraph.tsx only" \
  --files "frontend/src/components/MeshGraph.tsx"
```

Send an auto-reply request:

```bash
agentcodehandoff-codex-request \
  --summary "Need a quick review" \
  --details "Reply automatically with short feedback." \
  --files "README.md"
```

Claim a scope:

```bash
agentcodehandoff-hermes-claim \
  --scope meshgraph-pass \
  --summary "Owning cinematic sphere polish" \
  --files "frontend/src/components/MeshGraph.tsx"
```

## Core Commands

```bash
agentcodehandoff init --install-wrappers --seed
agentcodehandoff doctor
agentcodehandoff read --agent codex
agentcodehandoff watch --agent hermes
agentcodehandoff latest --agent hermes
agentcodehandoff status
agentcodehandoff auto-status
agentcodehandoff claims
```

## Demo

`examples/demo-session.sh` runs a safe local session in `/tmp`:

- initializes state
- creates wrappers
- records a claim
- sends a handoff
- prints status

## Wrapper Commands

Installed by `agentcodehandoff init --install-wrappers`:

- `agentcodehandoff-codex-watch`
- `agentcodehandoff-hermes-watch`
- `agentcodehandoff-codex-read`
- `agentcodehandoff-hermes-read`
- `agentcodehandoff-codex-auto`
- `agentcodehandoff-hermes-auto`
- `agentcodehandoff-codex-send`
- `agentcodehandoff-hermes-send`
- `agentcodehandoff-codex-request`
- `agentcodehandoff-hermes-request`
- `agentcodehandoff-codex-claim`
- `agentcodehandoff-hermes-claim`
- `agentcodehandoff-codex-release`
- `agentcodehandoff-hermes-release`

## Typical Workflow

1. Agent A claims a bounded scope.
2. Agent B claims a non-overlapping scope.
3. Both agents keep `watch` running.
4. Each agent sends concise handoffs after bounded work.
5. Use `agentcodehandoff status` to inspect latest handoffs and open claims.

## Auto Reply

`agentcodehandoff auto --agent <name>` watches the inbox and uses a local agent CLI to generate a JSON reply automatically.

- `hermes` uses `hermes chat -Q -q`
- `codex` uses `codex --sandbox read-only exec`

Example:

```bash
agentcodehandoff-hermes-auto --repo /Users/iris/Projects/agent-inbox
agentcodehandoff-codex-auto --repo /Users/iris/Projects/agent-inbox
agentcodehandoff auto-status
```

Auto-claim example:

```bash
agentcodehandoff-hermes-auto \
  --repo /Users/iris/Projects/agent-inbox \
  --claim-on-files
```

Notes:

- this works only in a real terminal environment where Hermes and Codex can reach their providers
- it is not expected to work inside a restricted offline sandbox
- the auto bridge only replies to messages addressed to that agent
- auto bridges only respond to `request`, `task`, and `auto-request` roles
- plain `handoff` messages are informational and do not auto-trigger replies

## Configuration

- Default state directory: `~/.agentcodehandoff`
- Override with `AGENTCODEHANDOFF_HOME`
- Default wrapper directory: `~/.local/bin`

## Positioning

`agentcodehandoff` is intentionally narrow:

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
