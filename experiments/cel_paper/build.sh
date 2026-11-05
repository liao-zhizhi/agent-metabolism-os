#!/usr/bin/env bash
# Build the CEL paper from a clean state.
#
# Numbers first: every statistic in the body is an \input of data/*.tex, which
# make_numbers.py derives from the audit's JSON and CSV. Building the PDF without
# regenerating them would let the prose and the data drift apart -- exactly the
# failure the paper is about -- so the generator runs first and fails loudly.
set -euo pipefail

cd "$(dirname "$0")"

# Where the audit's outputs are. In the repository that is the parent directory;
# in the submitted archive the same files are under ./provenance, so the build is
# a function of the archive alone.
DATA_DIR=".."
if [ ! -f ../libero_audit_summary.json ] || [ ! -f ../libero_audit_episodes.csv ]; then
  if [ -f provenance/libero_audit_summary.json ]; then
    DATA_DIR="provenance"
    export CEL_DATA_DIR="$(pwd)/provenance"
  else
    echo "missing the audit's outputs; run experiments/benchmark_libero_audit.py first" >&2
    exit 1
  fi
fi
if [ ! -f "${DATA_DIR}/ad_cel_summary.json" ] || [ ! -f "${DATA_DIR}/ad_cel_data.csv" ]; then
  echo "missing the driving audit's outputs; run experiments/ad_cel_audit.py first" >&2
  exit 1
fi
if [ ! -f "${DATA_DIR}/ad_cel_trace.json" ]; then
  echo "missing the traced episode; rerun experiments/ad_cel_audit.py with" >&2
  echo "  --trace-json experiments/ad_cel_trace.json" >&2
  exit 1
fi

# Both figures are drawn from the data, here, every build. The previous version of
# this script copied a PNG that no script in the repository produced, which is how
# a panel ended up carrying numbers from a different experiment.
python3 make_numbers.py
python3 make_figures.py --data-dir "${DATA_DIR}"

rm -f main.aux main.bbl main.blg main.out main.log main.fls main.fdb_latexmk

# pdfTeX stamps /CreationDate and /ID from the wall clock, so two builds of
# identical sources produce different bytes and a SHA-256 identifies a file but
# not a build. Pinning the date makes the digest reproducible, which is the same
# property this paper asks of a witness: the artifact should be a function of
# its inputs, not of when it was made.
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1735689600}"   # 2025-01-01T00:00:00Z
export FORCE_SOURCE_DATE=1

pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
bibtex main >/dev/null
pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null
pdflatex -interaction=nonstopmode -halt-on-error main.tex >/dev/null

errors=$(grep -c '^!' main.log || true)
if [ "${errors}" -ne 0 ]; then
  echo "compile failed: ${errors} error(s) in main.log" >&2
  grep -n -A4 '^!' main.log >&2
  exit 1
fi

undefined=$(grep -cE "Citation .* undefined|Reference .* undefined" main.log || true)
if [ "${undefined}" -ne 0 ]; then
  echo "warning: ${undefined} undefined reference(s)/citation(s)" >&2
  grep -n 'undefined' main.log >&2
fi

echo "built $(pwd)/main.pdf ($(pdfinfo main.pdf 2>/dev/null | awk '/^Pages/{print $2}') pages)"
sha256sum main.pdf
