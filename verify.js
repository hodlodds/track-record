/* Pure verification functions are also exercised by node --test; no CDN imports at module load. */
export const GENESIS = '0'.repeat(64);
const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', {fatal: true});
export const bytes = value => encoder.encode(value);
export const hex = buffer => [...new Uint8Array(buffer)].map(x => x.toString(16).padStart(2, '0')).join('');
export const digest = async value => hex(await crypto.subtle.digest('SHA-256', typeof value === 'string' ? bytes(value) : value));
export function minute(value) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$/.test(value)) throw Error('Use YYYY-MM-DDTHH:MMZ (UTC).');
  const t = Date.parse(value) / 1000;
  if (!Number.isFinite(t) || iso(t) !== value) throw Error('Invalid UTC minute.');
  return t;
}
export const iso = t => new Date(t * 1000).toISOString().slice(0, 16) + 'Z';
export function parseChain(text) {
  if (text && !text.endsWith('\n')) throw Error('Incomplete chain line.');
  return text ? text.slice(0, -1).split('\n').map(line => {
    const m = /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z) rows=(0|[1-9]\d*|paused) sha256=([a-f0-9]{64}) chain=([a-f0-9]{64})$/.exec(line);
    if (!m || m[0] !== line || (m[2] !== 'paused' && !Number.isSafeInteger(Number(m[2])))) throw Error('Malformed chain line.');
    return {minute: m[1], t: minute(m[1]), rows: m[2] === 'paused' ? 0 : Number(m[2]), paused: m[2] === 'paused', sha256: m[3], chain: m[4], line};
  }) : [];
}
export async function verifyLinks(entries, previous = GENESIS, after = -1) {
  for (const e of entries) {
    if (e.t <= after) throw Error('Chain minutes must strictly increase.');
    if (await digest(`${previous} ${e.minute} ${e.paused ? 'paused' : e.rows} ${e.sha256}`) !== e.chain) throw Error(`Broken chain link at ${e.minute}.`);
    previous = e.chain; after = e.t;
  }
  return previous;
}
export function parseIndex(text) {
  if (!text || !text.endsWith('\n')) throw Error('Missing or incomplete chain index.');
  const records = new Map();
  let previous = '';
  for (const line of text.slice(0, -1).split('\n')) {
    const m = /^(\d{4}-\d{2}-\d{2}) first=(\S+) last=(\S+) lines=([1-9]\d*) prev=([a-f0-9]{64}) head=([a-f0-9]{64})$/.exec(line);
    if (!m || m[0] !== line) throw Error('Malformed chain index.');
    const [, day, first, last, count, prev, head] = m;
    if (day <= previous || first.slice(0, 10) !== day || last.slice(0, 10) !== day || minute(first) > minute(last) || !Number.isSafeInteger(Number(count))) throw Error('Invalid index day bounds or order.');
    records.set(day, {day, first, last, lines: Number(count), prev, head});
    previous = day;
  }
  return records;
}
export async function checkDay(text, record) {
  const entries = parseChain(text);
  if (!entries.length || entries.some(e => e.minute.slice(0, 10) !== record.day) ||
      entries[0].minute !== record.first || entries.at(-1).minute !== record.last ||
      entries.length !== record.lines || entries.at(-1).chain !== record.head) throw Error('Day file differs from chain index.');
  await verifyLinks(entries, record.prev);
  return entries;
}
export function tokenIndex(text, day) {
  if (text && !text.endsWith('\n')) throw Error('Incomplete TSA index.');
  const names = text ? text.slice(0, -1).split('\n') : [];
  if (JSON.stringify(names) !== JSON.stringify([...new Set(names)].sort())) throw Error('Invalid TSA index order.');
  return names.map(name => {
    if (!/^\d{4}$/.test(name)) throw Error('Invalid TSA basename.');
    minute(`${day}T${name.slice(0, 2)}:${name.slice(2)}Z`);
    return `tsa/${day}/${name}.tsr`;
  });
}
export class Evidence {
  constructor(index, get) {
    this.index = index; this.get = get; this.days = new Map(); this.byTime = new Map();
  }
  async day(day) {
    if (!this.index.has(day)) throw Error('This minute is not in the public chain.');
    if (!this.days.has(day)) {
      const entries = await checkDay(decoder.decode(await this.get(`./chain/${day}.log`)), this.index.get(day));
      this.days.set(day, entries);
      for (const e of entries) this.byTime.set(e.t, e);
    }
    return this.days.get(day);
  }
  async covering(t) {
    const day = iso(t).slice(0, 10);
    await this.day(day);
    for (let delta = 0; delta <= 2; delta++) {
      const candidate = iso(Math.floor(t / 86400) * 86400 + delta * 86400).slice(0, 10);
      if (!this.index.has(candidate)) continue;
      const stamp = coveringStamp(tokenIndex(decoder.decode(await this.get(`./tsa/${candidate}/index.txt`)), candidate), t);
      if (!stamp) continue;
      let previous;
      for (const [d, record] of this.index) {
        if (d < day || d > candidate) continue;
        await this.day(d);
        if (previous !== undefined && record.prev !== previous) throw Error('Broken chain across day boundary.');
        previous = record.head;
      }
      return stamp;
    }
    return undefined;
  }
}
export const REVEAL_GRACE_S = 7200; // a due hour may still be waiting for the hourly publication run
export const DOWNGRADE = {high: 'medium', medium: 'low', low: 'low', experimental: 'experimental'};
export function canonicalLines(text) {
  if (text && !text.endsWith('\n')) throw Error('Incomplete canonical line.');
  const seen = new Set();
  return text ? text.slice(0, -1).split('\n').map(line => {
    const f = line.split('\t');
    if (f.length !== 5) throw Error('Canonical lines need five fields.');
    const [time, mint, kind, p, level] = f, t = Number(time);
    if (!/^(0|[1-9]\d*)$/.test(time) || !Number.isSafeInteger(t) || t % 60 || !mint || !kind || line.includes('\r')) throw Error('Invalid canonical row.');
    if (p && (typeof JSON.parse(p) !== 'number' || !Number.isFinite(JSON.parse(p)))) throw Error('Invalid canonical number.');
    const key = JSON.stringify([t, mint, kind]);
    if (seen.has(key)) throw Error('Duplicate canonical row.');
    seen.add(key);
    return {t, mint, kind, p, level, line};
  }) : [];
}
// Canonical identifiers are ASCII; compare code points, never locale-dependent collation.
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
// r100 (Fable audit 20): an empty commitment (rows=0) means nothing was published for that minute.
export const EMPTY_MINUTE = "No predictions were published for this minute (an empty commitment, for example during a feed pause). Check the answer's as_of minute.";
export function absentMessage(entry) {
  return entry.rows === 0 && !entry.paused ? EMPTY_MINUTE : 'This mint is absent from the verified minute.';
}
export function blockFor(rows, t) {
  const selected = rows.filter(r => r.t === t).sort((a, b) => compare(a.mint, b.mint) || compare(a.kind, b.kind));
  return {rows: selected, body: selected.map(r => r.line + '\n').join('')};
}
export async function checkBlock(rows, entry) {
  const block = blockFor(rows, entry.t);
  if (block.rows.length !== entry.rows || await digest(block.body) !== entry.sha256) throw Error('Revealed row count or fingerprint does not match.');
  return block.rows;
}
export function parseAnswer(answer) {
  if (!answer || !['hodlodds.answer.v1', 'hodlodds.board.v1'].includes(answer.schema)) throw Error('Paste a hodlodds.answer.v1 or hodlodds.board.v1 answer.');
  const t = answer.as_of;
  if (!Number.isSafeInteger(t) || t < 0 || t % 60) throw Error('Answer as_of must be epoch seconds at a minute boundary.');
  const rows = answer.schema === 'hodlodds.answer.v1' ? [answer.row] : answer.rows;
  if (!Array.isArray(rows) || !rows.length) throw Error('Answer contains no predictions.');
  return rows.map(row => {
    if (!row || typeof row.mint !== 'string' || !row.mint) throw Error('Missing mint.');
    const experimental = Object.hasOwn(row, 'experimental_score');
    const p = row[experimental ? 'experimental_score' : 'p_crash_60m'];
    const level = row.confidence?.level ?? '';
    if (typeof p !== 'number' || !Number.isFinite(p) || p < 0 || p > 1) throw Error('Missing or invalid prediction number.');
    if (typeof level !== 'string') throw Error('Invalid confidence level.');
    return {t, mint: row.mint, kind: experimental ? 'nonsol_exp' : 'sol', p, level,
      stale: answer.schema === 'hodlodds.answer.v1' && (answer.freshness === 'stale' || answer.stale === true)};
  });
}
export function matches(row, expected) {
  return row.mint === expected.mint && row.kind === expected.kind && row.p !== '' && JSON.parse(row.p) === expected.p && (expected.stale ? (DOWNGRADE[row.level] ?? row.level) : row.level) === expected.level;
}
export function coveringStamp(paths, t) {
  return paths.map(path => {
    if (!/^tsa\/\d{4}-\d{2}-\d{2}\/\d{4}\.tsr$/.test(path)) throw Error('Invalid TSA path.');
    return {path, t: minute(`${path.slice(4, 14)}T${path.slice(15, 17)}:${path.slice(17, 19)}Z`)};
  }).sort((a, b) => a.t - b.t).find(s => s.t >= t);
}
export function checkStampText(text, entry) {
  if (!entry || text !== entry.line + '\n') throw Error('TSA text does not equal its chain line.');
}
export function revealDue(t, delay) {
  if (typeof delay !== 'number' || !Number.isFinite(delay) || delay < 0) throw Error('Invalid reveal delay.');
  return (Math.floor(t / 3600) + 1) * 3600 + delay * 3600;
}

