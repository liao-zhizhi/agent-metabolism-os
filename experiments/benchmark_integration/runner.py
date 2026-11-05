"""Driving the harness: its server, its protocol, its episode runner.

No episode loop is written here. The pieces are the harness's own:

* ``vla_eval.api.serve_background`` hosts a ``PredictModelServer`` on a real
  WebSocket, with the harness's HELLO / EPISODE_START / OBSERVATION / ACTION /
  EPISODE_END sequence;
* ``vla_eval.connection.Connection`` is the benchmark-side client;
* ``vla_eval.runners.SyncEpisodeRunner`` is the loop that steps the environment
  and asks for an action at every control step.

What this module adds is the part the harness leaves to the caller outside
Docker: which ``Benchmark`` instance and which ``Task`` list go into a run. The
run is therefore the harness's protocol and the harness's runner against our
environment -- an in-process integration, not a reimplementation.

The Docker path (``vla-eval run --benchmark libero ...``) is what a full
LIBERO/SimplerEnv number would use; ``README.md`` records exactly why it did not
run here, with the commands that were attempted and their output.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Sequence

from vla_eval.api import serve_background
from vla_eval.connection import Connection
from vla_eval.runners.sync_runner import SyncEpisodeRunner

from .env_adapter import HORIZON, PushTableBenchmark
from .servers import _ArmServer, build_arm


async def _drive(url: str, benchmark: PushTableBenchmark,
                 episodes: Sequence[Dict[str, Any]],
                 max_steps: int = HORIZON) -> List[Dict[str, Any]]:
    runner = SyncEpisodeRunner()
    results: List[Dict[str, Any]] = []
    async with Connection(url, timeout=60.0) as conn:
        await conn.connect(benchmark=benchmark.name)
        for index, task in enumerate(episodes):
            episode = await runner.run_episode(benchmark, task, conn, max_steps=max_steps)
            episode["episode_id"] = index
            episode["task_name"] = task["name"]
            episode["suite"] = task.get("suite", "")
            episode["friction"] = float(task.get("friction", 0.0))
            episode["task_idx"] = int(task.get("task_idx", 0))
            results.append(episode)
    return results


def run_arm(arm: str, episodes: Sequence[Dict[str, Any]], suite: str,
            seed: int = 0, server: _ArmServer | None = None) -> tuple:
    """Serve one arm through the harness and return ``(episodes, server)``."""
    arm_server = server or build_arm(arm, seed=seed)
    benchmark = PushTableBenchmark(suite=suite, seed=seed)
    with serve_background(arm_server, ready_timeout=60.0) as handle:
        results = asyncio.run(_drive(handle.url, benchmark, episodes))
    return results, arm_server
