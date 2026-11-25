from __future__ import annotations
import textwrap
from typing import Any, Optional
from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
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
    "AWAITING_INPUT": "bold bright_yellow",
}

STATUS_ICONS = {
    "QUEUED": "⏳",
    "RUNNING": "⚙",
    "TESTING": "🧪",
    "DONE": "✔",
    "FAILED": "✖",
    "CANCELLED": "🚫",
    "AWAITING_INPUT": "❓",
}

THROBBER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

class SubagentCard(Static):
    """Interactive card representing a single subagent task.

    Cards start collapsed, occupying at most 6 terminal rows (1 metadata row,
    up to 3 text rows, and up to 2 border rows). Overflow in collapsed view is
    truncated. Users can expand to read full content, and collapse again.
    """

    DEFAULT_CSS = """
    SubagentCard {
        min-height: 3;
        max-height: 6;
        height: auto;
    }
    SubagentCard.expanded {
        max-height: 100%;
    }
    SubagentCard.selected {
        background: $surface-lighten-1;
    }
    SubagentCard.awaiting-input {
        border: heavy $accent;
    }
    """

    can_focus = True

    BINDINGS = [
        Binding("up,k", "app.select_prev_card", "Prev Card", show=False),
        Binding("down,j", "app.select_next_card", "Next Card", show=False),
        Binding("space,e,x", "toggle_expand", "Expand/Collapse", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    class Selected(Message):
        """Emitted when card is clicked or focused."""
        def __init__(self, card: SubagentCard):
            super().__init__()
            self.card = card

    is_active_selection = reactive(False)
    is_awaiting_input = reactive(False)
    is_expanded = reactive(False)
    pending_question: reactive[Any] = reactive(None)

    def __init__(self, task_record: TaskRecord):
        super().__init__()
        self.task_record = task_record
        self._frame_idx = 0

    @property
    def renderable(self) -> Any:
        return getattr(self, "_renderable", None) or self.render()

    def on_mount(self) -> None:
        self.set_interval(0.25, self.refresh_timer)
        self.refresh_display()

    def on_resize(self, event: Any = None) -> None:
        self.refresh_display()

    def refresh_timer(self) -> None:
        if self.task_record.status in ("RUNNING", "TESTING", "QUEUED"):
            self._frame_idx = (self._frame_idx + 1) % len(THROBBER_FRAMES)
            self.refresh_display()

    def toggle_expand(self) -> None:
        self.is_expanded = not self.is_expanded
        self.refresh_display()

    def expand(self) -> None:
        self.is_expanded = True
        self.refresh_display()

    def collapse(self) -> None:
        self.is_expanded = False
        self.refresh_display()

    def action_toggle_expand(self) -> None:
        self.toggle_expand()

    def refresh_display(self) -> None:
        if self.is_awaiting_input or self.task_record.status == "AWAITING_INPUT":
            icon = "❓"
            color = STATUS_COLORS["AWAITING_INPUT"]
            status_text = "AWAITING INPUT"
        elif self.task_record.status == "RUNNING":
            icon = THROBBER_FRAMES[self._frame_idx]
            color = STATUS_COLORS.get(self.task_record.status, "white")
            status_text = self.task_record.status
        else:
            icon = STATUS_ICONS.get(self.task_record.status, "•")
            color = STATUS_COLORS.get(self.task_record.status, "white")
            status_text = self.task_record.status
        agent_name = self.task_record.agent.upper()
        timer_str = f"⏱ {self.task_record.elapsed_formatted}"

        # CSS classes
        self.set_class(self.is_active_selection, "selected")
        self.set_class(self.is_awaiting_input, "awaiting-input")
        self.set_class(self.is_expanded, "expanded")
        self.set_class(not self.is_expanded, "collapsed")

        select_indicator = "▶ " if self.is_active_selection else "  "
        effort_str = getattr(self.task_record, "effort", "medium")
        effort_badge = f"[dim]({effort_str})[/dim]"
        expand_badge = "[dim][-][/dim]" if self.is_expanded else "[dim][+][/dim]"

        # Row 1: Metadata row
        metadata_row = (
            f"{select_indicator}[bold]{agent_name}[/bold] {effort_badge} "
            f"[{color}]{icon} {status_text}[/{color}]  "
            f"[yellow]{timer_str}[/yellow] {expand_badge}"
        )

        # Content rows calculation
        card_w = self.size.width if self.size.width > 4 else 38
        wrap_w = max(16, card_w - 4)

        raw_desc = f"Task #{self.task_record.task_id}: {self.task_record.task_desc}"
        raw_items: list[tuple[str, str]] = []  # (kind, text)

        for line in raw_desc.splitlines():
            if line:
                for sub in (textwrap.wrap(line, width=wrap_w) or [line]):
                    raw_items.append(("desc", sub))
            else:
                raw_items.append(("desc", ""))

        if self.is_awaiting_input and self.pending_question:
            q_text = getattr(self.pending_question, "question", str(self.pending_question))
            for line in f"❓ {q_text}".splitlines():
                if line:
                    for sub in (textwrap.wrap(line, width=wrap_w) or [line]):
                        raw_items.append(("question", sub))
        elif getattr(self.task_record, "diff_summary", None):
            for line in f"📊 {self.task_record.diff_summary}".splitlines():
                if line:
                    for sub in (textwrap.wrap(line, width=wrap_w) or [line]):
                        raw_items.append(("diff", sub))
        elif getattr(self.task_record, "error_summary", None):
            for line in f"✖ {self.task_record.error_summary}".splitlines():
                if line:
                    for sub in (textwrap.wrap(line, width=wrap_w) or [line]):
                        raw_items.append(("error", sub))

        # Truncate text rows to at most 3 when collapsed
        if not self.is_expanded:
            has_overflow = len(raw_items) > 3
            visible_items = raw_items[:3]
            if has_overflow and visible_items:
                kind, last_text = visible_items[-1]
                if len(last_text) > wrap_w - 3:
                    visible_items[-1] = (kind, last_text[:max(0, wrap_w - 3)] + "...")
                else:
                    visible_items[-1] = (kind, last_text + "...")
        else:
            visible_items = raw_items

        formatted_lines: list[str] = []
        task_prefix = f"Task #{self.task_record.task_id}:"
        for kind, text_line in visible_items:
            if kind == "desc":
                if text_line.startswith(task_prefix):
                    rest = text_line[len(task_prefix):]
                    formatted_lines.append(f"[dim]{task_prefix}[/dim]{escape(rest)}")
                else:
                    formatted_lines.append(escape(text_line))
            elif kind == "question":
                formatted_lines.append(f"[bold yellow]{escape(text_line)}[/bold yellow]")
            elif kind == "diff":
                formatted_lines.append(f"[yellow]{escape(text_line)}[/yellow]")
            elif kind == "error":
                formatted_lines.append(f"[red]{escape(text_line)}[/red]")
            else:
                formatted_lines.append(escape(text_line))

        body_text = "\n".join(formatted_lines)
        full_text = f"{metadata_row}\n{body_text}" if body_text else metadata_row
        self.update(full_text)

    def on_click(self) -> None:
        self.toggle_expand()
        self.post_message(self.Selected(self))

    def on_focus(self) -> None:
        self.post_message(self.Selected(self))

    def action_select(self) -> None:
        self.post_message(self.Selected(self))
