from __future__ import annotations

from typing import Any, Optional

from remote_ssh_cli.client import build_payload, resolve_config
from remote_ssh_cli.config import DEFAULT_IMAGE, ResolvedConfig, TargetConfig
from remote_ssh_cli.selectors import select_storage_bucket, select_team

TEAM_ID = "tm1060387743010000"
CLUSTER_ID = "1964608207239503873"
BUCKET_PATH = f"/home/{TEAM_ID}/a959271000"
STORAGE_PATH = f"/share{BUCKET_PATH}/xyh"


def _resolved_config(storage_bucket: Optional[dict[str, Any]] = None) -> ResolvedConfig:
    """Build a minimal resolved config for payload tests."""
    return ResolvedConfig(
        cluster={"id": CLUSTER_ID, "name": "default"},
        team={"teamId": TEAM_ID, "teamName": "eth.ai"},
        resource_pool={"id": "pool-1", "name": "public"},
        power_conf={"id": "power-1", "name": "GPU-单卡-nvidia-rtx-4090"},
        image={"id": "image-1", "name": DEFAULT_IMAGE},
        ssh_key={"id": "ssh-1", "name": "server"},
        storage_bucket=storage_bucket,
        storage_path=STORAGE_PATH if storage_bucket else None,
    )


def test_select_storage_bucket_matches_share_prefixed_filesystem_path() -> None:
    """Filesystem storage paths should match bucketPath after the /share prefix."""
    buckets = [
        {"id": "tm-other", "name": "other", "bucketPath": "/home/tm-other/a959271000"},
        {"id": TEAM_ID, "name": "eth.ai", "bucketPath": BUCKET_PATH},
    ]

    selected = select_storage_bucket(buckets, STORAGE_PATH)

    assert selected is not None
    assert selected["id"] == TEAM_ID


def test_select_team_defaults_to_first_available_team() -> None:
    """A shared CLI should not require the author's team name."""
    selected = select_team(
        [
            {"teamId": "tm-first", "teamName": "first-team"},
            {"teamId": TEAM_ID, "teamName": "eth.ai"},
        ],
        None,
    )

    assert selected["teamId"] == "tm-first"


def test_build_payload_uses_web_ui_filesystem_storage_contract() -> None:
    """File-storage payload should mirror the web UI after choosing a path."""
    target = TargetConfig(storage_from=STORAGE_PATH, mount_to=None)
    resolved = _resolved_config(
        {"id": TEAM_ID, "name": "eth.ai", "bucketPath": BUCKET_PATH}
    )

    payload = build_payload(target, resolved)

    assert payload["jobStorageList"] == [
        {
            "kind": "input",
            "businessType": 0,
            "businessName": "home",
            "businessId": "",
            "teamId": TEAM_ID,
            "volumeFrom": STORAGE_PATH,
            "volumeTo": STORAGE_PATH,
        }
    ]


def test_build_payload_respects_explicit_mount_to() -> None:
    """An explicit mount path remains available for users who need it."""
    target = TargetConfig(storage_from=STORAGE_PATH, mount_to="/workspace")
    resolved = _resolved_config(
        {"id": TEAM_ID, "name": "eth.ai", "bucketPath": BUCKET_PATH}
    )

    payload = build_payload(target, resolved)

    assert payload["jobStorageList"][0]["volumeTo"] == "/workspace"


def test_build_payload_uses_auto_resolved_storage_path() -> None:
    """Auto-detected file storage should be enough to build the mount payload."""
    target = TargetConfig(storage_from=None, mount_to=None)
    resolved = _resolved_config(
        {"id": TEAM_ID, "name": "eth.ai", "bucketPath": BUCKET_PATH}
    )

    payload = build_payload(target, resolved)

    assert payload["jobStorageList"][0]["volumeFrom"] == STORAGE_PATH
    assert payload["jobStorageList"][0]["volumeTo"] == STORAGE_PATH


class FakeClient:
    """Minimal API client stub for storage-resolution contract tests."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []

    def selected_cluster(self) -> None:
        """No preselected cluster is needed for this test."""
        return None

    def post(self, path: str, data: Optional[dict[str, Any]] = None) -> Any:
        """Record API calls and return minimal backend-like responses."""
        payload = data or {}
        self.posts.append((path, payload))
        if path == "/gateway/foundation/api/v1/cluster/action/list":
            return [{"id": CLUSTER_ID, "name": "default"}]
        if path == "/gateway/order/api/v1/team-user/team-powers/action/page":
            return [{"teamId": TEAM_ID, "teamName": "eth.ai"}]
        if path == "/gateway/foundation/api/v1/resource-pool/action/list":
            return [
                {
                    "id": "pool-1",
                    "publicFlag": 1,
                    "powerConfList": [
                        {
                            "id": "power-1",
                            "name": "GPU-单卡-nvidia-rtx-4090",
                            "cardNum": 1,
                        }
                    ],
                }
            ]
        if path == "/gateway/foundation/api/v1/image-job/action/official/list":
            return [{"id": "image-1", "name": DEFAULT_IMAGE}]
        if path == "/gateway/foundation/api/v1/buckets/team-user-storage/action/list":
            return [{"id": TEAM_ID, "name": "eth.ai", "bucketPath": BUCKET_PATH}]
        if path == "/gateway/foundation/api/v1/buckets/file-proxy/action/token":
            return "token-1"
        raise AssertionError(f"unexpected POST path: {path}")

    def get(self, path: str) -> Any:
        """Return a matching SSH key list."""
        if path == "/gateway/foundation/api/v1/ssh/action/list":
            return [{"id": "ssh-1", "name": "server"}]
        raise AssertionError(f"unexpected GET path: {path}")

    def file_proxy_get(
        self, path: str, token: str, params: Optional[dict[str, Any]] = None
    ) -> Any:
        """Return the first directory shown by the file-storage picker."""
        assert path == "/gateway/file-proxy/api/v1/list"
        assert token == "token-1"
        assert params == {
            "bucketName": "home",
            "storageType": "filesystem",
            "dir": f"home/{TEAM_ID}/a959271000/",
            "pageNumber": 1,
            "pageSize": 20,
            "region": CLUSTER_ID,
        }
        return {"fileList": [{"fileName": "xyh", "directory": True}]}


def test_resolve_config_uses_team_user_storage_endpoint() -> None:
    """Storage resolution should call the same endpoint as the file picker."""
    client = FakeClient()

    resolved = resolve_config(client, TargetConfig(storage_from=None))

    assert resolved.storage_bucket is not None
    assert resolved.storage_bucket["id"] == TEAM_ID
    assert resolved.storage_path == STORAGE_PATH
    assert (
        "/gateway/foundation/api/v1/buckets/team-user-storage/action/list",
        {"teamId": TEAM_ID, "clusterId": CLUSTER_ID},
    ) in client.posts
