from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENV_HOME = "AGENTCODEHANDOFF_HOME"
DEFAULT_HOME = Path(os.environ.get(ENV_HOME, Path.home() / ".agentcodehandoff")).expanduser()
DEFAULT_INBOX_PATH = DEFAULT_HOME / "inbox.jsonl"
DEFAULT_CLAIMS_PATH = DEFAULT_HOME / "claims.json"
DEFAULT_SESSIONS_PATH = DEFAULT_HOME / "sessions.json"
DEFAULT_BIN_DIR = Path.home() / ".local" / "bin"
DEFAULT_AUTOMATION_STATE_DIR = DEFAULT_HOME / "automation"
DEFAULT_BRIDGE_STATE_DIR = DEFAULT_HOME / "bridges"
AUTOMATION_STALE_SECONDS = 30
DASHBOARD_RECENT_MESSAGES = 8
RECENT_WORKFLOW_MESSAGES = 6
TERMINAL_FALLBACK_SIZE = (120, 40)
BRIDGE_HEARTBEAT_SECONDS = 10
BRIDGE_RESTART_BASE_DELAY = 2.0
BRIDGE_RESTART_MAX_DELAY = 30.0
REQUEST_STALE_SECONDS = 300
REQUEST_ESCALATE_SECONDS = 900
BRIDGE_EVENT_HISTORY = 8
BRIDGE_COOL_OFF_SECONDS = 300


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
    sessions_path = home / "sessions.json"
    if not sessions_path.exists():
        sessions_path.write_text("[]\n", encoding="utf-8")


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
    if getattr(args, "sessions_path", None) == DEFAULT_SESSIONS_PATH:
        args.sessions_path = args.home / "sessions.json"
    elif getattr(args, "sessions_path", None) is not None:
        args.sessions_path = Path(args.sessions_path).expanduser()
    if getattr(args, "bin_dir", None) is not None:
        args.bin_dir = Path(args.bin_dir).expanduser()
    if getattr(args, "repo", None) is not None:
        args.repo = Path(args.repo).expanduser()
    if getattr(args, "path", None) is not None:
        args.path = Path(args.path).expanduser()
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
        return {"seen_ids": [], "last_poll_at": "", "last_reply_at": "", "last_error": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen_ids": [], "last_poll_at": "", "last_reply_at": "", "last_error": ""}
    return data if isinstance(data, dict) else {"seen_ids": [], "last_poll_at": "", "last_reply_at": "", "last_error": ""}


def _write_automation_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _bridge_lock_path(home: Path, agent: str) -> Path:
    return home / "bridges" / f"{agent}.json"


def _bridge_profile_path(home: Path, agent: str) -> Path:
    return home / "bridges" / f"{agent}.profile.json"


def _read_bridge_lock(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_bridge_lock(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _read_bridge_profile(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_bridge_profile(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _save_bridge_profile(home: Path, payload: dict[str, Any]) -> None:
    agent = str(payload.get("agent", "")).strip()
    if not agent:
        return
    profile = {
        "agent": agent,
        "repo": str(payload.get("repo", "")).strip(),
        "interval": float(payload.get("interval", 2.0) or 2.0),
        "claim_on_files": bool(payload.get("claim_on_files", False)),
        "claim_scope_prefix": str(payload.get("claim_scope_prefix", "auto-") or "auto-"),
        "auto_sweep": bool(payload.get("auto_sweep", False)),
        "sweep_interval": float(payload.get("sweep_interval", 30.0) or 30.0),
        "max_restarts": int(payload.get("max_restarts", 5) or 5),
        "cool_off_seconds": float(payload.get("cool_off_seconds", BRIDGE_COOL_OFF_SECONDS) or BRIDGE_COOL_OFF_SECONDS),
        "updated_at": _now_iso(),
    }
    _write_bridge_profile(_bridge_profile_path(home, agent), profile)


def _bridge_profile_summary_line(profile: dict[str, Any], width: int) -> str:
    agent = str(profile.get("agent", "?")).strip() or "?"
    repo = str(profile.get("repo", "")).strip() or "(no repo)"
    auto_sweep = "sweep" if bool(profile.get("auto_sweep", False)) else "manual"
    max_restarts = int(profile.get("max_restarts", 0) or 0)
    cool_off_seconds = float(profile.get("cool_off_seconds", 0.0) or 0.0)
    return _truncate(
        f"{agent}: {repo} | {auto_sweep} | restart max={max_restarts} window={int(cool_off_seconds)}s",
        width,
    )


def _append_bridge_event(path: Path, event_type: str, summary: str, *, detail: str = "") -> None:
    payload = _read_bridge_lock(path)
    history = payload.get("recent_events", [])
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "timestamp": _now_iso(),
            "type": event_type,
            "summary": summary,
            "detail": detail,
        }
    )
    payload["recent_events"] = history[-BRIDGE_EVENT_HISTORY:]
    _write_bridge_lock(path, payload)


def _remove_bridge_lock(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _signal_pid(pid: int, sig: int) -> bool:
    try:
        os.kill(pid, sig)
    except OSError:
        return False
    return True


def _classify_error(text: str) -> str:
    lowered = text.lower()
    if not lowered:
        return ""
    if "not return json" in lowered or "json" in lowered:
        return "malformed-response"
    if "not found" in lowered or "no such file" in lowered:
        return "missing-dependency"
    if "quota" in lowered or "rate limit" in lowered:
        return "rate-limit"
    if "auth" in lowered or "permission" in lowered or "unauthorized" in lowered:
        return "auth"
    if "git" in lowered or "worktree" in lowered or "repo" in lowered:
        return "repo"
    return "runtime"


def _is_hard_failure(failure_class: str) -> bool:
    return failure_class in {"auth", "missing-dependency", "repo"}


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


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
        "request_id": payload.get("request_id", ""),
        "derived_from_request_id": payload.get("derived_from_request_id", ""),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def _send_record(
    inbox_path: Path,
    *,
    from_agent: str,
    to_agent: str,
    role: str,
    task: str,
    summary: str,
    details: str,
    files: str | list[str] | None,
    request_id: str = "",
    derived_from_request_id: str = "",
) -> dict[str, Any]:
    normalized_files = files if isinstance(files, list) else _split_files(files or "")
    return _write_message(
        inbox_path,
        {
            "from": from_agent,
            "to": to_agent,
            "role": role,
            "task": task,
            "summary": summary,
            "details": details,
            "files": normalized_files,
            "request_id": request_id,
            "derived_from_request_id": derived_from_request_id,
        },
    )


def _request_age_seconds(timestamp: str) -> float | None:
    dt = _parse_iso(timestamp)
    if dt is None:
        return None
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _request_records(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    requests = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() in {"request", "task", "auto-request"}
        and not str(message.get("derived_from_request_id", "")).strip()
    ]
    followups = [
        message for message in messages
        if str(message.get("derived_from_request_id", "")).strip()
    ]
    outcomes = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() in {"done", "blocked", "review", "approved", "closed", "escalated"}
    ]
    handoffs = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() == "handoff"
    ]
    records: list[dict[str, Any]] = []
    for request in requests:
        request_id = str(request.get("id", "")).strip()
        request_to = str(request.get("to", "")).strip().lower()
        related_outcomes = [
            item for item in outcomes
            if (
                str(item.get("request_id", "")).strip() == request_id
                or (
                    str(item.get("from", "")).strip().lower() == request_to
                    and str(item.get("to", "")).strip().lower() == str(request.get("from", "")).strip().lower()
                    and str(item.get("task", "")).strip() == str(request.get("task", "")).strip()
                )
            )
        ]
        related_handoffs = [
            item for item in handoffs
            if (
                str(item.get("request_id", "")).strip() == request_id
                or (
                    str(item.get("from", "")).strip().lower() == request_to
                    and str(item.get("to", "")).strip().lower() == str(request.get("from", "")).strip().lower()
                    and str(item.get("task", "")).strip() == str(request.get("task", "")).strip()
                )
            )
        ]
        latest_outcome = related_outcomes[-1] if related_outcomes else None
        latest_handoff = related_handoffs[-1] if related_handoffs else None
        age_seconds = _request_age_seconds(str(request.get("timestamp", "")).strip())
        state = "pending"
        if latest_outcome is not None:
            state = str(latest_outcome.get("role", "pending")).strip().lower() or "pending"
        elif latest_handoff is not None:
            state = "acknowledged"
        elif age_seconds is not None and age_seconds >= REQUEST_STALE_SECONDS:
            state = "stale"
        records.append(
            {
                "request": request,
                "request_id": request_id,
                "state": state,
                "age_seconds": age_seconds,
                "latest_outcome": latest_outcome,
                "latest_handoff": latest_handoff,
                "followups": [item for item in followups if str(item.get("derived_from_request_id", "")).strip() == request_id],
            }
        )
    return records


def _request_record_by_id(records: list[dict[str, Any]], request_id: str) -> dict[str, Any] | None:
    needle = request_id.strip()
    for record in records:
        if str(record.get("request_id", "")).strip() == needle:
            return record
    return None


def _bridge_recent_restart_times(lock: dict[str, Any]) -> list[datetime]:
    events = lock.get("recent_events", [])
    if not isinstance(events, list):
        return []
    restart_times: list[datetime] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("type", "")).strip() not in {"child-exit", "startup-failed"}:
            continue
        timestamp = _parse_iso(str(event.get("timestamp", "")).strip())
        if timestamp is not None:
            restart_times.append(timestamp)
    return restart_times


def _pending_age_buckets(messages: list[dict[str, Any]]) -> dict[str, int]:
    buckets = {"fresh": 0, "warm": 0, "stale": 0}
    for message in messages:
        age_seconds = _request_age_seconds(str(message.get("timestamp", "")).strip())
        if age_seconds is None:
            buckets["fresh"] += 1
        elif age_seconds >= REQUEST_STALE_SECONDS:
            buckets["stale"] += 1
        elif age_seconds >= 30:
            buckets["warm"] += 1
        else:
            buckets["fresh"] += 1
    return buckets


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


def _routing_score(agent: str, text: str, files: list[str]) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    lowered = text.lower()
    file_text = " ".join(files).lower()
    if agent == "hermes":
        rules = {
            "readme": 4,
            "docs": 4,
            "documentation": 4,
            "copy": 3,
            "wording": 3,
            "ux": 3,
            "review": 2,
            "summary": 2,
            "explain": 2,
            "install": 3,
            "guide": 3,
        }
        for key, weight in rules.items():
            if key in lowered:
                score += weight
                reasons.append(key)
        if ".md" in file_text or "readme" in file_text:
            score += 4
            reasons.append("markdown-files")
    else:
        rules = {
            "bug": 4,
            "fix": 4,
            "test": 4,
            "refactor": 4,
            "cli": 3,
            "build": 3,
            "compile": 3,
            "implementation": 3,
            "code": 2,
            "patch": 3,
            "error": 3,
            "stack trace": 4,
        }
        for key, weight in rules.items():
            if key in lowered:
                score += weight
                reasons.append(key)
        if any(token in file_text for token in [".py", ".ts", ".tsx", ".js", ".rs", ".go", ".sh"]):
            score += 3
            reasons.append("code-files")
    return score, reasons


def _recommend_agent(summary: str, details: str, files: list[str]) -> tuple[str, dict[str, Any]]:
    combined = " ".join(part for part in [summary, details] if part).strip()
    hermes_score, hermes_reasons = _routing_score("hermes", combined, files)
    codex_score, codex_reasons = _routing_score("codex", combined, files)
    if codex_score > hermes_score:
        return "codex", {"scores": {"codex": codex_score, "hermes": hermes_score}, "reasons": {"codex": codex_reasons, "hermes": hermes_reasons}}
    if hermes_score > codex_score:
        return "hermes", {"scores": {"codex": codex_score, "hermes": hermes_score}, "reasons": {"codex": codex_reasons, "hermes": hermes_reasons}}
    if any(item.lower().endswith(".md") for item in files):
        hermes_score += 1
        hermes_reasons.append("markdown-tiebreak")
        return "hermes", {"scores": {"codex": codex_score, "hermes": hermes_score}, "reasons": {"codex": codex_reasons, "hermes": hermes_reasons}}
    codex_score += 1
    codex_reasons.append("code-default")
    return "codex", {"scores": {"codex": codex_score, "hermes": hermes_score}, "reasons": {"codex": codex_reasons, "hermes": hermes_reasons}}


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


def _read_sessions(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _write_sessions(path: Path, sessions: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sessions, indent=2) + "\n", encoding="utf-8")


def _active_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [session for session in sessions if str(session.get("state", "active")) == "active"]


def _slugify(value: str) -> str:
    pieces = []
    current = []
    for char in value.lower():
        if char.isalnum():
            current.append(char)
        else:
            if current:
                pieces.append("".join(current))
                current = []
    if current:
        pieces.append("".join(current))
    return "-".join(pieces) or "session"


def _run_git(repo: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _git_output(repo: Path, args: list[str]) -> str:
    result = _run_git(repo, args)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        detail = stderr or stdout or "git command failed"
        raise RuntimeError(detail)
    return (result.stdout or "").strip()


def _repo_root(repo: Path) -> Path:
    return Path(_git_output(repo, ["rev-parse", "--show-toplevel"]))


def _git_current_branch(repo: Path) -> str:
    return _git_output(repo, ["branch", "--show-current"]) or "main"


def _git_changed_files(repo: Path) -> list[str]:
    result = _run_git(repo, ["status", "--porcelain"])
    if result.returncode != 0:
        return []
    changed: list[str] = []
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        path_part = line[3:] if len(line) > 3 else ""
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1]
        normalized = path_part.strip()
        if normalized:
            changed.append(normalized)
    return changed


def _linked_claim_for_session(session: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any] | None:
    explicit_scope = str(session.get("claim_scope", "")).strip()
    session_agent = str(session.get("agent", "")).strip().lower()
    session_scope = str(session.get("scope", "")).strip()
    for claim in claims:
        if str(claim.get("agent", "")).strip().lower() != session_agent:
            continue
        claim_scope = str(claim.get("scope", "")).strip()
        if explicit_scope and claim_scope == explicit_scope:
            return claim
        if not explicit_scope and claim_scope == session_scope:
            return claim
    return None


def _session_drift(session: dict[str, Any], claims: list[dict[str, Any]]) -> dict[str, Any]:
    worktree_path = Path(str(session.get("worktree_path", "")))
    if not worktree_path.exists():
        return {"status": "missing", "changed_files": [], "unexpected_files": [], "claim": _linked_claim_for_session(session, claims)}
    changed_files = _git_changed_files(worktree_path)
    claim = _linked_claim_for_session(session, claims)
    declared_files = {str(item).strip() for item in (claim.get("files", []) if claim else []) if str(item).strip()}
    unexpected = sorted(file for file in changed_files if declared_files and file not in declared_files)
    if not changed_files:
        status = "clean"
    elif not declared_files:
        status = "unscoped"
    elif unexpected:
        status = "drift"
    else:
        status = "aligned"
    return {
        "status": status,
        "changed_files": changed_files,
        "unexpected_files": unexpected,
        "claim": claim,
    }


def _claim_for_file(claims: list[dict[str, Any]], file_path: str, *, exclude_agent: str = "") -> dict[str, Any] | None:
    for claim in _open_claims(claims):
        claim_agent = str(claim.get("agent", "")).strip().lower()
        if exclude_agent and claim_agent == exclude_agent.lower():
            continue
        declared_files = {str(item).strip() for item in claim.get("files", []) if str(item).strip()}
        if file_path in declared_files:
            return claim
    return None


def _extension_set(files: list[str]) -> set[str]:
    extensions: set[str] = set()
    for file_path in files:
        suffix = Path(file_path).suffix.strip().lower()
        if suffix:
            extensions.add(suffix)
    return extensions


def _session_suggestions(session: dict[str, Any], drift: dict[str, Any], claims: list[dict[str, Any]]) -> list[str]:
    status = str(drift.get("status", "")).strip()
    changed_files = [str(item) for item in drift.get("changed_files", [])]
    unexpected_files = [str(item) for item in drift.get("unexpected_files", [])]
    claim = drift.get("claim")
    claim_scope = str(claim.get("scope", "")).strip() if isinstance(claim, dict) else ""
    session_agent = str(session.get("agent", "")).strip().lower()
    suggestions: list[str] = []

    if status == "clean":
        return ["No action needed. Session is clean."]
    if status == "aligned":
        return ["Stay in the current claim. All changed files are within scope."]
    if status == "missing":
        return ["Worktree is missing. Recreate the session or close it with `session-end`."]
    if status == "unscoped":
        file_list = ", ".join(changed_files[:3]) if changed_files else "current changes"
        return [f"Create or link a claim for {file_list} before continuing."]

    owner_suggestions: list[str] = []
    for file_path in unexpected_files:
        owner_claim = _claim_for_file(claims, file_path, exclude_agent=session_agent)
        if owner_claim:
            owner_suggestions.append(
                f"Handoff {file_path} to {owner_claim.get('agent', '?')} ({owner_claim.get('scope', '')})."
            )

    if owner_suggestions:
        suggestions.extend(owner_suggestions)

    declared_files = [str(item) for item in (claim.get("files", []) if isinstance(claim, dict) else []) if str(item).strip()]
    declared_ext = _extension_set(declared_files)
    unexpected_ext = _extension_set(unexpected_files)

    if unexpected_files and not owner_suggestions:
        if len(unexpected_files) <= 2 and (not declared_ext or unexpected_ext.issubset(declared_ext)):
            if claim_scope:
                suggestions.append(
                    f"Expand claim `{claim_scope}` to include {', '.join(unexpected_files[:3])}."
                )
            else:
                suggestions.append(
                    f"Add a claim covering {', '.join(unexpected_files[:3])}."
                )
        else:
            suggestions.append(
                f"Split {', '.join(unexpected_files[:3])} into a new scope or separate session."
            )

    if not suggestions:
        suggestions.append("Review changed files and either expand the claim or split the work.")
    return suggestions


def _session_remediations(session: dict[str, Any], drift: dict[str, Any], claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    status = str(drift.get("status", "")).strip()
    changed_files = [str(item) for item in drift.get("changed_files", [])]
    unexpected_files = [str(item) for item in drift.get("unexpected_files", [])]
    claim = drift.get("claim")
    claim_scope = str(claim.get("scope", "")).strip() if isinstance(claim, dict) else ""
    session_agent = str(session.get("agent", "")).strip().lower()
    remediations: list[dict[str, Any]] = []

    if status == "clean":
        return [{"type": "noop", "message": "Session is clean."}]
    if status == "aligned":
        return [{"type": "noop", "message": "All changed files are already within the active claim."}]
    if status == "missing":
        return [{"type": "manual", "message": "Worktree is missing. Recreate the session or close it manually."}]
    if status == "unscoped":
        return [{"type": "manual", "message": f"Create or link a claim for {', '.join(changed_files[:3]) or 'current changes'}."}]

    owner_actions: list[dict[str, Any]] = []
    for file_path in unexpected_files:
        owner_claim = _claim_for_file(claims, file_path, exclude_agent=session_agent)
        if owner_claim:
            owner_actions.append(
                {
                    "type": "handoff",
                    "to_agent": str(owner_claim.get("agent", "")).strip(),
                    "to_scope": str(owner_claim.get("scope", "")).strip(),
                    "files": [file_path],
                    "message": f"Handoff {file_path} to {owner_claim.get('agent', '?')} ({owner_claim.get('scope', '')}).",
                }
            )
    if owner_actions:
        remediations.extend(owner_actions)
        return remediations

    declared_files = [str(item) for item in (claim.get("files", []) if isinstance(claim, dict) else []) if str(item).strip()]
    declared_ext = _extension_set(declared_files)
    unexpected_ext = _extension_set(unexpected_files)

    if unexpected_files and len(unexpected_files) <= 2 and (not declared_ext or unexpected_ext.issubset(declared_ext)) and claim_scope:
        remediations.append(
            {
                "type": "expand-claim",
                "claim_scope": claim_scope,
                "files": unexpected_files,
                "message": f"Expand claim `{claim_scope}` to include {', '.join(unexpected_files[:3])}.",
            }
        )
        return remediations

    scope_slug = _slugify("-".join(unexpected_files[:2]) or f"{session.get('scope', '')}-split")
    new_scope = f"{session.get('scope', '')}-{scope_slug}".strip("-")
    summary = f"Split scope for {', '.join(unexpected_files[:2])}" if unexpected_files else "Split scope"
    recommended_agent, meta = _recommend_agent(summary, f"Split work from session {session.get('scope', '')}", unexpected_files)
    remediations.append(
        {
            "type": "split-claim",
            "scope": new_scope,
            "agent": recommended_agent,
            "files": unexpected_files,
            "route_meta": meta,
            "message": f"Create new scope `{new_scope}` for {', '.join(unexpected_files[:3]) or 'drifted files'} and assign it to {recommended_agent}.",
        }
    )
    return remediations


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
    state = str(claim.get("state", "open")).strip() or "open"
    print(
        f"[{_format_timestamp(str(claim.get('timestamp', '')))}] "
        f"{claim.get('agent', '?')} claims {claim.get('scope', '')} [{state}]"
    )
    summary = str(claim.get("summary", "")).strip()
    if summary:
        print(f"summary: {summary}")
    files = claim.get("files") or []
    if files:
        print("files:", ", ".join(str(item) for item in files))
    resolution_note = str(claim.get("resolution_note", "")).strip()
    if resolution_note:
        print(f"note: {resolution_note}")
    if state != "open":
        resolved_at = str(claim.get("resolved_at", "")).strip() or str(claim.get("released_at", "")).strip()
        if resolved_at:
            print(f"resolved: {resolved_at}")
    print()


def _open_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if str(claim.get("state", "open")).strip() == "open" and not claim.get("released")]


def _resolved_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [claim for claim in claims if str(claim.get("state", "open")).strip() != "open" or claim.get("released")]


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


def _generic_wrapper_script(kind: str) -> str:
    if kind == "dashboard":
        command = 'exec agentcodehandoff dashboard "$@"\n'
    elif kind == "auto-status":
        command = 'exec agentcodehandoff auto-status "$@"\n'
    elif kind == "status":
        command = 'exec agentcodehandoff status "$@"\n'
    elif kind == "requests":
        command = 'exec agentcodehandoff requests "$@"\n'
    elif kind == "request-sweep":
        command = 'exec agentcodehandoff request-sweep "$@"\n'
    elif kind == "sessions":
        command = 'exec agentcodehandoff sessions "$@"\n'
    elif kind == "drift":
        command = 'exec agentcodehandoff drift "$@"\n'
    elif kind == "suggest":
        command = 'exec agentcodehandoff suggest "$@"\n'
    elif kind == "remediate":
        command = 'exec agentcodehandoff remediate "$@"\n'
    elif kind == "bridge-status":
        command = 'exec agentcodehandoff bridge-status "$@"\n'
    elif kind == "bridge-recover":
        command = 'exec agentcodehandoff bridge-recover "$@"\n'
    elif kind == "bridge-profiles":
        command = 'exec agentcodehandoff bridge-profiles "$@"\n'
    elif kind == "bridge-profile-show":
        command = 'exec agentcodehandoff bridge-profile-show "$@"\n'
    elif kind == "bridge-profile-delete":
        command = 'exec agentcodehandoff bridge-profile-delete "$@"\n'
    elif kind == "ops":
        command = 'exec agentcodehandoff dashboard --view ops "$@"\n'
    elif kind == "request-approve":
        command = 'exec agentcodehandoff request-approve "$@"\n'
    elif kind == "request-close":
        command = 'exec agentcodehandoff request-close "$@"\n'
    elif kind == "request-escalate":
        command = 'exec agentcodehandoff request-escalate "$@"\n'
    elif kind == "request-resolve":
        command = 'exec agentcodehandoff request-resolve "$@"\n'
    else:
        raise ValueError(f"unsupported generic wrapper kind: {kind}")
    return "#!/usr/bin/env bash\nset -euo pipefail\n" + command


def _wrapper_script(kind: str, agent: str) -> str:
    if kind == "watch":
        command = f'exec agentcodehandoff watch --agent "{agent}" "$@"\n'
    elif kind == "read":
        command = f'exec agentcodehandoff read --agent "{agent}" "$@"\n'
    elif kind == "auto":
        command = f'exec agentcodehandoff auto --agent "{agent}" "$@"\n'
    elif kind == "request":
        default_to = "hermes" if agent == "codex" else "codex"
        command = (
            'if [ "$#" -lt 1 ]; then\n'
            f'  echo "usage: agentcodehandoff-{agent}-request --summary <text> [extra args]" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec agentcodehandoff request --from-agent "{agent}" --to-agent "{default_to}" "$@"\n'
        )
    elif kind == "claim":
        command = f'exec agentcodehandoff claim --agent "{agent}" "$@"\n'
    elif kind == "done":
        default_to = "hermes" if agent == "codex" else "codex"
        command = (
            'if [ "$#" -lt 1 ]; then\n'
            f'  echo "usage: agentcodehandoff-{agent}-done --summary <text> [extra args]" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec agentcodehandoff done --from-agent "{agent}" --to-agent "{default_to}" "$@"\n'
        )
    elif kind == "blocked":
        default_to = "hermes" if agent == "codex" else "codex"
        command = (
            'if [ "$#" -lt 1 ]; then\n'
            f'  echo "usage: agentcodehandoff-{agent}-blocked --summary <text> [extra args]" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec agentcodehandoff blocked --from-agent "{agent}" --to-agent "{default_to}" "$@"\n'
        )
    elif kind == "review":
        default_to = "hermes" if agent == "codex" else "codex"
        command = (
            'if [ "$#" -lt 1 ]; then\n'
            f'  echo "usage: agentcodehandoff-{agent}-review --summary <text> [extra args]" >&2\n'
            "  exit 1\n"
            "fi\n"
            f'exec agentcodehandoff review --from-agent "{agent}" --to-agent "{default_to}" "$@"\n'
        )
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
    for kind in ("dashboard", "ops", "auto-status", "status", "requests", "request-sweep", "sessions", "drift", "suggest", "remediate", "bridge-status", "bridge-recover", "bridge-profiles", "bridge-profile-show", "bridge-profile-delete", "request-approve", "request-close", "request-escalate", "request-resolve"):
        path = bin_dir / f"agentcodehandoff-{kind}"
        if path.exists() and not force:
            wrappers.append(path)
            continue
        path.write_text(_generic_wrapper_script(kind), encoding="utf-8")
        path.chmod(0o755)
        wrappers.append(path)
    for agent in ("codex", "hermes"):
        for kind in ("watch", "read", "auto", "send", "request", "claim", "done", "blocked", "review", "release"):
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


def _print_bridge_state(agent: str, state: dict[str, Any]) -> None:
    last_poll = _parse_iso(str(state.get("last_poll_at", "")).strip())
    last_reply = _parse_iso(str(state.get("last_reply_at", "")).strip())
    now = datetime.now(timezone.utc)
    is_alive = last_poll is not None and (now - last_poll).total_seconds() <= AUTOMATION_STALE_SECONDS
    print(f"{agent}: {'alive' if is_alive else 'stale'}")
    if last_poll:
        print(f"  last poll: {_format_timestamp(last_poll.isoformat())}")
    if last_reply:
        print(f"  last reply: {_format_timestamp(last_reply.isoformat())}")
    last_error = str(state.get("last_error", "")).strip()
    if last_error:
        print(f"  last error: {last_error}")
    seen_ids = state.get("seen_ids", [])
    if isinstance(seen_ids, list):
        print(f"  seen ids: {len(seen_ids)}")
    print()


def _terminal_size() -> os.terminal_size:
    return shutil.get_terminal_size(TERMINAL_FALLBACK_SIZE)


def _truncate(value: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def _message_summary_line(message: dict[str, Any], width: int) -> str:
    role = str(message.get("role", "")).strip() or "handoff"
    sender = str(message.get("from", "?")).strip() or "?"
    recipient = str(message.get("to", "?")).strip() or "?"
    summary = str(message.get("summary", "")).strip() or "(no summary)"
    text = f"{sender}->{recipient} [{role}] {summary}"
    return _truncate(text, width)


def _claim_summary_line(claim: dict[str, Any], width: int) -> str:
    agent = str(claim.get("agent", "?")).strip() or "?"
    scope = str(claim.get("scope", "")).strip() or "(no scope)"
    state = str(claim.get("state", "open")).strip() or "open"
    files = claim.get("files") or []
    suffix = f" :: {len(files)} file" if len(files) == 1 else f" :: {len(files)} files" if files else ""
    return _truncate(f"{agent} [{state}] {scope}{suffix}", width)


def _session_summary_line(session: dict[str, Any], width: int) -> str:
    agent = str(session.get("agent", "?")).strip() or "?"
    scope = str(session.get("scope", "")).strip() or "(no scope)"
    branch = str(session.get("branch", "")).strip() or "(no branch)"
    state = str(session.get("state", "active")).strip() or "active"
    return _truncate(f"{agent} [{state}] {scope} :: {branch}", width)


def _session_drift_summary_line(session: dict[str, Any], drift: dict[str, Any], width: int) -> str:
    agent = str(session.get("agent", "?")).strip() or "?"
    scope = str(session.get("scope", "")).strip() or "(no scope)"
    status = str(drift.get("status", "unknown"))
    changed_count = len(drift.get("changed_files", []))
    unexpected_count = len(drift.get("unexpected_files", []))
    if status == "drift":
        tail = f"{changed_count} changed, {unexpected_count} outside claim"
    elif status == "aligned":
        tail = f"{changed_count} changed, all claimed"
    elif status == "clean":
        tail = "no local changes"
    elif status == "unscoped":
        tail = f"{changed_count} changed, no claimed files"
    else:
        tail = "worktree missing"
    return _truncate(f"{agent}::{scope} [{status}] {tail}", width)


def _bridge_status_line(agent: str, state: dict[str, Any], width: int) -> str:
    last_poll = _parse_iso(str(state.get("last_poll_at", "")).strip())
    now = datetime.now(timezone.utc)
    alive = last_poll is not None and (now - last_poll).total_seconds() <= AUTOMATION_STALE_SECONDS
    status = "alive" if alive else "stale"
    seen_ids = state.get("seen_ids", [])
    seen_count = len(seen_ids) if isinstance(seen_ids, list) else 0
    last_reply = _parse_iso(str(state.get("last_reply_at", "")).strip())
    reply_text = _format_timestamp(last_reply.isoformat()) if last_reply else "--:--:--"
    text = f"{agent}: {status} | seen {seen_count} | reply {reply_text}"
    return _truncate(text, width)


def _supervised_bridge_status(home: Path, inbox_path: Path, agent: str) -> dict[str, Any]:
    lock = _read_bridge_lock(_bridge_lock_path(home, agent))
    profile = _read_bridge_profile(_bridge_profile_path(home, agent))
    pid = int(lock.get("pid", 0) or 0)
    supervisor_pid = int(lock.get("supervisor_pid", 0) or 0)
    alive = _pid_alive(pid)
    supervisor_alive = _pid_alive(supervisor_pid)
    heartbeat = _parse_iso(str(lock.get("last_heartbeat_at", "")).strip())
    now = datetime.now(timezone.utc)
    healthy = supervisor_alive and alive and heartbeat is not None and (now - heartbeat).total_seconds() <= BRIDGE_HEARTBEAT_SECONDS
    automation_state = _read_automation_state(_automation_state_path(home, agent))
    seen_ids = {str(item) for item in automation_state.get("seen_ids", []) if str(item).strip()}
    pending = _pending_messages_for_agent(_read_messages(inbox_path), agent, seen_ids)
    oldest_pending_at = ""
    if pending:
        timestamps = [_parse_iso(str(message.get("timestamp", "")).strip()) for message in pending]
        valid_times = [item for item in timestamps if item is not None]
        if valid_times:
            oldest_pending_at = min(valid_times).isoformat()
    pending_buckets = _pending_age_buckets(pending)
    return {
        "agent": agent,
        "pid": pid,
        "alive": alive,
        "supervisor_alive": supervisor_alive,
        "healthy": healthy,
        "lock": lock,
        "pending_count": len(pending),
        "pending_buckets": pending_buckets,
        "oldest_pending_at": oldest_pending_at,
        "automation_state": automation_state,
        "failure_class": str(lock.get("failure_class", "")).strip(),
        "restart_count": int(lock.get("restart_count", 0) or 0),
        "auto_sweep": bool(lock.get("auto_sweep", False)),
        "sweep_interval": float(lock.get("sweep_interval", 0.0) or 0.0),
        "last_sweep_at": str(lock.get("last_sweep_at", "")).strip(),
        "last_exit_at": str(lock.get("last_exit_at", "")).strip(),
        "last_exit_code": lock.get("last_exit_code", ""),
        "recent_events": lock.get("recent_events", []),
        "profile": profile,
    }


def _bridge_supervision_line(status: dict[str, Any], width: int) -> str:
    agent = str(status.get("agent", "?"))
    pid = int(status.get("pid", 0) or 0)
    supervisor_pid = int(status.get("lock", {}).get("supervisor_pid", 0) or 0) if isinstance(status.get("lock"), dict) else 0
    healthy = bool(status.get("healthy"))
    paused = bool(status.get("lock", {}).get("paused", False)) if isinstance(status.get("lock"), dict) else False
    pending_count = int(status.get("pending_count", 0) or 0)
    pending_buckets = status.get("pending_buckets", {}) if isinstance(status.get("pending_buckets"), dict) else {}
    restart_count = int(status.get("restart_count", 0) or 0)
    auto_sweep = bool(status.get("auto_sweep"))
    heartbeat = _parse_iso(str(status.get("lock", {}).get("last_heartbeat_at", "")).strip()) if isinstance(status.get("lock"), dict) else None
    heartbeat_text = _format_timestamp(heartbeat.isoformat()) if heartbeat else "--:--:--"
    state = "healthy" if healthy else "paused" if paused else "down"
    sweep_text = " | sweep" if auto_sweep else ""
    bucket_text = f"f{int(pending_buckets.get('fresh', 0) or 0)}/w{int(pending_buckets.get('warm', 0) or 0)}/s{int(pending_buckets.get('stale', 0) or 0)}"
    return _truncate(f"{agent}: {state} | supervisor {supervisor_pid or '-'} | bridge {pid or '-'} | pending {pending_count} ({bucket_text}) | hb {heartbeat_text} | restarts {restart_count}{sweep_text}", width)


def _bridge_command_args(args: argparse.Namespace, agent: str) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--home",
        str(args.home),
        "--inbox-path",
        str(args.inbox_path),
        "--claims-path",
        str(args.claims_path),
        "--sessions-path",
        str(args.sessions_path),
        "auto",
        "--agent",
        agent,
        "--repo",
        str(args.repo),
        "--interval",
        str(args.interval),
        "--supervised",
    ]
    if args.claim_on_files:
        command.append("--claim-on-files")
    if args.verbose:
        command.append("--verbose")
    if args.claim_scope_prefix:
        command.extend(["--claim-scope-prefix", args.claim_scope_prefix])
    if getattr(args, "log_path", ""):
        command.extend(["--log-path", str(args.log_path)])
    return command


def _supervisor_command_args(args: argparse.Namespace, agent: str, log_path: Path) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--home",
        str(args.home),
        "--inbox-path",
        str(args.inbox_path),
        "--claims-path",
        str(args.claims_path),
        "--sessions-path",
        str(args.sessions_path),
        "supervise",
        "--agent",
        agent,
        "--repo",
        str(args.repo),
        "--interval",
        str(args.interval),
        "--log-path",
        str(log_path),
    ]
    if args.claim_on_files:
        command.append("--claim-on-files")
    if args.verbose:
        command.append("--verbose")
    if args.claim_scope_prefix:
        command.extend(["--claim-scope-prefix", args.claim_scope_prefix])
    if getattr(args, "auto_sweep", False):
        command.append("--auto-sweep")
    if getattr(args, "sweep_interval", 0.0):
        command.extend(["--sweep-interval", str(args.sweep_interval)])
    if getattr(args, "max_restarts", None) is not None:
        command.extend(["--max-restarts", str(args.max_restarts)])
    if getattr(args, "cool_off_seconds", None) is not None:
        command.extend(["--cool-off-seconds", str(args.cool_off_seconds)])
    return command


