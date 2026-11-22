"""Public daily-report v2 arithmetic and validation; Python 3.9+, stdlib only.

No data loading or outcome computation on import. The Air calls the sealed
labeler before invoking these routines; public verification uses published labels.

v2 (R74 scale design, 22 Nov 2026): one day is one unit of work. A day's report is
computed from that day's rows and the small carry of the previous indexed day's
report; it also publishes the partials (sums, a fixed public score grid, alert
episode counts) from which longer windows are composed. periods() below is the v1
all-history computation, kept only as the tests' oracle: nothing in production calls it.
"""
import calendar
from collections import Counter
import gzip
import io
import itertools
import json
import math
import re
import statistics
import time
import zlib

SCHEMA = 'hodlodds.daily_report.v2'
# The public score grid (fixed for every published report; see DEFINITIONS['grid']).
GRID_S = 64                       # equal parts per power-of-two interval
GRID_OCT = 19                     # intervals below 0.5: [2^-20, 0.5); below 2^-20 is one cell
GRID_LOW = GRID_OCT*GRID_S + 1    # cells of the scores below 0.5
GRID_CELLS = 2*GRID_LOW           # cells 0 .. 2*GRID_LOW-1
HEADER = 't\tmint\tkind\ty\tfirst_cross_min\tdrop_reason\n'
DROPS = ('no_reference_bar', 'no_valid_reference_bar', 'uncovered_market_horizon',
         'invalid_horizon_bar', 'invalid_probability', 'experimental_excluded', 'outside_label_universe')
DEFINITIONS = {
    'label': 'air/r1020_forward.label_day: last native bar in [t-300,t-60] with finite positive close and reserves; y=1 iff a token close in [t,t+3540] is <=0.10 times reference close. All 60 market-wide native minutes required. Sparse token trading is allowed; invalid future closes censor. first_cross_min is start-stamped offset 0..59; crossing completes one minute later.',
    'universe': 'Committed sol rows only. nonsol_exp counted separately, never labelled. Invalid/missing probabilities dropped. A sol row whose mint does not end in "pump" is outside the native bar universe that label_day reads (it can only return no_reference_bar there): counted as outside_label_universe, never labelled. Rows removed by the other native-filter conditions appear as no_reference_bar or no_valid_reference_bar. Missing horizons are dropped, never negative.',
    'sampling': 'minute: all committed rows; coin_hour: first committed minute per mint and UTC hour, chosen before dropping unlabelled rows. These samples remain correlated.',
    'scores': 'crash_rate=mean(y); Brier=mean((p-y)^2); baseline_brier=crash_rate*(1-crash_rate); Brier skill=1-Brier/baseline_brier, null for constant outcomes. Aggregates use their own pooled base rate. Log loss uses natural logs and p clipped to [1e-12,1-1e-12].',
    'ranking': 'AP=sum over descending equal-score groups of (new positives / total positives)*(cumulative positives / cumulative rows); AP/prevalence=AP/crash_rate; both null without positives. ap_grid and ap_grid_over_prevalence: the same formula with the scores grouped by their grid cell (descending cells), computed exactly from the integer grid counts.',
    'calibration': 'Bands: lower [0,.02), watch [.02,.10), high [.10,1]. Deciles: ascending rank by (p,t,mint,kind), group floor(10*rank/n), ties may split. O/E=sum(y)/sum(p), null if sum(p)=0. Wilson 95% binomial interval z=1.959963984540054; descriptive only, does not adjust for correlated predictions.',
    'slope': 'Unpenalized logistic recalibration: logit P(y=1)=intercept+slope*logit(p), clip p to [1e-12,1-1e-12]; maximum likelihood by Newton with line search on logit(p) standardized by its weighted mean and standard deviation, from the identity recalibration (intercept 0, slope 1), at most 80 iterations, until both components of the gradient of the mean negative log-likelihood are below 1e-8; then, with their own budget, up to three undamped Newton steps, each kept unless it raises that loss by more than 1e-14, until a step is below 1e-13: the converged fit, which any solver reproduces to about 1e-9 or better. Null if fewer than 10 positive or 10 negative rows; nearly constant scores (the standard deviation of logit(p) below 1e-3 times max(1, the absolute mean)); singular information at any iterate (the determinant of the standardized mean information below 1e-16); no step among the 25 line-search sizes 1, 1/2, ..., 2^-24 with that loss at most 1e-14 above its current value; a last Newton step still above 1e-6 (a flat direction, such as separation); or no convergence within 80 iterations. Diagnostic, never a score adjustment. calibration_slope_grid and calibration_intercept_grid: the same fit on the grid cells, each cell at its exact midpoint grid_mid with its row and positive counts.',
    'alerts': 'r1020_common episode semantics: frozen artifact threshold; 3600-second per-mint cooldown over the whole report chain: a day starts from carry.cooldown of the previous indexed day (its alerts of its last 3600 s; no older alert can block a row of this day), unlabelled emissions consume cooldown. Episode=(mint,t+60*(first_cross_min+1)); credit only the alert own positive crossing after issuance; an episode that crosses after midnight keeps the first alert the previous day gave it (carry.episodes). Precision excludes unlabelled alerts; recall at >=5 and >=15 minutes; false episodes/day uses calendar exposure. Committed format lacks issued_at: prediction t is the issuance proxy, so lead times are optimistic and are NOT actual delivered-alert performance.',
    'misses': 'Up to 10 labelled negatives with highest p and 10 positives with lowest p, ties by (t,mint,kind); no binary threshold implied.',
    'windows': 'This file covers its UTC day only. Seven calendar days ending on a day, and since the first indexed commitment day, can be composed from the daily files alone (their summaries and partials): missing calendar days add exposure but no observations, and no historical bar is re-queried or re-labelled. Every indexed day through the report day must be reported.',
    'partials': 'Per sample, over labelled SOL rows: sum_p=fsum(p); sum_brier=fsum((p-y)^2); sum_log=fsum(y*log(p)+(1-y)*log1p(-p)) with p clipped as for log loss; band_sum_p=fsum(p) per band; grid={cell: [rows, positives]}; lead_minutes={minutes from first alert to crossing: crash episodes}, "0" = no alert before the crossing; shared_with_previous_day=the same count over the episodes the previous indexed day also had, as that day counted them. A window adds them; an episode that spans midnight is counted once, as the later day saw it.',
    'grid': 'grid_bin(p): below 0.5 the cell of p, from 0.5 the mirrored cell of 1-p (an exact subtraction); each power-of-two interval [2^e,2^(e+1)) for e=-20..-2 is cut into 64 equal parts with math.frexp and power-of-two scaling, and below 2^-20 is one cell: 2434 cells, non-decreasing in p, the same integer on every platform (no logarithm). grid_mid(k) is the cell exact midpoint.',
    'chain': 'previous={day, sha256} of the previous indexed day report file, null on the first day. carry, per sample: cooldown=[mint, t] of the alerts selected in the last 3600 s of the day; episodes=[mint, crossing, first alert t or null] of the crash episodes crossing after the day ends. A day is recomputed from its own rows and the previous report carry; verify.py report --deep recomputes every day from the first.',
    'limits': 'Labels are operator-supplied outcomes, not cryptographically proven market data. Recompute verifies arithmetic and prediction commitments, not the market bars, issuance time, selective feed loss, or profitability.'
}


