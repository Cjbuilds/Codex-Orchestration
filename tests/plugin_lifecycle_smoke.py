#!/usr/bin/env python3
"""Exercise a real Codex plugin install, Git upgrade, and runtime setup.

A disposable bare Git marketplace is served over loopback HTTP. The real Codex
CLI installs 0.2.0, runs its documented marketplace-upgrade command after 0.3.0
is pushed to that Git remote, installs the refreshed package, verifies its cache,
and runs the installed configurator and saved-role cleanup in an empty project.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
from typing import Any, Iterator


REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = REPO_ROOT / "plugins" / "codex-orchestration"
PLUGIN_ID = "codex-orchestration@codex-orchestration"
MARKETPLACE_NAME = "codex-orchestration"
OLD_RELEASE = "c7e5435f32eee3cec04e1759d16228b5202c8780"
OLD_VERSION = "0.2.0"
COMMAND_TIMEOUT_SECONDS = 60


class SmokeFailure(RuntimeError):
    """A lifecycle assertion or external command failed."""


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


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise SmokeFailure(f"{message}: expected {expected!r}, got {actual!r}")


def ignored(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or path.suffix == ".pyc"
        or path.name == ".DS_Store"
    )


def file_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.read_bytes(),
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


def write_fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys

if sys.argv[1:] == ["--version"]:
    print("codex-cli lifecycle-smoke")
    raise SystemExit(0)
if sys.argv[1:3] == ["debug", "models"]:
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
""",
        encoding="utf-8",
    )
    path.chmod(0o755)


def installed_entry(payload: dict[str, Any]) -> dict[str, Any]:
    matches = [
        entry
        for entry in payload.get("installed", [])
        if isinstance(entry, dict) and entry.get("pluginId") == PLUGIN_ID
    ]
    if len(matches) != 1:
        raise SmokeFailure(f"Expected one discovered {PLUGIN_ID!r}, got {matches!r}")
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
    assert_equal(current_version, "0.3.0", "checkout release version")

    with tempfile.TemporaryDirectory(prefix="codex-orchestration-lifecycle-") as raw:
        temp = Path(raw)
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

            installed_root = Path(new_install["installedPath"]).resolve()
            assert_equal(
                file_tree(installed_root),
                file_tree(PLUGIN_ROOT),
                "installed package contents",
            )

            fake_codex = temp / "fake-codex"
            write_fake_codex(fake_codex)
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
                "project",
                "--root",
                str(project),
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
            if (project / ".codex").exists():
                raise SmokeFailure(
                    "Dry run unexpectedly created project configuration"
                )

            applied = run([*configure_command, "--apply"], cwd=project, env=env)
            if "Standalone custom-agent configuration is valid" not in applied.stdout:
                raise SmokeFailure(
                    "Installed configurator did not validate the applied configuration"
                )
            executor_file = (
                project
                / ".codex"
                / "agents"
                / "codex-orchestration-executor.toml"
            )
            executor = executor_file.read_text(encoding="utf-8")
            for expected in (
                'name = "codex_orchestration_executor"',
                'model = "gpt-5.6-luna"',
                'model_reasoning_effort = "xhigh"',
            ):
                if expected not in executor:
                    raise SmokeFailure(f"Generated executor is missing {expected!r}")

            remove_roles_command = [
                sys.executable,
                str(configurator),
                "--scope",
                "project",
                "--root",
                str(project),
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
        "discovered the plugin, verified its cache, and ran setup plus cleanup"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1)
