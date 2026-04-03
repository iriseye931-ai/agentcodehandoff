# Contributing

AgentCodeHandoff is still being hardened for public release. Keep changes tight, testable, and local-first.

## Expectations

- prefer small, bounded pull requests
- do not broaden scope without updating tests and docs
- preserve terminal-first workflows
- avoid hidden background behavior unless it is clearly surfaced in operator views

## Before Opening a PR

Run:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile src/agentcodehandoff/cli.py tests/test_cli.py
```

If you change install, bridge lifecycle, or operator commands, update:

- `README.md`
- relevant examples under `examples/`
- wrapper generation behavior if command surfaces changed

## Good Contribution Areas

- supervision reliability
- operator UX and diagnostics
- test coverage for lifecycle and failure paths
- install portability
- public documentation and examples
