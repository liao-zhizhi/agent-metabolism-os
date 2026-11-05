"""Serve one arm so the harness CLI can drive a run against it.

``vla-eval run --no-docker -c cel_eval.yaml`` needs a model server at the URL in
its config. This is that server, started the same way any VLA server is started:
by handing a ``ModelServer`` to the harness's WebSocket server.

    .venv/bin/python -m experiments.benchmark_integration.serve_arm --arm B --port 8123
"""
from __future__ import annotations

import argparse
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from vla_eval.model_servers.serve import serve

from .servers import build_arm


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default="B", choices=("A", "C", "B"))
    parser.add_argument("--port", type=int, default=8123)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)

    server = build_arm(args.arm, seed=args.seed)
    print("serving arm {} at ws://{}:{}".format(server.arm_name, args.host, args.port),
          flush=True)
    serve(server, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