export async function verifyTimestamp(txt, token, caPem, certPem) {
  // Exact versions; loaded only when verification is requested. The page sends no answer to these hosts.
  const [asn1js, pkijs] = await Promise.all([
    import('https://cdn.jsdelivr.net/npm/asn1js@3.0.6/+esm'),
    import('https://cdn.jsdelivr.net/npm/pkijs@3.2.5/+esm')
  ]);
  const decode = buffer => {
    const parsed = asn1js.fromBER(buffer);
    if (parsed.offset === -1 || parsed.offset !== buffer.byteLength) throw Error('Invalid ASN.1 token or certificate.');
    return parsed.result;
  };
  const certificate = pem => {
    const der = Uint8Array.from(atob(pem.replace(/-----[^-]+-----/g, '').replace(/\s/g, '')), c => c.charCodeAt(0));
    return new pkijs.Certificate({schema: decode(der.buffer)});
  };
  const response = new pkijs.TimeStampResp({schema: decode(token)});
  if (![0, 1].includes(response.status.status) || !response.timeStampToken || response.timeStampToken.contentType !== '1.2.840.113549.1.7.2') throw Error('Time stamp was not granted.');
  const signed = new pkijs.SignedData({schema: response.timeStampToken.content});
  if (signed.encapContentInfo.eContentType !== '1.2.840.113549.1.9.16.1.4' || signed.signerInfos.length !== 1) throw Error('Expected one RFC 3161 signer.');
  const content = signed.encapContentInfo.eContent?.getValue();
  if (!content) throw Error('Missing signed timestamp content.');
  const info = new pkijs.TSTInfo({schema: decode(content)});
  if (info.messageImprint.hashAlgorithm.algorithmId !== '2.16.840.1.101.3.4.2.1' ||
      hex(info.messageImprint.hashedMessage.valueBlock.valueHexView) !== await digest(txt)) throw Error('Timestamp message imprint does not match the chain text.');
  const leaf = certificate(certPem), root = certificate(caPem);
  const eku = leaf.extensions?.find(e => e.extnID === '2.5.29.37');
  if (!eku?.critical || eku.parsedValue?.keyPurposes?.length !== 1 || eku.parsedValue.keyPurposes[0] !== '1.3.6.1.5.5.7.3.8') throw Error('Certificate is not exclusively authorized for time stamping.');
  // Use the published FreeTSA signer and root, not a root supplied inside the token.
  signed.certificates = [leaf];
  const ok = await signed.verify({signer: 0, trustedCerts: [root], checkChain: true,
    checkDate: info.genTime, data: txt.buffer.slice(txt.byteOffset, txt.byteOffset + txt.byteLength)});
  if (ok !== true) throw Error('FreeTSA signature or certificate chain verification failed.');
  return info.genTime.getTime() / 1000;
}

