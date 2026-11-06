# v1.0.8 — A Disclaimer, and What It Does Not Do

No code changed. This release adds a physical-safety disclaimer to the top of the
repository README, the console README, and the CEL front page, and points the Docker
build at this tag. The core suite is unchanged at 1122 tests (9 skipped).

## The disclaimer

The text is at the top of `README.md` and `product_mvp/README.md`. Three things it
says, in the order that matters:

1. **Research and evaluation only.** The code, the tests and the console are not
   designed, not tested and not validated for a safety-critical or physical-world
   system.
2. **This is not a certification.** Using the open-source code does not constitute a
   Physical Safety Certification, a conformity assessment or a regulatory approval.
   No audit, certification mark or endorsement is issued by the author or by this
   project. A commercial deployment in robotics, autonomous vehicles, industrial
   control, medical devices or aerospace must be independently audited and
   certified by a qualified third party, and is outside the scope of this project.
3. **No liability for physical-world harm.** To the maximum extent permitted by law,
   the author assumes no liability for direct, indirect, incidental, special,
   consequential or exemplary damages, including personal injury, damage to or
   destruction of robots or other property, autonomous driving incidents, and loss or
   corruption of data, whether in contract, tort (including negligence), strict
   liability or otherwise. The software is provided "AS IS", with no warranty.

## What it does not do

A README notice is not a licence, and it is not a substitute for one. Two limits are
worth stating plainly rather than implying a protection that is not there:

- **This repository has no `LICENSE` file.** Under default copyright, "public on
  GitHub" is not a grant of rights — but it is also not the clear, uniform permission
  that a disclaimer usually sits on top of. A disclaimer narrows what a user may
  claim; a licence defines what a user may do. They are different instruments, and
  only one of them is present here.
- **A notice does not bind a party that never agreed to it.** It is evidence of
  intent and of the terms of use, not a contract. Enforceability in a given
  jurisdiction — and the effect of any mandatory consumer or product-liability
  statute — is a question for a lawyer, not for this repository.

Nothing here is legal advice, and this release does not pretend otherwise.

## Docker now pins v1.0.8

`product_mvp/Dockerfile` fetches the core tarball by tag, so the default
`CEL_CORE_REF` moves from `v1.0.7` to `v1.0.8`. The referenced code is identical to
v1.0.7; the pin exists so a build is reproducible rather than "whatever `main` is
today". `product_mvp/README.md` and `DEPLOYMENT.md` follow the same number.

## Tests

| Suite | Before | After |
| --- | --- | --- |
| core (`agent_metabolism`) | 1122 | 1122 |
| `product_mvp` engine | 23 | 23 |
| `product_mvp` HTTP | 34 | 34 |

Documentation-only change, so the suites are re-run to prove nothing else moved, not
because they were expected to change.
