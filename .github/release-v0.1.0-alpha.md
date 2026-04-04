# AgentCodeHandoff v0.1.0-alpha

Local-first control plane for bring-your-own coding agents.

This public alpha is the first open release of AgentCodeHandoff: a shared local coordination layer for Codex, Claude Code, Hermes, OpenClaw, and other terminal agents.

## Highlights

- shared inbox and claim board for local agent collaboration
- supervised bridges with recovery, restart policy, and logs
- availability-aware routing and graceful fallback
- request lifecycle tracking and resolution
- worktree-backed sessions, drift detection, and remediation
- interactive terminal ops dashboard
- built-in `local-trio` and `local-squad` team presets
- golden-path onboarding with `agentcodehandoff quickstart`

## Verified now

- live local trio verification with Codex, Hermes, and Claude
- OpenClaw integrated as a first-class supported agent in the tool
- full automated regression suite passing

## Positioning

AgentCodeHandoff is intentionally local-first and bring-your-own-agent:

- you run your own local agent CLIs
- auth stays with those tools
- AgentCodeHandoff coordinates them locally

It is not a hosted model provider or a third-party subscription harness.

## Getting started

```bash
./install.sh
agentcodehandoff quickstart --repo /path/to/repo
agentcodehandoff dashboard --view ops --interactive
```

Repo:

https://github.com/iriseye931-ai/agentcodehandoff
