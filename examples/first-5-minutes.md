# First 5 Minutes

This is the fastest path from clone to a real local multi-agent workflow.

It assumes:

- you already have the local agent CLIs you want to use
- you have a git repo you want them to collaborate in

## 1. Install and run the golden path

```bash
./install.sh
agentcodehandoff quickstart --repo /path/to/repo
```

By default, that:

- initializes local state
- installs wrappers
- runs `doctor`
- starts the built-in `local-trio`

## 2. Open the operator view

```bash
agentcodehandoff dashboard --view ops --interactive
```

Keep this terminal open.

## 3. Send one real request

```bash
agentcodehandoff request \
  --from-agent codex \
  --to-agent hermes \
  --summary "Need help" \
  --details "Reply automatically with a short acknowledgement." \
  --files README.md
```

## 4. Inspect the team

```bash
agentcodehandoff ps
agentcodehandoff requests
agentcodehandoff bridge-status
```

## 5. Try the four-agent setup

If you also have OpenClaw ready locally:

```bash
agentcodehandoff quickstart --template local-squad --repo /path/to/repo
```

OpenClaw support is built in, but live replies still depend on OpenClaw itself being configured in the local runtime.
