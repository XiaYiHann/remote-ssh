"""Configuration dataclasses, constants, and exceptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

JsonDict = Dict[str, Any]

BASE_URL = "https://console.aicloud.szu.edu.cn"
CREATE_URL = f"{BASE_URL}/console/task-debug/create"
TASK_DEBUG_URL = f"{BASE_URL}/console/task-debug"

DEFAULT_IMAGE = "pytorch:2.2.2-cuda12.1-cudnn8-py310-ubuntu22.04"
DEFAULT_IMAGE_SOURCE = "auto"
IMAGE_SOURCE_CHOICES = ("auto", "custom", "official", "shared")
DEFAULT_TEAM = None
DEFAULT_STORAGE = None
DEFAULT_MOUNT = None
DEFAULT_GPU = "4090"
DEFAULT_KEY = "server"


class SzuAutomationError(RuntimeError):
    """Raised when the site state cannot be resolved safely."""


@dataclass(frozen=True)
class TargetConfig:
    """User-requested target configuration."""

    team_name: Optional[str] = DEFAULT_TEAM
    job_name: str = "remote-ssh-debug-4090"
    image: str = DEFAULT_IMAGE
    image_source: str = DEFAULT_IMAGE_SOURCE
    storage_from: Optional[str] = DEFAULT_STORAGE
    mount_to: Optional[str] = DEFAULT_MOUNT
    gpu_keyword: str = DEFAULT_GPU
    ssh_key_keyword: str = DEFAULT_KEY
    duration_hours: int = 1
    card_num: int = 1
    cluster_id: Optional[str] = None
    cluster_name: Optional[str] = None


@dataclass(frozen=True)
class ResolvedConfig:
    """Backend-resolved configuration with IDs."""

    cluster: JsonDict
    team: JsonDict
    resource_pool: JsonDict
    power_conf: JsonDict
    image: JsonDict
    ssh_key: JsonDict
    storage_bucket: Optional[JsonDict]
    storage_path: Optional[str]


@dataclass(frozen=True)
class SshCommands:
    """Generated SSH commands for a debug job."""

    job_id: str
    proxy: str
    key_path: str
    windows: str
    linux: str
    macos: str
    ssh_config_linux: str
