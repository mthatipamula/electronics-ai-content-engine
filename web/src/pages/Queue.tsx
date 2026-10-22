import { useMemo, useState } from 'react';
import { api, post } from '../api';
import { ago, pct, usd } from '../format';
import { useLoad } from '../hooks';
import type { JobSummary, Product, Status } from '../types';

const TABS: { id: string; label: string; match: (j: JobSummary) => boolean }[] = [
  { id: 'review', label: 'Awaiting review', match: (j) => j.status === 'PENDING_REVIEW' },
  { id: 'active', label: 'In progress', match: (j) => j.status === 'QUEUED' || j.status === 'RUNNING' },
  { id: 'failed', label: 'Failed', match: (j) => j.status === 'FAILED' },
  { id: 'done', label: 'Decided', match: (j) => j.status === 'APPROVED' || j.status === 'REJECTED' },
  { id: 'all', label: 'All', match: () => true },
];

export const STATUS_LABEL: Record<Status, string> = {
  QUEUED: 'Queued', RUNNING: 'Generating', PENDING_REVIEW: 'Awaiting review', APPROVED: 'Approved',
  REJECTED: 'Rejected', FAILED: 'Failed checks',
};

export function StatusTag({ status, flagged }: { status: Status; flagged?: boolean }) {
  const icon = { QUEUED: '◷', RUNNING: '◌', PENDING_REVIEW: '●', APPROVED: '✓', REJECTED: '✕', FAILED: '!' }[status];
  return (
    <span className={`tag status-${status.toLowerCase()}`}>
      {icon} {STATUS_LABEL[status]}{flagged && <span className="flag"> · needs attention</span>}
    </span>
  );
}

export default function Queue() {
  const [tab, setTab] = useState('review');
  const jobs = useLoad(() => api<JobSummary[]>('/api/jobs?limit=300'), [],
    (d) => (d?.some((j) => j.status === 'QUEUED' || j.status === 'RUNNING') ? 2000 : null));
  const [showNew, setShowNew] = useState(false);
  const rows = (jobs.data ?? []).filter(TABS.find((t) => t.id === tab)!.match);

  return (
    <div className="page">
      <div className="page-head">
        <h1>Content queue</h1>
        <button className="primary" onClick={() => setShowNew((v) => !v)}>{showNew ? 'Close' : 'Generate content'}</button>
      </div>
      {showNew && <NewRequest onDone={() => { setShowNew(false); setTab('active'); jobs.reload(); }} />}
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'active' : ''}
            onClick={() => setTab(t.id)}>
            {t.label} <span className="count">{(jobs.data ?? []).filter(t.match).length}</span>
          </button>
        ))}
      </div>
      {jobs.error && <div className="notice error">{jobs.error}</div>}
      <div className="card table-wrap">
        <table>
          <thead><tr><th>Product</th><th>Draft title</th><th>Channel</th><th>Status</th><th className="num">Attempts</th>
            <th className="num">Faithfulness</th><th className="num">Cost</th><th>Updated</th></tr></thead>
          <tbody>
            {rows.map((j) => (
              <tr key={j.id} className="clickable" onClick={() => { window.location.hash = `#/job/${j.id}`; }}>
                <td><a href={`#/job/${j.id}`}><strong>{j.sku}</strong></a><div className="small muted">{j.category}</div></td>
                <td className="title-cell">{j.title ?? <span className="muted">—</span>}</td>
                <td>{j.channel}</td>
                <td><StatusTag status={j.status} flagged={j.needs_attention} /></td>
                <td className="num">{j.attempts}</td>
                <td className="num">{pct(j.faithfulness)}</td>
                <td className="num">{usd(j.cost_usd)}</td>
                <td className="small muted">{ago(j.updated_at)}</td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={8} className="muted">Nothing here.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function NewRequest({ onDone }: { onDone: () => void }) {
  const catalog = useLoad(() => api<{ categories: { category: string; products: number }[]; products: Product[] }>(
    '/api/products'), []);
  const [category, setCategory] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [channel, setChannel] = useState('web');
  const [tone, setTone] = useState('technical');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const products = useMemo(() => (catalog.data?.products ?? []).filter((p) => !category || p.category === category),
    [catalog.data, category]);

  const toggle = (sku: string) => setSelected((s) => {
    const n = new Set(s);
    if (n.has(sku)) n.delete(sku); else n.add(sku);
    return n;
  });
  const submit = async () => {
    setBusy(true); setError(null);
    try {
      await post('/api/jobs', { skus: [...selected], channel, tone });
      onDone();
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <section className="card new-request">
      <div className="row">
        <label>Category
          <select value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">All categories</option>
            {catalog.data?.categories.map((c) => <option key={c.category}>{c.category}</option>)}
          </select>
        </label>
        <label>Channel
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            <option value="web">Website product page</option><option value="marketplace">Marketplace listing</option>
          </select>
        </label>
        <label>Tone
          <select value={tone} onChange={(e) => setTone(e.target.value)}>
            <option>technical</option><option>concise</option><option>friendly</option>
          </select>
        </label>
        <button className="link" onClick={() => setSelected(new Set(products.map((p) => p.sku)))}>Select all shown</button>
        <button className="link" onClick={() => setSelected(new Set())}>Clear</button>
      </div>
      {catalog.error && <div className="notice error">{catalog.error}</div>}
      <div className="product-grid">
        {products.map((p) => (
          <label key={p.sku} className={`product ${selected.has(p.sku) ? 'selected' : ''}`}>
            <input type="checkbox" checked={selected.has(p.sku)} onChange={() => toggle(p.sku)} />
            <span><strong>{p.sku}</strong><span className="small muted">{p.name}</span></span>
          </label>
        ))}
      </div>
      {error && <div className="notice error">{error}</div>}
      <div className="row end">
        <span className="muted small">{selected.size} selected (max 50)</span>
        <button className="primary" disabled={busy || selected.size === 0 || selected.size > 50} onClick={submit}>
          Generate {selected.size || ''} draft{selected.size === 1 ? '' : 's'}
        </button>
      </div>
    </section>
  );
}
