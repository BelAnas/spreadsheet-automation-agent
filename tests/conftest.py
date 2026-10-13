"""Shared fixtures. Everything runs offline on generated files in a temporary folder."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import pytest

from sheetagent import synth
from sheetagent.llm import Usage
from sheetagent.readers import RawTable


@dataclass
class ScriptedLLM:
    """A stand-in model: answers from a list, or from a function of the prompt. Records every prompt."""
    answers: list = field(default_factory=list)
    rule: object = None
    name: str = "scripted"
    model: str = "scripted"
    is_mock: bool = False
    usage: Usage = field(default_factory=Usage)
    prompts: list = field(default_factory=list)

    def complete(self, system: str, user: str, max_tokens: int = 200) -> str:
        self.prompts.append(user)
        self.usage.calls += 1
        if self.rule is not None:
            return self.rule(user)
        answer = self.answers.pop(0) if self.answers else ""
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture(scope="session")
def demo(tmp_path_factory) -> tuple[Path, dict[str, pd.DataFrame]]:
    root = tmp_path_factory.mktemp("demo")
    return root, synth.write_demo(root)


def grid(rows: list[list]) -> RawTable:
    width = max(len(r) for r in rows)
    return RawTable("test.xlsx", "Sheet1", pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object))