def _print_bridge_supervision(status: dict[str, Any]) -> None:
    print(_bridge_supervision_line(status, 120))
    lock = status.get("lock", {}) if isinstance(status.get("lock"), dict) else {}
    profile = status.get("profile", {}) if isinstance(status.get("profile"), dict) else {}
    repo = str(lock.get("repo", "")).strip()
    if repo:
        print(f"  repo: {repo}")
    elif profile.get("repo"):
        print(f"  repo profile: {profile.get('repo', '')}")
    log_path = str(lock.get("log_path", "")).strip()
    if log_path:
        print(f"  log: {log_path}")
    oldest_pending_at = str(status.get("oldest_pending_at", "")).strip()
    if oldest_pending_at:
        print(f"  oldest pending: {_format_timestamp(oldest_pending_at)}")
    pending_buckets = status.get("pending_buckets", {}) if isinstance(status.get("pending_buckets"), dict) else {}
    print(
        "  pending buckets:"
        f" fresh={int(pending_buckets.get('fresh', 0) or 0)}"
        f" warm={int(pending_buckets.get('warm', 0) or 0)}"
        f" stale={int(pending_buckets.get('stale', 0) or 0)}"
    )
    last_error = str(status.get("automation_state", {}).get("last_error", "")).strip()
    if last_error:
        print(f"  last error: {last_error}")
    failure_class = str(status.get("failure_class", "")).strip()
    if failure_class:
        print(f"  failure class: {failure_class}")
    if status.get("auto_sweep"):
        print(f"  auto sweep: every {float(status.get('sweep_interval', 0.0) or 0.0):.1f}s")
        last_sweep_at = str(status.get("last_sweep_at", "")).strip()
        if last_sweep_at:
            print(f"  last sweep: {_format_timestamp(last_sweep_at)}")
    print(
        f"  restart policy: max={int(lock.get('max_restarts', 0) or 0)}"
        f" window={float(lock.get('cool_off_seconds', 0.0) or 0.0):.0f}s"
    )
    last_exit_at = str(status.get("last_exit_at", "")).strip()
    if last_exit_at:
        print(f"  last exit: {_format_timestamp(last_exit_at)} code={status.get('last_exit_code', '')}")
    recent_events = status.get("recent_events", [])
    if isinstance(recent_events, list) and recent_events:
        print("  recent events:")
        for event in recent_events[-3:]:
            summary = str(event.get("summary", "")).strip()
            detail = str(event.get("detail", "")).strip()
            when = _format_timestamp(str(event.get("timestamp", "")).strip())
            line = f"    [{when}] {event.get('type', '')}: {summary}"
            print(line)
            if detail:
                print(f"      {detail}")
    profile_updated_at = str(profile.get("updated_at", "")).strip()
    if profile_updated_at:
        print(f"  saved profile: {_format_timestamp(profile_updated_at)}")
    print()


