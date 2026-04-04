# Security Policy

## Scope

AgentCodeHandoff is a local-first terminal tool. Most risk comes from:

- local command execution through installed agent CLIs
- filesystem access inside repos you point it at
- background bridge processes on the local machine

It is designed around a bring-your-own-agent model:

- users install and authenticate their own local agent CLIs
- AgentCodeHandoff coordinates those local tools
- AgentCodeHandoff is not intended to proxy or resell third-party subscription credentials through a hosted service

## Reporting

If you find a security issue, do not open a public issue first.

Report it privately to the maintainer and include:

- affected version or commit
- impact summary
- reproduction steps
- whether it requires local access, repo access, or a malicious agent/tool response

## Current Security Expectations

- review bridge automation before enabling it in sensitive repos
- prefer least-privilege local environments
- treat agent CLI auth state as sensitive local runtime context
- validate repo and environment assumptions before using unattended bridge automation in production workflows
- prefer local user-controlled runtimes over shared credential relays
