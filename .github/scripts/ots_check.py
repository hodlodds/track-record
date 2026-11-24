#!/usr/bin/env python3
"""Checks the timestamp chain's Bitcoin proofs without the ots client (the Python standard library only).

For each day with a proof (ots/<day>.chain.ots): the file it proves (ots/<day>.chain) must be the chain file
(chain/<day>.log); the proof's operations are applied to that file's SHA-256, and every Bitcoin attestation they reach
must be the merkle root of the Bitcoin block at its height, as two public block explorers serve that block's header
(blockstream.info and mempool.space): the same block hash at that height in both, the 80-byte header hashing to it.
The block hash both explorers serve is what ties a proof to Bitcoin; the header's work is only checked against a floor
(difficulty 2**40), which screens out a header made by hand, not the chain's own difficulty. A proof only waiting for
its Bitcoin block (pending: the calendars' attestations) is not yet a Bitcoin proof.

Exit 0: every day checked holds, or is only too new to be anchored. 3 (a warning): a day older than a day is still
not anchored or only pending, or a block could not be checked against both explorers (no answer, a refusal of the
runner, a disagreement between them, a mismatch near the tip or seen by one explorer only, the run's time budget
spent). 1: a proof does not hold, or a file is not as published. 2: the checker itself failed on a day (its FAIL
line names the day and where; the other days are still checked) or listing the days. An explorer that does not answer
is not asked again in the same run, and no request starts after BUDGET_S seconds, so a run ends in about that time
even when an explorer stalls.
Usage: python3 -I ots_check.py --dir REPO [--since YYYY-MM-DD] [--now UNIX_SECONDS]
"""
import argparse
import datetime as dt
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import sys
import time
import traceback
import urllib.error
import urllib.request

MAGIC = b'\x00OpenTimestamps\x00\x00Proof\x00\xbf\x89\xe2\xe8\x84\xe8\x92\x94'
TAG_BITCOIN = bytes.fromhex('0588960d73d71901')
TAG_PENDING = bytes.fromhex('83dfe30d2ef90c8e')
MAX_PROOF = 1 << 20          # bytes of one .ots file
MAX_DEPTH = 255              # nested operations: 255 parse, 256 do not, as in python-opentimestamps (Fable 12)
MAX_ARG = 4096               # bytes of an append/prepend argument
MAX_RESULT = 4096            # bytes of a message after an operation
MAX_PAYLOAD = 8192           # bytes of an attestation's payload
EXPLORERS = ('https://blockstream.info/api', 'https://mempool.space/api')
POW_LIMIT = 0xffff * 2 ** 208                     # mainnet, nBits 0x1d00ffff
MAX_TARGET = POW_LIMIT >> 40                      # difficulty 2**40 at least (about 1.1e12; mainnet is far above)
CONFIRMATIONS = 6                                 # a disagreement or mismatch nearer the tip may be a reorganization
DUE_S = 24 * 3600                                 # a day must be anchored a day after it ends
DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
USER_AGENT = 'ots-check/1'
PAUSE_S = 0.25                                    # between two requests to one explorer
BUDGET_S = 600                                    # no request starts later in a run (the workflow's step: 15 min)


class ProofError(ValueError):
    pass


class Reader:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ProofError('the proof ends early')
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def byte(self):
        return self.take(1)[0]

    def varuint(self):
        value = shift = 0
        while True:
            b = self.byte()
            value |= (b & 0x7f) << shift
            if not b & 0x80:
                return value
            shift += 7
            if shift > 63:
                raise ProofError('a number in the proof is too long')

    def varbytes(self, limit, least=0):
        n = self.varuint()
        if n > limit or n < least:
            raise ProofError('a field of the proof has a length out of bounds (%d)' % n)
        return self.take(n)


def _ripemd160(msg):
    try:
        return hashlib.new('ripemd160', msg).digest()
    except ValueError:            # not in every OpenSSL build: the branch cannot be followed here
        return None


