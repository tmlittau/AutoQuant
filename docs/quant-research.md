# AutoQuant — Quant Methodology Research Dossier

> **Status:** design contract for the "Sophisticated Quant Engine" work (Phases R1–R4).
> **Audience:** a single retail investor running AutoQuant on their own data.
> **Scope of evidence:** daily OHLCV from yfinance, ~15–25 instruments across stocks /
> ETFs / crypto, EUR base currency, no reliable fundamentals feed, one user, one
> Gunicorn worker. Every method below is judged against *those* constraints, not
> against an institutional desk's.

This document surveys the current state of the art in quantitative investment
methodology and justifies, with citations, the specific methods AutoQuant will
implement. It deliberately separates two problems the everyday phrase "better
stock picking" conflates:

1. **Signal quality** — for a *single* instrument, is now a good time to add,
   hold, or trim? (Today: a four-indicator heuristic in `autoquant/signals.py`.)
2. **Portfolio construction** — given scores for *all* instruments, how much of
   each should I hold so the whole book maximises return *and* stays robustly
   diversified against the risks that actually hurt? (Today: manual target
   weights + drift display.)

The two are coupled — a portfolio optimiser needs per-asset views, and a signal
is only worth acting on if it survives portfolio-level risk budgeting — but they
are distinct bodies of literature and AutoQuant treats them as distinct layers.

---

## 0. The non-negotiable precondition: validation

The most important finding of the entire survey is methodological, not
mathematical: **you cannot trust any of the sophistication below until you can
measure, out-of-sample and corrected for multiple testing, whether it adds
value.** The current engine's weights (trend 0.35 / momentum 0.30 / MACD 0.15 /
mean-reversion 0.20) and thresholds (±0.35) were chosen by judgement and have
never been backtested. Adding more knobs without a validation harness simply
multiplies the ways to fool yourself.

Bailey & López de Prado formalise exactly this trap. The **Probabilistic Sharpe
Ratio (PSR)** asks: given a track record's length, skew, and kurtosis, what is
the probability the *true* Sharpe exceeds a threshold? The **Deflated Sharpe
Ratio (DSR)** goes further and corrects for **selection bias under multiple
testing** — if you tried *N* strategy variants and reported the best, the
expected maximum Sharpe is inflated even when no variant has real skill. DSR
deflates the observed Sharpe by the number of (effectively independent) trials
[Bailey & López de Prado 2014].

**Design consequence:** the backtesting harness (Phase R2) is built *before* the
fancier signals (R3) and the optimiser (R4), and every backtest reports DSR/PSR
and the trial count. A factor model that can't beat the manual baseline on a
*deflated* metric does not ship as the default.

---

## 1. Risk and return measurement

The current code measures correlation (Pearson + distance correlation) and a few
per-asset indicators, but has **no portfolio-level risk model**: no covariance
matrix, no Sharpe/Sortino/CVaR on the book as a whole, no benchmark beta. That
gap has to close first because optimisation and backtesting both consume it.

### 1.1 Covariance estimation — shrinkage is the single biggest win

A naive sample covariance matrix on ~20 assets and ~250 daily observations is
noisy and, when assets ≳ observations, ill-conditioned or singular. Inverting it
(which mean-variance optimisation requires) amplifies that noise into extreme,
fragile weights — the classic "error maximisation" critique of Markowitz.

