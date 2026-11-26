#!/bin/sh
# One-command setup for the optional operator-side board watcher.
#
#   listener-setup.sh install     clone-or-update the repo, write
#                                 ~/.agent-bus-watch/watch.env (door URL,
#                                 bearer, boards) from `claude mcp get`,
#                                 stop an already-loaded service, run one
#                                 watcher cycle, install the launchd
#                                 (macOS) or systemd user (Linux) service.
#   listener-setup.sh uninstall   stop and remove that service.
#                                 --purge also removes ~/.agent-bus-watch.
#   listener-setup.sh status      report config and service state.
#
# Every knob can also be preset in the environment, which is how this
# script is tested non-interactively:
#
#   AGENT_BUS_REPO_URL            where to clone from
#   AGENT_BUS_SRC_DIR             where the repo lives locally
#   AGENT_BUS_SERVER_NAME         the MCP server name passed to `claude mcp get`
#   AGENT_BUS_BOARDS              comma-separated boards (empty = all)
#   AGENT_BUS_DOOR_URL/BEARER     skip `claude mcp get` entirely
#   AGENT_BUS_MCP_GET             command instead of `claude mcp get` (tests)
#   AGENT_BUS_RUN_ONCE / _CMD     skip or replace the run-once check
#   AGENT_BUS_SERVICE_MANAGER     launchd | systemd | none (default: detect)
#   AGENT_BUS_NO_ACTIVATE         write service files only, no launchctl/systemctl
#   AGENT_BUS_WATCH_DIR           state directory (default ~/.agent-bus-watch)
#   AGENT_BUS_LAUNCHD_DIR         default ~/Library/LaunchAgents
#   AGENT_BUS_SYSTEMD_DIR         default ~/.config/systemd/user
#   AGENT_BUS_LAUNCHD_LABEL       default local.agent-bus.board-watch
#   AGENT_BUS_PLIST_PATH          PATH written into the service (default: this shell's PATH)

set -u
# The door bearer must never appear in shell xtrace output.
case $- in *x*) set +x ;; esac

REPO_URL_DEFAULT="https://github.com/rickkeller/agent-bus-mcp.git"
SRC_DEFAULT="$HOME/agent-bus-mcp"
WATCH_DIR_DEFAULT="$HOME/.agent-bus-watch"
WATCH_ENV_NAME="watch.env"
LAUNCHD_LABEL_DEFAULT="local.agent-bus.board-watch"
SYSTEMD_UNIT_DEFAULT="agent-bus-board-watch.service"

REPO_URL=${AGENT_BUS_REPO_URL:-$REPO_URL_DEFAULT}
SRC_DIR=${AGENT_BUS_SRC_DIR:-$SRC_DEFAULT}
WATCH_DIR=${AGENT_BUS_WATCH_DIR:-$WATCH_DIR_DEFAULT}
LAUNCHD_LABEL=${AGENT_BUS_LAUNCHD_LABEL:-$LAUNCHD_LABEL_DEFAULT}
SYSTEMD_UNIT=${AGENT_BUS_SYSTEMD_UNIT:-$SYSTEMD_UNIT_DEFAULT}
SERVER_NAME=${AGENT_BUS_SERVER_NAME:-}
BOARDS=${AGENT_BUS_BOARDS:-}
NO_ACTIVATE=${AGENT_BUS_NO_ACTIVATE:-}
LAUNCHD_DIR=${AGENT_BUS_LAUNCHD_DIR:-$HOME/Library/LaunchAgents}
SYSTEMD_DIR=${AGENT_BUS_SYSTEMD_DIR:-$HOME/.config/systemd/user}

log() {
    printf '%s\n' "$*"
}

warn() {
    printf '%s\n' "$*: warning" >&2
}

