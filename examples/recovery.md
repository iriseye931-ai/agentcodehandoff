# Recovery Walkthrough

Use this when `quickstart` or `up --template local-trio` does not get you to a healthy team immediately.

## 1. Reproduce The Failure

Start with the normal flow:

```bash
agentcodehandoff quickstart --template local-trio --repo /path/to/repo
```

If team startup fails, AgentCodeHandoff now prints the next checks to run.

## 2. Check Claude Directly

Run:

```bash
agentcodehandoff agent-check --agent claude --repo /path/to/repo
```

Example output from a real mismatch:

```text
OK    claude CLI ready
      /Users/iris/.local/bin/claude | 2.1.91 (Claude Code)
FAIL  claude runtime ready
      { "loggedIn": false, "authMethod": "none", "apiProvider": "firstParty" }
      hint: The local claude CLI reports no active login in this runtime. Re-authenticate it and recover the bridge.
```

Interpretation:

- the `claude` binary is installed
- the bridge runtime is not actually logged in
- fix the Claude runtime first, then rerun `quickstart` or `bridge-recover`

## 3. Check Hermes Directly

Run:

```bash
agentcodehandoff agent-check --agent hermes --repo /path/to/repo
```

Example output from a real provider-path timeout:

```text
OK    hermes CLI ready
      /Users/iris/.local/bin/hermes | usage: hermes [-h] [--version] [--resume SESSION] [--continue [SESSION_NAME]]
FAIL  hermes runtime ready
      timed out after 20s | provider=custom | model=/Users/iris/.mlx/models/Qwen2.5-7B-Instruct-4bit | endpoint=http://192.168.1.186:8083/v1
      hint: Hermes CLI is available, but its provider path is timing out. Verify the configured endpoint/model and rerun `agentcodehandoff agent-check --agent hermes --repo /path/to/repo`.
```

Interpretation:

- the `hermes` binary is installed
- the problem is not the CLI itself
- the configured provider/model/endpoint path is slow or unavailable

## 4. Retry The Team

Once the failing agent is fixed, rerun:

```bash
agentcodehandoff quickstart --template local-trio --repo /path/to/repo
```

Or, if the state is already initialized:

```bash
agentcodehandoff up --template local-trio --repo /path/to/repo
```

## 5. Inspect Live State

Use these during recovery:

```bash
agentcodehandoff bridge-status
agentcodehandoff ps
agentcodehandoff dashboard --view ops --interactive
```

If a bridge is already configured but unhealthy, you can recover it after fixing the underlying runtime issue:

```bash
agentcodehandoff bridge-recover --agents claude hermes --force
```