UNARY = {0x08: lambda m: hashlib.sha256(m).digest(), 0x02: lambda m: hashlib.sha1(m).digest(),
         0x03: _ripemd160, 0x67: lambda m: None,        # keccak256: not in the standard library
         0xf2: lambda m: m[::-1], 0xf3: lambda m: m.hex().encode()}


def _op(reader, tag, msg):
    """The message after the operation `tag` (None once a branch cannot be computed here; its bytes are still read)."""
    if tag in (0xf0, 0xf1):
        arg = reader.varbytes(MAX_ARG, least=1)
        out = None if msg is None else (msg + arg if tag == 0xf0 else arg + msg)
    elif tag in UNARY:
        out = None if msg is None else UNARY[tag](msg)
    else:
        raise ProofError('unknown operation 0x%02x' % tag)
    if out is not None and len(out) > MAX_RESULT:
        raise ProofError('a message of the proof is too long')
    return out


def _tree(reader, msg, depth, found):
    if depth > MAX_DEPTH:
        raise ProofError('the proof is nested too deeply')
    tag = reader.byte()
    while tag == 0xff:                               # a fork: one more branch follows, then the rest
        _branch(reader, reader.byte(), msg, depth, found)
        tag = reader.byte()
    _branch(reader, tag, msg, depth, found)


def _branch(reader, tag, msg, depth, found):
    if tag == 0x00:
        kind = reader.take(8)
        found.append((kind, reader.varbytes(MAX_PAYLOAD), msg))
        return
    _tree(reader, _op(reader, tag, msg), depth + 1, found)


def parse(data):
    """(the file's SHA-256, [(attestation, detail, message)]): attestation 'bitcoin' (detail: the height), 'pending'
    (detail: the calendar), or 'other' (detail: the tag in hex); message None where the branch cannot be computed."""
    if len(data) > MAX_PROOF:
        raise ProofError('the proof is larger than %d bytes' % MAX_PROOF)
    if not data.startswith(MAGIC):
        raise ProofError('not an OpenTimestamps proof')
    reader = Reader(data)
    reader.take(len(MAGIC))
    if reader.varuint() != 1:
        raise ProofError('an unknown proof version')
    if reader.byte() != 0x08:
        raise ProofError("the proof's file digest is not SHA-256")
    digest = reader.take(32)
    raw = []
    _tree(reader, digest, 0, raw)
    if reader.pos != len(data):
        raise ProofError('bytes after the end of the proof')
    out = []
    for kind, payload, msg in raw:
        sub = Reader(payload)
        if kind == TAG_BITCOIN:
            height = sub.varuint()
            if sub.pos != len(payload):
                raise ProofError('bytes after a Bitcoin attestation')
            if msg is not None and len(msg) != 32:
                raise ProofError('a Bitcoin attestation on a message of %d bytes (a merkle root has 32)' % len(msg))
            out.append(('bitcoin', height, msg))
        elif kind == TAG_PENDING:
            uri = sub.varbytes(1000)
            if sub.pos != len(payload):
                raise ProofError('bytes after a pending attestation')
            text = uri.decode('ascii', 'replace')
            out.append(('pending', re.sub(r'[^A-Za-z0-9.:/_-]', '?', text)[:100], msg))
        else:
            out.append(('other', kind.hex(), msg))
    return digest, out


