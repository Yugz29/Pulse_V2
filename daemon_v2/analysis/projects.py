"""Resolve observed workspaces into logical projects and optional modules."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .terminal import useful_command_lines


GENERIC_MODULE_NAMES = {
    "api",
    "app",
    "backend",
    "client",
    "core",
    "daemon",
    "frontend",
    "lib",
    "server",
    "src",
    "tests",
    "web",
}
PROJECT_MARKERS = {
    ".git",
    "README.md",
    "manage.py",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
}


@dataclass(frozen=True)
class ProjectContext:
    project_root: str
    project_name: str
    cwd: str
    module: str | None = None


def activity_workspace(activity: dict[str, Any]) -> str | None:
    details = activity.get("details", {})
    if details.get("workspace"):
        return details["workspace"]
    if activity["type"] == "terminal_finished" and details.get("cwd"):
        return details["cwd"]
    return None


def _has_project_marker(path: Path) -> bool:
    if any((path / marker).exists() for marker in PROJECT_MARKERS):
        return True
    return any(path.glob("vite.config.*"))


def _projects_container_root(path: Path) -> Path | None:
    projects_root = Path.home() / "Projets"
    try:
        path.relative_to(projects_root)
    except ValueError:
        return None
    relative_parts = path.relative_to(projects_root).parts
    if not relative_parts:
        return None

    project_root = projects_root / relative_parts[0]
    nested_parts = list(relative_parts[1:])
    while (
        nested_parts
        and nested_parts[0].casefold() == project_root.name.casefold()
    ):
        nested_parts.pop(0)
    if (
        nested_parts
        and nested_parts[0].casefold() not in GENERIC_MODULE_NAMES
    ):
        project_root /= nested_parts[0]
    return project_root


def _marked_project_root(path: Path) -> Path | None:
    marked = []
    for candidate in (path, *path.parents):
        if candidate == Path.home() or candidate == candidate.parent:
            break
        if _has_project_marker(candidate):
            marked.append(candidate)
    return marked[-1] if marked else None


def resolve_project_context(cwd: str) -> ProjectContext:
    """Resolve an exact working directory without changing the stored event."""
    path = Path(cwd).expanduser()
    project_root = (
        _projects_container_root(path)
        or _marked_project_root(path)
        or path
    )
    project_name = project_root.name

    module = None
    try:
        relative_parts = list(path.relative_to(project_root).parts)
    except ValueError:
        relative_parts = []
    while relative_parts and relative_parts[0].casefold() == project_name.casefold():
        relative_parts.pop(0)
    for part in relative_parts:
        if part.casefold() in GENERIC_MODULE_NAMES:
            module = part
            break

    return ProjectContext(
        project_root=str(project_root),
        project_name=project_name,
        cwd=str(path),
        module=module,
    )


def activity_project_context(
    activity: dict[str, Any],
) -> ProjectContext | None:
    workspace = activity_workspace(activity)
    return resolve_project_context(workspace) if workspace else None


def activity_project_root(activity: dict[str, Any]) -> str | None:
    context = activity_project_context(activity)
    return context.project_root if context else None


def _generic_workspace_containers(home: Path) -> set[Path]:
    # Local heuristic only; this can become configurable if more layouts emerge.
    return {home / "Projets"}


def is_weak_workspace(workspace: str) -> bool:
    path = Path(workspace).expanduser()
    home = Path.home()
    return path == home or path in _generic_workspace_containers(home)


def last_observed_workspace(trace: dict[str, Any]) -> str | None:
    workspace = None
    for session in trace["sessions"]:
        for activity in session["activities"]:
            if activity["type"] == "app_activated":
                continue
            details = activity.get("details", {})
            useful_activity = (
                activity["type"] != "terminal_finished"
                or bool(useful_command_lines(details.get("command")))
            )
            if not useful_activity:
                continue
            candidate = activity_project_root(activity)
            if candidate and not is_weak_workspace(candidate):
                workspace = candidate
    return workspace


def most_frequent_explicit_workspace(
    trace: dict[str, Any],
) -> str | None:
    counts: dict[str, int] = {}
    for session in trace["sessions"]:
        for activity in session["activities"]:
            workspace = activity.get("details", {}).get("workspace")
            if workspace and not is_weak_workspace(workspace):
                project_root = resolve_project_context(workspace).project_root
                counts[project_root] = counts.get(project_root, 0) + 1
    return max(counts, key=counts.get) if counts else None
