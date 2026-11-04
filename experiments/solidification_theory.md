# The Dynamics of Solidification, and the Critical Budget

**Status:** derivation. Every number in the tables below is produced by
`experiments/critical_budget_experiment.py`, not typed by hand.

This document does three things. It writes the amortisation of a single
interface as an exact sum and takes its limit. It composes that sum with a
budget to get a *budget–depth map*, and solves for the budget at which a
compiled (solidified) chain overtakes an unlabelled one. It then composes the
map with the paper's saturating difficulty to get reach as a function of budget,
and states what that function forbids.

It also records one negative result. The brief that motivated this section
predicted that the *first* verification, being a one-off cost paid before any
depth is bought, would put the compiled arm *behind* at small budgets and create
an interior crossing $B^\* > c_v$. Under the accounting in the brief that
prediction is **false**: the unlabelled arm also pays $c_v$ for its first rung,
the two arms coincide exactly at $B=c_v$, and the compiled arm is strictly ahead
for every $B>c_v$. §3 proves this and identifies the terms that have to be
present for a non-degenerate $B^\*$ to exist — a *fixed* premium that only the
compiled arm pays, or a monitor charged on every traverse. §4 solves both in
closed form.

---

## 1. Notation

| symbol | meaning |
|---|---|
| $c_v$ | full verification of one interface (unsolidified traverse) |
| $c_c$ | replay of one compiled interface (solidified traverse) |
| $c_d$ | cost of *monitoring* a compiled interface for drift, per traverse |
| $S$ | fixed, one-off cost of the compiled regime (witness store, toolchain) |
| $\rho$ | per-traverse probability that a compiled interface goes stale |
| $p$ | $1-\rho$ |
| $B$ | budget, measured in units of $c_v$ |
| $K(B)$ | depth (number of interfaces) that $B$ buys |
| $T$ | number of traversals of one interface |

Baseline prices used throughout: $c_v=1$, $c_c=0.01$, $c_d=0.02$, $\rho=0.1$.
These are the values `solidification_experiment.py` already uses; nothing here
re-tunes them.

Two amortised rung prices appear, and keeping them apart is most of the work:

$$
\bar c \;=\; c_c + \rho\,(c_v-c_c)
\;=\; p\,c_c+(1-p)\,c_v
\qquad\text{(no monitor)},
$$

$$
\bar c_d \;=\; c_c + c_d + \rho\,(c_v-c_c)
\qquad\text{(monitor on every traverse)} .
$$

Read $\bar c_d$ as: every traverse pays the cache lookup and the monitor, and a
traverse that is found stale pays the verification *instead of* the replay.

Both are below $c_v$ exactly when the compile-plus-monitor price is below the
verification price, and that is the only inequality that decides whether
compilation can pay at all:

$$
c_v-\bar c_d \;=\; p\,(c_v-c_c)-c_d \;>\;0
\iff
c_d \;<\; p\,(c_v-c_c).
$$

The right-hand side is the detector break-even already reported in §V-F of the
paper, here as a per-traverse price. Every threshold below is that inequality
solved for a budget instead.

---

## 2. The single interface: an exact sum and its limit

**Proposition 1 (amortisation).** Over $T\ge1$ traversals of one interface,

$$
C_{\text{unsolid}}(T) = T\,c_v,
\qquad
C_{\text{solid}}(T) = c_v + (T-1)\,\bar c .
$$

The gain is

$$
G(T) \;=\; \frac{C_{\text{unsolid}}}{C_{\text{solid}}}
\;=\; \frac{T\,G_\infty}{T-1+G_\infty},
\qquad
G_\infty \;=\; \frac{c_v}{\bar c}
\;=\; \frac{1}{1-\rho\,(1-c_c/c_v)} ,
$$

and $G(T)\to G_\infty$ monotonically from below, with $G(T)>1$ **iff** $T>1$.

*Proof.* The $T-1$ later traversals are i.i.d.\ with mean $\bar c$, so the sum
is $c_v+(T-1)\bar c$; write $C_{\text{solid}}=\bar c\,(G_\infty+T-1)$. Then
$dG/dT = G_\infty(G_\infty-1)/(T-1+G_\infty)^2>0$ when $G_\infty>1$, and
$G(T)>1 \iff (T-1)(G_\infty-1)>0$. $\square$

**Numbers.** $G_\infty = 1/0.109 = 9.174$; $G(2) = 2G_\infty/(1+G_\infty) = 1.803$.
The break-even $T^\*=1$: solidification pays from the *second* traverse, because
the first traverse is the verification in both arms.

