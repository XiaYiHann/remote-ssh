"""SSH command generation and connection helpers."""

from __future__ import annotations

from typing import Any, Mapping, Optional

from remote_ssh_cli.config import JsonDict, SshCommands, SzuAutomationError
from remote_ssh_cli.utils import _norm


def build_ssh_commands(job_id: str, proxy: str, key_path: str = "id_rsa") -> SshCommands:
    """Generate OS-specific SSH commands and ssh_config snippet."""
    if not job_id:
        raise SzuAutomationError("job_id is required for ssh command")
    if not proxy:
        raise SzuAutomationError("ssh proxy path is empty")
    windows = (
        "ssh -i {key} -o ProxyCommand='ncat --proxy-type socks5 --proxy {proxy} %h %p' "
        "root@{job_id}"
    ).format(key=key_path, proxy=proxy, job_id=job_id)
    linux = (
        "ssh -i {key} -o ProxyCommand='nc -X 5 -x {proxy} %h %p' root@{job_id}"
    ).format(key=key_path, proxy=proxy, job_id=job_id)
    ssh_config_linux = "\n".join(
        [
            f"Host {job_id}",
            f"  HostName {job_id}",
            "  User root",
            f"  ProxyCommand nc -X 5 -x {proxy} %h %p",
            "  PreferredAuthentications publickey",
            f"  IdentityFile {key_path}",
        ]
    )
    return SshCommands(
        job_id=job_id,
        proxy=proxy,
        key_path=key_path,
        windows=windows,
        linux=linux,
        macos=linux,
        ssh_config_linux=ssh_config_linux,
    )


def job_id_from_create_result(result: Any) -> str:
    """Extract job id from the creation API response."""
    if isinstance(result, str):
        job_id = _norm(result)
        if job_id:
            return job_id
    if not isinstance(result, dict):
        raise SzuAutomationError(
            f"cannot extract job id from create result: {result!r}"
        )

    for key in ("id", "jobId", "taskId", "debugJobId"):
        job_id = _norm(result.get(key))
        if job_id:
            return job_id

    for key in ("job", "task", "debugJob"):
        nested = result.get(key)
        if isinstance(nested, dict):
            try:
                return job_id_from_create_result(nested)
            except SzuAutomationError:
                pass

    raise SzuAutomationError(f"cannot extract job id from create result: {result}")


def ssh_state_label(ssh_state: Any) -> str:
    """Map raw sshState to a readable label."""
    value = _norm(ssh_state)
    if value == "1":
        return "ready"
    if value in ("", "None", "null"):
        return "unknown"
    return "not_ready"


def _cluster_proxy(cluster: Mapping[str, Any]) -> str:
    for key in ("sshProxyPath", "sshProxy", "proxyPath"):
        proxy = _norm(cluster.get(key))
        if proxy:
            return proxy.replace("socks5://", "").replace("http://", "").replace("https://", "")
    return ""


def prefer_debug_ssh_proxy(proxy: str, access_status: Mapping[str, Any]) -> str:
    """Prefer the runtime sshUrl IP over the domain proxy."""
    ssh_url = _norm(access_status.get("sshUrl"))
    if not ssh_url:
        return proxy
    ssh_url = ssh_url.replace("socks5://", "").replace("http://", "").replace("https://", "")
    if ":" in ssh_url:
        return ssh_url
    if ":" not in proxy:
        return proxy
    port = proxy.rsplit(":", 1)[1]
    return f"{ssh_url}:{port}" if port else proxy
