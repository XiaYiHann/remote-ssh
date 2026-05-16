"""Backend record selection logic."""

from __future__ import annotations

import json
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from remote_ssh_cli.config import (
    DEFAULT_GPU,
    JsonDict,
    SzuAutomationError,
)
from remote_ssh_cli.utils import _ensure_list, _norm, _norm_lower, image_label


def select_team(teams_payload: Any, team_name: Optional[str]) -> JsonDict:
    """Pick a team by name from the backend list."""
    teams = _ensure_list(teams_payload, "teams")
    if not team_name:
        if not teams:
            raise SzuAutomationError("team list is empty")
        return teams[0]

    wanted = _norm_lower(team_name)
    for team in teams:
        names = [team.get("teamName"), team.get("name"), team.get("displayName")]
        if any(_norm_lower(name) == wanted for name in names):
            return team
    available = ", ".join(
        filter(None, (_norm(t.get("teamName") or t.get("name")) for t in teams))
    )
    raise SzuAutomationError(f"team not found: {team_name}; available={available}")


def select_image(images_payload: Any, target_image: str) -> JsonDict:
    """Pick an image by exact or partial match."""
    images = _ensure_list(images_payload, "images")
    wanted = _norm_lower(target_image)
    partial: List[JsonDict] = []
    for image in images:
        label = _norm_lower(image_label(image))
        if label == wanted:
            if not image.get("id"):
                raise SzuAutomationError(f"target image has no id: {image_label(image)}")
            return image
        if "pytorch" in label and "2.2.2" in label and "cuda12.1" in label:
            partial.append(image)
    if len(partial) == 1 and partial[0].get("id"):
        return partial[0]
    matches = ", ".join(image_label(item) for item in partial[:5])
    raise SzuAutomationError(f"image not found uniquely: {target_image}; partial={matches}")


def _power_confs(pool: Mapping[str, Any]) -> Sequence[JsonDict]:
    for key in ("powerConfList", "powerConfs", "jobTaskPowerConfList", "configList"):
        value = pool.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _card_num(power: Mapping[str, Any]) -> Optional[int]:
    for key in ("cardNum", "gpuNum", "gpuCardNum", "gpuCount"):
        value = power.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def select_public_pool_and_power(
    pools_payload: Any, gpu_keyword: str = DEFAULT_GPU, card_num: int = 1
) -> Tuple[JsonDict, JsonDict]:
    """Select a resource pool and power config matching GPU and card count."""
    pools = _ensure_list(pools_payload, "resource pools")
    public_pools = [pool for pool in pools if pool.get("publicFlag") == 1]
    candidates = public_pools or pools
    wanted_gpu = _norm_lower(gpu_keyword)
    fallback: List[Tuple[JsonDict, JsonDict]] = []

    for pool in candidates:
        for power in _power_confs(pool):
            haystack = json.dumps(power, ensure_ascii=False).lower()
            if wanted_gpu not in haystack:
                continue
            actual_cards = _card_num(power)
            if actual_cards == card_num:
                return pool, power
            if actual_cards is None and (
                "single" in haystack or "1*" in haystack or "1x" in haystack
            ):
                return pool, power
            fallback.append((pool, power))

    if len(fallback) == 1:
        return fallback[0]
    raise SzuAutomationError(f"single-card {gpu_keyword} resource config not found")


def select_ssh_key(keys_payload: Any, keyword: str) -> JsonDict:
    """Pick an SSH key by keyword match."""
    keys = _ensure_list(keys_payload, "ssh keys")
    wanted = _norm_lower(keyword)
    for key in keys:
        names = [key.get("name"), key.get("keyName"), key.get("sshName"), key.get("title")]
        if any(wanted in _norm_lower(name) for name in names):
            if not key.get("id"):
                raise SzuAutomationError(f"ssh key has no id: {names}")
            return key
    available = ", ".join(
        filter(None, (_norm(k.get("name") or k.get("keyName")) for k in keys))
    )
    raise SzuAutomationError(f"ssh key not found: {keyword}; available={available}")


def select_storage_bucket(buckets_payload: Any, storage_path: str) -> Optional[JsonDict]:
    """Pick the best-matching storage bucket for a path."""
    try:
        buckets = _ensure_list(buckets_payload, "storage buckets")
    except SzuAutomationError:
        return None

    storage_path = _norm(storage_path)
    if not storage_path:
        return buckets[0] if buckets else None

    ranked: List[Tuple[int, JsonDict]] = []
    for bucket in buckets:
        bucket_path = _norm(bucket.get("bucketPath"))
        fields = [
            bucket_path,
            f"/share{bucket_path}" if bucket_path else "",
            bucket.get("path"),
            bucket.get("rootPath"),
            bucket.get("mountPath"),
            bucket.get("name"),
            bucket.get("bucketName"),
        ]
        score = 0
        for field in fields:
            value = _norm(field)
            if value and storage_path.startswith(value):
                score = max(score, len(value))
            elif value and value in storage_path:
                score = max(score, len(value) // 2)
        if score:
            ranked.append((score, bucket))
    if not ranked:
        return buckets[0] if buckets else None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]
