from __future__ import annotations
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from superagent.runner import TaskRecord

STATUS_COLORS = {
    "QUEUED": "dim white",
    "RUNNING": "bold cyan",
    "TESTING": "bold yellow",
    "DONE": "bold green",
    "FAILED": "bold red",
    "CANCELLED": "bold magenta",
}

STATUS_ICONS = {
    "QUEUED": "⏳",
    "RUNNING": "⚙",
    "TESTING": "🧪",
    "DONE": "✔",
    "FAILED": "✖",
    "CANCELLED": "🚫",
}

THROBBER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

class SubagentCard(Static):
    """Interactive card representing a single subagent task."""
    
    can_focus = True

    class Selected(Message):
        """Emitted when card is clicked or focused."""
        def __init__(self, card: SubagentCard):
            super().__init__()
            self.card = card

    is_active_selection = reactive(False)

    def __init__(self, task_record: TaskRecord):
        super().__init__()
        self.task_record = task_record
        self._frame_idx = 0

    def on_mount(self) -> None:
        self.set_interval(0.25, self.refresh_timer)
        self.refresh_display()

    def refresh_timer(self) -> None:
        if self.task_record.status in ("RUNNING", "TESTING", "QUEUED"):
            self._frame_idx = (self._frame_idx + 1) % len(THROBBER_FRAMES)
            self.refresh_display()

    def refresh_display(self) -> None:
        if self.task_record.status == "RUNNING":
            icon = THROBBER_FRAMES[self._frame_idx]
        else:
            icon = STATUS_ICONS.get(self.task_record.status, "•")
        color = STATUS_COLORS.get(self.task_record.status, "white")
        agent_name = self.task_record.agent.upper()
        timer_str = f"⏱ {self.task_record.elapsed_formatted}"
        
        # Border style based on selection
        select_indicator = "▶ " if self.is_active_selection else "  "

        effort_str = getattr(self.task_record, "effort", "medium")
        effort_badge = f"[dim]({effort_str})[/dim]"

        text = (
            f"{select_indicator}[bold]{agent_name}[/bold] {effort_badge} "
            f"[{color}]{icon} {self.task_record.status}[/{color}]  "
            f"[yellow]{timer_str}[/yellow]\n"
            f"[dim]Task #{self.task_record.task_id}:[/dim] {self.task_record.task_desc}"
        )
        self.update(text)

    def on_click(self) -> None:
        self.post_message(self.Selected(self))

    def on_focus(self) -> None:
        self.post_message(self.Selected(self))