async function startUI() {
  const form = document.querySelector('form'), output = document.querySelector('#results'), button = form.querySelector('button');
  const statusLine = (parent, state, text) => {
    const li = document.createElement('li'); li.className = state;
    li.textContent = `${state.toUpperCase()}: ${text}`; parent.append(li);
  };
  form.addEventListener('submit', async event => {
    event.preventDefault(); output.replaceChildren(); button.disabled = true;
    const loading = document.createElement('p'); loading.textContent = 'Checking public files and signatures…'; output.append(loading);
    const cache = new Map(), stampCache = new Map(), blockCache = new Map();
    const get = async url => {
      if (!cache.has(url)) cache.set(url, fetch(url).then(async r => {
        if (!r.ok) { const e = Error(`Could not fetch ${url} (HTTP ${r.status}).`); e.status = r.status; throw e; }
        return new Uint8Array(await r.arrayBuffer());
      }));
      return cache.get(url);
    };
    try {
      const raw = document.querySelector('#answer').value.trim();
      const requests = raw ? parseAnswer(JSON.parse(raw)) : [{t: minute(document.querySelector('#minute').value.trim()), mint: document.querySelector('#mint').value.trim()}];
      if (!raw && !requests[0].mint) throw Error('Enter a mint and UTC minute, or paste your answer.');
      const index = parseIndex(decoder.decode(await get('./chain/index.txt')));
      const evidence = new Evidence(index, get);
      const status = JSON.parse(decoder.decode(await get('./reveal-status.json')));
      if (status.schema !== 'hodlodds.reveal.v1' || !status.url.startsWith('https://')) throw Error('Invalid reveal status.');
      for (const request of requests) {
        const section = document.createElement('section'), heading = document.createElement('h2'), steps = document.createElement('ol');
        heading.textContent = `${request.mint} · ${iso(request.t)}`; section.append(heading, steps); output.append(section);
        try {
          await evidence.day(iso(request.t).slice(0, 10));
          const entry = evidence.byTime.get(request.t);
          if (!entry) throw Error('This minute is not in the public chain.');
          statusLine(steps, 'pass', '1. This minute is in the public chain; the requested day matches its index and its links check out.');
          const stamp = await evidence.covering(request.t);
          if (!stamp) { statusLine(steps, 'pending', '2. No covering stamp yet (looked at most 2 days ahead).'); }
          else {
            const txt = await get('./' + stamp.path.replace(/\.tsr$/, '.txt'));
            checkStampText(decoder.decode(txt), evidence.byTime.get(stamp.t));
            if (!stampCache.has(stamp.path)) {
              const generated = await verifyTimestamp(txt, (await get('./' + stamp.path)).buffer,
                decoder.decode(await get('./tsa/cacert.pem')), decoder.decode(await get('./tsa/tsa.crt')));
              if (generated < stamp.t) throw Error('Time stamp predates its committed minute.');
              stampCache.set(stamp.path, generated);
            }
            const generated = stampCache.get(stamp.path);
            statusLine(steps, 'pass', `2. The chain links to a FreeTSA time stamp at ${new Date(generated * 1000).toISOString()} (+${generated - request.t} s). Its signature and certificate chain check out in this browser.`);
          }
          if (entry.paused) throw Error('not committed: storage paused');
          const due = revealDue(request.t, status.delay_hours);
          const advertised = Boolean(status.revealed_through) && request.t <= minute(status.revealed_through);
          const pending = () => statusLine(steps, 'pending', `3. Not yet revealed. Scheduled after ${new Date(due * 1000).toISOString()}; publication runs hourly.`);
          // Not due yet: nothing to download (and nothing to ask the reveal server).
          if (!advertised && Date.now() / 1000 < due) { pending(); continue; }
          const path = iso(request.t).slice(0, 10) + '/' + iso(request.t).slice(11, 13) + '.tsv.gz';
          let compressed;
          try { compressed = await get(status.url.replace(/\/$/, '') + '/' + path); }
          catch (error) {
            if (error.status !== 404) throw Error(`Could not download the revealed file (${error.message}).`);
            if (!advertised && Date.now() / 1000 < due + REVEAL_GRACE_S) { pending(); continue; }
            throw Error('The reveal is missing although due or advertised.');
          }
          if (!blockCache.has(request.t)) {
            if (typeof DecompressionStream === 'undefined') throw Error('This browser cannot decompress gzip. Use a current browser or verify.py.');
            const decompressed = await new Response(new Blob([compressed]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer();
            const all = canonicalLines(decoder.decode(decompressed));
            if (all.some(r => Math.floor(r.t / 3600) !== Math.floor(request.t / 3600))) throw Error('Reveal contains rows outside its hour.');
            blockCache.set(request.t, await checkBlock(all, entry));
          }
          const rows = blockCache.get(request.t);
          const found = rows.filter(r => r.mint === request.mint);
          if (!found.length) throw Error(absentMessage(entry));
          if (raw && !found.some(r => matches(r, request))) throw Error('The prediction number or confidence level does not match your answer.');
          statusLine(steps, 'pass', raw ? '3. The revealed file matches the committed fingerprint and your exact number and confidence level.' + (request.stale ? ' Confidence lowered for a stale last-known answer, as documented.' : '') : `3. The revealed file matches the committed fingerprint. ${found.map(r => `${r.kind}: ${r.p || '(no score)'}, confidence ${r.level || '(none)'}`).join('; ')}.`);
        } catch (error) { statusLine(steps, 'fail', error.message); }
      }
      const proofs = document.querySelector('#proofs'); proofs.replaceChildren();
      const proofDays = new Set([...index.keys()].slice(-7));
      for (const request of requests) proofDays.add(iso(request.t).slice(0, 10));
      for (const day of evidence.days.keys()) proofDays.add(day);
      for (const day of [...proofDays].sort()) {
        const path = `ots/${day}.chain.ots`;
        const li = document.createElement('li');
        try {
          await get('./' + path);
          const a = document.createElement('a'); a.href = './' + path; a.textContent = path; li.append(a);
        } catch (error) {
          if (error.status !== 404) throw error;
          li.textContent = `${day}: not yet anchored`;
        }
        proofs.append(li);
      }
    } catch (error) {
      const p = document.createElement('p'); p.className = 'fail'; p.textContent = 'FAIL: ' + error.message; output.append(p);
    } finally { loading.remove(); button.disabled = false; }
  });
}
if (typeof document !== 'undefined' && document.querySelector('form')) startUI();
