# Served models

Every API answer names the model that scored it. This table maps each name to its model file and to the data it was fitted
on. A row is added at each switch, under the pre-registration (`PREREG.md`) and its amendments. It holds no outcome
numbers.

| Model | model.json sha256, first 16 | Trees: landmarks with t+60m ≤ | Platt calibration: landmarks | Served (UTC) | Rule |
|---|---|---|---|---|---|
| `crashrisk-bf-frozen-1` (BF) | `c158d70e676b5733` | 2026-10-01 00:00 | t from 2026-10-01, t+60m ≤ 2026-10-15 00:00 | until 2026-11-02 22:41 | `PREREG.md`, Part 1 |
| `crashrisk-bf-2` | `fe55f0d05dc76452` | 2026-10-18 00:00 | t from 2026-10-18, t+60m ≤ 2026-11-01 00:00 | from 2026-11-02 22:41 | `PREREG_AMENDMENT_2_2026-11-02.md` |

Weekly refits under `PREREG_AMENDMENT_3_2026-11-03.md` are added here as `crashrisk-bf-3`, `crashrisk-bf-4` and so on. A
week whose candidate fails its gate adds a row for the frozen BF instead.

Whichever model is served, the frozen B (`2585c2b40c567061`) and the frozen BF are scored on the same live inputs in the
forward log from 2026-10-16 19:25 UTC, unchanged. The site's HIGH RISK line is 0.3393 for every model.
