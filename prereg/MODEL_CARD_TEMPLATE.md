<!-- Public copy prepared 2026-10-18 from the internal template (written before any outcome was unsealed). Edits are
limited to removing internal machine, file and system names; every rule is unchanged. Original SHA-256 in ORIGINALS.sha256. -->

# Crash-risk model card — October 20 decision template

Status: **UNFILLED — not a release approval.** No forward outcomes are included here.
Decision authority: `PREREG.md` (Part 2, decision rule), with unchanged numerical gates.
Product order: paid x402 API first; public website later. This repository change builds neither.

## Version and provenance

| Field | Value to publish |
|---|---|
| Card version / published UTC | TODO |
| Decision / selected arm | TODO: BF / B / neither |
| Artifact version | TODO from the selected artifact's `meta.json` |
| Full model JSON / text / metadata SHA256 | TODO from replay and forward reports |
| Registered model JSON SHA256 prefix | B `2585c2b40c567061`; BF `c158d70e676b5733` |
| Registered feature SHA256 prefixes | base features `5d1d4fefd57b33a2`; flow features `4c7a1def4c313ee5` |
| Serving evaluator SHA256 | TODO; compare the training-side and serving-side evaluators |
| Frozen training cutoff | Labels mature by 2026-10-01 00:00 UTC |
| Frozen calibration | t >= 2026-10-01; t + 60 min <= 2026-10-15 00:00 UTC |
| Threshold | TODO: exact artifact value; B 0.3406 / BF 0.3393 are rounded displays |
| Report/code/input checksums | TODO: link immutable historical, replay, forward JSON and manual audit review |
| Known unresolved defects | TODO; do not replace missing review with “none” |

No refitting or recalibration is performed by the decision scripts. A new artifact
requires a new version and evaluation. The reliability table embedded in the
existing artifact describes **historical weekly BF fits**, not this frozen fit.

## Event and time semantics

At a five-minute landmark `t`, the reference is the last eligible completed
one-minute close, with start timestamp in `[t-300, t-60]` and positive reserve.
The event is **at least a 90% decline in a completed one-minute closing price,
measured in SOL**, relative to that reference, during the registered horizon.
Future bars have start timestamps in `[t, t+3540]`; their closes are observed at
`bar_start + 60`. The label is positive if any such close is <= 0.10 times the
reference. `first_cross_min` in the corpus is the **offset in minutes from t to
the crossing bar's start**, not an epoch timestamp.

The forecast window ends at `t+3600`, irrespective of when a customer opens it.
Lead time is measured from actual `issued_at` to the crossing bar's end. Forecasts
issued after a crossing do not constitute advance warnings. Historical alerts
use landmark issuance because no contemporaneous publication log exists; mark
their lead times as retrospective/idealized.

Uncovered horizons are excluded and counted, never converted to negative labels.
An inactive token need not trade every minute: distinguish absent token trades
from missing source coverage. Feed coverage and audit limitations must be stated.

This event is not proof of malicious intent, a contract exploit, failure to sell,
or total financial loss. A price can collapse and recover between minute closes
without satisfying this label. Many observed events occur within a single minute.

## Supported population and abstention

- Probability evaluation covers the registered, active, SOL-quoted pump.fun
  graduates, including the pump-suffix and supported-pool filters. It does not
  cover every mint on Solana.
- Report total candidate rows, eligible/scored rows, unresolved labels and every
  exclusion reason. Do not advertise conditional coverage as whole-market coverage.
- USDC-quoted non-SOL pools: **experimental ranking only; no calibrated percentage**.
  Other unsupported tokens: `risk = null`, with an explicit reason.
- Missing/stale inputs and missing forecast provenance: risk unavailable, never zero.
- Every future response should carry model version, reference time, issuance time,
  horizon end, freshness, coverage status and abstention reason.

## Evaluation design

Historical periods have already been seen and are secondary evidence. Weekly
out-of-sample predictions use their own preceding 14-day calibration windows;
they are not predictions from the October frozen artifacts. Report any excluded
overlap explicitly. September 20 is excluded from the historical September–October
comparison. October 15 is a one-day post-calibration replay of the frozen artifacts.

