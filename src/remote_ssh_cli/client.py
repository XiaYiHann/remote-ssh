"""Playwright browser client and SZU AI Cloud API orchestration."""

from __future__ import annotations

import time
from typing import Any, List, Optional

from remote_ssh_cli.config import (
    CREATE_URL,
    JsonDict,
    ResolvedConfig,
    SzuAutomationError,
    TargetConfig,
    TASK_DEBUG_URL,
)
from remote_ssh_cli.selectors import (
    select_image,
    select_public_pool_and_power,
    select_ssh_key,
    select_storage_bucket,
    select_team,
)
from remote_ssh_cli.ssh import (
    build_ssh_commands,
    prefer_debug_ssh_proxy,
    ssh_state_label,
    _cluster_proxy,
)
from remote_ssh_cli.utils import _ensure_list, _id, _norm, _norm_lower, image_label, unwrap_response


class BrowserApiClient:
    """Thin wrapper around Playwright page.evaluate for backend API calls."""

    def __init__(self, page: Any) -> None:
        self.page = page

    def request(self, method: str, path: str, data: Optional[JsonDict] = None) -> Any:
        result = self.page.evaluate(
            """
            async ({method, path, data}) => {
              const user = JSON.parse(localStorage.getItem("USREINFO") || "{}") || {};
              const headers = {"Accept": "application/json, text/plain, */*"};
              if (data !== null && data !== undefined) {
                headers["Content-Type"] = "application/json;charset=UTF-8";
              }
              if (user.token) headers["token"] = user.token;
              const response = await fetch(path, {
                method,
                headers,
                credentials: "include",
                body: data !== null && data !== undefined ? JSON.stringify(data) : undefined
              });
              const text = await response.text();
              let payload = text;
              try { payload = JSON.parse(text); } catch (_) {}
              return {ok: response.ok, status: response.status, payload};
            }
            """,
            {"method": method.upper(), "path": path, "data": data},
        )
        if not result.get("ok"):
            raise SzuAutomationError(
                f"{method.upper()} {path} failed: status={result.get('status')} "
                f"body={result.get('payload')}"
            )
        return unwrap_response(result.get("payload"))

    def post(self, path: str, data: Optional[JsonDict] = None) -> Any:
        return self.request("POST", path, data or {})

    def get(self, path: str) -> Any:
        return self.request("GET", path, None)

    def selected_cluster(self) -> Optional[JsonDict]:
        value = self.page.evaluate(
            """
            () => {
              const raw = localStorage.getItem("selectedCluster");
              if (!raw) return null;
              try { return JSON.parse(raw); } catch (_) { return null; }
            }
            """
        )
        return value if isinstance(value, dict) else None


def login(page: Any, username: str, password: str) -> None:
    """Log in through the SZU AI Cloud web UI."""
    page.goto(TASK_DEBUG_URL, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2_000)

    if page.locator("#username").count() > 0:
        page.fill("#username", username)
        page.fill("#password", password)
        page.click(".login-btn")
        page.wait_for_timeout(5_000)

    page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=45_000)
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        page.wait_for_timeout(3_000)

    if page.locator("#username").count() > 0:
        raise SzuAutomationError("login did not complete; still on login form")


def resolve_cluster(client: BrowserApiClient, target: TargetConfig) -> JsonDict:
    """Pick the target cluster by id, name, or previously-selected default."""
    selected = client.selected_cluster()
    clusters_payload = client.post("/gateway/foundation/api/v1/cluster/action/list", {})
    clusters = _ensure_list(clusters_payload, "clusters")

    if target.cluster_id:
        for cluster in clusters:
            if _norm(cluster.get("id")) == target.cluster_id:
                return cluster
        raise SzuAutomationError(f"cluster id not found: {target.cluster_id}")

    if target.cluster_name:
        wanted = _norm_lower(target.cluster_name)
        for cluster in clusters:
            if wanted in _norm_lower(cluster.get("name")):
                return cluster
        raise SzuAutomationError(f"cluster name not found: {target.cluster_name}")

    if selected and selected.get("id"):
        for cluster in clusters:
            if cluster.get("id") == selected.get("id"):
                return cluster
        return selected

    if not clusters:
        raise SzuAutomationError("cluster list is empty")
    return clusters[0]


def resolve_ssh_proxy(
    client: BrowserApiClient, cluster_id: Optional[str] = None
) -> str:
    """Find the SOCKS5 proxy for a cluster."""
    selected = client.selected_cluster()
    clusters_payload = client.post("/gateway/foundation/api/v1/cluster/action/list", {})
    clusters = _ensure_list(clusters_payload, "clusters")

    if cluster_id:
        for cluster in clusters:
            if _norm(cluster.get("id")) == _norm(cluster_id):
                proxy = _cluster_proxy(cluster)
                if proxy:
                    return proxy
                raise SzuAutomationError(
                    f"cluster has no ssh proxy path: {cluster_id}"
                )
        raise SzuAutomationError(
            f"cluster id not found while resolving ssh proxy: {cluster_id}"
        )

    if selected and selected.get("id"):
        for cluster in clusters:
            if _norm(cluster.get("id")) == _norm(selected.get("id")):
                proxy = _cluster_proxy(cluster)
                if proxy:
                    return proxy
        proxy = _cluster_proxy(selected)
        if proxy:
            return proxy

    for cluster in clusters:
        proxy = _cluster_proxy(cluster)
        if proxy:
            return proxy
    raise SzuAutomationError("no ssh proxy path found in cluster list")