def epoch(day):
    if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
        raise ValueError('Invalid report day')
    value = calendar.timegm(time.strptime(day, '%Y-%m-%d'))
    if date(value) != day:
        raise ValueError('Invalid report day')
    return value


def date(t):
    return time.strftime('%Y-%m-%d', time.gmtime(t))


def fully_revealed(day, status):
    # Accept the public status or the publisher's progress.json. Both watermarks
    # stop before a withheld hour; do not infer permission from elapsed time.
    epoch(day)
    through = status.get('revealed_through')
    if not isinstance(through, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-2][0-9]:[0-5][0-9]Z', through):
        raise ValueError('Day is not fully revealed: ' + day)
    epoch(through[:10])
    if int(through[11:13]) > 23:
        raise ValueError('Invalid reveal watermark')
    if through < day + 'T23:59Z':
        raise ValueError('Day is not fully revealed: ' + day)


def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def gzip_bytes(body):
    out = io.BytesIO()
    with gzip.GzipFile(fileobj=out, mode='wb', filename='', compresslevel=9, mtime=0) as gz:
        gz.write(body)
    return out.getvalue()


def key(r):
    return r['t'], r['mint'], r['kind']


def in_label_universe(mint):
    """label_day reads native bars only, whose first condition is a mint ending in 'pump' (r1020_forward.NATIVE_FILTER)."""
    return mint.endswith('pump')


def reveal_order(r):
    return r['mint'], r['kind'], r['t']


def label_bytes(rows):
    lines = [HEADER]
    for r in sorted(rows, key=reveal_order):
        lines.append('%d\t%s\t%s\t%s\t%s\t%s\n' % (r['t'], r['mint'], r['kind'],
            '' if r['y'] is None else r['y'], '' if r['first_cross_min'] is None else r['first_cross_min'], r['drop_reason']))
    return gzip_bytes(''.join(lines).encode())


LABEL_TAIL_MAX = 6 + 1 + 2 + max(len(r) for r in DROPS)   # 5 tabs and the newline, y, first_cross_min, the longest reason