die() {
    printf '%s\n' "$*" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

service_path() {
    # PATH written into launchd/systemd: override, else this shell, else a
    # generic default that still includes Homebrew and ~/.local/bin.
    if [ -n "${AGENT_BUS_PLIST_PATH:-}" ]; then
        printf '%s' "$AGENT_BUS_PLIST_PATH"
    elif [ -n "${PATH:-}" ]; then
        printf '%s' "$PATH"
    else
        printf '%s' "/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    fi
}

# ---------------------------------------------------------------------------
# locate the checkout with examples/board-watch.py

has_watcher() {
    [ -f "$1/examples/board-watch.py" ]
}

fetch_source() {
    if has_watcher "$SRC_DIR"; then
        if ! git -C "$SRC_DIR" pull --ff-only >/dev/null 2>&1; then
            warn "could not fast-forward $SRC_DIR; using the copy as it is"
        fi
        return 0
    fi
    log "cloning the repo into $SRC_DIR"
    parent=$(dirname "$SRC_DIR")
    [ -d "$parent" ] || mkdir -p "$parent" 2>/dev/null || true
    if ! git clone -- "$REPO_URL" "$SRC_DIR" 2>/dev/null; then
        die "could not clone $REPO_URL into $SRC_DIR; run setup from inside the checkout, or clone it yourself first"
    fi
}

resolve_source() {
    # If we were run from inside a checkout, we are the checkout.
    script_dir=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || script_dir=""
    if [ -n "$script_dir" ] && has_watcher "$script_dir"; then
        SRC_DIR="$script_dir"
        return 0
    fi
    [ -d "$SRC_DIR" ] || fetch_source
    has_watcher "$SRC_DIR" || die "$SRC_DIR has no examples/board-watch.py"
}

resolve_source_quiet() {
    # Like resolve_source but never clones: uninstall must not fetch anything.
    script_dir=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd) || script_dir=""
    if [ -n "$script_dir" ] && has_watcher "$script_dir"; then
        SRC_DIR="$script_dir"
    elif [ -d "$SRC_DIR" ] && has_watcher "$SRC_DIR"; then
        :
    else
        SRC_DIR=""
    fi
}

# ---------------------------------------------------------------------------
# door config: env overrides, else `claude mcp get <server>`, never echoed

read_door_config() {
    DOOR_URL=""
    BEARER=""
    if [ -n "${AGENT_BUS_DOOR_URL:-}" ] && [ -n "${AGENT_BUS_BEARER:-}" ]; then
        DOOR_URL=$AGENT_BUS_DOOR_URL
        BEARER=$AGENT_BUS_BEARER
        return 0
    fi
    if [ -z "$SERVER_NAME" ]; then
        if [ -t 0 ]; then
            printf 'MCP server name to read the door from [agent-bus]: '
            read -r SERVER_NAME || SERVER_NAME=""
        fi
        [ -n "$SERVER_NAME" ] || SERVER_NAME="agent-bus"
    fi
    if [ -n "${AGENT_BUS_MCP_GET:-}" ]; then
        # shellcheck disable=SC2086  # word splitting lets tests pass a command
        mcp_output=$($AGENT_BUS_MCP_GET "$SERVER_NAME" 2>/dev/null) || mcp_output=""
    else
        mcp_output=$(claude mcp get "$SERVER_NAME" 2>/dev/null) || mcp_output=""
    fi
    # Real `claude mcp get` (Claude Code 2.1) prints a block with a `URL:`
    # line and an `Authorization: Bearer` header line.
    DOOR_URL=$(printf '%s\n' "$mcp_output" | sed -n 's/^[[:space:]]*URL:[[:space:]]*//p' | head -n 1 | tr -d '\r')
    BEARER=$(printf '%s\n' "$mcp_output" | sed -n 's/.*[Aa]uthorization:[[:space:]]*[Bb]earer[[:space:]]*//p' | head -n 1 | tr -d '\r')
    unset mcp_output
    case $DOOR_URL in
        http://*|https://*) ;;
        *) die "no door URL for MCP server '$SERVER_NAME'; configure it with 'claude mcp add' or set AGENT_BUS_DOOR_URL and AGENT_BUS_BEARER yourself" ;;
    esac
    [ -n "$BEARER" ] || die "no bearer token for MCP server '$SERVER_NAME'; set AGENT_BUS_DOOR_URL and AGENT_BUS_BEARER yourself"
}

