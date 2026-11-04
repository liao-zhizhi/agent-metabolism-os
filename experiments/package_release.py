"""Assemble the delivery zip from the sources that already exist.

Extract the previous package, overlay the files this revision touched, and re-zip
deterministically. Overlaying rather than rebuilding the manifest by hand is
deliberate: a package assembled from a hand-maintained list is a package that
silently loses a file the first time nobody is looking.

    python3 experiments/package_release.py

Writes ~/桌面/fractal_interface_paper.zip and refreshes the dated delivery folder.
No network. No commit. No push.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PAPER = os.path.join(HERE, "paper")

DESKTOP = os.path.expanduser("~/桌面")
DATED = os.path.join(DESKTOP, "分形接口论文_20261104")
PREVIOUS = os.path.join(DATED, "02_fractal_interface_paper.zip")
TARGET = os.path.join(DESKTOP, "fractal_interface_paper.zip")

TOP = "fractal_interface_paper"
FIXED_TIME = (2026, 11, 4, 0, 0, 0)

#: Every file this revision added or changed, relative to the package root.
OVERLAY = [
    ("main.pdf", os.path.join(PAPER, "main.pdf")),
    ("ERRATA.md", os.path.join(ROOT, "ERRATA.md")),
    ("main.tex", os.path.join(PAPER, "main.tex")),
    ("references.bib", os.path.join(PAPER, "references.bib")),
    ("data/solidification_macros.tex",
     os.path.join(PAPER, "data", "solidification_macros.tex")),
    ("data/solidification_table.tex",
     os.path.join(PAPER, "data", "solidification_table.tex")),
    ("figures/solidification.png",
     os.path.join(PAPER, "figures", "solidification.png")),
    ("source/solidification_experiment.py",
     os.path.join(HERE, "solidification_experiment.py")),
    ("source/solidification_data.csv",
     os.path.join(HERE, "solidification_data.csv")),
    ("source/solidification_summary.json",
     os.path.join(HERE, "solidification_summary.json")),
    ("source/solidification_figure.png",
     os.path.join(HERE, "solidification_figure.png")),
    ("source/test_solidification.py",
     os.path.join(ROOT, "tests", "test_solidification.py")),
    ("data/critical_budget_macros.tex",
     os.path.join(PAPER, "data", "critical_budget_macros.tex")),
    ("data/critical_budget_table.tex",
     os.path.join(PAPER, "data", "critical_budget_table.tex")),
    ("data/critical_budget_detector_table.tex",
     os.path.join(PAPER, "data", "critical_budget_detector_table.tex")),
    ("figures/critical_budget.png",
     os.path.join(PAPER, "figures", "critical_budget.png")),
    ("source/critical_budget_experiment.py",
     os.path.join(HERE, "critical_budget_experiment.py")),
    ("source/solidification_theory.md",
     os.path.join(HERE, "solidification_theory.md")),
    ("source/critical_budget_data.csv",
     os.path.join(HERE, "critical_budget_data.csv")),
    ("source/critical_budget_data_reach.csv",
     os.path.join(HERE, "critical_budget_data_reach.csv")),
    ("source/critical_budget_summary.json",
     os.path.join(HERE, "critical_budget_summary.json")),
    ("source/critical_budget_figure.png",
     os.path.join(HERE, "critical_budget_figure.png")),
    ("source/test_critical_budget.py",
     os.path.join(ROOT, "tests", "test_critical_budget.py")),
    ("data/nested_macros.tex",
     os.path.join(PAPER, "data", "nested_macros.tex")),
    ("data/nested_table.tex",
     os.path.join(PAPER, "data", "nested_table.tex")),
    ("figures/nested.png",
     os.path.join(PAPER, "figures", "nested.png")),
    ("source/nested_solidification_experiment.py",
     os.path.join(HERE, "nested_solidification_experiment.py")),
    ("source/nested_data.csv",
     os.path.join(HERE, "nested_data.csv")),
    ("source/nested_summary.json",
     os.path.join(HERE, "nested_summary.json")),
    ("source/nested_figure.png",
     os.path.join(HERE, "nested_figure.png")),
    ("source/test_nested_solidification.py",
     os.path.join(ROOT, "tests", "test_nested_solidification.py")),
    ("data/self_audit_macros.tex",
     os.path.join(PAPER, "data", "self_audit_macros.tex")),
    ("data/self_audit_table.tex",
     os.path.join(PAPER, "data", "self_audit_table.tex")),
    ("source/paper_self_audit.py",
     os.path.join(HERE, "paper_self_audit.py")),
    ("source/test_paper_self_audit.py",
     os.path.join(ROOT, "tests", "test_paper_self_audit.py")),
    ("PAPER_AUDIT_REPORT.md",
     os.path.join(HERE, "PAPER_AUDIT_REPORT.md")),
    ("CITATION_AUDIT_REPORT.md",
     os.path.join(HERE, "CITATION_AUDIT_REPORT.md")),
    ("LITERATURE_AUDIT.md",
     os.path.join(PAPER, "LITERATURE_AUDIT.md")),
    ("data/literature_search.json",
     os.path.join(PAPER, "data", "literature_search.json")),
    ("source/literature_search.py",
     os.path.join(HERE, "literature_search.py")),
    ("data/citation_audit_macros.tex",
     os.path.join(PAPER, "data", "citation_audit_macros.tex")),
    ("data/citation_audit_table.tex",
     os.path.join(PAPER, "data", "citation_audit_table.tex")),
    ("data/references_pre_audit.bib",
     os.path.join(PAPER, "data", "references_pre_audit.bib")),
    ("data/PRE_AUDIT_NOTE.txt",
     os.path.join(PAPER, "data", "PRE_AUDIT_NOTE.txt")),
    ("source/citation_audit_report.py",
     os.path.join(HERE, "citation_audit_report.py")),
    ("source/citation_audit_results.json",
     os.path.join(HERE, "citation_audit_results.json")),
    ("source/test_citation_audit.py",
     os.path.join(ROOT, "tests", "test_citation_audit.py")),
]


def _mkdirs(root: str, relative: str) -> None:
    parts = relative.split("/")[:-1]
    path = root
    for part in parts:
        path = os.path.join(path, part)
        os.makedirs(path, exist_ok=True)


def _add(zf: zipfile.ZipFile, name: str, data: bytes,
         directory: bool = False) -> None:
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = (
        (stat.S_IFDIR | 0o40755) if directory else (stat.S_IFREG | 0o100644)
    ) << 16
    zf.writestr(info, data)


def main() -> int:
    if not os.path.exists(PREVIOUS):
        raise SystemExit("previous package not found: {}".format(PREVIOUS))
    if not os.path.exists(os.path.join(PAPER, "main.pdf")):
        raise SystemExit("build the paper before packaging")

    with tempfile.TemporaryDirectory() as staging:
        root = os.path.join(staging, TOP)
        os.makedirs(root)
        with zipfile.ZipFile(PREVIOUS) as archive:
            archive.extractall(staging)
        for relative, source in OVERLAY:
            _mkdirs(root, relative)
            destination = os.path.join(root, *relative.split("/"))
            shutil.copyfile(source, destination)

        entries = []
        for folder, dirs, files in os.walk(root):
            dirs.sort()
            relative = os.path.relpath(folder, staging).replace(os.sep, "/")
            if relative != TOP:
                entries.append(("".join([relative, "/"]), None, True))
            for name in sorted(files):
                full = os.path.join(folder, name)
                with open(full, "rb") as handle:
                    entries.append(
                        (os.path.relpath(full, staging).replace(os.sep, "/"),
                         handle.read(), False)
                    )

        os.makedirs(DATED, exist_ok=True)
        with zipfile.ZipFile(TARGET, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data, is_dir in entries:
                _add(archive, name, b"" if is_dir else data, is_dir)

    shutil.copyfile(TARGET, os.path.join(DATED, "02_fractal_interface_paper.zip"))
    shutil.copyfile(os.path.join(PAPER, "main.pdf"),
                    os.path.join(DATED, "01_Fractal_Interfaces_main.pdf"))

    print("wrote {}".format(TARGET))
    print("wrote {}".format(os.path.join(DATED, "02_fractal_interface_paper.zip")))
    print("wrote {}".format(os.path.join(DATED, "01_Fractal_Interfaces_main.pdf")))

    with zipfile.ZipFile(TARGET) as archive:
        bad = archive.testzip()
        names = archive.namelist()
    print("entries {}   corrupt {}".format(len(names), bad))
    required = [
        "fractal_interface_paper/data/solidification_macros.tex",
        "fractal_interface_paper/data/solidification_table.tex",
        "fractal_interface_paper/figures/solidification.png",
        "fractal_interface_paper/source/solidification_experiment.py",
        "fractal_interface_paper/source/solidification_data.csv",
        "fractal_interface_paper/source/solidification_summary.json",
        "fractal_interface_paper/source/solidification_figure.png",
        "fractal_interface_paper/main.pdf",
        "fractal_interface_paper/data/citation_audit_macros.tex",
        "fractal_interface_paper/data/citation_audit_table.tex",
        "fractal_interface_paper/source/citation_audit_results.json",
        "fractal_interface_paper/CITATION_AUDIT_REPORT.md",
        "fractal_interface_paper/LITERATURE_AUDIT.md",
        "fractal_interface_paper/data/literature_search.json",
        "fractal_interface_paper/source/literature_search.py",
    ]
    missing = [name for name in required if name not in names]
    if missing:
        raise SystemExit("missing from package: {}".format(missing))
    print("all required entries present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
