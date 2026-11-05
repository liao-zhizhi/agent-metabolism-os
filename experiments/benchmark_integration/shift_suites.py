"""Evaluation suites: three distributions, one episode list, shared by every arm.

A benchmark comparison is only a comparison if the arms see the same episodes.
So a suite is materialised once as a list of task dicts, and every arm is run
against that same list -- same friction, same lane, same frame, same order. The
frictions are not sampled per arm and not resampled per episode inside an arm.

Three suites, chosen so that a win in one and a loss in another are both
informative:

``nominal``         every episode at the calibration friction. A system that has
                    learned nothing still scores here; this is the number a
                    leaderboard normally reports, and it is why a leaderboard can
                    hide a distribution shift.

``friction_shift``  50-episode regimes: nominal, slippery, nominal, rough,
                    slippery, rough. Non-i.i.d. by construction -- a regime
                    arrives and stays for 50 episodes, so "recover" means
                    "recover without the world moving back to help".

``layout_shift``    the friction is nominal and the *task* moves: for 50-episode
                    windows the frame sits 8 cm further along the lane. Physics
                    is unchanged, so a system with a correct friction belief
                    should be unaffected. This is the control: it separates
                    "the causal interface is better at physics" from "the causal
                    interface refuses more", and a win here would be a bug.
"""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from .task_spec import NOMINAL_FRICTION, episode_task

SLIPPERY = 0.18
ROUGH = 0.54

BASE_FRAME = (0.60, 0.72)
SHIFTED_FRAME = (0.68, 0.80)

WINDOW = 50
DEFAULT_EPISODES = 300


def _segments(episodes: int, window: int, values: Sequence[Any]) -> List[Any]:
    """Repeat *values* as windows, and truncate/pad to *episodes*."""
    out: List[Any] = []
    index = 0
    while len(out) < episodes:
        out.extend([values[index % len(values)]] * window)
        index += 1
    return out[:episodes]


def friction_shift_episodes(episodes: int = DEFAULT_EPISODES) -> tuple:
    """Friction regimes in 50-episode windows, with the onsets recorded."""
    sequence = [NOMINAL_FRICTION, SLIPPERY, NOMINAL_FRICTION, ROUGH,
                SLIPPERY, ROUGH]
    frictions = _segments(episodes, WINDOW, sequence)
    onsets = [i for i in range(1, len(frictions)) if frictions[i] != frictions[i - 1]]
    return frictions, onsets


def layout_shift_episodes(episodes: int = DEFAULT_EPISODES) -> tuple:
    frames = _segments(episodes, WINDOW, [BASE_FRAME, SHIFTED_FRAME,
                                          BASE_FRAME, SHIFTED_FRAME])
    onsets = [i for i in range(1, len(frames)) if frames[i] != frames[i - 1]]
    return frames, onsets


def build(suite: str, episodes: int = DEFAULT_EPISODES) -> Dict[str, Any]:
    """The suite as an explicit episode list plus the shift onsets."""
    if suite == "nominal":
        frictions = [NOMINAL_FRICTION] * episodes
        frames = [BASE_FRAME] * episodes
        onsets: List[int] = []
    elif suite == "friction_shift":
        frictions, onsets = friction_shift_episodes(episodes)
        frames = [BASE_FRAME] * episodes
    elif suite == "layout_shift":
        frames, onsets = layout_shift_episodes(episodes)
        frictions = [NOMINAL_FRICTION] * episodes
    else:
        raise ValueError("unknown suite {!r}".format(suite))

    tasks = []
    for index in range(episodes):
        tasks.append(episode_task(
            task_idx=index % 5,
            suite=suite,
            friction=frictions[index],
            frame=frames[index],
        ))
    return {
        "name": suite,
        "episodes": tasks,
        "onsets": onsets,
        "frictions": frictions,
        "frames": frames,
    }


SUITES = ("nominal", "friction_shift", "layout_shift")
