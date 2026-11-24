from __future__ import annotations
import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_DIR = Path.home() / ".superagent"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0",
    "active_project": None,
    "projects": {},
    "execution_profile": "workspace-write",
    "task_timeout_seconds": 1800,
    "agents": {
        "codex": {
            "cmd": "codex",
            "args_template": ["exec", "--skip-git-repo-check", "-C", "{cwd}", "{task}"],
            "description": "OpenAI Codex / ChatGPT CLI",
            "enabled": True
        },
        "claude": {
            "cmd": "claude",
            "args_template": ["-p", "{task}"],
            "description": "Anthropic Claude Code CLI",
            "enabled": False
        },
        "agy": {
            "cmd": "agy",
            "args_template": ["run", "{task}"],
            "description": "Google Antigravity CLI",
            "enabled": True
        }
    }
}

class ProjectConfig:
    def __init__(self, data: Dict[str, Any]):
        self.name: str = data.get("name", "")
        self.path: str = data.get("path", "")
        self.default_branch: str = data.get("default_branch", "main")
        self.test_cmd: str = data.get("test_cmd", "")
        self.default_agent: str = data.get("default_agent", "codex")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "default_branch": self.default_branch,
            "test_cmd": self.test_cmd,
            "default_agent": self.default_agent,
        }

class SuperagentConfig:
    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = Path(config_dir) if config_dir is not None else CONFIG_DIR
        self.config_file = self.config_dir / "config.json"
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = deepcopy(DEFAULT_CONFIG)
                self.save()
        else:
            self._data = deepcopy(DEFAULT_CONFIG)
            self.save()

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        temp_file = self.config_file.with_suffix(f".tmp.{os.getpid()}")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_file, self.config_file)
        except Exception:
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            raise

    @property
    def execution_profile(self) -> str:
        from superagent.execution import PROFILES
        profile = self._data.get('execution_profile', 'workspace-write')
        if profile not in PROFILES:
            raise ValueError(f'Unknown execution profile: {profile}')
        return profile

    @property
    def task_timeout_seconds(self) -> float:
        value = float(self._data.get('task_timeout_seconds', 1800))
        if not 0 < value < float('inf'):
            raise ValueError('Task timeout must be positive and finite.')
        return value

    @property
    def first_responder(self) -> str:
        return self._data.get("first_responder", "codex")

    def set_first_responder(self, agent: str) -> None:
        self._data["first_responder"] = agent.lower().strip()
        self.save()

    @property
    def gemini_api_key(self) -> Optional[str]:
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or self._data.get("gemini_api_key")

    def set_gemini_api_key(self, key: str) -> None:
        self._data["gemini_api_key"] = key.strip()
        self.save()

    def get_enabled_agents(self) -> List[str]:
        agents_dict = self._data.get("agents", {})
        enabled = [name.lower() for name, spec in agents_dict.items() if spec.get("enabled", True)]
        return enabled if enabled else ["codex", "agy"]

    def set_agent_enabled(self, agent_name: str, enabled: bool) -> bool:
        agent_key = agent_name.lower().strip()
        if agent_key in self._data.get("agents", {}):
            self._data["agents"][agent_key]["enabled"] = enabled
            self.save()
            return True
        return False

    def get_last_conversation_id(self, project_name: str) -> Optional[str]:
        return self._data.get("last_conversations", {}).get(project_name.lower())

    def set_last_conversation_id(self, project_name: str, conv_id: str) -> None:
        self._data.setdefault("last_conversations", {})[project_name.lower()] = conv_id
        self.save()

    @property
    def active_project(self) -> Optional[ProjectConfig]:
        pname = self._data.get("active_project")
        if pname and pname in self._data.get("projects", {}):
            return ProjectConfig(self._data["projects"][pname])
        # fallback to first project
        projects = self._data.get("projects", {})
        if projects:
            first_key = next(iter(projects))
            return ProjectConfig(projects[first_key])
        return None

    def set_active_project(self, name: str) -> bool:
        if name in self._data.get("projects", {}):
            self._data["active_project"] = name
            self.save()
            return True
        return False

    def get_project(self, name: str) -> Optional[ProjectConfig]:
        pdata = self._data.get("projects", {}).get(name)
        if pdata:
            return ProjectConfig(pdata)
        return None

    def list_projects(self) -> List[ProjectConfig]:
        return [ProjectConfig(v) for v in self._data.get("projects", {}).values()]

    def add_project(self, name: str, path: str, test_cmd: str = "", default_agent: str = "codex") -> ProjectConfig:
        from superagent.execution import validate_project_path
        path = validate_project_path(path)
        p_dict = {
            "name": name,
            "path": str(Path(path).resolve()),
            "default_branch": "main",
            "test_cmd": test_cmd,
            "default_agent": default_agent,
        }
        self._data.setdefault("projects", {})[name] = p_dict
        self._data["active_project"] = name
        self.save()
        return ProjectConfig(p_dict)

    def remove_project(self, name: str) -> bool:
        if name in self._data.get("projects", {}):
            del self._data["projects"][name]
            if self._data.get("active_project") == name:
                self._data["active_project"] = next(iter(self._data.get("projects", {})), None)
            self.save()
            return True
        return False

    def detect_project(self, cwd: Optional[str] = None) -> Optional[ProjectConfig]:
        current = Path(cwd or os.getcwd()).resolve()
        matches = []
        for p in self.list_projects():
            try:
                proj_path = Path(p.path).resolve()
                if proj_path.is_dir() and (current == proj_path or proj_path in current.parents):
                    matches.append((len(proj_path.parts), p))
            except Exception:
                continue
        return max(matches, key=lambda match: match[0])[1] if matches else None
