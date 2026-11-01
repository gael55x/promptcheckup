"""Replay cases through two prompt sets, back to back, with a call budget and a cache."""

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from promptcheckup.data import Case
from promptcheckup.runner.agents import Agent, AgentError, call_agent

PromptSet = Literal["A", "B"]


def prompt_hash(prompts: dict[str, str]) -> str:
    """Stable hash of a prompt set; key order does not matter, any text change does."""
    canonical = json.dumps(prompts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class Cache:
    """Successful agent outputs on disk, one JSON file per (prompt set, case, sample).

    Entries expire after `ttl_hours`: a pair is only fair when both sides ran under the
    same conditions, so outputs from an earlier day must not stand in for a fresh run.
    """

    def __init__(
        self,
        directory: Path,
        *,
        ttl_hours: float = 24,
        agent_version: str = "",
        clock: Callable[[], float] = time.time,
    ):
        self.directory = Path(directory)
        self.ttl_seconds = ttl_hours * 3600
        self.agent_version = agent_version
        self.clock = clock

    def _path(self, prompts_hash: str, case_id: str, sample: int) -> Path:
        key = json.dumps([prompts_hash, case_id, sample, self.agent_version])
        return self.directory / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}.json"

    def get(self, prompts_hash: str, case_id: str, sample: int) -> tuple[bool, Any]:
        path = self._path(prompts_hash, case_id, sample)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            created_at, output = float(entry["created_at"]), entry["output"]
        except (OSError, ValueError, KeyError, TypeError):
            return False, None  # missing or damaged entries are simply re-run
        if self.clock() - created_at > self.ttl_seconds:
            return False, None
        return True, output

    def put(self, prompts_hash: str, case_id: str, sample: int, output: Any) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        entry = {"created_at": self.clock(), "output": output}
        self._path(prompts_hash, case_id, sample).write_text(json.dumps(entry), encoding="utf-8")


@dataclass(frozen=True)
class Run:
    case_id: str
    prompt_set: PromptSet
    sample: int
    output: Any = None
    error: str | None = None
    cached: bool = False


@dataclass(frozen=True)
class Replay:
    runs: list[Run]
    calls: int  # live agent calls; cache hits are free
    budget_exhausted: bool


def replay(
    agent: Agent,
    cases: Sequence[Case],
    prompts_a: dict[str, str],
    prompts_b: dict[str, str],
    *,
    samples: int = 2,
    timeout_s: float = 60,
    max_calls: int = 400,
    cache: Cache | None = None,
) -> Replay:
    """Run every case through A then B, `samples` times, keeping each pair complete.

    Stops before any pair that would exceed `max_calls`, so the result never holds half
    a pair. Agent failures become runs with an error; they are never cached.
    """
    hashes = {"A": prompt_hash(prompts_a), "B": prompt_hash(prompts_b)}
    prompts = {"A": prompts_a, "B": prompts_b}
    runs: list[Run] = []
    calls = 0

    for case in cases:
        for sample in range(samples):
            hits = {
                side: cache.get(hashes[side], case.id, sample) if cache else (False, None)
                for side in ("A", "B")
            }
            needed = sum(not found for found, _ in hits.values())
            if calls + needed > max_calls:
                return Replay(runs, calls, budget_exhausted=True)

            for side in ("A", "B"):
                found, output = hits[side]
                if found:
                    runs.append(Run(case.id, side, sample, output=output, cached=True))
                    continue
                calls += 1
                try:
                    output = call_agent(agent, case.input, prompts[side], timeout_s=timeout_s)
                except AgentError as e:
                    runs.append(Run(case.id, side, sample, error=str(e)))
                    continue
                if cache:
                    cache.put(hashes[side], case.id, sample, output)
                runs.append(Run(case.id, side, sample, output=output))

    return Replay(runs, calls, budget_exhausted=False)
