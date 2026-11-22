# HodlOdds public track record

Check a prediction you bought at **https://hodlodds.github.io/track-record/**.
Public evidence begins at 2026-10-18 16:49 UTC. Every stored prediction in each
committed minute is included, including experimental scores and empty minutes.
Minute fingerprints link together; FreeTSA signs the head about every five
minutes. Daily OpenTimestamps proofs anchor the chain in Bitcoin. Predictions
are revealed later so the evidence can be independently checked.

## Quick check

Every minute's fingerprint includes the one before it, so changing any past minute changes every later fingerprint.
Those fingerprints no longer match the FreeTSA signatures made at the time (about every five minutes), the daily
Bitcoin proofs, or any earlier copy of this repository. To check it yourself:

```sh
git clone https://github.com/hodlodds/track-record
cd track-record
python3 verify.py --dir . chain --since 2026-11-01
```

`chain` recomputes the fingerprints from the files, checks each FreeTSA signature with OpenSSL and lists the Bitcoin
proofs. It exits 0 when every check passes, 4 when it reports LATE minutes and 1 on any failure (details under
"Three ways to verify"). Without `--since` it audits from the first minute, 2026-10-18 16:49 UTC.

## What a score means

0.04 means about 4 in 100 tokens like this one lose 90% or more of their price within the next 60 minutes. Higher means riskier. A low number is not a safety signal: smaller or slower drops are not counted, and the odds change minute by minute.

For covered SOL tokens, the 60-minute window starts at `as_of`. Scores update each minute; a saved answer is historical evidence, not current odds. Confidence is an evidence label, not a token-specific interval. Experimental non-SOL scores are not validated probabilities. The exact close-based definition appears below.

## Three ways to verify

1. **Browser:** paste your saved JSON answer (a single answer or a board), or
   enter a mint and UTC minute, then click **Verify**. All comparisons and
   signature checks run locally. The page downloads public evidence and exact
   pinned PKI.js 3.2.5 / ASN.1.js 3.0.6 modules from jsDelivr; it never uploads your
   answer. A modern HTTPS browser with Web Crypto and `DecompressionStream` is
   required. Pass, fail, pending and late are shown separately for each step.
2. **Command line:** download `verify.py` from this repository, inspect it, and run:

   ```sh
   python3 verify.py answer my_answer.json
   cat my_answer.json | python3 verify.py answer -
   python3 verify.py chain
   python3 verify.py chain --since 2026-10-18
   python3 verify.py minute 2026-10-18T16:54Z --mint TOKEN_MINT
   python3 verify.py --dir ./track-record --reveal-dir ./reveal answer my_answer.json
   ```

   Python 3.9+ uses only the standard library. OpenSSL must be on `PATH`.
   Without `--dir`, files come from
   `https://raw.githubusercontent.com/hodlodds/track-record/main/`; override with
   `--base-url https://YOUR-MIRROR/`. Reveal URLs come from `reveal-status.json`.
   A full `python3 verify.py --dir ./track-record chain` audit also compares the
   indexes with actual clone files. For a fully offline check, provide
   `--reveal-dir` as well. Global flags go before the subcommand; `--since` goes
   after `chain`.
   `answer` and `minute` exit 0 only when every check passes. They exit 1 if the
   chain, signature, number, level or reveal fingerprint differs, if no stamp
   covers a minute more than 7,800 seconds old, or if a due reveal is missing; 4
   if a minute is LATE; 3 if something is still pending (a reveal before its due
   time, or a stamp not published yet). The most serious one wins: 1, then 4,
   then 3. A minute is LATE when its first covering stamp came more than 600
   seconds after it: the chain head is stamped every 5 minutes, so that stamp
   does not show the minute was committed before what happened in between was
   known. The hourly publication copies the chain to its newest minute but only
   the stamps made by then, so the newest few minutes get their stamp one run
   later: a missing stamp is pending for 600 seconds plus two hours (7,800
   seconds), and the stamp is still judged by its own time when it comes. The
   other checks still run and print their verdicts. It prints a
   verdict per row, without rounding or float tolerance.
   `chain` audits every day from genesis and every indexed token; `chain --since
   YYYY-MM-DD` audits from that day's indexed `prev` onward for cheap periodic
   checks. With `--dir`, each audited token index must list exactly the tokens
   and corresponding texts present. It reports uncovered tail minutes and
   worst/median delays to the first covering stamp, and lists every stretch of
   LATE minutes (first covered more than 600 seconds after the minute, or not
   covered 7,800 seconds after it in the files read); it exits 4 when there is
   one, and 1 on any failure. It lists Bitcoin proofs and runs `ots verify` when
   installed (that optional check may require a network).