# ---------------------------------------------------------------------------
# watch.env: managed keys updated, operator's other lines kept

write_watch_env() {
    env_dir=$1
    env_file=$env_dir/$WATCH_ENV_NAME
    old_umask=$(umask)
    umask 077
    mkdir -p "$env_dir"
    chmod 700 "$env_dir" 2>/dev/null
    tmp=$env_file.tmp.$$
    preserved_boards=""
    boards_present=0
    trap 'rm -f "$tmp"; umask "'"$old_umask"'"' EXIT
    if [ -f "$env_file" ]; then
        : >"$tmp"
        while IFS= read -r line || [ -n "$line" ]; do
            case $line in
                AGENT_BUS_DOOR_URL=*|AGENT_BUS_BEARER=*) continue ;;
                AGENT_BUS_BOARDS=*)
                    boards_present=1
                    preserved_boards=${line#AGENT_BUS_BOARDS=}
                    continue ;;
            esac
            printf '%s\n' "$line" >>"$tmp"
        done <"$env_file"
        if [ "$boards_present" -eq 1 ] && [ -n "$BOARDS" ] && [ "$BOARDS" != "$preserved_boards" ]; then
            warn "$env_file already sets AGENT_BUS_BOARDS; your AGENT_BUS_BOARDS=$BOARDS was not applied"
        fi
    else
        : >"$tmp"
    fi
    {
        printf 'AGENT_BUS_DOOR_URL=%s\n' "$DOOR_URL"
        printf 'AGENT_BUS_BEARER=%s\n' "$BEARER"
        # The boards line survives a rerun when it is already there; otherwise
        # this run's value lands, and an empty one is valid (every board).
        if [ "$boards_present" -eq 1 ]; then
            printf 'AGENT_BUS_BOARDS=%s\n' "$preserved_boards"
        else
            printf 'AGENT_BUS_BOARDS=%s\n' "$BOARDS"
        fi
    } >>"$tmp"
    mv "$tmp" "$env_file"
    chmod 600 "$env_file"
    umask "$old_umask"
    trap - EXIT
    # Sanity: the values must parse back out.
    if [ "$(grep -c '^AGENT_BUS_DOOR_URL=' "$env_file")" -ne 1 ] ||
       [ "$(grep -c '^AGENT_BUS_BEARER=' "$env_file")" -ne 1 ]; then
        die "could not verify $env_file after writing"
    fi
}

check_watch_env() {
    env_file=$WATCH_DIR/$WATCH_ENV_NAME
    [ -f "$env_file" ] || die "$env_file is missing"
    mode=$(stat -c '%a' "$env_file" 2>/dev/null || stat -f '%Lp' "$env_file" 2>/dev/null)
    [ "$mode" = "600" ] || die "$env_file is mode $mode; it must be 600"
    grep -q '^AGENT_BUS_DOOR_URL=http' "$env_file" || die "$env_file has no usable AGENT_BUS_DOOR_URL"
    grep -q '^AGENT_BUS_BEARER=..' "$env_file" || die "$env_file has no bearer token"
    grep -q '^AGENT_BUS_BOARDS=' "$env_file" || die "$env_file has no AGENT_BUS_BOARDS line"
    log "checked $env_file (mode 600, door URL and bearer present)"
}

run_once_check() {
    if [ "${AGENT_BUS_RUN_ONCE:-1}" = "0" ]; then
        log "run-once check skipped (AGENT_BUS_RUN_ONCE=0)"
        return 0
    fi
    if [ -n "${AGENT_BUS_RUN_ONCE_CMD:-}" ]; then
        # shellcheck disable=SC2086
        $AGENT_BUS_RUN_ONCE_CMD
    else
        AGENT_BUS_IDLE_BACKOFF_SECONDS=5 \
        AGENT_BUS_FAIL_BACKOFF_SECONDS=5 \
        AGENT_BUS_WATCH_DIR="$WATCH_DIR" \
        python3 "$SRC_DIR/examples/board-watch.py" run-once --state-dir "$WATCH_DIR"
    fi
}

# ---------------------------------------------------------------------------
# services

