"""Typer CLI entry point."""

from __future__ import annotations

import getpass
import json
import time
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console
from rich.json import JSON as RichJSON
from rich.table import Table

from remote_ssh_cli.client import (
    BrowserApiClient,
    build_payload,
    list_resource_options,
    login,
    resolve_config,
    resolve_ssh_info,
    submit_payload,
    summarize,
    wait_for_ssh_info,
)
from remote_ssh_cli.config import (
    DEFAULT_GPU,
    DEFAULT_IMAGE,
    DEFAULT_KEY,
    DEFAULT_MOUNT,
    DEFAULT_STORAGE,
    DEFAULT_TEAM,
    SzuAutomationError,
    TargetConfig,
)
from remote_ssh_cli.ssh import job_id_from_create_result
from remote_ssh_cli.utils import default_key_path

app = typer.Typer(
    help="Manage GPU debug jobs and SSH connections on SZU AI Cloud",
    no_args_is_help=True,
)
console = Console()


def _launch_browser(headed: bool, proxy: Optional[str]) -> Any:
    from playwright.sync_api import sync_playwright

    launch_kwargs: dict[str, Any] = {
        "headless": not headed,
        "args": ["--no-sandbox", "--disable-blink-features=AutomationControlled"],
    }
    if proxy:
        launch_kwargs["proxy"] = {"server": proxy}

    pw = sync_playwright().start()
    browser = pw.chromium.launch(**launch_kwargs)
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 720},
    )
    page = context.new_page()
    return pw, browser, page


def _print_json(data: dict[str, Any]) -> None:
    console.print(RichJSON.from_data(data))


def _print_table(data: list[dict[str, Any]], title: str) -> None:
    if not data:
        console.print(f"[yellow]{title}: no data[/yellow]")
        return
    table = Table(title=title, show_lines=True)
    for key in data[0].keys():
        table.add_column(key)
    for row in data:
        table.add_row(*[str(row.get(k, "")) for k in data[0].keys()])
    console.print(table)


