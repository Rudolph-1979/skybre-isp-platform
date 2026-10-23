import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useAuth } from "../context/AuthContext";
import { apiErrorMessage } from "../utils/apiError";
import type { BankAccount } from "../types";

/**
 * FNB Integration Channel redirect URL (#185): /bank-feeds/fnb-callback.
 *
 * FNB sends the browser here with ?code= (and our ?state= when the
 * connection was started with "Connect to FNB"). The code is handed to the
 * backend, which swaps it for FNB tokens -- it is only ever exchanged by a
 * signed-in Administrator. Outside the admin layout on purpose: if the
 * session has expired, the code is kept in this tab (sessionStorage, 10
 * minutes) while the person signs in, then the page carries on.
 */

const STORE = "fnb_callback";
const MAX_AGE_MS = 10 * 60 * 1000;

type Pending = { code: string; state: string; at: number };

function takePending(): Pending | null {
  const params = new URLSearchParams(window.location.search);
  const code = params.get("code");
  if (code) {
    const pending = { code, state: params.get("state") ?? "", at: Date.now() };
    try {
      sessionStorage.setItem(STORE, JSON.stringify(pending));
    } catch {
      /* private mode: carry on without the fallback */
    }
    // Don't leave the code in the address bar or the history.
    window.history.replaceState(null, "", window.location.pathname);
    return pending;
  }
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE) || "null") as Pending | null;
    if (saved && Date.now() - saved.at < MAX_AGE_MS) return saved;
  } catch {
    /* ignore */
  }
  return null;
}

function clearPending() {
  try {
    sessionStorage.removeItem(STORE);
  } catch {
    /* ignore */
  }
}

export function FnbCallbackPage() {
  const { user, loading } = useAuth();
  const [pending] = useState<Pending | null>(() => takePending());
  const [fnbError] = useState(() => {
    const p = new URLSearchParams(window.location.search);
    return p.get("error") ? `${p.get("error")}${p.get("error_description") ? ` — ${p.get("error_description")}` : ""}` : "";
  });
  const [accounts, setAccounts] = useState<BankAccount[]>([]);
  const [accountId, setAccountId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<BankAccount | null>(null);
  const started = useRef(false);
  const redirectUri = `${window.location.origin}/bank-feeds/fnb-callback`;

  async function finish(extra: Record<string, unknown>) {
    if (!pending) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.post<BankAccount>("/bank-accounts/fnb-callback/", {
        code: pending.code,
        state: pending.state || undefined,
        redirect_uri: redirectUri,
        ...extra,
      });
      clearPending();
      setDone(res.data);
    } catch (err) {
      setError(apiErrorMessage(err, "FNB didn't accept the connection."));
    } finally {
      setBusy(false);
    }
  }

  const isAdmin = user?.role === "admin";

  useEffect(() => {
    if (!user || !isAdmin || !pending || started.current) return;
    started.current = true;
    if (pending.state) {
      finish({});
    } else {
      api
        .get<{ results: BankAccount[] }>("/bank-accounts/?page_size=50&ordering=name")
        .then((r) => {
          const withApi = (r.data.results ?? []).filter((a) => a.connection_status !== "csv_only");
          setAccounts(withApi);
          if (withApi.length === 1) setAccountId(String(withApi[0].id));
        })
        .catch(() => setError("Couldn't load the bank accounts."));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, isAdmin, pending]);

  let body;
  if (loading) {
    body = <p className="text-[var(--text-muted)]">Loading…</p>;
  } else if (fnbError) {
    body = <p className="text-[var(--status-critical)]">FNB returned an error: {fnbError}</p>;
  } else if (!pending) {
    body = <p>There is no FNB connection waiting here. Start again from Accountant → Bank Feeds → Accounts.</p>;
  } else if (!user) {
    body = (
      <p>
        Sign in as an Administrator to finish connecting the account.{" "}
        <Link className="text-[var(--series-1)] hover:underline" to="/login?next=/bank-feeds/fnb-callback">
          Sign in
        </Link>
      </p>
    );
  } else if (!isAdmin) {
    body = <p>Only an Administrator can connect a bank account to FNB. Ask an Administrator to open this link.</p>;
  } else if (done) {
    body = (
      <p>
        <strong className="text-[var(--status-good)]">{done.name} is connected to FNB.</strong> Use "Sync now" on the
        account to fetch the first transactions.
      </p>
    );
  } else if (pending.state) {
    body = <p>{busy ? "Connecting to FNB…" : error ? "" : "Connecting…"}</p>;
  } else {
    body = (
      <div className="space-y-3">
        <p>FNB sent an auth code. Which bank account is it for?</p>
        <select className="w-full rounded-md border border-[var(--border-hairline)] bg-[var(--surface-1)] px-3 py-2 text-sm" value={accountId} onChange={(e) => setAccountId(e.target.value)}>
          <option value="">Choose an account…</option>
          {accounts.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name} {a.account_number ? `(${a.account_number})` : ""}
            </option>
          ))}
        </select>
        {accounts.length === 0 && (
          <p className="text-xs text-[var(--text-muted)]">No account has FNB API details yet — fill them in on the account first.</p>
        )}
        <button
          type="button"
          disabled={busy || !accountId}
          className="rounded-md bg-[var(--series-1)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          onClick={() => finish({ account: Number(accountId) })}
        >
          {busy ? "Connecting…" : "Connect this account"}
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--surface-0,var(--surface-1))] p-4">
      <div className="w-full max-w-md rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-6 text-sm text-[var(--text-primary)]">
        <h1 className="mb-3 text-lg font-semibold">Connect FNB bank feed</h1>
        {body}
        {error && <p className="mt-3 text-[var(--status-critical)]">{error}</p>}
        {user && (
          <p className="mt-4">
            <Link className="text-[var(--series-1)] hover:underline" to="/admin/accountant?tab=bank-feeds&sub=accounts">
              Back to Bank Feeds
            </Link>
          </p>
        )}
      </div>
    </div>
  );
}