Two consequences worth naming, because they recur in §3:

1. **$T^\*$ is not a critical budget.** It equals $1$, independent of every
   price. Nothing is being optimised; the arms are identical for one traverse by
   construction.
2. **$G_\infty$ is a price ratio, not a speed-up.** It compares two ways of
   paying for the same interface and says nothing yet about how deep a fixed
   budget goes.

---

## 3. The budget–depth map, and why $B^\*=c_v$ is degenerate

**Proposition 2 (budget–depth map).** A chain of depth $K$ crosses each of its
$K$ interfaces once. With no fixed premium and no monitor,

$$
K_{\text{unsolid}}(B) \;=\; \frac{B}{c_v},
\qquad
K_{\text{solid}}(B) \;=\; 1+\frac{B-c_v}{\bar c}
\quad (B\ge c_v).
$$

*Proof.* The unlabelled chain charges $c_v$ per rung. The compiled chain charges
$c_v$ for the first rung — which must be verified before anything can be
compiled — and $\bar c$ for each of the remaining $K-1$. $\square$

**Theorem 1 (degeneracy).** For every $B\ge c_v$,

$$
K_{\text{solid}}(B)-K_{\text{unsolid}}(B)
\;=\;\Bigl(\frac{1}{\bar c}-\frac{1}{c_v}\Bigr)(B-c_v)\;\ge\;0,
$$

with equality at $B=c_v$ (and, below it, both arms still at the same fraction of
their first rung, so the difference is $0$ rather than negative). Hence the
crossing is $B^\*=c_v$, and **the compiled arm is never behind anywhere**. The
brief's loss region does not exist even in the idealisation where it would be
easiest to produce one.

*Proof.* Subtract and factor; the bracket is positive because $\bar c<c_v$.
$\square$

**This is the negative result.** The brief's prediction — "at small budgets
$K_{\text{solid}}<K_{\text{unsolid}}$, and a critical $B^\*$ separates the two
regimes" — does not follow from its own formulas. The initial $c_v$ is not a
penalty unique to the compiled arm: the unlabelled arm pays it too, on the same
rung. What the initial verification does is remove the slope advantage from the
first rung only, which leaves the arms tied at exactly one crossing and the
compiled arm ahead everywhere above it.

Below $c_v$ neither system completes a single interface, so the comparison there
is vacuous: a crossing placed where neither arm can finish one task is not an
engineering threshold, it is an artefact of allowing fractional depth. The
interesting threshold can only appear once a premium makes the compiled arm pay
for something the unlabelled arm does not.

**Corollary (what makes $B^\*$ interesting).** A non-degenerate interior
crossing requires a term that the compiled arm pays and the unlabelled arm does
not. Write the compiled depth as

$$
K_{\text{solid}}(B) \;=\; 1+\frac{B-c_v-c_d-S}{\bar c_d},
\qquad S\ge 0,
$$

where $S$ is any fixed premium of the compiled regime and $c_d$ any per-traverse
monitor. Then

$$
B^\* \;=\; c_v\,\frac{c_v+c_d+S-\bar c_d}{c_v-\bar c_d}
\;=\;
\frac{c_v\bigl[p\,(c_v-c_c)+S\bigr]}{p\,(c_v-c_c)-c_d},
$$

and the losing region is the non-empty interval $(c_v,B^\*)$ exactly when
$B^\*>c_v$. At $S=c_d=0$ it collapses to the point $c_v$, recovering Theorem 1.

---

## 4. Two premiums, solved in closed form

### 4.1 Fixed infrastructure ($S$)

$S$ is the witness store, the compiler, the toolchain: paid once, before any rung
is compiled, and not paid by the unlabelled arm. Setting $c_d=0$ in the corollary,

$$
B^\*(S) \;=\; c_v + S\,\frac{c_v}{c_v-\bar c}
\;=\; c_v + S\,\frac{G_\infty}{G_\infty-1},
$$

affine with intercept exactly $c_v$ and slope $G_\infty/(G_\infty-1)$.

The slope is the quantitative content: **the critical budget grows faster than
the fixed cost**, by $G_\infty/(G_\infty-1)$. At $G_\infty=9.174$ a unit of
infrastructure costs $1.122$ units of budget before it repays itself. Because
$G_\infty=c_v/\bar c$ and $\bar c$ rises with $\rho$, the same infrastructure is
more expensive to amortise in a drifting world: the slope is $1.010$ at
$\rho=0$, $1.122$ at $\rho=0.1$, $2.020$ at $\rho=0.5$, $10.10$ at $\rho=0.9$.
Infrastructure is leveraged by stability.

