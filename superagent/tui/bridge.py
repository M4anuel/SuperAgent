from __future__ import annotations
from dataclasses import dataclass, field
import time
from typing import Any, Callable, Dict, List, Optional


@dataclass
class PendingQuestion:
    """Represents a question posed to the user for a specific task."""
    task_id: str
    question: str
    options: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    answered: bool = False
    answer: Optional[str] = None
    contexts: List[str] = field(default_factory=list)


class InteractionBridge:
    """UI-local bridge decoupling the TUI from coordinator/backend mechanics.

    Consumes public coordinator methods and events for:
    - Pending questions querying and emission
    - Answering questions (choices or free text)
    - Sending targeted task context
    - Cancelling selected tasks
    """

    def __init__(self, coordinator: Optional[Any] = None) -> None:
        self.coordinator = coordinator
        self.pending_questions: Dict[str, PendingQuestion] = {}
        self.subscribers: List[Callable[[str, Any], None]] = []

    def subscribe(self, callback: Callable[[str, Any], None]) -> None:
        """Subscribe to bridge events ('question', 'answered', 'context', 'cancelled')."""
        self.subscribers.append(callback)

    def _notify(self, event: str, data: Any) -> None:
        for cb in list(self.subscribers):
            try:
                cb(event, data)
            except Exception:
                pass

    def get_pending_questions(self) -> Dict[str, PendingQuestion]:
        """Returns all unresolved pending questions, checking coordinator if present."""
        if self.coordinator and hasattr(self.coordinator, "get_pending_questions"):
            try:
                coord_questions = self.coordinator.get_pending_questions()
                if isinstance(coord_questions, dict):
                    for tid, q in coord_questions.items():
                        if tid not in self.pending_questions:
                            if isinstance(q, PendingQuestion):
                                self.pending_questions[tid] = q
                            elif isinstance(q, dict):
                                self.pending_questions[tid] = PendingQuestion(
                                    task_id=tid,
                                    question=q.get("question", ""),
                                    options=list(q.get("options") or [])
                                )
            except Exception:
                pass
        return {k: v for k, v in self.pending_questions.items() if not v.answered}

    def get_pending_question(self, task_id: str) -> Optional[PendingQuestion]:
        """Returns the pending question for the specified task, if unresolved."""
        q = self.pending_questions.get(task_id)
        if q and not q.answered:
            return q
        return None

    def has_pending_question(self, task_id: str) -> bool:
        """Checks if a task has an unresolved pending question."""
        return self.get_pending_question(task_id) is not None

    def ask_question(
        self,
        task_id: str,
        question: str,
        options: Optional[List[str]] = None
    ) -> PendingQuestion:
        """Registers a pending question for a specific task."""
        pq = PendingQuestion(
            task_id=task_id,
            question=question,
            options=list(options) if options else []
        )
        self.pending_questions[task_id] = pq

        if self.coordinator and hasattr(self.coordinator, "on_question_asked"):
            try:
                self.coordinator.on_question_asked(pq)
            except Exception:
                pass

        self._notify("question", pq)
        return pq

    def prompt_question(
        self,
        task_id: str,
        question: str,
        options: Optional[List[str]] = None
    ) -> PendingQuestion:
        """Alias for ask_question."""
        return self.ask_question(task_id, question, options)

    def answer_question(self, task_id: str, answer: str, *, from_coordinator: bool = False) -> bool:
        """Resolves a pending question targeting only the specified task."""
        pq = self.get_pending_question(task_id)
        if pq:
            pq.answered = True
            pq.answer = answer

        coord = self.coordinator
        if coord and not from_coordinator and not getattr(self, "_in_answer", False):
            self._in_answer = True
            try:
                if hasattr(coord, "answer_question") and coord is not self:
                    coord.answer_question(task_id, answer)
                elif hasattr(coord, "submit_answer"):
                    coord.submit_answer(task_id, answer)
                elif hasattr(coord, "resolve_question"):
                    coord.resolve_question(task_id, answer)
            except Exception:
                pass
            finally:
                self._in_answer = False

        self._notify("answered", {"task_id": task_id, "answer": answer, "question": pq})
        return True

    def submit_choice(self, task_id: str, choice: str) -> bool:
        """Alias for answer_question."""
        return self.answer_question(task_id, choice)

    def send_context(self, task_id: str, context: str, *, from_coordinator: bool = False) -> bool:
        """Sends targeted context to the specified task only."""
        pq = self.pending_questions.get(task_id)
        if pq:
            pq.contexts.append(context)

        coord = self.coordinator
        if coord and not from_coordinator and not getattr(self, "_in_context", False):
            self._in_context = True
            try:
                if hasattr(coord, "send_task_context") and coord is not self:
                    coord.send_task_context(task_id, context)
                elif hasattr(coord, "send_context") and coord is not self:
                    coord.send_context(task_id, context)
                elif hasattr(coord, "add_task_context"):
                    coord.add_task_context(task_id, context)
                elif hasattr(coord, "add_context"):
                    coord.add_context(task_id, context)
            except Exception:
                pass
            finally:
                self._in_context = False

        self._notify("context", {"task_id": task_id, "context": context})
        return True

    def send_task_context(self, task_id: str, context: str) -> bool:
        """Alias for send_context."""
        return self.send_context(task_id, context)

    def cancel_task(self, task_id: str, *, from_coordinator: bool = False) -> bool:
        """Cancels the specified task only, clearing any pending question."""
        if task_id in self.pending_questions:
            self.pending_questions[task_id].answered = True

        coord = self.coordinator
        cancelled = False
        if coord and not from_coordinator and not getattr(self, "_in_cancel", False):
            self._in_cancel = True
            try:
                if hasattr(coord, "cancel_task") and coord is not self:
                    cancelled = bool(coord.cancel_task(task_id))
                elif hasattr(coord, "runner") and hasattr(coord.runner, "cancel_task"):
                    cancelled = bool(coord.runner.cancel_task(task_id))
            except Exception:
                pass
            finally:
                self._in_cancel = False

        self._notify("cancelled", {"task_id": task_id})
        return cancelled