def join_labels(predictions, compressed):
    # Code review r7, 4: the body is inflated only up to the size its committed rows allow (a few hundred KB of gzip can
    # inflate to gigabytes), so a crafted label file cannot exhaust the checker's memory.
    limit = len(HEADER) + sum(len(str(p['t'])) + len(p['mint']) + len(p['kind']) + LABEL_TAIL_MAX for p in predictions)
    inflate = zlib.decompressobj(31)
    try:
        raw = inflate.decompress(compressed, limit + 1)
    except zlib.error as exc:
        raise ValueError('Invalid labels compression: %s' % exc)
    if len(raw) > limit:
        raise ValueError('Labels larger than their committed rows allow')
    if not inflate.eof or inflate.unused_data:
        raise ValueError('Invalid labels compression: truncated or trailing data')
    body = raw.decode()
    if not body.startswith(HEADER) or not body.endswith('\n'):
        raise ValueError('Invalid labels header/termination')
    lines = body[len(HEADER):].splitlines()
    ordered = sorted(predictions, key=reveal_order)
    if len(lines) != len(ordered):
        raise ValueError('Labels must cover every committed row exactly once')
    result = []
    for pred, line in zip(ordered, lines):
        fields = line.split('\t')
        if len(fields) != 6 or fields[:3] != [str(pred['t']), pred['mint'], pred['kind']]:
            raise ValueError('Label key/order differs from revealed predictions')
        y, fc, reason = fields[3:]
        if y not in ('', '0', '1') or (fc and not re.fullmatch(r'[0-9]|[1-5][0-9]', fc)):
            raise ValueError('Invalid outcome/crossing')
        if ((y == '1') != bool(fc) or (y == '') != bool(reason)
                or (reason and reason not in DROPS)):
            raise ValueError('Inconsistent label/drop reason')
        if pred['kind'] not in ('sol', 'nonsol_exp'):
            raise ValueError('Unknown committed kind')
        p = float(pred['p']) if pred['p'] != '' else None
        valid_p = p is not None and math.isfinite(p) and 0 <= p <= 1
        if pred['kind'] == 'nonsol_exp':
            if reason != 'experimental_excluded':
                raise ValueError('Experimental outcome must be excluded')
        elif reason == 'experimental_excluded' or (not valid_p) != (reason == 'invalid_probability'):
            raise ValueError('Invalid probability drop attribution')
        elif (valid_p and not in_label_universe(pred['mint'])) != (reason == 'outside_label_universe'):
            raise ValueError('Invalid label-universe drop attribution')
        result.append(dict(t=pred['t'], mint=pred['mint'], kind=pred['kind'], p=p,
                           y=int(y) if y else None, first_cross_min=int(fc) if fc else None, drop_reason=reason))
    return result


def wilson(positive, n):
    if not n:
        return [None, None]
    z = 1.959963984540054
    p = positive / n
    center = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return [max(0., center-half), min(1., center+half)]


def calibration_groups(rows):
    """[(p, rows, positives)] for each distinct score, in ascending p."""
    groups = {}
    for r in rows:
        count, positive = groups.get(r['p'], (0, 0))
        groups[r['p']] = count+1, positive+r['y']
    return [(p, count, positive) for p, (count, positive) in sorted(groups.items())]


def calibration_fit(rows):
    n = len(rows)
    empty = {'intercept': None, 'slope': None}
    if not n or not 0 < sum(r['y'] for r in rows) < n:
        return empty
    # Collapse equal scores; this preserves the likelihood and reduces repeated
    # minute-score work. Arithmetic is shared with the independent verifier.
    return fit_groups(calibration_groups(rows), n)


NEWTON_ITERATIONS = 80   # the main loop's budget; the polish after it has its own three steps


def fit_groups(groups, n, trace=None):
    """The fit of calibration_fit from grouped scores [(p, rows, positives)] in ascending p; n is the rows' total.
    The daily exact fit passes calibration_groups(rows); the grid fit passes (grid_mid(k), rows, positives).
    trace (tests): a list that receives the number of main-loop iterations the stop test took."""
    empty = {'intercept': None, 'slope': None}
    positives = sum(y for _, _, y in groups)
    if positives < 10 or n-positives < 10:
        # Too few events (code review r3, 1): with one or two crashes the fit is near separation, means nothing and
        # stops wherever the gradient first falls below 1e-8, so two platforms could publish different numbers.
        return empty
    def logit(p):
        p = min(1-1e-12, max(1e-12, p))
        return math.log(p) - math.log1p(-p)
    raw = [(logit(p), count, positive) for p, count, positive in groups]
    center = math.fsum(x*c for x,c,y in raw)/n
    scale = math.sqrt(math.fsum((x-center)**2*c for x,c,y in raw)/n)
    if scale < 1e-3*max(1., abs(center)):
        # Nearly constant scores (code reviews r5, 12 and r6, 5): the slope b/scale would carry a 1-ulp libm difference
        # in logit into the compared digits, so the M1 and the publisher could disagree (Mac vs glibc runs: at this
        # threshold the worst difference is 0.003 of the tolerance). Real days have a logit sd of ~1-2.
        return empty
    data = [((x-center)/scale, c/n, y/n) for x,c,y in raw]
    a, b = center, scale
    def loss(a, b):
        return math.fsum(c*(max(a+b*x, 0) + math.log1p(math.exp(-abs(a+b*x)))) - y*(a+b*x) for x,c,y in data)
    def newton(a, b):
        g0 = g1 = h00 = h01 = h11 = 0.
        for x,c,y in data:
            z = max(-700, min(700, a+b*x)); q = 1/(1+math.exp(-z))
            r = c*q-y; w = c*q*(1-q)
            g0 += r; g1 += r*x; h00 += w; h01 += w*x; h11 += w*x*x
        determinant = h00*h11-h01*h01
        if determinant < 1e-16:
            return None
        return max(abs(g0), abs(g1)), (h11*g0-h01*g1)/determinant, (h00*g1-h01*g0)/determinant
    for used in range(1, NEWTON_ITERATIONS+1):
        step = newton(a, b)
        if step is None:
            return empty
        gradient, da, db = step
        if gradient < 1e-8:
            break
        old = loss(a,b); alpha = 1.
        for _ in range(25):
            aa, bb = a-alpha*da, b-alpha*db
            if loss(aa,bb) <= old+1e-14:
                a,b = aa,bb
                break
            alpha *= .5
        else:
            return empty
    else:
        return empty
    # After the stop test, with its own budget (Fable S1, 4; code reviews r3, 1 and r4, 5-6): up to three undamped
    # Newton steps, each kept unless it raises the loss by more than the line search's 1e-14, until a step is below
    # 1e-13. A converging fit squares its error at each step (the first is ~1e-8/prevalence on rare-crash days), so
    # every platform takes the same steps and publishes the converged fit. A last step still above 1e-6 is a flat
    # direction (separation): null.
    for _ in range(3):
        if max(abs(da), abs(db)) <= 1e-13 or not loss(a-da, b-db) <= loss(a, b)+1e-14:
            break
        a, b = a-da, b-db
        step = newton(a, b)
        if step is None:
            return empty
        _, da, db = step
    if max(abs(da), abs(db)) > 1e-6:
        return empty
    if trace is not None:
        trace.append(used)
    return {'intercept': a-b*center/scale, 'slope': b/scale}


