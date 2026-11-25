from __future__ import annotations
from typing import Any, Dict, List, Optional
from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from superagent.tui.bridge import InteractionBridge, PendingQuestion


class TaskSelector(OptionList):
    """Task selector that displays each task's pending question or running status."""

    class TaskSelected(Message):
        """Emitted when a task is selected or highlighted in the selector."""
        def __init__(self, task_id: str) -> None:
            super().__init__()
            self.task_id = task_id

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._task_ids: List[str] = []

    def update_tasks(
        self,
        tasks: Dict[str, Any],
        pending_questions: Dict[str, PendingQuestion],
        active_task_id: Optional[str] = None
    ) -> None:
        self.clear_options()
        self._task_ids = []

        # Prioritize tasks that have pending questions awaiting user input
        ordered_ids: List[str] = []
        for tid in pending_questions:
            if tid in tasks and tid not in ordered_ids:
                ordered_ids.append(tid)
        # Then tasks in pending_questions that might not yet be in runner.tasks
        for tid in pending_questions:
            if tid not in ordered_ids:
                ordered_ids.append(tid)
        # Then other tasks
        for tid in tasks:
            if tid not in ordered_ids:
                ordered_ids.append(tid)

        if not ordered_ids:
            self.add_option(Option("[dim]No tasks awaiting input[/dim]", disabled=True))
            return

        selected_index = 0
        for idx, tid in enumerate(ordered_ids):
            self._task_ids.append(tid)
            task = tasks.get(tid)
            agent = getattr(task, "agent", "TASK").upper() if task else "TASK"
            has_question = tid in pending_questions

            if has_question:
                pq = pending_questions[tid]
                q_snippet = escape(pq.question[:32] + ("..." if len(pq.question) > 32 else ""))
                label = f"❓ [bold yellow]{agent} #{tid}[/bold yellow] [bright_yellow](AWAITING INPUT)[/bright_yellow]: {q_snippet}"
            else:
                status = getattr(task, "status", "QUEUED")
                desc = escape(getattr(task, "task_desc", "")[:32])
                label = f"⚙ [cyan]{agent} #{tid}[/cyan] [dim]({status})[/dim]: {desc}"

            self.add_option(Option(label, id=tid))
            if tid == active_task_id:
                selected_index = idx

        if self._task_ids and 0 <= selected_index < len(self._task_ids):
            self.highlighted = selected_index

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id:
            self.post_message(self.TaskSelected(str(event.option_id)))

    def on_option_list_option_highlighted(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id:
            self.post_message(self.TaskSelected(str(event.option_id)))


class TaskInteractionView(Container):
    """Interactive workspace panel for user choices and task micromanagement.

    Displays:
    - Visible awaiting-input status
    - Task selector with each task's pending question or status
    - Selected task's question and listed options/choices
    - Free text input for answers or targeted context
    - Action buttons to answer choices, send context, or cancel the selected task
    """

    selected_task_id = reactive("")

    def __init__(self, bridge: InteractionBridge, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bridge = bridge
        self.bridge.subscribe(self._on_bridge_event)

    def _on_bridge_event(self, event: str, data: Any) -> None:
        self.call_after_refresh(self.refresh_view)

    def compose(self) -> ComposeResult:
        yield Static("[dim]⚡ No tasks awaiting input[/dim]", id="awaiting-input-status")
        with Horizontal(id="interaction-main-box"):
            with Vertical(id="task-selector-box"):
                yield Label("🎯 SELECT TASK", id="task-selector-label")
                yield TaskSelector(id="task-selector")
            with Vertical(id="task-details-box"):
                yield Label("❓ QUESTION & CHOICES", id="task-question-label")
                yield Static("[dim]No task selected or no pending question.[/dim]", id="task-question")
                yield OptionList(id="task-options")
                with Horizontal(id="interaction-actions"):
                    yield Input(placeholder="Type answer, choice, or context...", id="interaction-input")
                    yield Button("Answer", id="btn-submit-answer", variant="primary")
                    yield Button("Send Context", id="btn-send-context", variant="default")
                    yield Button("Cancel Task", id="btn-cancel-task", variant="error")

    def on_mount(self) -> None:
        self.refresh_view()

    def set_selected_task(self, task_id: str) -> None:
        """Sets the selected task in the interaction view, updating details."""
        if self.selected_task_id != task_id:
            self.selected_task_id = task_id
            self.update_task_details(task_id)
            try:
                selector = self.query_one("#task-selector", TaskSelector)
                if task_id in selector._task_ids:
                    selector.highlighted = selector._task_ids.index(task_id)
            except Exception:
                pass

    def refresh_view(self) -> None:
        """Synchronizes selector options, status label, and details."""
        pending_questions = self.bridge.get_pending_questions()
        runner_tasks = getattr(getattr(self.app, "runner", None), "tasks", {})

        # Status text
        awaiting_count = len(pending_questions)
        try:
            status_widget = self.query_one("#awaiting-input-status", Static)
            if awaiting_count > 0:
                status_widget.update(
                    f"❓ [bold yellow]AWAITING INPUT • {awaiting_count} task(s) require your choice[/bold yellow]"
                )
                self.add_class("has-pending")
            else:
                status_widget.update(
                    "[dim]⚡ Fleet standing by • All tasks running smoothly with no pending inputs[/dim]"
                )
                self.remove_class("has-pending")
        except Exception:
            pass

        # Update selector options
        try:
            selector = self.query_one("#task-selector", TaskSelector)
            # Pick active task id from app if available
            app_active_id = getattr(self.app, "active_task_id", None)
            target_id = self.selected_task_id or app_active_id
            if not target_id:
                if pending_questions:
                    target_id = next(iter(pending_questions))
                elif runner_tasks:
                    target_id = next(iter(runner_tasks))

            selector.update_tasks(runner_tasks, pending_questions, target_id)
            if target_id:
                self.selected_task_id = target_id
                self.update_task_details(target_id)
        except Exception:
            pass

    def update_task_details(self, task_id: str) -> None:
        """Displays the specified task's question and options, or running info."""
        try:
            question_widget = self.query_one("#task-question", Static)
            options_list = self.query_one("#task-options", OptionList)
        except Exception:
            return

        pq = self.bridge.get_pending_question(task_id)
        if pq:
            question_widget.update(
                f"[bold yellow]Question:[/bold yellow] [bold white]{escape(pq.question)}[/bold white]"
            )
            options_list.clear_options()
            if pq.options:
                options_list.display = True
                for idx, opt in enumerate(pq.options, 1):
                    options_list.add_option(
                        Option(f"[bold cyan]{idx}.[/bold cyan] {escape(opt)}", id=opt)
                    )
            else:
                options_list.clear_options()
                options_list.display = False
        else:
            runner_tasks = getattr(getattr(self.app, "runner", None), "tasks", {})
            task = runner_tasks.get(task_id)
            if task:
                agent = getattr(task, "agent", "task").upper()
                status = getattr(task, "status", "UNKNOWN")
                question_widget.update(
                    f"[dim]Task #{task_id} ({agent}) is {status}. No pending question. You may send context or cancel.[/dim]"
                )
            else:
                question_widget.update("[dim]No task selected.[/dim]")
            options_list.clear_options()
            options_list.display = False

    def on_task_selector_task_selected(self, message: TaskSelector.TaskSelected) -> None:
        """User switched task in the selector."""
        self.selected_task_id = message.task_id
        self.update_task_details(message.task_id)
        if hasattr(self.app, "select_task") and getattr(self.app, "active_task_id", None) != message.task_id:
            self.app.select_task(message.task_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """User clicked or pressed Enter on a listed choice."""
        if event.option_list.id == "task-options" and event.option_id and self.selected_task_id:
            choice = str(event.option_id)
            if hasattr(self.app, "answer_question"):
                self.app.answer_question(self.selected_task_id, choice)
            else:
                self.bridge.answer_question(self.selected_task_id, choice)
            self.refresh_view()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if not self.selected_task_id:
            return

        input_widget = self.query_one("#interaction-input", Input)
        val = input_widget.value.strip()

        if btn_id == "btn-submit-answer":
            if val:
                # Check for numeric option choice
                pq = self.bridge.get_pending_question(self.selected_task_id)
                if pq and pq.options and val.isdigit():
                    idx = int(val) - 1
                    if 0 <= idx < len(pq.options):
                        val = pq.options[idx]

                if hasattr(self.app, "answer_question"):
                    self.app.answer_question(self.selected_task_id, val)
                else:
                    self.bridge.answer_question(self.selected_task_id, val)
                input_widget.value = ""
                self.refresh_view()

        elif btn_id == "btn-send-context":
            if val:
                if hasattr(self.app, "send_task_context"):
                    self.app.send_task_context(self.selected_task_id, val)
                else:
                    self.bridge.send_context(self.selected_task_id, val)
                input_widget.value = ""
                self.refresh_view()

        elif btn_id == "btn-cancel-task":
            if hasattr(self.app, "cancel_task"):
                self.app.cancel_task(self.selected_task_id)
            else:
                self.bridge.cancel_task(self.selected_task_id)
            self.refresh_view()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "interaction-input" and self.selected_task_id:
            val = event.value.strip()
            if not val:
                return

            if self.bridge.has_pending_question(self.selected_task_id):
                pq = self.bridge.get_pending_question(self.selected_task_id)
                if pq and pq.options and val.isdigit():
                    idx = int(val) - 1
                    if 0 <= idx < len(pq.options):
                        val = pq.options[idx]

                if hasattr(self.app, "answer_question"):
                    self.app.answer_question(self.selected_task_id, val)
                else:
                    self.bridge.answer_question(self.selected_task_id, val)
            else:
                if hasattr(self.app, "send_task_context"):
                    self.app.send_task_context(self.selected_task_id, val)
                else:
                    self.bridge.send_context(self.selected_task_id, val)

            event.input.value = ""
            self.refresh_view()
