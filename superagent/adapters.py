"""Worker signals and explicit continuation capabilities.

Workers can emit the awaiting_input envelope directly, or as their final message.
No adapter writes to stdin: the print/exec commands close input at launch.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re

from superagent.execution import agent_command


class UnsupportedContinuationError(RuntimeError):
    """The worker cannot safely continue its own identified session."""


@dataclass(frozen=True)
class PendingChoice:
    task_id: str
    question: str
    choices: tuple[str, ...] = ()
    allow_free_text: bool = True


@dataclass(frozen=True)
class TaskInput:
    kind: str  # context or answer
    text: str
    question: str | None = None


INPUT_PROTOCOL = (
    '\nIf user input is required, emit a JSON object as your final response: '
    '{"event":"awaiting_input","question":"Your question",'
    '"choices":["optional choice"],"allow_free_text":true}. '
    'Do not claim the task is complete while waiting for an answer.'
)


def pending_choice(task_id: str, data: dict) -> PendingChoice | None:
    if (data.get('event') or data.get('type')) not in ('awaiting_input', 'request_user_input'):
        return None
    question = data.get('question')
    choices = data.get('choices', [])
    free_text = data.get('allow_free_text', True)
    if (not isinstance(question, str) or not question.strip()
            or not isinstance(choices, list)
            or any(not isinstance(c, str) or not c.strip() for c in choices)
            or not isinstance(free_text, bool) or (not choices and not free_text)):
        return None
    return PendingChoice(task_id, question.strip(), tuple(choices), free_text)


def completion_choice(task_id: str, output: str) -> PendingChoice | None:
    """Prefer the protocol; only inspect a final question, never live tool logs."""
    text = output.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1]).strip()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        return pending_choice(task_id, data)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    choices = []
    while lines and re.match(r'^(?:\d+[.)]|[-*])\s+', lines[-1]):
        choices.insert(0, re.sub(r'^(?:\d+[.)]|[-*])\s+', '', lines.pop()))
    if not lines:
        return None
    question = lines[-1]
    if (question.endswith('?') and re.match(
            r'^(?:which|what|how|where|when|who|should|shall|would|could|can|do|does|are|is)\b',
            question, re.I)):
        return PendingChoice(task_id, question, tuple(choices))
    return None


class ProcessAdapter:
    def __init__(self, agent: str, custom: bool = False):
        self.agent = 'agy' if agent.lower() == 'antigravity' else agent.lower()
        self.custom = custom

    def start_command(self, prompt: str, cwd: str, effort: str, profile: str) -> list[str]:
        command = agent_command(self.agent, prompt, cwd, effort, profile)
        if self.agent == 'codex':
            # JSON provides the thread identifier and final assistant messages.
            command.insert(command.index('exec') + 1, '--json')
        return command

    def continuation_command(self, session_id: str | None, prompt: str,
                             cwd: str, effort: str, profile: str) -> list[str]:
        if self.custom or self.agent != 'codex':
            raise UnsupportedContinuationError(
                f'{self.agent}: this process adapter does not support session continuation.')
        if not session_id:
            raise UnsupportedContinuationError(
                f'{self.agent}: no session ID was reported; targeted continuation is unavailable.')
        # exec resume does not accept exec's -C/--sandbox flags. The subprocess
        # cwd and global configuration preserve the original execution policy.
        command = ['codex', '-a', 'never', '-c', f'model_reasoning_effort="{effort}"']
        if profile != 'unrestricted':
            command += ['-c', f'sandbox_mode="{profile}"']
        command += ['exec', 'resume', '--skip-git-repo-check', '--json']
        if profile == 'unrestricted':
            command += ['--dangerously-bypass-approvals-and-sandbox']
        return command + [session_id, prompt]

    def read_signal(self, task_id: str, data: dict):
        """Return (session ID, final assistant text, pending choice)."""
        kind = data.get('event') or data.get('type')
        session = None
        text = None
        choice = pending_choice(task_id, data)
        if kind == 'thread.started':
            session = data.get('thread_id')
        elif kind == 'init':
            session = data.get('conversation_id') or data.get('session_id')
        elif kind == 'item.completed':
            item = data.get('item')
            if isinstance(item, dict) and item.get('type') == 'agent_message':
                text = item.get('text')
        elif kind == 'result':
            result = data.get('result')
            if isinstance(result, dict):
                text = result.get('response')
                choice = choice or pending_choice(task_id, result)
        if not isinstance(session, str) or not session.strip():
            session = None
        if not isinstance(text, str):
            text = None
        if text:
            # Structured input requests are actionable before process exit.
            try:
                envelope = json.loads(text)
            except ValueError:
                envelope = None
            if isinstance(envelope, dict):
                choice = choice or pending_choice(task_id, envelope)
        return session, text, choice