def calibration_bin(rows, name):
    n = len(rows); positives = sum(r['y'] for r in rows); expected = math.fsum(r['p'] for r in rows)
    return dict(name=name, n=n, positives=positives, predicted=expected/n if n else None,
                observed=positives/n if n else None, wilson95=wilson(positives,n),
                observed_expected=positives/expected if expected else None)


def select_alerts(rows, threshold, cooldown=()):
    """cooldown: [mint, t] of alerts selected before these rows (the previous indexed day's carry), in ascending t."""
    selected, previous = [], {mint: t for mint, t in cooldown}
    for r in sorted(rows, key=key):
        if r['kind'] != 'sol' or r['p'] is None or not 0 <= r['p'] <= 1 or r['p'] < threshold:
            continue
        if r['t'] - previous.get(r['mint'], -math.inf) >= 3600:
            selected.append(r); previous[r['mint']] = r['t']
    return selected


def alerts(rows, selected, exposure):
    keys = {key(r) for r in rows}
    emitted = [r for r in selected if key(r) in keys]
    true = [r for r in selected if r['y'] == 1]
    first = {}
    for r in true:
        episode = r['mint'], r['t']+60*(r['first_cross_min']+1)
        first[episode] = min(first.get(episode, r['t']), r['t'])
    episodes = {(r['mint'], r['t']+60*(r['first_cross_min']+1)) for r in rows if r['y'] == 1}
    lead = [(cross-first[(mint,cross)])/60 for mint,cross in sorted(episodes) if (mint,cross) in first]
    count = len(emitted); true_n = sum(r['y'] == 1 for r in emitted); n_ep = len(episodes)
    five, fifteen = sum(x >= 5 for x in lead), sum(x >= 15 for x in lead)
    return dict(n_alerts=count, true_alerts=true_n, false_alerts=count-true_n,
                precision=true_n/count if count else None, crash_episodes=n_ep,
                warned_ge_5m=five, warned_ge_15m=fifteen, recall_ge_5m=five/n_ep if n_ep else None,
                recall_ge_15m=fifteen/n_ep if n_ep else None, false_episodes_per_day=(count-true_n)/exposure,
                median_first_notice_min=statistics.median(lead) if lead else None,
                cooldown_s=3600, exposure_days=exposure)