def list_resource_options(client: BrowserApiClient, target: TargetConfig) -> List[JsonDict]:
    """List available GPU resource options across clusters."""
    clusters_payload = client.post("/gateway/foundation/api/v1/cluster/action/list", {})
    clusters = _ensure_list(clusters_payload, "clusters")
    options: List[JsonDict] = []

    for cluster in clusters:
        cluster_id = _norm(cluster.get("id"))
        cluster_name = _norm(cluster.get("name") or cluster_id)
        if target.cluster_id and cluster_id != _norm(target.cluster_id):
            continue
        if target.cluster_name and _norm_lower(target.cluster_name) not in _norm_lower(
            cluster_name
        ):
            continue

        pools_payload = client.post(
            "/gateway/foundation/api/v1/resource-pool/action/list",
            {"sceneList": ["training"], "clusterId": cluster_id},
        )
        for pool in _ensure_list(pools_payload, "resource pools"):
            pool_name = _norm(
                pool.get("name") or pool.get("resourcePoolName") or pool.get("id")
            )
            from remote_ssh_cli.selectors import _card_num, _power_confs

            for power in _power_confs(pool):
                options.append(
                    {
                        "cluster": cluster_name,
                        "clusterId": cluster_id,
                        "resource_pool": pool_name,
                        "resourcePoolId": pool.get("id"),
                        "publicFlag": pool.get("publicFlag"),
                        "power": power.get("name") or power.get("title") or power.get("id"),
                        "powerConfId": power.get("id"),
                        "cardNum": _card_num(power),
                    }
                )
    return options


def resolve_config(client: BrowserApiClient, target: TargetConfig) -> ResolvedConfig:
    """Resolve all backend IDs needed to submit a debug job."""
    cluster = resolve_cluster(client, target)
    cluster_id = _id(cluster, "cluster")

    teams_payload = client.post(
        "/gateway/order/api/v1/team-user/team-powers/action/page",
        {"pageNum": 1, "pageSize": 9999, "clusterId": cluster_id},
    )
    team = select_team(teams_payload, target.team_name)
    team_id = team.get("teamId") or team.get("id")
    if not team_id:
        raise SzuAutomationError(f"team has no teamId/id: {team}")

    pools_payload = client.post(
        "/gateway/foundation/api/v1/resource-pool/action/list",
        {"sceneList": ["training"], "clusterId": cluster_id},
    )
    pool, power_conf = select_public_pool_and_power(
        pools_payload, gpu_keyword=target.gpu_keyword, card_num=target.card_num
    )

    image_payload = {
        "source": 1,
        "name": "",
        "operateSystemList": [],
        "cpuArchitectureList": [],
        "cardTypeList": [],
        "frameList": [],
        "teamId": None,
    }
    images_payload = client.post(
        "/gateway/foundation/api/v1/image-job/action/official/list", image_payload
    )
    image = select_image(images_payload, target.image)

    ssh_keys_payload = client.get("/gateway/foundation/api/v1/ssh/action/list")
    ssh_key = select_ssh_key(ssh_keys_payload, target.ssh_key_keyword)

    storage_bucket = None
    if target.storage_from:
        try:
            buckets_payload = client.post(
                "/gateway/foundation/api/v1/buckets/action/list",
                {
                    "clusterId": cluster_id,
                    "status": 1,
                    "writeFlag": 0,
                    "onlySelfFlag": 0,
                },
            )
            storage_bucket = select_storage_bucket(buckets_payload, target.storage_from)
        except SzuAutomationError as exc:
            print(f"[warn] storage bucket resolution failed; fallback to team id: {exc}")

    return ResolvedConfig(
        cluster=cluster,
        team=team,
        resource_pool=pool,
        power_conf=power_conf,
        image=image,
        ssh_key=ssh_key,
        storage_bucket=storage_bucket,
    )


