from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENV_HOME = "AGENTCODEHANDOFF_HOME"
DEFAULT_HOME = Path(os.environ.get(ENV_HOME, Path.home() / ".agentcodehandoff")).expanduser()
DEFAULT_INBOX_PATH = DEFAULT_HOME / "inbox.jsonl"
DEFAULT_CLAIMS_PATH = DEFAULT_HOME / "claims.json"
DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"
DEFAULT_AUTOMATION_STATE_DIR = DEFAULT_HOME / "automation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_timestamp(value: str) -> str:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%H:%M:%S")
    except Exception:
        return value


def _ensure_state(home: Path, inbox_path: Path, claims_path: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    if not inbox_path.exists():
        inbox_path.write_text("", encoding="utf-8")
    if not claims_path.exists():
        claims_path.write_text("[]\n", encoding="utf-8")


def _normalize_args(args: argparse.Namespace) -> argparse.Namespace:
    args.home = Path(args.home).expanduser()
    if getattr(args, "inbox_path", None) == DEFAULT_INBOX_PATH:
        args.inbox_path = args.home / "inbox.jsonl"
    else:
        args.inbox_path = Path(args.inbox_path).expanduser()
    if getattr(args, "claims_path", None) == DEFAULT_CLAIMS_PATH:
        args.claims_path = args.home / "claims.json"
    else:
        args.claims_path = Path(args.claims_path).expanduser()
    if getattr(args, "bin_dir", None) is not None:
        args.bin_dir = Path(args.bin_dir).expanduser()
    return args


def _split_files(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _extract_json_object(text: str) -> dict[str, Any] | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        for index in range(start, len(text)):
            char = text[index]
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:index + 1]
                    try:
                        parsed = json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    if isinstance(parsed, dict):
                        return parsed
        start = text.find("{", start + 1)
    return None


def _automation_state_path(home: Path, agent: str) -> Path:
    return home / "automation" / f"{agent}.json"


def _read_automation_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"seen_ids": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_ids": []}
    return data if isinstance(data, dict) else {"seen_ids": []}


def _write_automation_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _read_messages(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    messages: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                messages.append(record)
    return messages


def _write_message(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "id": payload.get("id") or f"msg-{datetime.now(timezone.utc).timestamp():.6f}",
        "timestamp": payload.get("timestamp") or _now_iso(),
        "from": payload["from"],
        "to": payload["to"],
        "role": payload.get("role", "handoff"),
        "task": payload.get("task", ""),
        "summary": payload["summary"],
        "details": payload.get("details", ""),
        "files": payload.get("files", []),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def _pending_messages_for_agent(messages: list[dict[str, Any]], agent: str, seen_ids: set[str]) -> list[dict[str, Any]]:
    needle = agent.strip().lower()
    pending: list[dict[str, Any]] = []
    for message in messages:
        message_id = str(message.get("id", "")).strip()
        if not message_id or message_id in seen_ids:
            continue
        recipient = str(message.get("to", "")).strip().lower()
        sender = str(message.get("from", "")).strip().lower()
        role = str(message.get("role", "")).strip().lower()
        if recipient != needle:
            continue
        if sender == needle:
            continue
        if role not in {"request", "task", "auto-request"}:
            continue
        pending.append(message)
    return pending


def _agent_prompt(agent: str, repo: Path, message: dict[str, Any]) -> str:
    files = message.get("files") or []
    files_block = "\n".join(f"- {item}" for item in files) if files else "- none provided"
    return (
        f"You are {agent} responding inside AgentCodeHandoff for repo {repo}.\n"
        "Return JSON only with this shape:\n"
        '{"summary":"short summary","details":"concise technical response","files":["optional/path"]}\n'
        "Do not include markdown fences or any extra text.\n"
        "If you are only acknowledging receipt, keep it brief.\n\n"
        f"From: {message.get('from', '')}\n"
        f"Task: {message.get('task', '')}\n"
        f"Summary: {message.get('summary', '')}\n"
        f"Details: {message.get('details', '')}\n"
        "Files:\n"
        f"{files_block}\n"
    )


def _run_hermes_auto(prompt: str, repo: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            str(shutil.which("hermes") or "/Users/iris/.local/bin/hermes"),
            "chat",
            "-Q",
            "--source",
            "tool",
            "-q",
            prompt,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (result.stdout or "") + ("\n" + result.stderr if result.stderr else "")
    parsed = _extract_json_object(combined)
    if not parsed:
        raise RuntimeError(f"hermes automation did not return JSON: {combined.strip()[:500]}")
    return parsed


def _run_codex_auto(prompt: str, repo: Path) -> dict[str, Any]:
    output_path = DEFAULT_HOME / "automation" / "codex-last-response.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(shutil.which("codex") or "/opt/homebrew/bin/codex"),
            "--sandbox",
            "read-only",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(repo),
            "-o",
            str(output_path),
            "-",
        ],
        input=prompt,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (output_path.read_text(encoding="utf-8") if output_path.exists() else "") + "\n" + (result.stderr or "")
    parsed = _extract_json_object(combined)
    if not parsed:
        raise RuntimeError(f"codex automation did not return JSON: {combined.strip()[:500]}")
    return parsed


def _run_auto_agent(agent: str, prompt: str, repo: Path) -> dict[str, Any]:
    if agent == "hermes":
        return _run_hermes_auto(prompt, repo)
    if agent == "codex":
        return _run_codex_auto(prompt, repo)
    raise ValueError(f"unsupported auto agent: {agent}")


def _read_claims(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _write_claims(path: Path, claims: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(claims, indent=2) + "\n", encoding="utf-8")


def _filter_messages(messages: list[dict[str, Any]], agent: str | None, limit: int) -> list[dict[str, Any]]:
    if agent:
        needle = agent.strip().lower()
        messages = [
            message
            for message in messages
            if str(message.get("from", "")).lower() == needle or str(message.get("to", "")).lower() == needle
        ]
    return messages[-limit:]


def _print_message(message: dict[str, Any]) -> None:
    header = f"[{_format_timestamp(str(message.get('timestamp', '')))}] {message.get('from', '?')} -> {message.get('to', '?')}"
    print(header)
    print(f"summary: {message.get('summary', '')}")
    task = str(message.get("task", "")).strip()
    if task:
        print(f"task: {task}")
    details = str(message.get("details", "")).strip()
    if details:
        print(details)
    files = message.get("files") or []
    if files:
        print("files:", ", ".join(str(item) for item in files))
    print()


def _print_claim(claim: dict[str, Any]) -> None:
    print(f"[{_format_timestamp(str(claim.get('timestamp', '')))}] {claim.get('agent', '?')} claims {claim.get('scope', '')}")
    summary = str(claim.get("summary", "")).strip()
    if summary:
        print(f"summary: {summary}")
    files = claim.get("files") or []
    if files:
        print("files:", ", ".join(str(item) for item in files))
    if claim.get("released"):
        print(f"released: {claim.get('released_at', '')}")
    print()


def _open_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if not claim.get("released")]


def _claim_conflicts(existing_claims: list[dict[str, Any]], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    candidate_agent = str(candidate.get("agent", "")).lower()
    candidate_scope = str(candidate.get("scope", "")).strip()
    candidate_files = {str(item).strip() for item in candidate.get("files", []) if str(item).strip()}
    conflicts: list[dict[str, Any]] = []

    for claim in _open_claims(existing_claims):
        existing_agent = str(claim.get("agent", "")).lower()
        if existing_agent == candidate_agent:
            continue
        existing_scope = str(claim.get("scope", "")).strip()
        existing_files = {str(item).strip() for item in claim.get("files", []) if str(item).strip()}
        overlapping_files = sorted(candidate_files & existing_files)
        same_scope = candidate_scope and existing_scope and candidate_scope == existing_scope
        if overlapping_files or same_scope:
            conflicts.append(
                {
                    "claim": claim,
                    "overlapping_files": overlapping_files,
                    "same_scope": same_scope,
                }
            )
    return conflicts


def _print_conflicts(conflicts: list[dict[str, Any]]) -> None:
    if not conflicts:
        return
    print("conflicts:")
    for conflict in conflicts:
        claim = conflict["claim"]
        print(
            f"- {claim.get('agent', '?')} already claims {claim.get('scope', '') or '(no scope)'}"
        )
        if conflict["same_scope"]:
            print("  same scope")
        if conflict["overlapping_files"]:
            print("  overlapping files:", ", ".join(conflict["overlapping_files"]))
    print()


def _wrapper_script(kind: str, agent: str) -> str:
    if kind == "watch":
        command = f'exec agentcodehandoff watch --agent "{agent}" "$@"\n'
    elif kind == "read":
        command = f'exec agentcodehandoff read --agent "{agent}" "$@"\n'
    elif kind == "auto":
        command = f'exec agentcodehandoff auto --agent "{agent}" "$@"\n'
    elif kind == "claim":
        command = f'exec agentcodehandoff claim --agent "{agent}" "$@"\n'
    elif kind == "release":
        command = f'exec agentcodehandoff release --agent "{agent}" "$@"\n'
    elif kind == "send":
        default_to = "hermes" if agent == "codex" else "codex"
        command = (
            'if [ "$#" -lt 1 ]; then\n'
            f'  echo "usage: agentcodehandoff-{agent}-send --summary <text> [extra args]" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec agentcodehandoff send --from-agent "{agent}" --to-agent "{default_to}" "$@"\n'
        )
    else:
        raise ValueError(f"unsupported wrapper kind: {kind}")
    return "#!/usr/bin/env bash\nset -euo pipefail\n" + command


def _install_wrappers(bin_dir: Path, force: bool = False) -> list[Path]:
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrappers: list[Path] = []
    for agent in ("codex", "hermes"):
        for kind in ("watch", "read", "auto", "send", "claim", "release"):
            path = bin_dir / f"agentcodehandoff-{agent}-{kind}"
            if path.exists() and not force:
                wrappers.append(path)
                continue
            path.write_text(_wrapper_script(kind, agent), encoding="utf-8")
            path.chmod(0o755)
            wrappers.append(path)
    return wrappers


def _print_check(level: str, label: str, detail: str) -> None:
    print(f"{level:4}  {label}")
    print(f"      {detail}")


def cmd_read(args: argparse.Namespace) -> None:
    messages = _filter_messages(_read_messages(args.inbox_path), args.agent, args.limit)
    for message in messages:
        _print_message(message)


def cmd_latest(args: argparse.Namespace) -> None:
    messages = _filter_messages(_read_messages(args.inbox_path), args.agent, max(1, args.limit))
    if messages:
        _print_message(messages[-1])


def cmd_send(args: argparse.Namespace) -> None:
    record = _write_message(
        args.inbox_path,
        {
            "from": args.from_agent,
            "to": args.to_agent,
            "role": args.role,
            "task": args.task,
            "summary": args.summary,
            "details": args.details,
            "files": _split_files(args.files or ""),
        },
    )
    _print_message(record)


def cmd_watch(args: argparse.Namespace) -> None:
    print(f"watching {args.inbox_path}")
    seen: set[str] = set()
    while True:
        messages = _filter_messages(_read_messages(args.inbox_path), args.agent, args.limit)
        for message in messages:
            message_id = str(message.get("id", ""))
            if message_id and message_id not in seen:
                seen.add(message_id)
                _print_message(message)
        time.sleep(args.interval)


def cmd_status(args: argparse.Namespace) -> None:
    messages = _read_messages(args.inbox_path)
    claims = _read_claims(args.claims_path)
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("from", "")).strip()
        if sender:
            latest_by_agent[sender] = message

    print("Latest agent handoffs")
    print()
    for agent in args.agents:
        message = latest_by_agent.get(agent)
        if message:
            print(f"{agent}: {message.get('summary', '')}")
        else:
            print(f"{agent}: waiting")
    print()
    print("Open claims")
    print()
    open_claims = [claim for claim in claims if not claim.get("released")]
    if not open_claims:
        print("none")
        print()
    else:
        for claim in open_claims:
            _print_claim(claim)

    conflicts_found = False
    for index, claim in enumerate(open_claims):
        remaining = open_claims[:index] + open_claims[index + 1 :]
        conflicts = _claim_conflicts(remaining, claim)
        if conflicts:
            if not conflicts_found:
                print("Claim conflicts")
                print()
                conflicts_found = True
            print(f"{claim.get('agent', '?')} -> {claim.get('scope', '') or '(no scope)'}")
            _print_conflicts(conflicts)
    if not conflicts_found:
        print("Claim conflicts")
        print()
        print("none")
        print()


def cmd_claim(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    claim = {
        "id": f"claim-{datetime.now(timezone.utc).timestamp():.6f}",
        "timestamp": _now_iso(),
        "agent": args.agent,
        "scope": args.scope,
        "summary": args.summary,
        "files": _split_files(args.files or ""),
        "released": False,
    }
    conflicts = _claim_conflicts(claims, claim)
    claims.append(claim)
    _write_claims(args.claims_path, claims)
    _print_claim(claim)
    if conflicts:
        print("warning: this claim overlaps with existing open claims")
        _print_conflicts(conflicts)


def cmd_claims(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    if args.agent:
        claims = [claim for claim in claims if str(claim.get("agent", "")).lower() == args.agent.lower()]
    if not args.all:
        claims = [claim for claim in claims if not claim.get("released")]
    claims = claims[-args.limit:]
    for claim in claims:
        _print_claim(claim)


def cmd_release(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    updated = False
    for claim in claims:
        matches_agent = str(claim.get("agent", "")).lower() == args.agent.lower()
        matches_scope = args.scope and str(claim.get("scope", "")) == args.scope
        if matches_agent and (matches_scope or not args.scope) and not claim.get("released"):
            claim["released"] = True
            claim["released_at"] = _now_iso()
            updated = True
    _write_claims(args.claims_path, claims)
    if not updated:
        print("no matching open claims")
    else:
        print("claims released")


def cmd_auto(args: argparse.Namespace) -> None:
    state_path = _automation_state_path(args.home, args.agent)
    state = _read_automation_state(state_path)
    seen_ids = {str(item) for item in state.get("seen_ids", [])}

    while True:
        messages = _read_messages(args.inbox_path)
        pending = _pending_messages_for_agent(messages, args.agent, seen_ids)
        for message in pending:
            message_id = str(message.get("id", "")).strip()
            prompt = _agent_prompt(args.agent, args.repo, message)
            try:
                response = _run_auto_agent(args.agent, prompt, args.repo)
            except Exception as exc:
                if args.verbose:
                    print(f"auto-reply error for {message_id}: {exc}", file=sys.stderr)
                seen_ids.add(message_id)
                state["seen_ids"] = sorted(seen_ids)
                _write_automation_state(state_path, state)
                continue

            summary = str(response.get("summary", "")).strip() or f"{args.agent} reply"
            details = str(response.get("details", "")).strip()
            files = response.get("files") if isinstance(response.get("files"), list) else []
            record = _write_message(
                args.inbox_path,
                {
                    "from": args.agent,
                    "to": str(message.get("from", "")).strip() or "codex",
                    "role": "handoff",
                    "task": str(message.get("task", "")).strip() or "auto-response",
                    "summary": summary,
                    "details": details,
                    "files": [str(item) for item in files if str(item).strip()],
                },
            )
            if args.verbose:
                _print_message(record)
            seen_ids.add(message_id)
            state["seen_ids"] = sorted(seen_ids)
            _write_automation_state(state_path, state)

        if args.once:
            return
        time.sleep(args.interval)


def cmd_init(args: argparse.Namespace) -> None:
    _ensure_state(args.home, args.inbox_path, args.claims_path)
    created_messages: list[str] = []

    for agent in args.agents:
        existing = any(
            str(message.get("from", "")).lower() == "system"
            and str(message.get("to", "")).lower() == agent.lower()
            and str(message.get("role", "")) == "bootstrap"
            for message in _read_messages(args.inbox_path)
        )
        if not existing and args.seed:
            _write_message(
                args.inbox_path,
                {
                    "from": "system",
                    "to": agent,
                    "role": "bootstrap",
                    "task": "setup",
                    "summary": f"{agent} inbox ready",
                    "details": "Use send, watch, claim, and status to coordinate work.",
                    "files": [],
                },
            )
            created_messages.append(agent)

    wrappers: list[Path] = []
    if args.install_wrappers:
        wrappers = _install_wrappers(args.bin_dir, force=args.force)

    print(f"home: {args.home}")
    print(f"inbox: {args.inbox_path}")
    print(f"claims: {args.claims_path}")
    if created_messages:
        print("seeded:", ", ".join(created_messages))
    if wrappers:
        print("wrappers:")
        for wrapper in wrappers:
            print(f"  {wrapper}")
    if str(args.bin_dir) not in os.environ.get("PATH", ""):
        print()
        print(f"add to PATH if needed: export PATH=\"{args.bin_dir}:$PATH\"")


def cmd_doctor(args: argparse.Namespace) -> None:
    failures = 0

    critical_checks = [
        ("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]),
        (f"home exists: {args.home}", args.home.exists(), "present" if args.home.exists() else "missing"),
        (f"inbox file: {args.inbox_path}", args.inbox_path.exists(), "present" if args.inbox_path.exists() else "missing"),
        (f"claims file: {args.claims_path}", args.claims_path.exists(), "present" if args.claims_path.exists() else "missing"),
        (
            f"inbox writable: {args.inbox_path}",
            args.inbox_path.exists() and os.access(args.inbox_path, os.W_OK),
            "yes" if args.inbox_path.exists() and os.access(args.inbox_path, os.W_OK) else "no",
        ),
        (
            f"claims writable: {args.claims_path}",
            args.claims_path.exists() and os.access(args.claims_path, os.W_OK),
            "yes" if args.claims_path.exists() and os.access(args.claims_path, os.W_OK) else "no",
        ),
    ]

    for label, ok, detail in critical_checks:
        _print_check("OK" if ok else "FAIL", label, detail)
        if not ok:
            failures += 1

    warnings = [
        ("agentcodehandoff on PATH", shutil.which("agentcodehandoff") is not None, shutil.which("agentcodehandoff") or "not found"),
        (
            f"bin dir on PATH: {args.bin_dir}",
            str(args.bin_dir) in os.environ.get("PATH", "").split(":"),
            "yes" if str(args.bin_dir) in os.environ.get("PATH", "").split(":") else "no",
        ),
    ]

    for agent in ("codex", "hermes"):
        for kind in ("watch", "send"):
            wrapper_path = args.bin_dir / f"agentcodehandoff-{agent}-{kind}"
            warnings.append(
                (
                    f"wrapper: {wrapper_path.name}",
                    wrapper_path.exists(),
                    str(wrapper_path) if wrapper_path.exists() else "missing",
                )
            )

    for label, ok, detail in warnings:
        _print_check("OK" if ok else "WARN", label, detail)

    if failures:
        print()
        print("Run `agentcodehandoff init --install-wrappers` to create state and wrapper scripts.")
        raise SystemExit(1)

    if any(not ok for _, ok, _ in warnings):
        print()
        print("Core setup is usable. Optional PATH and wrapper issues are warnings, not blockers.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Shared inbox and claim board for coding agents")
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME, help=f"state directory (default: ${ENV_HOME} or ~/.agentcodehandoff)")
    parser.add_argument("--inbox-path", type=Path, default=DEFAULT_INBOX_PATH, help="shared inbox file path")
    parser.add_argument("--claims-path", type=Path, default=DEFAULT_CLAIMS_PATH, help="shared claims file path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create local state and optional shell wrappers")
    init_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    init_parser.add_argument("--seed", action="store_true", help="seed bootstrap messages for agents")
    init_parser.add_argument("--install-wrappers", action="store_true", help="install helper wrapper scripts into the bin dir")
    init_parser.add_argument("--force", action="store_true", help="overwrite existing wrapper scripts")
    init_parser.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR, help="wrapper install directory")
    init_parser.set_defaults(func=cmd_init)

    doctor_parser = subparsers.add_parser("doctor", help="verify local setup and wrapper installation")
    doctor_parser.add_argument("--bin-dir", type=Path, default=DEFAULT_BIN_DIR, help="wrapper install directory")
    doctor_parser.set_defaults(func=cmd_doctor)

    auto_parser = subparsers.add_parser("auto", help="watch the inbox and auto-reply using a local agent CLI")
    auto_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    auto_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo working directory for the agent")
    auto_parser.add_argument("--interval", type=float, default=2.0)
    auto_parser.add_argument("--once", action="store_true", help="process pending messages once and exit")
    auto_parser.add_argument("--verbose", action="store_true")
    auto_parser.set_defaults(func=cmd_auto)

    read_parser = subparsers.add_parser("read", help="read recent agent messages")
    read_parser.add_argument("--agent", help="filter messages by agent name")
    read_parser.add_argument("--limit", type=int, default=20)
    read_parser.set_defaults(func=cmd_read)

    latest_parser = subparsers.add_parser("latest", help="show newest matching message")
    latest_parser.add_argument("--agent", help="filter messages by agent name")
    latest_parser.add_argument("--limit", type=int, default=1)
    latest_parser.set_defaults(func=cmd_latest)

    watch_parser = subparsers.add_parser("watch", help="poll and print new agent messages")
    watch_parser.add_argument("--agent", help="filter messages by agent name")
    watch_parser.add_argument("--limit", type=int, default=20)
    watch_parser.add_argument("--interval", type=float, default=2.0)
    watch_parser.set_defaults(func=cmd_watch)

    status_parser = subparsers.add_parser("status", help="show latest handoffs per agent and current claims")
    status_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    status_parser.set_defaults(func=cmd_status)

    send_parser = subparsers.add_parser("send", help="send an agent handoff")
    send_parser.add_argument("--from-agent", required=True)
    send_parser.add_argument("--to-agent", required=True)
    send_parser.add_argument("--summary", required=True)
    send_parser.add_argument("--details", default="")
    send_parser.add_argument("--task", default="shared task")
    send_parser.add_argument("--role", default="handoff")
    send_parser.add_argument("--files", default="", help="comma-separated file list")
    send_parser.set_defaults(func=cmd_send)

    claim_parser = subparsers.add_parser("claim", help="claim ownership of a scope or file set")
    claim_parser.add_argument("--agent", required=True)
    claim_parser.add_argument("--scope", required=True, help="high-level ownership scope")
    claim_parser.add_argument("--summary", required=True)
    claim_parser.add_argument("--files", default="", help="comma-separated file list")
    claim_parser.set_defaults(func=cmd_claim)

    claims_parser = subparsers.add_parser("claims", help="list current claims")
    claims_parser.add_argument("--agent", help="filter claims by agent")
    claims_parser.add_argument("--limit", type=int, default=20)
    claims_parser.add_argument("--all", action="store_true", help="include released claims")
    claims_parser.set_defaults(func=cmd_claims)

    release_parser = subparsers.add_parser("release", help="release claims for an agent")
    release_parser.add_argument("--agent", required=True)
    release_parser.add_argument("--scope", help="release only a specific scope")
    release_parser.set_defaults(func=cmd_release)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args = _normalize_args(args)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
