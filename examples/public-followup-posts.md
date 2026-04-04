# Public Follow-Up Posts

Use these after the initial launch to highlight the recovery path for people affected by the Claude harness policy/news.

## X Follow-Up

If `Claude Code + Hermes + Codex` is your local stack, `AgentCodeHandoff` now has a real recovery path built in:

- `agentcodehandoff quickstart --template local-trio --repo /path/to/repo`
- `agentcodehandoff agent-check --agent claude --repo /path/to/repo`
- `agentcodehandoff agent-check --agent hermes --repo /path/to/repo`

Why this matters:
- Claude can stay in the mix without a third-party harness model
- Hermes issues now show the real provider/model/endpoint that timed out
- Claude issues now show the real bridge-runtime login mismatch

Recovery walkthrough:
https://github.com/iriseye931-ai/agentcodehandoff/blob/main/examples/recovery.md

Repo:
https://github.com/iriseye931-ai/agentcodehandoff

## LinkedIn Follow-Up

One of the most important improvements to `AgentCodeHandoff` after launch has been recovery.

If you are trying to keep a local team like:
- Codex
- Claude Code
- Hermes
- optionally OpenClaw

working together after the recent Claude harness policy/news, the biggest problem is usually not “does the CLI exist?”
It is “does the real bridge runtime work?”

That is why `AgentCodeHandoff` now includes:
- `agentcodehandoff agent-check --agent claude --repo /path/to/repo`
- `agentcodehandoff agent-check --agent hermes --repo /path/to/repo`

These checks go deeper than a normal version/auth check:
- Claude: catches the runtime mismatch where `claude auth status` in one shell looks fine, but the supervised bridge runtime is not actually logged in
- Hermes: shows the actual provider/model/endpoint path that is timing out

There is also now a concrete recovery walkthrough in the repo:
https://github.com/iriseye931-ai/agentcodehandoff/blob/main/examples/recovery.md

The goal remains the same:
- bring your own local agents
- keep Claude in the mix
- keep everything local
- coordinate them through one control plane instead of a hosted harness
