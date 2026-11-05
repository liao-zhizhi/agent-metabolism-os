"""The task definitions, with no harness in them.

Split out of ``env_adapter`` so that the suites, the planner and the tests can be
imported on a machine where ``vla_eval`` is not installed. The adapter is the
only thing that needs the harness; the definition of a task does not, and a
module that cannot be imported without a dependency it does not use is a module
that will be imported with mocks sooner or later.
"""
from __future__ import annotations

from typing import Any, Dict, Sequence

from experiments.physics_sandbox import FRAME_X0, FRAME_X1, LANES

#: Control rate. 10 Hz is the rate VLA policies are quoted at in LIBERO and
#: SimplerEnv rollouts: a 10-step chunk at 10 Hz is one second of open-loop
#: execution.
CONTROL_DT = 0.10
CHUNK_SIZE = 10

#: The embodiment's velocity envelope, m/s. A command above this is not a physics
#: failure; it is the policy asking the robot for something it cannot do. 3.0 is
#: set by the task: the longest push in the suite (0.5 m) needs a 1.85 m/s launch
#: at nominal friction and 2.30 m/s at the roughest regime in the shift suite, so
#: a 1.5 m/s envelope would make the task unsolvable and every arm would fail for
#: a reason unrelated to the comparison.
MAX_COMMAND_SPEED = 3.0

#: Horizon in control steps: three seconds. Long enough for a plan that has to be
#: replanned twice, short enough that a stuck episode is a failure, not a timeout.
HORIZON = 30

#: Distance past the frame edge that counts as a miss rather than a near miss.
OVERSHOOT_TOLERANCE = 0.05

#: A block still sliding at the final step has not arrived, however well aimed.
SETTLED_SPEED = 0.02

NOMINAL_FRICTION = 0.35

TASK_NAMES = ("push_lane_0", "push_lane_1", "push_lane_2", "push_lane_3", "push_lane_4")
INSTRUCTIONS = tuple(
    "push the block in row {} into the target frame".format(i + 1) for i in range(5))

START_X = 0.16
DECOY_X = 0.86


def episode_task(task_idx: int, suite: str = "nominal",
                 friction: float = NOMINAL_FRICTION,
                 frame: Sequence[float] = (FRAME_X0, FRAME_X1)) -> Dict[str, Any]:
    """A ``vla_eval`` task descriptor -- the dict the harness threads through.

    ``friction`` and ``frame`` travel with the task rather than with the
    benchmark object, so an evaluation suite is an explicit list of worlds. Two
    arms are run against the same list, which is the only way a difference
    between them is a difference between them.
    """
    x0, x1 = float(frame[0]), float(frame[1])
    return {
        "name": TASK_NAMES[task_idx],
        "suite": suite,
        "task_idx": task_idx,
        "lane": LANES[task_idx],
        "instruction": INSTRUCTIONS[task_idx],
        "target_x": 0.5 * (x0 + x1),
        "frame": (x0, x1),
        "friction": float(friction),
    }
