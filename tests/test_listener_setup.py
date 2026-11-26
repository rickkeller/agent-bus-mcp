"""scripts/listener-setup.sh: env-file writing, MCP parsing, idempotency, and service renders.

The script is exercised through subprocess with every knob preset in the
environment, so no test touches the real ~/.agent-bus-watch, a live door,
or launchctl/systemctl (AGENT_BUS_SERVICE_MANAGER and AGENT_BUS_NO_ACTIVATE).
"""

import os
import plistlib
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "listener-setup.sh"
REPO = SCRIPT.parents[1]
WATCH_ENV_REL = Path(".agent-bus-watch") / "watch.env"

DOOR_V1 = "https://door.example.invalid/mcp"
TOKEN_V1 = "fake-bearer-token-one"
TOKEN_V2 = "fake-bearer-token-two"


class FakeMcp:
    """A stand-in for `claude mcp get`; the door and token live in files."""

    def __init__(self, tmp_path: Path) -> None:
        self.script = tmp_path / "fake-mcp-get"
        self.door_file = tmp_path / "fake-door.txt"
        self.token_file = tmp_path / "fake-token.txt"
        self.door_file.write_text(DOOR_V1)
        self.token_file.write_text(TOKEN_V1)
        self.script.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    f'door=$(cat "{self.door_file}")',
                    f'token=$(cat "{self.token_file}")',
                    'printf "%s:\\n" "$1"',
                    'printf "  Scope: Local config\\n"',
                    'printf "  Type: http\\n"',
                    'printf "  URL: %s\\n" "$door"',
                    'printf "  Headers:\\n"',
                    '[ -n "$token" ] && printf "    Authorization: Bearer %s\\n" "$token"',
                ]
            )
        )
        self.script.chmod(0o755)

    def rotate(self, token: str) -> None:
        self.token_file.write_text(token)

    def __str__(self) -> str:
        return str(self.script)


@pytest.fixture
def fake_mcp(tmp_path: Path) -> FakeMcp:
    return FakeMcp(tmp_path)


def setup_env(fake_mcp: FakeMcp, **overrides: str) -> dict[str, str]:
    env = {
        "AGENT_BUS_MCP_GET": str(fake_mcp),
        "AGENT_BUS_SERVICE_MANAGER": "none",
        "AGENT_BUS_RUN_ONCE": "0",
    }
    env.update(overrides)
    return env


def run_script(
    args: list[str],
    env: dict[str, str],
    home: Path,
    cwd: Path | None = None,
    script: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    for key in list(merged):
        if key.startswith("AGENT_BUS_"):
            del merged[key]
    merged["HOME"] = str(home)
    merged.update(env)
    return subprocess.run(
        ["bash", str(script or SCRIPT), *args],
        env=merged,
        cwd=str(cwd or home),
        capture_output=True,
        text=True,
        timeout=60,
    )


def env_file(home: Path) -> Path:
    return home / WATCH_ENV_REL


def watch_values(home: Path) -> dict[str, str]:
    values = {}
    for line in env_file(home).read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


def test_install_writes_the_env_file_from_the_mcp_config_and_never_prints_the_bearer(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    result = run_script(["install"], setup_env(fake_mcp), tmp_path)
    assert result.returncode == 0, result.stderr
    path = env_file(tmp_path)
    assert path.exists()
    values = watch_values(tmp_path)
    assert values["AGENT_BUS_DOOR_URL"] == DOOR_V1
    assert values["AGENT_BUS_BEARER"] == TOKEN_V1
    assert values["AGENT_BUS_BOARDS"] == ""  # empty is the default: every board the hint names
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / ".agent-bus-watch").stat().st_mode) == 0o700
    combined = result.stdout + result.stderr
    assert TOKEN_V1 not in combined


