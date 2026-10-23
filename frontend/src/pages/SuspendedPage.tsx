import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import axios from "axios";
import skybreIcon from "../assets/skybre-icon.png";

// Deliberately NOT the shared `api` client, for the same reason
// PublicUsagePage isn't: that one attaches the staff JWT from localStorage
// and redirects to /login on a 401. Whoever is reading this page has no
// account session -- they are a customer whose line has just been cut off,
// and bouncing them to a login form is the one thing that would make this
// page useless.
const publicApi = axios.create({ baseURL: "/api" });

type PaymentDetails = {
  company_name: string;
  phone: string;
  email: string;
  bank_name: string;
  bank_account_number: string;
  bank_branch_code: string;
};

// The reference arrives in the query string (the suspension email builds
// the link with it) and is printed straight onto the page. React escapes
// it, so there is no injection here -- but this page carries Skybre's logo
// and name, and a link with `?ref=Call+0800-000-000+to+reactivate` would
// have rendered attacker-chosen text under our branding. So it is filtered
// down to the shape a payment reference can actually have, and anything
// else is treated as absent rather than shown.
const REF_MAX = 32;
function cleanReference(raw: string | null) {
  if (!raw) return "";
  const trimmed = raw.trim().slice(0, REF_MAX);
  return /^[A-Za-z0-9 ._-]+$/.test(trimmed) ? trimmed : "";
}

function CopyButton({ value, label }: { value: string; label: string }) {
  const [done, setDone] = useState(false);
  return (
    <button
      type="button"
      aria-label={`Copy ${label}`}
      onClick={() => {
        // navigator.clipboard is unavailable on a page served over plain
        // HTTP and throws if the browser denies permission. A copy button
        // that silently does nothing is annoying; one that takes the page
        // down with an unhandled rejection is worse.
        try {
          navigator.clipboard?.writeText(value).then(
            () => {
              setDone(true);
              setTimeout(() => setDone(false), 1500);
            },
            () => undefined,
          );
        } catch {
          /* no clipboard on this browser -- the value is on screen anyway */
        }
      }}
      className="ml-2 rounded border border-[var(--border-hairline)] px-1.5 py-0.5 text-[11px] font-normal text-[var(--text-muted)] hover:bg-[var(--tint-hover)]"
    >
      {done ? "Copied" : "Copy"}
    </button>
  );
}

