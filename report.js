/* No external libraries or remote code. Report contents are inserted as text. */
'use strict';
const $ = id => document.getElementById(id);
const fmt = (x, percent = false) => x == null ? 'undefined' : percent ? `${(100*x).toFixed(2)}%` : Number(x).toPrecision(5);
function node(tag, text, parent) {
  const e = document.createElement(tag); if (text != null) e.textContent = text; if (parent) parent.append(e); return e;
}
function link(parent, text, url) { const a = node('a', text, parent); a.href = url; return a; }
async function get(url) {
  const r = await fetch(url); if (!r.ok) throw Error(`Could not load ${url} (${r.status})`); return r.json();
}
function chart(parent, bands, title) {
  node('h3', title, parent);
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 520 330'); svg.setAttribute('role', 'img');
  svg.setAttribute('aria-label', `${title}: predicted probability versus observed crash rate, with Wilson 95% intervals`);
  parent.append(svg);
  function draw(tag, attrs, text) {
    const e = document.createElementNS(ns, tag);
    for (const [k,v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text) e.textContent = text; svg.append(e); return e;
  }
  const max = Math.min(1, Math.max(.10, ...bands.flatMap(b => [b.predicted || 0, b.wilson95[1] || 0]))*1.12);
  const x = v => 60+v/max*400, y = v => 270-v/max*220;
  draw('line', {x1:60,y1:270,x2:460,y2:270,stroke:'currentColor'});
  draw('line', {x1:60,y1:270,x2:60,y2:50,stroke:'currentColor'});
  draw('line', {x1:60,y1:270,x2:460,y2:50,stroke:'#888','stroke-dasharray':'5 4'});
  for (let i=0;i<=4;i++) {
    const v = max*i/4;
    draw('text',{x:x(v),y:290,'text-anchor':'middle',fill:'currentColor','font-size':12},fmt(v,true));
    draw('text',{x:54,y:y(v)+4,'text-anchor':'end',fill:'currentColor','font-size':12},fmt(v,true));
  }
  draw('text',{x:260,y:321,'text-anchor':'middle',fill:'currentColor'},'Mean predicted probability');
  draw('text',{x:60,y:24,fill:'currentColor'},'Observed crash rate');
  bands.forEach((b,i) => {
    if (!b.n) return;
    const color = ['#188b68','#ac6f00','#d14a55'][i];
    draw('line',{x1:x(b.predicted),x2:x(b.predicted),y1:y(b.wilson95[0]),y2:y(b.wilson95[1]),stroke:color,'stroke-width':3});
    draw('circle',{cx:x(b.predicted),cy:y(b.observed),r:5,fill:color});
    draw('text',{x:x(b.predicted)+8,y:y(b.observed)-8,fill:color,'font-size':13},b.name);
  });
  table(parent,['Band','N','Mean predicted','Observed','Wilson 95%'], bands.map(b => [b.name,b.n,fmt(b.predicted,true),fmt(b.observed,true),b.wilson95.map(v=>fmt(v,true)).join(' – ')]));
}
function table(parent, headers, rows) {
  const wrap = node('div',null,parent); wrap.style.overflowX = 'auto';
  const t = node('table',null,wrap), head = node('tr',null,node('thead',null,t));
  headers.forEach(h=>node('th',h,head)); const body = node('tbody',null,t);
  rows.forEach(row=>{const tr=node('tr',null,body);row.forEach(value=>node('td',value,tr));});
}
let serial = 0;
async function show() {
  const request = ++serial, day = $('day').value, sample = $('sample').value;
  $('status').textContent = 'Loading report…'; $('report').replaceChildren();
  try {
    const [report,status] = await Promise.all([get(`reports/${day}.json`),get('reveal-status.json')]);
    if (request!==serial) return;
    if (report.schema!=='hodlodds.daily_report.v1' || report.day!==day || !status.revealed_through || status.revealed_through<`${day}T23:59Z`) throw Error('Report is not a fully revealed day');
    const parent = $('report');
    node('h2',`${day} UTC`,parent);
    const links = node('p',null,parent);
    link(links,'JSON',`reports/${day}.json`); node('span',' · ',links);link(links,'Plain English summary',`reports/${day}.md`);
    node('p',`Recompute: python3 verify.py report ${day} (also download report_metrics.py).`,parent);
    for (const [name,title] of [['daily','Latest selected day'],['rolling_7d','Seven calendar days'],['since_launch','Since launch']]) {
      const period=report.periods[name], s=period[sample], section=node('section',null,parent);
      node('h2',`${title}: ${period.start} – ${period.end}`,section);
      table(section,['Committed SOL','Labelled','Dropped','Experimental excluded','Crash rate','Brier','Brier skill','Log loss','AP','AP/prevalence','O/E','Slope'],[[s.n_committed,s.n_labelled,s.n_dropped,s.n_experimental,fmt(s.crash_rate,true),fmt(s.brier),fmt(s.brier_skill),fmt(s.log_loss),fmt(s.ap),fmt(s.ap_over_prevalence),fmt(s.observed_expected),fmt(s.calibration_slope)]]);
      node('p',`Alert precision: ${fmt(s.alerts.precision,true)}; recall ≥5m: ${fmt(s.alerts.recall_ge_5m,true)}; recall ≥15m: ${fmt(s.alerts.recall_ge_15m,true)}. Prediction-time proxy.`,section);
      if (name!=='since_launch') chart(section,s.bands,`${title} calibration`);
    }
    const misses=report.periods.daily[sample].misses;
    for (const [name,title] of [['confident_noncrashes','Highest predicted risk without a crash'],['unwarned_crashes','Lowest predicted risk with a crash']]) {
      node('h2',title,parent);
      table(parent,['Mint','UTC minute','Probability','Outcome','First crossing offset (min)'],misses[name].map(r=>[r.mint,new Date(r.t*1000).toISOString(),fmt(r.p,true),r.y,r.first_cross_min??'—']));
    }
    node('h2','Per-prediction outcome labels',parent);
    const url = new URL(status.url);
    if (url.protocol!=='https:') throw Error('Labels require HTTPS');
    const labels = node('p',null,parent);
    for (const hh of Object.keys(report.label_files).sort()) {
      link(labels,`${hh}:00`,`${url.href.replace(/\/$/,'')}/${day}/${hh}.labels.tsv.gz`);node('span',' ',labels);
    }
    $('status').textContent = 'Published report loaded. Use the CLI to verify all arithmetic and prediction hashes.';
  } catch(e) { if(request===serial) {$('report').replaceChildren();$('status').textContent=e.message;} }
}
async function start() {
  try {
    const index=await get('reports/index.json');
    const days=Object.keys(index).filter(d=>/^\d{4}-\d{2}-\d{2}$/.test(d)).sort().reverse();
    if(!days.length) { $('status').textContent='No fully revealed daily reports yet.'; return; }
    days.forEach(d=>{const o=node('option',d,$('day'));o.value=d;});
    $('day').disabled=false; $('day').addEventListener('change',show);$('sample').addEventListener('change',show);await show();
  } catch(e) { $('status').textContent=`No report available: ${e.message}`; }
}
start();
