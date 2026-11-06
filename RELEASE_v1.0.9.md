# v1.0.9 — MIT, So "Open Source" Stops Being a Figure of Speech

No code changed. This release adds a `LICENSE` file (MIT, 2026, Zhizhi Liao), points
`pyproject.toml` at it, and adds a License section to the READMEs. The core suite is
unchanged at 1122 tests (9 skipped).

## Why this is a release and not a typo fix

v1.0.8 added a physical-safety disclaimer and said plainly what was missing: there
was no `LICENSE` at all. Under default copyright, code being readable on GitHub grants
nobody the right to use it, so every README sentence that said "open source" was
describing an intention rather than a permission.

MIT fixes the half that a disclaimer cannot: it *grants* rights — use, copy, modify,
merge, publish, distribute, sublicense, sell — subject only to keeping the copyright
and permission notice. The disclaimer fixes the half a licence does not: it states
what the code is not. Two documents, two jobs, and both are now present.

## What changed

```
LICENSE                     new — MIT, Copyright (c) 2026 Zhizhi Liao
pyproject.toml              license = "MIT", license-files, setuptools>=77
README.md                   new License section
product_mvp/README.md       new License section (§7)
RELEASE_v1.0.9.md           new
```

The README section does not just name the licence, because naming it was never the
problem. It says which instrument does what: MIT grants permission and carries the
"AS IS" warranty and liability disclaimer; the physical-safety notice at the top of
the same file states the scope that permission is granted in — research and
evaluation, with no certification implied and an independent audit required before
any physical-world deployment.

## Getting the metadata line right took two tries

Declaring a licence in `pyproject.toml` is not a one-liner if the file is supposed to
stay buildable. Both of the first two attempts failed `pip wheel`, and only the third
produced one:

| Attempt | Written as | Build result |
| --- | --- | --- |
| 1 | `license = { text = "MIT" }`, `setuptools>=61` | **failed** — `project.license` must be a string |
| 2 | `license = "MIT"` + `License :: OSI Approved :: MIT License` classifier | **failed** — licence classifiers are superseded by licence expressions (PEP 639) |
| 3 | `license = "MIT"`, `license-files`, no licence classifier, `setuptools>=77` | built |

The table form has been the documented way to write this for years, and every setuptools
released since 77 rejects it. Keeping `setuptools>=61` would have meant a fresh install
resolving to a version that refuses to build the package — a broken build that only
appears for someone who does not already have an old setuptools cached locally. The
build requirement now states 77, and the licence is an SPDX expression.

Verified in the built wheel, not in the source tree:

```
agent_metabolism_os-1.0.9-py3-none-any.whl
agent_metabolism_os-1.0.9.dist-info/licenses/LICENSE
License-Expression: MIT
License-File: LICENSE
```

## Docker now pins v1.0.9

`product_mvp/Dockerfile` fetches the core tarball by tag, so `CEL_CORE_REF` moves from
`v1.0.8` to `v1.0.9`. The code is byte-identical to v1.0.8; the pin moves so that a
build is reproducible instead of tracking `main`. `product_mvp/README.md` and
`DEPLOYMENT.md` follow the same number.

## Tests

| Suite | Before | After |
| --- | --- | --- |
| core (`agent_metabolism`) | 1122 | 1122 |
| `product_mvp` engine | 23 | 23 |
| `product_mvp` HTTP | 34 | 34 |

Re-run because the package metadata changed, not because the code did.

## Known limits

- **MIT gives away the certification business, and that is the point.** MIT permits
  commercial use, so a company can deploy this in a robot without paying anything.
  What MIT does not give them is a certification, a warranty, or the author's
  liability — those were never for sale in the open-source tier, and the notice says
  so. A paid audit is a separate agreement, to be drafted when a first customer asks.
- **A README notice is still not a contract.** It binds the reader to nothing they did
  not accept; enforceability depends on jurisdiction and on mandatory product-liability
  law. MIT's own liability clause does real work here, but no wording makes a notice
  equivalent to a signed agreement.
- **Not legal advice.** The text was written to be accurate about what each document
  does, not to be a substitute for counsel.
