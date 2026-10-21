#!/usr/bin/env python3
"""Verify HodlOdds public commitments. Python >=3.9, stdlib + OpenSSL."""
import argparse
import calendar
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

RAW = 'https://raw.githubusercontent.com/hodlodds/track-record/main/'
GENESIS = '0' * 64
# r100 (Fable audit 20): an empty commitment (rows=0) means nothing was published for that minute, not that
# this mint was left out of a published minute.
EMPTY_MINUTE = "No predictions were published for this minute (an empty commitment, for example during a feed pause). Check the answer's as_of minute."


def absent_message(entry):
    return EMPTY_MINUTE if entry['rows'] == 0 and not entry.get('paused') else 'Mint is absent from the verified minute'


LINE = re.compile(r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z) rows=(0|[1-9]\d*|paused) sha256=([a-f0-9]{64}) chain=([a-f0-9]{64})')
REVEAL_GRACE_S = 7200  # a due hour may still be waiting for the hourly publication run
DOWNGRADE = {'high': 'medium', 'medium': 'low', 'low': 'low', 'experimental': 'experimental'}
INDEX = re.compile(r'(\d{4}-\d{2}-\d{2}) first=(\S+) last=(\S+) lines=([1-9]\d*) prev=([a-f0-9]{64}) head=([a-f0-9]{64})')


def iso(t):
    return time.strftime('%Y-%m-%dT%H:%MZ', time.gmtime(t))


def minute(text):
    if not isinstance(text, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z', text):
        raise ValueError('Use a UTC minute: YYYY-MM-DDTHH:MMZ')
    t = calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%MZ'))
    if iso(t) != text:
        raise ValueError('Invalid UTC minute')
    return t


def sha(data):
    return hashlib.sha256(data).hexdigest()


def parse_chain(data, previous=GENESIS, after=None):
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    if data and not data.endswith('\n'):
        raise ValueError('Incomplete chain line')
    result = []
    for line in data.split('\n')[:-1]:
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError('Malformed chain line')
        stamp, count, block, link = match.groups()
        t = minute(stamp)
        if after is not None and t <= after:
            raise ValueError('Chain minutes must strictly increase')
        if sha(('%s %s %s %s' % (previous, stamp, count, block)).encode()) != link:
            raise ValueError('Broken chain link at ' + stamp)
        result.append(dict(minute=stamp, t=t, rows=0 if count == 'paused' else int(count), paused=count == 'paused', sha256=block, chain=link, line=line))
        previous, after = link, t
    return result


def index_line(day, entries, previous):
    return '%s first=%s last=%s lines=%d prev=%s head=%s\n' % (
        day, entries[0]['minute'], entries[-1]['minute'], len(entries), previous, entries[-1]['chain'])


def parse_index(data):
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    if not data or not data.endswith('\n'):
        raise ValueError('Missing or incomplete chain index')
    result = {}
    for line in data.split('\n')[:-1]:
        match = INDEX.fullmatch(line)
        if not match:
            raise ValueError('Malformed chain index')
        day, first, last, count, previous, head = match.groups()
        if first[:10] != day or last[:10] != day or minute(first) > minute(last):
            raise ValueError('Invalid index day bounds')
        if result and day <= next(reversed(result)):
            raise ValueError('Index days must strictly increase')
        result[day] = dict(day=day, first=first, last=last, lines=int(count), prev=previous,
                           head=head, line=line + '\n')
    return result


def check_day(data, record):
    entries = parse_chain(data, record['prev'])
    if (not entries or any(e['minute'][:10] != record['day'] for e in entries)
            or index_line(record['day'], entries, record['prev']) != record['line']):
        raise ValueError('Day file differs from chain index: ' + record['day'])
    return entries


def token_index(data, day):
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    names = data.split('\n')[:-1] if data else []
    if (data and not data.endswith('\n')) or names != sorted(set(names)):
        raise ValueError('Invalid TSA index')
    for name in names:
        if not re.fullmatch(r'\d{4}', name):
            raise ValueError('Invalid TSA basename')
        minute(day + 'T' + name[:2] + ':' + name[2:] + 'Z')
    return ['tsa/%s/%s.tsr' % (day, name) for name in names]


def canonical_lines(data):
    if isinstance(data, bytes):
        data = data.decode('utf-8')
    if data and not data.endswith('\n'):
        raise ValueError('Incomplete canonical line')
    rows, seen = [], set()
    for line in data.split('\n')[:-1]:
        fields = line.split('\t')
        if len(fields) != 5:
            raise ValueError('Canonical lines need five fields')
        t, mint, kind, p, level = fields
        if not re.fullmatch(r'0|[1-9]\d*', t) or int(t) % 60 or not mint or not kind or '\r' in line:
            raise ValueError('Invalid canonical row')
        if p:
            value = json.loads(p)
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError('Invalid canonical number')
        key = (int(t), mint, kind)
        if key in seen:
            raise ValueError('Duplicate canonical row')
        seen.add(key)
        rows.append(dict(t=int(t), mint=mint, kind=kind, p=p, level=level, line=line))
    return rows


def block_for(rows, t):
    selected = sorted((r for r in rows if r['t'] == t), key=lambda r: (r['mint'], r['kind']))
    return selected, ''.join(r['line'] + '\n' for r in selected).encode()


def check_block(rows, entry):
    selected, body = block_for(rows, entry['t'])
    if len(selected) != entry['rows'] or sha(body) != entry['sha256']:
        raise ValueError('Revealed row count or fingerprint does not match ' + entry['minute'])
    return selected


def parse_answer(answer):
    if not isinstance(answer, dict) or answer.get('schema') not in ('hodlodds.answer.v1', 'hodlodds.board.v1'):
        raise ValueError('Expected a saved hodlodds.answer.v1 or hodlodds.board.v1 answer')
    t = answer.get('as_of')
    if isinstance(t, bool) or not isinstance(t, int) or t < 0 or t % 60:
        raise ValueError('Answer as_of must be epoch seconds at a minute boundary')
    rows = [answer.get('row')] if answer['schema'] == 'hodlodds.answer.v1' else answer.get('rows')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Answer contains no predictions')
    result = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('mint'), str) or not row['mint']:
            raise ValueError('Missing mint')
        experimental = 'experimental_score' in row
        p = row.get('experimental_score' if experimental else 'p_crash_60m')
        conf = row.get('confidence') or {}
        level = conf.get('level', '') if isinstance(conf, dict) else ''
        if isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError('Missing or invalid prediction number')
        if not isinstance(level, str):
            raise ValueError('Invalid confidence level')
        result.append(dict(t=t, mint=row['mint'], kind='nonsol_exp' if experimental else 'sol', p=p, level=level,
                           stale=answer['schema'] == 'hodlodds.answer.v1' and
                           (answer.get('freshness') == 'stale' or answer.get('stale') is True)))
    return result


