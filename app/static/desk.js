import { element, folio, paintEvidence, paintExtras, stageTable, states } from './evidence.js';
import { paintRevision } from './revision.js';

const $ = id => document.getElementById(id);
const sample = $('sample');
const selectionButtons = [...document.querySelectorAll('[data-stage]')];
const metrics = { expected: '应到期位', covered: '已覆盖', missing: '缺刊', disputed: '争议', supplements: '增刊件' };
const changeLabels = JSON.parse(document.querySelector('#change-labels').textContent);
let ledger = null;
let stage = 'balance';
let currentSource = null;
let activeRun = null;
let signature = null;
let revisionDocs = [];
let activeRevisionId = null;
let selected = null;
let busy = false;
let revision = 0;

async function api(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.message || '读取失败，请重试');
  }
  return response.json();
}

function lane() {
  if (!activeRun) return { balance: ledger, label: '当前清册', delta: null, extras: null };
  if (!activeRevisionId) {
    return { balance: activeRun.result.balance, label: '交班签收', delta: null,
      extras: { supplements: activeRun.result.supplements, duplicates: activeRun.result.duplicates } };
  }
  const revisionDoc = revisionDocs.find(item => item.id === activeRevisionId);
  return revisionDoc
    ? { balance: revisionDoc.result.balance, label: revisionDoc.supplement.note || '补录修订',
      delta: revisionDoc.baseline_delta,
      extras: { supplements: revisionDoc.result.supplements, duplicates: revisionDoc.result.duplicates } }
    : { balance: ledger, label: '当前清册', delta: null, extras: null };
}

function changeIndex() {
  if (!activeRevisionId) return {};
  const revisionDoc = revisionDocs.find(item => item.id === activeRevisionId);
  const map = {};
  // 期位表比较的是“较签收版”，因此用 baseline_delta；单批变化在修订对照里另列。
  for (const change of revisionDoc?.baseline_delta.changes || []) {
    map[`${change.chain}|${change.year}|${change.issue}`] = change;
  }
  return map;
}

function paintRows() {
  const current = lane();
  if (!current.balance) return;
  const changed = changeIndex();
  const rows = current.balance.slots.filter(row => ($('chain').value === 'all' || row.chain === $('chain').value)
    && ($('filter').value === 'all' || row.status === $('filter').value));
  $('register').replaceChildren();
  if (!rows.length) {
    const cell = $('register').insertRow().insertCell();
    cell.colSpan = 6;
    cell.textContent = '试试其他筛选条件。';
  }
  for (const slot of rows) {
    const row = $('register').insertRow();
    row.dataset.status = slot.status;
    const delta = changed[`${slot.chain}|${slot.year}|${slot.issue}`];
    row.classList.toggle('selected', selected === slot);
    row.classList.toggle('changed', Boolean(delta));
    row.insertCell().textContent = slot.chain;
    row.insertCell().textContent = folio(slot);
    row.insertCell().append(element('span', states[slot.status], 'seal'));
    const badge = row.insertCell();
    badge.append(delta
      ? element('span', changeLabels[delta.reason] || delta.reason, 'seal delta-badge')
      : element('span', '—', 'hint'));
    row.insertCell().append(element('code', slot.evidence.map(item => item.accession).join(' / ') || '—'));
    const button = element('button', '看原件');
    button.type = 'button';
    button.setAttribute('aria-label', `查看 ${slot.chain} ${folio(slot)}`);
    button.addEventListener('click', () => {
      selected = slot;
      document.querySelector('.selected')?.classList.remove('selected');
      row.classList.add('selected');
      paintEvidence(delta ? { ...slot, before: delta.before, reason: delta.reason } : slot);
    });
    row.insertCell().append(button);
  }
  const totals = current.balance.totals;
  $('sum').textContent = `显示 ${rows.length} / ${totals.expected} 期 · ${
    current.balance.status === 'MATCHED' ? '期位与行数已对平' : '存在勾稽差额'} · ${current.label}`;
  if (current.extras) paintExtras(current.extras.supplements, current.balance.quarantine, current.extras.duplicates);
}

function paintTotals() {
  const current = lane();
  if (!current.balance) return;
  const reference = current.balance.totals;
  $('totals').replaceChildren(...Object.entries(metrics).map(([key, label]) => {
    const metric = element('div', undefined, 'kpi');
    metric.dataset.metric = key;
    metric.append(element('span', label), element('strong', reference[key]));
    return metric;
  }));
}

function paintVersions() {
  const pick = $('version');
  const options = [new Option('交班签收', 'baseline')];
  revisionDocs.forEach((document, index) => {
    const note = document.supplement.note ? ` · ${document.supplement.note}` : '';
    options.push(new Option(`修订 ${index + 1}${note} · ${document.id.slice(0, 8)}`, document.id));
  });
  pick.replaceChildren(...options);
  pick.value = activeRevisionId || 'baseline';
  pick.hidden = !activeRun;
}

