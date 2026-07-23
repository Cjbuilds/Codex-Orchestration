#!/usr/bin/env python3
"""Exercise a real Codex plugin install, Git upgrade, and both setup routes.

A disposable bare Git marketplace is served over loopback HTTP. The real Codex
CLI installs the affected Advisor-only 0.5.0 bundle, runs its documented
marketplace-upgrade command after the current release is pushed to that Git
remote, installs the refreshed package, verifies the new cache and routing
contract, and runs native-policy plus custom-agent setup/status/cleanup in
isolation.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from threading import Thread
import time
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-orchestration"
PLUGIN_ID = "codex-orchestration@codex-orchestration"
MARKETPLACE_NAME = "codex-orchestration"
ALTERNATE_MARKETPLACE_NAME = "alternate-orchestration"
ALTERNATE_PLUGIN_ID = f"codex-orchestration@{ALTERNATE_MARKETPLACE_NAME}"
OLD_RELEASE = "a1d9c546665c3253cdcaa8fe5c0c060199a6126c"
OLD_VERSION = "0.5.0"
NEW_VERSION = "0.9.2"
COMMAND_TIMEOUT_SECONDS = 60


class SmokeFailure(RuntimeError):
    """A lifecycle assertion or external command failed."""


@contextmanager
def temporary_lifecycle_root() -> Iterator[Path]:
    """Remove the disposable tree despite brief post-command Windows Git locks."""

    root = Path(tempfile.mkdtemp(prefix="codex-orchestration-lifecycle-"))

    def clear_readonly(
        function: Any, path: str, error_info: tuple[type[BaseException], BaseException, Any]
    ) -> None:
        error = error_info[1]
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        function(path)

    try:
        yield root
    finally:
        for attempt in range(7):
            try:
                shutil.rmtree(root, onerror=clear_readonly)
                break
            except FileNotFoundError:
                break
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(0.1 * (2**attempt))


def run(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure(f"Could not run {command!r}: {exc}") from exc
    if completed.returncode != 0:
        output = completed.stderr.strip() or completed.stdout.strip() or "no output"
        raise SmokeFailure(
            f"Command failed ({completed.returncode}): {command!r}\n{output}"
        )
    return completed


def run_json(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> Any:
    completed = run(command, cwd=cwd, env=env)
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeFailure(
            f"Command did not return JSON: {command!r}\n{completed.stdout}"
        ) from exc


def probe_mcp_subprocess(
    script: Path,
    *,
    cwd: Path,
    env: dict[str, str],
    expected_server: str,
    expected_tools: set[str],
) -> None:
    requests = "\n".join(
        json.dumps(request)
        for request in (
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lifecycle-smoke", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
    ) + "\n"
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=cwd,
            env=env,
            input=requests,
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure(f"Installed MCP subprocess failed: {exc}") from exc
    if completed.returncode != 0:
        raise SmokeFailure(
            "Installed Fable MCP subprocess did not shut down cleanly: "
            f"exit {completed.returncode}; {completed.stderr.strip()}"
        )
    try:
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
    except json.JSONDecodeError as exc:
        raise SmokeFailure("Installed MCP returned malformed JSON-RPC") from exc
    if len(responses) != 2 or [response.get("id") for response in responses] != [1, 2]:
        raise SmokeFailure(f"Installed MCP returned unexpected responses: {responses!r}")
    server_info = responses[0].get("result", {}).get("serverInfo", {})
    assert_equal(
        server_info.get("name"),
        expected_server,
        "installed MCP server identity",
    )
    tools = responses[1].get("result", {}).get("tools", [])
    tool_names = {
        tool.get("name") for tool in tools if isinstance(tool, dict)
    }
    assert_equal(
        tool_names,
        expected_tools,
        "installed MCP tool list",
    )


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise SmokeFailure(f"{message}: expected {expected!r}, got {actual!r}")


def replace_multiline_toml_setting(text: str, key: str, value: str) -> str:
    prefix = f'{key} = """\n'
    start = text.find(prefix)
    if start < 0 or text.find(prefix, start + len(prefix)) >= 0:
        raise SmokeFailure(f"Expected exactly one multiline TOML setting {key!r}")
    content_start = start + len(prefix)
    suffix = '\n"""\n'
    end = text.find(suffix, content_start)
    if end < 0:
        raise SmokeFailure(f"Could not find the end of multiline TOML setting {key!r}")
    return text[:content_start] + value.rstrip("\n") + text[end:]