detect_manager() {
    if [ -n "${AGENT_BUS_SERVICE_MANAGER:-}" ]; then
        MANAGER=$AGENT_BUS_SERVICE_MANAGER
    else
        MANAGER=none
        case "$(uname -s 2>/dev/null)" in
            Darwin) MANAGER=launchd ;;
            Linux) have systemctl && MANAGER=systemd ;;
        esac
    fi
    case $MANAGER in
        launchd|systemd|none) ;;
        *) die "AGENT_BUS_SERVICE_MANAGER must be launchd, systemd, or none" ;;
    esac
}

write_launchd_plist() {
    template="$SRC_DIR/examples/launchd/local.agent-bus.board-watch.plist"
    [ -f "$template" ] || die "no launchd template at $template"
    [ -d "$LAUNCHD_DIR" ] || mkdir -p "$LAUNCHD_DIR"
    plist=$LAUNCHD_DIR/$LAUNCHD_LABEL.plist
    python_bin=$(command -v python3) || die "python3 is required"
    path_value=$(service_path)
    python3 - "$template" "$plist" "$SRC_DIR/examples/board-watch.py" "$HOME" "$path_value" "$python_bin" "$WATCH_DIR" "$LAUNCHD_LABEL" <<'PY' || die "could not write the launchd plist"
import sys
from pathlib import Path
from xml.sax.saxutils import escape

template, dest, script, home, path, python, watch_dir, label = sys.argv[1:]
text = Path(template).read_text(encoding="utf-8")
old_path = "/opt/homebrew/bin:/usr/local/bin:/Users/USER/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
if old_path not in text:
    sys.exit("launchd template PATH line was not found")
text = text.replace(old_path, escape(path), 1)
text = text.replace("/Users/USER/agent-bus-mcp/examples/board-watch.py", escape(script))
text = text.replace("/usr/bin/python3", escape(python))
text = text.replace("/Users/USER/.agent-bus-watch", escape(watch_dir))
text = text.replace("/Users/USER", escape(home))
default_label = "local.agent-bus.board-watch"
if label != default_label:
    text = text.replace(default_label, escape(label))
marker = "    <string>run</string>\n"
insert = (
    marker
    + f"    <string>--state-dir</string>\n    <string>{escape(watch_dir)}</string>\n"
)
if marker not in text:
    sys.exit("launchd template is missing the run argument")
text = text.replace(marker, insert, 1)
Path(dest).write_text(text, encoding="utf-8")
PY
    if have plutil; then
        plutil -lint "$plist" >/dev/null || die "the generated $plist is not a valid plist"
    fi
}

launchd_running() {
    # launchctl print reports `state = running` with no quotes around the
    # state; the same dump also has indented `state = active` lines.
    launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null | grep -Eq '^[[:space:]]*state = running[[:space:]]*$'
}

install_launchd() {
    write_launchd_plist
    log "wrote $plist"
    [ "$NO_ACTIVATE" = "1" ] && return 0
    launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null
    if ! launchctl bootstrap "gui/$(id -u)" "$plist"; then
        warn "launchctl bootstrap failed; start it by hand with: launchctl bootstrap gui/$(id -u) $plist"
        return 0
    fi
    if launchd_running; then
        log "launchd service $LAUNCHD_LABEL is running"
    else
        warn "$LAUNCHD_LABEL is loaded but not reported running yet"
    fi
}

uninstall_launchd() {
    [ "${AGENT_BUS_NO_ACTIVATE:-}" = "1" ] || launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null
    remove_service_files
}

