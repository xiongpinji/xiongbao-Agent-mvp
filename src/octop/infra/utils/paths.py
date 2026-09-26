"""Filesystem layout for ``~/.octop/``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathLayout:
    root: Path

    @classmethod
    def from_env(cls) -> PathLayout:
        """Resolve install root from ``OCTOP_HOME`` or ``~/.octop``."""
        raw = os.environ.get("OCTOP_HOME", "").strip()
        if raw:
            return cls(Path(raw).expanduser())
        return cls(Path.home() / ".octop")

    @property
    def db(self) -> Path:
        return self.root / "octop.db"

    @property
    def logs_dir(self) -> Path:
        """Structured runtime logs: ``~/.octop/logs/``."""
        return self.root / "logs"

    @property
    def log(self) -> Path:
        return self.logs_dir / "octop.log"

    def ensure_logs_dir(self) -> Path:
        """Create the logs directory and return it."""
        out = self.logs_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_log(self) -> Path:
        """Create the logs directory and return ``octop.log``."""
        self.ensure_logs_dir()
        return self.log

    @property
    def config(self) -> Path:
        return self.root / "config.json"

    @property
    def users_dir(self) -> Path:
        return self.root / "users"

    def user_dir(self, username: str) -> Path:
        return self.users_dir / username

    @property
    def agents_dir(self) -> Path:
        """Global agents directory: ~/.octop/agents/"""
        return self.root / "agents"

    @property
    def expert_market_dir(self) -> Path:
        """Cached SkillHub expert templates: ``~/.octop/expert_market/``."""
        return self.root / "expert_market"

    @property
    def published_experts_dir(self) -> Path:
        """User-published expert snapshots: ``~/.octop/published_experts/``."""
        return self.root / "published_experts"

    @property
    def skill_packages_dir(self) -> Path:
        """Global skill package content: ``~/.octop/skill-packages/``."""
        return self.root / "skill-packages"

    @property
    def knowledge_dir(self) -> Path:
        """Global knowledge base files: ``~/.octop/knowledge/``."""
        return self.root / "knowledge"

    @property
    def project_assets_dir(self) -> Path:
        """Private project asset objects: ``~/.octop/project-assets/``.

        Never served statically and never inside the workspace or knowledge
        dirs; reachable only through the membership-gated download route.
        """
        return self.root / "project-assets"

    def ensure_project_assets_dir(self) -> Path:
        """Create the project asset root and return it."""
        out = self.project_assets_dir
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(out, 0o700)
        return out

    @property
    def project_task_files_dir(self) -> Path:
        """Managed roots for internal project-task file runtimes (030A).

        ``~/.octop/project-task-files/`` — deliberately outside ``agents_dir``:
        these directories are task-private filesystems for ``runtime_kind =
        'project_task_files'`` agents, reachable only through the six-tool
        boundary, never served statically and never a user-managed folder.
        """
        return self.root / "project-task-files"

    def project_task_file_runtime_dir(self, agent_id: str) -> Path:
        """Deterministic private root for one internal runtime agent.

        Derived from the trusted layout plus the internal (random, unguessable)
        agent id only — never from persisted config. Unsafe ids (empty, ``.``,
        ``..`` or anything containing ``/`` or ``\\``) are rejected with
        :class:`ValueError` so a crafted runtime id can never escape the
        managed parent directory.
        """
        if not agent_id or agent_id in {".", ".."} or "/" in agent_id or "\\" in agent_id:
            raise ValueError(f"unsafe project-task runtime id: {agent_id!r}")
        return self.project_task_files_dir / agent_id

    def ensure_project_task_file_runtime_dir(self, agent_id: str) -> Path:
        """Create the private runtime root with 0700 on POSIX and return it."""
        out = self.project_task_file_runtime_dir(agent_id)
        out.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            os.chmod(out, 0o700)
        return out

    def agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace: ~/.octop/agents/<agent_id>/"""
        return self.agents_dir / agent_id

    def ensure_agent_workspace(self, agent_id: str) -> Path:
        """Global agent workspace, mkdir -p."""
        out = self.agent_workspace(agent_id)
        out.mkdir(parents=True, exist_ok=True)
        return out

    def ensure_root(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root

    @property
    def plugins_dir(self) -> Path:
        return self.root / "plugins"

    @property
    def tool_guard_rules_dir(self) -> Path:
        """User-editable command guard rules: ``~/.octop/security/tool_guard/``."""
        return self.root / "security" / "tool_guard"

    @property
    def tool_guard_rules_file(self) -> Path:
        return self.tool_guard_rules_dir / "dangerous_shell_commands.yaml"

    @property
    def backups_dir(self) -> Path:
        """Stored system backup archives: ``~/.octop/backups/``."""
        return self.root / "backups"

    def ensure_backups_dir(self) -> Path:
        out = self.backups_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    def backup_file(self, filename: str) -> Path:
        """Resolve a backup archive path under :attr:`backups_dir` (basename only)."""
        return self.backups_dir / Path(filename).name

    @property
    def ssl_dir(self) -> Path:
        """TLS certificates and ACME account keys: ``~/.octop/ssl/``."""
        return self.root / "ssl"

    def ensure_ssl_dir(self) -> Path:
        out = self.ssl_dir
        out.mkdir(parents=True, exist_ok=True)
        return out

    @property
    def connector_cli_dir(self) -> Path:
        """Per-instance CLI config roots: ``~/.octop/connector-cli/``."""
        return self.root / "connector-cli"

    def connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        """Isolated config dir for one connector CLI instance."""
        safe_kind = Path(kind).name
        safe_key = (
            "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in instance_key)[:80]
            or "default"
        )
        return self.connector_cli_dir / safe_kind / safe_key

    def ensure_connector_cli_instance_dir(self, kind: str, instance_key: str) -> Path:
        out = self.connector_cli_instance_dir(kind, instance_key)
        out.mkdir(parents=True, exist_ok=True)
        return out
