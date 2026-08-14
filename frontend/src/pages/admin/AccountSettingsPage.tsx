import { useEffect, useState, type FormEvent } from "react";
import { api } from "../../api/client";
import { PageHeader } from "../../components/PageHeader";
import { FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";

function extractError(err: unknown, fallback: string) {
  const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || fallback;
}

export function AccountSettingsPage() {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(true);

  const [setupSecret, setSetupSecret] = useState<string | null>(null);
  const [setupQr, setSetupQr] = useState<string | null>(null);
  const [confirmCode, setConfirmCode] = useState("");
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const [showDisable, setShowDisable] = useState(false);
  const [disablePassword, setDisablePassword] = useState("");

  useEffect(() => {
    refreshStatus();
  }, []);

  function refreshStatus() {
    setLoading(true);
    api
      .get<{ enabled: boolean }>("/2fa/status/")
      .then((r) => setEnabled(r.data.enabled))
      .finally(() => setLoading(false));
  }

  async function startSetup() {
    setError("");
    setBackupCodes(null);
    try {
      const r = await api.post<{ secret: string; qr_code: string }>("/2fa/setup/");
      setSetupSecret(r.data.secret);
      setSetupQr(r.data.qr_code);
      setConfirmCode("");
    } catch (err) {
      setError(extractError(err, "Could not start setup."));
    }
  }

  async function handleConfirm(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      const r = await api.post<{ backup_codes: string[] }>("/2fa/confirm/", { code: confirmCode });
      setBackupCodes(r.data.backup_codes);
      setSetupSecret(null);
      setSetupQr(null);
      refreshStatus();
    } catch (err) {
      setError(extractError(err, "Invalid code — check the time on your phone and try again."));
    } finally {
      setSaving(false);
    }
  }

  async function handleDisable(e: FormEvent) {
    e.preventDefault();
    setError("");
    setSaving(true);
    try {
      await api.post("/2fa/disable/", { password: disablePassword });
      setShowDisable(false);
      setDisablePassword("");
      refreshStatus();
    } catch (err) {
      setError(extractError(err, "Could not disable two-factor authentication."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader title="Account Settings" subtitle="Manage your login security." />
      <div className="max-w-lg rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-6">
        <h2 className="mb-1 text-base font-semibold text-[var(--text-primary)]">Two-factor authentication</h2>
        <p className="mb-4 text-sm text-[var(--text-secondary)]">
          Adds a second step at login using a code from an authenticator app (Google Authenticator, Authy, etc.),
          on top of your password.
        </p>

        {loading ? (
          <p className="text-sm text-[var(--text-muted)]">Loading…</p>
        ) : backupCodes ? (
          <div>
            <p className="mb-2 text-sm font-medium text-[#0ca30c]">Two-factor authentication is now enabled.</p>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Save these one-time backup codes somewhere safe — each can be used once instead of a code from your
              app, if you ever lose access to it. They won't be shown again.
            </p>
            <div className="mb-4 grid grid-cols-2 gap-2 rounded-md bg-[var(--tint-subtle)] p-3 font-mono text-sm">
              {backupCodes.map((c) => (
                <div key={c}>{c}</div>
              ))}
            </div>
            <button className={btnPrimary} onClick={() => setBackupCodes(null)}>
              Done
            </button>
          </div>
        ) : enabled ? (
          !showDisable ? (
            <div>
              <p className="mb-4 text-sm text-[var(--text-secondary)]">
                Two-factor authentication is currently{" "}
                <span className="font-medium text-[var(--text-primary)]">enabled</span> on your account.
              </p>
              <button className={btnSecondary} onClick={() => setShowDisable(true)}>
                Disable two-factor authentication
              </button>
            </div>
          ) : (
            <form onSubmit={handleDisable}>
              <FormField label="Confirm your password to disable">
                <input
                  type="password"
                  className={inputClass}
                  required
                  value={disablePassword}
                  onChange={(e) => setDisablePassword(e.target.value)}
                />
              </FormField>
              {error && <p className="mb-3 text-sm text-[#b32e2e]">{error}</p>}
              <div className="flex gap-2">
                <button
                  type="button"
                  className={btnSecondary}
                  onClick={() => {
                    setShowDisable(false);
                    setDisablePassword("");
                    setError("");
                  }}
                >
                  Cancel
                </button>
                <button type="submit" disabled={saving} className={btnPrimary}>
                  {saving ? "Disabling…" : "Disable"}
                </button>
              </div>
            </form>
          )
        ) : setupQr ? (
          <form onSubmit={handleConfirm}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Scan this QR code with your authenticator app, then enter the 6-digit code it shows.
            </p>
            <img src={setupQr} alt="Two-factor setup QR code" className="mb-3 h-40 w-40" />
            <p className="mb-3 break-all text-xs text-[var(--text-muted)]">
              Can't scan it? Enter this key manually: <span className="font-mono">{setupSecret}</span>
            </p>
            <FormField label="6-digit code">
              <input
                className={`${inputClass} text-center text-lg tracking-widest`}
                value={confirmCode}
                onChange={(e) => setConfirmCode(e.target.value)}
                placeholder="123456"
                required
              />
            </FormField>
            {error && <p className="mb-3 text-sm text-[#b32e2e]">{error}</p>}
            <div className="flex gap-2">
              <button
                type="button"
                className={btnSecondary}
                onClick={() => {
                  setSetupQr(null);
                  setSetupSecret(null);
                  setError("");
                }}
              >
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Verifying…" : "Confirm & enable"}
              </button>
            </div>
          </form>
        ) : (
          <div>
            <p className="mb-4 text-sm text-[var(--text-secondary)]">
              Two-factor authentication is currently{" "}
              <span className="font-medium text-[var(--text-primary)]">not enabled</span>.
            </p>
            {error && <p className="mb-3 text-sm text-[#b32e2e]">{error}</p>}
            <button className={btnPrimary} onClick={startSetup}>
              Enable two-factor authentication
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
