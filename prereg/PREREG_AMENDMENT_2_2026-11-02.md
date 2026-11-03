# Amendment 2: a refit of BF serves the launch (decided 2026-11-02, before the refit was fitted)

This amendment was decided and written on 2026-11-02 at about 21:40 UTC, before any label of a landmark on or after
2026-10-15 had been built, and before the refit below was fitted. It is published here on 2026-11-03 with the
substance unchanged; the working record holds internal file paths and is kept privately. Nothing in it changes the
frozen artifacts B and BF, their hashes, or their forward log.

## Why

The 2026-10-30 descriptive look (`results/RESULTS_2026-10-30.md`) found crashes at 1.04 times BF's predicted rate over
13 days, but 1.12, 1.13, 1.18 and 1.21 times on October 26 to 29. That drift matches the calibration ageing we had
measured on the development period: a model whose trees and calibration stay fixed under-predicts more as the market
moves away from its training weeks. BF's trees end on 2026-10-01. So the public launch on 2026-11-07 is scored by a
refit of BF, made with BF's own recipe on newer data, if it passes the gate below; otherwise by the frozen BF.

## What this breaks, disclosed in advance

1. **The 2026-11-15 report becomes descriptive.** It already decided nothing after Amendment 1. Now the refit also reads
   labels from 2026-10-15 to 10-31, which overlap the forward window (from 2026-10-16 19:25 UTC). The forward log itself
   is unchanged: frozen B and frozen BF keep being scored on the same live inputs, with the same hashes, in shadow. The
   2026-11-15 report is still produced from that log, with the registered metrics, labelled descriptive, no pass/fail.
2. **The blind replay** (frozen models scored afterwards on 2026-10-16 to 11-15) is no longer blind for 10-16 to 10-31.
3. **The locked window of the next model study** (2026-09-01 to 10-14) is now used to train the refit, so it can no
   longer serve as a held-out check against it.
4. **Outcome numbers of the gate below are not published.** They use outcomes inside the forward window before its
   report date. What is published is the rule, the decision and the served model's identity.

## The gate, fixed before the fit

- **Recipe:** BF's, unchanged: the same features, LightGBM settings and 300 rounds, then Platt scaling on the raw score.
- **Origin O = 2026-11-01 00:00 UTC.** Only landmarks whose 60-minute horizon ended by O are used.
- **Data check before any fit, without outcomes:** on each day from 2026-10-15 to 10-31, the share of rows missing the graduation time or the
  wallet-skill feature may be at most twice, plus 2 points, its share on 2026-10-01 to 10-14. Otherwise no fit.
- **Candidate H:** trees on horizons ending by O minus 21 days; Platt on O minus 21 to O minus 7 days.
- **Test week:** landmarks of O minus 7 days to O, unseen by H and by the frozen BF.
- **Incumbent I:** the frozen BF as served.
- **G3, the daily calibration guard:** at the start of each UTC day, the model's own rows of the last 3 days whose
  horizon has ended; with at least 50 crashes among them, one intercept shift d so that the shifted odds add up to the
  crashes seen, clamped to ±0.7; otherwise d = 0. A model's first day has no rows of its own, so d = 0.
- **H passes if all hold:** (a) its mean daily AP on the test week is at least I's; (b) its pooled log loss with G3 is at
  most I's with G3; (c) its observed/expected crash ratio with G3 is within 0.80 to 1.25, the band of the 2026-10-20
  probability gate; (d) every test day has rows and no probability is missing.
- **If H passes:** the final fit F, with trees on horizons ending by O minus 14 days and Platt on O minus 14 days to O,
  is served as `crashrisk-bf-2`, with G3. **If H fails:** the frozen BF is served, with G3.
- The site's HIGH RISK line stays at 0.3393, the frozen BF's alert threshold, whichever model is served.

## Decision

H passed on 2026-11-02 at 21:48 UTC. `crashrisk-bf-2` (model.json sha256 prefix `fe55f0d05dc76452`) has been served
since 2026-11-02 22:41 UTC, with G3. The served model's name is in every API answer; the forward log keeps the frozen
BF (`c158d70e676b5733`) and B.