write_systemd_unit() {
    [ -d "$SYSTEMD_DIR" ] || mkdir -p "$SYSTEMD_DIR"
    unit=$SYSTEMD_DIR/$SYSTEMD_UNIT
    python_bin=$(command -v python3) || die "python3 is required"
    path_value=$(service_path)
    python3 - "$unit" "$SRC_DIR/examples/board-watch.py" "$HOME" "$path_value" "$python_bin" "$WATCH_DIR" <<'PY' || die "could not write the systemd unit"
import sys
from pathlib import Path

dest, script, home, path, python, watch_dir = sys.argv[1:]

def q(value: str) -> str:
    # systemd ExecStart / Environment quoting, and % is a specifier.
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'

lines = [
    "[Unit]",
    "Description=Agent Bus board watcher",
    "",
    "[Service]",
    f"ExecStart={q(python)} {q(script)} run --state-dir {q(watch_dir)}",
    f"WorkingDirectory={q(home)}",
    f"Environment=PATH={q(path)}",
    "Restart=always",
    "RestartSec=30",
    "",
    "[Install]",
    "WantedBy=default.target",
    "",
]
Path(dest).write_text("\n".join(lines), encoding="utf-8")
PY
}

install_systemd() {
    write_systemd_unit
    log "wrote $unit"
    [ "$NO_ACTIVATE" = "1" ] && return 0
    systemctl --user daemon-reload || true
    if ! systemctl --user enable --now "$SYSTEMD_UNIT"; then
        warn "could not enable $SYSTEMD_UNIT; enable it by hand with: systemctl --user enable --now $SYSTEMD_UNIT"
        return 0
    fi
    if [ "$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null)" = "active" ]; then
        log "systemd service $SYSTEMD_UNIT is active"
    else
        warn "$SYSTEMD_UNIT is enabled but not active"
    fi
}

remove_service_files() {
    if [ -f "$LAUNCHD_DIR/$LAUNCHD_LABEL.plist" ]; then
        rm -f "$LAUNCHD_DIR/$LAUNCHD_LABEL.plist"
        log "removed $LAUNCHD_DIR/$LAUNCHD_LABEL.plist"
    fi
    if [ -f "$SYSTEMD_DIR/$SYSTEMD_UNIT" ]; then
        rm -f "$SYSTEMD_DIR/$SYSTEMD_UNIT"
        log "removed $SYSTEMD_DIR/$SYSTEMD_UNIT"
    fi
}

uninstall_systemd() {
    if [ "${AGENT_BUS_NO_ACTIVATE:-}" != "1" ]; then
        systemctl --user disable --now "$SYSTEMD_UNIT" 2>/dev/null
        systemctl --user daemon-reload 2>/dev/null
    fi
    remove_service_files
}

launchd_loaded() {
    # `launchctl print` succeeds while the label is loaded, running or not.
    launchctl print "gui/$(id -u)/$LAUNCHD_LABEL" >/dev/null 2>&1
}

stop_loaded_service() {
    # A loaded service keeps its own wait_unread cycles on the door, and a
    # run-once behind it can time out. Stop it before the live check; the
    # install steps below load and start it again. The stopped service's
    # open wait still holds the door for up to 25s (a disconnect does not
    # interrupt it), so the run-once check uses a short wait and retries
    # while that call drains. Skipped for AGENT_BUS_NO_ACTIVATE like every
    # launchctl/systemctl call here, and only for the selected manager, so
    # a `none` or test run cannot stop a live user service.
    [ "$NO_ACTIVATE" = "1" ] && return 0
    case $MANAGER in
        launchd)
            if launchd_loaded; then
                log "stopping the loaded launchd service $LAUNCHD_LABEL before the run-once check"
                launchctl bootout "gui/$(id -u)/$LAUNCHD_LABEL" 2>/dev/null ||
                    warn "could not stop $LAUNCHD_LABEL; the run-once check may run behind it"
                log "the stopped watcher's open wait can hold the door up to 25s; the run-once check waits it out"
            fi
            ;;
        systemd)
            if [ "$(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null)" = "active" ]; then
                log "stopping the loaded systemd service $SYSTEMD_UNIT before the run-once check"
                systemctl --user stop "$SYSTEMD_UNIT" 2>/dev/null ||
                    warn "could not stop $SYSTEMD_UNIT; the run-once check may run behind it"
                log "the stopped watcher's open wait can hold the door up to 25s; the run-once check waits it out"
            fi
            ;;
    esac
    return 0
}

# ---------------------------------------------------------------------------
# subcommands

