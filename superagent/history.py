from __future__ import annotations
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class SubtaskRecordData(BaseModel):
    task_id: str
    agent: str
    effort: str = "medium"
    title: str = ""
    task_brief: str = ""
    status: str = "DONE"
    elapsed_formatted: str = "00m:00s"
    output_clean: str = ""
    diff_summary: Optional[str] = None
    error_summary: Optional[str] = None

class HistoryItem(BaseModel):
    id: str
    timestamp: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    project_name: str
    project_path: str
    user_prompt: str
    plan_action: str = "delegate"  # "reply" or "delegate"
    plan_reasoning: str = ""
    direct_reply: Optional[str] = None
    subtasks: List[SubtaskRecordData] = Field(default_factory=list)
    final_synthesis: Optional[str] = None

class HistoryManager:
    """Manages persistent conversation history across SuperAgent sessions."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (Path.home() / ".superagent" / "history")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, item_id: str) -> Path:
        return self.base_dir / f"{item_id}.json"

    def save_turn(self, item: HistoryItem) -> None:
        try:
            path = self._file_path(item.id)
            with open(path, "w", encoding="utf-8") as f:
                f.write(item.model_dump_json(indent=2))
        except Exception:
            pass

    def get_turn(self, item_id: str) -> Optional[HistoryItem]:
        path = self._file_path(item_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return HistoryItem(**data)
        except Exception:
            return None

    def list_turns(self, project_name: Optional[str] = None, limit: int = 50) -> List[HistoryItem]:
        items: List[HistoryItem] = []
        if not self.base_dir.exists():
            return items

        files = sorted(self.base_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    item = HistoryItem(**data)
                    if project_name and item.project_name.lower() != project_name.lower():
                        continue
                    items.append(item)
                    if len(items) >= limit:
                        break
            except Exception:
                continue
        return items

    def delete_turn(self, item_id: str) -> bool:
        path = self._file_path(item_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception:
                pass
        return False