3. **Manual, independent tools:** obtain the public files and retain a copy:

   ```sh
   openssl ts -reply -in tsa/2026-10-18/1654.tsr -text
   openssl ts -verify -data tsa/2026-10-18/1654.txt \
     -in tsa/2026-10-18/1654.tsr -CAfile tsa/cacert.pem -untrusted tsa/tsa.crt
   ots verify ots/2026-10-18.chain.ots
   # A pending Bitcoin proof can be upgraded in your own local copy:
   ots upgrade ots/2026-10-18.chain.ots
   ```

   For historical certificates, OpenSSL's `-attime <genTime epoch seconds>` checks
   certificate validity at signing time. The CLI and browser use that time.
   Compare the TSA text byte-for-byte with its public chain line plus newline.
   Rebuild the minute's canonical block and link(s) as below; a signature alone
   does not connect your answer to the signed head. For Bitcoin, also compare
   `<day>.chain` byte-for-byte with `chain/<day>.log`. You can use
   [opentimestamps.org](https://opentimestamps.org) instead of installing `ots`.

## Exact formats

All text is UTF-8, LF terminated, with no header. No existing commitment format
is changed by publication.

* Canonical prediction line: `t\tmint\tkind\tp\tlevel\n` (literal tabs/newline).
  `t` is integer Unix seconds on a UTC minute boundary. `kind` is `sol` or
  `nonsol_exp`. `p` is the stored JSON number (`p_crash_60m`, or
  `experimental_score`), not a displayed/rounded percentage; missing values are
  empty. `level` is the stored `confidence.level`, or empty. The live paid answer
  has the same number and level. A stale `/v1/risk/{mint}/last` answer is detected
  by `freshness: "stale"` or `stale: true` on `hodlodds.answer.v1`. Its number
  must still match exactly; its level must equal the documented `DOWNGRADE` of
  the stored level: high → medium, medium → low, low → low, experimental →
  experimental. Missing confidence remains missing. The API also appends a
  staleness reason; reasons are not committed fields. The verdict explains
  “confidence lowered for a stale last-known answer, as documented”.
* A minute's **block** is its canonical lines sorted by `(mint, kind)` and
  concatenated, including every trailing newline. Empty minutes hash empty bytes.
* Chain line: `<YYYY-MM-DDTHH:MMZ> rows=<n> sha256=<block> chain=<link>\n`.
  Hashes are lowercase SHA-256 hex. `link = sha256(UTF8("<previous link> <minute> <n> <block>"))`.
  The link input has single spaces and **no newline**. Genesis is 64 zeros.
  Minutes strictly increase across UTC day files. A gap is visible; the chain
  alone does not claim that a missing minute was committed.
* `chain/<day>.log`: that UTC day's chain lines, chronologically ordered; a
  day's first link still uses the preceding day's head, never a new genesis.
* `tsa/<day>/<HHMM>.txt`: the exact stamped chain line **plus newline**.
  `.tsr`: DER RFC 3161 TimeStampResp containing CMS SignedData/TSTInfo.
  Its SHA-256 messageImprint covers the `.txt` bytes. `genTime` is FreeTSA's
  independently signed time. `tsa/cacert.pem` and `tsa/tsa.crt` are the published
  root and timestamp signer. The browser checks the imprint, signature, signer
  timestamping purpose, and certificate path to that root. Private `.tsq` files
  are not published. A head stamped after your minute covers it only through
  verified links; the first covering head can be in the next UTC day.
* `ots/<day>.chain` is that day's chain text, with `.chain.ots` its detached
  OpenTimestamps proof. Proofs may be upgraded in place; the input never changes.
* Reveal file: `https://api.hodlodds.com/track/reveal/<day>/<HH>.tsv.gz`.
  `https://api.hodlodds.com/track/reveal/` lists the revealed files by day.
  The uncompressed canonical lines are sorted by **`(mint, kind, t)`** across
  the hour. Gzip level 9, mtime 0, no filename. Re-sort each selected minute by
  `(mint, kind)` before hashing, and compare both row count and block SHA-256.
  The HTTP type is `application/gzip`, with **no Content-Encoding**, so browser
  code performs decompression explicitly. CORS is open; no payment is required.
* `reveal-status.json`:
  `{"schema":"hodlodds.reveal.v1","revealed_through":"2026-10-18T16:59Z","delay_hours":24,"url":"https://api.hodlodds.com/track/reveal/"}`.
  `revealed_through` is null until the first hour is revealed; it advances only
  through consecutive verified hours. It is informational, not a substitute for
  checking a reveal's hash. A corrupt or absent due reveal fails verification.
* `chain/index.txt`: one line per UTC day, ordered by day:
  `<day> first=<minute> last=<minute> lines=<n> prev=<64 hex> head=<64 hex>\n`.
  Minutes have the full `YYYY-MM-DDTHH:MMZ` form. `prev` is the link before the
  first line (64 zeros at genesis); `head` is the last link. The publisher only
  updates the last published day and appends newer days; earlier lines cannot
  change. Each day file must match all five fields.
* `tsa/<day>/index.txt`: sorted token basenames (`HHMM`), one per LF-terminated
  line, with no suffix. An empty file means no tokens that day. Each basename
  discovers both `<HHMM>.txt` and `<HHMM>.tsr`. No global file manifest is used.

## Bounded verification

`answer`, `minute`, and the browser download `chain/index.txt` and the requested
minute's day file. They search that day's token index, then at most the next two
UTC days' token indexes. Once a covering stamp is found, they fetch its day
file and any intervening day needed to check every link. If none is found, the
result is “no covering stamp yet”. One answer never downloads the whole history.
The browser lists Bitcoin proofs for the requested/checked days and the last
seven days in the index; a missing `.ots` means “not yet anchored”.

For a partial check, `prev` comes from the day index. The link equation binds
that value together with the minute, row count, and block hash. Changing `prev`
while retaining the independently stamped head would require breaking SHA-256
preimage resistance (or a hash collision); the checked links bind the requested
block to that head. Each day is checked against first/last/lines/prev/head, and
each next day's `prev` must equal the preceding day's `head`. This authenticates
the requested block without downloading its earlier history; it does not audit
the earlier history. The full audit is `python3 verify.py chain`, from genesis.
Use `python3 verify.py chain --since YYYY-MM-DD` to audit a recent suffix from
its indexed `prev`, ideally after retaining an earlier full audit and head.

## Reveal policy

An hour becomes eligible **24 hours after it ends**, then is published on the
hourly run at minute :35 UTC. For example, October 18's 16:00–16:59 UTC hour is
due October 19 at 17:00 and normally appears at 17:35. The delay is configurable
and published in the status file. A minute that is not due yet is reported as pending and
nothing is downloaded. Once due, a missing file stays pending for up to two hours
(publication runs hourly) and then fails; any hour covered by `revealed_through`
must exist. A missing due file is deliberately visible, never a pass.

Every minute of an hour must already be committed and match its private archive
before the hour is published. For the launch hour only, checking starts at the
first committed minute (16:49); earlier minutes are outside the record. Missing
commitments wait. Corrupt archives are withheld and reported. An empty committed
hour can be revealed as an empty gzip file. Public reveal files are immutable.

## Trust and limits

For the committed minutes, the hashes and independent stamps detect predictions
changed or selectively removed after commitment, provided you retain the
previously witnessed evidence. They do **not** prove that all predictions were
submitted in the first place, that an answer was sold at its claimed time, that
the first commitment was immediate, or that a model is accurate or profitable.
A late timestamp gives a correspondingly weaker timing claim; the verifier
reports its actual delay. How odds matched outcomes is measured separately: the forward test's
reports (below) measure the frozen model BF on the same live inputs (which model served when:
prereg/SERVED_MODELS.md), and the daily accuracy report, once it is published, scores the predictions served.

FreeTSA's clock and signing key are trusted for RFC 3161 time. Published
certificates must themselves be obtained from a trusted source; downloading a
new root from a compromised repository does not establish trust. Historical
checks do not fetch certificate revocation data. GitHub history is a public
witness, **not an immutable ledger controlled by nobody**. Force-push protections,
independent clones, saved answers, and publicly retained anchors make rewrites
observable. OpenTimestamps supplies an additional Bitcoin witness once a proof
is confirmed; pending calendar receipts alone are not Bitcoin confirmation.

The reveal server need not be trusted for authenticity because files must match
the chain. It can withhold files, and GitHub/indexes can hide a suffix from a
first-time visitor. A current copy alone cannot prove that an unseen suffix
never existed. Retain independent copies, compare heads/history, and validate
Bitcoin proofs. The browser page and CDN code also require trust; inspect or
archive the code and use the independent CLI/manual checks when appropriate.

## Forward test: pre-registration and reports

The forecast model's forward test was pre-registered. Its files:

* [prereg/PREREG.md](prereg/PREREG.md): the public copy of the pre-registration (frozen 2026-10-16 19:25 UTC) and of
  its Amendment 1 (2026-10-17, before any forward outcome was inspected), time-stamped before the outcomes were
  unsealed: [prereg/MANIFEST.sha256](prereg/MANIFEST.sha256), with its FreeTSA token (`MANIFEST.tsr`) and its
  OpenTimestamps proof (`MANIFEST.sha256.ots`). Part 1's schedule ("2026-11-15: the binding report") is kept as it
  was frozen: Amendment 1, in Part 2, moved the binding decision to October 20, and Amendment 2 made the November 15
  report descriptive.
* [Amendment 2](prereg/PREREG_AMENDMENT_2_2026-11-02.md) (2026-11-02): a refit of the model makes the odds, and the
  November 15 report becomes descriptive. [Amendment 3](prereg/PREREG_AMENDMENT_3_2026-11-03.md) (2026-11-03): the
  model is refit every week under the same gate.
* [prereg/SERVED_MODELS.md](prereg/SERVED_MODELS.md): which model made the odds when, with each model file's
  fingerprint.
* [prereg/MODEL_CARD_TEMPLATE.md](prereg/MODEL_CARD_TEMPLATE.md): the unfilled model-card template time-stamped with
  the pre-registration, not a filled model card.

The reports:

* [results/RESULTS_2026-10-20.md](results/RESULTS_2026-10-20.md): the pre-registered decision over about three days:
  passed, model BF selected.
* [results/RESULTS_2026-10-30.md](results/RESULTS_2026-10-30.md): the follow-up look, 13 days, descriptive.
* [results/RESULTS_2026-11-15.md](results/RESULTS_2026-11-15.md): the last look the pre-registration schedules, 29
  days, descriptive. It was computed at 10:07 UTC that day; the weekly refit `crashrisk-bf-4` began serving at
  12:56 UTC (see SERVED_MODELS.md).

## Daily accuracy report

Once it is published, [Daily accuracy](report.html) shows each fully
revealed UTC day: a calibration chart, headline scores, alert precision/recall,
and the most confident misses in both directions. Switch between every committed minute and the **first
committed prediction per coin per UTC hour**. Selection precedes censoring; a
missing label is never replaced by a later prediction. Both samples remain
correlated; Wilson intervals are descriptive binomial intervals, not independent
coin or temporal confidence intervals. Empty/undefined statistics are JSON `null`.

Daily accuracy reports appear in `reports/` as days become fully revealed; until the first one is published
there is no `reports/` folder and `verify.py report` (below) has nothing to check. The report job runs daily at
**06:20 UTC** and builds completed, fully revealed days, oldest first; the hourly publisher (:35) checks and
publishes new reports (one per run in this deployment). With the normal 24-hour reveal delay, day D is fully revealed around
**D+2 00:35 UTC**, and its report normally arrives at the first :35 publisher run after the D+2 06:20
computation. Days before publication started (from 18 October 2026) are built the same way, from the stored
market bars of those days. A longer prelaunch delay postpones this further. No outcome computation is allowed before
**2026-10-20T01:00:00Z**. Even after that boundary, neither elapsed time nor an
available private archive is sufficient: `revealed_through` must reach D 23:59.
The mirror must also contain the chain through that minute or beyond.

Only `sol` rows that rehash to their minute commitment enter the main metrics.
`nonsol_exp` rows are counted separately and never assigned outcomes. Missing or
invalid probabilities are dropped explicitly. The evaluator calls the same labelling function
(`label_day`, in the operator's code, not in this repository) used by the prospective evaluation; its
rules:

* Reference: last native completed bar in `[t−300,t−60]`, with finite positive
  close and SOL reserves. A crash is a close **≤10% of that reference** in
  `[t,t+3540]`. `first_cross_min` is the first crossing bar's start offset 0–59;
  that bar completes one minute later.
* All 60 market-wide native bar minutes must be present. Sparse token trading
  alone is not an outage. Invalid horizons and unavailable references are
  dropped, never silently labelled negative. Native-universe filtering is
  unchanged: a `sol` row whose mint does not end in `pump` has no native bars
  and is counted under `outside_label_universe`, never labelled. Market-wide
  coverage cannot detect selective feed losses or retroactive registry changes.
* Brier is `mean((p−y)²)`. Skill is `1−Brier/[q(1−q)]`, where `q` is that
  sample's observed crash rate; it is undefined for constant outcomes. Aggregate
  skill uses the aggregate's pooled base rate. Log loss uses natural logs with
  probabilities clipped to `[1e−12,1−1e−12]`.
* AP evaluates precision at the end of each descending equal-score group,
  weighted by that group's share of all positives. AP/prevalence divides by
  the observed crash rate. Neither is defined without positives.
* Public bands are **lower <2%**, **watch 2–10%**, **high ≥10%** (2% goes in
  watch, 10% in high). Deciles are ascending equal-count ranks, breaking ties by
  `(t,mint,kind)`; ties may split and empty bins remain explicit. Each bin shows
  its mean prediction, observed rate, Wilson 95% interval and observed/expected
  ratio. Overall calibration slope is an unpenalized logistic fit with intercept
  against logit(p); degenerate or nonconvergent fits are null.
* Alerts use the deployed frozen B/BF metadata's **exact** threshold and the
  forward evaluator's 60-minute per-mint cooldown and episode matching. An
  unlabelled emission still consumes cooldown. Selection spans day boundaries:
  a day starts from the alerts the previous day selected in its last hour
  (`carry.cooldown`), and an episode that crosses after midnight keeps the first
  alert the previous day gave it (`carry.episodes`), exactly as a computation
  over the whole history would. Precision credits only an alert's own
  crossing. Recall is measured at ≥5 and ≥15 minutes before the crossing bar
  completes. False episodes/day uses calendar-day exposure. **The commitment
  format lacks `issued_at`**, so prediction time is the explicit issuance proxy
  (as supported by the original selector). Lead times are optimistic; these
  are not measurements of delivered push alerts. The threshold and metadata
  hash are published and cannot change mid-history in this workflow.
* Misses are the ten highest-probability labelled noncrashes and the ten
  lowest-probability crashes, ties by `(t,mint,kind)`. They do not imply a
  binary decision threshold.

`reports/<day>.json` has schema `hodlodds.daily_report.v2`, exact definitions,
code revision and file hashes, the chain day's first/last/count/previous/head,
label hashes, and for both observation units: the day's statistics
(`periods.daily`), its partial sums (`partials`: sums, a fixed public score grid
of 2,434 cells, crash episodes by minutes of first notice) and the small state the
next day starts from (`carry`). `previous` names the previous indexed day's report
and its SHA-256, so the reports form a chain. A day is computed from its own rows
and labels and the previous report's carry only; historical labels and bars are
never re-read. Seven-day and since-launch windows can be built from the daily
files' partials alone (calendar exposure includes missing days; the first indexed
commitment day starts since-launch, including the initial partial day). On the
grid, AP and the calibration slope are named `ap_grid` and
`calibration_slope_grid`; each daily file gives them beside the exact daily AP and
slope. Reports and labels are immutable; `reports/index.json` is a small
day-to-headline map.

Labels are at the same free reveal API under
`<day>/<HH>.labels.tsv.gz`. All 24 hours have files, including empty hours before
the initial commitment. Gzip uses mtime 0 and no filename. Unlike prediction
reveals, label TSVs have the header:

```text
t\tmint\tkind\ty\tfirst_cross_min\tdrop_reason
```

Rows are sorted `(mint,kind,t)` and cover every revealed prediction exactly once.
`y` is 0/1, or empty when dropped; `first_cross_min` is populated only for y=1.
Reasons are `no_reference_bar`, `no_valid_reference_bar`,
`uncovered_market_horizon`, `invalid_horizon_bar`, `invalid_probability`,
`experimental_excluded`, or `outside_label_universe`. Non-SOL exclusions do not
inflate SOL drop counts.

Download **both** `verify.py` and [report_metrics.py](report_metrics.py), or clone
the public repository, then run (Python 3.9+, standard library only):

```sh
python3 verify.py report 2026-10-20
python3 verify.py report 2026-10-20 --deep
python3 verify.py --dir ./track-record --reveal-dir ./reveal report 2026-10-20 --deep
```

By default this command checks one day: it verifies the day's minute blocks and
chain day, joins its published labels by exact key, checks their hashes, takes the
`carry` of the previous report (whose bytes must hash to the day's
`previous.sha256`), recomputes the day's statistics, partials and carry, and
compares the JSON and Markdown (exit 1 on any mismatch). It prints what it relied
on: the previous report's carry, which that run does not recompute. `--deep`
recomputes every day from the first, in order, holding one day at a time;
`--deep --since DAY` starts at DAY and relies on the carry of the day before.
Floating-point comparisons allow only rounding noise (relative 1e−9,
absolute 1e−11); integers/keys are exact. Run `verify.py chain` separately for TSA
signature and Bitcoin evidence. Recomputing report arithmetic does **not**
independently establish the truth of operator-supplied market labels. Those can
be audited against market bars using the published definition and per-row keys.

Odds, not guarantees. Not financial advice.
