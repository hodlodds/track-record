"""Public daily-report v1 arithmetic and validation; Python 3.9+, stdlib only.

No data loading or outcome computation on import. The Air calls the sealed
labeler before invoking these routines; public verification uses published labels.
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

SCHEMA = 'hodlodds.daily_report.v1'
HEADER = 't\tmint\tkind\ty\tfirst_cross_min\tdrop_reason\n'
DROPS = ('no_reference_bar', 'no_valid_reference_bar', 'uncovered_market_horizon',
         'invalid_horizon_bar', 'invalid_probability', 'experimental_excluded')
DEFINITIONS = {
    'label': 'air/r1020_forward.label_day: last native bar in [t-300,t-60] with finite positive close and reserves; y=1 iff a token close in [t,t+3540] is <=0.10 times reference close. All 60 market-wide native minutes required. Sparse token trading is allowed; invalid future closes censor. first_cross_min is start-stamped offset 0..59; crossing completes one minute later.',
    'universe': 'Committed sol rows only. nonsol_exp counted separately, never labelled. Invalid/missing probabilities dropped. Missing horizons are dropped, never negative.',
    'sampling': 'minute: all committed rows; coin_hour: first committed minute per mint and UTC hour, chosen before dropping unlabelled rows. These samples remain correlated.',
    'scores': 'crash_rate=mean(y); Brier=mean((p-y)^2); baseline_brier=crash_rate*(1-crash_rate); Brier skill=1-Brier/baseline_brier, null for constant outcomes. Aggregates use their own pooled base rate. Log loss uses natural logs and p clipped to [1e-12,1-1e-12].',
    'ranking': 'AP=sum over descending equal-score groups of (new positives / total positives)*(cumulative positives / cumulative rows); AP/prevalence=AP/crash_rate; both null without positives.',
    'calibration': 'Bands: lower [0,.02), watch [.02,.10), high [.10,1]. Deciles: ascending rank by (p,t,mint,kind), group floor(10*rank/n), ties may split. O/E=sum(y)/sum(p), null if sum(p)=0. Wilson 95% binomial interval z=1.959963984540054; descriptive only, does not adjust for correlated predictions.',
    'slope': 'Unpenalized logistic recalibration: logit(y)=intercept+slope*logit(p), clip p to [1e-12,1-1e-12], Newton with line search (80 iterations); null if single class, constant scores, singular information, or no convergence. Diagnostic, never a score adjustment.',
    'alerts': 'r1020_common episode semantics: frozen artifact threshold; 3600-second per-mint cooldown selected across all report history before slicing (unlabelled emissions consume cooldown). Episode=(mint,t+60*(first_cross_min+1)); credit only the alert own positive crossing after issuance. Precision excludes unlabelled alerts; recall at >=5 and >=15 minutes; false episodes/day uses calendar exposure. Committed format lacks issued_at: prediction t is the issuance proxy, so lead times are optimistic and are NOT actual delivered-alert performance.',
    'misses': 'Up to 10 labelled negatives with highest p and 10 positives with lowest p, ties by (t,mint,kind); no binary threshold implied.',
    'windows': 'Daily UTC day; rolling seven calendar days ending on day; since first indexed commitment day. Only indexed days are listed; missing calendar days add exposure but no observations. Every indexed day through report day must be reported. No re-query of historical bars.',
    'limits': 'Labels are operator-supplied outcomes, not cryptographically proven market data. Recompute verifies arithmetic and prediction commitments, not Ana bars, issuance time, selective feed loss, or profitability.'
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


def reveal_order(r):
    return r['mint'], r['kind'], r['t']


def label_bytes(rows):
    lines = [HEADER]
    for r in sorted(rows, key=reveal_order):
        lines.append('%d\t%s\t%s\t%s\t%s\t%s\n' % (r['t'], r['mint'], r['kind'],
            '' if r['y'] is None else r['y'], '' if r['first_cross_min'] is None else r['first_cross_min'], r['drop_reason']))
    return gzip_bytes(''.join(lines).encode())


def join_labels(predictions, compressed):
    body = gzip.decompress(compressed).decode()
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


def calibration_fit(rows):
    n = len(rows)
    empty = {'intercept': None, 'slope': None}
    if not n or not 0 < sum(r['y'] for r in rows) < n:
        return empty
    # Collapse equal scores; this preserves the likelihood and reduces repeated
    # minute-score work. Arithmetic is shared with the independent verifier.
    groups = {}
    for r in rows:
        count, positive = groups.get(r['p'], (0, 0))
        groups[r['p']] = count+1, positive+r['y']
    def logit(p):
        p = min(1-1e-12, max(1e-12, p))
        return math.log(p) - math.log1p(-p)
    raw = [(logit(p), count, positive) for p, (count, positive) in sorted(groups.items())]
    center = math.fsum(x*c for x,c,y in raw)/n
    scale = math.sqrt(math.fsum((x-center)**2*c for x,c,y in raw)/n)
    if scale < 1e-12:
        return empty
    data = [((x-center)/scale, c/n, y/n) for x,c,y in raw]
    a, b = center, scale
    def loss(a, b):
        return math.fsum(c*(max(a+b*x, 0) + math.log1p(math.exp(-abs(a+b*x)))) - y*(a+b*x) for x,c,y in data)
    for _ in range(80):
        g0 = g1 = h00 = h01 = h11 = 0.
        for x,c,y in data:
            z = max(-700, min(700, a+b*x)); q = 1/(1+math.exp(-z))
            r = c*q-y; w = c*q*(1-q)
            g0 += r; g1 += r*x; h00 += w; h01 += w*x; h11 += w*x*x
        determinant = h00*h11-h01*h01
        if determinant < 1e-16:
            return empty
        if max(abs(g0), abs(g1)) < 1e-8:
            return {'intercept': a-b*center/scale, 'slope': b/scale}
        da, db = (h11*g0-h01*g1)/determinant, (h00*g1-h01*g0)/determinant
        old = loss(a,b); alpha = 1.
        for _ in range(25):
            aa, bb = a-alpha*da, b-alpha*db
            if loss(aa,bb) <= old+1e-14:
                a,b = aa,bb
                break
            alpha *= .5
        else:
            return empty
    return empty


def calibration_bin(rows, name):
    n = len(rows); positives = sum(r['y'] for r in rows); expected = math.fsum(r['p'] for r in rows)
    return dict(name=name, n=n, positives=positives, predicted=expected/n if n else None,
                observed=positives/n if n else None, wilson95=wilson(positives,n),
                observed_expected=positives/expected if expected else None)


def select_alerts(rows, threshold):
    selected, previous = [], {}
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


def periods(history, day, threshold):
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
        '%d committed SOL predictions; %d labelled; %d dropped. Experimental scores are excluded.\n\n'
        'Crash rate: %s. Brier: %s. Brier skill: %s. AP: %s. AP/prevalence: %s.\n\n'
        'These are minute-level observations and are correlated. The JSON also reports the first committed prediction per coin-hour, calibration bands and deciles, descriptive Wilson intervals, alert episodes, misses, seven-day and since-launch aggregates.\n\n'
        'Alert lead times use prediction time because commitments do not contain issuance time; they are optimistic proxies. Labels are operator-supplied; verification checks arithmetic and committed predictions, not market data. See definitions in the JSON.\n\n'
        '[JSON](%s.json) · [Daily report](../report.html)\n\n'
        'Odds, not guarantees. Not financial advice.\n' % (report['day'],s['n_committed'],s['n_labelled'],s['n_dropped'],
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
    """Strict v1 structural schema; recomputation supplies semantic validation."""
    fields = {'schema','day','code','chain_range','definitions','alert_policy','label_files','periods'}
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
    # Validate all aggregate metric keys/types, even on the publisher's O(new)
    # path. The public CLI independently recomputes their numerical values.
    expected_periods = periods({d:[] for d in index if d<=day},day,t)
    if not isinstance(report['periods'],dict) or set(report['periods'])!=set(expected_periods):
        raise ValueError('Invalid periods')
    for name, expected in expected_periods.items():
        actual = report['periods'][name]
        if not isinstance(actual,dict) or set(actual)!=set(expected):
            raise ValueError('Invalid period structure')
        for k in ('days','start','end'):
            compare(actual[k],expected[k],name+'.'+k)
        for sample in ('minute','coin_hour'):
            validate_summary(actual[sample],expected[sample])


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
            if not isinstance(actual[k],dict) or set(actual[k])-set(DROPS[:-1]):
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
