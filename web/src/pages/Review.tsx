import { useEffect, useState } from 'react';
import { api, post } from '../api';
import { pct, usd } from '../format';
import { useLoad } from '../hooks';
import type { Content, Job } from '../types';
import { StatusTag } from './Queue';

const CATEGORIES = [
  ['inaccurate', 'Inaccurate'], ['missing_info', 'Missing information'], ['tone', 'Tone or style'],
  ['formatting', 'Formatting'], ['compliance', 'Compliance or legal'], ['other', 'Other'],
] as const;

export default function Review({ id, reviewer }: { id: string; reviewer: string }) {
  const job = useLoad(() => api<Job>(`/api/jobs/${id}`), [id],
    (d) => (d && (d.status === 'QUEUED' || d.status === 'RUNNING') ? 2000 : null));
  const [draft, setDraft] = useState<Content | null>(null);
  const [rejecting, setRejecting] = useState(false);
  const [category, setCategory] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const j = job.data;
  useEffect(() => { if (j) setDraft(j.final_content ?? j.content); }, [j]);
  if (!j) return <div className="page">{job.error ? <div className="notice error">{job.error}</div> : <p className="muted">Loading…</p>}</div>;

  const reviewable = j.status === 'PENDING_REVIEW';
  const edited = !!draft && !!j.content && JSON.stringify(draft) !== JSON.stringify(j.content);
  const facts = j.sources?.facts;
  const cited = new Set(j.content?.cited_spec_keys ?? []);

  const decide = async (decision: 'approve' | 'reject') => {
    setBusy(true); setError(null);
    try {
      await post(`/api/jobs/${id}/decision`, decision === 'approve'
        ? { reviewer, decision, content: edited ? draft : null, comment: comment || null }
        : { reviewer, decision, category, comment });
      setRejecting(false); job.reload();
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  };

  return (
    <div className="page">
      <a className="back small" href="#/queue">← Queue</a>
      <div className="page-head">
        <div>
          <h1>{facts?.name ?? j.sku}</h1>
          <div className="row tight"><StatusTag status={j.status} flagged={j.judge?.needs_attention} />
            <span className="muted small">{j.sku} · {j.channel} · {j.tone} · attempts {j.attempts} · {usd(j.cost_usd)}</span></div>
        </div>
      </div>

      {j.error && <div className="notice error">Pipeline error: {j.error}</div>}
      {j.judge?.needs_attention && (
        <div className="notice warn">⚠ The automated checks passed, but the faithfulness judge still objected after {j.attempts} attempts.
          Check its concerns below; judges can be wrong.</div>)}
      {j.status === 'APPROVED' && <div className="notice ok">✓ Approved by {j.reviewed_by}{j.edited ? ' with edits' : ''}; saved as draft
        version {j.draft_version}.</div>}
      {j.status === 'REJECTED' && <div className="notice">✕ Rejected by {j.reviewed_by} ({j.review_category}): {j.review_comment}.
        This comment now guides future drafts in {j.category}.</div>}

      <div className="split">
        <section className="card">
          <h2>Sources</h2>
          {j.sources?.withheld.length ? (
            <div className="notice warn small">⚠ {j.sources.withheld.length} datasheet passage(s) withheld from the model:
              {j.sources.withheld.map((w) => <div key={w.chunk_id}>chunk {w.chunk_id}: {w.reason}</div>)}</div>) : null}
          {facts && (<>
            <h3>Product data</h3>
            <table className="specs"><tbody>
              {Object.entries(facts.specs).map(([k, v]) => (
                <tr key={k} className={cited.has(k) ? 'cited' : ''}><th>{k.replace(/_/g, ' ')}</th><td>{v}</td>
                  <td className="small muted">{cited.has(k) ? 'cited' : ''}</td></tr>))}
            </tbody></table>
          </>)}
          <h3>Datasheet passages ({j.sources?.passages.length ?? 0})</h3>
          {j.sources?.passages.map((p) => (
            <blockquote key={p.chunk_id}><div className="small muted">chunk {p.chunk_id} · relevance {p.score.toFixed(4)}</div>{p.text}</blockquote>
          ))}
          {j.sources?.guidance.length ? (<>
            <h3>Reviewer guidance applied</h3>
            <ul className="small">{j.sources.guidance.map((g, i) => <li key={i}>{g}</li>)}</ul>
          </>) : null}
        </section>

        <section className="card">
          <h2>Draft {edited && <span className="tag">edited</span>}</h2>
          {!draft ? <p className="muted">{j.status === 'QUEUED' || j.status === 'RUNNING' ? 'Generating…' : 'No draft was produced.'}</p> : (
            <DraftEditor value={draft} onChange={setDraft} readOnly={!reviewable} />)}

          {j.guardrails && (<>
            <h3>Guardrails {j.guardrails.passed ? <span className="tag ok">✓ passed</span> : <span className="tag bad">! failed</span>}</h3>
            {j.guardrails.findings.length === 0 ? <p className="small muted">No findings.</p> : (
              <ul className="findings">{j.guardrails.findings.map((f, i) => (
                <li key={i} className={f.severity}><strong>{f.severity === 'error' ? '!' : '⚠'} {f.rule}</strong> {f.detail}</li>))}</ul>)}
          </>)}

          {j.judge && (<>
            <h3>Faithfulness {pct(j.judge.score)} {j.judge.passed ? <span className="tag ok">✓</span> : <span className="tag warn">⚠</span>}</h3>
            <ul className="claims">{j.judge.claims.map((c, i) => (
              <li key={i} className={c.supported ? 'ok' : 'bad'}><span>{c.supported ? '✓' : '✕'} {c.claim}</span>
                <span className="small muted">{c.evidence}</span></li>))}</ul>
          </>)}

          {reviewable && (
            <div className="actions">
              {!reviewer && <div className="notice warn small">Enter your name in the top bar to review.</div>}
              {!rejecting ? (
                <div className="row end">
                  <input className="grow" placeholder={edited ? 'What did you change? (teaches future drafts)' : 'Comment (optional)'}
                    value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} />
                  <button className="danger" disabled={busy || !reviewer} onClick={() => setRejecting(true)}>Reject…</button>
                  <button className="primary" disabled={busy || !reviewer} onClick={() => decide('approve')}>
                    {edited ? 'Approve with edits' : 'Approve'}</button>
                </div>
              ) : (
                <div className="reject">
                  <label>Reason
                    <select value={category} onChange={(e) => setCategory(e.target.value)}>
                      <option value="">Choose…</option>
                      {CATEGORIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                    </select>
                  </label>
                  <label>What should change? This becomes guidance for future {j.category} drafts.
                    <textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} maxLength={500} />
                  </label>
                  <div className="row end">
                    <button className="secondary" onClick={() => setRejecting(false)}>Cancel</button>
                    <button className="danger" disabled={busy || !category || comment.trim().length < 5} onClick={() => decide('reject')}>
                      Reject draft</button>
                  </div>
                </div>
              )}
              {error && <div className="notice error">{error}</div>}
            </div>
          )}
        </section>
      </div>

      <section className="card">
        <h2>How it was made</h2>
        <div className="steps">{j.steps.map((s, i) => (
          <div key={i} className="step"><strong>{s.step}</strong><span className="small muted">{s.ms} ms</span>
            <span className="small">{Object.entries(s).filter(([k]) => !['step', 'ms'].includes(k))
              .map(([k, v]) => `${k}: ${typeof v === 'string' && v.length > 80 ? v.slice(0, 80) + '…' : v}`).join(' · ')}</span></div>))}</div>
        <div className="table-wrap"><table>
          <thead><tr><th>Model call</th><th>Tier</th><th>Deployment</th><th>Route</th><th className="num">Latency</th><th className="num">Cost</th></tr></thead>
          <tbody>{j.model_calls.map((c, i) => (
            <tr key={i}><td>{c.step}</td><td>{c.tier}</td><td className="mono small">{c.deployment}</td>
              <td className="small">{c.attempts.join(' → ')}{c.cached ? ' (cache hit)' : ''}</td>
              <td className="num">{c.latency_ms} ms</td><td className="num">{usd(c.cost_usd, 5)}</td></tr>))}</tbody>
          <tfoot><tr><td colSpan={4}>{j.prompt_tokens.toLocaleString()} input + {j.completion_tokens.toLocaleString()} output tokens</td>
            <td /><td className="num">{usd(j.cost_usd, 5)}</td></tr></tfoot>
        </table></div>
      </section>
    </div>
  );
}