def matches(row, expected):
    # Numeric equality, never rounded display equality or an epsilon tolerance.
    return (row['mint'] == expected['mint'] and row['kind'] == expected['kind']
            and bool(row['p']) and json.loads(row['p']) == expected['p']
            and (DOWNGRADE.get(row['level'], row['level']) if expected.get('stale') else row['level']) == expected['level'])


def verify_tsa(txt, token, ca, cert):
    """OpenSSL verifies both the RFC3161 imprint and CMS signature/certificate path."""
    if not shutil.which('openssl'):
        raise ValueError('OpenSSL is required to check the FreeTSA signature')
    with tempfile.TemporaryDirectory() as tmp:
        paths = []
        for name, data in [('data.txt', txt), ('reply.tsr', token), ('ca.pem', ca), ('tsa.crt', cert)]:
            path = Path(tmp) / name
            path.write_bytes(data)
            paths.append(str(path))
        data, reply, root, leaf = paths
        env = dict(os.environ, LC_ALL='C')
        info = subprocess.run(['openssl', 'ts', '-reply', '-in', reply, '-text'],
                              capture_output=True, text=True, env=env, timeout=30)
        found = re.search(r'^Time stamp: (.+ GMT)$', info.stdout, re.M)
        if info.returncode or not found or not re.search(r'^Hash Algorithm: sha256$', info.stdout, re.M):
            raise ValueError('Invalid RFC 3161 SHA-256 response')
        generated = calendar.timegm(time.strptime(found[1], '%b %d %H:%M:%S %Y GMT'))
        verified = subprocess.run(['openssl', 'ts', '-verify', '-data', data, '-in', reply,
                                   '-CAfile', root, '-untrusted', leaf, '-attime', str(generated)],
                                  capture_output=True, text=True, env=env, timeout=30)
        if verified.returncode:
            raise ValueError('FreeTSA signature / certificate / message imprint verification failed')
        return generated