Prospective window: logged landmarks from 2026-10-16 19:25 UTC with
`t+3600 <= 2026-10-20 00:00 UTC`. No outcome access before
**2026-10-20 01:00 UTC**. Dry runs inspect engineering metadata only.

Uniform 20% historical samples do not reproduce a full-landmark alert stream.
Historical episode recall and cooldown counts are sample-based diagnostics;
label their coverage. The forward evaluation uses all logged eligible landmarks.
Three forward days provide little temporal replication. Mint-cluster confidence
intervals are conditional on those dates; no seven-day bootstrap is meaningful
for this look. The original +20% AP differentiation criterion remains failed.

## Results to fill from JSON (do not hand-select favorable strata)

| Period / arm | Rows / dates / mints | Prevalence | AP / AP lift | Brier / baseline / skill | Log loss | Observed/expected | Calibration slope |
|---|---|---|---|---|---|---|---|
| August, weekly B2/B/BF | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| Sep–Oct, weekly B2/B/BF | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| October 15, frozen B2/B/BF | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| Prospective, B/BF | TODO | TODO | TODO | TODO | TODO | TODO | TODO |

Link the complete per-date and stratum tables (hour boundaries, not already
dumping, and reserves <50 / 50–300 / >=300 SOL). Retain null/undefined metrics.
Do not infer calibration from AP or AUC.

| Risk band | Rows | Distinct mints | Positive landmarks | Mean predicted | Observed | Absolute error / allowed error | Validated / fail / unvalidated |
|---|---|---|---|---|---|---|---|
| [0, .005) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.005, .01) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.01, .02) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.02, .05) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.05, .1) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.1, .2) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.2, .4) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.4, .7) | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| [.7, 1] | TODO | TODO | TODO | TODO | TODO | TODO | TODO |

Bands need >=100 distinct mints and >=20 positive landmarks to be evaluated.
Allowed absolute error is `max(0.02, 0.25 * mean predicted)`. Underpopulated
bands are unvalidated, not passing observations. Calibration-band uncertainty is
group-level evidence, not an individual token's guaranteed probability interval.

| Alert arm | Exact threshold | Alert episodes | Precision | Crash episodes | Recall >=5 min | Recall >=15 min | False episodes/day | Push-alert gate |
|---|---|---|---|---|---|---|---|---|
| B | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO |
| BF | TODO | TODO | TODO | TODO | TODO | TODO | TODO | TODO |

Alert cooldown: 60 minutes per mint across midnight. Publish the exact episode
definition, boundary exposure and sample limitations from the report.

| Decision gates | Result and reason |
|---|---|
| Logging duration / scheduled runs / issuance delay / provenance | TODO |
| >=500 distinct crashed mints; no unresolved audit defects | TODO |
| Historical quality, separately in both periods | TODO |
| Historical and forward probability gates | TODO |
| Forward consistency and BF-vs-B retention | TODO |
| Selected arm, or neither | TODO |
| Independent push-alert permission | TODO |

## Public wording and limitations

> Experimental forecasts of a >=90% decline in the observed one-minute closing
> price, measured in SOL, during the displayed forecast window. Historical
> testing shows useful ranking with modest improvement over simple price-and-
> activity baselines. Prospective evaluation currently covers approximately
> three days. Trading benefit has not been demonstrated. Losses can occur
> without warning.

Only use “probability” externally if the probability release gates pass. Otherwise
retain the private/experimental ranking tool. Never describe a token as “safe,”
“rug-proof,” “97% accurate,” or guaranteed sellable. Contract/authority checks
must be separate from this market-dynamics forecast. The operator's exit-guard experiment
did not demonstrate improved trading returns.

The operator runs an independent strategy that may trade overlapping tokens.
Future customer searches, wallets, API usage and order flow must not feed that
strategy. Disclose any trading fees separately; no paid alteration of scores.

## Monitoring and follow-up

Publish the October 30 and November 15 follow-up reports even after a beta launch.
Record coverage, stale refusals, missing skill-table provenance, calibration drift
and customer-impact incidents. Publish corrections with versioned provenance;
never silently overwrite historical predictions or fit calibration on this look
to make the decision pass.
