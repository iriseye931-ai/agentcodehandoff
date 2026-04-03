from __future__ import annotations

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


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "src" / "agentcodehandoff" / "cli.py"


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
        bin_dir / "codex",
        common_header
        + textwrap.dedent(
            """
            args = sys.argv[1:]
            if args == ["--version"]:
                print("codex 0.0-test")
                raise SystemExit(0)
            if "exec" in args and "-o" in args:
                out_path = args[args.index("-o") + 1]
                prompt = sys.stdin.read()
                payload = {"summary": "codex reply", "details": prompt[:80], "files": []}
                with open(out_path, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload))
                raise SystemExit(0)
            print("codex test stub")
            """
        ),
    )

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
        self.assertIn("OK    codex CLI ready", doctor.stdout)
        self.assertIn("OK    hermes CLI ready", doctor.stdout)
        self.assertIn("OK    claude CLI ready", doctor.stdout)

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
        self.assertIn("codex: applied preset local-trio", apply.stdout)
        self.assertIn("hermes: applied preset local-trio", apply.stdout)
        self.assertIn("claude: applied preset local-trio", apply.stdout)

        for agent in ("codex", "hermes", "claude"):
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

    def test_local_trio_starts_and_reports_healthy(self) -> None:
        init = run_cli(["init", "--install-wrappers", "--seed", "--bin-dir", str(self.bin_dir)], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        up = run_cli(["up", "--template", "local-trio", "--repo", str(self.repo)], env=self.env, cwd=self.repo)
        self.assertEqual(up.returncode, 0, up.stdout + up.stderr)
        self.assertIn("started claude bridge", up.stdout)
        bridge_output = self.wait_for_all_bridge_health(["codex", "hermes", "claude"])
        self.assertIn("codex: healthy", bridge_output)
        self.assertIn("hermes: healthy", bridge_output)

        request = run_cli(
            [
                "request",
                "--from-agent",
                "codex",
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
        self.assertTrue(any(message.get("from") == "codex" and message.get("to") == "claude" for message in messages))

        requests = run_cli(["requests"], env=self.env, cwd=self.repo)
        self.assertEqual(requests.returncode, 0, requests.stdout + requests.stderr)
        self.assertIn("Test request", requests.stdout)

    def test_request_resolve_appends_linked_outcome(self) -> None:
        init = run_cli(["init"], env=self.env)
        self.assertEqual(init.returncode, 0, init.stderr)

        request = run_cli(
            [
                "request",
                "--from-agent",
                "codex",
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
        self.assertEqual(outcome["to"], "codex")

        requests = run_cli(["requests"], env=self.env, cwd=self.repo)
        self.assertEqual(requests.returncode, 0, requests.stdout + requests.stderr)
        self.assertIn("[approved] Resolve me", requests.stdout)


if __name__ == "__main__":
    unittest.main()
