# Local Squad Recovery

Use this when you want the full four-agent setup:

- Codex
- Hermes
- Claude
- OpenClaw

Start the squad with:

```bash
agentcodehandoff quickstart --template local-squad --repo /path/to/repo
```

If the squad does not come up cleanly, check each external runtime directly:

```bash
agentcodehandoff agent-check --agent claude --repo /path/to/repo
agentcodehandoff agent-check --agent hermes --repo /path/to/repo
agentcodehandoff agent-check --agent openclaw --repo /path/to/repo
```

What each one validates:

- `claude`
  - Claude Code CLI is installed
  - the bridge runtime can see a real Claude login
  - the bridge invocation path can return structured output
- `hermes`
  - Hermes CLI is installed
  - Hermes can reach its configured provider path
  - the output tells you which provider/model/endpoint timed out
- `openclaw`
  - OpenClaw CLI is installed
  - the local `openclaw agent --json --agent main` path is usable

Recommended order:

1. Fix Claude runtime/login issues first.
2. Fix Hermes provider connectivity next.
3. Fix OpenClaw gateway or agent configuration last.
4. Rerun the squad:

```bash
agentcodehandoff up --template local-squad --repo /path/to/repo
```

During recovery, use:

```bash
agentcodehandoff bridge-status
agentcodehandoff ps
agentcodehandoff dashboard --view ops --interactive
```
