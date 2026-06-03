"""Loop detection — three-layer defense against stuck agents.

Layer 1 (Fingerprint): SHA-256 hash of tool calls, detect exact repeats
Layer 2 (Ceiling): Iteration limit + cost budget + wall-clock timeout (in agent.py)
Layer 3 (Human fallback): Pause and ask when ceiling hits (in cli.py)
"""

import hashlib
import logging
from typing import Any

logger = logging.getLogger("ming")


class LoopDetector:
    """Detects repeated tool calls via fingerprinting."""

    def __init__(self, warn_threshold: int = 3, block_threshold: int = 5):
        self.warn_threshold = warn_threshold
        self.block_threshold = block_threshold
        self._fingerprints: list[str] = []
        self._consecutive_identical: int = 0
        self._last_fingerprint: str = ""
        self._warned: bool = False

    def _hash_call(self, tool_name: str, arguments: str) -> str:
        """Create a fingerprint for a tool call."""
        content = f"{tool_name}:{arguments}"
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def check(self, tool_name: str, arguments: str) -> str:
        """Check if a tool call looks like a loop.

        Returns:
            "allow" — proceed normally
            "warn"  — inject warning to LLM, but allow
            "block" — refuse to execute, inject error
        """
        fp = self._hash_call(tool_name, arguments)
        self._fingerprints.append(fp)

        if fp == self._last_fingerprint:
            self._consecutive_identical += 1
        else:
            self._consecutive_identical = 1
            self._last_fingerprint = fp
            self._warned = False

        if self._consecutive_identical >= self.block_threshold:
            logger.warning(
                "Loop blocked: %s called %sx identically",
                tool_name,
                self._consecutive_identical,
            )
            return "block"

        if self._consecutive_identical >= self.warn_threshold and not self._warned:
            logger.warning(
                "Loop warning: %s called %sx identically",
                tool_name,
                self._consecutive_identical,
            )
            self._warned = True
            return "warn"

        return "allow"

    def reset(self) -> None:
        """Reset state for a new user turn."""
        self._fingerprints = []
        self._consecutive_identical = 0
        self._last_fingerprint = ""
        self._warned = False

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "total_calls": len(self._fingerprints),
            "consecutive_identical": self._consecutive_identical,
        }
