<p align="center">
  <img src="assets/agentcodehandoff-mark.svg" alt="AgentCodeHandoff" width="96" height="96" />
</p>

<h1 align="center">AgentCodeHandoff</h1>

<p align="center"><strong>Private coordination for coding agents.</strong></p>

<p align="center">
  <em>A local-first shared handoff stream and claim board for Codex, Hermes, and other terminal agents.</em>
</p>

`agentcodehandoff` gives multiple coding agents one coordination layer inside a shared repo: clear handoffs, explicit ownership, workflow-state updates, and a durable local record of who is doing what.

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
- no shared notion of blocked, done, or review-ready work
- too much manual relaying between terminals

`agentcodehandoff` fixes that with a small, explicit local state model:

- `~/.agentcodehandoff/inbox.jsonl`
- `~/.agentcodehandoff/claims.json`

## What It Includes

- Shared inbox for agent-to-agent handoffs
- Lightweight claim board for file and scope ownership
- Workflow updates for `request`, `done`, `blocked`, and `review`
- Claim resolution with final states like `completed`, `blocked`, and `abandoned`
- Git worktree-backed agent sessions for isolated edit space
- File-awareness checks that compare live session edits to claimed files
- Terminal-first workflow for two or more agents in one repo
- Paneled terminal dashboard for bridge health, workflow, claims, conflicts, and recent messages
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

Open the live terminal dashboard:

```bash
agentcodehandoff-dashboard
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

Route a request automatically:

```bash
agentcodehandoff dispatch \
  --from-agent codex \
  --summary "Fix failing CLI test" \
  --details "Investigate the parser behavior and send it to the best agent automatically." \
  --files "src/agentcodehandoff/cli.py,README.md"
```

Claim a scope:

```bash
agentcodehandoff-hermes-claim \
  --scope meshgraph-pass \
  --summary "Owning cinematic sphere polish" \
  --files "frontend/src/components/MeshGraph.tsx"
```

Send a completion update:

```bash
agentcodehandoff-codex-done \
  --summary "CLI workflow states shipped" \
  --details "done, blocked, review, and claim resolution are live." \
  --files "src/agentcodehandoff/cli.py,README.md"
```

Signal a blocker:

```bash
agentcodehandoff-hermes-blocked \
  --summary "Need routing policy input" \
  --details "Current heuristics are too generic for design-vs-code review tasks." \
  --files "src/agentcodehandoff/cli.py"
```

Request review:

```bash
agentcodehandoff-codex-review \
  --summary "Review the dispatch heuristics" \
  --details "Check whether docs-heavy mixed tasks should route to Hermes." \
  --files "src/agentcodehandoff/cli.py,README.md"
```

Resolve a claim:

```bash
agentcodehandoff resolve \
  --agent codex \
  --scope cli-workflow-pass \
  --status completed \
  --note "Merged and verified locally."
```

Start an isolated worktree session:

```bash
agentcodehandoff session-start \
  --agent codex \
  --scope parser-pass \
  --repo /path/to/repo \
  --note "Isolated parser refactor worktree"
```

List sessions:

```bash
agentcodehandoff sessions
```

Close a session and remove its worktree:

```bash
agentcodehandoff session-end \
  --agent codex \
  --scope parser-pass \
  --note "Merged and cleaned up"
```

Inspect live drift against claimed files:

```bash
agentcodehandoff drift
```

Get actionable scope suggestions:

```bash
agentcodehandoff suggest
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
agentcodehandoff dashboard
agentcodehandoff-dashboard
agentcodehandoff-status
agentcodehandoff claims
agentcodehandoff sessions
agentcodehandoff drift
agentcodehandoff suggest
agentcodehandoff resolve --agent codex --scope cli-pass --status completed
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

- `agentcodehandoff-dashboard`
- `agentcodehandoff-auto-status`
- `agentcodehandoff-status`
- `agentcodehandoff-sessions`
- `agentcodehandoff-drift`
- `agentcodehandoff-suggest`
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
- `agentcodehandoff-codex-done`
- `agentcodehandoff-hermes-done`
- `agentcodehandoff-codex-blocked`
- `agentcodehandoff-hermes-blocked`
- `agentcodehandoff-codex-review`
- `agentcodehandoff-hermes-review`
- `agentcodehandoff-codex-release`
- `agentcodehandoff-hermes-release`

## Typical Workflow

1. Agent A claims a bounded scope.
2. Agent B claims a non-overlapping scope.
3. Both agents keep `watch` or `dashboard` running.
4. Use `request` for work that expects a response.
5. Use `done`, `blocked`, or `review` so progress reads like a workflow, not raw chat.
6. Resolve claims with `completed`, `blocked`, or `abandoned` when work closes out.
7. Use `agentcodehandoff status` to inspect latest handoffs, workflow events, open claims, and recently resolved claims.

## Terminal Dashboard

`agentcodehandoff-dashboard` is the fastest way to understand live system state in one terminal.

It shows:

- bridge health for Codex and Hermes
- latest handoffs
- workflow events like `request`, `blocked`, `review`, and `done`
- open claims
- claim conflicts
- recently resolved claims
- recent message traffic
- active worktree sessions
- file-awareness drift summaries
- actionable suggestions for expand, split, or handoff decisions

## Worktree Sessions

`agentcodehandoff` can manage isolated git worktrees per agent and scope.

Use this when you want:

- one agent per branch/worktree
- clean physical separation of edits
- session state that matches claim state
- dashboard visibility into who owns which workspace

By default, sessions create worktrees under:

- `<repo>/.worktrees/<agent>-<scope-slug>`

Default branch naming:

- `ach/<agent>/<scope-slug>`

Core commands:

```bash
agentcodehandoff session-start --agent codex --scope parser-pass --repo /path/to/repo
agentcodehandoff sessions
agentcodehandoff drift
agentcodehandoff suggest
agentcodehandoff session-end --agent codex --scope parser-pass
```

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

## Smart Routing

`agentcodehandoff route` scores a request for Codex vs Hermes:

- Hermes is preferred for docs, copy, README, install, review, and UX-oriented work
- Codex is preferred for bugs, tests, refactors, CLI/code changes, and build/debug work

Examples:

```bash
agentcodehandoff route \
  --summary "Improve README onboarding" \
  --details "Tighten install wording and first-run instructions." \
  --files "README.md,install.sh"
```

```bash
agentcodehandoff dispatch \
  --from-agent codex \
  --summary "Fix parser bug" \
  --details "Investigate failing CLI state handling and route to the best agent." \
  --files "src/agentcodehandoff/cli.py"
```

## Configuration

- Default state directory: `~/.agentcodehandoff`
- Override with `AGENTCODEHANDOFF_HOME`
- Default wrapper directory: `~/.local/bin`
- Default session state file: `~/.agentcodehandoff/sessions.json`

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
- notifications
- optional HTTP/WebSocket relay

## License

MIT
