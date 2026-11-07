#!/usr/bin/env python3
"""Switch the landing page from the git+ install command to the PyPI one.

Run this *after* the package is on PyPI. It refuses to touch anything until
PyPI itself confirms the release exists, because the failure it prevents is
one-directional and embarrassing: flipping the hero CTA to `pip install
harness-audit-sidecar` while that name still 404s means every visitor's first
action fails, at the exact moment the page is claiming the tool is installable.

That check is not a formality. It is the same rule the product sells -- a claim
must be backed by an independent witness -- applied to the page's own claim about
the outside world. `deploy_demo.sh` asserts the same thing at deploy time; this
script asserts it at edit time so the tree is never left in a state the deploy
would have to reject.

The command appears in three places, and they must move together:

    templates/index.html   #install-cmd       the copyable block
    templates/index.html   .dev-note           the sentence explaining it
    api_server.py          write_policy.install.pip   what /api/v1/stats reports

`tests/test_api.py` pins the agreement between the first and the third, so a
half-applied switch fails the unit suite as well.

Usage:
    scripts/switch_install_command.py --check   # what would change (read-only)
    scripts/switch_install_command.py --apply   # do it

Exit codes:
    0  already switched, or --apply succeeded
    1  PyPI does not have the package; nothing was touched
    2  the source no longer looks the way this script expects; nothing was touched
    3  --check found work to do
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

PKG = "harness-audit-sidecar"
BARE = f"pip install {PKG}"
GIT = f'pip install "git+https://github.com/liao-zhizhi/{PKG}.git"'

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "product_mvp" / "templates" / "index.html"
API = ROOT / "product_mvp" / "api_server.py"

#: The old dev-note, verbatim. Replaced as a block rather than line by line so a
#: half-edited note cannot survive: the note's job is to say "this is the command
#: that works today", and after the release the old note says the opposite.
NOTE_OLD = """      <p class="dev-note">
        PyPI 上尚未发布，所以上面的命令走 git，今天就能装。
        发布后命令会简化为 <code>pip install harness-audit-sidecar</code>。
        装完应该有一个可执行文件：
        <code>harness-audit-server --help</code>。
      </p>"""

NOTE_NEW = f"""      <p class="dev-note">
        已发布在 PyPI 上，装完应该有一个可执行文件：
        <code>harness-audit-server --help</code>。
        也可以用 git 直接装当前开发版：
        <code>pip install "git+https://github.com/liao-zhizhi/{PKG}.git"</code>。
      </p>"""


def pypi_version() -> str | None:
    """The latest version on PyPI, or None. Network failure is not 'missing'.

    A timeout must not be read as "not published": that would refuse a switch
    that is actually correct, and the operator would learn to add an override
    flag. Better to fail loudly and let them retry.
    """
    url = f"https://pypi.org/pypi/{PKG}/json"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.load(resp)["info"]["version"]
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return None
        raise SystemExit(
            f"FATAL: PyPI answered {err.code} for {PKG}; cannot tell whether it "
            f"is published. Nothing changed."
        )
    except Exception as err:  # noqa: BLE001 - any transport failure
        raise SystemExit(
            f"FATAL: could not reach PyPI ({type(err).__name__}: {err}). "
            f"Nothing changed -- refusing to guess."
        )


def plan() -> list[tuple[Path, str, str]]:
    """``(path, old, new)`` triples. Asserts each ``old`` occurs exactly once.

    Exactly once, not at least once: two occurrences would mean a previous edit
    left a second copy behind, and replacing only the first is how a page ends up
    advertising two different commands in the same panel.
    """
    edits: list[tuple[Path, str, str]] = []

    index = INDEX.read_text(encoding="utf-8")
    block_old = f'<code id="install-cmd">{GIT}</code>'
    block_new = f'<code id="install-cmd">{BARE}</code>'
    if block_old in index:
        edits.append((INDEX, block_old, block_new))
    if NOTE_OLD in index:
        edits.append((INDEX, NOTE_OLD, NOTE_NEW))

    api = API.read_text(encoding="utf-8")
    api_old = (
        '            "pip": \'pip install "git+https://github.com/liao-zhizhi/\'\n'
        "                   'harness-audit-sidecar.git\"',\n"
    )
    api_new = f'            "pip": "{BARE}",\n'
    if api_old in api:
        edits.append((API, api_old, api_new))

    for path, old, _ in edits:
        count = path.read_text(encoding="utf-8").count(old)
        if count != 1:
            raise SystemExit(
                f"FATAL: {path.name} contains the target text {count} times, "
                f"expected exactly 1. The source drifted from what this script "
                f"expects; nothing changed."
            )
    return edits


def leftover_git_plus() -> list[str]:
    """Any remaining ``git+`` in the two served strings: an incomplete switch."""
    problems = []
    index = INDEX.read_text(encoding="utf-8")
    block = index.split('<code id="install-cmd">', 1)[1].split("</code>", 1)[0]
    if "git+" in block:
        problems.append("index.html #install-cmd still contains git+")
    api = API.read_text(encoding="utf-8")
    section = api.split('"install": {', 1)[1].split("}", 1)[0]
    pip_line = next((l for l in section.splitlines() if '"pip"' in l), "")
    if "git+" in pip_line:
        problems.append("api_server.py write_policy.install.pip still contains git+")
    return problems


def main(argv: list[str]) -> int:
    apply_mode = "--apply" in argv
    check_mode = "--check" in argv or not apply_mode
    if not (apply_mode or check_mode):
        print(__doc__)
        return 2

    version = pypi_version()
    if version is None:
        print(
            f"REFUSING: {PKG} is not on PyPI (404).\n"
            f"  The page tells visitors to run '{BARE}', and that command would\n"
            f"  fail. Publish first (see agent-harness-audit/scripts/publish_pypi.sh),\n"
            f"  then run this again. Nothing was changed.",
            file=sys.stderr,
        )
        return 1

    print(f"{PKG} {version} is on PyPI")

    edits = plan()
    if not edits:
        problems = leftover_git_plus()
        if problems:
            print("INCOMPLETE: no editable target matched, but:", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 2
        print("already switched: nothing to do")
        return 0

    for path, old, new in edits:
        print(f"  {path.relative_to(ROOT)}: {old.splitlines()[0][:64]}... -> replaced")

    if not apply_mode:
        print("\n--check: would apply {} edit(s). Run --apply to write.".format(len(edits)))
        return 3

    for path, old, new in edits:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    problems = leftover_git_plus()
    if problems:
        print("FATAL: switch left git+ behind:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    print("\nswitched. Next:")
    print("  product_mvp/.venv/bin/python -m unittest discover -s product_mvp/tests -q")
    print("  CEL_DEPLOY_PYTHON=.venv/bin/python bash scripts/deploy_demo.sh")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
