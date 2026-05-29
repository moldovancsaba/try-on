from __future__ import annotations

import os
import shlex
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from model_paths import get_app_root


@dataclass(frozen=True)
class ManagedService:
    key: str
    label: str
    launch_label: str
    process_match: str
    launchd_plist_name: str
    stdout_log: str
    stderr_log: str
    start_command: str
    restart_command: str
    oneshot_command: str | None = None


def _repo_root(app_root: Path | None = None) -> Path:
    return app_root or get_app_root()


def _service_definitions(app_root: Path | None = None) -> dict[str, ManagedService]:
    root = _repo_root(app_root)
    python_bin = root / ".venv311" / "bin" / "python"
    app_log_out = root / "queue" / "logs" / "app.stdout.log"
    app_log_err = root / "queue" / "logs" / "app.stderr.log"
    worker_log_out = root / "queue" / "logs" / "worker.stdout.log"
    worker_log_err = root / "queue" / "logs" / "worker.stderr.log"
    app_cmd = (
        f"cd {shlex.quote(str(root))} && "
        "export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 PYTORCH_ENABLE_MPS_FALLBACK=1 SMF_CATVTON_USE_MPS=1 && "
        f"nohup {shlex.quote(str(python_bin))} -u {shlex.quote(str(root / 'app.py'))} "
        f"> {shlex.quote(str(app_log_out))} 2> {shlex.quote(str(app_log_err))} < /dev/null &"
    )
    worker_cmd = (
        f"cd {shlex.quote(str(root))} && "
        f"nohup {shlex.quote(str(python_bin))} {shlex.quote(str(root / 'scripts' / 'tryon_queue_worker.py'))} "
        f"> {shlex.quote(str(worker_log_out))} 2> {shlex.quote(str(worker_log_err))} < /dev/null &"
    )
    worker_once_cmd = (
        f"cd {shlex.quote(str(root))} && "
        f"nohup {shlex.quote(str(python_bin))} {shlex.quote(str(root / 'scripts' / 'tryon_queue_worker.py'))} --once "
        f"> {shlex.quote(str(worker_log_out))} 2> {shlex.quote(str(worker_log_err))} < /dev/null &"
    )
    return {
        "app": ManagedService(
            key="app",
            label="App Server",
            launch_label="com.tryon.app-server",
            process_match=str(root / "app.py"),
            launchd_plist_name="com.tryon.app-server.plist",
            stdout_log=str(app_log_out),
            stderr_log=str(app_log_err),
            start_command=app_cmd,
            restart_command=f"pkill -f {shlex.quote(str(root / 'app.py'))} >/dev/null 2>&1 || true; sleep 1; {app_cmd}",
        ),
        "worker": ManagedService(
            key="worker",
            label="Queue Worker",
            launch_label="com.tryon.camera-worker",
            process_match=str(root / "scripts" / "tryon_queue_worker.py"),
            launchd_plist_name="com.tryon.camera-worker.plist",
            stdout_log=str(worker_log_out),
            stderr_log=str(worker_log_err),
            start_command=worker_cmd,
            restart_command=f"pkill -f {shlex.quote(str(root / 'scripts' / 'tryon_queue_worker.py'))} >/dev/null 2>&1 || true; sleep 1; {worker_cmd}",
            oneshot_command=worker_once_cmd,
        ),
    }


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _shell_background(command: str) -> None:
    subprocess.Popen(["bash", "-lc", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _launchctl_label(service: ManagedService) -> str:
    return f"gui/{os.getuid()}/{service.launch_label}"


def _install_launch_agent(service: ManagedService, app_root: Path | None = None) -> Path:
    root = _repo_root(app_root)
    source = root / "launchd" / service.launchd_plist_name
    target = Path.home() / "Library" / "LaunchAgents" / service.launchd_plist_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        target.write_bytes(source.read_bytes())
    return target


def _launchctl_load(service: ManagedService, app_root: Path | None = None) -> None:
    target = _install_launch_agent(service, app_root)
    _run(["launchctl", "unload", str(target)])
    result = _run(["launchctl", "load", str(target)])
    if result.returncode not in (0,):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"launchctl load failed for {service.launch_label}")


def _launchctl_kickstart(service: ManagedService) -> None:
    result = _run(["launchctl", "kickstart", "-k", _launchctl_label(service)])
    if result.returncode not in (0,):
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"launchctl kickstart failed for {service.launch_label}")


