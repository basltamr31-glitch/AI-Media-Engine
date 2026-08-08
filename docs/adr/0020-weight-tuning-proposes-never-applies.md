# ADR 0020: Weight tuning proposes; it never applies

- **Status:** Accepted
- **Date:** Phase 6

## Context

ADR 0016 kept every score's per-signal `raw` measurements specifically so that
weights could be refitted against real engagement later:

> Storing signals separately gives a **feature matrix per story**, which is
> exactly the training input for fitting weights against real engagement.

Phase 5 supplied the other half — publications and the performance metrics
recorded against them. Both halves now exist, so the question is no longer
whether the data is there but what to do with it.

The pull is towards a closed loop: fit weights automatically, write them back,
let the ranking improve itself. That is also the version most likely to destroy
the thing the architecture exists to protect. `docs/ARCHITECTURE.md` names
editorial judgement as one of three non-commodities:

> **Editorial judgement.** Choosing 3 stories from 40 is the durable IP.

Handing it to an unsupervised statistic over a few dozen posts trades the most
valuable thing in the system for arithmetic.

## Decision

Three decisions, each making the tuner less clever and more trustworthy.

### 1. Correlation per signal, not regression

Least squares is the obvious method and the wrong one at this scale. Seven
signals fitted on a few dozen published posts has enough freedom to explain
noise exactly, producing confident, unstable weights that swing with each new
week of data — and no way to tell that from learning.

Pearson correlation, computed per signal, answers a weaker and more honest
question: *did stories scoring high on this signal do better?* One signal at a
time, with a sample size attached, and interpretable by someone who is not a
statistician.

Signals that never varied are reported as `never varied`, **not** as zero
correlation. Absence of evidence and evidence of absence are different findings
and a tuner that conflates them silently demotes a signal for having been
constant.

### 2. It refuses on thin data, and damps when it does not

- Below **20 measured stories**, no proposal is made at all.
- Proposals are damped towards the current weights (default **0.3**), so one
  unusual fortnight nudges the ranking instead of replacing it.
- Negative correlations are floored at zero rather than becoming negative
  weights. A signal that is actively *bad* for engagement is interesting and is
  reported — but a negative weight makes the scorer reward the opposite, which
  is a far stronger claim than the sample supports.

### 3. It prints; a human pastes

`engine tune weights` outputs a table of correlations and a YAML block. Nothing
writes to a workspace config. Adopting a proposal is a config edit, reviewed
like any other.

Observations are grouped **by story, not by publication**: one story published
to four channels is one editorial decision distributed four ways, and counting
it four times would let a single well-chosen story dominate the fit while
looking like four independent confirmations.

## Consequences

**Good**

- The loop is closed as an *information* loop: the editor sees that
  corroboration correlates with engagement and recency does not, and decides
  better whether or not they take the numbers.
- Entirely algorithmic — no model call, honouring the cost rule that the eval
  suite already follows.
- No new dependency: the correlation is a dozen lines of arithmetic rather than
  numpy or scipy.
- Cannot silently degrade ranking, because it cannot change ranking.

**Costs, accepted**

- It will not discover interactions between signals. Correlation is per signal
  by construction. When there is enough data for that question to be answerable,
  the stored feature matrix supports a better method, and this ADR should be
  superseded rather than worked around.
- An operator must act for anything to change. That is the point.
- Correlation is not causation, and the report says so where it is read.

## Alternatives considered

**Fit and apply automatically.** The closed loop. Rejected on sample size and on
principle: it optimises for engagement alone, which is the objective that
produces clickbait, and it does so without anybody choosing to.

**Wait for enough data to do it properly.** Nothing accumulates data faster for
being ignored, and the value of seeing the correlations does not depend on the
sample being large enough to fit on. The refusal threshold delivers the caution
without delaying the visibility.

**A `--write` flag that updates the workspace YAML.** Convenient, and one
absent-minded invocation from replacing hand-tuned editorial policy with a
month of noise. Printing the block costs one paste and makes the change appear
in a diff.
