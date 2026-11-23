from __future__ import annotations
import json
import os
import time
import uuid
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

class Conversation(BaseModel):
    id: str
    title: str = "New Conversation"
    project_name: str
    project_path: str
    created_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    updated_at: str = Field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    turns: List[HistoryItem] = Field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def last_snippet(self) -> str:
        if not self.turns:
            return "Empty conversation"
        last = self.turns[-1]
        line = last.user_prompt.splitlines()[0] if last.user_prompt else ""
        return line[:55] + "..." if len(line) > 55 else line

class HistoryManager:
    """Manages persistent conversation history across SuperAgent sessions."""

    def __init__(self, base_dir: Optional[Path] = None):
        self.base_dir = base_dir or (Path.home() / ".superagent" / "history")
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.conv_dir = self.base_dir / "conversations"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_turns()

    def _migrate_legacy_turns(self) -> None:
        """Migrates unassociated flat turn files into a legacy conversation if none exist."""
        try:
            existing_convs = list(self.conv_dir.glob("*.json"))
            if existing_convs:
                return

            flat_turns = sorted(self.base_dir.glob("turn_*.json"), key=os.path.getmtime)
            if not flat_turns:
                return

            by_project: Dict[str, List[HistoryItem]] = {}
            for f in flat_turns:
                try:
                    with open(f, "r", encoding="utf-8") as fp:
                        item = HistoryItem(**json.load(fp))
                        by_project.setdefault(item.project_name.lower(), []).append(item)
                except Exception:
                    continue

            for proj_name, turns in by_project.items():
                if not turns:
                    continue
                conv_id = f"conv_legacy_{proj_name}"
                first_turn = turns[0]
                first_prompt = first_turn.user_prompt.splitlines()[0][:45] if first_turn.user_prompt else "Previous Session"
                conv = Conversation(
                    id=conv_id,
                    title=f"{first_prompt}",
                    project_name=first_turn.project_name,
                    project_path=first_turn.project_path,
                    created_at=turns[0].timestamp,
                    updated_at=turns[-1].timestamp,
                    turns=turns
                )
                self.save_conversation(conv)
        except Exception:
            pass

    def _file_path(self, item_id: str) -> Path:
        return self.base_dir / f"{item_id}.json"

    def _conv_path(self, conv_id: str) -> Path:
        return self.conv_dir / f"{conv_id}.json"

    # --- Conversation CRUD ---

    def save_conversation(self, conv: Conversation) -> None:
        try:
            path = self._conv_path(conv.id)
            with open(path, "w", encoding="utf-8") as f:
                f.write(conv.model_dump_json(indent=2))
        except Exception:
            pass

    def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        path = self._conv_path(conv_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return Conversation(**data)
        except Exception:
            return None

    def create_conversation(
        self,
        project_name: str,
        project_path: str,
        title: Optional[str] = None
    ) -> Conversation:
        conv_id = f"conv_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        conv = Conversation(
            id=conv_id,
            title=title or "New Conversation",
            project_name=project_name,
            project_path=project_path
        )
        self.save_conversation(conv)
        return conv

    def add_turn_to_conversation(self, conv_id: str, turn: HistoryItem) -> Optional[Conversation]:
        conv = self.get_conversation(conv_id)
        if not conv:
            conv = self.create_conversation(turn.project_name, turn.project_path)
        
        conv.turns.append(turn)
        if conv.title == "New Conversation" or not conv.title.strip():
            first_line = turn.user_prompt.splitlines()[0].strip()
            conv.title = first_line[:45] + ("..." if len(first_line) > 45 else "")
        conv.updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_conversation(conv)
        self.save_turn(turn)
        return conv

    def list_conversations(self, project_name: Optional[str] = None, limit: int = 50) -> List[Conversation]:
        items: List[Conversation] = []
        if not self.conv_dir.exists():
            return items

        files = sorted(self.conv_dir.glob("*.json"), key=os.path.getmtime, reverse=True)
        for f in files:
            try:
                with open(f, "r", encoding="utf-8") as fp:
                    data = json.load(fp)
                    conv = Conversation(**data)
                    if project_name and conv.project_name.lower() != project_name.lower():
                        continue
                    items.append(conv)
                    if len(items) >= limit:
                        break
            except Exception:
                continue
        return items

    def delete_conversation(self, conv_id: str) -> bool:
        path = self._conv_path(conv_id)
        if path.exists():
            try:
                path.unlink()
                return True
            except Exception:
                pass
        return False

    # --- Turn CRUD (legacy and individual turn lookup) ---

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
