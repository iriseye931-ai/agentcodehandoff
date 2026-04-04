# Changelog

## Unreleased

- added `ps` for compact per-agent team summaries
- added `logs` for per-agent bridge log inspection
- improved operator diagnostics in `ops`
- added restart-cap supervision coverage
- added startup validation for invalid repos and missing agent CLIs
- added public release docs, support matrix, and contributing guide
- clarified bring-your-own-agent positioning and local authentication model
- completed a real public-alpha trio verification with Codex, Hermes, and Claude

## 0.1.0-alpha

Initial public-alpha baseline for local multi-agent coordination.

Highlights:

- local pair and trio templates
- supervised bridges with restart policy, recovery, and saved profiles
- availability-aware routing across Codex, Hermes, and Claude
- interactive terminal ops dashboard
- request lifecycle tracking and resolution
- worktree-backed sessions, drift detection, and remediation
- operator-focused commands:
  - `bridge-status`
  - `bridge-recover`
  - `logs`
  - `ps`
  - `ops-next`

Quality baseline:

- critical-path automated regression suite
- documented limitations
- release checklist
- live local trio verification through supervised bridges
