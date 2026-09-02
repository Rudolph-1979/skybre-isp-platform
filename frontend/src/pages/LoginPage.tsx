import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ThemeToggle } from "../components/ThemeToggle";
import skybreIcon from "../assets/skybre-icon.png";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [needsCode, setNeedsCode] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const user = await login(username, password, needsCode ? totpCode : undefined);
      const isStaff = user.role !== "customer";
      navigate(isStaff ? "/admin" : "/portal");
    } catch (err) {
      const data = (err as { response?: { data?: { code?: string } } })?.response?.data;
      if (data?.code === "two_factor_required") {
        setNeedsCode(true);
        setError("");
      } else if (data?.code === "invalid_two_factor_code") {
        setError("Invalid code — try again, or use one of your backup codes.");
      } else {
        setError("Invalid username or password.");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-[var(--page-plane)]">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-sm rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center">
          <img src={skybreIcon} alt="Skybre" className="mb-3 h-14 w-14 object-contain" />
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Skybre</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">
            {needsCode ? "Enter your two-factor authentication code" : "Sign in to your account"}
          </p>
        </div>
        <form onSubmit={handleSubmit}>
          {!needsCode ? (
            <>
              <label className="mb-3 block text-sm">
                <span className="mb-1 block font-medium text-[var(--text-secondary)]">Username</span>
                <input
                  className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoFocus
                />
              </label>
              <label className="mb-2 block text-sm">
                <span className="mb-1 block font-medium text-[var(--text-secondary)]">Password</span>
                <input
                  type="password"
                  className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <Link
                to="/forgot-password"
                className="mb-4 block text-right text-xs font-medium text-[var(--series-1)] hover:underline"
              >
                Forgot password?
              </Link>
            </>
          ) : (
            <>
              <label className="mb-4 block text-sm">
                <span className="mb-1 block font-medium text-[var(--text-secondary)]">Authentication code</span>
                <input
                  className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-center text-lg tracking-widest focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                  value={totpCode}
                  onChange={(e) => setTotpCode(e.target.value)}
                  placeholder="123456"
                  autoFocus
                  maxLength={9}
                />
              </label>
              <button
                type="button"
                className="mb-4 text-xs font-medium text-[var(--series-1)] hover:underline"
                onClick={() => {
                  setNeedsCode(false);
                  setTotpCode("");
                  setError("");
                }}
              >
                ← Back
              </button>
            </>
          )}
          {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
          <button
            type="submit"
            disabled={loading}
            className="w-full rounded-md bg-[var(--series-1)] py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {loading ? "Signing in…" : needsCode ? "Verify" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