**Ledoit-Wolf linear shrinkage** [Ledoit & Wolf 2004, *"Honey, I Shrunk the
Sample Covariance Matrix"*] pulls the noisy sample matrix toward a structured
target (constant-correlation) with an *analytically optimal*, parameter-free
shrinkage intensity. There is no cross-validation, no free knob, and the result
is guaranteed well-conditioned and invertible. Empirically it reduces tracking
error and raises realised information ratios, and in small-sample studies the
global-minimum-variance portfolio built on a Ledoit-Wolf estimate is a robust
default across investor types [arXiv 2305.11298; arXiv 2601.20643].

**Verdict: ADOPT as the foundational covariance estimator** (`sklearn.covariance.LedoitWolf`).
Keep the plain sample covariance available only for side-by-side comparison.

### 1.2 The metric suite

Variance/Sharpe treat upside and downside symmetrically; a retail investor cares
mostly about the downside. The suite AutoQuant will compute:

- **Sharpe** (annualised excess-return / vol) — the lingua franca, kept for
  comparability, but always shown next to its probabilistic cousins.
- **Sortino** — Sharpe using *downside* deviation only; penalises harmful
  volatility, not upside.
- **Calmar** — annualised return / max drawdown; the "how much pain per unit of
  gain" ratio a drawdown-averse holder feels.
- **Max drawdown** and the **underwater curve** — the most psychologically real
  risk number.
- **Historical VaR and CVaR (95%)** — see §1.3.
- **Beta** to a benchmark (a held index ETF, else `^GSPC`) — how much of the
  book is just market exposure.
- **Volatility** (annualised) and **rolling** versions of vol/drawdown for charts.

Several already exist in `autoquant/metrics.py` (`sharpe_ratio`, `max_drawdown`,
`rolling_volatility`); the rest extend that module rather than duplicate it.

### 1.3 Tail risk — CVaR over VaR

**Value at Risk (VaR)** answers "what loss is exceeded only *q*% of the time?"
but says nothing about *how bad* the exceedances are, and — fatally for
optimisation — it is **not sub-additive**: VaR can punish diversification.
**Conditional VaR (CVaR / Expected Shortfall)** is the *average* loss beyond the
VaR threshold. Rockafellar & Uryasev showed CVaR is a **coherent** risk measure —
sub-additive, so it *rewards* diversification — and, crucially, that minimising
CVaR is a **linear program**, hence tractable inside an optimiser [Rockafellar &
Uryasev 2000]. For fat-tailed daily equity/crypto returns this is the right
downside measure.

**Verdict: ADOPT CVaR** as both a reported metric (R1) and an optimisation
objective (R4).

### 1.4 Risk *contribution*, not just correlation

Average correlation and "effective N = 1/avg_corr" (already computed) are a
coarse diversification lens. A sharper one is the **marginal/percentage risk
contribution** of each holding to total portfolio variance: two assets can be
weakly correlated yet one still dominates risk because of its weight × vol. R1
adds per-holding risk contributions, which is also what risk-parity-style
construction equalises.

---

## 2. Signal quality — from ad-hoc blend to a factor model

### 2.1 What the academic factor literature says

Cross-sectional **factor investing** is the dominant paradigm for systematic
return prediction. The robust, repeatedly-replicated equity factors are
**value, momentum, quality, low-volatility (min-vol), and size** [MSCI; Pacer;
Alpha Architect on momentum]. Their practical appeal for a small book is that
the factors are **imperfectly correlated** — momentum has notably *low*
correlation with low-volatility and value — so combining them diversifies the
*signal* itself across regimes (momentum leads in trends, low-vol/value cushion
drawdowns) [iShares; MSCI].

**Constraint check:** value and quality need fundamentals (P/E, ROE, margins).
yfinance exposes some via `.info`, but the research and community consensus is
that Yahoo's fundamentals are *fragile and frequently empty* — the statement-
level methods often return blank frames. AutoQuant therefore commits to
**price-only factors** (the user's explicit choice), all computable from the
daily OHLCV already fetched, and leaves a clean, optional hook for fundamentals
later:

| Factor | Definition (price-only) | Rationale |
|---|---|---|
| **Momentum (12-1)** | trailing 12-month return, skipping the most recent month | the canonical cross-sectional momentum factor; skip-month avoids short-term reversal contamination |
| **Low volatility** | negative trailing realised vol | the low-vol anomaly; defensive sleeve |
| **Trend quality** | price vs SMA50/SMA200 + slope consistency | trend-following with a persistence filter (generalises today's trend sub-signal) |
| **Mean reversion** | negated rolling z-score | fade stretched prices (kept from the current engine) |
| **Short-term reversal** | negated trailing 1-month return | the well-documented 1-month reversal effect |
| **Beta** | regression beta vs benchmark | exposure control / low-beta tilt |

### 2.2 How to *combine* factors — cross-sectional standardisation

The current engine compares each indicator to an *absolute* threshold (RSI 50,
z-score ±2). The factor-investing standard is **cross-sectional**: for each date,
*z-score (or rank) each factor across the holding universe*, then blend. This
makes scores comparable across assets and regimes and is far more stable than
absolute cut-offs. The composite becomes a weighted sum of standardised factor
scores; BUY/HOLD/TRIM is then a mapping on that composite (top/bottom of the
cross-section), preserving the existing UX while upgrading the engine beneath it.

The legacy engine is **kept selectable** (`method="legacy"|"factor"`) precisely
so R2 can A/B them out-of-sample — the upgrade has to *earn* the default slot.

### 2.3 Regime conditioning — Hidden Markov Models

Factor performance is regime-dependent: momentum thrives in calm trends and
crashes at turning points; low-vol and mean-reversion cushion crises. A
**Gaussian Hidden Markov Model (HMM)** on (return, rolling-vol) features infers
an unobservable market state (e.g. calm-bull / volatile / crisis) from observable
data, returning *filtered* regime probabilities rather than a hard label
[QuantStart; QuantInsti; MDPI 13/12/311 "Regime-Switching Factor Investing"].
Research consistently finds regime-aware allocation improves risk-adjusted
returns, largely by *avoiding persistent high-volatility periods*.

AutoQuant uses a 2–3 state `hmmlearn.GaussianHMM` as a **conditioning layer**:
the current filtered regime overrides the factor weights (e.g. crisis →
momentum↓, low-vol↑, mean-reversion↑). It is deliberately a *gate on existing
factors*, not a black-box predictor — one extra, inspectable moving part.

> **A note on ML for return prediction.** López de Prado's *Advances in
> Financial Machine Learning* (triple-barrier labelling, meta-labelling, purged
> cross-validation) is the reference for supervised ML on markets. It is
> powerful but data- and discipline-hungry, and the dominant failure mode is
> backtest overfitting — the very thing §0 warns about. With ~20 assets and a
> few years of daily bars, a full ML pipeline is **out of scope**: the honest
> risk/reward favours a transparent factor model validated by a deflated
> backtest over an opaque classifier that will overfit. Meta-labelling (a
> secondary model that sizes/filters a primary signal) is noted as a *future*
> hook once the factor + backtest foundation is proven.

---

## 3. Portfolio construction — robust diversification + return

This is the heart of "optimise for earnings *and* robust diversification *and*
other factors." Four families, in increasing reliance on (error-prone) expected-
return estimates.

### 3.1 Mean-variance (Markowitz) — the baseline, not the default

Classic efficient-frontier optimisation. Mathematically elegant, but notoriously
**fragile out-of-sample**: it concentrates into a few assets and is exquisitely
sensitive to expected-return estimates and covariance noise. AutoQuant keeps it
for the **efficient-frontier visual** and as the **baseline the backtester is
expected to beat** — not as a recommended allocation.

### 3.2 Hierarchical Risk Parity (HRP) — the default

López de Prado's HRP [2016, *"Building Diversified Portfolios that Outperform Out
of Sample"*] is the headline method. It (1) hierarchically *clusters* the
covariance, (2) quasi-diagonalises it, and (3) recursively bisects capital
between clusters by inverse variance. Critically it **never inverts the
covariance matrix** and **needs no expected-return estimates** — sidestepping
both fragilities of §3.1. On small, noisy samples it **outperforms mean-variance
out-of-sample** even though its in-sample variance is higher; the in-sample
optimum is not the out-of-sample optimum [López de Prado 2016; Wikipedia HRP;
arXiv 2305.17523]. For a ~20-asset retail book this is the ideal robust default.

**Verdict: ADOPT HRP as the default optimiser** (PyPortfolioOpt `HRPOpt`).

### 3.3 Mean-CVaR — tail-aware allocation

Replace the variance objective with **CVaR** (§1.3): minimise the average of the
worst-*q*% outcomes subject to a return target. Because CVaR minimisation is an
LP it is tractable, and because CVaR is coherent it rewards genuine
diversification. This is the right option when the user wants to **manage
drawdowns / fat tails** specifically rather than symmetric variance — especially
relevant with crypto in the book. PyPortfolioOpt `EfficientCVaR`.

**Verdict: ADOPT as the tail-risk option.**

### 3.4 Black-Litterman — fusing the signal with the market

Naive mean-variance fed with historical mean returns produces wild corner
solutions. **Black-Litterman** [Black & Litterman 1990] starts from the
**market-cap equilibrium** (the allocation the market implies via reverse
optimisation), treats that as a Bayesian *prior*, and updates it with the
investor's **views** and the *confidence* in each. The posterior expected
returns are well-behaved, so the subsequent optimisation stays diversified while
tilting toward the views [Hudson & Thames; PyPortfolioOpt BL docs].

This is the natural home for the R3 **factor scores**: they become the views
(per-asset expected-return tilts), blended with a market-cap prior. It is how
AutoQuant connects "the signal says X looks good" to "therefore hold this much of
X" *without* the instability of feeding raw factor scores into vanilla
mean-variance.

**Verdict: ADOPT to fuse R3 signals with a market prior** (PyPortfolioOpt
`BlackLittermanModel`).

### 3.5 Why not reinforcement learning / end-to-end deep allocation?

Comparative studies (e.g. arXiv 2305.17523 on the Indian market) put RL-based
allocation alongside MVO and HRP; results are mixed and RL is heavily
data-hungry and opaque. For a single user with a few years of daily data it
fails the same overfitting and transparency tests as §2.3's ML caveat.
**Out of scope.**

---

## 4. Tooling decision

| Library | Role | Why |
|---|---|---|
| **PyPortfolioOpt** (MIT) | MVO, HRP, mean-CVaR, Black-Litterman, Ledoit-Wolf wiring | MIT-licensed, lean, `cvxpy`-backed; covers every §3 method with one consistent API |
| **cvxpy** | convex solver backend (ECOS/OSQP/SCS) | required by the CVaR LP and MVO QP; manylinux wheels, no system deps |
| **scikit-learn** | `LedoitWolf` shrinkage; HMM scaffolding | the canonical shrinkage implementation |
| **hmmlearn** | `GaussianHMM` regime detection | the standard, lightweight HMM package |

Riskfolio-Lib was considered (35 risk measures, HERC, EVaR) but is heavier;
PyPortfolioOpt is the leaner MIT default and covers the chosen methods. Riskfolio
remains a future option if exotic risk measures are wanted.

---

## 5. The chosen architecture (what R1–R4 build)

```
            ┌──────────────────────────────────────────────┐
   data →   │  multi-year daily closes (cached, EUR)       │
            └──────────────────────────────────────────────┘
                 │                    │
   R1  ┌─────────▼─────────┐   R3 ┌───▼──────────────────────┐
       │ risk.py           │      │ factors.py + regime.py   │
       │ Ledoit-Wolf cov   │      │ cross-sectional z-scored │
       │ Sharpe/Sortino/   │      │ price factors, HMM-gated │
       │ Calmar/CVaR/beta  │      │ → composite BUY/HOLD/TRIM│
       │ risk contributions│      └───┬──────────────────────┘
       └─────────┬─────────┘          │ (views)
                 │                     │
   R4  ┌─────────▼─────────────────────▼──────────────────┐
       │ optimize.py: HRP (default) · mean-CVaR ·          │
       │ Black-Litterman(views=factor scores) · MVO(base)  │
       │ → target weights + rebalance trade list           │
       └─────────┬─────────────────────────────────────────┘
                 │
   R2  ┌─────────▼─────────────────────────────────────────┐
       │ backtest.py: walk-forward, costs, equity curve,    │
       │ DSR/PSR — VALIDATES every layer above              │
       └────────────────────────────────────────────────────┘
```

**Reading the diagram:** R1 supplies the risk model everything consumes; R3
produces per-asset views; R4 turns views + covariance into actionable weights;
R2 sits underneath and is the arbiter — no layer becomes the default until R2
shows it beats the manual baseline on a *deflated* Sharpe. Build order is
R1 → R2 → R3 → R4 so the validator exists before the things it must validate.

---

## 6. Explicit non-goals (and why)

- **Fundamentals-based value/quality factors** — Yahoo data too unreliable; clean
  hook left for a future paid feed.
- **Intraday / microstructure** — no intraday data; irrelevant to a monthly
  rebalancer.
- **Supervised ML return prediction / RL allocation** — overfitting risk and
  opacity outweigh benefit at this data scale (§2.3, §3.5). Meta-labelling noted
  as a future hook.
- **Auto-execution** — AutoQuant is advisory; it proposes a rebalance, the user
  trades. Non-negotiable.

---

## Sources

- López de Prado, M. (2016). *Building Diversified Portfolios that Outperform Out
  of Sample* (Hierarchical Risk Parity). [ResearchGate 305747867](https://www.researchgate.net/publication/305747867_Building_Diversified_Portfolios_that_Outperform_Out_of_Sample) · [Wikipedia: Hierarchical Risk Parity](https://en.wikipedia.org/wiki/Hierarchical_Risk_Parity)
- Ledoit, O. & Wolf, M. (2004). *Honey, I Shrunk the Sample Covariance Matrix.* [SSRN 433840](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=433840) · empirical: [arXiv 2305.11298](https://arxiv.org/pdf/2305.11298), [arXiv 2601.20643](https://arxiv.org/html/2601.20643v1)
- Rockafellar, R.T. & Uryasev, S. (2000). *Optimization of Conditional
  Value-at-Risk.* [PDF](https://sites.math.washington.edu/~rtr/papers/rtr179-CVaR1.pdf) · CVaR optimization overview: [financerisks.com](https://www.financerisks.com/filedati/WP/paper/CVaR%20Portfolio%20Optimization.pdf)
- Black, F. & Litterman, R. (1990). Black-Litterman model. [Wikipedia](https://en.wikipedia.org/wiki/Black%E2%80%93Litterman_model) · [Hudson & Thames: Bayesian Portfolio Optimisation](https://hudsonthames.org/bayesian-portfolio-optimisation-the-black-litterman-model/)
- Bailey, D. & López de Prado, M. (2014). *The Deflated Sharpe Ratio.* [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) · [deflated-sharpe.pdf](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) · [Wikipedia](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- Factor investing: [MSCI — Understanding Factor Investing](https://www.msci.com/documents/10199/4c5bd381-5b29-453e-ad73-6df24290a172) · [Alpha Architect — Momentum](https://alphaarchitect.com/momentum-factor-investing/) · [Pacer — Multi-Factor Investing](https://www.paceretfs.com/resources/resource-library/multi-factor-investing-beyond-the-traditional-factors/) · [iShares — Dynamic Factor Rotation](https://www.ishares.com/us/insights/dynamic-factor-rotation-investing)
- Regime detection (HMM): [QuantStart — Market Regime Detection with HMMs](https://www.quantstart.com/articles/market-regime-detection-using-hidden-markov-models-in-qstrader/) · [QuantInsti — Regime-adaptive trading in Python](https://blog.quantinsti.com/regime-adaptive-trading-python/) · [MDPI 13/12/311 — Regime-Switching Factor Investing with HMMs](https://www.mdpi.com/1911-8074/13/12/311)
- Libraries: [PyPortfolioOpt (GitHub)](https://github.com/PyPortfolio/PyPortfolioOpt) · [Riskfolio-Lib](https://riskfolio-lib.readthedocs.io/en/latest/)
- ML for finance (scoped out): López de Prado, *Advances in Financial Machine
  Learning* — [notes](https://reasonabledeviations.com/notes/adv_fin_ml/) · triple-barrier/meta-labelling: [Hudson & Thames](https://hudsonthames.org/does-meta-labeling-add-to-signal-efficacy-triple-barrier-method/)
- MVO vs HRP vs RL comparison: [arXiv 2305.17523](https://arxiv.org/pdf/2305.17523)