def summary(rows, selected, exposure):
    sol = [r for r in rows if r['kind'] == 'sol']
    labelled = [r for r in sol if r['y'] is not None]
    n = len(labelled); positive = sum(r['y'] for r in labelled)
    prevalence = positive/n if n else None
    brier = math.fsum((r['p']-r['y'])**2 for r in labelled)/n if n else None
    base = prevalence*(1-prevalence) if n else None
    ap, tp, count = 0., 0, 0
    for _, group in itertools.groupby(sorted(labelled, key=lambda r: -r['p']), key=lambda r: r['p']):
        group = list(group); hits = sum(r['y'] for r in group)
        tp += hits; count += len(group); ap += hits*tp/count
    ap = ap/positive if positive else None
    clipped = [(r['y'], min(1-1e-12,max(1e-12,r['p']))) for r in labelled]
    bins = [[],[],[]]
    for r in labelled:
        bins[0 if r['p'] < .02 else 1 if r['p'] < .10 else 2].append(r)
    deciles = [[] for _ in range(10)]
    for rank,r in enumerate(sorted(labelled,key=lambda r: (r['p'],)+key(r))):
        deciles[10*rank//n].append(r)
    fit = calibration_fit(labelled)
    def miss(r):
        return {k:r[k] for k in ('mint','t','p','y','first_cross_min')}
    expected = math.fsum(r['p'] for r in labelled)
    return dict(n_committed=len(sol), n_labelled=n, n_dropped=len(sol)-n,
        dropped_by_reason=dict(sorted(Counter(r['drop_reason'] for r in sol if r['y'] is None).items())),
        n_experimental=sum(r['kind']=='nonsol_exp' for r in rows), positives=positive,
        distinct_mints=len({r['mint'] for r in labelled}), crash_rate=prevalence,
        brier=brier, baseline_brier=base, brier_skill=1-brier/base if base else None,
        log_loss=-math.fsum(y*math.log(p)+(1-y)*math.log1p(-p) for y,p in clipped)/n if n else None,
        ap=ap, ap_over_prevalence=ap/prevalence if prevalence else None,
        mean_prediction=expected/n if n else None, observed_expected=positive/expected if expected else None,
        calibration_slope=fit['slope'], calibration_intercept=fit['intercept'],
        bands=[calibration_bin(b,name) for b,name in zip(bins,('lower','watch','high'))],
        deciles=[calibration_bin(b,str(i+1)) for i,b in enumerate(deciles)], alerts=alerts(labelled,selected,exposure),
        misses={'confident_noncrashes':[miss(r) for r in sorted((r for r in labelled if not r['y']),key=lambda r: (-r['p'],)+key(r))[:10]],
                'unwarned_crashes':[miss(r) for r in sorted((r for r in labelled if r['y']),key=lambda r: (r['p'],)+key(r))[:10]]})


def coin_hours(rows):
    result, seen = [], set()
    for r in sorted(rows, key=key):
        k = r['mint'], r['kind'], r['t']//3600
        if k not in seen:
            seen.add(k); result.append(r)
    return result


def _grid_low(x):
    if x < 2.0**-(GRID_OCT+1):
        return 0
    mant, e = math.frexp(x)                      # x = mant*2**e, 0.5 <= mant < 1, e in [-GRID_OCT, -1]
    return 1 + (e+GRID_OCT)*GRID_S + int((mant-.5)*(2*GRID_S))


def grid_bin(p):
    """The public grid cell of a score in [0, 1]: exact float arithmetic only, non-decreasing in p.
    Below 0.5 it cuts p; from 0.5 it cuts 1-p (an exact subtraction there)."""
    if p < .5:
        return _grid_low(p)
    q = 1.0 - p
    return 2*GRID_LOW - 1 - (GRID_OCT*GRID_S if q == .5 else _grid_low(q))


def _grid_low_mid(j):
    if j == 0:
        return 2.0**-(GRID_OCT+2)
    e, s = (j-1)//GRID_S - GRID_OCT, (j-1) % GRID_S
    return math.ldexp(1 + (s+.5)/GRID_S, e-1)


def grid_mid(k):
    """The exact midpoint of grid cell k."""
    return _grid_low_mid(k) if k < GRID_LOW else 1.0 - _grid_low_mid(2*GRID_LOW - 1 - k)


def grid_scores(grid, n, positives):
    """AP and the logistic recalibration of the scores grouped on the grid; grid = {cell: [rows, positives]}."""
    ap, tp, count = 0., 0, 0
    for k in sorted(grid, reverse=True):
        rows, hits = grid[k]
        tp += hits; count += rows; ap += hits*tp/count
    ap = ap/positives if positives else None
    fit = {'intercept': None, 'slope': None}
    if n and 0 < positives < n:
        fit = fit_groups([(grid_mid(k), grid[k][0], grid[k][1]) for k in sorted(grid)], n)
    return dict(ap_grid=ap, ap_grid_over_prevalence=ap/(positives/n) if positives else None,
                calibration_slope_grid=fit['slope'], calibration_intercept_grid=fit['intercept'])


EMPTY_CARRY = {'cooldown': [], 'episodes': []}


def crossing(r):
    return r['t'] + 60*(r['first_cross_min']+1)


def day_sample(rows, day, threshold, carry):
    """One sample of one day: (summary, partials, carry for the next indexed day).

    The summary is v1's summary() unchanged, with the alerts selected from carry['cooldown'] on and the previous
    day's first alerts of the episodes still open at midnight (carry['episodes']) given to it as rows it credits
    exactly as the all-history computation did."""
    hi = epoch(day) + 86400
    selected = select_alerts(rows, threshold, carry['cooldown'])
    carried = [dict(t=first, mint=mint, kind='sol', p=None, y=1, first_cross_min=(cross-first)//60-1, drop_reason='')
               for mint, cross, first in carry['episodes'] if first is not None]
    daily = summary(rows, carried+selected, 1.0)     # float exposure, as periods() passes it
    labelled = [r for r in rows if r['kind'] == 'sol' and r['y'] is not None]
    grid, bands = {}, [[], [], []]
    for r in labelled:
        cell = grid.setdefault(grid_bin(r['p']), [0, 0])
        cell[0] += 1; cell[1] += r['y']
        bands[0 if r['p'] < .02 else 1 if r['p'] < .10 else 2].append(r['p'])
    clipped = [(r['y'], min(1-1e-12, max(1e-12, r['p']))) for r in labelled]
    before = {(mint, cross): first for mint, cross, first in carry['episodes']}
    first = {e: t for e, t in before.items() if t is not None}
    for r in selected:
        if r['y'] == 1:
            e = r['mint'], crossing(r)
            first[e] = min(first.get(e, r['t']), r['t'])
    episodes = {(r['mint'], crossing(r)) for r in labelled if r['y'] == 1}
    lead = Counter((cross-first[(mint, cross)])//60 if (mint, cross) in first else 0 for mint, cross in episodes)
    shared = Counter((e[1]-before[e])//60 if before[e] is not None else 0 for e in episodes if e in before)
    daily.update(grid_scores(grid, daily['n_labelled'], daily['positives']))
    partials = dict(sum_p=math.fsum(r['p'] for r in labelled),
                    sum_brier=math.fsum((r['p']-r['y'])**2 for r in labelled),
                    sum_log=math.fsum(y*math.log(p)+(1-y)*math.log1p(-p) for y, p in clipped),
                    band_sum_p=[math.fsum(b) for b in bands],
                    grid={str(k): grid[k] for k in sorted(grid)},
                    lead_minutes={str(k): lead[k] for k in sorted(lead)},
                    shared_with_previous_day={str(k): shared[k] for k in sorted(shared)})
    carry_out = dict(cooldown=sorted([r['mint'], r['t']] for r in selected if r['t'] >= hi-3600),
                     episodes=sorted(([mint, cross, first.get((mint, cross))] for mint, cross in episodes if cross > hi),
                                     key=lambda x: x[:2]))
    return daily, partials, carry_out


def day_report(rows, day, threshold, carry=None):
    """(periods, partials, carry) of a v2 daily report from the day's rows only and carry = the previous indexed
    day's report carry (None on the first day)."""
    daily, partials, carry_out = dict(days=[day], start=day, end=day), {}, {}
    for sample, sampled in (('minute', rows), ('coin_hour', coin_hours(rows))):
        daily[sample], partials[sample], carry_out[sample] = day_sample(
            sampled, day, threshold, carry[sample] if carry else EMPTY_CARRY)
    return {'daily': daily}, partials, carry_out


def check_day(report, rows, carry):
    """Recompute a report's daily numbers from its rows and the previous report's carry; raise on any difference."""
    periods, partials, carry_out = day_report(rows, report['day'], report['alert_policy']['threshold'], carry)
    compare(report['periods'], periods, 'periods')
    compare(report['partials'], partials, 'partials')
    compare(report['carry'], carry_out, 'carry')
    return carry_out


def periods(history, day, threshold):
    """v1: every day's rows in memory (about 0.9 KB per row). The tests' oracle only; nothing in production calls it."""
    days = sorted(history)
    if not days or days[-1] != day:
        raise ValueError('History must end at report day')
    all_rows = [r for d in days for r in history[d]]
    hour_rows = coin_hours(all_rows)
    selected = select_alerts(all_rows,threshold); hourly_selected = select_alerts(hour_rows,threshold)
    windows = {'daily':day, 'rolling_7d':date(max(epoch(days[0]),epoch(day)-6*86400)), 'since_launch':days[0]}
    result = {}
    for name,start in windows.items():
        included = [d for d in days if start <= d <= day]
        lo,hi = epoch(start),epoch(day)+86400
        exposure = (hi-lo)/86400
        result[name] = dict(days=included, start=start, end=day,
            minute=summary([r for r in all_rows if lo<=r['t']<hi],selected,exposure),
            coin_hour=summary([r for r in hour_rows if lo<=r['t']<hi],hourly_selected,exposure))
    return result


def headline(report):
    s = report['periods']['daily']['minute']
    return {k:s[k] for k in ('n_committed','n_labelled','n_dropped','crash_rate','brier','brier_skill','ap','ap_over_prevalence')}


def markdown(report):
    s = headline(report)
    def number(x):
        return 'undefined' if x is None else '%.6g' % x
    return ('# Daily accuracy — %s UTC\n\n'
        '%d committed SOL predictions; %d labelled; %d dropped, %d of them outside the label universe (mint not ending in "pump"). Experimental scores are excluded.\n\n'
        'Crash rate: %s. Brier: %s. Brier skill: %s. AP: %s. AP/prevalence: %s.\n\n'
        'These are minute-level observations and are correlated. The JSON also reports the first committed prediction per coin-hour, calibration bands and deciles, descriptive Wilson intervals, alert episodes, misses, and the partial sums from which seven-day and since-launch windows can be computed.\n\n'
        'Alert lead times use prediction time because commitments do not contain issuance time; they are optimistic proxies. Labels are operator-supplied; verification checks arithmetic and committed predictions, not market data. See definitions in the JSON.\n\n'
        '[JSON](%s.json) · [Daily report](../report.html)\n\n'
        'Odds, not guarantees. Not financial advice.\n' % (report['day'],s['n_committed'],s['n_labelled'],s['n_dropped'],
            report['periods']['daily']['minute']['dropped_by_reason'].get('outside_label_universe',0),
            number(s['crash_rate']),number(s['brier']),number(s['brier_skill']),number(s['ap']),number(s['ap_over_prevalence']),report['day'])).encode()


def compare(actual, expected, path='report'):
    """Strict shape and values, tolerant only to floating-point roundoff."""
    if isinstance(expected,dict):
        if not isinstance(actual,dict) or actual.keys()!=expected.keys():
            raise ValueError('Field mismatch: '+path)
        for k,v in expected.items():
            compare(actual[k],v,path+'.'+k)
    elif isinstance(expected,list):
        if not isinstance(actual,list) or len(actual)!=len(expected):
            raise ValueError('List mismatch: '+path)
        for i,v in enumerate(expected):
            compare(actual[i],v,path+'[%d]'%i)
    elif isinstance(expected,float):
        if isinstance(actual,bool) or not isinstance(actual,(float,int)) or not math.isfinite(actual) or not math.isclose(actual,expected,rel_tol=1e-9,abs_tol=1e-11):
            raise ValueError('Numeric mismatch: '+path)
    elif type(actual) is not type(expected) or actual!=expected:
        raise ValueError('Value mismatch: '+path)


def validate_report(report, index, status):
    """Strict v2 structural schema; recomputation (check_day) supplies semantic validation."""
    fields = {'schema','day','code','chain_range','definitions','alert_policy','label_files','previous','periods',
              'partials','carry'}
    if not isinstance(report,dict) or set(report)!=fields or report['schema']!=SCHEMA:
        raise ValueError('Invalid daily report schema')
    day = report['day']; fully_revealed(day,status)
    if day not in index:
        raise ValueError('Report day is absent from chain')
    compare(report['chain_range'],{k:v for k,v in index[day].items() if k!='line'},'chain_range')
    compare(report['definitions'],DEFINITIONS,'definitions')
    code = report['code']
    if not isinstance(code,dict) or set(code)!={'git','sha256'} or not isinstance(code['git'],str) or not re.fullmatch(r'[0-9a-f]{40}(?:-dirty)?|unknown',code['git']):
        raise ValueError('Invalid code version')
    names = {'air/track_report.py','air/r1020_forward.py','cloud/tools/commitments.py','track/site/verify.py','track/site/report_metrics.py'}
    if not isinstance(code['sha256'],dict) or set(code['sha256'])!=names:
        raise ValueError('Missing code hashes')
    for digest in code['sha256'].values():
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Invalid code hash')
    policy = report['alert_policy']
    if not isinstance(policy,dict) or set(policy)!={'model','threshold','meta_sha256'} or policy['model'] not in ('B','BF'):
        raise ValueError('Invalid alert policy')
    t = policy['threshold']
    if isinstance(t,bool) or not isinstance(t,(float,int)) or not math.isfinite(t) or not 0<t<1 or not re.fullmatch('[0-9a-f]{64}',policy['meta_sha256']):
        raise ValueError('Invalid alert threshold/provenance')
    if not isinstance(report['label_files'],dict) or set(report['label_files'])!={'%02d'%h for h in range(24)}:
        raise ValueError('Need all 24 label files')
    for digest in report['label_files'].values():
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):
            raise ValueError('Invalid labels hash')
    earlier = [d for d in index if d<day]
    previous = report['previous']
    if not earlier:
        if previous is not None:
            raise ValueError('The first indexed day has no previous report')
    elif (not isinstance(previous,dict) or set(previous)!={'day','sha256'} or previous['day']!=max(earlier)
            or not isinstance(previous['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',previous['sha256'])):
        raise ValueError('previous must name the previous indexed day report and its sha256')
    # Validate all metric keys/types, even on the publisher's path; the numbers
    # themselves are recomputed by check_day (publisher and verify.py).
    expected_periods, expected_partials, expected_carry = day_report([],day,t)
    if not isinstance(report['periods'],dict) or set(report['periods'])!={'daily'}:
        raise ValueError('Invalid periods')
    actual, expected = report['periods']['daily'], expected_periods['daily']
    if not isinstance(actual,dict) or set(actual)!=set(expected):
        raise ValueError('Invalid period structure')
    for k in ('days','start','end'):
        compare(actual[k],expected[k],'daily.'+k)
    for name, part in (('partials',expected_partials),('carry',expected_carry)):
        if not isinstance(report[name],dict) or set(report[name])!=set(part):
            raise ValueError('Invalid '+name)
    for sample in ('minute','coin_hour'):
        s = actual[sample]
        validate_summary(s,expected[sample])
        validate_partials(report['partials'][sample],s)
        validate_carry(report['carry'][sample],day)


def _count(value, positive=False):
    if isinstance(value,bool) or not isinstance(value,int) or value<(1 if positive else 0):
        raise ValueError('Invalid count')
    return value


def _finite(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Invalid partial sum')
    return value


def _counts(table, keys):
    """{str(int): positive count} with canonical integer keys in keys."""
    if not isinstance(table,dict):
        raise ValueError('Invalid count table')
    for k,v in table.items():
        if not isinstance(k,str) or not re.fullmatch('0|[1-9][0-9]*',k) or int(k) not in keys:
            raise ValueError('Invalid count key')
        _count(v,True)
    return table


def validate_partials(p, s):
    """Partials of one sample, and their integer agreement with that sample's summary s."""
    keys = {'sum_p','sum_brier','sum_log','band_sum_p','grid','lead_minutes','shared_with_previous_day'}
    if not isinstance(p,dict) or set(p)!=keys:
        raise ValueError('Invalid partials')
    for k in ('sum_p','sum_brier','sum_log'):
        _finite(p[k])
    if not isinstance(p['band_sum_p'],list) or len(p['band_sum_p'])!=3:
        raise ValueError('Invalid band sums')
    for value in p['band_sum_p']:
        _finite(value)
    grid = p['grid']
    if not isinstance(grid,dict):
        raise ValueError('Invalid grid')
    rows = hits = 0
    for k,cell in grid.items():
        if not isinstance(k,str) or not re.fullmatch('0|[1-9][0-9]*',k) or int(k)>=GRID_CELLS:
            raise ValueError('Invalid grid cell')
        if not isinstance(cell,list) or len(cell)!=2:
            raise ValueError('Invalid grid cell counts')
        n, y = _count(cell[0],True), _count(cell[1])
        if y>n:
            raise ValueError('Invalid grid cell counts')
        rows += n; hits += y
    lead = _counts(p['lead_minutes'],range(61))
    _counts(p['shared_with_previous_day'],range(61))
    if rows!=s['n_labelled'] or hits!=s['positives'] or sum(lead.values())!=s['alerts']['crash_episodes']:
        raise ValueError('Partials disagree with the summary')


def validate_carry(c, day):
    """Carry of one sample: the alerts of the last 3600 s and the episodes crossing after the day's end."""
    hi = epoch(day)+86400
    if not isinstance(c,dict) or set(c)!={'cooldown','episodes'}:
        raise ValueError('Invalid carry')
    cooldown, episodes = c['cooldown'], c['episodes']
    if not isinstance(cooldown,list) or not isinstance(episodes,list):
        raise ValueError('Invalid carry')
    for item in cooldown:
        if (not isinstance(item,list) or len(item)!=2 or not isinstance(item[0],str)
                or not hi-3600<=_count(item[1])<hi or item[1]%60):
            raise ValueError('Invalid carried alert')
    for item in episodes:
        if (not isinstance(item,list) or len(item)!=3 or not isinstance(item[0],str)
                or not hi<_count(item[1])<=hi+3600 or item[1]%60
                or (item[2] is not None and (not item[1]-3600<=_count(item[2])<min(item[1],hi) or item[2]%60))):
            raise ValueError('Invalid carried episode')
    if (cooldown!=sorted(cooldown) or len({tuple(x) for x in cooldown})!=len(cooldown)
            or episodes!=sorted(episodes,key=lambda x: x[:2]) or len({tuple(x[:2]) for x in episodes})!=len(episodes)):
        raise ValueError('Carry is not sorted and unique')


def validate_summary(actual, template):
    if not isinstance(actual,dict) or set(actual)!=set(template):
        raise ValueError('Invalid summary fields')
    def numeric(value, integer=False):
        if value is None and not integer:
            return
        if isinstance(value,bool) or not isinstance(value,int if integer else (int,float)) or not math.isfinite(value):
            raise ValueError('Invalid metric number')
        if integer and value<0:
            raise ValueError('Negative metric count')
    for k,v in template.items():
        if k in ('bands','deciles'):
            if not isinstance(actual[k],list) or len(actual[k])!=len(v):
                raise ValueError('Invalid calibration bins')
            for a,b in zip(actual[k],v):
                if not isinstance(a,dict) or set(a)!=set(b) or a['name']!=b['name']:
                    raise ValueError('Invalid calibration bin')
                for field in ('n','positives'):
                    numeric(a[field],True)
                for field in ('predicted','observed','observed_expected'):
                    numeric(a[field])
                if not isinstance(a['wilson95'],list) or len(a['wilson95'])!=2:
                    raise ValueError('Invalid Wilson interval')
                for value in a['wilson95']:
                    numeric(value)
        elif k=='alerts':
            if not isinstance(actual[k],dict) or set(actual[k])!=set(v):
                raise ValueError('Invalid alerts')
            for field,value in v.items():
                numeric(actual[k][field],isinstance(value,int))
        elif k=='dropped_by_reason':
            if not isinstance(actual[k],dict) or set(actual[k])-(set(DROPS)-{'experimental_excluded'}):
                raise ValueError('Invalid drop reasons')
            for value in actual[k].values():
                numeric(value,True)
        elif k=='misses':
            if not isinstance(actual[k],dict) or set(actual[k])!=set(v):
                raise ValueError('Invalid misses')
            for direction, rows in actual[k].items():
                if not isinstance(rows,list) or len(rows)>10:
                    raise ValueError('Invalid misses list')
                for row in rows:
                    if not isinstance(row,dict) or set(row)!={'mint','t','p','y','first_cross_min'} or not isinstance(row['mint'],str):
                        raise ValueError('Invalid miss')
                    for field in ('t','y'):
                        numeric(row[field],True)
                    numeric(row['p']); numeric(row['first_cross_min'])
                    if row['y'] != (1 if direction=='unwarned_crashes' else 0):
                        raise ValueError('Wrong miss direction')
        else:
            numeric(actual[k],isinstance(v,int))
    if actual['n_committed']!=actual['n_labelled']+actual['n_dropped'] or sum(actual['dropped_by_reason'].values())!=actual['n_dropped']:
        raise ValueError('Inconsistent report counts')