def _pgrep_pid(match: str) -> int | None:
    proc = _run(["pgrep", "-fo", match])
    if proc.returncode != 0:
        return None
    try:
        return int((proc.stdout or "").strip().splitlines()[0])
    except Exception:
        return None


def _pid_metadata(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"pid": None, "uptimeSeconds": None}
    proc = _run(["ps", "-o", "etime=", "-o", "pid=", "-p", str(pid)])
    if proc.returncode != 0 or not proc.stdout.strip():
        return {"pid": pid, "uptimeSeconds": None}
    parts = proc.stdout.strip().split()
    etime = parts[0] if parts else ""
    return {"pid": pid, "uptimeSeconds": _parse_etime_seconds(etime)}


def _parse_etime_seconds(value: str) -> int | None:
    raw = (value or "").strip()
    if not raw:
        return None
    days = 0
    if "-" in raw:
        day_part, raw = raw.split("-", 1)
        try:
            days = int(day_part)
        except Exception:
            return None
    chunks = raw.split(":")
    try:
        if len(chunks) == 2:
            hours = 0
            minutes, seconds = (int(chunks[0]), int(chunks[1]))
        elif len(chunks) == 3:
            hours, minutes, seconds = (int(chunks[0]), int(chunks[1]), int(chunks[2]))
        else:
            return None
    except Exception:
        return None
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _launch_agent_status(service: ManagedService) -> dict[str, Any]:
    uid = os.getuid()
    proc = _run(["launchctl", "print", f"gui/{uid}/{service.launch_label}"])
    installed_path = Path.home() / "Library" / "LaunchAgents" / service.launchd_plist_name
    bundled_path = _repo_root() / "launchd" / service.launchd_plist_name
    return {
        "configured": bundled_path.exists(),
        "installed": installed_path.exists(),
        "loaded": proc.returncode == 0,
    }


def get_managed_services_status(app_root: Path | None = None, *, current_process_is_app: bool = False) -> dict[str, Any]:
    root = _repo_root(app_root)
    services: dict[str, Any] = {}
    for key, service in _service_definitions(root).items():
        pid = os.getpid() if key == "app" and current_process_is_app else _pgrep_pid(service.process_match)
        pid_info = _pid_metadata(pid)
        launchd_info = _launch_agent_status(service)
        services[key] = {
            "key": key,
            "label": service.label,
            "running": pid is not None,
            "healthy": pid is not None,
            "pid": pid_info["pid"],
            "uptimeSeconds": pid_info["uptimeSeconds"],
            "launchd": launchd_info,
            "stdoutLogPath": service.stdout_log,
            "stderrLogPath": service.stderr_log,
        }
    return services


def perform_service_action(target: str, action: str, app_root: Path | None = None) -> dict[str, Any]:
    root = _repo_root(app_root)
    service = _service_definitions(root).get(target)
    if not service:
        raise ValueError(f"unsupported target: {target}")

    normalized = (action or "").strip().lower()
    if normalized == "start":
        _launchctl_load(service, root)
        _launchctl_kickstart(service)
    elif normalized == "restart":
        _launchctl_load(service, root)
        _launchctl_kickstart(service)
    elif normalized == "run_now":
        if not service.oneshot_command:
            raise ValueError(f"unsupported action for {target}: {action}")
        _shell_background(service.oneshot_command)
    else:
        raise ValueError(f"unsupported action: {action}")

    return {
        "target": target,
        "action": normalized,
        "acceptedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
