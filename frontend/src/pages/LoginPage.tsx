import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const user = await login(username, password);
      const isStaff = ["admin", "staff", "technician"].includes(user.role);
      navigate(isStaff ? "/admin" : "/portal");
    } catch {
      setError("Invalid username or password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--page-plane)]">
      <div className="w-full max-w-sm rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center">
          <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-md bg-[var(--series-1)] text-base font-bold text-white">
            IP
          </div>
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">ISP Management Platform</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Sign in to your account</p>
        </div>
        <form onSubmit={handleSubmit}>
          <label className="mb-3 block text-sm">
            <span className="mb-1 block font-medium text-[var(--text-secondary)]">Username</span>
            <input
              className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoFocus
            />
          </label>
          <label className="mb-4 block text-sm">
            <span className="mb-1 block font-medium text-[var(--text-secondary)]">Password</span>
            <input
              type="password"
              className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-[var(--series-1)] py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <div className="mt-6 rounded-md bg-black/[0.03] p-3 text-xs text-[var(--text-muted)]">
          <p className="mb-1 font-medium text-[var(--text-secondary)]">Demo logins</p>
          <p>Staff: admin / admin12345</p>
          <p>Customer: cust000 / customer12345</p>
        </div>
      </div>
    </div>
  );
}
