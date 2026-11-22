import { element, folio, states } from './evidence.js';

const changeLabels = JSON.parse(document.querySelector('#change-labels').textContent);
const supplementKinds = JSON.parse(document.querySelector('#supplement-kinds').textContent);
const labels = JSON.parse(document.querySelector('#reason-labels').textContent);

function evidenceLine(item) {
  const record = element('div', undefined, 'delta-evidence');
  record.dataset.origin = item.origin;
  const prefix = item.origin === 'supplement' ? `补录第 ${item.supplement_line} 行 · 原始行 ${item.line}`
    : item.origin === 'prior' ? `先前补录 · 原始行 ${item.line}`
    : `签收原件 · 原始行 ${item.line}`;
  record.append(
    element('span', prefix, 'folio'),
    element('strong', item.accession),
    element('span', item.raw?.title || ''),
    element('span', `原录 ${item.raw?.volume ?? '—'} 卷 ${item.raw?.issue ?? '—'} 期`),
  );
  return record;
}

function statusText(outcome) {
  return `${states[outcome.status] || outcome.status}${outcome.code ? ` · ${labels[outcome.code] || outcome.code}` : ''}`;
}

function slotChange(change) {
  const card = element('article', undefined, 'delta-card');
  card.dataset.reason = change.reason;
  const head = element('div', undefined, 'delta-head');
  head.append(
    element('strong', `${change.chain} · ${folio(change)}`),
    element('span', changeLabels[change.reason] || change.reason, 'seal'),
  );
  card.append(head);
  const compare = element('div', undefined, 'delta-compare');
  for (const [label, outcome] of [['上一版', change.before], ['本版', change.after]]) {
    const column = element('div', undefined, 'delta-column');
    column.dataset.origin = label === '上一版' ? 'baseline' : 'current';
    column.append(element('span', label, 'folio'), element('p', statusText(outcome), 'seal'));
    const list = element('div', undefined, 'delta-evidence-list');
    if (!outcome.evidence.length) list.append(element('p', '无来刊原件', 'hint'));
    for (const item of outcome.evidence) list.append(evidenceLine(item));
    column.append(list);
    compare.append(column);
  }
  card.append(compare);
  return card;
}

export function paintRevision(revisionDoc) {
  const panel = document.querySelector('#revisions');
  const body = document.querySelector('#revision-body');
  if (!revisionDoc) {
    panel.hidden = true;
    body.replaceChildren();
    return;
  }
  panel.hidden = false;
  const change = revisionDoc.signature_change;
  const summary = element('div', undefined, 'revision-summary');
  summary.append(
    element('span', `签收状态 ${change.status.baseline} · 当天状态 ${change.status.current}`, 'folio'),
    element('span', `应到期位 ${change.expected}`),
    element('span', `覆盖 ${change.baseline.covered} → ${change.current.covered}`),
    element('span', `缺刊 ${change.baseline.missing} → ${change.current.missing}`),
    element('span', `争议 ${change.baseline.disputed} → ${change.current.disputed}`),
    element('span', `本版较上一版改变 ${revisionDoc.delta.counts.changed} 处`),
    element('span', `较签收版累计改变 ${revisionDoc.baseline_delta.counts.changed} 处`),
  );
  const audit = element('div', undefined, 'revision-audit');
  for (const row of revisionDoc.supplement_audit.rows) {
    const line = element('p');
    line.dataset.kind = row.kind;
    line.append(
      element('span', `${supplementKinds[row.kind] || row.kind}`, 'seal'),
      element('span', ` 补录第 ${row.supplement_line} 行 · ${row.accession || '—'}${row.code ? ` · ${labels[row.code] || row.code}` : ''}`),
    );
    audit.append(line);
  }
  const cards = element('div', undefined, 'delta-grid');
  for (const item of revisionDoc.delta.changes) cards.append(slotChange(item));
  if (!revisionDoc.delta.changes.length) cards.append(element('p', '本期补录未改变任何期位。', 'hint'));
  body.replaceChildren(summary, audit, cards);
}
