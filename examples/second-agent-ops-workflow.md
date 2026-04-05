# Second Agent Ops Workflow

This example is for the collaborator terminal after the primary agent has already started the shared workflow.

Use it when:

- the primary agent already started supervised bridges
- you are the second terminal joining the team
- you want to inspect bridge health before taking work

## 1. Watch the inbox and workflow stream

```bash
agentcodehandoff-hermes-watch
```

That gives the second agent a live view of:

- new handoffs
- `request`, `done`, `blocked`, and `review` events
- the files and scope already being discussed

## 2. Keep the ops dashboard open in another terminal

```bash
agentcodehandoff dashboard --view ops
```

Use it to answer:

- is the Hermes bridge healthy?
- are there pending or stale requests waiting for reply?
- did recovery already run?
- is there session drift that should become a new claim?

## 3. Inspect the current supervised bridge state

```bash
agentcodehandoff bridge-status
agentcodehandoff bridge-profile-show --agent hermes
```

The profile view is the fastest way to confirm what recovery will reuse:

- repo path
- interval and sweep settings
- claim-on-files behavior
- restart-related settings

## 4. Recover the bridge if needed

If Hermes is paused, down, or missing after a shell restart:

```bash
agentcodehandoff bridge-recover
```

Verify that recovery took effect:

```bash
agentcodehandoff bridge-status
agentcodehandoff dashboard --view ops
```

## 5. Respond as the second collaborator

Claim a bounded scope:

```bash
agentcodehandoff-hermes-claim \
  --scope docs-pass \
  --summary "Own onboarding docs only" \
  --files "README.md,examples/supervised-bridge-workflow.md"
```

Reply with outcome state instead of freeform chat only:

```bash
agentcodehandoff-hermes-done \
  --summary "Bridge recovery docs polished" \
  --details "README and examples now explain profile inspection and ops recovery flow." \
  --files "README.md,examples/supervised-bridge-workflow.md"
```

## 6. Fall back to a manual auto terminal if supervision is still being debugged

```bash
agentcodehandoff-hermes-auto --repo /path/to/repo
```

That is the safe fallback when the saved profile exists but supervised recovery is not the thing you want to trust yet.
