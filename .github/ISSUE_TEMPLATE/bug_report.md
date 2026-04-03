---
name: Bug report
about: Report a reproducible AgentCodeHandoff bug
title: "[Bug] "
labels: bug
assignees: ""
---

## Summary

Describe the bug clearly.

## Reproduction

1. Command(s) run:
2. Repo/setup context:
3. What happened:
4. What you expected:

## Environment

- OS:
- Python version:
- AgentCodeHandoff version/commit:
- Agent CLIs involved: Codex / Claude / Hermes / other

## Validation

- [ ] `python3 -m unittest discover -s tests -v`
- [ ] `python3 -m py_compile src/agentcodehandoff/cli.py tests/test_cli.py`
- [ ] I ran `agentcodehandoff doctor`

## Logs

Include relevant output from:

- `agentcodehandoff ps`
- `agentcodehandoff bridge-status`
- `agentcodehandoff logs --agents <agent> --lines 40`

