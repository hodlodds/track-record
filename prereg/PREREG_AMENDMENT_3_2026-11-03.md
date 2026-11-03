# Amendment 3: the served model is refit every week (written 2026-11-03, before the first weekly gate)

Written on 2026-11-03, before any label of a landmark on or after 2026-11-01 was built for a gate. It repeats the gate
of Amendment 2 every week with the origin moved forward 7 days. The frozen artifacts B and BF, their hashes and their
forward log stay unchanged.

## Why

Amendment 2 refit BF once, for the launch. The same ageing that made it necessary goes on: on the development period, a
fit's calibration had drifted measurably one week after its training data ended, and weekly refits kept it closest to
the crashes that followed. G3 corrects one intercept a day; a weekly refit renews the trees and the calibration.

## The rule

- **Origin O:** every Saturday 00:00 UTC, the first on 2026-11-07. A week's last labels need the next day's data, so
  each gate runs on the Sunday, not before 01:30 UTC.
- **Gate:** exactly Amendment 2's, with that week's O: the data check on O minus 7 days to O minus 1 day; candidate H with
  trees on horizons ending by O minus 21 days and Platt on O minus 21 to O minus 7 days; the test week O minus 7 days to
  O; the incumbent the frozen BF with G3; pass conditions (a) to (d) unchanged.
- **The first week's overlap, disclosed:** the first test week starts on 2026-10-31, which was inside Amendment 2's test
  week and inside `crashrisk-bf-2`'s calibration window, so that day's outcomes were seen before this was written. H and
  the frozen BF use no label of that day, and the pass conditions were fixed before it.
- **If H passes:** the final fit F (trees on horizons ending by O minus 14 days, Platt on O minus 14 days to O) is served
  as `crashrisk-bf-N`, with N counting up from 3, and with G3, which starts each new model at d = 0.
- **If H fails:** the frozen BF is served, with G3, until a later week passes.
- The site's HIGH RISK line stays at 0.3393.
- Each switch is visible in the served model's name in every API answer. The forward log keeps scoring the frozen BF and
  B in shadow, unchanged.

## What we expect the 2026-11-15 report to show

That report describes the frozen BF, whose trees end on 2026-10-01 and which is never refit. We expect its calibration
to keep ageing over the window: crashes running above its predicted rate, more so in the later weeks than in the first,
as on October 26 to 29. We write this down on 2026-11-03. Outcomes up to 2026-10-31 have been seen, in the
2026-10-30 look and in the gate of Amendment 2; outcomes from 2026-11-01 on have not. The report is published on its
date, whatever it shows.