cmd_install() {
    resolve_source
    if ! have python3; then
        die "python3 is required"
    fi
    if [ -z "${AGENT_BUS_BOARDS:-}" ] && [ -t 0 ]; then
        printf 'Boards to watch, comma-separated [Enter watches every board]: '
        read -r BOARDS || BOARDS=""
    fi
    read_door_config
    write_watch_env "$WATCH_DIR"
    check_watch_env
    detect_manager
    stop_loaded_service
    run_once_check || die "the run-once check failed; fix the door or config above, then rerun"
    log "run-once check passed"
    case $MANAGER in
        launchd) install_launchd ;;
        systemd) install_systemd ;;
        *)
            log "no service manager detected; start it by hand with:"
            log "  python3 $SRC_DIR/examples/board-watch.py run --state-dir $WATCH_DIR"
            log "or set AGENT_BUS_SERVICE_MANAGER=launchd|systemd and rerun"
            ;;
    esac
    log "installed. start/stop: see $SRC_DIR/examples/BOARD-WATCH.md"
    log "uninstall: $0 uninstall"
}

cmd_uninstall() {
    resolve_source_quiet
    detect_manager
    case $MANAGER in
        launchd) uninstall_launchd ;;
        systemd) uninstall_systemd ;;
        *)
            # Remove leftover unit files without talking to launchctl/systemctl,
            # so a test or AGENT_BUS_SERVICE_MANAGER=none run cannot stop a
            # live user service.
            remove_service_files
            ;;
    esac
    if [ -n "$SRC_DIR" ] && [ -f "$WATCH_DIR/listener.pid" ] && [ "${AGENT_BUS_NO_ACTIVATE:-}" != "1" ]; then
        python3 "$SRC_DIR/examples/board-watch.py" stop --state-dir "$WATCH_DIR" 2>/dev/null || true
    fi
    case ${1:-} in
        --purge)
            if [ -d "$WATCH_DIR" ]; then
                rm -rf "$WATCH_DIR"
                log "purged $WATCH_DIR (config, logs, and the seen set)"
            else
                log "$WATCH_DIR is already gone"
            fi
            ;;
        *)
            log "kept $WATCH_DIR (config and seen set); remove it by hand or rerun with --purge"
            ;;
    esac
    log "uninstalled"
}

cmd_status() {
    env_file=$WATCH_DIR/$WATCH_ENV_NAME
    if [ -f "$env_file" ] && grep -q '^AGENT_BUS_DOOR_URL=' "$env_file"; then
        log "config: $env_file (mode $(stat -c '%a' "$env_file" 2>/dev/null || stat -f '%Lp' "$env_file" 2>/dev/null))"
        log "boards: $(sed -n 's/^AGENT_BUS_BOARDS=//p' "$env_file" | head -n 1)"
        log "door:   $(sed -n 's/^AGENT_BUS_DOOR_URL=//p' "$env_file" | head -n 1)"
        log "bearer: set $(grep -q '^AGENT_BUS_BEARER=..' "$env_file" && printf '(non-empty)' || printf '(MISSING)')"
    else
        log "config: none at $env_file"
    fi
    detect_manager
    case $MANAGER in
        launchd)
            if launchd_running; then log "service: launchd $LAUNCHD_LABEL running"
            else log "service: launchd not running (plist at $LAUNCHD_DIR/$LAUNCHD_LABEL.plist)" ; fi
            ;;
        systemd)
            log "service: $(systemctl --user is-active "$SYSTEMD_UNIT" 2>/dev/null || echo inactive) ($SYSTEMD_UNIT)"
            ;;
        *)
            log "service: none installed (no launchd/systemd detected; use AGENT_BUS_SERVICE_MANAGER)"
            ;;
    esac
    if [ -f "$WATCH_DIR/listener.pid" ]; then
        log "raw runner: pid $(cat "$WATCH_DIR/listener.pid") (from board-watch.py run)"
    fi
    log "log: $WATCH_DIR/listener.log"
}

case ${1:-} in
    install)    shift; cmd_install "$@" ;;
    uninstall)  shift; cmd_uninstall "$@" ;;
    status)     cmd_status ;;
    *) sed -n '4,10p' "$0" ;;
esac
