import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { ThemeToggle } from "../components/ThemeToggle";
import skybreIcon from "../assets/skybre-icon.png";

// Shared by both staff and customer-portal accounts, since they sign in
// through the same LoginPage. Always shows the same generic confirmation
// regardless of whether the identifier matched anything -- the backend
// (accounts.views.PasswordResetRequestView) mirrors that same behaviour
// so this page can't be used to probe which usernames/emails exist.
export function ForgotPasswordPage() {
  const [identifier, setIdentifier] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.post("/password-reset/", { identifier });
    } finally {
      setSubmitting(false);
      setDone(true);
    }
  }

  return (
    <div className="relative flex min-h-screen items-center justify-center bg-[var(--page-plane)]">
      <ThemeToggle className="absolute right-4 top-4" />
      <div className="w-full max-w-sm rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-8 shadow-sm">
        <div className="mb-6 flex flex-col items-center">
          <img src={skybreIcon} alt="Skybre" className="mb-3 h-14 w-14 object-contain" />
          <h1 className="text-lg font-semibold text-[var(--text-primary)]">Skybre</h1>
          <p className="mt-1 text-sm text-[var(--text-secondary)]">Reset your password</p>
        </div>

        {done ? (
          <>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">
              If an account matches, we've emailed a password reset link to the address on file. It's valid for a
              few days and can only be used once.
            </p>
            <Link to="/login" className="text-sm font-medium text-[var(--series-1)] hover:underline">
              ← Back to sign in
            </Link>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <label className="mb-4 block text-sm">
              <span className="mb-1 block font-medium text-[var(--text-secondary)]">Username or email</span>
              <input
                className="w-full rounded-md border border-[var(--baseline)] px-3 py-2 text-sm focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
                value={identifier}
                onChange={(e) => setIdentifier(e.target.value)}
                autoFocus
                required
              />
            </label>
            <button
              type="submit"
              disabled={submitting}
              className="w-full rounded-md bg-[var(--series-1)] py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {submitting ? "Sending…" : "Send reset link"}
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
