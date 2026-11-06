# v1.0.10 — Patch: Standardize the Copyright Holder to 'Zhizhi Liao' for Legal Clarity

Patch: Standardize the copyright holder to 'Zhizhi Liao' for legal clarity.

No code changed. The only functional difference between this tag and `v1.0.9` is
the author string in the built artefact's metadata, plus the Docker pin moving to
this tag.

## Why this tag exists at all

`v1.0.9` was tagged before the copyright holder was standardised, so building from
that tag produces:

```
Author: liaozhizhi
```

while building from `main` produces:

```
Author: Zhizhi Liao
```

Two names for one author, depending on which ref you check out. `product_mvp/Dockerfile`
fetches the core by tag (`CEL_CORE_REF`), so the difference is not theoretical: a
container built at `v1.0.9` and one built at `main` would carry different metadata for
the same code.

A one-line metadata change is still a change to the artefact, so it gets a version.

## What changed

```
pyproject.toml                  authors: "liaozhizhi" -> "Zhizhi Liao"
pyproject.toml                  version = "1.0.10"
agent_metabolism/__init__.py    __version__ = "1.0.10"
product_mvp/Dockerfile          CEL_CORE_REF = v1.0.9 -> v1.0.10
product_mvp/README.md           tag reference follows
product_mvp/DEPLOYMENT.md       tag reference follows
```

The core library version and the Docker pin move together, because a `CEL_CORE_REF`
that names an older tag than the release it ships with is how a rebuild silently
produces something other than what the release notes describe.

## Verified from a fresh clone at the tag

```
git clone --branch v1.0.10 https://github.com/liao-zhizhi/agent-metabolism-os
cd agent-metabolism-os && pip wheel --no-deps --no-build-isolation .
```

Read back from the built wheel:

```
Version:        1.0.10
Author:         Zhizhi Liao
License-Expression: MIT
License-File:   LICENSE
```

## Tests

| Suite | Before | After |
| --- | --- | --- |
| core (`agent_metabolism`) | 1122 | 1122 |
| `product_mvp` engine | 23 | 23 |
| `product_mvp` HTTP | 34 | 34 |