def ignored(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or path.suffix == ".pyc"
        or path.name == ".DS_Store"
    )


def file_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes().replace(b"\r\n", b"\n"),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file() and not ignored(path.relative_to(root))
    }


def replace_publisher_source(publisher: Path) -> None:
    destination = publisher / "plugins" / "codex-orchestration"
    shutil.rmtree(destination)
    shutil.copytree(
        PLUGIN_ROOT,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    shutil.copy2(
        REPO_ROOT / ".agents" / "plugins" / "marketplace.json",
        publisher / ".agents" / "plugins" / "marketplace.json",
    )


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, message_format: str, *args: object) -> None:
        pass


@contextmanager
def serve_git(root: Path) -> Iterator[str]:
    handler = partial(QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/marketplace.git"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise SmokeFailure("Loopback Git server did not stop cleanly")


def write_fake_codex(path: Path, cwd: Path) -> Path:
    body = """import json
import sys

if sys.argv[1:] in (["--version"], ["models"]):
    if sys.argv[1:] == ["--version"]:
        print("codex-cli lifecycle-smoke")
        raise SystemExit(0)
    print(json.dumps({"models": [{
        "slug": "gpt-5.6-luna",
        "display_name": "GPT-5.6 Luna",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {"effort": "high"},
            {"effort": "xhigh"}
        ]
    }]}))
    raise SystemExit(0)
if sys.argv[1:] == ["debug", "models"]:
    print(json.dumps({"models": [{
        "slug": "gpt-5.6-luna",
        "display_name": "GPT-5.6 Luna",
        "default_reasoning_level": "high",
        "supported_reasoning_levels": [
            {"effort": "high"},
            {"effort": "xhigh"}
        ]
    }]}))
    raise SystemExit(0)
print("unsupported smoke command", file=sys.stderr)
raise SystemExit(2)
"""
    if os.name == "nt":
        (cwd / "debug").write_text(body, encoding="utf-8")
        return Path(sys.executable).resolve()
    path.write_text(
        """#!/usr/bin/env python3
""" + body,
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def installed_entry(
    payload: dict[str, Any], plugin_id: str = PLUGIN_ID
) -> dict[str, Any]:
    matches = [
        entry
        for entry in payload.get("installed", [])
        if isinstance(entry, dict) and entry.get("pluginId") == plugin_id
    ]
    if len(matches) != 1:
        raise SmokeFailure(f"Expected one discovered {plugin_id!r}, got {matches!r}")
    return matches[0]


def git_head(git: str, repository: Path, *, cwd: Path, env: dict[str, str]) -> str:
    return run(
        [git, "-C", str(repository), "rev-parse", "HEAD"],
        cwd=cwd,
        env=env,
    ).stdout.strip()


def main() -> int:
    codex = shutil.which(os.environ.get("CODEX_BIN", "codex"))
    git = shutil.which("git")
    if not codex:
        raise SmokeFailure("Codex CLI not found; set CODEX_BIN or install codex")
    if not git:
        raise SmokeFailure("git executable not found")

    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(
            encoding="utf-8"
        )
    )
    current_version = manifest.get("version")
    if not isinstance(current_version, str):
        raise SmokeFailure("checkout release version is not a string")
    assert_equal(
        current_version.split("+", 1)[0], NEW_VERSION, "checkout release base version"
    )

    with temporary_lifecycle_root() as temp:
        publisher = temp / "publisher"
        web_root = temp / "www"
        remote = web_root / "marketplace.git"
        codex_home = temp / "codex-home"
        home = temp / "home"
        project = temp / "project"
        home.mkdir()
        project.mkdir()
        codex_home.mkdir()
        web_root.mkdir()

        env = os.environ.copy()
        env.update(
            {
                "CODEX_HOME": str(codex_home),
                "HOME": str(home),
                "XDG_CONFIG_HOME": str(home / ".config"),
            }
        )

        run(
            [git, "clone", "--no-local", "--quiet", str(REPO_ROOT), str(publisher)],
            cwd=temp,
            env=env,
        )
        run(
            [git, "checkout", "--quiet", "-B", "main", OLD_RELEASE],
            cwd=publisher,
            env=env,
        )
        run([git, "config", "user.name", "Lifecycle Smoke"], cwd=publisher, env=env)
        run(
            [git, "config", "user.email", "smoke@example.invalid"],
            cwd=publisher,
            env=env,
        )

        old_manifest = json.loads(
            (
                publisher
                / "plugins"
                / "codex-orchestration"
                / ".codex-plugin"
                / "plugin.json"
            ).read_text(encoding="utf-8")
        )
        assert_equal(old_manifest.get("version"), OLD_VERSION, "old release fixture")

        run(
            [
                git,
                "clone",
                "--bare",
                "--no-local",
                str(publisher),
                str(remote),
            ],
            cwd=temp,
            env=env,
        )
        run(
            [git, f"--git-dir={remote}", "symbolic-ref", "HEAD", "refs/heads/main"],
            cwd=temp,
            env=env,
        )
        run(
            [git, f"--git-dir={remote}", "update-server-info"],
            cwd=temp,
            env=env,
        )
        run(
            [git, "remote", "set-url", "origin", str(remote)],
            cwd=publisher,
            env=env,
        )

        with serve_git(web_root) as marketplace_url:
            marketplace_add = run_json(
                [
                    codex,
                    "plugin",
                    "marketplace",
                    "add",
                    marketplace_url,
                    "--ref",
                    "main",
                    "--json",
                ],
                cwd=project,
                env=env,
            )
            marketplace_snapshot = Path(marketplace_add["installedRoot"]).resolve()
            assert_equal(
                git_head(git, marketplace_snapshot, cwd=temp, env=env),
                OLD_RELEASE,
                "initial marketplace snapshot commit",
            )

            old_install = run_json(
                [codex, "plugin", "add", PLUGIN_ID, "--json"],
                cwd=project,
                env=env,
            )
            assert_equal(
                old_install.get("version"), OLD_VERSION, "initial install version"
            )
            old_installed_root = Path(old_install["installedPath"]).resolve()
            old_skill = (
                old_installed_root
                / "skills"
                / "codex-orchestration"
                / "SKILL.md"
            ).read_text(encoding="utf-8")
            if "setup planner: Claude Fable 5 High" in old_skill:
                raise SmokeFailure("old Advisor-only cache unexpectedly supports Planner")
            if "built-in advisor label" not in old_skill:
                raise SmokeFailure("old release fixture is not the affected Advisor-only cache")

            old_discovery = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            old_entry = installed_entry(old_discovery)
            assert_equal(
                old_entry.get("version"), OLD_VERSION, "discovered old version"
            )
            assert_equal(old_entry.get("enabled"), True, "old plugin enabled state")
            marketplace_source = old_entry.get("marketplaceSource") or {}
            assert_equal(
                marketplace_source.get("sourceType"),
                "git",
                "marketplace source type",
            )
            assert_equal(
                marketplace_source.get("source"), marketplace_url, "marketplace URL"
            )

            replace_publisher_source(publisher)
            run([git, "add", "--all"], cwd=publisher, env=env)
            run(
                [git, "commit", "--quiet", "-m", "Publish lifecycle smoke release"],
                cwd=publisher,
                env=env,
            )
            published_commit = git_head(git, publisher, cwd=temp, env=env)
            run(
                [git, "push", "--quiet", "origin", "main"],
                cwd=publisher,
                env=env,
            )
            run(
                [git, f"--git-dir={remote}", "update-server-info"],
                cwd=temp,
                env=env,
            )

            upgrade = run_json(
                [
                    codex,
                    "plugin",
                    "marketplace",
                    "upgrade",
                    MARKETPLACE_NAME,
                    "--json",
                ],
                cwd=project,
                env=env,
            )
            assert_equal(
                upgrade.get("selectedMarketplaces"),
                [MARKETPLACE_NAME],
                "selected marketplace upgrade",
            )
            assert_equal(upgrade.get("errors"), [], "marketplace upgrade errors")
            upgraded_roots = {
                str(Path(value).resolve())
                for value in upgrade.get("upgradedRoots", [])
                if isinstance(value, str)
            }
            if str(marketplace_snapshot) not in upgraded_roots:
                raise SmokeFailure(
                    "Marketplace upgrade did not report the configured Git snapshot"
                )
            assert_equal(
                git_head(git, marketplace_snapshot, cwd=temp, env=env),
                published_commit,
                "upgraded marketplace snapshot commit",
            )
            cached_manifest = json.loads(
                (
                    marketplace_snapshot
                    / "plugins"
                    / "codex-orchestration"
                    / ".codex-plugin"
                    / "plugin.json"
                ).read_text(encoding="utf-8")
            )
            assert_equal(
                cached_manifest.get("version"),
                current_version,
                "upgraded marketplace manifest",
            )

            new_install = run_json(
                [codex, "plugin", "add", PLUGIN_ID, "--json"],
                cwd=project,
                env=env,
            )
            assert_equal(
                new_install.get("version"),
                current_version,
                "upgraded install version",
            )

            discovery = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            entry = installed_entry(discovery)
            assert_equal(
                entry.get("version"), current_version, "discovered new version"
            )
            assert_equal(entry.get("enabled"), True, "new plugin enabled state")
            refreshed_source = entry.get("marketplaceSource") or {}
            assert_equal(
                refreshed_source.get("sourceType"),
                "git",
                "refreshed marketplace source type",
            )
            assert_equal(
                refreshed_source.get("source"),
                marketplace_url,
                "refreshed marketplace URL",
            )

            installed_root = Path(new_install["installedPath"]).resolve()
            if installed_root == old_installed_root:
                raise SmokeFailure(
                    f"{current_version} reused the Advisor-only 0.5.0 cache directory"
                )
            assert_equal(
                file_tree(installed_root),
                file_tree(PLUGIN_ROOT),
                "installed package contents",
            )
            installed_skill = (
                installed_root / "skills" / "codex-orchestration" / "SKILL.md"
            ).read_text(encoding="utf-8")
            for expected in (
                "Explicit seat labels are authoritative",
                "never reinterpret a supplied `planner:` model as an Advisor",
                "Fable Planner uses `create_plan` and `revise_plan`",
                "Designer may edit only explicitly delegated design artifacts",
                "is Kimi available to use as Designer?",
                "Qwen 3.8 Max Preview",
                "Implicit invocation is discovery, not mutation authority",
                "/codex-orchestration repair",
                "/codex-orchestration --update",
            ):
                if expected not in installed_skill:
                    raise SmokeFailure(
                        f"Upgraded installed skill is missing Planner contract {expected!r}"
                    )

            installed_metadata = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "agents"
                / "openai.yaml"
            ).read_text(encoding="utf-8")
            if "allow_implicit_invocation: true" not in installed_metadata:
                raise SmokeFailure(
                    "Upgraded installed skill still blocks natural-language invocation"
                )

            installed_fable_mcp = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "fable_advisor_mcp.py"
            )
            probe_mcp_subprocess(
                installed_fable_mcp,
                cwd=project,
                env=env,
                expected_server="codex-orchestration-fable-advisor",
                expected_tools={"create_plan", "revise_plan", "review_plan", "status"},
            )

            installed_kimi_mcp = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "kimi_designer_mcp.py"
            )
            probe_mcp_subprocess(
                installed_kimi_mcp,
                cwd=project,
                env=env,
                expected_server="codex-orchestration-kimi-designer",
                expected_tools={"create_design_handoff", "status"},
            )

            installed_qwen_mcp = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "qwen_advisor_mcp.py"
            )
            probe_mcp_subprocess(
                installed_qwen_mcp,
                cwd=project,
                env=env,
                expected_server="codex-orchestration-qwen-advisor",
                expected_tools={"review_plan", "status"},
            )

            native_configurator = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "configure_native_routing.py"
            )
            direct_native_command = [
                sys.executable,
                str(native_configurator),
                "--codex-bin",
                codex,
                "--codex-home",
                str(codex_home),
                "--allow-incompatible-client",
                "--executor-model",
                "gpt-5.6-luna",
                "--executor-effort",
                "xhigh",
                "--designer-model",
                "gpt-5.6-luna",
                "--designer-effort",
                "high",
            ]
            direct_preview = run(direct_native_command, cwd=project, env=env)
            if "Dry run only" not in direct_preview.stdout:
                raise SmokeFailure("Direct native setup did not report a dry run")
            direct_apply = run(
                [*direct_native_command, "--apply"], cwd=project, env=env
            )
            if "Native routing policy installed" not in direct_apply.stdout:
                raise SmokeFailure("Direct native setup did not install its policy")
            direct_status = run(
                [
                    sys.executable,
                    str(native_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--status",
                    "--require-effective",
                ],
                cwd=project,
                env=env,
            )
            if "Executor: gpt-5.6-luna@xhigh" not in direct_status.stdout:
                raise SmokeFailure("Direct native status lost the selected model route")
            if "Designer: gpt-5.6-luna@high" not in direct_status.stdout:
                raise SmokeFailure("Direct native status lost the selected Designer route")

            state_path = codex_home / ".codex-orchestration-routing.json"
            state_before_repair = state_path.read_bytes()
            config_path = codex_home / "config.toml"
            drifted_config = config_path.read_text(encoding="utf-8")
            drifted_config = replace_multiline_toml_setting(
                drifted_config,
                "multi_agent_mode_hint_text",
                "[codex-orchestration managed-policy v1]\n"
                "route through execution_worker",
            )
            drifted_config = replace_multiline_toml_setting(
                drifted_config,
                "usage_hint_text",
                "[codex-orchestration managed-policy v1]\n"
                "route through verification_worker",
            )
            config_path.write_text(drifted_config, encoding="utf-8")
            conflicted_status = run(
                [
                    sys.executable,
                    str(native_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--status",
                ],
                cwd=project,
                env=env,
            )
            if "managed fields conflict" not in conflicted_status.stdout:
                raise SmokeFailure("Real native status did not reproduce hint drift")
            repair_command = [
                sys.executable,
                str(native_configurator),
                "--codex-bin",
                codex,
                "--codex-home",
                str(codex_home),
                "--repair",
            ]
            repair_preview = run(repair_command, cwd=project, env=env)
            if "Will restore saved managed mode and usage hints only" not in repair_preview.stdout:
                raise SmokeFailure("Native repair preview did not remain two-field-only")
            repair_apply = run(
                [*repair_command, "--apply"], cwd=project, env=env
            )
            if "Native routing policy repaired" not in repair_apply.stdout:
                raise SmokeFailure("Native repair did not report success")
            assert_equal(
                state_path.read_bytes(),
                state_before_repair,
                "native repair preserved saved state bytes",
            )
            repaired_status = run(
                [
                    sys.executable,
                    str(native_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--status",
                    "--require-effective",
                ],
                cwd=project,
                env=env,
            )
            if "Native policy: installed and effective" not in repaired_status.stdout:
                raise SmokeFailure("Native repair did not restore effective policy")
            run(
                [
                    sys.executable,
                    str(native_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--disable",
                    "--apply",
                ],
                cwd=project,
                env=env,
            )

            alternate_publisher = temp / "alternate-publisher"
            alternate_remote = web_root / "alternate.git"
            run(
                [
                    git,
                    "clone",
                    "--no-local",
                    "--quiet",
                    str(publisher),
                    str(alternate_publisher),
                ],
                cwd=temp,
                env=env,
            )
            run(
                [git, "config", "user.name", "Lifecycle Smoke"],
                cwd=alternate_publisher,
                env=env,
            )
            run(
                [git, "config", "user.email", "smoke@example.invalid"],
                cwd=alternate_publisher,
                env=env,
            )
            alternate_manifest_path = (
                alternate_publisher / ".agents" / "plugins" / "marketplace.json"
            )
            alternate_manifest = json.loads(
                alternate_manifest_path.read_text(encoding="utf-8")
            )
            alternate_manifest["name"] = ALTERNATE_MARKETPLACE_NAME
            alternate_manifest_path.write_text(
                json.dumps(alternate_manifest, indent=2) + "\n", encoding="utf-8"
            )
            run(
                [git, "add", ".agents/plugins/marketplace.json"],
                cwd=alternate_publisher,
                env=env,
            )
            run(
                [
                    git,
                    "commit",
                    "--quiet",
                    "-m",
                    "Publish alternate lifecycle marketplace",
                ],
                cwd=alternate_publisher,
                env=env,
            )
            run(
                [
                    git,
                    "clone",
                    "--bare",
                    "--no-local",
                    str(alternate_publisher),
                    str(alternate_remote),
                ],
                cwd=temp,
                env=env,
            )
            run(
                [
                    git,
                    f"--git-dir={alternate_remote}",
                    "symbolic-ref",
                    "HEAD",
                    "refs/heads/main",
                ],
                cwd=temp,
                env=env,
            )
            run(
                [git, f"--git-dir={alternate_remote}", "update-server-info"],
                cwd=temp,
                env=env,
            )
            alternate_url = marketplace_url.rsplit("/", 1)[0] + "/alternate.git"
            alternate_marketplace = run_json(
                [
                    codex,
                    "plugin",
                    "marketplace",
                    "add",
                    alternate_url,
                    "--ref",
                    "main",
                    "--json",
                ],
                cwd=project,
                env=env,
            )
            alternate_snapshot = Path(
                alternate_marketplace["installedRoot"]
            ).resolve()
            assert_equal(
                json.loads(
                    (
                        alternate_snapshot
                        / ".agents"
                        / "plugins"
                        / "marketplace.json"
                    ).read_text(encoding="utf-8")
                ).get("name"),
                ALTERNATE_MARKETPLACE_NAME,
                "alternate marketplace identity",
            )
            alternate_install = run_json(
                [codex, "plugin", "add", ALTERNATE_PLUGIN_ID, "--json"],
                cwd=project,
                env=env,
            )
            assert_equal(
                alternate_install.get("version"),
                current_version,
                "alternate installation version",
            )
            alternate_installed_root = Path(
                alternate_install["installedPath"]
            ).resolve()
            alternate_inventory = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            for sibling_id in (PLUGIN_ID, ALTERNATE_PLUGIN_ID):
                sibling = installed_entry(alternate_inventory, sibling_id)
                assert_equal(
                    sibling.get("version"),
                    current_version,
                    f"{sibling_id} sibling version",
                )
                assert_equal(
                    sibling.get("enabled"), True, f"{sibling_id} sibling enabled state"
                )

            alternate_configurator = (
                alternate_installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "configure_native_routing.py"
            )
            alternate_native_command = [
                sys.executable,
                str(alternate_configurator),
                "--codex-bin",
                codex,
                "--codex-home",
                str(codex_home),
                "--allow-incompatible-client",
                "--executor-model",
                "gpt-5.6-luna",
                "--executor-effort",
                "xhigh",
            ]
            alternate_preview = run(
                alternate_native_command, cwd=project, env=env
            )
            if "Dry run only" not in alternate_preview.stdout:
                raise SmokeFailure("Alternate native setup did not report a dry run")
            alternate_apply = run(
                [*alternate_native_command, "--apply"], cwd=project, env=env
            )
            if "Native routing policy installed" not in alternate_apply.stdout:
                raise SmokeFailure("Alternate native setup did not install its policy")
            alternate_state_path = codex_home / ".codex-orchestration-routing.json"
            alternate_state = json.loads(
                alternate_state_path.read_text(encoding="utf-8")
            )
            assert_equal(alternate_state.get("schema"), 7, "alternate state schema")
            assert_equal(
                alternate_state.get("plugin_id"),
                ALTERNATE_PLUGIN_ID,
                "alternate saved plugin identity",
            )
            alternate_status = run(
                [
                    sys.executable,
                    str(alternate_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--status",
                    "--require-effective",
                ],
                cwd=project,
                env=env,
            )
            if f"Plugin identity: {ALTERNATE_PLUGIN_ID}" not in alternate_status.stdout:
                raise SmokeFailure("Alternate native status selected the wrong identity")
            if "Native policy: installed and effective" not in alternate_status.stdout:
                raise SmokeFailure("Alternate native status did not report effectiveness")
            alternate_disable = run(
                [
                    sys.executable,
                    str(alternate_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--disable",
                    "--apply",
                ],
                cwd=project,
                env=env,
            )
            if "Native routing disabled" not in alternate_disable.stdout:
                raise SmokeFailure("Alternate native disable did not report success")
            if alternate_state_path.exists():
                raise SmokeFailure("Alternate native disable retained routing state")
            sibling_inventory = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            canonical_sibling = installed_entry(sibling_inventory)
            assert_equal(
                canonical_sibling.get("enabled"),
                True,
                "canonical sibling preserved after alternate disable",
            )
            run_json(
                [codex, "plugin", "remove", ALTERNATE_PLUGIN_ID, "--json"],
                cwd=project,
                env=env,
            )
            after_alternate_remove = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            installed_entry(after_alternate_remove)
            if any(
                item.get("pluginId") == ALTERNATE_PLUGIN_ID
                for item in after_alternate_remove.get("installed", [])
                if isinstance(item, dict)
            ):
                raise SmokeFailure("Alternate plugin remained after exact removal")
            run_json(
                [
                    codex,
                    "plugin",
                    "marketplace",
                    "remove",
                    ALTERNATE_MARKETPLACE_NAME,
                    "--json",
                ],
                cwd=project,
                env=env,
            )

            fake_codex = write_fake_codex(temp / "fake-codex", project)
            configurator = (
                installed_root
                / "skills"
                / "codex-orchestration"
                / "scripts"
                / "configure_orchestration.py"
            )
            configure_command = [
                sys.executable,
                str(configurator),
                "--scope",
                "personal",
                "--root",
                str(project),
                "--codex-home",
                str(codex_home),
                "--personal-route-names",
                "--executor-model",
                "gpt-5.6-luna",
                "--executor-effort",
                "xhigh",
                "--remove-advisor",
                "--codex-bin",
                str(fake_codex),
            ]
            preview = run(configure_command, cwd=project, env=env)
            if "Dry run only" not in preview.stdout:
                raise SmokeFailure("Installed configurator did not report a dry run")
            if (codex_home / "agents").exists():
                raise SmokeFailure(
                    "Dry run unexpectedly created personal agent configuration"
                )

            applied = run([*configure_command, "--apply"], cwd=project, env=env)
            if "Standalone custom-agent configuration is valid" not in applied.stdout:
                raise SmokeFailure(
                    "Installed configurator did not validate the applied configuration"
                )
            route_suffix = hashlib.sha256(
                os.fsencode(str(codex_home.resolve()))
            ).hexdigest()[:12]
            executor_name = f"codex_orchestration_executor_{route_suffix}"
            executor_file = (
                codex_home
                / "agents"
                / f"codex-orchestration-executor-{route_suffix}.toml"
            )
            executor = executor_file.read_text(encoding="utf-8")
            for expected in (
                f'name = "{executor_name}"',
                'model = "gpt-5.6-luna"',
                'model_reasoning_effort = "xhigh"',
            ):
                if expected not in executor:
                    raise SmokeFailure(f"Generated executor is missing {expected!r}")

            native_command = [
                sys.executable,
                str(native_configurator),
                "--codex-bin",
                codex,
                "--codex-home",
                str(codex_home),
                "--allow-incompatible-client",
                "--executor-agent",
                executor_name,
            ]
            native_preview = run(native_command, cwd=project, env=env)
            if "Dry run only" not in native_preview.stdout:
                raise SmokeFailure("Native configurator did not report a dry run")
            native_applied = run([*native_command, "--apply"], cwd=project, env=env)
            if "Native routing policy installed" not in native_applied.stdout:
                raise SmokeFailure("Native configurator did not activate routing")

            user_config = (codex_home / "config.toml").read_text(encoding="utf-8")
            for expected in (
                "[codex-orchestration managed-policy v1]",
                f'agent_type = "{executor_name}"',
                'fork_turns = "none"',
                'tool_namespace = "agents"',
            ):
                if expected not in user_config:
                    raise SmokeFailure(f"Native config is missing {expected!r}")

            native_status = run(
                [
                    sys.executable,
                    str(native_configurator),
                    "--codex-bin",
                    codex,
                    "--codex-home",
                    str(codex_home),
                    "--status",
                    "--require-effective",
                ],
                cwd=project,
                env=env,
            )
            if "Native policy: installed and effective" not in native_status.stdout:
                raise SmokeFailure("Native status did not report the active preset")

            native_disable = [
                sys.executable,
                str(native_configurator),
                "--codex-bin",
                codex,
                "--codex-home",
                str(codex_home),
                "--disable",
            ]
            disable_preview = run(native_disable, cwd=project, env=env)
            if "Dry run only" not in disable_preview.stdout:
                raise SmokeFailure("Native disable preview was not non-mutating")
            run([*native_disable, "--apply"], cwd=project, env=env)
            disabled_config = (codex_home / "config.toml").read_text(
                encoding="utf-8"
            )
            if "[codex-orchestration managed-policy" in disabled_config:
                raise SmokeFailure("Managed native policy remained after disable")
            if "tool_namespace" in disabled_config:
                raise SmokeFailure("Pre-setup namespace absence was not restored")
            if (codex_home / ".codex-orchestration-routing.json").exists():
                raise SmokeFailure("Native restore state remained after disable")

            remove_roles_command = [
                sys.executable,
                str(configurator),
                "--scope",
                "personal",
                "--root",
                str(project),
                "--codex-home",
                str(codex_home),
                "--personal-route-names",
                "--remove-saved-roles",
            ]
            remove_preview = run(remove_roles_command, cwd=project, env=env)
            if "Dry run only" not in remove_preview.stdout or not executor_file.exists():
                raise SmokeFailure("Saved-role removal preview was not non-mutating")
            run([*remove_roles_command, "--apply"], cwd=project, env=env)
            if executor_file.exists():
                raise SmokeFailure("Managed executor remained after saved-role removal")

            run_json(
                [codex, "plugin", "remove", PLUGIN_ID, "--json"],
                cwd=project,
                env=env,
            )
            after_remove = run_json(
                [codex, "plugin", "list", "--json"], cwd=project, env=env
            )
            if any(
                entry.get("pluginId") == PLUGIN_ID
                for entry in after_remove.get("installed", [])
                if isinstance(entry, dict)
            ):
                raise SmokeFailure("Plugin still appears installed after removal")
            run_json(
                [
                    codex,
                    "plugin",
                    "marketplace",
                    "remove",
                    MARKETPLACE_NAME,
                    "--json",
                ],
                cwd=project,
                env=env,
            )

    print(
        f"PASS: installed {OLD_VERSION}, upgraded to {current_version}, "
        "verified its cache, and ran native plus custom setup/status/cleanup"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
