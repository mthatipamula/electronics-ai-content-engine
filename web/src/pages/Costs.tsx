import { useState } from 'react';
import { api } from '../api';
import { num, pct, usd } from '../format';
import { useLoad } from '../hooks';
import type { Metrics, UsageRow } from '../types';

const GROUPS = [['feature', 'Business feature'], ['tier', 'Model tier'], ['task', 'Task'], ['day', 'Day']] as const;

export default function Costs() {
  const m = useLoad(() => api<Metrics>('/api/metrics'), []);
  const [group, setGroup] = useState<string>('feature');
  const [days, setDays] = useState(30);
  const u = useLoad(() => api<UsageRow[]>(`/api/usage?group_by=${group}&days=${days}`), [group, days]);
  const total = (u.data ?? []).reduce((a, r) => a + r.cost_usd, 0);
  const saved = (u.data ?? []).reduce((a, r) => a + r.saved_usd, 0);
  const rows = [...(u.data ?? [])].sort((a, b) => (group === 'day' ? String(b.key).localeCompare(String(a.key)) : b.cost_usd - a.cost_usd));

  return (
    <div className="page">
      <h1>Quality & cost</h1>
      {m.error && <div className="notice error">{m.error}</div>}
      <h2 className="section">Review outcomes</h2>
      <div className="stats">
        <Stat label="Approval rate" value={pct(m.data?.approval_rate)} sub={`${num(m.data?.approved)} of ${num(m.data?.reviewed)} reviewed`} />
        <Stat label="Approved without edits" value={pct(m.data?.first_pass_acceptance)} sub="the agent's copy shipped as written" />
        <Stat label="Avg faithfulness" value={pct(m.data?.avg_faithfulness)} sub="LLM judge, per draft" />
        <Stat label="Avg attempts" value={m.data?.avg_attempts?.toFixed(2) ?? '—'} sub="generation retries after checks" />
        <Stat label="Cost per draft" value={usd(m.data?.avg_cost_per_job)} sub={`${usd(m.data?.total_cost, 2)} total`} />
        <Stat label="Awaiting review" value={num(m.data?.pending_review)} sub={`${num(m.data?.failed)} failed checks`} />
      </div>
      {m.data && Object.keys(m.data.rejection_reasons).length > 0 && (
        <section className="card">
          <h3>Why reviewers rejected drafts</h3>
          <table><tbody>{Object.entries(m.data.rejection_reasons).map(([k, v]) => <tr key={k}><td>{k}</td><td className="num">{v}</td></tr>)}</tbody></table>
        </section>)}

      <div className="page-head">
        <h2 className="section">Model usage (from the LLM gateway ledger)</h2>
        <div className="row tight">
          <select value={group} onChange={(e) => setGroup(e.target.value)} aria-label="Group by">
            {GROUPS.map(([v, l]) => <option key={v} value={v}>By {l.toLowerCase()}</option>)}
          </select>
          <select value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label="Period">
            {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
          </select>
        </div>
      </div>
      <div className="stats">
        <Stat label="Model spend" value={usd(total, 4)} sub={`last ${days} day${days > 1 ? 's' : ''}`} />
        <Stat label="Saved by cache" value={usd(saved, 4)} sub="identical deterministic calls" />
        <Stat label="Requests" value={num((u.data ?? []).reduce((a, r) => a + r.requests, 0))} />
      </div>
      {u.error && <div className="notice error">{u.error}</div>}
      <div className="card table-wrap">
        <table>
          <thead><tr><th>{GROUPS.find(([v]) => v === group)![1]}</th><th className="num">Requests</th><th className="num">Input tokens</th>
            <th className="num">Output tokens</th><th className="num">Cost</th><th className="num">Share</th><th className="num">Cache hits</th>
            <th className="num">p95 latency</th><th className="num">Fallbacks</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={String(r.key)}><td>{r.key ?? '—'}</td><td className="num">{num(r.requests)}</td><td className="num">{num(r.prompt_tokens)}</td>
              <td className="num">{num(r.completion_tokens)}</td><td className="num">{usd(r.cost_usd)}</td>
              <td className="num">{total ? pct(r.cost_usd / total) : '—'}</td><td className="num">{pct(r.cache_hit_rate)}</td>
              <td className="num">{num(Math.round(r.p95_latency_ms))} ms</td><td className="num">{num(r.fallbacks)}</td></tr>))}
            {rows.length === 0 && <tr><td colSpan={9} className="muted">No usage in this period.</td></tr>}</tbody>
        </table>
      </div>
    </div>
  );
}

export function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div className="stat"><div className="stat-label">{label}</div><div className="stat-value">{value}</div>{sub && <div className="small muted">{sub}</div>}</div>;
}
