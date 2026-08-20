"""Runtime provenance shared by health checks and persisted suite artifacts."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, Field

from repro_runner import __version__


UNKNOWN = "unknown"
_SAFE_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")


class RuntimeProvenance(BaseModel):
    """The build identity needed to interpret a reproduction result."""

    model_config = ConfigDict(extra="forbid")

    service_version: str
    source_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    git_commit: str
    workflow_version: str


def runtime_provenance(workflow_version: str | None = None) -> RuntimeProvenance:
    """Return deterministic source identity plus explicitly supplied build metadata."""
    configured_workflow = workflow_version
    if configured_workflow in (None, "", UNKNOWN):
        configured_workflow = os.getenv("REPRO_RUNNER_WORKFLOW_VERSION")

    return RuntimeProvenance(
        service_version=__version__,
        source_digest=source_tree_digest(),
        git_commit=_metadata_value(os.getenv("REPRO_RUNNER_GIT_COMMIT")),
        workflow_version=_metadata_value(configured_workflow),
    )


def source_tree_digest(source_root: Path | None = None) -> str:
    """Hash the Python source tree without depending on filesystem ordering."""
    root = (source_root or Path(__file__).resolve().parent).resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _metadata_value(value: str | None) -> str:
    if value is None:
        return UNKNOWN
    candidate = value.strip()
    return candidate if is_safe_metadata_value(candidate) else UNKNOWN


def is_safe_metadata_value(value: str) -> bool:
    """Return whether a public metadata value cannot expose path-like secrets."""
    return _SAFE_METADATA.fullmatch(value) is not None
