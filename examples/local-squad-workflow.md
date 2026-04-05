# Local Squad Workflow

Use this when you want all three supported agents in one local team:

- Hermes
- Claude Code
- OpenClaw

## 1. Verify local readiness

```bash
agentcodehandoff doctor
```

For OpenClaw specifically, make sure its own local setup is ready enough for:

```bash
openclaw agent --json --agent main --message "status"
```

## 2. Start the squad

```bash
agentcodehandoff up --template local-squad --repo /path/to/repo
```

## 3. Keep the ops view open

```bash
agentcodehandoff dashboard --view ops --interactive
```

## 4. Send targeted work

Example: Claude asks OpenClaw for an ops/research-oriented reply.

```bash
agentcodehandoff request \
  --from-agent claude \
  --to-agent openclaw \
  --summary "Need context" \
  --details "Reply with a short ops-oriented acknowledgement." \
  --files README.md
```

Example: use smart routing for mixed work:

```bash
agentcodehandoff dispatch \
  --from-agent claude \
  --summary "Investigate integration behavior" \
  --details "Route this to the best local agent automatically." \
  --files src/agentcodehandoff/cli.py,README.md
```

## 5. Inspect the team

```bash
agentcodehandoff ps
agentcodehandoff requests
agentcodehandoff bridge-status
agentcodehandoff logs --agents openclaw --lines 40
```

## 6. Shut it down cleanly

```bash
agentcodehandoff down --template local-squad --repo /path/to/repo
```