export function SuspendedPage() {
  const [params] = useSearchParams();
  const reference = cleanReference(params.get("ref"));
  const [details, setDetails] = useState<PaymentDetails | null>(null);
  const [detailsFailed, setDetailsFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    publicApi
      .get<PaymentDetails>("/public/payment-details/")
      .then((res) => {
        if (!cancelled) setDetails(res.data);
      })
      .catch(() => {
        if (!cancelled) setDetailsFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const companyName = details?.company_name || "Skybre";
  const hasBanking = Boolean(details?.bank_account_number);
  // Whether this page can offer a way to reach a human at all. Every
  // fallback below is worded off this, because the one thing a page for a
  // cut-off customer must never do is send them somewhere that isn't
  // there -- "contact accounts using the details below" with nothing
  // below it is a dead end at the worst possible moment.
  const hasContact = Boolean(details?.email || details?.phone);

  return (
    <div className="min-h-screen bg-[var(--surface-0)] px-4 py-8">
      <div className="mx-auto max-w-xl">
        <header className="mb-6 flex items-center gap-3">
          <img src={skybreIcon} alt="Skybre" className="h-10 w-10 shrink-0 object-contain" />
          <div>
            <p className="text-sm font-semibold text-[var(--text-primary)]">{companyName}</p>
            <h1 className="text-lg font-semibold text-[var(--text-primary)]">
              Your internet service is suspended
            </h1>
          </div>
        </header>

        {/* Said first, and said plainly. Someone lands here because every
            other page they tried failed, so the first job is to tell them
            this is not a fault and their equipment is fine. */}
        <section className="mb-4 rounded-lg border border-[#e0a800] bg-[var(--surface-1)] p-5">
          <p className="text-sm text-[var(--text-primary)]">
            There is nothing wrong with your line or your equipment. Your account has an
            unpaid balance, so the connection has been limited until it is settled.
          </p>
        </section>

        {reference ? (
          <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
              Use this reference on your payment
            </p>
            {/* Same red as the invoice PDF's reference line, and for the
                same reason: a payment that arrives without this reference
                cannot be matched to an account automatically, and sits in
                the unallocated queue while the customer stays cut off. */}
            <p className="mt-1 flex items-center text-2xl font-bold text-[#ff0000]">
              {reference}
              <CopyButton value={reference} label="reference" />
            </p>
          </section>
        ) : (
          <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">
              Use your account reference on the payment
            </p>
            <p className="mt-1 text-sm text-[var(--text-secondary)]">
              It is printed on every invoice we send you, next to your name. Without it we
              cannot match your payment to your account, and the line stays off.
            </p>
          </section>
        )}

        <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
          <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Where to pay</h2>
          {hasBanking && details ? (
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Account holder</dt>
                <dd className="text-right font-medium text-[var(--text-primary)]">{details.company_name}</dd>
              </div>
              {details.bank_name && (
                <div className="flex justify-between gap-4">
                  <dt className="text-[var(--text-muted)]">Bank</dt>
                  <dd className="text-right font-medium text-[var(--text-primary)]">{details.bank_name}</dd>
                </div>
              )}
              <div className="flex justify-between gap-4">
                <dt className="text-[var(--text-muted)]">Account number</dt>
                <dd className="flex items-center text-right font-medium tabular-nums text-[var(--text-primary)]">
                  {details.bank_account_number}
                  <CopyButton value={details.bank_account_number} label="account number" />
                </dd>
              </div>
              {details.bank_branch_code && (
                <div className="flex justify-between gap-4">
                  <dt className="text-[var(--text-muted)]">Branch code</dt>
                  <dd className="text-right font-medium tabular-nums text-[var(--text-primary)]">
                    {details.bank_branch_code}
                  </dd>
                </div>
              )}
            </dl>
          ) : (
            // No banking details captured yet, or the request for them
            // failed. Either way this page must not invent an account
            // number -- and it points at the invoice rather than at itself,
            // because the invoice PDF carries the same bank, account,
            // branch, phone and email, and the customer already has it.
            <p className="text-sm text-[var(--text-secondary)]">
              {detailsFailed
                ? "We couldn't load our payment details just now. They're printed on your latest invoice, next to your reference."
                : "Our payment details are printed on your latest invoice, next to your reference."}
              {hasContact ? " You can also reach accounts using the details below." : ""}
            </p>
          )}
        </section>

        {/* The honest part. A payment landing in the bank does NOT put the
            line back on by itself -- reactivation is a deliberate action
            somebody in accounts takes. Telling a customer "it will restore
            automatically" would leave them waiting on something that is
            never going to happen. */}
        <section className="mb-4 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
          <h2 className="mb-2 text-sm font-semibold text-[var(--text-primary)]">
            After you have paid
          </h2>
          <p className="text-sm text-[var(--text-secondary)]">
            Send your proof of payment to accounts — that is the fastest way back online.
            Payments are reconciled during office hours, and your line is switched back on
            once yours has been confirmed.
          </p>
          {!hasContact && (
            <p className="mt-3 text-sm text-[var(--text-secondary)]">
              Our email address and phone number are printed on your invoice.
            </p>
          )}
          {details && hasContact && (
            <div className="mt-3 space-y-1 text-sm">
              {details.email && (
                <p className="text-[var(--text-primary)]">
                  <span className="text-[var(--text-muted)]">Email: </span>
                  <a className="underline" href={`mailto:${details.email}`}>
                    {details.email}
                  </a>
                </p>
              )}
              {details.phone && (
                <p className="text-[var(--text-primary)]">
                  <span className="text-[var(--text-muted)]">Phone: </span>
                  <a className="underline" href={`tel:${details.phone.replace(/[^\d+]/g, "")}`}>
                    {details.phone}
                  </a>
                </p>
              )}
            </div>
          )}
        </section>

        <p className="text-center text-xs text-[var(--text-muted)]">
          Already paid and still seeing this page? Send the proof of payment through, or call
          us, and we will get your line back on.
        </p>
      </div>
    </div>
  );
}
