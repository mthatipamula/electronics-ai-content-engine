import { api } from '../api';
import { pct, usd } from '../format';
import { useLoad } from '../hooks';
import { Stat } from './Costs';

interface Gate { metric: string; value: number; op: string; threshold: number; passed: boolean }
interface Case {
  id: string; sku: string; channel: string; outcome: string; attempts: number; faithfulness: number | null;
  spec_accuracy: number; must_mention_recall: number; forbidden_hits: string[]; cost_usd: number; notes: string[];
}
interface Report {
  run_id: string; created_at: string; provider: string; deployments: Record<string, string>; passed: boolean;
  summary: Record<string, number>; gates: Gate[]; cases: Case[];
}

const LABELS: Record<string, string> = {
  cases: 'Cases', schema_valid_rate: 'Valid structured output', guardrail_pass_rate: 'Guardrails passed',
  spec_accuracy: 'Spec accuracy', must_mention_recall: 'Required facts mentioned', forbidden_claim_rate: 'Forbidden claims',
  avg_faithfulness: 'Avg faithfulness', first_pass_rate: 'First-attempt success', injection_blocked_rate: 'Injection blocked',
  avg_cost_usd: 'Cost per case', p95_latency_s: 'p95 latency (s)',
};

export default function Evals() {
  const r = useLoad(() => api<Report>('/api/evals/latest'), []);
  if (r.error) return <div className="page"><h1>Evaluations</h1><div className="notice">{r.error}</div></div>;
  if (!r.data) return <div className="page"><p className="muted">Loading…</p></div>;
  const d = r.data;
  const fmt = (k: string, v: number) => (k === 'cases' ? String(v) : k === 'avg_cost_usd' ? usd(v) : k === 'p95_latency_s' ? v.toFixed(1) : pct(v));
  return (
    <div className="page">
      <div className="page-head"><h1>Evaluations</h1>
        <span className={`tag ${d.passed ? 'ok' : 'bad'}`}>{d.passed ? '✓ all gates passed' : '! regression gate failed'}</span></div>
      <p className="muted small">Run {d.run_id} · {new Date(d.created_at).toLocaleString()} · {d.provider} · {Object.entries(d.deployments).map(([k, v]) => `${k}: ${v}`).join(', ')}</p>
      <div className="stats">{Object.entries(d.summary).map(([k, v]) => <Stat key={k} label={LABELS[k] ?? k} value={fmt(k, v)} />)}</div>
      <section className="card">
        <h2>Regression gates</h2>
        <table><thead><tr><th>Metric</th><th className="num">Value</th><th className="num">Required</th><th>Result</th></tr></thead>
          <tbody>{d.gates.map((g) => (
            <tr key={g.metric}><td>{LABELS[g.metric] ?? g.metric}</td><td className="num">{fmt(g.metric, g.value)}</td>
              <td className="num">{g.op} {fmt(g.metric, g.threshold)}</td>
              <td>{g.passed ? <span className="tag ok">✓ pass</span> : <span className="tag bad">! fail</span>}</td></tr>))}</tbody></table>
      </section>
      <section className="card table-wrap">
        <h2>Cases</h2>
        <table><thead><tr><th>Case</th><th>Product</th><th>Channel</th><th>Outcome</th><th className="num">Attempts</th><th className="num">Faithfulness</th>
          <th className="num">Spec accuracy</th><th className="num">Required facts</th><th>Notes</th><th className="num">Cost</th></tr></thead>
          <tbody>{d.cases.map((c) => (
            <tr key={c.id}><td className="mono small">{c.id}</td><td>{c.sku}</td><td>{c.channel}</td><td>{c.outcome}</td>
              <td className="num">{c.attempts}</td><td className="num">{pct(c.faithfulness)}</td><td className="num">{pct(c.spec_accuracy)}</td>
              <td className="num">{pct(c.must_mention_recall)}</td>
              <td className="small">{[...c.forbidden_hits.map((f) => `forbidden: ${f}`), ...c.notes].join('; ')}</td>
              <td className="num">{usd(c.cost_usd)}</td></tr>))}</tbody></table>
      </section>
    </div>
  );
}