### 4.2 A monitor paid on every traverse ($c_d$)

A compiled path cannot know it is stale without checking, so the monitor is paid
on every traverse, including the first, where the check is redundant but the loop
is already running. Then

$$
K_{\text{solid}}(B)=1+\frac{B-c_v-c_d}{\bar c_d},
\qquad
\bar c_d=c_c+c_d+\rho\,(c_v-c_c).
$$

**Theorem 2 (detector-priced critical budget).** With $a\equiv c_v-c_c$ and
$p=1-\rho$,

$$
B^\*(\rho,S,c_d)
\;=\;
\frac{c_v\,\bigl[p\,a+S\bigr]}{p\,a-c_d}
\;=\;
c_v\,\frac{a(1-\rho)+S}{a(1-\rho)-c_d}.
$$

Consequences:

1. **Existence.** Finite and positive iff $c_d<p\,a$ — the detector break-even
   of §V-F. Above it, compiling is never rational at any budget.
2. **In $S$.** Affine, slope $c_v/(p\,a-c_d)$: infrastructure is *more*
   expensive to amortise when the monitor is dear, by the same factor.
3. **In $\rho$.** With $S=0$ the threshold is **independent of $\rho$**: the
   factor $(B-c_v)$ survives factoring, and drift only changes the slope after
   the crossing. With $S>0$ or a monitor, $B^\*$ rises with $\rho$ and has a
   **vertical asymptote at $\rho^{\circ}=1-c_d/a$**, beyond which compiling never
   pays. At $c_d=0.02$ that is $\rho^{\circ}=0.980$.
4. **Divergence in $c_d$.** At $\rho=0$ it reduces to
   $c_v a/(a-c_d)$; the threshold diverges as $c_d\uparrow a$, so the *budget*
   at which compiling becomes rational blows up before the *price* becomes
   prohibitive. A system one per cent away from the break-even price needs an
   unbounded budget to prefer compiling.

*Proof.* Set $K_{\text{solid}}=K_{\text{unsolid}}$: with $m=\bar c_d$,
$1+(B-c_v-c_d)/m=B/c_v$ gives $B(1-m/c_v)=c_v+c_d-m$. Now
$c_v-m=p\,a-c_d$ and $c_v+c_d-m=p\,a$; substitute and divide. The $\rho$-claims
follow by differentiating $a(1-\rho)/(a(1-\rho)-c_d)$.
$\square$

The $\rho$-independence at $S=c_d=0$ is a real prediction and worth stating
plainly: **without a monitor there is no drift-dependent threshold**, because
drift changes the price of every rung after the first by the same multiplicative
factor, and a multiplicative price change cannot move the point at which two
lines through $(c_v,c_v)$ separate. Drift moves the *slope*, not the *crossing*.
It is the monitor — a payment that does not scale with the drift it defends
against — that couples the two.

**Numbers.** $B^\*=1.0230$ at $c_d=0.02$, $\rho=0.1$ ($1.0206$ at $\rho=0$,
$1.0421$ at $\rho=0.5$, $1.2532$ at $\rho=0.9$); $2.279$ at $c_d=0.5$; $21.73$ at
$c_d=0.85$; no value at $c_d=0.891$.

---

## 5. Reach against budget, and the finite optimum