class Source:
    def __init__(self, directory=None, base=RAW, reveal_dir=None, *, sleep=time.sleep,
                 monotonic=time.monotonic):
        self.directory = Path(directory) if directory else None
        self.base = base
        self.reveal_dir = Path(reveal_dir) if reveal_dir else None
        self.cache = {}
        self.blocks = {}
        self.sleep = sleep
        self.monotonic = monotonic
        self.next_reveal_at = 0
        self.status = json.loads(self.read('reveal-status.json'))
        if self.status.get('schema') != 'hodlodds.reveal.v1':
            raise ValueError('Unknown reveal status schema')
        delay = self.status.get('delay_hours')
        if isinstance(delay, bool) or not isinstance(delay, (float, int)) or not math.isfinite(delay) or delay < 0:
            raise ValueError('Invalid reveal delay')
        self.index = parse_index(self.read('chain/index.txt'))
        self.days = {}
        self.by_time = {}
        self.stamp_cache = {}

    def day(self, day):
        if day not in self.index:
            raise ValueError('Minute is not in the public chain')
        if day not in self.days:
            self.days[day] = check_day(self.read('chain/' + day + '.log'), self.index[day])
            self.by_time.update((e['t'], e) for e in self.days[day])
        return self.days[day]

    def tokens(self, day, exact=False):
        paths = token_index(self.read('tsa/' + day + '/index.txt'), day)
        if exact and self.directory:
            folder = self.directory / 'tsa' / day
            if ({p.name for p in folder.glob('*.tsr')} != {Path(p).name for p in paths}
                    or {p.name for p in folder.glob('*.txt') if p.name != 'index.txt'} !=
                    {Path(p).stem + '.txt' for p in paths}):
                raise ValueError('TSA index does not list exactly the token/text files present')
        return [(minute(day + 'T' + Path(p).stem[:2] + ':' + Path(p).stem[2:] + 'Z'), p) for p in paths]

    def covering(self, t):
        day = iso(t)[:10]
        self.day(day)
        # Only small token indexes are read while looking ahead. Day files are
        # fetched only once a covering stamp is found (including intervening days).
        for delta in range(3):
            candidate = iso(t // 86400 * 86400 + delta * 86400)[:10]
            if candidate not in self.index:
                continue
            covering = next(((head, path) for head, path in self.tokens(candidate) if head >= t), None)
            if covering:
                previous = None
                for d in self.index:
                    if day <= d <= candidate:
                        self.day(d)
                        if previous is not None and self.index[d]['prev'] != previous:
                            raise ValueError('Broken chain across day boundary')
                        previous = self.index[d]['head']
                return covering
        raise ValueError('No covering stamp yet (looked at most 2 days ahead)')

    def audit(self, since=None):
        if since is not None and since not in self.index:
            raise ValueError('--since must name a day in the chain index')
        previous = self.index[since]['prev'] if since else GENESIS
        entries, stamps = [], []
        if self.directory:
            actual = {p.stem for p in (self.directory / 'chain').glob('*.log')}
            if actual != set(self.index):
                raise ValueError('Chain index does not list exactly the days present')
            for folder in (self.directory / 'tsa').iterdir():
                if folder.is_dir() and folder.name not in self.index and (not since or folder.name >= since):
                    if any(folder.glob('*.tsr')) or any(folder.glob('*.txt')):
                        raise ValueError('TSA files outside indexed days')
        for day, record in self.index.items():
            if since and day < since:
                continue
            if record['prev'] != previous:
                raise ValueError('Broken chain across day boundary')
            entries.extend(self.day(day))
            previous = record['head']
            for head, path in self.tokens(day, exact=True):
                generated, _ = self.verify_stamp(head, path)
                stamps.append((head, generated, path))
        return entries, stamps

    def read(self, path, reveal=False):
        key = (reveal, path)
        if key not in self.cache:
            local = self.reveal_dir if reveal else self.directory
            if local:
                data = (local / path).read_bytes()
            else:
                base = self.status['url'] if reveal else self.base
                url = base.rstrip('/') + '/' + path
                if not url.startswith('https://'):
                    raise ValueError('Only HTTPS downloads are allowed')
                # Five attempts total; only temporary overload responses retry.
                for attempt in range(5):
                    if reveal:
                        delay = self.next_reveal_at - self.monotonic()
                        if delay > 0:
                            self.sleep(delay)
                        self.next_reveal_at = self.monotonic() + 0.25
                    try:
                        with urllib.request.urlopen(url, timeout=30) as response:
                            if not response.url.startswith('https://'):
                                raise ValueError('Refusing non-HTTPS redirect')
                            data = response.read()
                        break
                    except urllib.error.HTTPError as exc:
                        if exc.code not in (429, 503) or attempt == 4:
                            raise
                        retry_after = (exc.headers.get('Retry-After', '') if exc.headers else '').strip()
                        try:
                            delay = min(30, int(retry_after)) if re.fullmatch(r'[0-9]+', retry_after) else 5
                        except ValueError:
                            delay = 5
                        exc.close()
                        self.sleep(delay)
            self.cache[key] = data
        return self.cache[key]

    def stamp(self, t):
        return self.verify_stamp(*self.covering(t))

    def verify_stamp(self, head, path):
        if head not in self.by_time:
            raise ValueError('TSA minute is absent from the chain: ' + path)
        if path not in self.stamp_cache:
            if self.read(path[:-4] + '.txt') != (self.by_time[head]['line'] + '\n').encode():
                raise ValueError('TSA text does not equal its chain line: ' + path)
            generated = verify_tsa(self.read(path[:-4] + '.txt'), self.read(path),
                                   self.read('tsa/cacert.pem'), self.read('tsa/tsa.crt'))
            if generated < head:
                raise ValueError('Time stamp predates its committed minute')
            self.stamp_cache[path] = generated
        return self.stamp_cache[path], path

    def revealed(self, entry):
        if entry.get('paused'):
            raise ValueError('not committed: storage paused')
        if entry['t'] in self.blocks:
            return self.blocks[entry['t']], None
        path = entry['minute'][:10] + '/' + entry['minute'][11:13] + '.tsv.gz'
        due = (entry['t'] // 3600 + 1) * 3600 + self.status['delay_hours'] * 3600
        through = self.status.get('revealed_through')
        advertised = bool(through) and entry['t'] <= minute(through)
        if not advertised and time.time() < due:
            return None, due  # Not due yet: there is nothing to download.
        try:
            data = self.read(path, reveal=True)
        except (FileNotFoundError, urllib.error.HTTPError) as exc:
            if isinstance(exc, urllib.error.HTTPError) and exc.code != 404:
                raise
            if not advertised and time.time() < due + REVEAL_GRACE_S:
                return None, due  # Publication runs hourly.
            raise ValueError('Reveal is missing although due or advertised: ' + path)
        rows = canonical_lines(gzip.decompress(data))
        if any(r['t'] // 3600 != entry['t'] // 3600 for r in rows):
            raise ValueError('Reveal contains rows outside its hour')
        self.blocks[entry['t']] = check_block(rows, entry)
        return self.blocks[entry['t']], None


def report_module():
    spec = importlib.util.spec_from_file_location('hodlodds_report_metrics', Path(__file__).with_name('report_metrics.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report_rows(source, day, report, metrics=None):
    """Rehash every committed block, including experimental and empty blocks."""
    m = metrics or report_module()
    m.validate_report(report, source.index, source.status)
    if report['day'] != day:
        raise ValueError('Report filename/day mismatch')
    entries = {e['t']:e for e in source.day(day)}
    first_hour = minute(next(iter(source.index.values()))['first']) // 3600
    start = m.epoch(day)
    joined = []
    for hh in range(24):
        hour = start + hh*3600
        path = '%s/%02d' % (day, hh)
        rows = canonical_lines(gzip.decompress(source.read(path+'.tsv.gz', reveal=True))) if hour//3600 >= first_hour else []
        grouped = {}
        for r in rows:
            if not hour <= r['t'] < hour+3600 or r['t'] not in entries:
                raise ValueError('Revealed row outside committed hour')
            grouped.setdefault(r['t'],[]).append(r)
        for t in range(hour,hour+3600,60):
            if t in entries:
                check_block(grouped.get(t,[]),entries[t])
        labels = source.read(path+'.labels.tsv.gz',reveal=True)
        if sha(labels)!=report['label_files']['%02d'%hh]:
            raise ValueError('Labels fingerprint mismatch: '+path)
        joined.extend(m.join_labels(rows,labels))
    return joined


def verify_report(source, day):
    m = report_module()
    m.epoch(day)
    report = json.loads(source.read('reports/'+day+'.json'))
    m.validate_report(report,source.index,source.status)
    history = {}; previous = GENESIS
    for d,record in source.index.items():
        if d>day:
            break
        if record['prev'] != previous:
            raise ValueError('Broken report chain boundary')
        previous = record['head']
        daily = json.loads(source.read('reports/'+d+'.json'))
        m.compare(daily['alert_policy'],report['alert_policy'],'historical alert policy')
        history[d] = report_rows(source,d,daily,m)
    m.compare(report['periods'],m.periods(history,day,report['alert_policy']['threshold']),'periods')
    if source.read('reports/'+day+'.md') != m.markdown(report):
        raise ValueError('Markdown summary mismatch')
    print('PASS: %s daily, seven-day and since-launch metrics match all committed predictions and published labels. Market labels and actual issuance times are not independently verified.' % day)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', help='local public repository clone')
    parser.add_argument('--reveal-dir', help='local revealed files, for offline verification')
    parser.add_argument('--base-url', default=RAW)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('chain').add_argument('--since', help='audit from this UTC day onward')
    sub.add_parser('answer').add_argument('file')
    sub.add_parser('report').add_argument('day')
    m = sub.add_parser('minute'); m.add_argument('minute'); m.add_argument('--mint')
    args = parser.parse_args(argv)
    try:
        source = Source(args.dir, args.base_url, args.reveal_dir)
        if args.command == 'report':
            verify_report(source, args.day)
            return 0
        if args.command == 'chain':
            entries, stamps = source.audit(args.since)
            for head, generated, path in stamps:
                print('%s: FreeTSA genTime %s' % (path, time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(generated))))
            delays, cursor = [], 0
            for entry in entries:
                while cursor < len(stamps) and stamps[cursor][0] < entry['t']:
                    cursor += 1
                if cursor < len(stamps):
                    delays.append(stamps[cursor][1] - entry['t'])
            print('Verified %d chain minutes from %s. %d have a covering stamp; %d uncovered tail minutes.' %
                  (len(entries), args.since or 'genesis', len(delays), len(entries) - len(delays)))
            if delays:
                print('First covering stamp delay: worst %g s; median %g s.' % (max(delays), statistics.median(delays)))
            for day in source.days:
                path = 'ots/' + day + '.chain.ots'
                try:
                    proof = source.read(path)
                except (FileNotFoundError, urllib.error.HTTPError) as exc:
                    if isinstance(exc, urllib.error.HTTPError) and exc.code != 404:
                        raise
                    print(day + ': not yet anchored')
                    continue
                expected = source.read('chain/' + day + '.log')
                if source.read(path[:-4]) != expected:
                    raise ValueError('OTS input differs from public chain: ' + path)
                print('Bitcoin proof: %s — ots verify %s (or https://opentimestamps.org)' % (path, path))
                if shutil.which('ots'):
                    with tempfile.TemporaryDirectory() as tmp:
                        target = Path(tmp) / Path(path[:-4]).name
                        target.write_bytes(expected)
                        Path(str(target) + '.ots').write_bytes(proof)
                        subprocess.run(['ots', 'verify', str(target) + '.ots'], check=True, timeout=120)
            return 0
        if args.command == 'answer':
            raw = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
            requests = parse_answer(json.loads(raw))
        else:
            requests = [dict(t=minute(args.minute), mint=args.mint)]
        failed = False
        for expected in requests:
            try:
                source.day(iso(expected['t'])[:10])
                entry = source.by_time.get(expected['t'])
                if entry is None:
                    raise ValueError('Minute is not in the public chain')
                if entry.get('paused'):
                    raise ValueError('not committed: storage paused')
                generated, _ = source.stamp(entry['t'])
                print('%s: committed at %s; stamped by FreeTSA at %s (+%d s).' %
                      (expected['mint'] or 'Minute', entry['minute'],
                       time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(generated)), generated - entry['t']))
                rows, due = source.revealed(entry)
                if rows is None:
                    print('PENDING: not yet revealed; scheduled after %s.' % time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(due)))
                elif args.command == 'answer':
                    if not any(matches(row, expected) for row in rows):
                        raise ValueError('Revealed prediction number or confidence level does not match the answer')
                    print('PASS: revealed and matching.' + (' Confidence lowered for a stale last-known answer, as documented.'
                                                            if expected.get('stale') else ''))
                else:
                    selected = [r for r in rows if not expected['mint'] or r['mint'] == expected['mint']]
                    if expected['mint'] and not selected:
                        raise ValueError(absent_message(entry))
                    for row in selected:
                        print(row['line'])
            except Exception as exc:
                print('FAIL %s: %s' % (expected.get('mint') or 'Minute', exc), file=sys.stderr)
                failed = True
        return int(failed)
    except Exception as exc:
        print('FAIL: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