function DraftEditor({ value, onChange, readOnly }: { value: Content; onChange: (c: Content) => void; readOnly: boolean }) {
  const set = <K extends keyof Content>(k: K, v: Content[K]) => onChange({ ...value, [k]: v });
  const count = (s: string, max: number) => <span className={`small ${s.length > max ? 'over' : 'muted'}`}>{s.length}/{max}</span>;
  return (
    <div className="editor">
      <label>Title {count(value.title, 150)}
        <input value={value.title} readOnly={readOnly} onChange={(e) => set('title', e.target.value)} /></label>
      <label>Short description {count(value.short_description, 300)}
        <textarea rows={2} value={value.short_description} readOnly={readOnly} onChange={(e) => set('short_description', e.target.value)} /></label>
      <label>Long description {count(value.long_description, 2000)}
        <textarea rows={6} value={value.long_description} readOnly={readOnly} onChange={(e) => set('long_description', e.target.value)} /></label>
      <div className="field">Bullets
        {value.bullets.map((b, i) => (
          <div key={i} className="bullet">
            <input value={b} readOnly={readOnly} onChange={(e) => set('bullets', value.bullets.map((x, k) => (k === i ? e.target.value : x)))} />
            {!readOnly && <button className="link" aria-label="Remove bullet" onClick={() => set('bullets', value.bullets.filter((_, k) => k !== i))}>✕</button>}
          </div>))}
        {!readOnly && value.bullets.length < 6 && <button className="link small" onClick={() => set('bullets', [...value.bullets, ''])}>+ Add bullet</button>}
      </div>
      <label>SEO keywords
        <input value={value.seo_keywords.join(', ')} readOnly={readOnly}
          onChange={(e) => set('seo_keywords', e.target.value.split(',').map((s) => s.trim()).filter(Boolean))} /></label>
    </div>
  );
}
