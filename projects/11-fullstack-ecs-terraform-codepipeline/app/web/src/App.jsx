import { useCallback, useEffect, useState } from 'react';

const OWNER = 'demo';

export default function App() {
  const [todos, setTodos] = useState([]);
  const [title, setTitle] = useState('');
  const [status, setStatus] = useState({ state: 'loading' });

  const load = useCallback(async () => {
    try {
      const response = await fetch(`/api/todos?ownerId=${OWNER}`);
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const data = await response.json();
      setTodos(data.items ?? []);
      setStatus({ state: 'ready' });
    } catch (error) {
      setStatus({ state: 'error', message: String(error.message ?? error) });
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function addTodo(event) {
    event.preventDefault();
    const trimmed = title.trim();
    if (!trimmed) return;

    // Optimistic insert, rolled back if the API rejects it.
    const optimistic = { todoId: `tmp-${Date.now()}`, title: trimmed, done: false, pending: true };
    setTodos((current) => [optimistic, ...current]);
    setTitle('');

    try {
      const response = await fetch('/api/todos', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ title: trimmed, ownerId: OWNER }),
      });
      if (!response.ok) throw new Error(`API returned ${response.status}`);
      const saved = await response.json();
      setTodos((current) => current.map((todo) => (todo.todoId === optimistic.todoId ? saved : todo)));
    } catch (error) {
      setTodos((current) => current.filter((todo) => todo.todoId !== optimistic.todoId));
      setStatus({ state: 'error', message: String(error.message ?? error) });
    }
  }

  async function toggle(todo) {
    setTodos((current) =>
      current.map((item) => (item.todoId === todo.todoId ? { ...item, done: !item.done } : item)),
    );
    await fetch(`/api/todos/${todo.todoId}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ done: !todo.done }),
    });
  }

  async function remove(todo) {
    setTodos((current) => current.filter((item) => item.todoId !== todo.todoId));
    await fetch(`/api/todos/${todo.todoId}`, { method: 'DELETE' });
  }

  return (
    <main>
      <header>
        <h1>Todos</h1>
        <p className="sub">React on ECS Fargate · API behind the same load balancer</p>
      </header>

      <form onSubmit={addTodo}>
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="What needs doing?"
          maxLength={200}
          aria-label="New todo"
        />
        <button type="submit" disabled={!title.trim()}>
          Add
        </button>
      </form>

      {status.state === 'loading' && <p className="muted">Loading…</p>}
      {status.state === 'error' && (
        <p className="error">
          Could not reach the API: {status.message}{' '}
          <button type="button" onClick={load}>
            Retry
          </button>
        </p>
      )}

      <ul>
        {todos.map((todo) => (
          <li key={todo.todoId} className={todo.pending ? 'pending' : ''}>
            <label>
              <input type="checkbox" checked={Boolean(todo.done)} onChange={() => toggle(todo)} />
              <span className={todo.done ? 'done' : ''}>{todo.title}</span>
            </label>
            <button type="button" className="link" onClick={() => remove(todo)} aria-label="Delete">
              ×
            </button>
          </li>
        ))}
      </ul>

      {status.state === 'ready' && todos.length === 0 && <p className="muted">Nothing yet.</p>}
    </main>
  );
}