def sha256d(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def target_of(bits):
    exponent, mantissa = bits >> 24, bits & 0x007fffff
    if bits & 0x00800000 or not mantissa:
        return None
    return mantissa << (8 * (exponent - 3)) if exponent >= 3 else mantissa >> (8 * (3 - exponent))


def check_header(header, block_hash, max_target=None):
    """None when the 80-byte header hashes to block_hash with valid work at the floor (MAX_TARGET); else why not."""
    if len(header) != 80:
        return 'a header of %d bytes' % len(header)
    work = sha256d(header)
    if work[::-1].hex() != block_hash:
        return 'the header does not hash to the block hash'
    target = target_of(int.from_bytes(header[72:76], 'little'))
    if target is None or target > (MAX_TARGET if max_target is None else max_target):
        return 'the header claims too little work'
    if int.from_bytes(work, 'little') > target:
        return "the header's work is not valid"
    return None


class NoBlock(Exception):
    """HTTP 404 from an explorer: it has no such block (its own class, so no other lookup error reads as one)."""


def clean(text):
    """An explorer's words in a line of ours: no control character, backtick or other mark (Fable 2)."""
    return re.sub(r'[^A-Za-z0-9 .,:;/()_=+%-]', '?', text)[:80]


def fetch(url, retries=2):
    """The answer's text, or raises NoBlock (HTTP 404: the explorer has no such block) or OSError (no usable
    answer: the network, a timeout or a broken response, HTTP 429 or 5xx after retries, and any other HTTP error at
    once, such as a 403 from a block of the runner)."""
    for attempt in range(retries + 1):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(request, timeout=20) as answer:
                return answer.read(4097).decode('ascii', 'replace').strip()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NoBlock('HTTP 404')
            if exc.code != 429 and exc.code < 500:
                raise OSError('HTTP %d' % exc.code)
            error = OSError('HTTP %d' % exc.code)
        except (urllib.error.URLError, socket.timeout, OSError, http.client.HTTPException, ValueError) as exc:
            error = OSError(clean(str(getattr(exc, 'reason', exc))) or type(exc).__name__)
        if attempt < retries:
            time.sleep(2 + 3 * attempt)
    raise error


class Explorers:
    """Block headers by height from each explorer: (hash, header) or a reason it gave none; cached per run."""

    def __init__(self, bases=EXPLORERS, max_target=None, pause=PAUSE_S, budget=BUDGET_S):
        self.bases, self.max_target, self.pause, self.budget = bases, max_target, pause, budget
        self.cache, self.tips, self.down = {}, {}, {}
        self.deadline = time.monotonic() + budget

    def _get(self, url):
        out = fetch(url)
        if self.pause:
            time.sleep(self.pause)
        return out

    def _ask(self, base, url):
        """One request to one explorer. One that did not answer is not asked again in this run (each unanswered
        request costs up to about a minute), and no request starts once the run's time budget is spent."""
        if base in self.down:
            raise OSError(self.down[base])
        if time.monotonic() > self.deadline:
            raise OSError('not asked: the run spent its %d s' % self.budget)
        try:
            return self._get(url)
        except OSError as exc:
            self.down[base] = 'not asked again after %s' % exc
            raise

    def tip(self, base):
        if base not in self.tips:
            try:
                text = self._ask(base, base + '/blocks/tip/height')
                self.tips[base] = int(text) if text.isdigit() else None
            except (OSError, NoBlock):
                self.tips[base] = None
        return self.tips[base]

    def header(self, base, height):
        key = (base, height)
        if key not in self.cache:
            self.cache[key] = self._header(base, height)
        return self.cache[key]

    def _header(self, base, height):
        try:
            block_hash = self._ask(base, '%s/block-height/%d' % (base, height))
            if not HEX64.match(block_hash):
                return ('bad', 'an answer that is not a block hash')
            text = self._ask(base, '%s/block/%s/header' % (base, block_hash))
            if not re.fullmatch(r'[0-9a-f]{160}', text):
                return ('bad', 'an answer that is not an 80-byte header')
        except NoBlock as exc:
            return ('none', 'no block at height %d (%s)' % (height, exc))
        except OSError as exc:
            return ('unreachable', str(exc))
        header = bytes.fromhex(text)
        why = check_header(header, block_hash, self.max_target)
        if why:
            return ('bad', why)
        return ('ok', block_hash, header)


def name(base):
    return re.sub(r'^https?://([^/]+).*$', r'\1', base)


def check_bitcoin(explorers, height, msg):
    """('ok', block hash, block time) | ('warn', why) | ('fail', why) for one Bitcoin attestation."""
    answers = [(base, explorers.header(base, height)) for base in explorers.bases]
    good = [(base, a[1], a[2]) for base, a in answers if a[0] == 'ok']
    none = [base for base, a in answers if a[0] == 'none']
    trouble = ['%s: %s' % (name(base), a[1]) for base, a in answers if a[0] in ('unreachable', 'bad')]
    matching = [g for g in good if g[2][36:68] == msg]
    missing = trouble + ['%s: no block at height %d' % (name(b), height) for b in none]
    # A tip that cannot be read counts as near: a mismatch or a missing block there may be a reorganization
    near = lambda bases: any(t is None or height > t - CONFIRMATIONS for t in (explorers.tip(b) for b in bases))
    if good and not matching:                       # first: no block served holds the proof's root (code review r2 3)
        why = 'the proof does not reach the merkle root of block %d (%s)' % (height, ', '.join(name(b) for b, _, _ in good))
        if missing:                                  # one explorer's header alone does not fail a proof (Fable 5)
            return ('warn', why + '; not confirmed by the other (%s)' % '; '.join(missing))
        if near([b for b, _, _ in good]):
            return ('warn', why + ': within %d blocks of the tip or the tip unknown, a reorganization?' % CONFIRMATIONS)
        return ('fail', why)
    if len(set(g[1] for g in good)) > 1:            # one of them holds the root: which block is right is not known
        return ('warn', 'the explorers serve different blocks at height %d (a reorganization?)' % height)
    if len(none) == len(explorers.bases):        # every explorer answered: no such block
        if near(none):
            return ('warn', 'no explorer has a block at height %d yet (within %d blocks of the tip, or the tip unknown)'
                    % (height, CONFIRMATIONS))
        return ('fail', 'no explorer has a block at height %d, %d or more blocks below its tip' % (height, CONFIRMATIONS))
    if len(matching) == len(explorers.bases):
        when = int.from_bytes(matching[0][2][68:72], 'little')
        return ('ok', matching[0][1], when)
    if matching:
        return ('warn', 'block %d checked against %s only (%s)' % (height, name(matching[0][0]), '; '.join(missing)))
    return ('warn', 'block %d not checked (%s)' % (height, '; '.join(missing)))


def where(exc):
    """The innermost frame as name:line: where the checker failed, without the error's words (no explorer's text)."""
    frames = traceback.extract_tb(exc.__traceback__)
    return '%s:%d' % (frames[-1].name, frames[-1].lineno) if frames else 'unknown'


def iso(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def day_end(day):
    return int(dt.datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=dt.timezone.utc).timestamp()) + 86400


def check_day(repo, day, explorers, now):
    """(status, line): status 'ok', 'note' (too new to be anchored), 'warn' or 'fail'."""
    due = now >= day_end(day) + DUE_S
    proof_path = repo / 'ots' / (day + '.chain.ots')
    if not proof_path.exists():
        if due:
            return ('warn', 'WARNING Bitcoin proof %s: no proof file a day after the day ended' % day)
        return ('note', 'Bitcoin proof %s: not yet anchored (not due yet)' % day)
    chain = repo / 'chain' / (day + '.log')
    stamped = repo / 'ots' / (day + '.chain')
    if not chain.exists() or not stamped.exists() or stamped.read_bytes() != chain.read_bytes():
        return ('fail', 'FAIL Bitcoin proof %s: ots/%s.chain is not the chain file chain/%s.log' % (day, day, day))
    try:
        digest, found = parse(proof_path.read_bytes())
    except ProofError as exc:
        return ('fail', 'FAIL Bitcoin proof %s: %s' % (day, exc))
    if digest != hashlib.sha256(stamped.read_bytes()).digest():
        return ('fail', 'FAIL Bitcoin proof %s: the proof is for another file (its SHA-256 differs)' % day)
    bitcoin = sorted(set((h, m) for kind, h, m in found if kind == 'bitcoin' and m is not None))
    if not bitcoin:
        calendars = sorted(set(d for kind, d, _ in found if kind == 'pending'))
        text = 'only pending (%s)' % ', '.join(calendars) if calendars else 'no Bitcoin attestation'
        if any(kind == 'bitcoin' for kind, _, _ in found):
            text = 'its Bitcoin attestations need an operation this checker lacks'
        return ('warn' if due else 'note', 'PENDING Bitcoin proof %s: %s%s' % (day, text, '' if due else ' (not due yet)'))
    blocks, worst, notes = [], 'ok', []
    for height, msg in bitcoin:
        result = check_bitcoin(explorers, height, msg)
        if result[0] == 'ok':
            blocks.append('block %d %s (%s)' % (height, result[1], iso(result[2])))
        elif result[0] == 'fail':
            worst = 'fail'
            notes.append(result[1])
        else:
            worst = 'warn' if worst == 'ok' else worst
            notes.append(result[1])
    if worst == 'fail':
        return ('fail', 'FAIL Bitcoin proof %s: %s' % (day, '; '.join(notes)))
    if worst == 'warn':
        return ('warn', 'WARNING Bitcoin proof %s: %s%s' % (day, '; '.join(notes), ('; verified: ' + ', '.join(blocks)) if blocks else ''))
    return ('ok', 'Bitcoin proof %s: verified, %s; the same header from %s' % (
        day, ', '.join(blocks), ' and '.join(name(b) for b in explorers.bases)))


def days_of(repo, since):
    days = set()
    index = repo / 'chain' / 'index.txt'
    if index.exists():
        for line in index.read_text().splitlines():
            first = line.split()[0] if line.split() else ''
            if DAY.match(first):
                days.add(first)
    for path in (repo / 'ots').glob('*.chain.ots'):
        day = path.name[:-len('.chain.ots')]
        if DAY.match(day):
            days.add(day)
    return sorted(d for d in days if since is None or d >= since)


def main(argv=None, explorers=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--dir', required=True, type=Path)
    parser.add_argument('--since', help='the first day to check (YYYY-MM-DD); every day without it')
    parser.add_argument('--now', type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.since is not None and not DAY.match(args.since):
        parser.error('--since takes YYYY-MM-DD')
    now = int(time.time()) if args.now is None else args.now
    explorers = explorers or Explorers()
    try:
        days = days_of(args.dir, args.since)
    except Exception as exc:                         # the checker itself, not a proof (Fable 6, code review r2 2)
        print('FAIL Bitcoin proofs: the checker itself failed listing the days (%s at %s)' % (type(exc).__name__, where(exc)))
        return 2
    if not days:
        print('FAIL Bitcoin proofs: no day to check%s' % (' since ' + args.since if args.since else ''))
        return 1
    counts = {'ok': 0, 'note': 0, 'warn': 0, 'fail': 0, 'crash': 0}
    for day in days:
        try:
            status, line = check_day(args.dir, day, explorers, now)
        except Exception as exc:                     # this day only, the others are still checked (code review r2 4, 5)
            status, line = 'crash', 'FAIL Bitcoin proof %s: the checker itself failed on this day (%s at %s)' % (
                day, type(exc).__name__, where(exc))
        counts[status] += 1
        print(line)
    print('Bitcoin proofs: %d verified, %d failed, %d with a warning, %d not due yet%s (%s to %s)' % (
        counts['ok'], counts['fail'], counts['warn'], counts['note'],
        ', %d the checker could not check' % counts['crash'] if counts['crash'] else '', days[0], days[-1]))
    return 1 if counts['fail'] else 2 if counts['crash'] else 3 if counts['warn'] else 0


if __name__ == '__main__':
    sys.exit(main())
