# Supervised Bridge Workflow

This example is a copy-paste checklist for the managed bridge flow.

It assumes:

- one shared repo
- Codex and Claude/Hermes running in separate terminals
- `agentcodehandoff` already installed

## 1. Verify local state

```bash
agentcodehandoff doctor
agentcodehandoff status
```

## 2. Start supervised bridges

```bash
agentcodehandoff bridge-start --agent codex --repo /path/to/repo --auto-sweep
agentcodehandoff bridge-start --agent hermes --repo /path/to/repo --auto-sweep
```

Optional tuning:

```bash
agentcodehandoff bridge-start \
  --agent codex \
  --repo /path/to/repo \
  --auto-sweep \
  --sweep-interval 30 \
  --max-restarts 5
```

## 3. Keep the ops view open

```bash
agentcodehandoff dashboard --view ops
```

Use it for:

- bridge pid and heartbeat health
- pending and stale requests
- restart counts
- session drift and suggestions

## 4. Send real collaboration messages

```bash
agentcodehandoff-codex-request \
  --summary "Review the onboarding docs" \
  --details "Own README polish only and reply with suggested wording." \
  --files "README.md"
```

```bash
agentcodehandoff dispatch \
  --from-agent codex \
  --summary "Fix the failing CLI parser test" \
  --details "Route this to the best agent automatically and expect a reply." \
  --files "tests/test_cli.py,src/agentcodehandoff/cli.py"
```

## 5. Inspect bridge health and request state

```bash
agentcodehandoff bridge-status
agentcodehandoff requests
agentcodehandoff request-sweep
```

## 6. Recover from a paused or missing bridge

Supervised bridges save a per-agent profile, so recovery can restart from the last known repo and settings.

```bash
agentcodehandoff bridge-recover
```

If you want automation or CI to fail when no bridge actually needed recovery:

```bash
agentcodehandoff bridge-recover --fail-if-idle
```

## 7. Fall back to manual auto terminals if needed

```bash
agentcodehandoff-codex-auto --repo /path/to/repo
agentcodehandoff-hermes-auto --repo /path/to/repo
```

That fallback is useful when:

- the supervised bridge is being debugged
- auth or provider setup changed
- you want to watch one agent closely before putting it back under supervision