function showRevision(revisionDoc) {
  activeRevisionId = revisionDoc ? revisionDoc.id : null;
  paintVersions();
  paintTotals();
  paintRows();
  paintRevision(revisionDoc || null);
  paintEvidence(null);
}

async function loadLineage(run) {
  activeRun = run;
  activeRevisionId = null;
  revisionDocs = [];
  signature = null;
  paintVersions();
  $('sign').disabled = !run;
  $('revision').disabled = true;
  $('signature-ref').textContent = '未签收';
  $('sign-hint').textContent = '签收后，交班依据固定；补录不改写签收版，另生成可追溯修订版。';
  $('revision-summary').hidden = true;
  paintRevision(null);
  if (!run) return;
  try {
    const state = await api(`/api/runs/${run.id}/signature`);
    signature = state.signature;
    $('sign').disabled = Boolean(signature) || run.kind === 'revision';
    $('signature-ref').textContent = signature
      ? `已签收 ${signature.signed_at}${signature.signer ? ` · ${signature.signer}` : ''}`
      : '未签收';
    $('sign-hint').textContent = signature
      ? '交班依据已固定为签收版。补录进入当天判断，签收版保持原样。'
      : '签收后，交班依据固定；补录不改写签收版，另生成可追溯修订版。';
    $('revision').disabled = !signature;
    revisionDocs = await Promise.all(state.revisions.map(item => api(`/api/runs/${item.id}`)));
    paintLineageSummary(state);
    const latest = revisionDocs[revisionDocs.length - 1];
    showRevision(latest || null);
  } catch (error) {
    $('run-feedback').textContent = error.message;
  }
}

function paintLineageSummary(state) {
  const summary = $('revision-summary');
  if (!state.revisions.length) {
    summary.hidden = true;
    return;
  }
  const latest = state.revisions[state.revisions.length - 1];
  summary.hidden = false;
  summary.textContent = `签收版 ${state.baseline_id.slice(0, 12)} · 修订 ${state.revisions.length} 版 · 现行 ${latest.id.slice(0, 12)} · 签收 ${state.baseline_status} → 当天 ${state.current_status}`;
}

function setArchive(run) {
  $('rerun').disabled = !run || busy;
  $('export').hidden = !run;
  $('run-metadata').textContent = run ? `${run.id.slice(0, 12)} · ${run.manifest.generated_at}` : '未归档';
  if (run) $('export').href = `/api/runs/${run.id}/export`;
}

async function readStage(name) {
  stage = name;
  for (const link of selectionButtons) {
    if (link.dataset.stage === name) link.setAttribute('aria-current', 'location');
    else link.removeAttribute('aria-current');
  }
  if (name === 'balance' || name === 'runs') {
    $('stage').hidden = true;
    return;
  }
  $('stage').hidden = false;
  $('stage-title').textContent = selectionButtons.find(link => link.dataset.stage === name).textContent;
  $('stage-body').textContent = '读取阶段材料…';
  $('stage').scrollIntoView({ block: 'nearest' });
  if (!currentSource) {
    $('stage-body').textContent = '请先核算清册。';
    return;
  }
  const initialRevision = revision;
  try {
    const data = currentSource.sample ? await api(`/api/${name}/${currentSource.sample}`)
      : await api(`/api/${name}`, currentSource);
    if (initialRevision === revision && stage === name) $('stage-body').replaceChildren(stageTable(name, data));
  } catch (error) {
    if (initialRevision === revision && stage === name) $('stage-body').textContent = error.message;
  }
}

