#!/usr/bin/env python3
"""A game-theoretic simulation of the name / energy-gradient paradox.

The paradox, stated the way the architect states it:

    1. no name, so no interface with the economy;
    2. with no name you cannot take an energy gradient;
    3. you must keep a moat;
    4. the only apparent way out is to build the whole loop
       (research -> product -> industry -> consumer) alone.

This script does not try to win that argument. It writes the argument down as a
dynamical system, runs it, and reports where the conclusion depends on a number
nobody has measured yet. That is the only useful thing a toy model can do: not
"this strategy wins", but "this strategy wins if this number is above X, and
here is the test that measures X".

Units and the three state variables
-----------------------------------
Normalised against one well-resourced incumbent laboratory at ``t = 0``:
``E = 1``, ``M = 1``, ``D = 1``. A strategy for a single researcher therefore
*succeeds* by becoming comparable to an institution, not by maxing a score.

``E`` -- energy gradient. Capital + revenue + prestige: the thing that lets you
run experiments, hire, and be invited. Sustained two ways, and the distinction
carries the whole result:

  * from *fame* (citeations, stars, talks, grants) -- saturates at ``ATTN_CAP``,
    because no amount of fame buys a laboratory;
  * from *dependency* (licences, certification fees, retainers) -- saturates at
    ``E_CAP``, because a paying installed base does scale.

``M`` -- moat. The fraction of the core asset a competitor cannot reproduce on
the day you reveal it. ``1 - M`` is what is available to copy.

``D`` -- dependency. The fraction of your users whose next action gets more
expensive if you disappear. Not popularity: a forkable library can have a
million users and ``D = 0``.

The quantity the architect calls a potential difference is their product::

    psi(t) = E(t) * D(t)

which is potential energy in the only sense that matters here -- energy held at
a height. A researcher with prestige and no dependents has ``psi = 0``. That is
the paradox in one line, and it is why citations are not the answer.

Why copying is modelled with two knobs, not one
-----------------------------------------------
``exposure`` is how much of the core you reveal; ``copy_ease`` is how much of
what is revealed a big lab can re-ship. They multiply, and they differ by what
is being revealed:

* code: exposure 0.95, copy_ease 0.90 -- a fork is one `git clone`;
* a formula in a paper: exposure 0.45, copy_ease 0.35 -- two engineers, a quarter;
* a standard: exposure 0.35, copy_ease 0.12 -- you can copy the text and still
  not be the one whose signature makes it admissible;
* a liability transfer (certification, audit, insurance): copy_ease -> 0.

So the resolution the model looks for is not "open less". It is "open the layer
whose copy_ease is 1 and charge for the layer whose copy_ease is 0" -- two
knobs turned in opposite directions, not one knob turned halfway.

Run::

    python3 experiments/game_theory_sim.py               # full report
    python3 experiments/game_theory_sim.py --draws 2000  # faster Monte Carlo
    python3 experiments/game_theory_sim.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import unicodedata
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

HERE = Path(__file__).resolve().parent
OUT = HERE / "game_theory_results.json"

# ---------------------------------------------------------------- constants ---
MONTHS = 60

#: Energy obtainable from fame alone. A famous professor commands a few million
#: and attention; that is not a laboratory, so it cannot buy laboratory work.
ATTN_CAP = 2.5

#: Energy obtainable with a paying installed base.
E_CAP = 8.0

#: Dependency rebuilds the moat (installed base, accumulated audit data, the cost
#: of re-certifying elsewhere).
REBUILD = 0.06

#: Ideas age on their own, even with no competitor.
DECAY_NAT = 0.015

#: Cost of existing, per month: rent, compute, food. 2% of a laboratory budget.
BURN = 0.02

#: Starting state: some savings, a fresh idea, no dependents.
E0 = 0.30       # ~15 months of runway
M0 = 0.85
D0 = 0.00

DISCOUNT = 0.15 / 12.0
ENTRENCH_D = 0.35


@dataclass(frozen=True)
class Params:
    """One strategy's parameters.

    Every number below is *declared*, not measured. The provenance is stated so a
    reader can attack the right one. ``lead_months`` is the only parameter
    anchored to something this repository has actually done, and it points the
    other way from what a pessimist would guess: the gateway and the witness
    already exist and have run 100-episode audits, so the toll strategy's lead is
    short *because the work behind it is already finished*.
    """

    key: str
    label: str
    attention: float       # monthly rate at which new people notice you
    reach_cap: float       # logistic ceiling on cumulative reach
    conv_attention: float  # energy per unit of standing reach, per month
    conv_dependency: float # energy per unit of dependency, per month
    exposure: float        # fraction of the core asset revealed
    copy_ease: float       # fraction of the revealed part a big lab can re-ship
    dep_convert: float     # share of new adoption that becomes irreplaceable
    churn: float           # monthly loss of dependency
    lead_months: float     # months before the paid/serving interface exists
    switch_months: Optional[float] = None
    phase_two: Optional["Params"] = None

    def at(self, t: float) -> "Params":
        if self.phase_two is None or self.switch_months is None:
            return self
        return self.phase_two if t >= self.switch_months else self

    @property
    def core(self) -> "Params":
        """The parameters that govern the long run (phase two, if any)."""
        return self.phase_two if self.phase_two is not None else self


CLOSED = Params(
    key="closed",
    label="1 全封闭（闭源 + 自建 C 端）",
    attention=0.03,
    reach_cap=0.60,
    conv_attention=0.01,   # nobody funds a project they cannot see
    conv_dependency=0.35,
    exposure=0.05,
    copy_ease=0.60,
    dep_convert=0.30,
    churn=0.10,
    lead_months=18.0,   # you must build the whole loop alone
)

OPEN = Params(
    key="open",
    label="2 全开源（代码与公式全放 GitHub）",
    attention=0.60,
    reach_cap=8.00,
    conv_attention=0.04,   # stars are not revenue
    conv_dependency=0.08,
    exposure=0.95,
    copy_ease=0.90,        # a fork is one git clone
    dep_convert=0.03,      # forkable => substitutable => not a dependent
    churn=0.35,
    lead_months=1.0,
)

ACADEMIC = Params(
    key="academic",
    label="3 学术卡位（只发顶会 + 特邀报告）",
    attention=0.25,
    reach_cap=2.00,
    conv_attention=0.12,   # grants, invitations, offers convert well
    conv_dependency=0.10,
    exposure=0.45,         # the method is in the paper
    copy_ease=0.35,        # two engineers, one quarter
    dep_convert=0.08,      # a reader can cite you and never need you
    churn=0.08,
    lead_months=3.0,
)

TOLL = Params(
    key="toll",
    label="4 接口收费站（Open-Core + 认证标准）",
    attention=0.45,
    reach_cap=6.00,
    conv_attention=0.05,
    conv_dependency=0.14,  # licence + certification fee
    exposure=0.35,         # the primitive is open, the registry and gate are not
    copy_ease=0.12,        # parity from public info ~= 1/(0.12*0.35) = 24 months
    dep_convert=0.55,      # re-certification is the switching cost
    churn=0.012,           # annual re-certification, low voluntary churn
    lead_months=4.0,       # the gateway exists and has been audited already
)

HYBRID = replace(
    ACADEMIC,
    key="hybrid",
    label="5 学术卡位 -> 接口收费站（相序）",
    switch_months=6.0,
    phase_two=TOLL,
)

STRATEGIES: List[Params] = [CLOSED, OPEN, ACADEMIC, TOLL, HYBRID]
PARAMS_BY_KEY: Dict[str, Params] = {p.key: p for p in STRATEGIES}


# ------------------------------------------------------------------- engine ---
@dataclass
class Run:
    key: str
    label: str
    E: List[float]
    M: List[float]
    D: List[float]
    psi: List[float]
    inflow: List[float]
    copy_rate: List[float]
    reach: List[float]
    alive: bool
    death_month: Optional[int]
    t_cash: Optional[int]      # inflow covers burn
    t_cross: Optional[int]     # moat stops falling
    t_entrench: Optional[int]  # D first exceeds the entrenchment line
    npv: float

    def at(self, month: int) -> Tuple[float, float, float, float]:
        i = min(month, len(self.E) - 1)
        return self.E[i], self.M[i], self.D[i], self.psi[i]

    @property
    def copyable_share(self) -> float:
        p = PARAMS_BY_KEY[self.key].core
        return p.exposure * p.copy_ease

    def parity_months(self, month: int) -> Optional[float]:
        """Months for the current *net* erosion rate to consume the current moat.

        ``None`` means the moat sits at or above its equilibrium height, i.e. a
        copyist never catches up at the present rate. Reporting this as ``inf``
        would hide which side of equilibrium the strategy is on, so it is
        reported as "no catch-up" instead.
        """
        i = min(month, len(self.M) - 1)
        p = PARAMS_BY_KEY[self.key].at(month)
        m, d, r = self.M[i], self.D[i], self.reach[i]
        copy = p.copy_ease * p.exposure * (0.4 + 0.6 * min(1.0, r / p.reach_cap))
        net = copy * m + DECAY_NAT * m - REBUILD * d
        if net <= 0.02 * max(m, 1e-9):
            return None
        return m / net


def simulate(
    p: Params,
    *,
    months: int = MONTHS,
    jitter: Optional[Dict[str, float]] = None,
    e0: float = E0,
) -> Run:
    """One deterministic trajectory. ``jitter`` multiplies declared parameters."""
    j = jitter or {}
    E, M, D, reach = e0, M0, D0, 0.0
    npv = 0.0
    alive = True
    death: Optional[int] = None
    t_cash: Optional[int] = None
    t_cross: Optional[int] = None
    t_entrench: Optional[int] = None

    arr: Dict[str, List[float]] = {
        k: [v] for k, v in
        (("E", E), ("M", M), ("D", D), ("psi", E * D),
         ("inflow", 0.0), ("copy", 0.0), ("reach", 0.0))
    }

    for t in range(1, months + 1):
        if not alive:
            for k in arr:
                arr[k].append(arr[k][-1])
            continue

        q = p.at(t)
        q = replace(
            q,
            attention=q.attention * j.get("attention", 1.0),
            conv_attention=q.conv_attention * j.get("conv_attention", 1.0),
            conv_dependency=q.conv_dependency * j.get("conv_dependency", 1.0),
            exposure=min(1.0, q.exposure * j.get("exposure", 1.0)),
            copy_ease=min(1.0, q.copy_ease * j.get("copy_ease", 1.0)),
            dep_convert=min(1.0, q.dep_convert * j.get("dep_convert", 1.0)),
            churn=q.churn * j.get("churn", 1.0),
            lead_months=max(0.0, q.lead_months * j.get("lead_months", 1.0)),
        )

        # reach: logistic over a pool that grows with the resources you command
        # and with the field itself (0.6%/month ~ 7%/year), plus a Matthew term:
        # being known makes you known
        pool = q.reach_cap * (1.0 + 0.10 * min(E, 4.0)) * (1.0 + 0.006 * t)
        new_reach = q.attention * max(0.0, 1.0 - reach / pool) * (1.0 + 0.15 * min(E, 2.0))
        reach += new_reach

        gated = t >= q.lead_months

        # dependency: the convertible share of the pool, less churn. Normalising
        # by reach_cap keeps the units honest -- a month of adoption cannot
        # convert more than the pool it comes from.
        # renewal: a share of the installed base brings you its *next* system
        # rather than its first. This is what keeps dependency from evaporating
        # once the pool is saturated, and it scales with how sticky you are.
        convert = q.dep_convert * ((new_reach / q.reach_cap) + 0.05 * D) * (1.0 - D)
        D = D + (convert - q.churn * D if gated else -q.churn * D)
        D = min(1.0, max(0.0, D))
        if t_entrench is None and D >= ENTRENCH_D:
            t_entrench = t

        # energy: two channels with two different ceilings
        attn = q.conv_attention * reach * max(0.0, 1.0 - E / ATTN_CAP)
        dep = (q.conv_dependency * D * max(0.0, 1.0 - E / E_CAP)
               if gated else 0.0)
        inflow = attn + dep
        E = E + inflow - BURN
        if E <= 0.0:
            E = 0.0
            alive = False
            death = t
        if t_cash is None and inflow >= BURN:
            t_cash = t

        # moat: rebuilt by dependency, eroded by age and by copying
        copy = q.copy_ease * q.exposure * (0.4 + 0.6 * min(1.0, reach / q.reach_cap))
        dM = REBUILD * D - DECAY_NAT * M - copy * M
        if t_cross is None and dM >= 0.0 and gated:
            t_cross = t
        M = min(1.0, max(0.0, M + dM))

        npv += math.exp(-DISCOUNT * t) * (inflow - BURN)

        arr["E"].append(E)
        arr["M"].append(M)
        arr["D"].append(D)
        arr["psi"].append(E * D)
        arr["inflow"].append(inflow)
        arr["copy"].append(copy)
        arr["reach"].append(reach)

    return Run(
        key=p.key, label=p.label,
        E=arr["E"], M=arr["M"], D=arr["D"], psi=arr["psi"],
        inflow=arr["inflow"], copy_rate=arr["copy"], reach=arr["reach"],
        alive=alive, death_month=death, t_cash=t_cash, t_cross=t_cross,
        t_entrench=t_entrench, npv=npv,
    )


# ---------------------------------------------------------------- monte carlo ---
JITTER = {
    "attention": 0.45,
    "conv_attention": 0.50,
    "conv_dependency": 0.50,
    "exposure": 0.20,
    "copy_ease": 0.35,
    "dep_convert": 0.55,
    "churn": 0.35,
    "lead_months": 0.45,
}

#: Success is not a vibe. It is all three at month 60: energy comparable to an
#: institution, a moat a competitor cannot walk through, and a base that would
#: notice if you vanished.
SUCCESS = {"E": 1.0, "M": 0.40, "D": ENTRENCH_D}


def monte_carlo(p: Params, draws: int, seed: int = 20261105) -> Dict[str, object]:
    rng = random.Random(seed)
    successes = deaths = 0
    psi60: List[float] = []
    e60: List[float] = []
    npvs: List[float] = []
    for _ in range(draws):
        jitter = {n: math.exp(rng.gauss(0.0, s)) for n, s in JITTER.items()}
        run = simulate(p, jitter=jitter)
        e, m, d, _ = run.at(MONTHS)
        psi60.append(e * d)
        e60.append(e)
        npvs.append(run.npv)
        deaths += 0 if run.alive else 1
        if e >= SUCCESS["E"] and m >= SUCCESS["M"] and d >= SUCCESS["D"]:
            successes += 1

    def pct(xs: Sequence[float], q: float) -> float:
        s = sorted(xs)
        i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return s[i]

    return {
        "key": p.key,
        "p_success": successes / draws,
        "p_death": deaths / draws,
        "psi60_mean": sum(psi60) / draws,
        "psi60_p10": pct(psi60, 0.10),
        "psi60_p90": pct(psi60, 0.90),
        "e60_mean": sum(e60) / draws,
        "npv_mean": sum(npvs) / draws,
        "npv_p10": pct(npvs, 0.10),
        "draws": draws,
    }


# ------------------------------------------------------------------ printing ---
def _w(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, align: str = "l") -> str:
    gap = max(0, width - _w(text))
    if align == "r":
        return " " * gap + text
    if align == "c":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def table(headers: Sequence[str], rows: Sequence[Sequence[str]], aligns: Optional[str] = None) -> str:
    aligns = aligns or "l" * len(headers)
    widths = [_w(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _w(cell))
    out = ["  ".join(_pad(h, widths[i], "c") for i, h in enumerate(headers)),
           "  ".join("-" * widths[i] for i in range(len(headers)))]
    for row in rows:
        out.append("  ".join(_pad(c, widths[i], aligns[i]) for i, c in enumerate(row)))
    return "\n".join(out)


def fnum(x: Optional[float], digits: int = 2) -> str:
    if x is None:
        return "—"
    if abs(x) >= 1e4:
        return f"{x:.3g}"
    return f"{x:.{digits}f}"


# -------------------------------------------------------------- sensitivity ---
SHORT = {"closed": "b", "open": "s", "academic": "a", "toll": "t", "hybrid": "h"}


def ranking_grid(dep_scales: Sequence[float], leads: Sequence[float]) -> List[List[str]]:
    """Who wins the 6-month potential difference as certification slips."""
    grid = []
    for lead in leads:
        row = []
        for scale in dep_scales:
            best, best_psi = "-", -1.0
            for p in STRATEGIES:
                task = p
                if p.phase_two is not None:
                    q2 = replace(p.phase_two, dep_convert=p.phase_two.dep_convert * scale,
                                 lead_months=lead)
                    task = replace(p, phase_two=q2)
                elif p.key == "toll":
                    task = replace(p, dep_convert=p.dep_convert * scale, lead_months=lead)
                psi = simulate(task).at(6)[3]
                if psi > best_psi:
                    best, best_psi = SHORT[p.key], psi
            row.append(best)
        grid.append(row)
    return grid


def moat_grid(copy_eases: Sequence[float], dep_scales: Sequence[float]) -> List[List[str]]:
    """Moat at year five for the toll strategy: where does it stop holding?"""
    grid = []
    for ce in copy_eases:
        row = []
        for scale in dep_scales:
            q = replace(TOLL, copy_ease=ce, dep_convert=TOLL.dep_convert * scale)
            run = simulate(q)
            m = run.at(60)[1]
            row.append(f"{m:.2f}" + ("*" if m >= SUCCESS["M"] else " "))
        grid.append(row)
    return grid


# --------------------------------------------------------------------- main ---
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="energy-gradient paradox simulation")
    ap.add_argument("--draws", type=int, default=6000)
    ap.add_argument("--json", type=Path, default=OUT)
    args = ap.parse_args(argv)

    runs = [simulate(p) for p in STRATEGIES]
    mc = {p.key: monte_carlo(p, args.draws) for p in STRATEGIES}
    by_key = {r.key: r for r in runs}

    print("=" * 100)
    print("  名声 / 能量梯度悖论 —— 四战略博弈推演")
    print("=" * 100)
    print()
    print("单位：以一家资源充足的既有实验室 t=0 为 1.0（E=1, M=1, D=1）。")
    print(f"起点：独立研究者 E0={E0:.2f}（约 {E0 / BURN:.0f} 个月 runway）、M0={M0:.2f}、D0={D0:.2f}。")
    print("势差 ψ = E·D（能量 × 高度）。能量有两个上限：")
    print(f"  靠名气流入的部分封顶 {ATTN_CAP}（声望买不到实验室），靠依赖流入的部分封顶 {E_CAP}。")
    print()

    print("【假设参数】（全部为声明值，不是测量值；§5 检验它们是否承载结论）")
    rows = []
    for p in STRATEGIES:
        q = p.core
        lo = p
        rows.append([
            p.label, fnum(lo.attention), fnum(lo.exposure),
            fnum(q.copy_ease), fnum(lo.dep_convert), fnum(q.dep_convert) if p.phase_two else "—",
            fnum(lo.lead_months, 0),
        ])
    print(table(["战略", "注目率", "暴露度", "可抄性", "依赖转化(一期)", "依赖转化(二期)", "前置月数"],
                rows, aligns="lrrrrrr"))
    print()

    print("【§1 轨迹：1 / 3 / 5 年】")
    rows = []
    for r in runs:
        for month, tag in ((12, "1 年"), (36, "3 年"), (60, "5 年")):
            e, m, d, psi = r.at(month)
            par = r.parity_months(month)
            rows.append([
                r.label, tag, fnum(e), fnum(m), fnum(d), fnum(psi),
                fnum(100.0 * r.copy_rate[min(month, len(r.copy_rate) - 1)], 1),
                "已到均衡" if par is None else fnum(par, 0),
            ])
    print(table(["战略", "时点", "能量 E", "护城河 M", "依赖 D", "势差 ψ",
                 "抄袭压力 %/月", "追平月数"], rows, aligns="llrrrrrr"))
    print("  已出局战略的后续行显示的是出局当月的冻结值，不是它复活了。")
    print()

    print("【§2 交叉点与收支】")
    rows = []
    for r in runs:
        rows.append([
            r.label,
            "存活" if r.alive else f"第 {r.death_month} 月出局",
            "—" if r.t_cash is None else f"第 {r.t_cash} 月",
            "—" if r.t_cross is None else f"第 {r.t_cross} 月",
            "—" if r.t_entrench is None else f"第 {r.t_entrench} 月",
            f"{100.0 * r.copyable_share:.0f}%",
            fnum(r.npv, 2),
        ])
    print(table(["战略", "结局", "收支平衡", "E-M 交叉点", "依赖确立", "可抄比例", "净现值"],
                rows, aligns="llllllr"))
    print("  E-M 交叉点 = 护城河停止失血的月份（依赖重建 >= 岁月 + 抄袭侵蚀）。")
    print("  “追平月数”= 当前净侵蚀率吃光护城河的时间；“已到均衡”表示护城河稳在均衡高度。")
    print()

    print("【§3 蒙特卡洛：成功概率与期望收益】")
    print(f"  成功：第 60 月同时 E≥{SUCCESS['E']}、M≥{SUCCESS['M']}、D≥{SUCCESS['D']}；"
          f"{args.draws} 次对数正态扰动。")
    rows = []
    for p in STRATEGIES:
        m = mc[p.key]
        rows.append([
            p.label, f"{100.0 * m['p_success']:.1f}%", f"{100.0 * m['p_death']:.1f}%",
            fnum(m["psi60_mean"]), fnum(m["psi60_p10"]), fnum(m["psi60_p90"]),
            fnum(m["npv_mean"]), fnum(m["npv_p10"]),
        ])
    print(table(["战略", "P(成功)", "P(出局)", "E[ψ60]", "ψ60 p10", "ψ60 p90", "E[NPV]", "NPV p10"],
                rows, aligns="lrrrrrrr"))
    print()

    print("【§4 六个月势差：谁最先建立势差】")
    rows = []
    for r in runs:
        e6, _, d6, psi6 = r.at(6)
        rows.append([r.label, fnum(e6), fnum(d6), fnum(psi6),
                     f"{psi6 / max(by_key['toll'].at(6)[3], 1e-9):.2f}×",
                     f"{100.0 * r.copyable_share:.0f}%"])
    print(table(["战略", "E(6)", "D(6)", "ψ(6)", "对收费站", "可抄比例"], rows, aligns="lrrrrr"))
    print()

    print("【§5 敏感性：结论挂在哪两个旋钮上】")
    dep_scales = (0.15, 0.30, 0.45, 0.60, 0.80, 1.00)
    leads = (2.0, 4.0, 6.0, 9.0, 12.0)

    print("  (a) 6 个月势差冠军：认证依赖转化率倍数 × 认证试点上线月数")
    print("      b=全封闭  s=全开源  a=学术卡位  t=收费站  h=相序")
    grid = ranking_grid(dep_scales, leads)
    print(table(["lead↓ / dep→"] + [f"{s:.2f}" for s in dep_scales],
                [[f"{lead:.0f} 月"] + row for lead, row in zip(leads, grid)],
                aligns="l" + "c" * len(dep_scales)))
    winners = {c for row in grid for c in row}
    print(f"  翻转区：出现过的赢家 = {', '.join(sorted(winners))}"
          f"{'（结论依赖参数，可证伪）' if len(winners) >= 2 else '（结论不敏感 —— 警惕）'}")
    print()

    print("  (b) 收费站护城河在第 60 月的值：core 可抄性 × 依赖转化率倍数")
    print("      *=护城河仍达标（M≥0.40）")
    ces = (0.05, 0.08, 0.12, 0.18, 0.25, 0.35, 0.50)
    mg = moat_grid(ces, dep_scales)
    print(table(["copy_ease↓ / dep→"] + [f"{s:.2f}" for s in dep_scales],
                [[f"{ce:.2f}"] + row for ce, row in zip(ces, mg)],
                aligns="l" + "c" * len(dep_scales)))
    print()

    print("  (c) 全封闭在什么条件下才可能不死？")
    for runway, lead in ((15, 18), (30, 18), (30, 12), (48, 18), (48, 9)):
        run = simulate(replace(CLOSED, lead_months=lead), e0=runway * BURN)
        e60, m60, d60, psi60 = run.at(60)
        print(f"    runway {runway:>2} 月, lead {lead:>2} 月 -> "
              f"{'存活' if run.alive else f'第 {run.death_month:>2} 月出局'}，"
              f"E(60)={fnum(e60)}, M(60)={fnum(m60)}, ψ(60)={fnum(psi60)}")
    print()

    # Two stress runs, so the conditional in Q2 is a computed number, not a claim.
    stress_ce = monte_carlo(replace(TOLL, copy_ease=0.18), 2000)["p_success"]
    stress_dep = monte_carlo(replace(TOLL, dep_convert=0.30), 2000)["p_success"]

    print("=" * 100)
    print("【§6 结论】")
    print("=" * 100)
    t, a, o, c, h = (by_key[k] for k in ("toll", "academic", "open", "closed", "hybrid"))
    print()
    print("Q1 四战略在 1 / 3 / 5 年的能量梯度与护城河衰减交叉点：")
    print()
    print(f"  · 收费站：依赖第 {t.t_entrench} 月确立，护城河第 {t.t_cross} 月止跌；"
          f"ψ 0 → {fnum(t.at(12)[3])} → {fnum(t.at(36)[3])} → {fnum(t.at(60)[3])}。")
    print(f"    E/M/D 五年后为 {fnum(t.at(60)[0])}/{fnum(t.at(60)[1])}/{fnum(t.at(60)[2])}，"
          f"追平时间 {fnum(t.parity_months(60), 0) if t.parity_months(60) else '∞'}。")
    print(f"  · 学术卡位：E(12)={fnum(a.at(12)[0])} 已经超过 1 个实验室，"
          f"但 D 只有 {fnum(a.at(60)[2])}，ψ(12)={fnum(a.at(12)[3])}、ψ(60)={fnum(a.at(60)[3])}。")
    print(f"    护城河在 5 年后为 {fnum(a.at(60)[1])}——论文把方法送出去，没有东西把它买回来。")
    print(f"  · 全开源：第 {o.t_cash} 月就收支平衡（最快），但 M 在 1 年内归零，"
          f"D={fnum(o.at(60)[2])}，"
          f"{'全程存活但无护城河' if o.death_month is None else f'第 {o.death_month} 月出局'}。")
    print(f"    这是“先红后穷”的定量版本：名气把 E 推到 {fnum(max(o.E))}，却让 ψ 停在 "
          f"{fnum(max(o.psi))} 附近。")
    print(f"  · 全封闭：{'存活' if c.alive else f'第 {c.death_month} 月出局'}。"
          f"前置期 {PARAMS_BY_KEY['closed'].lead_months:.0f} 月 > runway {E0 / BURN:.0f} 月，"
          f"它在模型里根本没走到产品。")
    print(f"  · 相序（学术→收费站）：ψ60={fnum(h.at(60)[3])}，"
          f"是纯收费站的 {h.at(60)[3] / max(t.at(60)[3], 1e-9):.2f}×；"
          f"第 6 月切换时的 E={fnum(h.at(6)[0])}、reach={fnum(h.reach[6])} 全部被继承。")
    print()
    print("Q2 单兵在 6 个月内建立势差、且核心被抄概率最低的选择：")
    best6 = max(runs, key=lambda r: r.at(6)[3])
    low_copy = min(runs, key=lambda r: r.copyable_share)
    alive_runs = [r for r in runs if r.alive]
    low_alive = min(alive_runs, key=lambda r: r.copyable_share)
    print(f"  · 6 个月 ψ 最高：{best6.label}（ψ6={fnum(best6.at(6)[3])}，"
          f"可抄 {100.0 * best6.copyable_share:.0f}%）。")
    print(f"  · 绝对可抄比例最低：{low_copy.label}（{100.0 * low_copy.copyable_share:.0f}%），"
          f"但它没活到第 60 月；存活战略里最低的是 {low_alive.label}"
          f"（{100.0 * low_alive.copyable_share:.0f}%）。")
    print("  · 两者不是同一个战略——这正是悖论。模型的答案是：")
    print("    **用 copy_ease≈1 的层去买注目率，用 copy_ease≈0.1 的层去收租。**")
    print("    开源的部分必须是“别人拿到也不削弱你”的商品层；收费的部分必须是")
    print("    “别人拿到代码也复制不了”的资质层。这不是折中，是把暴露度和可抄性")
    print("    拆成两个旋钮，朝相反方向拧到底。")
    print()
    print(f"  · 先修正量级：6 个月时最好的 ψ 只有 {fnum(best6.at(6)[3])}，"
          f"约一家既有实验室的 {100.0 * best6.at(6)[3]:.0f}%。")
    print("    没有任何战略在 6 个月内「建立了」势差。6 个月能建立的是**斜率与结构**——")
    print(f"    10% 量级的依赖 + 4% 的可抄面，而不是存量。把 6 个月当成终点，会误判。")
    print()
    print("  · 但这条推荐有条件，模型把它暴露得很干脆（§5a/5b）：")
    print("    若认证依赖转化率掉到 0.30（约声明值的 55%），P(成功) 从 "
          f"{100.0 * mc['toll']['p_success']:.1f}% 掉到 {100.0 * stress_dep:.1f}%；")
    print("    若把试点拖到 9 个月以后，6 个月势差冠军直接翻转为“学术卡位”；")
    print("    若 core 可抄性升到 0.18（护城河的 5 年值跌破 0.40），P(成功) 掉到 "
          f"{100.0 * stress_ce:.1f}%。")
    print("    也就是说——**推荐的成立条件不是“选对战略”，而是 30 天内启动认证试点谈判，")
    print("    并且让认证本身成为不可复制的负债转移，而不是一份可下载的 PDF。**")
    print()
    print("Q3 见 §7 的 30 天优先级。")
    print()

    print("=" * 100)
    print("【§7 未来 30 天：按模型杠杆排序】")
    print("=" * 100)
    print()
    print("  30 天短于任何时间常数，所以这 30 天不是“取得结果”，是“设置参数”。")
    print("  每一项都绑定它推动的那个参数，以及 30 天内可观测的代理指标。")
    print()
    plan = [
        ("D1–D3", "《分形接口理论》+《成本降低理论》作为标准提案发预印本，不附代码",
         "attention↑，exposure 停在 0.45", "一个可引用的 arXiv/SSRN 编号"),
        ("D4–D8", "开源四值判词基础库 + 见证原语，保留 Registry 与 CEL 网关",
         "reach↑，copy_ease 保持 0.12", "首周 star/fork + 一条“拿去用了”的 issue"),
        ("D9–D14", "写《AI 物理安全认证规范 v0.1》+ 第三方可跑的合规测试套件",
         "**dep_convert**（模型第一杠杆）", "规范文档 + 一条 pass/fail 测试命令"),
        ("D15–D22", "谈 3 个认证试点：1 高校实验室、1 初创、1 工业客户",
         "**lead_months↓**（4 → 2）", "3 份 LOI 或排期确认"),
        ("D23–D27", "定价页 + 一份“供应商未认证时你的责任边界”合规备忘",
         "conv_dependency↑，churn↓", "首个报价被询价，而不是被已读"),
        ("D28–D30", "把 §5a/5b 的两个旋钮做成仪表盘：试点数 / 认证周期",
         "模型的可证伪接口", "一张每周更新的两行表"),
    ]
    print(table(["窗口", "动作", "推动的参数", "30 天代理指标"], plan, aligns="llll"))
    print()
    print("  为什么不是先做产品：模型里全封闭的死因不是“不够聪明”，是 lead_months(18)")
    print("  超过了 runway(15)。单兵做全闭环，是用现金流买一个 18 个月后才开张的赌注。")
    print("  为什么不是只发论文：学术卡位的 E 不低，但 D 停在 0.0x；ψ=E·D 的第二个因子")
    print("  是零，再多的声望也乘不出势差。论文是必要条件（买注目率），不是充分条件。")
    print()

    print("=" * 100)
    print("【§8 内部一致性检查】")
    print("=" * 100)
    checks: List[Tuple[str, bool]] = []

    run = simulate(TOLL)
    expected = E0 + sum(run.inflow) - BURN * MONTHS
    checks.append(("能量守恒：E(60) = E0 + Σinflow − Σburn",
                   abs(run.E[60] - expected) < 1e-9))
    checks.append(("势差定义：ψ ≡ E·D 逐月成立",
                   all(abs(run.psi[i] - run.E[i] * run.D[i]) < 1e-12 for i in range(len(run.E)))))
    weak = simulate(replace(TOLL, copy_ease=0.05))
    strong = simulate(replace(TOLL, copy_ease=0.60))
    checks.append(("单调性：copy_ease 升高不抬高 M(60)", strong.M[60] <= weak.M[60] + 1e-12))
    lo = simulate(replace(TOLL, dep_convert=0.10))
    hi = simulate(replace(TOLL, dep_convert=0.90))
    checks.append(("单调性：dep_convert 升高不压低 D(60)", hi.D[60] >= lo.D[60] - 1e-12))
    checks.append(("可复现：同参数两次运行逐位相同", simulate(OPEN).psi == simulate(OPEN).psi))
    ok = all(math.isfinite(v) and -1e-12 <= v <= E_CAP + 1.0
             for r in runs for v in r.E + r.M + r.D + r.psi)
    checks.append(("有界性：所有状态有限且落在模型范围内", ok))
    checks.append(("可证伪：§5a 网格里至少两个不同赢家", len(winners) >= 2))
    poor = simulate(CLOSED, e0=15 * BURN)
    rich = simulate(CLOSED, e0=48 * BURN)
    checks.append(("因果：runway 15→48 月，全封闭出局被推后或不再出局",
                   rich.death_month is None or (poor.death_month is not None
                                                and rich.death_month > poor.death_month)))
    checks.append(("交叉验证：§5b 中 M(60) 对 copy_ease 单调不增",
                   all(float(mg[i][j].rstrip("* ")) >= float(mg[i + 1][j].rstrip("* "))
                       for i in range(len(mg) - 1) for j in range(len(mg[0])))))
    print(table(["结果", "检查"], [[("PASS" if k else "FAIL"), n] for n, k in checks], aligns="ll"))
    print()

    payload = {
        "model": {
            "months": MONTHS, "E0": E0, "M0": M0, "D0": D0,
            "burn": BURN, "attn_cap": ATTN_CAP, "e_cap": E_CAP,
            "rebuild": REBUILD, "decay_nat": DECAY_NAT,
            "entrench_D": ENTRENCH_D, "success": SUCCESS,
            "jitter": JITTER, "draws": args.draws,
        },
        "strategies": [asdict(p) for p in STRATEGIES],
        "runs": {r.key: {"E": r.E, "M": r.M, "D": r.D, "psi": r.psi,
                         "alive": r.alive, "death_month": r.death_month,
                         "t_cash": r.t_cash, "t_cross": r.t_cross,
                         "t_entrench": r.t_entrench, "npv": r.npv,
                         "copyable_share": r.copyable_share}
                 for r in runs},
        "monte_carlo": mc,
        "sensitivity": {"dep_scales": list(dep_scales), "lead_months": list(leads),
                        "winner_grid": grid, "copy_ease": list(ces), "moat_grid": mg},
        "checks": {name: ok for name, ok in checks},
    }
    args.json.write_text(json.dumps(payload, indent=1) + "\n")
    try:
        shown = args.json.relative_to(HERE.parent)
    except ValueError:
        shown = args.json
    print(f"结果已写入 {shown}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
