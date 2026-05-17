"""Playwright browser client and SZU AI Cloud API orchestration."""

from __future__ import annotations

import time
from typing import Any, List, Optional
from urllib.parse import urlencode

from remote_ssh_cli.config import (
    CREATE_URL,
    DEFAULT_IMAGE,
    DEFAULT_IMAGE_SOURCE,
    TASK_DEBUG_URL,
    JsonDict,
    ResolvedConfig,
    SzuAutomationError,
    TargetConfig,
)
from remote_ssh_cli.selectors import (
    select_image,
    select_public_pool_and_power,
    select_ssh_key,
    select_storage_bucket,
    select_team,
)
from remote_ssh_cli.ssh import (
    _cluster_proxy,
    build_ssh_commands,
    prefer_debug_ssh_proxy,
    ssh_state_label,
)
from remote_ssh_cli.utils import (
    _ensure_list,
    _id,
    _norm,
    _norm_lower,
    image_label,
    unwrap_response,
)

IMAGE_SOURCE_CODES = {"official": 1, "custom": 2, "shared": 3}


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

    def file_proxy_get(
        self, path: str, token: str, params: Optional[JsonDict] = None
    ) -> Any:
        """Call the file-proxy API with its bearer token."""
        query = urlencode(params or {})
        request_path = f"{path}?{query}" if query else path
        result = self.page.evaluate(
            """
            async ({path, token}) => {
              const response = await fetch(path, {
                method: "GET",
                headers: {
                  "Accept": "application/json, text/plain, */*",
                  "Authorization": `Bearer ${token}`
                },
                credentials: "include"
              });
              const text = await response.text();
              let payload = text;
              try { payload = JSON.parse(text); } catch (_) {}
              return {ok: response.ok, status: response.status, payload};
            }
            """,
            {"path": request_path, "token": token},
        )
        if not result.get("ok"):
            raise SzuAutomationError(
                f"GET {request_path} failed: status={result.get('status')} "
                f"body={result.get('payload')}"
            )
        payload = result.get("payload")
        if isinstance(payload, dict):
            message = payload.get("message")
            if isinstance(message, dict):
                code = message.get("code")
                if code not in (None, 0, "0", 200, "200"):
                    raise SzuAutomationError(
                        f"file proxy returned code={code}: {message.get('message')}"
                    )
            return payload.get("data", payload)
        return payload

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


def _filesystem_storage_root(bucket: JsonDict) -> Optional[str]:
    bucket_path = _norm(bucket.get("bucketPath"))
    if not bucket_path:
        return None
    if not bucket_path.startswith("/"):
        bucket_path = f"/{bucket_path}"
    return f"/share{bucket_path}"


def _filesystem_proxy_dir(bucket: JsonDict) -> Optional[str]:
    bucket_path = _norm(bucket.get("bucketPath")).strip("/")
    if not bucket_path:
        return None
    return f"{bucket_path}/"


def resolve_filesystem_storage_path(
    client: BrowserApiClient, bucket: JsonDict, cluster_id: str
) -> Optional[str]:
    """Resolve the path selected by the web UI's file-storage picker."""
    root = _filesystem_storage_root(bucket)
    proxy_dir = _filesystem_proxy_dir(bucket)
    if not root or not proxy_dir:
        return root

    try:
        token = client.post(
            "/gateway/foundation/api/v1/buckets/file-proxy/action/token",
            {"crName": "home", "expires": 3600, "pathList": ["*"]},
        )
        files_payload = client.file_proxy_get(
            "/gateway/file-proxy/api/v1/list",
            _norm(token),
            {
                "bucketName": "home",
                "storageType": "filesystem",
                "dir": proxy_dir,
                "pageNumber": 1,
                "pageSize": 20,
                "region": cluster_id,
            },
        )
    except SzuAutomationError as exc:
        print(f"[warn] storage directory auto-detection failed; fallback to root: {exc}")
        return root

    file_list = _ensure_list(files_payload.get("fileList", []), "storage files")
    for item in file_list:
        if item.get("directory") is True and _norm(item.get("fileName")):
            return f"{root}/{_norm(item.get('fileName')).strip('/')}"
    return root