async function preview() {
  const token = ++revision;
  const choice = sample.value;
  $('custom').hidden = choice !== 'custom';
  $('status').textContent = '正在勾稽…';
  $('run-feedback').textContent = '';
  $('revision-feedback').textContent = '';
  $('save').disabled = true;
  currentSource = null;
  ledger = null;
  activeRun = null;
  setArchive(null);
  $('totals').replaceChildren();
  $('register').replaceChildren();
  $('sum').textContent = '';
  $('extras').replaceChildren();
  paintEvidence(null);
  paintRevision(null);
  $('revisions').hidden = true;
  try {
    let source = { sample: choice };
    if (choice === 'custom') {
      const text = $('source-data').value.trim();
      if (!text) throw new Error('请输入清册 JSON');
      try { source = { data: JSON.parse(text) }; }
      catch { throw new Error('JSON 格式有误，请检查后重试'); }
    }
    const get = name => source.sample ? api(`/api/${name}/${choice}`) : api(`/api/${name}`, source);
    const [data, claims, intake] = await Promise.all([get('balance'), get('combined'), get('receipts')]);
    if (token !== revision) return;
    ledger = data;
    currentSource = source;
    selected = null;
    $('chain').replaceChildren(new Option('全部', 'all'), ...[...new Set(data.slots.map(slot => slot.chain))].map(key => new Option(key, key)));
    $('totals').replaceChildren(...Object.entries(metrics).map(([key, label]) => {
      const metric = element('div', undefined, 'kpi');
      metric.dataset.metric = key;
      metric.append(element('span', label), element('strong', data.totals[key]));
      return metric;
    }));
    const years = data.years.map(row => row.year);
    $('edition-note').textContent = `${Math.min(...years)}—${Math.max(...years)} 年 · ${data.row_totals.input} 条来刊记录`;
    paintRows();
    paintExtras(claims.supplements, data.quarantine, intake.duplicates);
    $('status').textContent = '清册已核算';
    await loadLineage(null);
    if (stage !== 'balance' && stage !== 'runs') readStage(stage);
  } catch (error) {
    if (token !== revision) return;
    $('status').textContent = error.message;
    $('edition-note').textContent = '等待有效清册';
    if (stage !== 'balance' && stage !== 'runs') readStage(stage);
  } finally {
    if (token === revision) $('save').disabled = !currentSource || busy;
  }
}

async function save(reuse) {
  if (!currentSource || busy || (reuse && !activeRun)) return;
  const token = revision;
  busy = true;
  $('save').disabled = $('rerun').disabled = true;
  $('run-feedback').textContent = '正在归档…';
  try {
    const run = await api(reuse ? `/api/runs/${activeRun.id}/rerun` : '/api/runs', reuse ? {} : currentSource);
    if (token !== revision) return;
    setArchive(run);
    $('run-feedback').textContent = reuse ? '重跑一致' : (run.reused ? '已定位原归档' : '证据已归档');
    await loadLineage(run);
    if (run.reused) $('run-feedback').textContent = '已定位原归档';
  } catch (error) {
    if (token === revision) $('run-feedback').textContent = error.message;
  } finally {
    busy = false;
    $('save').disabled = !currentSource;
    $('rerun').disabled = !activeRun;
  }
}

async function sign() {
  if (!activeRun || busy) return;
  busy = true;
  $('sign').disabled = true;
  $('run-feedback').textContent = '正在签收…';
  try {
    await api(`/api/runs/${activeRun.id}/sign`, { signer: $('signer').value.trim() || null, note: $('sign-note').value.trim() || null });
    $('run-feedback').textContent = '交班签收已固定';
    await loadLineage(activeRun);
  } catch (error) {
    $('run-feedback').textContent = error.message;
    $('sign').disabled = false;
  } finally {
    busy = false;
  }
}

async function submitRevision() {
  if (!activeRun || !signature || busy) return;
  const text = $('supplement-data').value.trim();
  if (!text) {
    $('revision-feedback').textContent = '请粘贴补录 JSON';
    return;
  }
  let payload;
  try { payload = JSON.parse(text); }
  catch {
    $('revision-feedback').textContent = 'JSON 格式有误，请检查后重试';
    return;
  }
  busy = true;
  $('revision').disabled = true;
  $('revision-feedback').textContent = '正在登记补录…';
  try {
    const revisionDoc = await api(`/api/runs/${activeRun.id}/revisions`, payload);
    const listing = await api(`/api/runs/${activeRun.id}/signature`);
    revisionDocs = await Promise.all(listing.revisions.map(item => api(`/api/runs/${item.id}`)));
    paintLineageSummary(listing);
    $('revision-feedback').textContent = revisionDoc.reused ? '同一补录，已定位既有修订版' : '修订版已生成';
    showRevision(revisionDocs.find(item => item.id === revisionDoc.id) || revisionDoc);
  } catch (error) {
    $('revision-feedback').textContent = error.message;
  } finally {
    busy = false;
    $('revision').disabled = !signature;
  }
}

for (const link of selectionButtons) {
  link.addEventListener('click', event => {
    const name = link.dataset.stage;
    if (name !== 'balance' && name !== 'runs') event.preventDefault();
    readStage(name);
  });
}
$('chain').addEventListener('change', paintRows);
$('filter').addEventListener('change', paintRows);
sample.addEventListener('change', preview);
$('preview').addEventListener('click', preview);
$('save').addEventListener('click', () => save(false));
$('rerun').addEventListener('click', () => save(true));
$('sign').addEventListener('click', sign);
$('revision').addEventListener('click', submitRevision);
$('version').addEventListener('change', () => {
  const value = $('version').value;
  showRevision(value === 'baseline' ? null : revisionDocs.find(item => item.id === value) || null);
});
$('print').addEventListener('click', () => window.print());
preview();