def _conflict_lines(claims: list[dict[str, Any]], width: int, limit: int) -> list[str]:
    lines: list[str] = []
    open_claims = _open_claims(claims)
    for index, claim in enumerate(open_claims):
        remaining = open_claims[:index] + open_claims[index + 1 :]
        conflicts = _claim_conflicts(remaining, claim)
        if not conflicts:
            continue
        left = _truncate(f"{claim.get('agent', '?')}::{claim.get('scope', '') or '(no scope)'}", max(12, width // 2))
        conflict = conflicts[0]
        conflict_claim = conflict["claim"]
        right = _truncate(
            f"{conflict_claim.get('agent', '?')}::{conflict_claim.get('scope', '') or '(no scope)'}",
            max(12, width // 2),
        )
        overlap_note = "same scope" if conflict["same_scope"] else ", ".join(conflict["overlapping_files"][:2]) or "file overlap"
        lines.append(_truncate(f"{left} <-> {right} ({overlap_note})", width))
        if len(lines) >= limit:
            break
    return lines


def _drift_lines(sessions: list[dict[str, Any]], claims: list[dict[str, Any]], width: int, limit: int) -> list[str]:
    lines: list[str] = []
    for session in _active_sessions(sessions):
        drift = _session_drift(session, claims)
        if drift["status"] in {"aligned", "drift", "unscoped", "missing"}:
            lines.append(_session_drift_summary_line(session, drift, width))
        if len(lines) >= limit:
            break
    return lines


def _suggestion_lines(sessions: list[dict[str, Any]], claims: list[dict[str, Any]], width: int, limit: int) -> list[str]:
    lines: list[str] = []
    for session in _active_sessions(sessions):
        drift = _session_drift(session, claims)
        suggestions = _session_suggestions(session, drift, claims)
        if not suggestions:
            continue
        head = f"{session.get('agent', '?')}::{session.get('scope', '') or '(no scope)'}"
        lines.append(_truncate(f"{head} -> {suggestions[0]}", width))
        if len(lines) >= limit:
            break
    return lines


def _supervision_rows(home: Path, inbox_path: Path, width: int, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for agent in ("codex", "hermes"):
        status = _supervised_bridge_status(home, inbox_path, agent)
        lines.append(_bridge_supervision_line(status, width))
        buckets = status.get("pending_buckets", {}) if isinstance(status.get("pending_buckets"), dict) else {}
        lines.append(
            _truncate(
                f"  buckets fresh={int(buckets.get('fresh', 0) or 0)} warm={int(buckets.get('warm', 0) or 0)} stale={int(buckets.get('stale', 0) or 0)}",
                width,
            )
        )
        failure_class = str(status.get("failure_class", "")).strip()
        last_exit_at = str(status.get("last_exit_at", "")).strip()
        if failure_class or last_exit_at:
            fail_line = f"  last exit={status.get('last_exit_code', '') or '-'}"
            if last_exit_at:
                fail_line += f" at {_format_timestamp(last_exit_at)}"
            if failure_class:
                fail_line += f" failure={failure_class}"
            lines.append(_truncate(fail_line, width))
        recent_events = status.get("recent_events", [])
        if isinstance(recent_events, list) and recent_events:
            event = recent_events[-1]
            lines.append(
                _truncate(
                    f"  event [{_format_timestamp(str(event.get('timestamp', '')).strip())}] {event.get('type', '')}: {event.get('summary', '')}",
                    width,
                )
            )
        if len(lines) >= limit:
            break
    return lines[:limit]


def _ops_request_rows(request_records: list[dict[str, Any]], width: int, limit: int = 8) -> list[str]:
    rows: list[str] = []
    for record in request_records:
        if str(record.get("state", "")).strip() in {"stale", "blocked", "escalated"}:
            rows.append(_request_status_line(record, width))
        elif str(record.get("state", "")).strip() == "acknowledged":
            age_seconds = record.get("age_seconds")
            if isinstance(age_seconds, (int, float)) and age_seconds >= 60:
                rows.append(_request_status_line(record, width))
        if len(rows) >= limit:
            break
    return rows


def _ops_supervision_rows(home: Path, inbox_path: Path, width: int, limit: int = 8) -> list[str]:
    rows: list[str] = []
    for agent in ("codex", "hermes"):
        status = _supervised_bridge_status(home, inbox_path, agent)
        unhealthy = not bool(status.get("healthy")) or bool(status.get("failure_class")) or int(status.get("pending_count", 0) or 0) > 0
        if not unhealthy:
            continue
        rows.append(_bridge_supervision_line(status, width))
        failure_class = str(status.get("failure_class", "")).strip()
        if failure_class:
            rows.append(_truncate(f"  failure={failure_class}", width))
        oldest_pending_at = str(status.get("oldest_pending_at", "")).strip()
        if oldest_pending_at:
            rows.append(_truncate(f"  oldest pending={_format_timestamp(oldest_pending_at)}", width))
        if len(rows) >= limit:
            break
    return rows[:limit]


def _render_ops_dashboard(home: Path, inbox_path: Path, claims_path: Path, sessions_path: Path) -> str:
    messages = _read_messages(inbox_path)
    claims = _read_claims(claims_path)
    sessions = _read_sessions(sessions_path)
    request_records = _request_records(messages)
    size = _terminal_size()
    total_width = max(80, size.columns)
    left_width = max(38, (total_width - 2) // 2)
    right_width = max(38, total_width - left_width - 2)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    lines.append("AgentCodeHandoff Ops")
    lines.append("=" * total_width)
    lines.append(_truncate(f"Actionable issues only | {timestamp} | home {home}", total_width))
    lines.append("")

    lines.extend(
        _merge_columns(
            _render_panel("Bridge Problems", _ops_supervision_rows(home, inbox_path, left_width - 4, limit=8) or ["No bridge issues"], left_width, height=10),
            _render_panel("Stale Requests", _ops_request_rows(request_records, right_width - 4, limit=8) or ["No stale requests"], right_width, height=10),
        )
    )
    lines.append("")

    lines.extend(
        _merge_columns(
            _render_panel("Claim Conflicts", _conflict_lines(claims, left_width - 4, limit=8) or ["No claim conflicts"], left_width, height=10),
            _render_panel("Session Drift", _drift_lines(sessions, claims, right_width - 4, limit=8) or ["No session drift"], right_width, height=10),
        )
    )
    lines.append("")

    urgent_messages = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() in {"blocked", "escalated", "review"}
    ][-8:]
    recent_rows = [_message_summary_line(message, total_width - 4) for message in urgent_messages] or ["No urgent workflow events"]
    lines.extend(_render_panel("Urgent Workflow", recent_rows, total_width, height=10))
    lines.append("")
    lines.append("Ctrl-C to exit")
    return "\n".join(lines)


def _request_status_line(record: dict[str, Any], width: int) -> str:
    request = record["request"]
    state = str(record.get("state", "pending"))
    summary = str(request.get("summary", "")).strip() or "(no summary)"
    sender = str(request.get("from", "?")).strip() or "?"
    recipient = str(request.get("to", "?")).strip() or "?"
    age_seconds = record.get("age_seconds")
    age_text = "?"
    if isinstance(age_seconds, (int, float)):
        age_text = f"{int(age_seconds)}s"
    return _truncate(f"{sender}->{recipient} [{state}] {summary} ({age_text})", width)


def _stale_request_actions(record: dict[str, Any]) -> list[dict[str, Any]]:
    request = record["request"]
    state = str(record.get("state", "pending"))
    age_seconds = record.get("age_seconds")
    request_summary = str(request.get("summary", "")).strip()
    request_from = str(request.get("from", "")).strip()
    if state not in {"stale", "acknowledged"}:
        return []
    if state == "acknowledged":
        if isinstance(age_seconds, (int, float)) and age_seconds >= REQUEST_ESCALATE_SECONDS and _request_has_followup(record, "remind"):
            return [
                {
                    "type": "escalate",
                    "to_agent": request_from,
                    "message": f"Escalate acknowledged request `{request_summary}` back to {request_from} for human review.",
                }
            ]
        return [
            {
                "type": "remind",
                "to_agent": str(request.get("to", "")).strip(),
                "message": f"Follow up with {request.get('to', '')} on acknowledged request `{request_summary}`.",
            }
        ]
    files = [str(item) for item in request.get("files", []) if str(item).strip()]
    summary = str(request.get("summary", "")).strip()
    details = str(request.get("details", "")).strip()
    from_agent = str(request.get("from", "")).strip()
    original_to = str(request.get("to", "")).strip()
    target, _ = _recommend_agent(summary, details, files)
    if target.lower() == original_to.lower():
        target = "hermes" if original_to.lower() == "codex" else "codex"
    if isinstance(age_seconds, (int, float)) and age_seconds >= REQUEST_ESCALATE_SECONDS and _request_has_followup(record, "reroute"):
        return [
            {
                "type": "escalate",
                "to_agent": request_from,
                "message": f"Escalate stale request `{request_summary}` after automatic reroute failed to resolve it.",
            }
        ]
    return [
        {
            "type": "reroute",
            "to_agent": target,
            "message": f"Reroute stale request `{summary}` to {target}.",
        }
    ]


def _request_has_followup(record: dict[str, Any], action_type: str) -> bool:
    for followup in record.get("followups", []):
        summary = str(followup.get("summary", "")).strip().lower()
        if action_type == "remind" and summary.startswith("follow-up:"):
            return True
        if action_type == "reroute" and summary.startswith("rerouted:"):
            return True
    return False


def _apply_request_timeout_actions(
    inbox_path: Path,
    records: list[dict[str, Any]],
    *,
    owner_agent: str | None,
    dry_run: bool,
) -> list[str]:
    lines: list[str] = []
    for record in records:
        request = record["request"]
        if owner_agent and str(request.get("from", "")).strip().lower() != owner_agent.lower():
            continue
        actions = _stale_request_actions(record)
        if not actions:
            continue
        action = actions[0]
        action_type = str(action.get("type", "")).strip()
        if _request_has_followup(record, action_type):
            continue
        lines.append(_request_status_line(record, 120))
        lines.append(f"  plan: {action.get('message', '')}")
        if dry_run:
            lines.append("")
            continue
        request_id = str(request.get("id", "")).strip()
        if action_type == "remind":
            _send_record(
                inbox_path,
                from_agent=str(request.get("from", "")).strip() or "system",
                to_agent=str(action.get("to_agent", "")).strip(),
                role="request",
                task=str(request.get("task", "")).strip() or "follow-up",
                summary=f"Follow-up: {request.get('summary', '')}",
                details=f"Reminder on acknowledged request: {request.get('summary', '')}",
                files=request.get("files", []),
                request_id=request_id,
                derived_from_request_id=request_id,
            )
            lines.append(f"  sent reminder to {action.get('to_agent', '')}")
        elif action_type == "reroute":
            _send_record(
                inbox_path,
                from_agent=str(request.get("from", "")).strip() or "system",
                to_agent=str(action.get("to_agent", "")).strip(),
                role="request",
                task=str(request.get("task", "")).strip() or "rerouted request",
                summary=f"Rerouted: {request.get('summary', '')}",
                details=f"Original request to {request.get('to', '')} became stale and was rerouted.",
                files=request.get("files", []),
                request_id=request_id,
                derived_from_request_id=request_id,
            )
            lines.append(f"  rerouted to {action.get('to_agent', '')}")
        elif action_type == "escalate":
            _send_record(
                inbox_path,
                from_agent=str(request.get("from", "")).strip() or "system",
                to_agent=str(action.get("to_agent", "")).strip() or str(request.get("from", "")).strip() or "operator",
                role="escalated",
                task=str(request.get("task", "")).strip() or "request escalation",
                summary=f"Escalated: {request.get('summary', '')}",
                details="Automatic recovery exhausted safe actions; human review is recommended.",
                files=request.get("files", []),
                request_id=request_id,
            )
            lines.append(f"  escalated to {action.get('to_agent', '') or request.get('from', '')}")
        lines.append("")
    return lines


def _render_panel(title: str, rows: list[str], width: int, height: int | None = None) -> list[str]:
    inner_width = max(10, width - 4)
    top = f"+- {title} " + "-" * max(0, width - len(title) - 5) + "+"
    lines = [top[:width], "|" + " " * (width - 2) + "|"]

    content: list[str] = []
    for row in rows:
        wrapped = textwrap.wrap(str(row), width=inner_width) or [""]
        content.extend(wrapped)

    if height is not None:
        usable = max(0, height - 3)
        content = content[:usable]
        while len(content) < usable:
            content.append("")

    for row in content:
        lines.append(f"| {_truncate(row, inner_width).ljust(inner_width)} |")

    lines.append("+" + "-" * (width - 2) + "+")
    if height is not None and len(lines) > height:
        return lines[:height]
    return lines


def _merge_columns(left: list[str], right: list[str], gap: int = 2) -> list[str]:
    left_width = max((len(line) for line in left), default=0)
    right_width = max((len(line) for line in right), default=0)
    total = max(len(left), len(right))
    merged: list[str] = []
    for index in range(total):
        left_line = left[index] if index < len(left) else " " * left_width
        right_line = right[index] if index < len(right) else " " * right_width
        merged.append(left_line.ljust(left_width) + (" " * gap) + right_line)
    return merged


def _render_dashboard(home: Path, inbox_path: Path, claims_path: Path, sessions_path: Path) -> str:
    messages = _read_messages(inbox_path)
    claims = _read_claims(claims_path)
    sessions = _read_sessions(sessions_path)
    request_records = _request_records(messages)
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for message in messages:
        sender = str(message.get("from", "")).strip()
        if sender:
            latest_by_agent[sender] = message

    size = _terminal_size()
    total_width = max(80, size.columns)
    left_width = max(38, (total_width - 2) // 2)
    right_width = max(38, total_width - left_width - 2)
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")

    lines: list[str] = []
    lines.append("AgentCodeHandoff")
    lines.append("=" * total_width)
    lines.append(_truncate(f"Shared coordination for coding agents | {timestamp} | home {home}", total_width))
    lines.append("")

    bridge_rows = []
    for agent in ("codex", "hermes"):
        status = _supervised_bridge_status(home, inbox_path, agent)
        bridge_rows.append(_bridge_supervision_line(status, left_width - 4))
    if not bridge_rows:
        bridge_rows = ["No bridge state yet"]

    handoff_rows = []
    for agent in ("codex", "hermes"):
        message = latest_by_agent.get(agent)
        if message:
            handoff_rows.append(_truncate(f"{agent}: {message.get('summary', '')}", right_width - 4))
        else:
            handoff_rows.append(f"{agent}: waiting")
    summary_row = _merge_columns(
        _render_panel("Auto Bridges", bridge_rows, left_width, height=6),
        _render_panel("Latest Handoffs", handoff_rows, right_width, height=6),
    )
    lines.extend(summary_row)
    lines.append("")

    workflow_messages = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() in {"request", "done", "blocked", "review", "approved", "closed", "escalated"}
    ][-RECENT_WORKFLOW_MESSAGES:]
    open_claims = _open_claims(claims)
    active_sessions = _active_sessions(sessions)
    resolved_claims = _resolved_claims(claims)[-4:]
    workflow_rows = [_message_summary_line(message, left_width - 4) for message in workflow_messages] or ["No workflow events"]
    claim_rows = [_claim_summary_line(claim, right_width - 4) for claim in open_claims] or ["No open claims"]
    lines.extend(
        _merge_columns(
            _render_panel("Workflow", workflow_rows, left_width, height=10),
            _render_panel("Open Claims", claim_rows, right_width, height=10),
        )
    )
    lines.append("")

    request_rows = [_request_status_line(record, left_width - 4) for record in request_records[-4:]] or ["No tracked requests"]
    supervision_rows = _supervision_rows(home, inbox_path, right_width - 4, limit=8) or ["No supervision data"]
    lines.extend(
        _merge_columns(
            _render_panel("Requests", request_rows, left_width, height=8),
            _render_panel("Supervision", supervision_rows, right_width, height=8),
        )
    )
    lines.append("")

    conflict_rows = _conflict_lines(claims, left_width - 4, limit=4) or ["No claim conflicts"]
    lines.extend(
        _merge_columns(
            _render_panel("Conflicts", conflict_rows, left_width, height=8),
            _render_panel("Suggestions", _suggestion_lines(sessions, claims, right_width - 4, limit=4) or ["No suggestions"], right_width, height=8),
        )
    )
    lines.append("")

    drift_rows = _drift_lines(sessions, claims, left_width - 4, limit=4) or ["No session drift"]
    session_rows = [_session_summary_line(session, right_width - 4) for session in active_sessions[-4:]] or ["No active sessions"]
    lines.extend(
        _merge_columns(
            _render_panel("File Awareness", drift_rows, left_width, height=8),
            _render_panel("Active Sessions", session_rows, right_width, height=8),
        )
    )
    lines.append("")

    lines.extend(
        _render_panel("Recently Resolved", [_claim_summary_line(claim, total_width - 4) for claim in resolved_claims] or ["No resolved claims"], total_width, height=7)
    )
    lines.append("")

    recent_rows: list[str] = []
    for message in messages[-DASHBOARD_RECENT_MESSAGES:]:
        recent_rows.append(_truncate(
            f"[{_format_timestamp(str(message.get('timestamp', '')))}] {message.get('from', '?')}->{message.get('to', '?')} [{message.get('role', '')}] {message.get('summary', '')}",
            total_width - 4,
        ))
        details = str(message.get("details", "")).strip()
        if details:
            recent_rows.append(_truncate(f"  {details}", total_width - 4))
    recent_rows = recent_rows or ["No messages yet"]
    lines.extend(_render_panel("Recent Messages", recent_rows, total_width, height=min(14, max(8, size.lines - 28))))
    lines.append("")
    lines.append("Ctrl-C to exit")
    return "\n".join(lines)


def cmd_read(args: argparse.Namespace) -> None:
    messages = _filter_messages(_read_messages(args.inbox_path), args.agent, args.limit)
    for message in messages:
        _print_message(message)


def cmd_latest(args: argparse.Namespace) -> None:
    messages = _filter_messages(_read_messages(args.inbox_path), args.agent, max(1, args.limit))
    if messages:
        _print_message(messages[-1])


def cmd_send(args: argparse.Namespace) -> None:
    record = _send_record(
        args.inbox_path,
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        role=args.role,
        task=args.task,
        summary=args.summary,
        details=args.details,
        files=args.files,
        request_id=getattr(args, "request_id", ""),
    )
    _print_message(record)


def cmd_request(args: argparse.Namespace) -> None:
    record = _send_record(
        args.inbox_path,
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        role=args.role,
        task=args.task,
        summary=args.summary,
        details=args.details,
        files=args.files,
        request_id=getattr(args, "request_id", ""),
    )
    _print_message(record)


def _cmd_workflow_message(args: argparse.Namespace) -> None:
    record = _send_record(
        args.inbox_path,
        from_agent=args.from_agent,
        to_agent=args.to_agent,
        role=args.role,
        task=args.task,
        summary=args.summary,
        details=args.details,
        files=args.files,
        request_id=getattr(args, "request_id", ""),
    )
    _print_message(record)


def _cmd_request_action(args: argparse.Namespace) -> None:
    records = _request_records(_read_messages(args.inbox_path))
    record = _request_record_by_id(records, args.request_id)
    if not record:
        raise SystemExit(f"no request found for id {args.request_id}")
    request = record["request"]
    from_agent = args.from_agent or str(request.get("to", "")).strip() or "system"
    to_agent = args.to_agent or str(request.get("from", "")).strip() or "system"
    role = str(args.role).strip().lower()
    summary = args.summary or f"{role.title()}: {request.get('summary', '')}"
    details = args.details or f"{role.title()} request outcome for `{request.get('summary', '')}`."
    task = args.task or str(request.get("task", "")).strip() or "request outcome"
    outcome = _send_record(
        args.inbox_path,
        from_agent=from_agent,
        to_agent=to_agent,
        role=role,
        task=task,
        summary=summary,
        details=details,
        files=request.get("files", []),
        request_id=str(record.get("request_id", "")).strip(),
    )
    _print_message(outcome)


def cmd_request_resolve(args: argparse.Namespace) -> None:
    role_map = {
        "approve": "approved",
        "close": "closed",
        "escalate": "escalated",
    }
    args.role = role_map[args.action]
    _cmd_request_action(args)


def cmd_route(args: argparse.Namespace) -> None:
    files = _split_files(args.files or "")
    agent, meta = _recommend_agent(args.summary, args.details, files)
    print(f"recommended_agent: {agent}")
    print(f"codex_score: {meta['scores']['codex']}")
    print(f"hermes_score: {meta['scores']['hermes']}")
    if meta["reasons"][agent]:
        print("reasons:", ", ".join(meta["reasons"][agent]))


def cmd_dispatch(args: argparse.Namespace) -> None:
    files = _split_files(args.files or "")
    chosen = args.to_agent
    meta: dict[str, Any] | None = None
    rerouted = False
    if args.route == "smart":
        chosen, meta = _recommend_agent(args.summary, args.details, files)
    if chosen == args.from_agent and not args.allow_self_route:
        chosen = "hermes" if args.from_agent == "codex" else "codex"
        rerouted = True
    record = _write_message(
        args.inbox_path,
        {
            "from": args.from_agent,
            "to": chosen,
            "role": args.role,
            "task": args.task,
            "summary": args.summary,
            "details": args.details,
            "files": files,
        },
    )
    if meta is not None:
        print(f"routed_to: {chosen}")
        print(f"codex_score: {meta['scores']['codex']}")
        print(f"hermes_score: {meta['scores']['hermes']}")
        if meta["reasons"][chosen]:
            print("reasons:", ", ".join(meta["reasons"][chosen]))
        if rerouted:
            print("reroute_note: avoided sending the request back to the originating agent")
        print()
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
    sessions = _read_sessions(args.sessions_path)
    request_records = _request_records(messages)
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
    print("Supervised bridges")
    print()
    for agent in args.agents:
        _print_bridge_supervision(_supervised_bridge_status(args.home, args.inbox_path, agent))
    print("Requests")
    print()
    if not request_records:
        print("none")
        print()
    else:
        for record in request_records[-args.requests_limit:]:
            print(_request_status_line(record, 120))
            print(f"  request_id: {record.get('request_id', '')}")
            outcome = record.get("latest_outcome")
            if outcome:
                print(f"  outcome: {outcome.get('role', '')} :: {outcome.get('summary', '')}")
            elif record.get("latest_handoff"):
                handoff = record["latest_handoff"]
                print(f"  acknowledged: {handoff.get('summary', '')}")
            followups = record.get("followups", [])
            if followups:
                print(f"  followups: {len(followups)}")
            print()
    print("Workflow updates")
    print()
    workflow_messages = [
        message for message in messages
        if str(message.get("role", "")).strip().lower() in {"request", "done", "blocked", "review", "approved", "closed", "escalated"}
    ][-args.workflow_limit:]
    if not workflow_messages:
        print("none")
        print()
    else:
        for message in workflow_messages:
            _print_message(message)

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
    print("Recently resolved claims")
    print()
    resolved_claims = _resolved_claims(claims)[-args.resolved_limit:]
    if not resolved_claims:
        print("none")
        print()
    else:
        for claim in resolved_claims:
            _print_claim(claim)

    print("Active sessions")
    print()
    active_sessions = _active_sessions(sessions)
    if not active_sessions:
        print("none")
        print()
    else:
        for session in active_sessions[-args.sessions_limit:]:
            print(_session_summary_line(session, 120))
            print(f"  path: {session.get('worktree_path', '')}")
            print(f"  repo: {session.get('repo_root', '')}")
            print()

    print("File awareness")
    print()
    drift_found = False
    for session in active_sessions[-args.sessions_limit:]:
        drift = _session_drift(session, claims)
        print(_session_drift_summary_line(session, drift, 120))
        claim = drift.get("claim")
        if claim:
            print(f"  claim: {claim.get('scope', '')}")
        changed_files = drift.get("changed_files", [])
        if changed_files:
            print(f"  changed: {', '.join(changed_files[:6])}")
        unexpected_files = drift.get("unexpected_files", [])
        if unexpected_files:
            print(f"  outside claim: {', '.join(unexpected_files[:6])}")
        print()
        drift_found = True
    if not drift_found:
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
        "state": "open",
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
        claims = _open_claims(claims)
    claims = claims[-args.limit:]
    for claim in claims:
        _print_claim(claim)


def cmd_session_start(args: argparse.Namespace) -> None:
    repo_root = _repo_root(args.repo)
    scope_slug = _slugify(args.scope)
    branch = args.branch or f"ach/{args.agent}/{scope_slug}"
    base_ref = args.base_ref or _git_current_branch(repo_root)
    default_path = repo_root / ".worktrees" / f"{args.agent}-{scope_slug}"
    worktree_path = (args.path or default_path).expanduser()

    sessions = _read_sessions(args.sessions_path)
    existing_active = [
        session for session in _active_sessions(sessions)
        if str(session.get("agent", "")).lower() == args.agent.lower()
        and str(session.get("scope", "")) == args.scope
    ]
    if existing_active:
        raise SystemExit("an active session already exists for this agent and scope")
    if worktree_path.exists():
        raise SystemExit(f"worktree path already exists: {worktree_path}")

    worktree_path.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(repo_root, ["worktree", "add", "-b", branch, str(worktree_path), base_ref])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise SystemExit(detail or "failed to create worktree")

    session = {
        "id": f"session-{datetime.now(timezone.utc).timestamp():.6f}",
        "timestamp": _now_iso(),
        "agent": args.agent,
        "scope": args.scope,
        "branch": branch,
        "base_ref": base_ref,
        "repo_root": str(repo_root),
        "worktree_path": str(worktree_path),
        "state": "active",
        "claim_scope": args.claim_scope or "",
        "note": args.note,
    }
    sessions.append(session)
    _write_sessions(args.sessions_path, sessions)

    print(f"agent: {session['agent']}")
    print(f"scope: {session['scope']}")
    print(f"branch: {session['branch']}")
    print(f"base_ref: {session['base_ref']}")
    print(f"repo_root: {session['repo_root']}")
    print(f"worktree_path: {session['worktree_path']}")


def cmd_sessions(args: argparse.Namespace) -> None:
    sessions = _read_sessions(args.sessions_path)
    if args.agent:
        sessions = [session for session in sessions if str(session.get("agent", "")).lower() == args.agent.lower()]
    if not args.all:
        sessions = _active_sessions(sessions)
    sessions = sessions[-args.limit:]
    if not sessions:
        print("no sessions")
        return
    for session in sessions:
        print(_session_summary_line(session, 120))
        print(f"  path: {session.get('worktree_path', '')}")
        print(f"  repo: {session.get('repo_root', '')}")
        base_ref = str(session.get("base_ref", "")).strip()
        if base_ref:
            print(f"  base: {base_ref}")
        note = str(session.get("note", "")).strip()
        if note:
            print(f"  note: {note}")
        print()


def cmd_drift(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    sessions = _read_sessions(args.sessions_path)
    if args.agent:
        sessions = [session for session in sessions if str(session.get("agent", "")).lower() == args.agent.lower()]
    if not args.all:
        sessions = _active_sessions(sessions)
    sessions = sessions[-args.limit:]
    if not sessions:
        print("no sessions")
        return
    for session in sessions:
        drift = _session_drift(session, claims)
        print(_session_drift_summary_line(session, drift, 120))
        claim = drift.get("claim")
        if claim:
            print(f"  claim: {claim.get('scope', '')}")
        changed_files = drift.get("changed_files", [])
        if changed_files:
            print(f"  changed: {', '.join(changed_files[:8])}")
        else:
            print("  changed: none")
        unexpected_files = drift.get("unexpected_files", [])
        if unexpected_files:
            print(f"  outside claim: {', '.join(unexpected_files[:8])}")
        print()


def cmd_suggest(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    sessions = _read_sessions(args.sessions_path)
    if args.agent:
        sessions = [session for session in sessions if str(session.get("agent", "")).lower() == args.agent.lower()]
    if not args.all:
        sessions = _active_sessions(sessions)
    sessions = sessions[-args.limit:]
    if not sessions:
        print("no sessions")
        return
    for session in sessions:
        drift = _session_drift(session, claims)
        suggestions = _session_suggestions(session, drift, claims)
        remediations = _session_remediations(session, drift, claims)
        print(_session_drift_summary_line(session, drift, 120))
        for suggestion in suggestions:
            print(f"  suggest: {suggestion}")
        for remediation in remediations:
            action = str(remediation.get("type", "")).strip()
            if action and action not in {"noop", "manual"}:
                detail = str(remediation.get("message", "")).strip()
                print(f"  action: {action}")
                if detail:
                    print(f"    {detail}")
        print()


def cmd_requests(args: argparse.Namespace) -> None:
    records = _request_records(_read_messages(args.inbox_path))
    if args.agent:
        needle = args.agent.lower()
        records = [
            record for record in records
            if str(record["request"].get("from", "")).lower() == needle or str(record["request"].get("to", "")).lower() == needle
        ]
    records = records[-args.limit:]
    if not records:
        print("no requests")
        return
    for record in records:
        print(_request_status_line(record, 120))
        request = record["request"]
        print(f"  request_id: {record.get('request_id', '')}")
        print(f"  task: {request.get('task', '')}")
        outcome = record.get("latest_outcome")
        if outcome:
            print(f"  outcome: {outcome.get('role', '')} :: {outcome.get('summary', '')}")
        elif record.get("latest_handoff"):
            handoff = record["latest_handoff"]
            print(f"  acknowledged: {handoff.get('summary', '')}")
        followups = record.get("followups", [])
        if followups:
            print(f"  followups: {len(followups)}")
        print()


def cmd_request_sweep(args: argparse.Namespace) -> None:
    records = _request_records(_read_messages(args.inbox_path))
    stale_records = [record for record in records if str(record.get("state", "")) in {"stale", "acknowledged"}][-args.limit:]
    if not stale_records:
        print("no stale or acknowledged requests")
        return
    lines = _apply_request_timeout_actions(
        args.inbox_path,
        stale_records,
        owner_agent=args.agent,
        dry_run=args.dry_run,
    )
    if not lines:
        print("no actionable stale requests")
        return
    print("\n".join(lines))


def _find_session(sessions: list[dict[str, Any]], *, agent: str, scope: str) -> dict[str, Any] | None:
    for session in sessions:
        if str(session.get("agent", "")).lower() == agent.lower() and str(session.get("scope", "")) == scope:
            return session
    return None


def _expand_claim_files(claims: list[dict[str, Any]], *, agent: str, scope: str, files: list[str]) -> bool:
    updated = False
    for claim in claims:
        if str(claim.get("agent", "")).lower() == agent.lower() and str(claim.get("scope", "")) == scope:
            existing = [str(item) for item in claim.get("files", []) if str(item).strip()]
            for file_path in files:
                if file_path not in existing:
                    existing.append(file_path)
                    updated = True
            claim["files"] = existing
            if updated:
                claim["summary"] = str(claim.get("summary", "")).strip() or f"Expanded scope {scope}"
            break
    return updated


def cmd_remediate(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    sessions = _read_sessions(args.sessions_path)
    session = _find_session(sessions, agent=args.agent, scope=args.scope)
    if not session:
        raise SystemExit("no matching session")
    drift = _session_drift(session, claims)
    remediations = _session_remediations(session, drift, claims)
    actionable = [item for item in remediations if str(item.get("type", "")) in {"expand-claim", "handoff", "split-claim"}]
    if not actionable:
        for item in remediations:
            print(item.get("message", "no actionable remediation"))
        return

    chosen = actionable[0] if args.action == "auto" else next((item for item in actionable if item.get("type") == args.action), None)
    if not chosen:
        raise SystemExit(f"requested action {args.action} is not available for this session")

    action_type = str(chosen.get("type", ""))
    print(f"session: {args.agent}::{args.scope}")
    print(f"action: {action_type}")
    print(f"plan: {chosen.get('message', '')}")
    if args.dry_run:
        return

    if action_type == "expand-claim":
        claim_scope = str(chosen.get("claim_scope", "")).strip()
        files = [str(item) for item in chosen.get("files", []) if str(item).strip()]
        if not claim_scope or not files:
            raise SystemExit("expand-claim remediation is missing claim scope or files")
        updated = _expand_claim_files(claims, agent=args.agent, scope=claim_scope, files=files)
        if not updated:
            print("no claim changes were needed")
            return
        _write_claims(args.claims_path, claims)
        print(f"expanded claim {claim_scope} with: {', '.join(files)}")
        return

    if action_type == "handoff":
        to_agent = str(chosen.get("to_agent", "")).strip()
        to_scope = str(chosen.get("to_scope", "")).strip()
        files = [str(item) for item in chosen.get("files", []) if str(item).strip()]
        if not to_agent or not files:
            raise SystemExit("handoff remediation is missing agent or files")
        summary = f"Drift handoff for {', '.join(files)}"
        details = f"Session {args.agent}/{args.scope} touched files owned by {to_agent}"
        if to_scope:
            details += f" in claim {to_scope}"
        _send_record(
            args.inbox_path,
            from_agent=args.agent,
            to_agent=to_agent,
            role="handoff",
            task="drift remediation",
            summary=summary,
            details=details,
            files=files,
        )
        print(f"sent handoff to {to_agent} for: {', '.join(files)}")
        return

    if action_type == "split-claim":
        new_scope = str(chosen.get("scope", "")).strip()
        target_agent = str(chosen.get("agent", "")).strip() or args.agent
        files = [str(item) for item in chosen.get("files", []) if str(item).strip()]
        if not new_scope or not files:
            raise SystemExit("split-claim remediation is missing scope or files")
        new_claim = {
            "id": f"claim-{datetime.now(timezone.utc).timestamp():.6f}",
            "timestamp": _now_iso(),
            "agent": target_agent,
            "scope": new_scope,
            "summary": f"Split from {args.scope}",
            "files": files,
            "state": "open",
            "released": False,
        }
        claims.append(new_claim)
        _write_claims(args.claims_path, claims)
        print(f"created claim {new_scope} for {target_agent}: {', '.join(files)}")

        if target_agent != args.agent:
            _send_record(
                args.inbox_path,
                from_agent=args.agent,
                to_agent=target_agent,
                role="handoff",
                task="split remediation",
                summary=f"New split scope {new_scope}",
                details=f"Created split claim {new_scope} from session {args.scope}",
                files=files,
            )
            print(f"sent handoff to {target_agent} for new scope {new_scope}")
            return

        if args.create_session:
            repo_root = Path(str(session.get("repo_root", "")))
            start_args = argparse.Namespace(
                sessions_path=args.sessions_path,
                agent=target_agent,
                scope=new_scope,
                repo=repo_root,
                branch=None,
                base_ref=None,
                path=None,
                claim_scope=new_scope,
                note=f"Auto-created from split remediation of {args.scope}",
            )
            cmd_session_start(start_args)
        return


def cmd_session_end(args: argparse.Namespace) -> None:
    sessions = _read_sessions(args.sessions_path)
    updated = False
    for session in sessions:
        matches_agent = str(session.get("agent", "")).lower() == args.agent.lower()
        matches_scope = args.scope and str(session.get("scope", "")) == args.scope
        if matches_agent and (matches_scope or not args.scope) and str(session.get("state", "active")) == "active":
            repo_root = Path(str(session.get("repo_root", "")))
            worktree_path = Path(str(session.get("worktree_path", "")))
            if not args.keep_worktree:
                result = _run_git(repo_root, ["worktree", "remove", "--force", str(worktree_path)])
                if result.returncode != 0:
                    detail = (result.stderr or result.stdout or "").strip()
                    raise SystemExit(detail or f"failed to remove worktree {worktree_path}")
            session["state"] = "closed"
            session["closed_at"] = _now_iso()
            session["close_note"] = args.note
            session["kept_worktree"] = bool(args.keep_worktree)
            updated = True
    _write_sessions(args.sessions_path, sessions)
    if not updated:
        print("no matching active sessions")
    else:
        print("sessions closed")


def cmd_resolve(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    updated = False
    for claim in claims:
        matches_agent = str(claim.get("agent", "")).lower() == args.agent.lower()
        matches_scope = args.scope and str(claim.get("scope", "")) == args.scope
        if matches_agent and (matches_scope or not args.scope) and str(claim.get("state", "open")) == "open":
            claim["state"] = args.status
            claim["released"] = True
            claim["released_at"] = _now_iso()
            claim["resolved_at"] = claim["released_at"]
            claim["resolution_note"] = args.note
            updated = True
    _write_claims(args.claims_path, claims)
    if not updated:
        print("no matching open claims")
    else:
        print(f"claims marked {args.status}")


def cmd_release(args: argparse.Namespace) -> None:
    claims = _read_claims(args.claims_path)
    updated = False
    for claim in claims:
        matches_agent = str(claim.get("agent", "")).lower() == args.agent.lower()
        matches_scope = args.scope and str(claim.get("scope", "")) == args.scope
        if matches_agent and (matches_scope or not args.scope) and not claim.get("released"):
            claim["state"] = "released"
            claim["released"] = True
            claim["released_at"] = _now_iso()
            claim["resolved_at"] = claim["released_at"]
            updated = True
    _write_claims(args.claims_path, claims)
    if not updated:
        print("no matching open claims")
    else:
        print("claims released")


def cmd_auto(args: argparse.Namespace) -> None:
    state_path = _automation_state_path(args.home, args.agent)
    lock_path = _bridge_lock_path(args.home, args.agent)
    state = _read_automation_state(state_path)
    seen_ids = {str(item) for item in state.get("seen_ids", [])}
    if args.supervised:
        existing = _read_bridge_lock(lock_path)
        existing_pid = int(existing.get("pid", 0) or 0)
        if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
            raise SystemExit(f"another supervised bridge is already active for {args.agent} (pid {existing_pid})")

        payload = {
            "agent": args.agent,
            "pid": os.getpid(),
            "supervisor_pid": int(existing.get("supervisor_pid", 0) or 0),
            "repo": str(args.repo),
            "started_at": existing.get("started_at") or _now_iso(),
            "last_heartbeat_at": _now_iso(),
            "interval": args.interval,
            "claim_on_files": bool(args.claim_on_files),
            "log_path": args.log_path,
            "mode": "supervised",
            "restart_count": int(existing.get("restart_count", 0) or 0),
            "backoff_seconds": float(existing.get("backoff_seconds", 0.0) or 0.0),
            "failure_class": str(existing.get("failure_class", "")).strip(),
            "recent_events": existing.get("recent_events", []),
            "max_restarts": int(existing.get("max_restarts", getattr(args, "max_restarts", 0)) or getattr(args, "max_restarts", 0)),
            "cool_off_seconds": float(existing.get("cool_off_seconds", getattr(args, "cool_off_seconds", 0.0)) or getattr(args, "cool_off_seconds", 0.0)),
        }
        _write_bridge_lock(lock_path, payload)

    try:
        while True:
            _write_automation_state(state_path, state)
            if args.supervised:
                lock = _read_bridge_lock(lock_path)
                lock.update(
                    {
                        "agent": args.agent,
                        "pid": os.getpid(),
                        "supervisor_pid": int(lock.get("supervisor_pid", 0) or 0),
                        "repo": str(args.repo),
                        "last_heartbeat_at": _now_iso(),
                        "interval": args.interval,
                        "claim_on_files": bool(args.claim_on_files),
                        "log_path": args.log_path,
                        "mode": "supervised",
                        "max_restarts": int(lock.get("max_restarts", getattr(args, "max_restarts", 0)) or getattr(args, "max_restarts", 0)),
                        "cool_off_seconds": float(lock.get("cool_off_seconds", getattr(args, "cool_off_seconds", 0.0)) or getattr(args, "cool_off_seconds", 0.0)),
                    }
                )
                if not lock.get("started_at"):
                    lock["started_at"] = _now_iso()
                _write_bridge_lock(lock_path, lock)

            state["last_poll_at"] = _now_iso()
            _write_automation_state(state_path, state)
            messages = _read_messages(args.inbox_path)
            pending = _pending_messages_for_agent(messages, args.agent, seen_ids)
            for message in pending:
                message_id = str(message.get("id", "")).strip()
                prompt = _agent_prompt(args.agent, args.repo, message)
                try:
                    response = _run_auto_agent(args.agent, prompt, args.repo)
                    state["last_error"] = ""
                except Exception as exc:
                    if args.verbose:
                        print(f"auto-reply error for {message_id}: {exc}", file=sys.stderr)
                    state["last_error"] = str(exc)[:500]
                    seen_ids.add(message_id)
                    state["seen_ids"] = sorted(seen_ids)
                    _write_automation_state(state_path, state)
                    continue

                summary = str(response.get("summary", "")).strip() or f"{args.agent} reply"
                details = str(response.get("details", "")).strip()
                files = response.get("files") if isinstance(response.get("files"), list) else []
                normalized_files = [str(item) for item in files if str(item).strip()]
                if args.claim_on_files and normalized_files:
                    claims = _read_claims(args.claims_path)
                    scope = f"{args.claim_scope_prefix}{message_id}"
                    claim = {
                        "id": f"claim-{datetime.now(timezone.utc).timestamp():.6f}",
                        "timestamp": _now_iso(),
                        "agent": args.agent,
                        "scope": scope,
                        "summary": summary,
                        "files": normalized_files,
                        "released": False,
                    }
                    claims.append(claim)
                    _write_claims(args.claims_path, claims)
                record = _write_message(
                    args.inbox_path,
                    {
                        "from": args.agent,
                        "to": str(message.get("from", "")).strip() or "codex",
                        "role": "handoff",
                        "task": str(message.get("task", "")).strip() or "auto-response",
                        "summary": summary,
                        "details": details,
                        "files": normalized_files,
                    },
                )
                if args.verbose:
                    _print_message(record)
                seen_ids.add(message_id)
                state["seen_ids"] = sorted(seen_ids)
                state["last_reply_at"] = _now_iso()
                _write_automation_state(state_path, state)

            if args.once:
                return
            time.sleep(args.interval)
    finally:
        if args.supervised:
            current = _read_bridge_lock(lock_path)
            current_pid = int(current.get("pid", 0) or 0)
            supervisor_pid = int(current.get("supervisor_pid", 0) or 0)
            if current_pid == os.getpid() and not supervisor_pid:
                _remove_bridge_lock(lock_path)


def cmd_auto_status(args: argparse.Namespace) -> None:
    print("Auto bridges")
    print()
    for agent in args.agents:
        state = _read_automation_state(_automation_state_path(args.home, agent))
        _print_bridge_state(agent, state)


def cmd_bridge_status(args: argparse.Namespace) -> None:
    print("Supervised bridges")
    print()
    for agent in args.agents:
        status = _supervised_bridge_status(args.home, args.inbox_path, agent)
        _print_bridge_supervision(status)


def cmd_bridge_profiles(args: argparse.Namespace) -> None:
    agents = args.agents or ["codex", "hermes"]
    profiles = []
    for agent in agents:
        profile = _read_bridge_profile(_bridge_profile_path(args.home, agent))
        if profile:
            profiles.append(profile)
    if not profiles:
        print("no saved bridge profiles")
        return
    for profile in profiles:
        print(_bridge_profile_summary_line(profile, 120))
        updated_at = str(profile.get("updated_at", "")).strip()
        if updated_at:
            print(f"  updated: {_format_timestamp(updated_at)}")
        print(f"  repo: {profile.get('repo', '')}")
        print(
            f"  interval: {float(profile.get('interval', 2.0) or 2.0):.1f}s"
            f" | auto_sweep: {bool(profile.get('auto_sweep', False))}"
            f" | sweep_interval: {float(profile.get('sweep_interval', 30.0) or 30.0):.1f}s"
        )
        print(
            f"  restart policy: max={int(profile.get('max_restarts', 5) or 5)}"
            f" window={float(profile.get('cool_off_seconds', BRIDGE_COOL_OFF_SECONDS) or BRIDGE_COOL_OFF_SECONDS):.0f}s"
        )
        print()


def cmd_bridge_profile_show(args: argparse.Namespace) -> None:
    profile = _read_bridge_profile(_bridge_profile_path(args.home, args.agent))
    if not profile:
        raise SystemExit(f"no saved profile for {args.agent}")
    print(json.dumps(profile, indent=2))


def cmd_bridge_profile_delete(args: argparse.Namespace) -> None:
    path = _bridge_profile_path(args.home, args.agent)
    if not path.exists():
        print(f"no saved profile for {args.agent}")
        return
    path.unlink()
    print(f"deleted saved profile for {args.agent}")


def cmd_bridge_start(args: argparse.Namespace) -> None:
    status = _supervised_bridge_status(args.home, args.inbox_path, args.agent)
    if status["alive"]:
        print(f"bridge already running for {args.agent} (pid {status['pid']})")
        return

    logs_dir = args.home / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{args.agent}-bridge.log"
    args.log_path = str(log_path)
    log_handle = log_path.open("a", encoding="utf-8")
    command = _supervisor_command_args(args, args.agent, log_path)
    process = subprocess.Popen(
        command,
        cwd=args.home,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        text=True,
    )
    log_handle.close()
    lock_path = _bridge_lock_path(args.home, args.agent)
    existing = _read_bridge_lock(lock_path)
    history = existing.get("recent_events", [])
    if not isinstance(history, list):
        history = []
    pending_event = getattr(args, "pending_event", None)
    if isinstance(pending_event, dict):
        history.append(pending_event)
    history.append(
        {
            "timestamp": _now_iso(),
            "type": "start",
            "summary": f"Started supervised bridge for {args.agent}",
            "detail": f"supervisor pid {process.pid}",
        }
    )
    _write_bridge_lock(
        lock_path,
        {
            "agent": args.agent,
            "pid": 0,
            "supervisor_pid": process.pid,
            "repo": str(args.repo),
            "started_at": _now_iso(),
            "last_heartbeat_at": "",
            "interval": args.interval,
            "claim_on_files": bool(args.claim_on_files),
            "log_path": str(log_path),
            "mode": "supervised",
            "restart_count": 0,
            "backoff_seconds": 0.0,
            "failure_class": "",
            "auto_sweep": bool(args.auto_sweep),
            "sweep_interval": float(args.sweep_interval),
            "last_sweep_at": "",
            "max_restarts": int(args.max_restarts),
            "cool_off_seconds": float(args.cool_off_seconds),
            "recent_events": history[-BRIDGE_EVENT_HISTORY:],
        },
    )
    _save_bridge_profile(
        args.home,
        {
            "agent": args.agent,
            "repo": str(args.repo),
            "interval": args.interval,
            "claim_on_files": bool(args.claim_on_files),
            "claim_scope_prefix": args.claim_scope_prefix,
            "auto_sweep": bool(args.auto_sweep),
            "sweep_interval": float(args.sweep_interval),
            "max_restarts": int(args.max_restarts),
            "cool_off_seconds": float(args.cool_off_seconds),
        },
    )
    print(f"started {args.agent} bridge")
    print(f"supervisor pid: {process.pid}")
    print(f"log: {log_path}")


def cmd_bridge_stop(args: argparse.Namespace) -> None:
    lock_path = _bridge_lock_path(args.home, args.agent)
    lock = _read_bridge_lock(lock_path)
    pid = int(lock.get("supervisor_pid", 0) or 0) or int(lock.get("pid", 0) or 0)
    if not pid:
        print("no supervised bridge lock found")
        return
    if not _pid_alive(pid):
        _remove_bridge_lock(lock_path)
        print("bridge process was not running; removed stale lock")
        return
    _append_bridge_event(lock_path, "stop", f"Stopping supervised bridge for {args.agent}", detail=f"pid {pid}")
    _signal_pid(pid, signal.SIGTERM)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            _remove_bridge_lock(lock_path)
            print(f"stopped {args.agent} bridge")
            return
        time.sleep(0.2)
    if args.force:
        _signal_pid(pid, signal.SIGKILL)
        time.sleep(0.2)
        _remove_bridge_lock(lock_path)
        print(f"killed {args.agent} bridge")
        return
    raise SystemExit(f"bridge {args.agent} did not stop within {args.timeout:.1f}s; rerun with --force")


def cmd_bridge_restart(args: argparse.Namespace) -> None:
    restart_event = {
        "timestamp": _now_iso(),
        "type": "restart",
        "summary": f"Restart requested for {args.agent}",
        "detail": "",
    }
    stop_args = argparse.Namespace(
        home=args.home,
        agent=args.agent,
        timeout=args.timeout,
        force=True,
    )
    cmd_bridge_stop(stop_args)
    start_args = argparse.Namespace(
        home=args.home,
        inbox_path=args.inbox_path,
        claims_path=args.claims_path,
        sessions_path=args.sessions_path,
        repo=args.repo,
        agent=args.agent,
        interval=args.interval,
        claim_on_files=args.claim_on_files,
        claim_scope_prefix=args.claim_scope_prefix,
        verbose=args.verbose,
        auto_sweep=args.auto_sweep,
        sweep_interval=args.sweep_interval,
        max_restarts=args.max_restarts,
        cool_off_seconds=args.cool_off_seconds,
        pending_event=restart_event,
    )
    cmd_bridge_start(start_args)


def cmd_bridge_recover(args: argparse.Namespace) -> None:
    agents = args.agents or ["codex", "hermes"]
    recovered = False
    for agent in agents:
        status = _supervised_bridge_status(args.home, args.inbox_path, agent)
        should_restart = args.force or (not bool(status.get("healthy")) and (bool(status.get("failure_class")) or not bool(status.get("alive")) or bool(status.get("lock", {}).get("paused", False))))
        if not should_restart:
            print(f"{agent}: no recovery needed")
            continue
        lock = status.get("lock", {}) if isinstance(status.get("lock"), dict) else {}
        profile = status.get("profile", {}) if isinstance(status.get("profile"), dict) else {}
        repo_value = (
            str(lock.get("repo", "")).strip()
            or str(profile.get("repo", "")).strip()
            or str(args.repo)
        )
        repo = Path(repo_value)
        common_args = argparse.Namespace(
            home=args.home,
            inbox_path=args.inbox_path,
            claims_path=args.claims_path,
            sessions_path=args.sessions_path,
            agent=agent,
            repo=repo,
            interval=float(lock.get("interval", profile.get("interval", args.interval)) or profile.get("interval", args.interval) or args.interval),
            claim_on_files=bool(lock.get("claim_on_files", profile.get("claim_on_files", args.claim_on_files))),
            claim_scope_prefix=str(lock.get("claim_scope_prefix", profile.get("claim_scope_prefix", args.claim_scope_prefix)) or profile.get("claim_scope_prefix", args.claim_scope_prefix) or args.claim_scope_prefix),
            verbose=args.verbose,
            timeout=args.timeout,
            auto_sweep=bool(lock.get("auto_sweep", profile.get("auto_sweep", args.auto_sweep))),
            sweep_interval=float(lock.get("sweep_interval", profile.get("sweep_interval", args.sweep_interval)) or profile.get("sweep_interval", args.sweep_interval) or args.sweep_interval),
            max_restarts=int(lock.get("max_restarts", profile.get("max_restarts", args.max_restarts)) or profile.get("max_restarts", args.max_restarts) or args.max_restarts),
            cool_off_seconds=float(lock.get("cool_off_seconds", profile.get("cool_off_seconds", args.cool_off_seconds)) or profile.get("cool_off_seconds", args.cool_off_seconds) or args.cool_off_seconds),
        )
        if lock and (int(lock.get("supervisor_pid", 0) or 0) or int(lock.get("pid", 0) or 0)):
            common_args.timeout = args.timeout
            cmd_bridge_restart(common_args)
        else:
            cmd_bridge_start(common_args)
        recovered = True
    if not recovered and args.fail_if_idle:
        raise SystemExit("no bridges required recovery")


def cmd_supervise(args: argparse.Namespace) -> None:
    lock_path = _bridge_lock_path(args.home, args.agent)
    existing = _read_bridge_lock(lock_path)
    existing_pid = int(existing.get("supervisor_pid", 0) or 0)
    if existing_pid and existing_pid != os.getpid() and _pid_alive(existing_pid):
        raise SystemExit(f"supervisor already running for {args.agent} (pid {existing_pid})")

    restart_count = int(existing.get("restart_count", 0) or 0)
    while True:
        logs_dir = args.home / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{args.agent}-bridge.log"
        command = _bridge_command_args(args, args.agent)
        try:
            with log_path.open("a", encoding="utf-8") as log_handle:
                process = subprocess.Popen(
                    command,
                    cwd=args.repo,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    text=True,
                )
        except Exception as exc:
            restart_count += 1
            failure_class = _classify_error(str(exc)) or "repo"
            current = _read_bridge_lock(lock_path)
            current.update(
                {
                    "pid": 0,
                    "supervisor_pid": os.getpid(),
                    "last_exit_code": -1,
                    "last_exit_at": _now_iso(),
                    "failure_class": failure_class,
                    "restart_count": restart_count,
                    "backoff_seconds": 0.0,
                    "last_error": str(exc)[:500],
                    "paused": True,
                    "max_restarts": int(args.max_restarts),
                    "cool_off_seconds": float(args.cool_off_seconds),
                    "repo": str(args.repo),
                    "log_path": str(log_path),
                }
            )
            _write_bridge_lock(lock_path, current)
            _append_bridge_event(lock_path, "startup-failed", f"Supervisor could not start {args.agent}", detail=str(exc)[:240])
            return

        _write_bridge_lock(
            lock_path,
            {
                "agent": args.agent,
                "pid": process.pid,
                "supervisor_pid": os.getpid(),
                "repo": str(args.repo),
                "started_at": existing.get("started_at") or _now_iso(),
                "last_heartbeat_at": "",
                "interval": args.interval,
                "claim_on_files": bool(args.claim_on_files),
                "log_path": str(log_path),
                "mode": "supervised",
                "restart_count": restart_count,
                "backoff_seconds": 0.0,
                "failure_class": "",
                "auto_sweep": bool(args.auto_sweep),
                "sweep_interval": float(args.sweep_interval),
                "last_sweep_at": str(existing.get("last_sweep_at", "")).strip(),
                "max_restarts": int(args.max_restarts),
                "cool_off_seconds": float(args.cool_off_seconds),
                "recent_events": existing.get("recent_events", []),
            },
        )
        _append_bridge_event(lock_path, "child-start", f"Bridge child started for {args.agent}", detail=f"pid {process.pid}")
        last_sweep_monotonic = time.monotonic()
        while True:
            return_code = process.poll()
            if return_code is not None:
                break
            if args.auto_sweep and args.sweep_interval > 0 and (time.monotonic() - last_sweep_monotonic) >= args.sweep_interval:
                records = _request_records(_read_messages(args.inbox_path))
                sweep_lines = _apply_request_timeout_actions(
                    args.inbox_path,
                    records,
                    owner_agent=args.agent,
                    dry_run=False,
                )
                current = _read_bridge_lock(lock_path)
                current.update(
                    {
                        "agent": args.agent,
                        "supervisor_pid": os.getpid(),
                        "auto_sweep": True,
                        "sweep_interval": float(args.sweep_interval),
                        "last_sweep_at": _now_iso(),
                    }
                )
                _write_bridge_lock(lock_path, current)
                if sweep_lines:
                    _append_bridge_event(lock_path, "sweep", f"Automatic sweep applied {max(1, len(sweep_lines) // 3)} action(s)", detail=sweep_lines[0])
                last_sweep_monotonic = time.monotonic()
            time.sleep(min(1.0, max(0.2, args.interval)))

        state = _read_automation_state(_automation_state_path(args.home, args.agent))
        last_error = str(state.get("last_error", "")).strip()
        failure_class = _classify_error(last_error)
        if return_code == 0 and not args.always_restart:
            current = _read_bridge_lock(lock_path)
            current.update(
                {
                    "pid": 0,
                    "supervisor_pid": os.getpid(),
                    "last_exit_code": return_code,
                    "last_exit_at": _now_iso(),
                    "failure_class": "",
                    "backoff_seconds": 0.0,
                    "paused": False,
                }
            )
            _write_bridge_lock(lock_path, current)
            _append_bridge_event(lock_path, "child-exit", f"{args.agent} bridge exited cleanly", detail=f"exit code {return_code}")
            return

        restart_count += 1
        backoff = min(BRIDGE_RESTART_MAX_DELAY, BRIDGE_RESTART_BASE_DELAY * (2 ** max(0, restart_count - 1)))
        hard_failure = _is_hard_failure(failure_class)
        recent_restart_times = _bridge_recent_restart_times(_read_bridge_lock(lock_path))
        now = datetime.now(timezone.utc)
        within_window = [
            stamp for stamp in recent_restart_times
            if (now - stamp).total_seconds() <= float(args.cool_off_seconds)
        ]
        exceeded_restart_cap = int(args.max_restarts) > 0 and len(within_window) >= int(args.max_restarts)
        if exceeded_restart_cap:
            hard_failure = True
            if not failure_class:
                failure_class = "restart-limit"
        current = _read_bridge_lock(lock_path)
        current.update(
            {
                "pid": 0,
                "supervisor_pid": os.getpid(),
                "last_exit_code": return_code,
                "last_exit_at": _now_iso(),
                "failure_class": failure_class,
                "restart_count": restart_count,
                "backoff_seconds": backoff,
                "last_error": last_error,
                "paused": hard_failure,
                "max_restarts": int(args.max_restarts),
                "cool_off_seconds": float(args.cool_off_seconds),
            }
        )
        _write_bridge_lock(lock_path, current)
        _append_bridge_event(lock_path, "child-exit", f"{args.agent} bridge exited", detail=f"exit {return_code} failure={failure_class or 'none'} restart={restart_count}")
        if hard_failure and not args.always_restart:
            if args.verbose:
                print(f"{args.agent} bridge entered paused state due to hard failure: {failure_class}", file=sys.stderr)
            detail = f"hard failure {failure_class}"
            if exceeded_restart_cap:
                detail = f"restart cap reached: {len(within_window)} exits within {float(args.cool_off_seconds):.0f}s"
            _append_bridge_event(lock_path, "paused", f"{args.agent} bridge paused", detail=detail)
            return
        if args.verbose:
            print(f"{args.agent} bridge exited with {return_code}; restarting in {backoff:.1f}s", file=sys.stderr)
        time.sleep(backoff)


def cmd_dashboard(args: argparse.Namespace) -> None:
    while True:
        output = (
            _render_ops_dashboard(args.home, args.inbox_path, args.claims_path, args.sessions_path)
            if args.view == "ops"
            else _render_dashboard(args.home, args.inbox_path, args.claims_path, args.sessions_path)
        )
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(output + "\n")
        sys.stdout.flush()
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
    print(f"sessions: {args.sessions_path}")
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
        (f"sessions file: {args.sessions_path}", args.sessions_path.exists(), "present" if args.sessions_path.exists() else "missing"),
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
        (
            f"sessions writable: {args.sessions_path}",
            args.sessions_path.exists() and os.access(args.sessions_path, os.W_OK),
            "yes" if args.sessions_path.exists() and os.access(args.sessions_path, os.W_OK) else "no",
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
    parser.add_argument("--sessions-path", type=Path, default=DEFAULT_SESSIONS_PATH, help="shared session state file path")
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
    auto_parser.add_argument("--claim-on-files", action="store_true", help="create a claim automatically when the agent reply includes files")
    auto_parser.add_argument("--claim-scope-prefix", default="auto-", help="scope prefix used for auto-generated claims")
    auto_parser.add_argument("--supervised", action="store_true", help=argparse.SUPPRESS)
    auto_parser.add_argument("--log-path", default="", help=argparse.SUPPRESS)
    auto_parser.add_argument("--max-restarts", type=int, default=0, help=argparse.SUPPRESS)
    auto_parser.add_argument("--cool-off-seconds", type=float, default=0.0, help=argparse.SUPPRESS)
    auto_parser.set_defaults(func=cmd_auto)

    supervise_parser = subparsers.add_parser("supervise", help=argparse.SUPPRESS)
    supervise_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    supervise_parser.add_argument("--repo", type=Path, default=Path.cwd())
    supervise_parser.add_argument("--interval", type=float, default=2.0)
    supervise_parser.add_argument("--claim-on-files", action="store_true")
    supervise_parser.add_argument("--claim-scope-prefix", default="auto-")
    supervise_parser.add_argument("--verbose", action="store_true")
    supervise_parser.add_argument("--log-path", default="")
    supervise_parser.add_argument("--always-restart", action="store_true")
    supervise_parser.add_argument("--auto-sweep", action="store_true", help="periodically recover stale requests owned by this agent")
    supervise_parser.add_argument("--sweep-interval", type=float, default=30.0, help="seconds between automatic stale-request sweeps")
    supervise_parser.add_argument("--max-restarts", type=int, default=5, help="pause the supervisor after this many exits within the cool-off window")
    supervise_parser.add_argument("--cool-off-seconds", type=float, default=BRIDGE_COOL_OFF_SECONDS, help="restart counting window for max-restarts")
    supervise_parser.set_defaults(func=cmd_supervise)

    auto_status_parser = subparsers.add_parser("auto-status", help="show whether Codex and Hermes auto bridges appear alive")
    auto_status_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    auto_status_parser.set_defaults(func=cmd_auto_status)

    bridge_status_parser = subparsers.add_parser("bridge-status", help="show supervised bridge health, pid, and pending requests")
    bridge_status_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    bridge_status_parser.set_defaults(func=cmd_bridge_status)

    bridge_profiles_parser = subparsers.add_parser("bridge-profiles", help="list saved bridge profiles")
    bridge_profiles_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    bridge_profiles_parser.set_defaults(func=cmd_bridge_profiles)

    bridge_profile_show_parser = subparsers.add_parser("bridge-profile-show", help="show the saved profile for one agent")
    bridge_profile_show_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    bridge_profile_show_parser.set_defaults(func=cmd_bridge_profile_show)

    bridge_profile_delete_parser = subparsers.add_parser("bridge-profile-delete", help="delete the saved profile for one agent")
    bridge_profile_delete_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    bridge_profile_delete_parser.set_defaults(func=cmd_bridge_profile_delete)

    bridge_start_parser = subparsers.add_parser("bridge-start", help="start a supervised background bridge for an agent")
    bridge_start_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    bridge_start_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo working directory for the bridge")
    bridge_start_parser.add_argument("--interval", type=float, default=2.0)
    bridge_start_parser.add_argument("--claim-on-files", action="store_true")
    bridge_start_parser.add_argument("--claim-scope-prefix", default="auto-")
    bridge_start_parser.add_argument("--verbose", action="store_true")
    bridge_start_parser.add_argument("--auto-sweep", action="store_true", help="periodically recover stale requests owned by this agent")
    bridge_start_parser.add_argument("--sweep-interval", type=float, default=30.0, help="seconds between automatic stale-request sweeps")
    bridge_start_parser.add_argument("--max-restarts", type=int, default=5, help="pause the supervisor after this many exits within the cool-off window")
    bridge_start_parser.add_argument("--cool-off-seconds", type=float, default=BRIDGE_COOL_OFF_SECONDS, help="restart counting window for max-restarts")
    bridge_start_parser.set_defaults(func=cmd_bridge_start)

    bridge_stop_parser = subparsers.add_parser("bridge-stop", help="stop a supervised background bridge for an agent")
    bridge_stop_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    bridge_stop_parser.add_argument("--timeout", type=float, default=3.0)
    bridge_stop_parser.add_argument("--force", action="store_true")
    bridge_stop_parser.set_defaults(func=cmd_bridge_stop)

    bridge_restart_parser = subparsers.add_parser("bridge-restart", help="restart a supervised background bridge for an agent")
    bridge_restart_parser.add_argument("--agent", required=True, choices=["codex", "hermes"])
    bridge_restart_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo working directory for the bridge")
    bridge_restart_parser.add_argument("--interval", type=float, default=2.0)
    bridge_restart_parser.add_argument("--claim-on-files", action="store_true")
    bridge_restart_parser.add_argument("--claim-scope-prefix", default="auto-")
    bridge_restart_parser.add_argument("--verbose", action="store_true")
    bridge_restart_parser.add_argument("--timeout", type=float, default=3.0)
    bridge_restart_parser.add_argument("--auto-sweep", action="store_true", help="periodically recover stale requests owned by this agent")
    bridge_restart_parser.add_argument("--sweep-interval", type=float, default=30.0, help="seconds between automatic stale-request sweeps")
    bridge_restart_parser.add_argument("--max-restarts", type=int, default=5, help="pause the supervisor after this many exits within the cool-off window")
    bridge_restart_parser.add_argument("--cool-off-seconds", type=float, default=BRIDGE_COOL_OFF_SECONDS, help="restart counting window for max-restarts")
    bridge_restart_parser.set_defaults(func=cmd_bridge_restart)

    bridge_recover_parser = subparsers.add_parser("bridge-recover", help="restart paused or down supervised bridges using their last known settings")
    bridge_recover_parser.add_argument("--agents", nargs="+", default=["codex", "hermes"])
    bridge_recover_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="fallback repo if a bridge has no saved repo")
    bridge_recover_parser.add_argument("--interval", type=float, default=2.0)
    bridge_recover_parser.add_argument("--claim-on-files", action="store_true")
    bridge_recover_parser.add_argument("--claim-scope-prefix", default="auto-")
    bridge_recover_parser.add_argument("--verbose", action="store_true")
    bridge_recover_parser.add_argument("--timeout", type=float, default=3.0)
    bridge_recover_parser.add_argument("--auto-sweep", action="store_true")
    bridge_recover_parser.add_argument("--sweep-interval", type=float, default=30.0)
    bridge_recover_parser.add_argument("--max-restarts", type=int, default=5)
    bridge_recover_parser.add_argument("--cool-off-seconds", type=float, default=BRIDGE_COOL_OFF_SECONDS)
    bridge_recover_parser.add_argument("--force", action="store_true", help="restart even if the bridge does not appear degraded")
    bridge_recover_parser.add_argument("--fail-if-idle", action="store_true", help="exit non-zero when no bridges needed recovery")
    bridge_recover_parser.set_defaults(func=cmd_bridge_recover)

    dashboard_parser = subparsers.add_parser("dashboard", help="render a live terminal dashboard for handoffs, claims, and bridge health")
    dashboard_parser.add_argument("--interval", type=float, default=2.0)
    dashboard_parser.add_argument("--once", action="store_true")
    dashboard_parser.add_argument("--view", choices=["full", "ops"], default="full")
    dashboard_parser.set_defaults(func=cmd_dashboard)

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
    status_parser.add_argument("--workflow-limit", type=int, default=6)
    status_parser.add_argument("--resolved-limit", type=int, default=5)
    status_parser.add_argument("--sessions-limit", type=int, default=5)
    status_parser.add_argument("--requests-limit", type=int, default=6)
    status_parser.set_defaults(func=cmd_status)

    requests_parser = subparsers.add_parser("requests", help="show request lifecycle state from message history")
    requests_parser.add_argument("--agent", help="filter requests by from/to agent")
    requests_parser.add_argument("--limit", type=int, default=20)
    requests_parser.set_defaults(func=cmd_requests)

    request_sweep_parser = subparsers.add_parser("request-sweep", help="dry-run or apply timeout actions for stale requests")
    request_sweep_parser.add_argument("--agent", help="filter requests by from/to agent")
    request_sweep_parser.add_argument("--limit", type=int, default=20)
    request_sweep_parser.add_argument("--dry-run", action="store_true")
    request_sweep_parser.set_defaults(func=cmd_request_sweep)

    send_parser = subparsers.add_parser("send", help="send an agent handoff")
    send_parser.add_argument("--from-agent", required=True)
    send_parser.add_argument("--to-agent", required=True)
    send_parser.add_argument("--summary", required=True)
    send_parser.add_argument("--details", default="")
    send_parser.add_argument("--task", default="shared task")
    send_parser.add_argument("--role", default="handoff")
    send_parser.add_argument("--files", default="", help="comma-separated file list")
    send_parser.add_argument("--request-id", default="", help="optional request id this handoff relates to")
    send_parser.set_defaults(func=cmd_send)

    request_parser = subparsers.add_parser("request", help="send a message that auto bridges will respond to")
    request_parser.add_argument("--from-agent", required=True)
    request_parser.add_argument("--to-agent", required=True)
    request_parser.add_argument("--summary", required=True)
    request_parser.add_argument("--details", default="")
    request_parser.add_argument("--task", default="shared task")
    request_parser.add_argument("--role", default="request")
    request_parser.add_argument("--files", default="", help="comma-separated file list")
    request_parser.add_argument("--request-id", default="", help="optional request id to preserve across retries")
    request_parser.set_defaults(func=cmd_request)

    done_parser = subparsers.add_parser("done", help="send a completion update to another agent")
    done_parser.add_argument("--from-agent", required=True)
    done_parser.add_argument("--to-agent", required=True)
    done_parser.add_argument("--summary", required=True)
    done_parser.add_argument("--details", default="")
    done_parser.add_argument("--task", default="completed work")
    done_parser.add_argument("--role", default="done")
    done_parser.add_argument("--files", default="", help="comma-separated file list")
    done_parser.add_argument("--request-id", default="", help="optional request id this update resolves")
    done_parser.set_defaults(func=_cmd_workflow_message)

    blocked_parser = subparsers.add_parser("blocked", help="send a blocked update to another agent")
    blocked_parser.add_argument("--from-agent", required=True)
    blocked_parser.add_argument("--to-agent", required=True)
    blocked_parser.add_argument("--summary", required=True)
    blocked_parser.add_argument("--details", default="")
    blocked_parser.add_argument("--task", default="blocked work")
    blocked_parser.add_argument("--role", default="blocked")
    blocked_parser.add_argument("--files", default="", help="comma-separated file list")
    blocked_parser.add_argument("--request-id", default="", help="optional request id this update resolves")
    blocked_parser.set_defaults(func=_cmd_workflow_message)

    review_parser = subparsers.add_parser("review", help="send a review-request update to another agent")
    review_parser.add_argument("--from-agent", required=True)
    review_parser.add_argument("--to-agent", required=True)
    review_parser.add_argument("--summary", required=True)
    review_parser.add_argument("--details", default="")
    review_parser.add_argument("--task", default="review request")
    review_parser.add_argument("--role", default="review")
    review_parser.add_argument("--files", default="", help="comma-separated file list")
    review_parser.add_argument("--request-id", default="", help="optional request id this update resolves")
    review_parser.set_defaults(func=_cmd_workflow_message)

    for command_name, role_name, help_text in (
        ("request-approve", "approved", "approve a tracked request by id"),
        ("request-close", "closed", "close a tracked request by id"),
        ("request-escalate", "escalated", "escalate a tracked request by id"),
    ):
        action_parser = subparsers.add_parser(command_name, help=help_text)
        action_parser.add_argument("--request-id", required=True)
        action_parser.add_argument("--from-agent", default="", help="defaults to the request assignee")
        action_parser.add_argument("--to-agent", default="", help="defaults to the original requester")
        action_parser.add_argument("--summary", default="", help="optional override summary")
        action_parser.add_argument("--details", default="", help="optional override details")
        action_parser.add_argument("--task", default="", help="optional override task")
        action_parser.set_defaults(func=_cmd_request_action, role=role_name)

    request_resolve_parser = subparsers.add_parser("request-resolve", help="resolve a tracked request with approve, close, or escalate")
    request_resolve_parser.add_argument("--request-id", required=True)
    request_resolve_parser.add_argument("--action", required=True, choices=["approve", "close", "escalate"])
    request_resolve_parser.add_argument("--from-agent", default="", help="defaults to the request assignee")
    request_resolve_parser.add_argument("--to-agent", default="", help="defaults to the original requester")
    request_resolve_parser.add_argument("--summary", default="", help="optional override summary")
    request_resolve_parser.add_argument("--details", default="", help="optional override details")
    request_resolve_parser.add_argument("--task", default="", help="optional override task")
    request_resolve_parser.set_defaults(func=cmd_request_resolve)

    route_parser = subparsers.add_parser("route", help="recommend Codex or Hermes for a request")
    route_parser.add_argument("--summary", required=True)
    route_parser.add_argument("--details", default="")
    route_parser.add_argument("--files", default="", help="comma-separated file list")
    route_parser.set_defaults(func=cmd_route)

    dispatch_parser = subparsers.add_parser("dispatch", help="send a request using smart or explicit routing")
    dispatch_parser.add_argument("--from-agent", required=True)
    dispatch_parser.add_argument("--summary", required=True)
    dispatch_parser.add_argument("--details", default="")
    dispatch_parser.add_argument("--task", default="shared task")
    dispatch_parser.add_argument("--files", default="", help="comma-separated file list")
    dispatch_parser.add_argument("--route", choices=["smart", "explicit"], default="smart")
    dispatch_parser.add_argument("--to-agent", choices=["codex", "hermes"], default="hermes")
    dispatch_parser.add_argument("--allow-self-route", action="store_true", help="allow smart routing to target the originating agent")
    dispatch_parser.add_argument("--role", default="request")
    dispatch_parser.set_defaults(func=cmd_dispatch)

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

    session_start_parser = subparsers.add_parser("session-start", help="create a git worktree-backed agent session")
    session_start_parser.add_argument("--agent", required=True)
    session_start_parser.add_argument("--scope", required=True)
    session_start_parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository path")
    session_start_parser.add_argument("--branch", help="branch name for the worktree")
    session_start_parser.add_argument("--base-ref", help="branch or ref to branch from")
    session_start_parser.add_argument("--path", type=Path, help="worktree path to create")
    session_start_parser.add_argument("--claim-scope", help="optional linked claim scope")
    session_start_parser.add_argument("--note", default="")
    session_start_parser.set_defaults(func=cmd_session_start)

    sessions_parser = subparsers.add_parser("sessions", help="list agent worktree sessions")
    sessions_parser.add_argument("--agent", help="filter sessions by agent")
    sessions_parser.add_argument("--limit", type=int, default=20)
    sessions_parser.add_argument("--all", action="store_true", help="include closed sessions")
    sessions_parser.set_defaults(func=cmd_sessions)

    drift_parser = subparsers.add_parser("drift", help="inspect changed files in agent sessions against claimed scope")
    drift_parser.add_argument("--agent", help="filter sessions by agent")
    drift_parser.add_argument("--limit", type=int, default=20)
    drift_parser.add_argument("--all", action="store_true", help="include closed sessions")
    drift_parser.set_defaults(func=cmd_drift)

    suggest_parser = subparsers.add_parser("suggest", help="recommend how to resolve session drift or unscoped edits")
    suggest_parser.add_argument("--agent", help="filter sessions by agent")
    suggest_parser.add_argument("--limit", type=int, default=20)
    suggest_parser.add_argument("--all", action="store_true", help="include closed sessions")
    suggest_parser.set_defaults(func=cmd_suggest)

    remediate_parser = subparsers.add_parser("remediate", help="apply a safe remediation for session drift")
    remediate_parser.add_argument("--agent", required=True)
    remediate_parser.add_argument("--scope", required=True)
    remediate_parser.add_argument("--action", choices=["auto", "expand-claim", "handoff", "split-claim"], default="auto")
    remediate_parser.add_argument("--create-session", action="store_true", help="when split-claim stays with the same agent, create a new session too")
    remediate_parser.add_argument("--dry-run", action="store_true")
    remediate_parser.set_defaults(func=cmd_remediate)

    session_end_parser = subparsers.add_parser("session-end", help="close an agent worktree session")
    session_end_parser.add_argument("--agent", required=True)
    session_end_parser.add_argument("--scope", help="close only a specific scope")
    session_end_parser.add_argument("--keep-worktree", action="store_true", help="mark the session closed without removing the worktree")
    session_end_parser.add_argument("--note", default="")
    session_end_parser.set_defaults(func=cmd_session_end)

    resolve_parser = subparsers.add_parser("resolve", help="close claims with a final state")
    resolve_parser.add_argument("--agent", required=True)
    resolve_parser.add_argument("--scope", help="resolve only a specific scope")
    resolve_parser.add_argument("--status", choices=["completed", "blocked", "abandoned"], required=True)
    resolve_parser.add_argument("--note", default="", help="optional resolution note")
    resolve_parser.set_defaults(func=cmd_resolve)

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
