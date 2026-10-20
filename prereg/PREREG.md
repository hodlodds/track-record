# HodlOdds pre-registration: prospective B vs BF crash-risk evaluation and the October 20 decision rule

Public copy prepared on 2026-10-18 from the internal pre-registration, which was frozen on 2026-10-16 19:25 UTC and amended on
2026-10-17 before any forward outcome was inspected. No outcome had been unsealed when this copy was written; it was
time-stamped before 2026-10-20 01:00 UTC, when the evaluation unseals the outcomes. Edits relative to the internal text are
limited to removing internal infrastructure, file and tool names and translating one operator quote into English. Every
criterion, number and date is unchanged. SHA-256 of the internal originals: `ORIGINALS.sha256`.

---

## Part 1 — Pre-registration (frozen 2026-10-16 19:25 UTC)

Written before any forward outcome was observed.

### Artifacts (fixed; never refit or recalibrated)

- **B**: model JSON sha256[:16] `2585c2b40c567061`.
- **BF**: model JSON sha256[:16] `c158d70e676b5733`. Features of B plus the flow features.
- Both are LightGBM with the frozen settings.
  - Training: a 20 % sample of the crash dataset with t+60m ≤ 2026-10-01 00:00 UTC.
  - Platt calibration: sample rows with t ≥ 10-01 and t+60m ≤ 10-15 00:00.
- Alert thresholds are the artifacts' own `alert_threshold` values (99th percentile of the calibration slice):

| Artifact | Alert threshold |
|---|---|
| B | 0.3406 |
| BF | 0.3393 |

- Feature code: base features sha `5d1d4fefd57b33a2`, flow features sha `4c7a1def4c313ee5`.

### Data (prospective, live)

- **Where it runs:** the operator's private scoring service.
- **Rows:** every 5-minute landmark t from **2026-10-16 19:25 UTC**. Each landmark logs **every eligible row**: a pump-suffix
  mint, SOL-quoted, not in a blocked pool, with a completed bar in [t−300, t−60].
- **Inputs:** both models are scored on identical live inputs read from the operator's market database (completed one-minute
  bars and token metadata) at t+20 s.
- **Logged** per landmark:
  - all 37 features, full precision;
  - p_B, p_BF, raw margins;
  - issued_at, compute_s, n_active, n_eligible;
  - the wallet-skill table day and its load time;
  - model and feature hashes.
- **Labels** come later, from completed one-minute bars, with the crash definition:
  - y = 1 if the first completed bar in [t, t+3540] has close ≤ 10 % of the close at t;
  - a horizon not covered by data is dropped, never labelled negative.

### Looks

- **2026-10-30:** descriptive only (landmarks t ≤ 10-29 23:55). No pass/fail.
- **2026-11-15:** the binding report. Landmarks with t+60m ≤ 2026-11-15 00:00 UTC.

Until those looks: availability and latency monitoring only; prediction outcomes are not inspected.

### Report (both looks)

- **Paired AP**, BF vs B: point estimate, 7-day block and mint-cluster intervals, tie-correct AP.
- **Brier and log loss.**
- **Reliability bands.**
- **The not-already-dumping stratum** (close ≥ 0.8 × completed 60-min high).
- **Episode metrics:** recall of crash episodes with an alert issued **≥5 min** and **≥15 min** before the first crossing,
  measured from actual issuance time.
  - Alerts use the frozen thresholds and a fixed 60-minute per-mint alert cooldown.
  - Also: false-alert episodes per day.
- **Separately:** coverage, latency (issued_at − t), errors, and skill-table delays.

### Separation

This forward subset is distinct from the blind replay (the frozen arms B2/B/BF scored retrospectively on 10-16..11-15 full
landmarks). Reconstructed earlier predictions are never called forward logs.

### H

Parked. Recorded: "H feasibility failed; predictive value untested." Reopen only with a source that has complete
token-account movements and ownership.

---

