import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";
import skybreIcon from "../assets/skybre-icon.png";

// Landed on from the link emailed by ForgotPasswordPage's request (or an
// admin's "Send reset link" action) -- /reset-password/:uid/:token.
// uid/token are consumed exactly once against
// accounts.views.PasswordResetConfirmView; a stale, reused, or tampered
// link comes back as the same generic "invalid or expired" message.
export function ResetPasswordPage() {
  const { uid, token } = useParams();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setSubmitting(true);
    try {
      await api.post("/password-reset/confirm/", { uid, token, new_password: password });
      setDone(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "This reset link is invalid or has expired.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-[var(--page-plane)]">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-sm rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center">
          <img src={skybreIcon} alt="Skybre" className="mb-3 h-14 w-14 object-contain" />
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Skybre</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Set a new password</p>
        </div>

        {done ? (
          <>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">
              Your password has been reset. You can now sign in with it.
            </p>
            <button
              type="button"
              onClick={() => navigate("/login")}
              className="w-full rounded-md bg-[var(--series-1)] py-2 text-sm font-medium text-white hover:opacity-90"
            >
              Go to sign in
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <label className="mb-3 block text-sm">
              <span className="mb-1 block font-medium text-[var(--text-secondary)]">New password</span>
              <input
                type="password"
                className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoFocus
                required
                minLength={6}
              />
            </label>
            <label className="mb-4 block text-sm">
              <span className="mb-1 block font-medium text-[var(--text-secondary)]">Confirm new password</span>
              <input
                type="password"
                className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                required
                minLength={6}
              />
            </label>
            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-[var(--series-1)] py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Saving…" : "Reset password"}
            </button>
            <Link
              to="/login"
              className="mt-4 block text-center text-sm font-medium text-[var(--series-1)] hover:underline"
            >
              ← Back to sign in
            </Link>
          </form>
        )}
      </div>
    </div>
  );
}