def _image_source_order(image_source: str) -> List[str]:
    source = _norm_lower(image_source or DEFAULT_IMAGE_SOURCE)
    if source == "auto":
        return ["custom", "official"]
    if source in IMAGE_SOURCE_CODES:
        return [source]
    choices = ", ".join(["auto", *IMAGE_SOURCE_CODES.keys()])
    raise SzuAutomationError(f"invalid image source: {image_source}; choices={choices}")


def _image_list_payload(source: str, team_id: str, cluster_id: str) -> JsonDict:
    payload: JsonDict = {
        "source": IMAGE_SOURCE_CODES[source],
        "name": "",
        "operateSystemList": [],
        "cpuArchitectureList": [],
        "cardTypeList": [],
        "frameList": [],
    }
    if source in ("custom", "shared"):
        payload["teamId"] = team_id
        payload["clusterId"] = cluster_id
    else:
        payload["teamId"] = None
    return payload


def _image_list_endpoint(source: str) -> str:
    if source == "official":
        return "/gateway/foundation/api/v1/image-job/action/official/list"
    return "/gateway/foundation/api/v1/image-job/action/list"


def _tag_image_source(image: JsonDict, source: str) -> JsonDict:
    tagged = dict(image)
    tagged["_source"] = source
    tagged["_sourceCode"] = IMAGE_SOURCE_CODES[source]
    return tagged


def resolve_image(
    client: BrowserApiClient, target: TargetConfig, team_id: str, cluster_id: str
) -> JsonDict:
    """Resolve the image ID, preferring team custom images in auto mode."""
    errors: List[str] = []
    requested_sources = _image_source_order(target.image_source)
    for source in requested_sources:
        images_payload = client.post(
            _image_list_endpoint(source),
            _image_list_payload(source, team_id=team_id, cluster_id=cluster_id),
        )
        try:
            image = select_image(
                images_payload,
                target.image,
                allow_first=source == "custom" and _norm(target.image) == DEFAULT_IMAGE,
            )
            return _tag_image_source(image, source)
        except SzuAutomationError as exc:
            errors.append(f"{source}: {exc}")
            if len(requested_sources) == 1:
                raise
    raise SzuAutomationError("image not found; " + "; ".join(errors))


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

    image = resolve_image(client, target, team_id=team_id, cluster_id=cluster_id)

    ssh_keys_payload = client.get("/gateway/foundation/api/v1/ssh/action/list")
    ssh_key = select_ssh_key(ssh_keys_payload, target.ssh_key_keyword)

    storage_bucket = None
    storage_path = None
    if target.storage_from != "":
        try:
            buckets_payload = client.post(
                "/gateway/foundation/api/v1/buckets/team-user-storage/action/list",
                {"teamId": team_id, "clusterId": cluster_id},
            )
            storage_bucket = select_storage_bucket(
                buckets_payload, target.storage_from or ""
            )
            if target.storage_from:
                storage_path = target.storage_from
            elif storage_bucket:
                storage_path = resolve_filesystem_storage_path(
                    client, storage_bucket, cluster_id
                )
        except SzuAutomationError as exc:
            print(f"[warn] storage bucket resolution failed; fallback to team id: {exc}")
            storage_path = target.storage_from or None

    return ResolvedConfig(
        cluster=cluster,
        team=team,
        resource_pool=pool,
        power_conf=power_conf,
        image=image,
        ssh_key=ssh_key,
        storage_bucket=storage_bucket,
        storage_path=storage_path,
    )


def build_payload(target: TargetConfig, resolved: ResolvedConfig) -> JsonDict:
    """Build the job creation payload from resolved config."""
    cluster_id = _id(resolved.cluster, "cluster")
    team_id = resolved.team.get("teamId") or resolved.team.get("id")
    if not team_id:
        raise SzuAutomationError(f"team has no teamId/id: {resolved.team}")

    storage_entry: Optional[JsonDict] = None
    storage_from = target.storage_from or resolved.storage_path
    if storage_from:
        bucket = resolved.storage_bucket or {}
        volume_to = target.mount_to or storage_from
        storage_entry = {
            "kind": "input",
            "businessType": 0,
            "businessName": "home",
            "businessId": "",
            "teamId": bucket.get("id") or team_id,
            "volumeFrom": storage_from,
            "volumeTo": volume_to,
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
        "image_source": resolved.image.get("_source"),
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