def test_explicit_boards_land_in_the_file(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    result = run_script(["install"], setup_env(fake_mcp, AGENT_BUS_BOARDS="alpha,beta"), tmp_path)
    assert result.returncode == 0, result.stderr
    assert watch_values(tmp_path)["AGENT_BUS_BOARDS"] == "alpha,beta"


def test_rerun_refreshes_the_token_keeps_custom_lines_and_does_not_clobber_boards(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    first = run_script(["install"], setup_env(fake_mcp, AGENT_BUS_BOARDS="alpha"), tmp_path)
    assert first.returncode == 0, first.stderr
    with open(env_file(tmp_path), "a") as handle:
        handle.write("MY_TURN_TOOL=custom-value\n")
    fake_mcp.rotate(TOKEN_V2)
    second = run_script(["install"], setup_env(fake_mcp, AGENT_BUS_BOARDS="beta"), tmp_path)
    assert second.returncode == 0, second.stderr
    values = watch_values(tmp_path)
    assert values["AGENT_BUS_BEARER"] == TOKEN_V2
    assert values["AGENT_BUS_BOARDS"] == "alpha"  # not clobbered by the rerun
    assert values["MY_TURN_TOOL"] == "custom-value"
    assert "was not applied" in second.stderr


def test_install_fails_without_a_bearer_and_writes_nothing(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    fake_mcp.rotate("")
    result = run_script(["install"], setup_env(fake_mcp), tmp_path)
    assert result.returncode != 0
    assert not env_file(tmp_path).exists()
    assert TOKEN_V1 not in result.stdout + result.stderr


def test_run_once_failure_fails_the_install(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    failing = tmp_path / "fail-run-once"
    failing.write_text("#!/bin/sh\necho run-once failed by design\nexit 3\n")
    failing.chmod(0o755)
    result = run_script(
        ["install"],
        setup_env(fake_mcp, AGENT_BUS_RUN_ONCE_CMD=str(failing), AGENT_BUS_RUN_ONCE="1"),
        tmp_path,
    )
    assert result.returncode != 0
    assert "run-once" in result.stderr


def test_uninstall_when_nothing_installed_is_safe(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    result = run_script(["uninstall"], setup_env(fake_mcp), tmp_path)
    assert result.returncode == 0, result.stderr
    assert "uninstalled" in result.stdout
    assert not (tmp_path / "agent-bus-mcp").exists()


def test_uninstall_purge_removes_the_state_dir(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    run_script(["install"], setup_env(fake_mcp), tmp_path)
    assert env_file(tmp_path).exists()
    result = run_script(["uninstall", "--purge"], setup_env(fake_mcp), tmp_path)
    assert result.returncode == 0, result.stderr
    assert not env_file(tmp_path).exists()


def test_status_reports_the_config_without_the_bearer(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    run_script(["install"], setup_env(fake_mcp), tmp_path)
    result = run_script(["status"], setup_env(fake_mcp), tmp_path)
    assert result.returncode == 0, result.stderr
    assert f"door:   {DOOR_V1}" in result.stdout
    assert f"config: {env_file(tmp_path)} (mode 600)" in result.stdout
    assert "Type: ext2/ext3" not in result.stdout
    assert "bearer: set" in result.stdout
    assert TOKEN_V1 not in result.stdout + result.stderr


def test_launchd_plist_is_rendered_with_real_paths(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    launchd_dir = tmp_path / "LaunchAgents"
    expected_path = "/opt/homebrew/bin:/usr/local/bin:/custom/bin"
    result = run_script(
        ["install"],
        setup_env(
            fake_mcp,
            AGENT_BUS_SERVICE_MANAGER="launchd",
            AGENT_BUS_NO_ACTIVATE="1",
            AGENT_BUS_LAUNCHD_DIR=str(launchd_dir),
            AGENT_BUS_PLIST_PATH=expected_path,
        ),
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    plist = launchd_dir / "local.agent-bus.board-watch.plist"
    assert plist.exists()
    text = plist.read_text()
    with open(plist, "rb") as handle:
        data = plistlib.load(handle)
    args = data["ProgramArguments"]
    assert args[1] == str(REPO / "examples" / "board-watch.py")
    assert args[2] == "run"
    assert args[3] == "--state-dir"
    assert args[4] == str(tmp_path / ".agent-bus-watch")
    assert data["EnvironmentVariables"]["PATH"] == expected_path
    assert "/opt/homebrew/bin" in data["EnvironmentVariables"]["PATH"]
    assert "/Users/USER" not in text
    if shutil.which("plutil"):
        lint = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
        assert lint.returncode == 0, lint.stdout


def test_systemd_unit_is_written_and_uninstall_removes_it(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    systemd_dir = tmp_path / "systemd-user"
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="systemd",
        AGENT_BUS_NO_ACTIVATE="1",
        AGENT_BUS_SYSTEMD_DIR=str(systemd_dir),
        AGENT_BUS_SRC_DIR=str(REPO),  # keep SRC at the repo; only the unit file moves
    )
    install = run_script(["install"], env, tmp_path)
    assert install.returncode == 0, install.stderr
    unit = systemd_dir / "agent-bus-board-watch.service"
    assert unit.exists()
    text = unit.read_text()
    watch_dir = str(tmp_path / ".agent-bus-watch")
    assert str(REPO / "examples" / "board-watch.py") in text
    assert f"--state-dir \"{watch_dir}\"" in text or f'--state-dir "{watch_dir}"' in text
    assert "Environment=PATH=" in text
    assert "Restart=always" in text
    # A custom state dir must be written literally, not the default %h path.
    custom_dir = tmp_path / "custom-watch"
    custom = run_script(
        ["install"],
        setup_env(
            fake_mcp,
            AGENT_BUS_SERVICE_MANAGER="systemd",
            AGENT_BUS_NO_ACTIVATE="1",
            AGENT_BUS_SYSTEMD_DIR=str(systemd_dir),
            AGENT_BUS_SRC_DIR=str(REPO),
            AGENT_BUS_WATCH_DIR=str(custom_dir),
        ),
        tmp_path,
    )
    assert custom.returncode == 0, custom.stderr
    custom_text = unit.read_text()
    assert str(custom_dir) in custom_text
    assert "%h/.agent-bus-watch" not in custom_text
    uninstall = run_script(["uninstall"], env, tmp_path)
    assert uninstall.returncode == 0, uninstall.stderr
    assert not unit.exists()
    again = run_script(["uninstall"], env, tmp_path)
    assert again.returncode == 0, again.stderr


def test_a_fresh_clone_from_a_local_git_origin(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()
    (origin / "examples").mkdir()
    (origin / "scripts").mkdir()
    shutil.copy2(SCRIPT, origin / "scripts" / "listener-setup.sh")
    shutil.copy2(REPO / "examples" / "board-watch.py", origin / "examples" / "board-watch.py")
    subprocess.run(["git", "init", "-q", "-b", "main", str(origin)], check=True)
    subprocess.run(
        ["git", "-C", str(origin), "add", "scripts/listener-setup.sh", "examples/board-watch.py"], check=True
    )
    subprocess.run(
        ["git", "-C", str(origin), "-c", "user.email=t@example.com", "-c", "user.name=t",
         "commit", "-qm", "fixture repo"],
        check=True,
    )
    launcher = tmp_path / "standalone" / "listener-setup.sh"
    launcher.parent.mkdir()
    shutil.copy2(SCRIPT, launcher)
    env = setup_env(fake_mcp, AGENT_BUS_REPO_URL=str(origin), AGENT_BUS_SRC_DIR=str(tmp_path / "cloned"))
    result = run_script(["install"], env, tmp_path, cwd=tmp_path / "standalone", script=launcher)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "cloned" / "examples" / "board-watch.py").exists()
    assert env_file(tmp_path).exists()

def test_xtrace_does_not_print_the_bearer(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    merged = os.environ.copy()
    for key in list(merged):
        if key.startswith("AGENT_BUS_"):
            del merged[key]
    merged["HOME"] = str(tmp_path)
    merged.update(setup_env(fake_mcp))
    result = subprocess.run(
        ["bash", "-x", str(SCRIPT), "install"],
        env=merged,
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    assert TOKEN_V1 not in combined
    assert env_file(tmp_path).exists()


def test_service_manager_rejects_shell_metacharacters(tmp_path: Path, fake_mcp: FakeMcp) -> None:
    result = run_script(
        ["install"],
        setup_env(fake_mcp, AGENT_BUS_SERVICE_MANAGER="systemd;true"),
        tmp_path,
    )
    assert result.returncode != 0
    assert "must be launchd, systemd, or none" in result.stderr
    assert TOKEN_V1 not in result.stdout + result.stderr


def make_shim_dir(tmp_path: Path, name: str, body: str) -> Path:
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir(exist_ok=True)
    shim = shim_dir / name
    shim.write_text(body)
    shim.chmod(0o755)
    return shim_dir


def run_once_recorder(tmp_path: Path, events: Path) -> Path:
    once = tmp_path / "run-once"
    once.write_text(f'#!/bin/sh\nprintf \'%s\\n\' "run-once" >> "{events}"\n')
    once.chmod(0o755)
    return once


def launchd_shim(tmp_path: Path, events: Path, loaded: bool) -> Path:
    print_case = (
        "    print) printf 'state = running\\n'; exit 0 ;;"
        if loaded
        else "    print) exit 1 ;;"
    )
    return make_shim_dir(
        tmp_path,
        "launchctl",
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "launchctl $*" >> "{events}"\n'
        "case \"$1\" in\n"
        f"{print_case}\n"
        "    *) exit 0 ;;\n"
        "esac\n",
    )


def test_install_stops_a_loaded_launchd_service_before_the_run_once_check(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    events = tmp_path / "events.log"
    shim_dir = launchd_shim(tmp_path, events, loaded=True)
    once = run_once_recorder(tmp_path, events)
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="launchd",
        AGENT_BUS_RUN_ONCE="1",
        AGENT_BUS_RUN_ONCE_CMD=str(once),
        AGENT_BUS_LAUNCHD_DIR=str(tmp_path / "LaunchAgents"),
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    result = run_script(["install"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "stopping the loaded launchd service" in result.stdout
    lines = events.read_text().splitlines()
    assert lines[0].startswith("launchctl print")  # the loaded check
    bootout = next(i for i, line in enumerate(lines) if line.startswith("launchctl bootout"))
    run_once = lines.index("run-once")
    bootstrap = next(i for i, line in enumerate(lines) if line.startswith("launchctl bootstrap"))
    assert bootout < run_once, lines
    assert run_once < bootstrap, lines


def test_install_skips_the_stop_when_no_launchd_service_is_loaded(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    events = tmp_path / "events.log"
    shim_dir = launchd_shim(tmp_path, events, loaded=False)
    once = run_once_recorder(tmp_path, events)
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="launchd",
        AGENT_BUS_RUN_ONCE="1",
        AGENT_BUS_RUN_ONCE_CMD=str(once),
        AGENT_BUS_LAUNCHD_DIR=str(tmp_path / "LaunchAgents"),
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    result = run_script(["install"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "stopping the loaded" not in result.stdout
    lines = events.read_text().splitlines()
    run_once = lines.index("run-once")
    assert not any(line.startswith("launchctl bootout") for line in lines[:run_once]), lines


def test_install_says_the_check_waits_out_the_stopped_watchers_open_wait(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    events = tmp_path / "events.log"
    shim_dir = launchd_shim(tmp_path, events, loaded=True)
    once = run_once_recorder(tmp_path, events)
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="launchd",
        AGENT_BUS_RUN_ONCE="1",
        AGENT_BUS_RUN_ONCE_CMD=str(once),
        AGENT_BUS_LAUNCHD_DIR=str(tmp_path / "LaunchAgents"),
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    result = run_script(["install"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    stop = next(i for i, line in enumerate(lines) if "stopping the loaded" in line)
    drained = next(i for i, line in enumerate(lines) if "open wait can hold the door up to 25s" in line)
    passed = next(i for i, line in enumerate(lines) if "run-once check passed" in line)
    assert stop < drained < passed, lines


def test_install_stops_a_loaded_systemd_service_before_the_run_once_check(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    events = tmp_path / "events.log"
    shim_dir = make_shim_dir(
        tmp_path,
        "systemctl",
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "systemctl $*" >> "{events}"\n'
        'case "$2" in\n'
        "    is-active) printf 'active\\n'; exit 0 ;;\n"
        "    *) exit 0 ;;\n"
        "esac\n",
    )
    once = run_once_recorder(tmp_path, events)
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="systemd",
        AGENT_BUS_RUN_ONCE="1",
        AGENT_BUS_RUN_ONCE_CMD=str(once),
        AGENT_BUS_SYSTEMD_DIR=str(tmp_path / "systemd-user"),
        AGENT_BUS_SRC_DIR=str(REPO),
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    result = run_script(["install"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "stopping the loaded systemd service" in result.stdout
    lines = events.read_text().splitlines()
    assert lines[0].startswith("systemctl --user is-active")
    stop = next(i for i, line in enumerate(lines) if line.startswith("systemctl --user stop"))
    run_once = lines.index("run-once")
    enable = next(i for i, line in enumerate(lines) if "enable --now" in line)
    assert stop < run_once, lines
    assert run_once < enable, lines


def test_install_with_no_activate_never_calls_the_service_manager(
    tmp_path: Path, fake_mcp: FakeMcp
) -> None:
    events = tmp_path / "events.log"
    shim_dir = launchd_shim(tmp_path, events, loaded=True)
    once = run_once_recorder(tmp_path, events)
    env = setup_env(
        fake_mcp,
        AGENT_BUS_SERVICE_MANAGER="launchd",
        AGENT_BUS_NO_ACTIVATE="1",
        AGENT_BUS_RUN_ONCE="1",
        AGENT_BUS_RUN_ONCE_CMD=str(once),
        AGENT_BUS_LAUNCHD_DIR=str(tmp_path / "LaunchAgents"),
        PATH=f"{shim_dir}:{os.environ['PATH']}",
    )
    result = run_script(["install"], env, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "stopping the loaded" not in result.stdout
    assert events.read_text().splitlines() == ["run-once"]
