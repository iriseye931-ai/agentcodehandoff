from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "src" / "agentcodehandoff" / "cli.py"
sys.path.insert(0, str(REPO_ROOT / "src"))
from agentcodehandoff import cli as ach_cli


def run_cli(args: list[str], *, env: dict[str, str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *args],
        cwd=str(cwd or REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def create_fake_agent_bin(bin_dir: Path) -> None:
    common_header = "#!/usr/bin/env python3\nfrom __future__ import annotations\nimport json, os, sys\n"

    write_executable(
        bin_dir / "hermes",
        common_header
        + textwrap.dedent(
            """
            args = sys.argv[1:]
            if "--help" in args:
                print("hermes help")
                raise SystemExit(0)
            if "chat" in args:
                prompt = args[-1] if args else ""
                print(json.dumps({"summary": "hermes reply", "details": prompt[:80], "files": []}))
                raise SystemExit(0)
            print("hermes test stub")
            """
        ),
    )

    write_executable(
        bin_dir / "claude",
        common_header
        + textwrap.dedent(
            """
            args = sys.argv[1:]
            if args == ["--version"]:
                print("claude 0.0-test")
                raise SystemExit(0)
            if args == ["auth", "status"]:
                print(json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
                raise SystemExit(0)
            if "-p" in args:
                output_format = "text"
                if "--output-format" in args:
                    output_format = args[args.index("--output-format") + 1]
                schema = {}
                if "--json-schema" in args:
                    schema = json.loads(args[args.index("--json-schema") + 1])
                prompt = args[-1] if args else ""
                structured = {
                    "summary": "claude structured reply",
                    "details": f"handled: {prompt[:60]}",
                    "files": ["README.md"] if "README.md" in prompt else [],
                }
                if output_format == "json":
                    print(
                        json.dumps(
                            {
                                "type": "result",
                                "subtype": "success",
                                "is_error": False,
                                "structured_output": structured,
                                "schema_seen": bool(schema),
                            }
                        )
                    )
                else:
                    print(json.dumps(structured))
                raise SystemExit(0)
            print("claude test stub")
            """
        ),
    )

    write_executable(
        bin_dir / "openclaw",
        common_header
        + textwrap.dedent(
            """
            args = sys.argv[1:]
            if args == ["--version"]:
                print("openclaw 0.0-test")
                raise SystemExit(0)
            if args[:2] == ["agent", "--json"] and "--message" in args:
                prompt = args[args.index("--message") + 1]
                print(json.dumps({"reply": f"openclaw handled: {prompt[:60]}"}))
                raise SystemExit(0)
            print("openclaw test stub")
            """
        ),
    )


def read_inbox(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class AgentCodeHandoffCLITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="ach-tests-")
        self.root = Path(self.temp_dir.name)
        self.home = self.root / "home"
        self.bin_dir = self.root / "bin"
        self.repo = self.root / "repo"
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        create_fake_agent_bin(self.bin_dir)

        self.env = os.environ.copy()
        self.env["AGENTCODEHANDOFF_HOME"] = str(self.home)
        self.env["PATH"] = f"{self.bin_dir}:{self.env.get('PATH', '')}"

        self.repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main", str(self.repo)], check=True, capture_output=True)
        (self.repo / "README.md").write_text("test\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "README.md"], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "init"],
            check=True,
            capture_output=True,
        )

    def write_bridge_profile(self, agent: str, *, repo: Path | None = None) -> Path:
        path = self.home / "bridges" / f"{agent}.profile.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "agent": agent,
                    "repo": str(repo or self.repo),
                    "interval": 2.0,
                    "claim_on_files": False,
                    "claim_scope_prefix": "auto-",
                    "auto_sweep": True,
                    "sweep_interval": 30.0,
                    "max_restarts": 5,
                    "cool_off_seconds": 300.0,
                    "updated_at": "2026-04-03T00:00:00+00:00",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def write_bridge_lock(self, agent: str, payload: dict[str, object]) -> Path:
        path = self.home / "bridges" / f"{agent}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return path

    def write_bridge_log(self, agent: str, text: str) -> Path:
        path = self.home / "logs" / f"{agent}-bridge.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def wait_for_bridge_health(self, agent: str, *, timeout: float = 20.0) -> str:
        deadline = time.time() + timeout
        last_output = ""
        while time.time() < deadline:
            bridge_status = run_cli(["bridge-status"], env=self.env, cwd=self.repo)
            self.assertEqual(bridge_status.returncode, 0, bridge_status.stdout + bridge_status.stderr)
            last_output = bridge_status.stdout
            if f"{agent}: healthy" in bridge_status.stdout:
                return bridge_status.stdout
            time.sleep(0.5)
        self.fail(last_output)

    def wait_for_all_bridge_health(self, agents: list[str], *, timeout: float = 20.0) -> str:
        deadline = time.time() + timeout
        last_output = ""
        while time.time() < deadline:
            bridge_status = run_cli(["bridge-status"], env=self.env, cwd=self.repo)
            self.assertEqual(bridge_status.returncode, 0, bridge_status.stdout + bridge_status.stderr)
            last_output = bridge_status.stdout
            if all(f"{agent}: healthy" in bridge_status.stdout for agent in agents):
                return bridge_status.stdout
            time.sleep(0.5)
        self.fail(last_output)

    def read_bridge_lock(self, agent: str) -> dict[str, object]:
        path = self.home / "bridges" / f"{agent}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def tearDown(self) -> None:
        try:
            run_cli(["down", "--template", "local-trio", "--repo", str(self.repo), "--force"], env=self.env)
        finally:
            self.temp_dir.cleanup()

    def test_init_and_doctor_with_fake_agents(self) -> None:
        init = run_cli(["init", "--install-wrappers", "--seed", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        doctor = run_cli(["doctor", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("OK    hermes CLI ready", doctor.stdout)
        self.assertIn("OK    claude CLI ready", doctor.stdout)
        self.assertIn("OK    openclaw CLI ready", doctor.stdout)
        self.assertIn("OK    hermes runtime ready", doctor.stdout)
        self.assertIn("OK    claude runtime ready", doctor.stdout)

    def test_agent_check_runs_bridge_path_for_claude(self) -> None:
        result = run_cli(["agent-check", "--agent", "claude", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("OK    claude CLI ready", result.stdout)
        self.assertIn("OK    claude runtime ready", result.stdout)
        self.assertIn("OK    claude bridge invocation", result.stdout)

    def test_claude_runner_returns_structured_output(self) -> None:
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": {"summary": "ok", "details": "fine", "files": []},
        }

        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps(payload), stderr="")

        with mock.patch("agentcodehandoff.cli.subprocess.run", side_effect=fake_run):
            result = ach_cli._run_claude_auto("Return JSON only.", self.repo)
        self.assertEqual(result["summary"], "ok")

    def test_doctor_shows_actionable_cli_hint(self) -> None:
        init = run_cli(["init", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        env = self.env.copy()
        git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
        env["PATH"] = f"{self.bin_dir}:{git_dir}"
        (self.bin_dir / "claude").unlink()
        doctor = run_cli(["doctor", "--bin-dir", str(self.bin_dir)], env=env)
        self.assertEqual(doctor.returncode, 0, doctor.stdout + doctor.stderr)
        self.assertIn("WARN  claude CLI ready", doctor.stdout)
        self.assertIn("hint: Install or expose the local claude CLI on PATH", doctor.stdout)

    def test_summarize_error_strips_ansi_and_newlines(self) -> None:
        summarized = ach_cli._summarize_error("\x1b[32mError:\x1b[0m Connection error.\nRetrying now.\n")
        self.assertEqual(summarized, "Error: Connection error. Retrying now.")

    def test_extract_hermes_runtime_context(self) -> None:
        text = "Provider: custom  Model: /tmp/model\nEndpoint: http://127.0.0.1:8080/v1\n"
        context = ach_cli._extract_hermes_runtime_context(text)
        self.assertIn("provider=custom", context)
        self.assertIn("model=/tmp/model", context)
        self.assertIn("endpoint=http://127.0.0.1:8080/v1", context)

    def test_failure_hint_handles_claude_logged_out_json(self) -> None:
        hint = ach_cli._failure_hint("claude", "", '{ "loggedIn": false, "authMethod": "none" }')
        self.assertIn("reports no active login", hint)

    def test_failure_hint_handles_hermes_timeout(self) -> None:
        hint = ach_cli._failure_hint("hermes", "", "Command timed out after 20 seconds")
        self.assertIn("provider path is timing out", hint)

    def test_hermes_runtime_health_timeout_includes_context(self) -> None:
        def fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd=args[0],
                timeout=20,
                output="Provider: custom  Model: /tmp/model\nEndpoint: http://127.0.0.1:8080/v1\n",
                stderr="Connection error.",
            )

        with mock.patch("agentcodehandoff.cli.shutil.which", return_value="/tmp/hermes"):
            with mock.patch("agentcodehandoff.cli.subprocess.run", side_effect=fake_run):
                ok, detail = ach_cli._hermes_runtime_health(self.repo)
        self.assertFalse(ok)
        self.assertIn("timed out after 20s", detail)
        self.assertIn("provider=custom", detail)
        self.assertIn("endpoint=http://127.0.0.1:8080/v1", detail)

    def test_validate_bridge_start_rejects_claude_runtime_failure(self) -> None:
        with mock.patch("agentcodehandoff.cli._agent_cli_health", return_value=(True, "ok")):
            with mock.patch("agentcodehandoff.cli._agent_runtime_health", return_value=(False, "not logged in")):
                with self.assertRaises(SystemExit) as exc:
                    ach_cli._validate_bridge_start("claude", self.repo)
        self.assertIn("claude runtime is not ready: not logged in", str(exc.exception))

    def test_validate_bridge_start_rejects_hermes_runtime_failure(self) -> None:
        with mock.patch("agentcodehandoff.cli._agent_cli_health", return_value=(True, "ok")):
            with mock.patch("agentcodehandoff.cli._agent_runtime_health", return_value=(False, "APIConnectionError")):
                with self.assertRaises(SystemExit) as exc:
                    ach_cli._validate_bridge_start("hermes", self.repo)
        self.assertIn("hermes runtime is not ready: APIConnectionError", str(exc.exception))

    def test_agent_check_shows_failure_hint_on_bridge_invocation_error(self) -> None:
        args = argparse.Namespace(agent="claude", repo=self.repo)
        buffer = io.StringIO()
        with mock.patch("agentcodehandoff.cli._agent_cli_health", return_value=(True, "ok")):
            with mock.patch("agentcodehandoff.cli._agent_runtime_health", return_value=(True, "logged in via claude.ai")):
                with mock.patch("agentcodehandoff.cli._run_auto_agent", side_effect=RuntimeError("Not logged in")):
                    with contextlib.redirect_stdout(buffer):
                        with self.assertRaises(SystemExit):
                            ach_cli.cmd_agent_check(args)
        output = buffer.getvalue()
        self.assertIn("FAIL  claude bridge invocation", output)
        self.assertIn("hint: Re-authenticate the local claude CLI", output)

    def test_quickstart_runs_golden_path(self) -> None:
        result = run_cli(
            ["quickstart", "--repo", str(self.repo), "--bin-dir", str(self.bin_dir)],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("home:", result.stdout)
        self.assertIn("started claude bridge", result.stdout)
        self.assertIn("next:", result.stdout)

    def test_quickstart_failure_points_to_agent_check(self) -> None:
        args = argparse.Namespace(
            home=self.home,
            inbox_path=self.home / "inbox.jsonl",
            claims_path=self.home / "claims.json",
            sessions_path=self.home / "sessions.json",
            agents=ach_cli._default_agents(),
            seed=True,
            force=False,
            bin_dir=self.bin_dir,
            template="local-trio",
            repo=self.repo,
            start_team=True,
            verbose=False,
            timeout=3.0,
        )
        buffer = io.StringIO()
        with mock.patch("agentcodehandoff.cli.cmd_doctor", return_value=None):
            with mock.patch("agentcodehandoff.cli.cmd_up", side_effect=SystemExit("claude runtime is not ready")):
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit):
                        ach_cli.cmd_quickstart(args)
        output = buffer.getvalue()
        self.assertIn("team startup needs attention:", output)
        self.assertIn("agentcodehandoff agent-check --agent claude", output)
        self.assertIn("agentcodehandoff agent-check --agent hermes", output)

    def test_quickstart_local_squad_mentions_openclaw(self) -> None:
        result = run_cli(
            ["quickstart", "--template", "local-squad", "--repo", str(self.repo), "--bin-dir", str(self.bin_dir)],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("started openclaw bridge", result.stdout)
        self.assertIn("--to-agent openclaw", result.stdout)

    def test_route_respects_availability_override(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        set_result = run_cli(
            ["availability-set", "--agent", "claude", "--state", "offline", "--note", "test override"],
            env=self.env,
        )
        self.assertEqual(set_result.returncode, 0, set_result.stderr)
        route = run_cli(
            ["route", "--summary", "Architecture review", "--details", "Need design tradeoff planning", "--files", "README.md"],
            env=self.env,
        )
        self.assertEqual(route.returncode, 0, route.stderr)
        self.assertIn("recommended_agent: hermes", route.stdout)
        self.assertIn("claude_available: no", route.stdout)

    def test_bridge_preset_apply_persists_local_trio_profiles(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        apply = run_cli(
            ["bridge-preset-apply", "--name", "local-trio", "--repo", str(self.repo)],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
        self.assertIn("hermes: applied preset local-trio", apply.stdout)
        self.assertIn("claude: applied preset local-trio", apply.stdout)

        for agent in ("hermes", "claude"):
            profile_path = self.home / "bridges" / f"{agent}.profile.json"
            self.assertTrue(profile_path.exists(), profile_path.as_posix())
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(profile["repo"], str(self.repo))
            self.assertIn("updated_at", profile)

    def test_bridge_preset_apply_persists_local_squad_profiles(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        apply = run_cli(
            ["bridge-preset-apply", "--name", "local-squad", "--repo", str(self.repo)],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
        self.assertIn("openclaw: applied preset local-squad", apply.stdout)

        for agent in ("hermes", "claude", "openclaw"):
            profile_path = self.home / "bridges" / f"{agent}.profile.json"
            self.assertTrue(profile_path.exists(), profile_path.as_posix())
            profile = json.loads(profile_path.read_text(encoding="utf-8"))
            self.assertEqual(profile["repo"], str(self.repo))
            self.assertIn("updated_at", profile)

    def test_bridge_start_rejects_non_git_repo(self) -> None:
        bad_repo = self.root / "not-a-repo"
        bad_repo.mkdir(parents=True, exist_ok=True)
        result = run_cli(["bridge-start", "--agent", "claude", "--repo", str(bad_repo)], env=self.env, cwd=bad_repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("repo is not a git repository", result.stderr)

    def test_bridge_start_rejects_missing_agent_cli(self) -> None:
        env = self.env.copy()
        git_dir = str(Path(shutil.which("git") or "/usr/bin/git").parent)
        env["PATH"] = f"{self.bin_dir}:{git_dir}"
        (self.bin_dir / "claude").unlink()
        result = run_cli(["bridge-start", "--agent", "claude", "--repo", str(self.repo)], env=env, cwd=self.repo)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("claude CLI is not ready", result.stderr)
        self.assertIn("agentcodehandoff doctor", result.stderr)

    def test_bridge_stop_removes_stale_lock(self) -> None:
        lock_path = self.write_bridge_lock(
            "claude",
            {
                "agent": "claude",
                "pid": 999999,
                "supervisor_pid": 999998,
                "repo": str(self.repo),
                "paused": False,
            },
        )
        result = run_cli(["bridge-stop", "--agent", "claude"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("removed stale lock", result.stdout)
        self.assertFalse(lock_path.exists())

    def test_logs_shows_tail_for_selected_agent(self) -> None:
        self.write_bridge_log("claude", "line-1\nline-2\nline-3\n")
        result = run_cli(["logs", "--agents", "claude", "--lines", "2"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude:", result.stdout)
        self.assertNotIn("line-1", result.stdout)
        self.assertIn("line-2", result.stdout)
        self.assertIn("line-3", result.stdout)

    def test_logs_reports_missing_file(self) -> None:
        result = run_cli(["logs", "--agents", "hermes", "--lines", "5"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("hermes:", result.stdout)
        self.assertIn("log file not found", result.stdout)

    def test_ps_shows_compact_team_summary(self) -> None:
        self.write_bridge_lock(
            "claude",
            {
                "agent": "claude",
                "pid": 0,
                "supervisor_pid": 0,
                "repo": str(self.repo),
                "paused": True,
                "failure_class": "auth",
                "log_path": str(self.home / "logs" / "claude-bridge.log"),
            },
        )
        (self.home / "automation").mkdir(parents=True, exist_ok=True)
        (self.home / "automation" / "claude.json").write_text(
            json.dumps({"seen_ids": [], "last_poll_at": "", "last_reply_at": "", "last_error": "Not logged in"}),
            encoding="utf-8",
        )
        result = run_cli(["ps", "--agents", "claude"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude: paused", result.stdout)
        self.assertIn("failure=auth", result.stdout)
        self.assertIn("error=Not logged in", result.stdout)

    def test_bridge_status_shows_actionable_failure_hint(self) -> None:
        self.write_bridge_profile("claude", repo=self.repo)
        self.write_bridge_lock(
            "claude",
            {
                "agent": "claude",
                "pid": 0,
                "supervisor_pid": 0,
                "repo": str(self.repo),
                "paused": True,
                "failure_class": "auth",
                "log_path": str(self.home / "logs" / "claude-bridge.log"),
                "max_restarts": 3,
                "cool_off_seconds": 60.0,
            },
        )
        (self.home / "automation").mkdir(parents=True, exist_ok=True)
        (self.home / "automation" / "claude.json").write_text(
            json.dumps({"seen_ids": [], "last_poll_at": "", "last_reply_at": "", "last_error": "Not logged in"}),
            encoding="utf-8",
        )
        result = run_cli(["bridge-status", "--agents", "claude"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("failure class: auth", result.stdout)
        self.assertIn("hint: Re-authenticate the local claude CLI", result.stdout)

    def test_bridge_status_shows_stale_when_processes_alive_but_heartbeat_old(self) -> None:
        self.write_bridge_profile("hermes", repo=self.repo)
        self.write_bridge_lock(
            "hermes",
            {
                "agent": "hermes",
                "pid": 111,
                "supervisor_pid": 222,
                "repo": str(self.repo),
                "paused": False,
                "last_heartbeat_at": "2000-01-01T00:00:00+00:00",
            },
        )
        with mock.patch("agentcodehandoff.cli._pid_alive", return_value=True):
            status = ach_cli._supervised_bridge_status(self.home, self.home / "inbox.jsonl", "hermes")
        self.assertTrue(status["stale"])
        self.assertFalse(status["healthy"])
        line = ach_cli._bridge_supervision_line(status, 160)
        self.assertIn("hermes: stale", line)

    def test_events_merges_messages_and_bridge_events(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.write_bridge_lock(
            "claude",
            {
                "agent": "claude",
                "pid": 0,
                "supervisor_pid": 0,
                "repo": str(self.repo),
                "paused": True,
                "recent_events": [
                    {
                        "timestamp": "2026-04-04T00:00:00+00:00",
                        "type": "paused",
                        "summary": "Paused after startup failure",
                        "detail": "auth problem",
                    }
                ],
            },
        )
        request = run_cli(
            [
                "request",
                "--from-agent",
                "hermes",
                "--to-agent",
                "claude",
                "--summary",
                "Timeline test",
                "--details",
                "Need acknowledgement.",
                "--files",
                "README.md",
            ],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(request.returncode, 0, request.stdout + request.stderr)
        result = run_cli(["events", "--agents", "claude", "hermes", "--limit", "10"], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("claude bridge [paused]", result.stdout)
        self.assertIn("hermes -> claude [request]", result.stdout)
        self.assertIn("Timeline test", result.stdout)

    def test_request_trace_prefers_latest_matching_request(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)
        first = run_cli(
            [
                "request",
                "--from-agent",
                "claude",
                "--to-agent",
                "hermes",
                "--summary",
                "Older request",
                "--details",
                "First request",
                "--files",
                "README.md",
            ],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        second = run_cli(
            [
                "request",
                "--from-agent",
                "claude",
                "--to-agent",
                "hermes",
                "--summary",
                "Newer request",
                "--details",
                "Second request",
                "--files",
                "README.md",
            ],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        inbox_path = self.home / "inbox.jsonl"
        messages = read_inbox(inbox_path)
        newer_request_id = str(messages[1]["id"])
        ach_cli._send_record(
            inbox_path,
            from_agent="hermes",
            to_agent="claude",
            role="handoff",
            task="shared task",
            summary="Ack newer",
            details="Acknowledged latest request only.",
            files=["README.md"],
        )
        requests = run_cli(["requests", "--limit", "10"], env=self.env, cwd=self.repo)
        self.assertEqual(requests.returncode, 0, requests.stdout + requests.stderr)
        self.assertIn("claude->hermes [pending] Older request", requests.stdout)
        self.assertIn("claude->hermes [acknowledged] Newer request", requests.stdout)
        trace = run_cli(["request-trace", "--request-id", newer_request_id], env=self.env, cwd=self.repo)
        self.assertEqual(trace.returncode, 0, trace.stdout + trace.stderr)
        self.assertIn("Newer request", trace.stdout)
        self.assertIn("Ack newer", trace.stdout)

    def test_bridge_recover_starts_from_saved_profile_without_live_lock(self) -> None:
        self.write_bridge_profile("claude")
        result = run_cli(["bridge-recover", "--agents", "claude", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("started claude bridge", result.stdout)
        self.wait_for_bridge_health("claude")

    def test_bridge_recover_restarts_paused_stale_lock(self) -> None:
        self.write_bridge_profile("claude")
        self.write_bridge_lock(
            "claude",
            {
                "agent": "claude",
                "pid": 999999,
                "supervisor_pid": 999998,
                "repo": str(self.repo),
                "paused": True,
                "failure_class": "auth",
                "interval": 2.0,
                "claim_on_files": False,
                "claim_scope_prefix": "auto-",
                "auto_sweep": True,
                "sweep_interval": 30.0,
                "max_restarts": 5,
                "cool_off_seconds": 300.0,
            },
        )
        result = run_cli(["bridge-recover", "--agents", "claude", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("bridge process was not running; removed stale lock", result.stdout)
        self.assertIn("started claude bridge", result.stdout)
        self.wait_for_bridge_health("claude")

    def test_supervise_pauses_after_restart_cap(self) -> None:
        ach_cli._ensure_state(self.home, self.home / "inbox.jsonl", self.home / "claims.json")
        lock_path = self.home / "bridges" / "claude.json"

        class FakeProcess:
            next_pid = 40000

            def __init__(self, *args, **kwargs) -> None:
                self.pid = FakeProcess.next_pid
                FakeProcess.next_pid += 1

            def poll(self) -> int:
                return 1

        args = argparse.Namespace(
            home=self.home,
            inbox_path=self.home / "inbox.jsonl",
            claims_path=self.home / "claims.json",
            sessions_path=self.home / "sessions.json",
            agent="claude",
            repo=self.repo,
            interval=0.01,
            claim_on_files=False,
            claim_scope_prefix="auto-",
            verbose=False,
            log_path="",
            always_restart=False,
            auto_sweep=False,
            sweep_interval=30.0,
            max_restarts=1,
            cool_off_seconds=300.0,
        )

        with mock.patch.object(ach_cli.subprocess, "Popen", side_effect=FakeProcess), mock.patch.object(ach_cli.time, "sleep", lambda _: None):
            ach_cli.cmd_supervise(args)

        lock = self.read_bridge_lock("claude")
        self.assertTrue(lock.get("paused"))
        self.assertEqual(lock.get("failure_class"), "restart-limit")
        self.assertGreaterEqual(int(lock.get("restart_count", 0) or 0), 2)
        events = lock.get("recent_events", [])
        self.assertTrue(any(event.get("type") == "paused" for event in events if isinstance(event, dict)))
        lock_path.unlink(missing_ok=True)

    def test_local_trio_starts_and_reports_healthy(self) -> None:
        init = run_cli(["init", "--install-wrappers", "--seed", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        up = run_cli(["up", "--template", "local-trio", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
        self.assertIn("started claude bridge", up.stdout)
        bridge_output = self.wait_for_all_bridge_health(["hermes", "claude"])
        self.assertIn("hermes: healthy", bridge_output)

        request = run_cli(
            [
                "request",
                "--from-agent",
                "hermes",
                "--to-agent",
                "claude",
                "--summary",
                "Test request",
                "--details",
                "Please acknowledge README.md",
                "--files",
                "README.md",
            ],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(request.returncode, 0, request.stdout + request.stderr)

        inbox_path = self.home / "inbox.jsonl"
        messages = read_inbox(inbox_path)
        self.assertTrue(any(message.get("from") == "hermes" and message.get("to") == "claude" for message in messages))

        requests = run_cli(["requests"], env=self.env, cwd=self.repo)
        self.assertEqual(requests.returncode, 0, requests.stdout + requests.stderr)
        self.assertIn("Test request", requests.stdout)

    def test_local_squad_starts_and_reports_healthy(self) -> None:
        init = run_cli(["init", "--install-wrappers", "--seed", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        up = run_cli(["up", "--template", "local-squad", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
        self.assertIn("started openclaw bridge", up.stdout)
        bridge_output = self.wait_for_all_bridge_health(["hermes", "claude", "openclaw"])
        self.assertIn("openclaw: healthy", bridge_output)

    def test_request_resolve_appends_linked_outcome(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        request = run_cli(
            [
                "request",
                "--from-agent",
                "hermes",
                "--to-agent",
                "claude",
                "--summary",
                "Resolve me",
                "--details",
                "Please close this request.",
                "--files",
                "README.md",
            ],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(request.returncode, 0, request.stdout + request.stderr)

        inbox_path = self.home / "inbox.jsonl"
        messages = read_inbox(inbox_path)
        request_id = str(messages[-1]["id"])

        resolve = run_cli(
            ["request-resolve", "--request-id", request_id, "--action", "approve"],
            env=self.env,
            cwd=self.repo,
        )
        self.assertEqual(resolve.returncode, 0, resolve.stdout + resolve.stderr)

        messages = read_inbox(inbox_path)
        self.assertEqual(len(messages), 2)
        outcome = messages[-1]
        self.assertEqual(outcome["role"], "approved")
        self.assertEqual(outcome["request_id"], request_id)
        self.assertEqual(outcome["from"], "claude")
        self.assertEqual(outcome["to"], "hermes")

        requests = run_cli(["requests"], env=self.env, cwd=self.repo)
        self.assertEqual(requests.returncode, 0, requests.stdout + requests.stderr)
        self.assertIn("[approved] Resolve me", requests.stdout)


if __name__ == "__main__":
    unittest.main()
