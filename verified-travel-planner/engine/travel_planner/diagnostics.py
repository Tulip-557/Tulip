"""Local capability diagnostics without exposing credentials or account state."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Mapping, Optional


def default_data_dir(
    environment: Optional[Mapping[str, str]] = None,
    system_name: Optional[str] = None,
    home: Optional[Path] = None,
) -> Path:
    environment = environment if environment is not None else os.environ
    configured = environment.get("TRAVEL_PLANNER_DATA_DIR", "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        if not configured_path.is_absolute():
            raise ValueError("TRAVEL_PLANNER_DATA_DIR must be an absolute path")
        return configured_path.resolve()

    user_home = home or Path.home()
    current_system = system_name or platform.system()
    if current_system == "Darwin":
        return user_home / "Library" / "Application Support" / "travel-planner-mvp"

    xdg_data_home = environment.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else user_home / ".local" / "share"
    return base / "travel-planner-mvp"


def _rail_runtime_status(data_dir: Path) -> dict:
    checkout = data_dir / "mcp-server-12306"
    ready = (checkout / "pyproject.toml").is_file() and (checkout / ".venv").is_dir()
    return {
        "status": "READY" if ready else "MISSING",
        "path": str(checkout),
    }


def _codex_mcp_status(
    command_finder: Callable[[str], Optional[str]],
    command_runner,
) -> dict:
    codex = command_finder("codex")
    if not codex:
        return {
            "status": "UNAVAILABLE",
            "message": "Codex CLI is unavailable; verify MCP registration in the client settings.",
        }

    result = command_runner(
        [codex, "mcp", "get", "12306"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return {"status": "READY"}
    return {
        "status": "MISSING",
        "message": "The 12306 MCP is not registered in Codex.",
    }


def _json_mcp_status(
    candidates: list,
    server_name: str = "12306",
    client_label: str = "the client",
) -> dict:
    """Look for an MCP registration across the given config files, in order.

    ``candidates`` is a list of ``(human_label, Path)`` pairs, highest
    priority first — a client may keep the registration in more than one
    place, and the workspace-level file wins over the user-level one.

    Only the server's own entry is read, and only enough of it to tell whether
    it could start. These files also hold unrelated account and project state,
    none of which belongs in a report.

    The name alone is not enough. An entry hand-written without a ``command``
    is exactly what a user produces when following the setup instructions
    partially, and reporting it READY sends them to restart the client and
    find the tools still absent — the least debuggable outcome available.
    """

    checked = []
    for label, path in candidates:
        checked.append(f"{label} ({path})")
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return {
                "status": "UNVERIFIED",
                "message": f"The {client_label} configuration at {label} could not be read.",
            }
        # 空文件与坏 JSON 要分开报：前者是「没初始化 / 被清空」，后者是「文件坏了」，
        # 用户该做的事完全不同。实测本机 ~/.claude.json 就是一个 0 字节文件。
        if not text.strip():
            return {
                "status": "EMPTY",
                "message": (
                    f"The {client_label} configuration at {label} is empty (0 bytes) — "
                    "the client may not be initialized yet, or its config was cleared. "
                    "Start the client once, then register the MCP server."
                ),
            }
        try:
            config = json.loads(text)
        except json.JSONDecodeError as exc:
            return {
                "status": "UNVERIFIED",
                "message": (
                    f"The {client_label} configuration at {label} is not valid JSON "
                    f"(line {exc.lineno}, column {exc.colno})."
                ),
            }
        servers = config.get("mcpServers")
        if not isinstance(servers, dict) or server_name not in servers:
            continue
        entry = servers.get(server_name)
        if not isinstance(entry, dict) or not str(entry.get("command") or "").strip():
            return {
                "status": "INCOMPLETE",
                "message": (
                    f"The {server_name} entry in {label} has no command, so the "
                    "server cannot start. Point its \"command\" at the rail MCP "
                    "you installed yourself — this Skill ships no installer."
                ),
            }
        return {"status": "READY", "client": client_label, "config": label}
    return {
        "status": "MISSING",
        "message": (
            f"The {server_name} MCP is not registered in {client_label}. "
            "Looked in: " + "; ".join(checked)
        ),
    }


def _claude_code_mcp_status(
    server_name: str = "12306",
    config_path: Optional[Path] = None,
) -> dict:
    """Claude Code keeps MCP servers in ``~/.claude.json``."""
    path = config_path or Path.home() / ".claude.json"
    return _json_mcp_status([("~/.claude.json", path)], server_name, "claude-code")


def _zcode_mcp_status(
    server_name: str = "12306",
    config_path: Optional[Path] = None,
    workspace: Optional[Path] = None,
) -> dict:
    """ZCode (Zhipu) reads MCP servers from the workspace-root ``.mcp.json``.

    Same family as Claude Code's project file, plus a project-level
    ``.zcode/mcp.json``. Verified against the installed client on 2026-09-27:
    the workspace-root file is the one that takes effect.
    """
    if config_path is not None:
        candidates = [(str(config_path), config_path)]
    else:
        ws = workspace or Path.cwd()
        candidates = [
            ("<workspace>/.mcp.json", ws / ".mcp.json"),
            ("<workspace>/.zcode/mcp.json", ws / ".zcode" / "mcp.json"),
        ]
    return _json_mcp_status(candidates, server_name, "zcode")


def _workbuddy_mcp_status(
    server_name: str = "12306",
    config_path: Optional[Path] = None,
) -> dict:
    """WorkBuddy keeps MCP servers in ``~/.workbuddy/mcp.json``."""
    path = config_path or Path.home() / ".workbuddy" / "mcp.json"
    return _json_mcp_status([("~/.workbuddy/mcp.json", path)], server_name, "workbuddy")


def detect_clients(
    environment: Optional[Mapping[str, str]] = None,
    command_finder: Callable[[str], Optional[str]] = shutil.which,
    home: Optional[Path] = None,
) -> list:
    """List every Agent client that left a trace on this machine, best first.

    Detection is a guess by nature: a developer routinely has several clients
    installed at once (Claude Code, Codex, ZCode, WorkBuddy), so "which one is
    running me" cannot be read off the filesystem. Reporting **all** the
    candidates lets the caller see that, instead of silently trusting one.

    Order: explicit environment markers first (the client sets these itself),
    then per-client install traces, most specific first.
    """

    environment = environment if environment is not None else os.environ
    user_home = home or Path.home()
    found = []

    def add(name: str) -> None:
        if name not in found:
            found.append(name)

    if environment.get("CLAUDECODE") or environment.get("CLAUDE_CODE_ENTRYPOINT"):
        add("claude-code")
    if environment.get("CODEX_SANDBOX") or environment.get("CODEX_HOME"):
        add("codex")
    if environment.get("ZCODE") or environment.get("ZCODE_HOME"):
        add("zcode")
    if command_finder("codex"):
        add("codex")
    if (user_home / ".zcode").is_dir():
        add("zcode")
    if (user_home / ".workbuddy").is_dir():
        add("workbuddy")
    if (user_home / ".claude.json").is_file():
        add("claude-code")
    return found or ["generic"]


def detect_client(
    environment: Optional[Mapping[str, str]] = None,
    command_finder: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """Guess which Agent client is running this Skill (single value).

    Prefer ``detect_clients`` when you can act on more than one — this returns
    only the best guess, which is wrong whenever several clients coexist.
    """

    return detect_clients(environment=environment, command_finder=command_finder)[0]


_CLIENT_LABELS = {
    "codex": "Codex",
    "claude-code": "Claude Code",
    "zcode": "ZCode",
    "workbuddy": "WorkBuddy",
}


def build_doctor_report(
    amap_status: dict,
    *,
    data_dir: Optional[Path] = None,
    browser_status: str = "unknown",
    client: str = "auto",
    mcp_config_path: Optional[Path] = None,
    claude_config_path: Optional[Path] = None,   # 旧名，等价于 mcp_config_path
    workspace: Optional[Path] = None,
    command_finder: Callable[[str], Optional[str]] = shutil.which,
    command_runner=subprocess.run,
) -> dict:
    resolved_data_dir = data_dir or default_data_dir()
    python_ready = sys.version_info >= (3, 9)
    rail_runtime = _rail_runtime_status(resolved_data_dir)
    client_candidates = detect_clients(command_finder=command_finder)
    auto_ambiguous = False
    if client == "auto":
        client = client_candidates[0]
        # 装了好几个客户端时，「哪个在跑我」是读不出来的。照常用第一个，
        # 但把这个不确定性摊开，而不是假装知道。
        auto_ambiguous = len(client_candidates) > 1
    config_path = mcp_config_path or claude_config_path
    if client == "codex":
        rail_registration = _codex_mcp_status(command_finder, command_runner)
    elif client == "claude-code":
        rail_registration = _claude_code_mcp_status(config_path=config_path)
    elif client == "zcode":
        rail_registration = _zcode_mcp_status(
            config_path=config_path, workspace=workspace)
    elif client == "workbuddy":
        rail_registration = _workbuddy_mcp_status(config_path=config_path)
    else:
        rail_registration = {
            "status": "UNVERIFIED",
            "message": "Verify the stdio MCP in the selected Agent client.",
        }

    if rail_runtime["status"] == "READY" and rail_registration["status"] == "READY":
        rail_status = "READY"
    elif rail_runtime["status"] == "MISSING":
        rail_status = "MISSING"
    else:
        rail_status = "PARTIAL"

    browser_map = {
        "available": {
            "status": "AVAILABLE",
            "message": "The Agent reported an interactive browser capability.",
        },
        "unavailable": {
            "status": "UNAVAILABLE",
            "message": "Browser-backed OTA and Xiaohongshu research will be skipped.",
        },
        "unknown": {
            "status": "UNVERIFIED",
            "message": "The CLI cannot inspect Agent browser tools; confirm in the client.",
        },
    }
    browser = browser_map[browser_status]

    actions = []
    if not python_ready:
        actions.append("Install Python 3.9 or newer.")
    if amap_status.get("status") not in {"CONFIGURED", "READY"}:
        actions.append("Configure an Amap Web Service API key.")
    if rail_runtime["status"] == "MISSING":
        actions.append(
            "Rail (12306) MCP is optional and NOT bundled with this Skill — "
            "this package ships no installer. Rail facts stay at evidence level "
            "[C]/[D] unless you supply a third-party MCP yourself (a stdio "
            "server runs local code, so audit it before registering)."
        )
    elif rail_registration["status"] == "EMPTY":
        actions.append(
            "The "
            + _CLIENT_LABELS.get(client, "client")
            + " configuration is empty — start the client once so it writes its "
              "config, then register the 12306 MCP."
        )
    elif rail_registration["status"] == "MISSING":
        actions.append(
            "Register the installed 12306 stdio MCP in "
            + _CLIENT_LABELS.get(client, "the client")
            + "."
        )
    elif rail_registration["status"] == "INCOMPLETE":
        # A registration that exists but cannot start needs a different fix
        # from one that is absent, so say which.
        actions.append(
            rail_registration.get("message")
            or "Complete the 12306 MCP registration; its entry cannot start."
        )
    if browser["status"] == "UNVERIFIED":
        actions.append("Confirm whether the Agent client provides an interactive browser.")
    if auto_ambiguous:
        actions.append(
            "Detected more than one Agent client (%s). The MCP check above used "
            "'%s'; pass --client explicitly to check another."
            % (", ".join(client_candidates), client)
        )

    core_ready = python_ready and amap_status.get("status") in {"CONFIGURED", "READY"}
    overall = "READY" if core_ready and rail_status == "READY" else "PARTIAL"
    if not core_ready:
        overall = "NOT_READY"

    return {
        "status": overall,
        "core": {
            "python": {
                "status": "READY" if python_ready else "UNSUPPORTED",
                "version": platform.python_version(),
            },
            "amap": amap_status,
        },
        "client": client,
        # 本机装了哪些客户端（不只 auto 选中的那个）——检测是猜的，把全部痕迹摊开
        "client_candidates": client_candidates,
        "client_ambiguous": auto_ambiguous,
        "rail_mcp": {
            "status": rail_status,
            "runtime": rail_runtime,
            "registration": rail_registration,
        },
        "browser": browser,
        "actions": actions,
    }
