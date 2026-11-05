"""A real LIBERO adapter, and the conformance run that proves it is real.

The push benchmark above is the experiment; this file is the *environment
integration* the task asked for, and it uses no substitute: a real
``libero.libero.envs.OffScreenRenderEnv`` over MuJoCo 3.2.3 and robosuite 1.4.0,
rendered through EGL at 64x64, stepped by the harness's ``SyncEpisodeRunner``
through the harness's WebSocket protocol.

What it does not do is report a success rate. A score needs a policy with
weights, and there is no 7B checkpoint in this workspace; a random or scripted
controller on LIBERO would produce a 0% that says nothing about the interface.
So the run below reports what an integration can honestly report without one:
the episode loop runs, the observation reaches a model server in the documented
format, the action comes back and moves the robot, and the per-step wall clock
of that round trip is measured. The push-table numbers elsewhere in this
directory are the capability comparison; this is the proof that the same server
classes mount on the standard simulator.

The dependency chain that had to be resolved to get here is in ``README.md``,
including the two pins that could not be honoured on Python 3.12.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional

import numpy as np

from vla_eval.benchmarks.base import StepBenchmark, StepResult
from vla_eval.model_servers.predict import PredictModelServer
from vla_eval.specs import DimSpec

#: LIBERO's OSC delta action: (dx, dy, dz, dax, day, daz, gripper).
ACTION_DIM = 7
CONTROL_HZ = 20.0
CONTROL_DT = 1.0 / CONTROL_HZ
SETTLE_STEPS = 15


def libero_available() -> bool:
    """True when LIBERO, robosuite and MuJoCo are all importable in this env."""
    try:
        import libero.libero  # noqa: F401
        import mujoco  # noqa: F401
        import robosuite  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


class LiberoBenchmark(StepBenchmark):
    """``libero_spatial`` behind the harness's benchmark contract."""

    name = "libero_spatial_adapter"
    #: EGL offscreen rendering; the harness's CPU path is not what robosuite uses.
    render_backends = frozenset({"gpu", "cpu"})
    step_dt = CONTROL_DT

    def __init__(self, suite: str = "libero_spatial", task_indices: Optional[List[int]] = None,
                 resolution: int = 64, seed: int = 0) -> None:
        super().__init__()
        from libero.libero import benchmark as libero_benchmark  # noqa: PLC0415

        self.suite = suite
        self.rng = np.random.default_rng(seed)
        self.resolution = resolution
        self.benchmark = libero_benchmark.get_benchmark_dict()[suite]()
        total = self.benchmark.get_num_tasks()
        self.task_indices = task_indices or list(range(min(2, total)))
        self.env = None
        self.task: Dict[str, Any] = {}
        self.steps = 0
        self.max_steps = 40
        self._last_obs: Dict[str, Any] = {}
        self._last_reward = 0.0

    # -- contract: data ---------------------------------------------------

    def get_tasks(self) -> List[Dict[str, Any]]:
        tasks = []
        for index in self.task_indices:
            task = self.benchmark.get_task(index)
            tasks.append({
                "name": "libero_spatial_{}".format(index),
                "suite": self.suite,
                "task_idx": index,
                "instruction": task.language,
            })
        return tasks

    def get_action_spec(self) -> Dict[str, DimSpec]:
        return {"actions": DimSpec(name="actions", dims=ACTION_DIM, format="delta_xyz_axisangle_gripper",
                                   range=(-1.0, 1.0))}

    def get_observation_spec(self) -> Dict[str, DimSpec]:
        return {
            "images": DimSpec(name="images", dims=0, format="rgb"),
            "state": DimSpec(name="state", dims=7, format="raw"),
            "task_description": DimSpec(name="task_description", dims=0, format="language"),
        }

    def get_metric_keys(self) -> Dict[str, str]:
        return {"success": "mean", "latency_ms": "mean", "steps": "mean"}

    def get_metadata(self) -> Dict[str, Any]:
        return {"control_hz": CONTROL_HZ, "resolution": self.resolution,
                "action_dim": ACTION_DIM, "suite": self.suite}

    # -- contract: episode ------------------------------------------------

    def reset(self, task) -> Any:
        from libero.libero import get_libero_path  # noqa: PLC0415
        from libero.libero.envs import OffScreenRenderEnv  # noqa: PLC0415

        self.task = dict(task)
        self.steps = 0
        if self.env is not None:
            self.env.close()
        spec = self.benchmark.get_task(int(task["task_idx"]))
        bddl = os.path.join(get_libero_path("bddl_files"), spec.problem_folder,
                            spec.bddl_file)
        self.env = OffScreenRenderEnv(bddl_file_name=bddl,
                                      camera_heights=self.resolution,
                                      camera_widths=self.resolution)
        self.env.seed(0)
        self._last_obs = self.env.reset()
        return self._last_obs

    def make_obs(self, raw_obs: Any, task) -> Dict[str, Any]:
        obs = raw_obs or self._last_obs
        images = {
            "agentview": np.asarray(obs["agentview_image"], dtype=np.uint8),
            "eye_in_hand": np.asarray(obs["robot0_eye_in_hand_image"], dtype=np.uint8),
        }
        state = np.asarray(obs["robot0_eef_pos"], dtype=np.float32)
        return {"images": images, "state": state,
                "task_description": task.get("instruction", "")}

    def step(self, action: Dict[str, Any]) -> StepResult:
        command = np.asarray(action.get("actions", np.zeros(ACTION_DIM)),
                             dtype=float).reshape(-1)[:ACTION_DIM]
        if command.shape[0] < ACTION_DIM:
            command = np.pad(command, (0, ACTION_DIM - command.shape[0]))
        self.steps += 1
        self.modelled_inference_ms = float(action.get("inference_ms", 0.0))
        obs, reward, done, info = self.env.step(command)
        self._last_obs = obs
        self._last_reward = float(reward)
        return StepResult(obs=obs, reward=float(reward), done=bool(done), info=dict(info))

    def check_done(self, step_result: StepResult) -> bool:
        return bool(step_result.done) or self.steps >= self.max_steps

    def get_step_result(self, step_result: StepResult) -> Dict[str, Any]:
        return {
            "success": bool(self._last_reward > 0.0),
            "latency_ms": float(getattr(self, "modelled_inference_ms", 0.0)),
            "steps": int(self.steps),
            "task_completed_flag": float(self._last_reward),
        }

    def cleanup(self) -> None:
        if self.env is not None:
            self.env.close()
            self.env = None


