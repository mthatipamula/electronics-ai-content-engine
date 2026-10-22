import { useEffect, useState } from 'react';
import { useReviewer } from './hooks';
import Queue from './pages/Queue';
import Review from './pages/Review';
import Costs from './pages/Costs';
import Evals from './pages/Evals';

function route() {
  const [, page = 'queue', id] = window.location.hash.split('/');
  return { page, id };
}

export default function App() {
  const [r, setR] = useState(route);
  const [reviewer, setReviewer] = useReviewer();
  useEffect(() => {
    const on = () => setR(route());
    window.addEventListener('hashchange', on);
    return () => window.removeEventListener('hashchange', on);
  }, []);

  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">Content Review</div>
        <nav>
          {[['queue', 'Queue'], ['costs', 'Quality & cost'], ['evals', 'Evaluations']].map(([id, label]) => (
            <a key={id} href={`#/${id}`} className={r.page === id || (id === 'queue' && r.page === 'job') ? 'active' : ''}>{label}</a>
          ))}
        </nav>
        <label className="reviewer">Reviewer
          <input value={reviewer} placeholder="Your name" onChange={(e) => setReviewer(e.target.value)} maxLength={80} />
        </label>
      </header>
      <main>
        {r.page === 'job' && r.id ? <Review id={r.id} reviewer={reviewer} />
          : r.page === 'costs' ? <Costs />
          : r.page === 'evals' ? <Evals />
          : <Queue />}
      </main>
    </div>
  );
}
