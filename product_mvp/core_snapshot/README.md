# Optional local core for image builds.
#
# The Dockerfile normally fetches the core library from the pinned GitHub release
# tarball (CEL_CORE_REF). That is right for a published image, and wrong the
# moment the core has unreleased changes: the API would ship code calling a
# method the image's core does not have, and it would fail at request time
# rather than at build time.
#
# `scripts/deploy_demo.sh` fills this directory with the working tree's
# `agent_metabolism/` when it detects that the core differs from the release.
# The Dockerfile prefers whatever is here and falls back to the tarball when the
# directory carries only this file.
#
# It is empty in the repository on purpose: a plain `docker build` must stay
# reproducible against a release tag, not against whatever was in someone's
# checkout.