@app.command()
def create(
    submit: bool = typer.Option(False, "--submit", help="Actually create the debug job"),
    wait_ssh: bool = typer.Option(False, "--wait-ssh", help="Poll until SSH is ready"),
    wait_timeout_seconds: int = typer.Option(600, "--wait-timeout-seconds"),
    poll_seconds: int = typer.Option(15, "--poll-seconds"),
    headed: bool = typer.Option(False, "--headed", help="Show browser window"),
    proxy: Optional[str] = typer.Option(None, "--proxy", envvar="SZU_AICLOUD_PROXY"),
    username: Optional[str] = typer.Option(None, "--username", envvar="SZU_AICLOUD_USERNAME"),
    password: Optional[str] = typer.Option(None, "--password", envvar="SZU_AICLOUD_PASSWORD"),
    key_path: str = typer.Option(default_key_path(), "--key-path"),
    team: Optional[str] = typer.Option(
        DEFAULT_TEAM,
        "--team",
        help="Team name; defaults to the first available team",
    ),
    job_name: Optional[str] = typer.Option(None, "--job-name"),
    image: str = typer.Option(DEFAULT_IMAGE, "--image"),
    storage_from: Optional[str] = typer.Option(
        DEFAULT_STORAGE,
        "--storage-from",
        help="File-storage path; defaults to auto-detecting the current team's storage",
    ),
    mount_to: Optional[str] = typer.Option(
        DEFAULT_MOUNT,
        "--mount-to",
        help="Container mount path; defaults to the selected file-storage path",
    ),
    gpu: str = typer.Option(DEFAULT_GPU, "--gpu"),
    ssh_key: str = typer.Option(DEFAULT_KEY, "--ssh-key"),
    duration_hours: int = typer.Option(1, "--duration-hours"),
    card_num: int = typer.Option(1, "--card-num"),
    cluster_id: Optional[str] = typer.Option(None, "--cluster-id"),
    cluster_name: Optional[str] = typer.Option(None, "--cluster-name"),
    save_payload: Optional[Path] = typer.Option(None, "--save-payload"),
) -> None:
    """Create (or dry-run) a SZU AI Cloud debug job."""
    actual_username = username or input("SZU username: ").strip()
    actual_password = password or getpass.getpass("SZU password: ")
    if not actual_username or not actual_password:
        raise typer.BadParameter("username and password are required")

    target = TargetConfig(
        team_name=team,
        job_name=job_name or f"remote-ssh-debug-{int(time.time())}",
        image=image,
        storage_from=storage_from,
        mount_to=mount_to,
        gpu_keyword=gpu,
        ssh_key_keyword=ssh_key,
        duration_hours=duration_hours,
        card_num=card_num,
        cluster_id=cluster_id,
        cluster_name=cluster_name,
    )

    pw, browser, page = _launch_browser(headed, proxy)
    try:
        login(page, actual_username, actual_password)
        client = BrowserApiClient(page)

        resolved = resolve_config(client, target)
        payload = build_payload(target, resolved)
        summary = summarize(target, resolved, payload, will_submit=submit)

        _print_json(summary)

        if save_payload:
            with open(save_payload, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.write("\n")
            console.print(f"[green]Payload saved to {save_payload}[/green]")

        if not submit:
            console.print("[yellow][dry-run] payload resolved; no job was created[/yellow]")
            return

        result = submit_payload(client, payload)
        job_id = job_id_from_create_result(result)
        console.print(f"[green]Job created: {job_id}[/green]")

        if wait_ssh:
            ssh_info = wait_for_ssh_info(
                client, job_id, key_path, wait_timeout_seconds, poll_seconds
            )
        else:
            ssh_info = resolve_ssh_info(client, job_id, key_path=key_path)
        _print_json({"ssh": ssh_info})
    finally:
        browser.close()
        pw.stop()


@app.command()
def ssh(
    job_id: str = typer.Argument(..., help="Existing debug job id"),
    wait_ssh: bool = typer.Option(False, "--wait-ssh", help="Poll until SSH is ready"),
    wait_timeout_seconds: int = typer.Option(600, "--wait-timeout-seconds"),
    poll_seconds: int = typer.Option(15, "--poll-seconds"),
    headed: bool = typer.Option(False, "--headed", help="Show browser window"),
    proxy: Optional[str] = typer.Option(None, "--proxy", envvar="SZU_AICLOUD_PROXY"),
    username: Optional[str] = typer.Option(None, "--username", envvar="SZU_AICLOUD_USERNAME"),
    password: Optional[str] = typer.Option(None, "--password", envvar="SZU_AICLOUD_PASSWORD"),
    key_path: str = typer.Option(default_key_path(), "--key-path"),
) -> None:
    """Resolve SSH commands for an existing debug job."""
    actual_username = username or input("SZU username: ").strip()
    actual_password = password or getpass.getpass("SZU password: ")
    if not actual_username or not actual_password:
        raise typer.BadParameter("username and password are required")

    pw, browser, page = _launch_browser(headed, proxy)
    try:
        login(page, actual_username, actual_password)
        client = BrowserApiClient(page)
        if wait_ssh:
            ssh_info = wait_for_ssh_info(
                client, job_id, key_path, wait_timeout_seconds, poll_seconds
            )
        else:
            ssh_info = resolve_ssh_info(client, job_id, key_path=key_path)
        _print_json({"ssh": ssh_info})
    finally:
        browser.close()
        pw.stop()


@app.command(name="list-resources")
def list_resources(
    headed: bool = typer.Option(False, "--headed", help="Show browser window"),
    proxy: Optional[str] = typer.Option(None, "--proxy", envvar="SZU_AICLOUD_PROXY"),
    username: Optional[str] = typer.Option(None, "--username", envvar="SZU_AICLOUD_USERNAME"),
    password: Optional[str] = typer.Option(None, "--password", envvar="SZU_AICLOUD_PASSWORD"),
    cluster_id: Optional[str] = typer.Option(None, "--cluster-id"),
    cluster_name: Optional[str] = typer.Option(None, "--cluster-name"),
) -> None:
    """List available GPU training resource options."""
    actual_username = username or input("SZU username: ").strip()
    actual_password = password or getpass.getpass("SZU password: ")
    if not actual_username or not actual_password:
        raise typer.BadParameter("username and password are required")

    target = TargetConfig(cluster_id=cluster_id, cluster_name=cluster_name)

    pw, browser, page = _launch_browser(headed, proxy)
    try:
        login(page, actual_username, actual_password)
        client = BrowserApiClient(page)
        options = list_resource_options(client, target)
        _print_table(options, title="Available GPU Resources")
    finally:
        browser.close()
        pw.stop()


def main() -> None:
    try:
        app()
    except SzuAutomationError as exc:
        console.print(f"[red][error] {exc}[/red]")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