def build_payload(target: TargetConfig, resolved: ResolvedConfig) -> JsonDict:
    """Build the job creation payload from resolved config."""
    cluster_id = _id(resolved.cluster, "cluster")
    team_id = resolved.team.get("teamId") or resolved.team.get("id")
    if not team_id:
        raise SzuAutomationError(f"team has no teamId/id: {resolved.team}")

    storage_entry: Optional[JsonDict] = None
    if target.storage_from:
        bucket = resolved.storage_bucket or {}
        storage_entry = {
            "kind": "input",
            "businessType": 0,
            "businessName": bucket.get("name") or bucket.get("bucketName") or "File Storage",
            "businessId": "",
            "teamId": bucket.get("id") or team_id,
            "volumeFrom": target.storage_from,
            "volumeTo": target.mount_to,
        }

    payload: JsonDict = {
        "jobName": target.job_name,
        "teamId": team_id,
        "jobTaskList": [
            {
                "taskName": "task-1",
                "taskNum": 1,
                "resourcePoolId": _id(resolved.resource_pool, "resource pool"),
                "powerConfId": _id(resolved.power_conf, "power config"),
                "imageId": _id(resolved.image, "image"),
            }
        ],
        "tagList": [],
        "jobType": "training",
        "jobSubType": "training-debug",
        "sshId": _id(resolved.ssh_key, "ssh key"),
        "debugDuration": int(target.duration_hours) * 60,
        "jobCapabilityList": ["ssh"],
        "clusterId": cluster_id,
    }
    if storage_entry:
        payload["jobStorageList"] = [storage_entry]
    return payload


def summarize(
    target: TargetConfig, resolved: ResolvedConfig, payload: JsonDict, will_submit: bool = False
) -> JsonDict:
    """Human-readable summary of the resolved job configuration."""
    storage = (
        payload.get("jobStorageList", [{}])[0]
        if payload.get("jobStorageList")
        else {}
    )
    return {
        "mode": "submit" if will_submit else "dry-run",
        "cluster": resolved.cluster.get("name") or resolved.cluster.get("id"),
        "team": resolved.team.get("teamName") or resolved.team.get("name"),
        "job_name": target.job_name,
        "image": image_label(resolved.image),
        "imageId": payload["jobTaskList"][0]["imageId"],
        "resource_pool": resolved.resource_pool.get("name")
        or resolved.resource_pool.get("id"),
        "power": resolved.power_conf.get("name") or resolved.power_conf.get("id"),
        "powerConfId": payload["jobTaskList"][0]["powerConfId"],
        "ssh_key": resolved.ssh_key.get("name") or resolved.ssh_key.get("keyName"),
        "sshId": payload["sshId"],
        "duration_minutes": payload["debugDuration"],
        "storage": {
            "from": storage.get("volumeFrom"),
            "to": storage.get("volumeTo"),
            "bucket_id": storage.get("teamId"),
        },
    }


def submit_payload(client: BrowserApiClient, payload: JsonDict) -> Any:
    return client.post("/gateway/training/api/v1/job/debug", payload)


def get_debug_job_detail(client: BrowserApiClient, job_id: str) -> JsonDict:
    detail = client.get(f"/gateway/training/api/v1/job/debug/{job_id}")
    if not isinstance(detail, dict):
        raise SzuAutomationError(f"debug job detail is not a dict: {detail!r}")
    return detail


def get_debug_access_status(client: BrowserApiClient, job_id: str) -> JsonDict:
    status = client.get(f"/gateway/training/api/v1/job/{job_id}/debug-url")
    if not isinstance(status, dict):
        return {"raw": status}
    return status


def resolve_ssh_info(
    client: BrowserApiClient, job_id: str, key_path: str = "id_rsa"
) -> JsonDict:
    detail = get_debug_job_detail(client, job_id)
    cluster_id = _norm(detail.get("clusterId") or detail.get("clusterID"))
    proxy = resolve_ssh_proxy(client, cluster_id or None)
    access_status: JsonDict = {}
    try:
        access_status = get_debug_access_status(client, job_id)
    except SzuAutomationError as exc:
        access_status = {"error": str(exc)}

    ssh_state = access_status.get("sshState", detail.get("sshState"))
    proxy = prefer_debug_ssh_proxy(proxy, access_status)
    commands = build_ssh_commands(job_id, proxy, key_path=key_path)
    return {
        "job_id": job_id,
        "job_name": detail.get("jobName") or detail.get("name"),
        "clusterId": cluster_id or None,
        "state": detail.get("state") or detail.get("jobState") or detail.get("status"),
        "ssh_state": ssh_state,
        "ssh_state_label": ssh_state_label(ssh_state),
        "proxy": proxy,
        "linux": commands.linux,
        "macos": commands.macos,
        "windows": commands.windows,
        "ssh_config_linux": commands.ssh_config_linux,
        "debug_access": access_status,
    }


def wait_for_ssh_info(
    client: BrowserApiClient,
    job_id: str,
    key_path: str,
    timeout_seconds: int,
    poll_seconds: int,
) -> JsonDict:
    """Poll until SSH is ready or timeout."""
    deadline = time.monotonic() + max(timeout_seconds, 0)
    last_info: Optional[JsonDict] = None
    while True:
        last_info = resolve_ssh_info(client, job_id, key_path=key_path)
        if last_info.get("ssh_state_label") == "ready":
            return last_info
        if time.monotonic() >= deadline:
            return last_info
        print(
            f"[wait] ssh not ready yet: state={last_info.get('state')} "
            f"ssh_state={last_info.get('ssh_state')}; polling in {poll_seconds}s"
        )
        time.sleep(max(poll_seconds, 1))
