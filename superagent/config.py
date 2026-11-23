from __future__ import annotations
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

CONFIG_DIR = Path.home() / ".superagent"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0",
    "active_project": "betterilias",
    "projects": {
        "betterilias": {
            "name": "betterilias",
            "path": str(Path(__file__).resolve().parents[3]),  # root of current repo
            "default_branch": "main",
            "test_cmd": "python -m pytest tests/",
            "default_agent": "codex",
        }
    },
    "agents": {
        "codex": {
            "cmd": "codex",
            "args_template": ["exec", "--skip-git-repo-check", "-C", "{cwd}", "{task}"],
            "description": "OpenAI Codex / ChatGPT CLI"
        },
        "claude": {
            "cmd": "claude",
            "args_template": ["-p", "{task}"],
            "description": "Anthropic Claude Code CLI"
        },
        "agy": {
            "cmd": "agy",
            "args_template": ["run", "{task}"],
            "description": "Google Antigravity CLI"
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
    def __init__(self):
        self.config_dir = CONFIG_DIR
        self.config_file = CONFIG_FILE
        self._data: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = DEFAULT_CONFIG.copy()
                self.save()
        else:
            self._data = DEFAULT_CONFIG.copy()
            self.save()

    def save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config_file, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

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
        for p in self.list_projects():
            try:
                proj_path = Path(p.path).resolve()
                if current == proj_path or proj_path in current.parents:
                    return p
            except Exception:
                continue
        return None