Reach is exponential in difficulty, and difficulty saturates in depth
(the paper's Eq.~(12) with $P\mapsto K$, $P_0\mapsto K_0$):

$$
D(K)=1-e^{-K/K_0},
\qquad
R(B)\;=\;A_0\,e^{\alpha D(K(B))}
\;=\;A_0\exp\!\Bigl[\alpha\bigl(1-e^{-K(B)/K_0}\bigr)\Bigr].
$$

**Proposition 3 (reach ratio).** For two arms with depths $K_A(B)\le K_B(B)$,

$$
\frac{R_B}{R_A}
\;=\;\exp\!\Bigl[\alpha\bigl(e^{-K_A/K_0}-e^{-K_B/K_0}\bigr)\Bigr]
\;\ge\;1 ,
$$

the bracket being increasing in $K_B-K_A$ and decreasing in $K_A$.

**Theorem 3 (the advantage is transient in budget).** Fix $S,c_d$ with
$B^\*<\infty$. Then

1. $R_B(B)/R_A(B)\to1$ as $B\to\infty$;
2. the ratio attains a maximum at a finite $B^\dagger$;
3. the depth gain $K_B-K_A=(B-c_v-c_d-S)(1/\bar c_d-1/c_v)+O(1)$ is
   **unbounded**, linear in $B$.

*Proof.* (1) $K_A,K_B\to\infty$, so both exponentials vanish and the bracket
$\to0$. (2) The bracket is $g(B)=e^{-K_A/K_0}(1-e^{-\Delta/K_0})$ with
$\Delta=K_B-K_A\to\infty$: $g\to0$ at both ends of the domain and $g>0$ between,
so the maximum is interior. (3) Substitute Proposition 2. $\square$

The reading is the one §V-F reached from the other direction, now as a function
of budget: **the depth advantage is unbounded and the reach advantage is not.**
A compiled system goes arbitrarily deeper for a fixed extra budget, but reach
saturates at the horizon $A_0e^{\alpha}$, so beyond
$B\sim c_vK_0\ln(\alpha\Delta/K_0)$ the unlabelled arm has caught up in *reach*
while remaining arbitrarily far behind in *depth*. The two quantities diverge,
and quoting one as the other is the error the section exists to prevent.

**Local exponential (the honest form of "explosive").** For $K\ll K_0$,
$D(K)\approx K/K_0$, so

$$
\frac{d}{dB}\ln\frac{R_B}{R_A}
\;\approx\;
\frac{\alpha}{K_0}\Bigl(\frac{1}{\bar c_d}-\frac{1}{c_v}\Bigr)e^{-K_A/K_0}
$$

in the window $B-c_v\ll c_vK_0$, and the ratio grows exponentially there with
that rate. The factor $e^{-K_A/K_0}$ is not decoration: it is the probability
the unlabelled arm has not already saturated, and dropping it overstates the
rate by ten per cent at the baseline. Outside the window saturation dominates and
the ratio turns over — exponential locally, bounded globally, the same shape as
the paper's §II-G and for the same reason: the coordinate is convex, so no
constant rate survives a range.

---

## 6. Task granularity: the second threshold

A real chain completes whole rungs. The fluid crossing $B^\*$ is where the
*expected depth* ties; the budget at which a scheduler first sees the compiled
arm get further ahead is a different number, and it is not a small correction:
the two differ by one or two rungs, which is the same order as $B^\*-c_v$.

With $k$ replays affordable beyond the verification, the expected completed
count is $1+\sum_{j\le k}p^j$, while the comparison budget is
$(c_v+c_d+S+kc_c)/c_v$. So the whole-rung threshold is

$$
B^\*_{\text{int}} \;=\; c_v+c_d+S+k^\*c_c,
\qquad
k^\*=\min\Bigl\{k:\;1+\sum_{j\le k}p^j \;>\; \frac{c_v+c_d+S+kc_c}{c_v}\Bigr\}.
$$

Two facts follow, both falsifiable.

* The realised threshold is **below** the fluid one whenever $S$ is large enough
  to matter, because the geometric sum grows faster than $S+kc_c$. At $S=4$ the
  gap is $0.43$ budget units, $7.8\%$ of $B^\*$; at $S=0.25$ it is $0.02$, $1.6\%$.
* The sum converges to $p/(1-p)$, so there is a **second critical premium**,
  $S^{\circ}=\max_k\bigl[\sum_{j\le k}p^j-kc_c/c_v\bigr]$ ($8.47$ at the
  baseline prices), above which the compiled arm never overtakes a whole-rung
  chain at *any* budget. The fluid theory has no such limit; granularity
  introduces one.

The mechanism is the inspection paradox: a budget-constrained run stops just
after a cheap replay, so its realised count is biased above $B/\bar c_d$. The
bias helps compilation, and it is invisible to any argument stated in expected
cost alone.

---

## 7. Falsifiable predictions

Each is a number, a functional form, or an ordering, fixed *before* the
simulation is run. The script reports each with the measurement that could break
it, and all seven are checked mechanically.

| id | prediction | refuted if |
|---|---|---|
| **P1** | $B^\*=c_v$ exactly when $S=c_d=0$, and the compiled arm is never behind for $B>c_v$ | any interior $B^\*>c_v$ with $S=c_d=0$, or a measured $K_B<K_A$ at $B>c_v$ |
| **P2** | $B^\*(S)=c_v+S\,G_\infty/(G_\infty-1)$: affine, intercept $c_v$, slope $1.1223$ | a non-affine relation, or a slope off by more than 1\% |
| **P3** | $B^\*(\rho)=c_v\,a(1-\rho)/(a(1-\rho)-c_d)$, independent of $\rho$ at $c_d=0$, asymptote at $1-c_d/a$ | any $\rho$-dependence at $c_d=0$, or a different asymptote |
| **P4** | $R_B/R_A\to1$ as $B\to\infty$, with an interior maximum at $B^\dagger$ | monotone ratio, or a ratio staying above 1 at $B\gg c_vK_0$ |
| **P5** | depth gain unbounded, linear in $B$, slope $1/\bar c-1/c_v$ | sublinear growth or saturation of $\Delta K$ |
| **P6** | local rate $\alpha(1/\bar c-1/c_v)e^{-c_v/K_0}/K_0$ | the window-shrinking secant not converging to it |
| **P7** | whole-rung threshold $B^\*_{\text{int}}$ within one replay of $B^\*$, below it for $S>0$ | a gap larger than $c_c$ on the high side or 10\% on the low side |

**The operational claim, stated so it can be wrong:** *a system whose compiled
path carries a fixed premium $S$ should not compile when its budget is below
$B^\*(S)$, and below $B^\*$ the correct policy is to re-verify every rung.* The
claim is vacuous for $S=0$, where the threshold is the price of one crossing.

---

## 8. What would refute the theory

1. **A measured $B^\*$ that is not affine in $S$.** The corollary fixes both the
   intercept $c_v$ and the slope; a curve would mean the premium is not fixed,
   and the amortisation picture would need a state variable it does not have.
2. **$\rho$-dependence at $c_d=0$.** Theorem 2 says there is none. A simulation
   that moves the threshold without a monitor has a drift term that is not
   multiplicative in the per-rung price.
3. **A monotone reach ratio.** If $R_B/R_A$ keeps rising with $B$, difficulty
   saturation is not binding in the regime tested — which relocates the horizon
   rather than confirming an explosion.
4. **$c_c\ge c_v$.** Then $\bar c\ge c_v$, $G_\infty<1$, and compiling is
   strictly worse at every depth. The theory predicts a reversal, not a smaller
   gain; profitable compilation with $c_c>c_v$ falsifies the price model.
5. **A granular threshold above the fluid one at large $S$.** The inspection
   paradox predicts the opposite sign. A measured gap in the other direction
   means the stopping-time bias is not the one computed here.

---

## 9. Instantiated numbers (baseline: $c_v=1$, $c_c=0.01$, $c_d=0.02$, $\rho=0.1$)

| quantity | closed form | value |
|---|---|---|
| $\bar c$ | $c_c+\rho(c_v-c_c)$ | $0.1090$ |
| $\bar c_d$ | $c_c+c_d+\rho(c_v-c_c)$ | $0.1290$ |
| $G_\infty$ | $c_v/\bar c$ | $9.1743$ |
| $G(2)$ | $2G_\infty/(1+G_\infty)$ | $1.8034$ |
| $B^\*$ at $S=c_d=0$ | $c_v$ | $1.0000$ |
| $B^\*$ slope in $S$ | $G_\infty/(G_\infty-1)$ | $1.1223$ (fit $1.1246$, $R^2=1.000000$) |
| $B^\*$ at $S=1$ | $c_v+S\,G_\infty/(G_\infty-1)$ | $2.1223$ |
| $B^\*_{\text{int}}$ at $S=1$ | §6 | $2.0198$ |
| $B^\*$ at $c_d=0.02,\ \rho=0$ | $c_v(c_v-c_c)/(c_v-c_c-c_d)$ | $1.0206$ |
| $B^\*$ at $c_d=0.02,\ \rho=0.1$ | Theorem 2 | $1.0230$ |
| $B^\*$ at $c_d=0.02,\ \rho=0.5$ | Theorem 2 | $1.0421$ |
| $B^\*$ at $c_d=0.02,\ \rho=0.9$ | Theorem 2 | $1.2532$ |
| $B^\*$ at $c_d=0.50,\ \rho=0.1$ | Theorem 2 | $2.2794$ |
| $B^\*$ at $c_d=0.85,\ \rho=0.1$ | Theorem 2 | $21.73$ |
| monitor limit | $c_d<p(c_v-c_c)$ | $0.891$ |
| drift limit | $\rho^{\circ}=1-c_d/(c_v-c_c)$ | $0.980$ |
| premium limit (whole rungs) | $\max_k[\sum_{j\le k}p^j-kc_c]$ | $8.47$ |

Reach parameters: $A_0=1$, $\alpha=\ln 10^{12}$ (the paper's horizon),
$K_0=10$ (the paper's worked ladder). Neither is re-fitted here; changing $\alpha$
moves $B^\dagger$ and leaves every $B^\*$ unchanged — which is itself a check,
because the threshold is a cost statement and the peak is a saturation statement.
