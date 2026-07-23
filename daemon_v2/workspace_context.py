"""Reliable workspace resolution for producer-side event enrichment."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .git_context import GitContext, read_git_context


PROJECT_MARKERS = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "requirements.txt",
    "setup.py",
    ".venv",
)
_GIT_NOT_CHECKED = object()


@dataclass(frozen=True)
class WorkspaceContext:
    project_name: str
    workspace_root: str
    git_root: str | None
    resolution_method: str
    resolution_confidence: str

    def as_details(self) -> dict[str, str | None]:
        return asdict(self)


def read_workspace_context(
    path: Path,
    *,
    git_context: GitContext | None | object = _GIT_NOT_CHECKED,
) -> WorkspaceContext | None:
    """Resolve Git, nearest project marker, then cwd without raising.

    Callers that already checked Git must pass their result explicitly,
    including ``None``. This prevents a duplicate Git lookup.
    """
    try:
        candidate = Path(path).expanduser().absolute()
        start = candidate.parent if candidate.is_file() else candidate

        resolved_git = (
            read_git_context(start)
            if git_context is _GIT_NOT_CHECKED
            else git_context
        )
        if isinstance(resolved_git, GitContext):
            return WorkspaceContext(
                project_name=resolved_git.repository,
                workspace_root=resolved_git.git_root,
                git_root=resolved_git.git_root,
                resolution_method="git",
                resolution_confidence="high",
            )

        for directory in (start, *start.parents):
            if any((directory / marker).exists() for marker in PROJECT_MARKERS):
                if not directory.name:
                    return None
                return WorkspaceContext(
                    project_name=directory.name,
                    workspace_root=str(directory),
                    git_root=None,
                    resolution_method="marker",
                    resolution_confidence="medium",
                )

        if not start.name:
            return None
        return WorkspaceContext(
            project_name=start.name,
            workspace_root=str(start),
            git_root=None,
            resolution_method="cwd",
            resolution_confidence="low",
        )
    except Exception:
        return None
