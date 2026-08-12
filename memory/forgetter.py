"""Compatibility entry points for the legacy forgetting API route."""

from __future__ import annotations

from core.constants import MemoryType, Scene
from core.models import ForgetLog


def preview_forget_by_instruction(
    user_id: str,
    instruction: str,
    scenario: Scene,
    memory_types: list[MemoryType],
) -> list[str]:
    """Return an empty preview until the first-stage forgetter is implemented."""

    del user_id, instruction, scenario, memory_types
    return []


def forget_by_instruction(
    user_id: str,
    instruction: str,
    scenario: Scene,
    memory_types: list[MemoryType],
    forget_mode: object,
) -> ForgetLog:
    """Provide a non-mutating result compatible with the legacy route contract."""

    del scenario, memory_types, forget_mode
    return ForgetLog(
        log_id="forgetter_not_implemented",
        user_id=user_id,
        instruction=instruction,
    )