## Part 2 — Amendment 1 (written 2026-10-17 10:50 UTC, before any forward outcome was inspected)

Operator decision (2026-10-17 10:47 UTC): move the rule and the decision earlier, to October 20, and test on historical data
until then so a decision can be made as soon as possible.

- **Binding look moves to 2026-10-20.** The data window is landmarks with t+60m ≤ 2026-10-20 00:00 UTC (from 2026-10-16
  19:25 UTC, ~3.2 days). The 2026-10-30 descriptive look and the 2026-11-15 binding report become follow-ups: they are still
  produced, but they no longer decide.
- **Caveat recorded now.** ~3.2 forward days give a small, likely underpowered paired comparison. The 10-20 decision therefore
  rests mainly on the historical evidence (blind replay and backtests on data before 2026-10-16), with the forward subset as
  a consistency check. The exact decision rule for 10-20 is set before 10-20 and appended here before the look.
- **Unchanged:** artifacts, hashes, features, labels and report metrics. No outcome is inspected before the 10-20 look.

### Decision rule for the 2026-10-20 look (appended 2026-10-17 ~11:15 UTC, before any forward outcome was inspected)

Operator direction (2026-10-17 11:11 UTC): the paid x402 API comes first, the public website last.

> **Purpose:** October 20 decides eligibility for a limited public beta. It does not overturn the failed original +20%
> relative-AP differentiation criterion.
>
> **Window:** logged predictions from October 16 19:25 UTC, with `t+60m ≤ October 20 00:00 UTC`. Keep artifacts, Platt
> maps and thresholds unchanged: **BF 0.3393; B 0.3406**.
>
> **Engineering gate:** ≥72 hours logged; ≥99% of scheduled scoring runs completed; issuance-delay p99 ≤60 seconds;
> model/feature hashes present on every prediction; ≥500 distinct crashed mints; no unresolved timestamp, label or parity
> defect. Missing horizons are excluded and counted.
>
> **Historical quality gate:** assessed separately in August and the seen September–October interval: AP/prevalence ≥10;
> Brier skill ≥20% versus each calibration-window constant baseline.
>
> **Probability gate:** historical and forward aggregate observed/expected incidence must lie in **[0.80, 1.25]**, and
> calibration slope in **[0.80, 1.20]**. Use fixed bands `[0,.005,.01,.02,.05,.1,.2,.4,.7,1]`. For bands containing ≥100
> distinct mints and ≥20 positive landmarks, require
> `|observed − predicted| ≤ max(0.02, 0.25 × mean predicted)`.
> Underpopulated bands are explicitly unvalidated.
>
> **Forward consistency:** AP/prevalence ≥10 and Brier skill >0. Retain BF if AP ≥0.95×B AP, and both Brier and log loss
> ≤1.05×B’s. Otherwise select B only if it passes the absolute gates. Neither passes → no paid probability product; retain
> a private/experimental ranking tool.
>
> **Alerts are separately gated:** at the unchanged threshold and 60-minute per-mint cooldown, episode precision ≥50% and
> crash-episode recall with ≥5-minute notice ≥40%. Failure disables push alerts; it does not justify lowering the threshold.
>
> These are product acceptance tolerances, not significance tests. Publish failures and retain the October 30 and
> November 15 follow-ups.

Historical work before the look (compute budget ≤6 h/day; no feature search, threshold sweep or recalibration):
- 10-17: from the existing weekly predictions, August and September–October separately: AP, AP/prevalence, Brier, log loss,
  calibration, episode alerts. Plus hour boundaries, the not-already-dumping rows, and reserve bands <50 / 50–300 / ≥300 SOL.
- 10-18: replay the actual frozen B/BF/B2 artifacts on 2026-10-15 (labels ending before 10-16); audit 100 positive labels,
  100 negatives and 100 feature timestamps; check serving parity.
- 10-19: model card and report template; tests for missing/stale data, API outage and payment failure.
- The frozen model's 10-01..10-14 calibration slice is NOT test data.