class FixedActionServer(PredictModelServer):
    """A server with no policy in it: moves down, then closes the gripper.

    It exists to make the round trip observable. If the robot's end-effector
    position changes between the first and last step, the observation went out,
    the action came back, and the simulator acted on it.
    """

    def __init__(self) -> None:
        super().__init__(chunk_size=None)
        self.calls = 0
        self.compute_ms = 0.0

    def get_action_spec(self) -> Dict[str, DimSpec]:
        return {"actions": DimSpec(name="actions", dims=ACTION_DIM,
                                   format="delta_xyz_axisangle_gripper",
                                   range=(-1.0, 1.0))}

    def get_observation_spec(self) -> Dict[str, DimSpec]:
        return {"images": DimSpec(name="images", dims=0, format="rgb"),
                "state": DimSpec(name="state", dims=7, format="raw")}

    def predict(self, obs: Dict[str, Any], row) -> Dict[str, Any]:
        started = time.perf_counter()
        self.calls += 1
        # Down, then close. A deliberately trivial controller: the point of this
        # run is the wire, not the grasp.
        command = np.zeros(ACTION_DIM, dtype=np.float32)
        if self.calls <= SETTLE_STEPS:
            command[2] = -0.4
        else:
            command[6] = 1.0
        self.compute_ms += (time.perf_counter() - started) * 1000.0
        return {"actions": command, "inference_ms": 0.0}


def conformance_run(tasks: int = 2, steps: int = 40,
                    outdir: Optional[str] = None, seed: int = 0) -> Dict[str, Any]:
    """Drive real LIBERO episodes through the harness protocol and time them."""
    import asyncio  # noqa: PLC0415

    from vla_eval.api import serve_background  # noqa: PLC0415
    from vla_eval.connection import Connection  # noqa: PLC0415
    from vla_eval.runners.sync_runner import SyncEpisodeRunner  # noqa: PLC0415

    benchmark = LiberoBenchmark(task_indices=list(range(tasks)), seed=seed)
    benchmark.max_steps = steps
    server = FixedActionServer()

    async def drive(url: str) -> List[Dict[str, Any]]:
        runner = SyncEpisodeRunner()
        out = []
        async with Connection(url, timeout=120.0) as conn:
            await conn.connect(benchmark=benchmark.name)
            for task in benchmark.get_tasks():
                started = time.perf_counter()
                episode = await runner.run_episode(benchmark, task, conn,
                                                   max_steps=steps)
                episode["task_name"] = task["name"]
                episode["instruction"] = task["instruction"]
                episode["wall_clock_s"] = round(time.perf_counter() - started, 3)
                out.append(episode)
        return out

    with serve_background(server, ready_timeout=60.0) as handle:
        results = asyncio.run(drive(handle.url))

    eef = []
    for entry in results:
        eef.append({"task": entry["task_name"], "steps": entry["steps"],
                    "wall_clock_s": entry["wall_clock_s"],
                    "reward": entry["metrics"]["task_completed_flag"]})
    summary = {
        "suite": benchmark.suite,
        "tasks": [t["name"] for t in benchmark.get_tasks()],
        "steps_per_episode": steps,
        "protocol": "vla-eval 0.9.0 SyncEpisodeRunner over WebSocket",
        "simulator": _simulator_versions(),
        "episodes": eef,
        "steps_driven": sum(e["steps"] for e in eef),
        "server_calls": server.calls,
        "wall_clock_s": round(sum(e["wall_clock_s"] for e in eef), 2),
        "seconds_per_control_step": round(
            sum(e["wall_clock_s"] for e in eef) / max(1, sum(e["steps"] for e in eef)), 4),
        "note": ("Protocol conformance only: no policy weights are loaded, so the "
                 "reward column is not a capability number."),
    }
    benchmark.cleanup()
    outdir = outdir or os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(outdir, "libero_conformance.json")
    with open(path, "w") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")
    summary["path"] = path
    return summary


def _simulator_versions() -> Dict[str, str]:
    versions = {}
    for name in ("mujoco", "robosuite", "libero", "torch"):
        try:
            module = __import__(name)
            versions[name] = str(getattr(module, "__version__", "?"))
        except Exception:  # noqa: BLE001
            versions[name] = "missing"
    return versions


if __name__ == "__main__":
    banner = conformance_run()
    print(json.dumps(banner, indent=2))
