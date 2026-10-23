import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import { Table, THead, TH, TR, TD } from "./Table";
import { StatusBadge } from "./StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "./Modal";
import { PdfPreviewModal } from "./PdfPreviewModal";
import { formatIsoDateTime } from "../utils/billingTerms";
import { apiErrorMessage } from "../utils/apiError";
import type { Contract, ContractTemplate } from "../types";

/**
 * The client's contracts (Rudolph, 6 Oct 2026: "Digital Contract Signing --
 * saved in every customer profile"). From a template filled in from this
 * profile, or an uploaded PDF. The client signs from an emailed link, or
 * here and now on a staff phone/tablet ("Sign on this device"). The signed
 * PDF carries a certificate page: who, when, from where, and the document's
 * fingerprint.
 */

export function contractBadge(c: Pick<Contract, "status" | "is_expired" | "status_display">) {
  const style = c.is_expired
    ? "overdue"
    : ({ draft: "draft", sent: "sent", viewed: "pending", signed: "approved", cancelled: "cancelled" } as const)[
        c.status
      ];
  return <StatusBadge status={style} label={c.status_display} />;
}

export function CustomerContractsTab({
  customerId,
  customerEmail,
}: {
  customerId: number;
  customerEmail: string;
}) {
  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [openId, setOpenId] = useState<number | null>(null);
  const [preview, setPreview] = useState<{ title: string; url: string; filename: string } | null>(null);

  function load() {
    setLoading(true);
    api
      .get<Contract[]>(`/contracts/?customer=${customerId}`)
      .then((res) => setContracts(res.data))
      .catch(() => setError("Couldn't load the contracts."))
      .finally(() => setLoading(false));
  }

  useEffect(load, [customerId]);

  const signed = contracts.find((c) => c.status === "signed");
  const waiting = contracts.find((c) => c.status !== "signed" && c.status !== "cancelled");

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold text-[var(--text-primary)]">Contracts</h2>
          <p className="mt-1 text-xs text-[var(--text-muted)]">
            {signed
              ? `Signed ${signed.signed_at ? formatIsoDateTime(signed.signed_at) : ""} by ${signed.signer_name} (${signed.reference}).`
              : waiting
                ? `${waiting.reference} is waiting for the client's signature.`
                : "No contract yet."}
          </p>
        </div>
        <button className={btnPrimary} onClick={() => setShowNew(true)}>
          + New contract
        </button>
      </div>

      {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}
      {notice && (
        <p className="mb-3 rounded-md bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-secondary)]">{notice}</p>
      )}

      <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)]">
        <Table>
          <THead>
            <TR>
              <TH>Reference</TH>
              <TH>Contract</TH>
              <TH>Status</TH>
              <TH>Sent</TH>
              <TH>Signed</TH>
              <TH />
            </TR>
          </THead>
          <tbody>
            {contracts.map((c) => (
              <TR key={c.id}>
                <TD className="whitespace-nowrap tabular-nums">{c.reference}</TD>
                <TD>
                  <div>{c.title}</div>
                  <div className="text-xs text-[var(--text-muted)]">
                    {c.kind === "upload" ? "Uploaded PDF" : c.template_name || "Template"}
                    {c.created_by_name ? ` · by ${c.created_by_name}` : ""}
                  </div>
                </TD>
                <TD>{contractBadge(c)}</TD>
                <TD className="text-xs">
                  {c.sent_at ? (
                    <>
                      {formatIsoDateTime(c.sent_at)}
                      <div className="text-[var(--text-muted)]">{c.sent_to}</div>
                    </>
                  ) : (
                    "—"
                  )}
                </TD>
                <TD className="text-xs">
                  {c.signed_at ? (
                    <>
                      {formatIsoDateTime(c.signed_at)}
                      <div className="text-[var(--text-muted)]">
                        {c.signer_name}
                        {c.signed_in_person ? " · in person" : " · online"}
                      </div>
                    </>
                  ) : (
                    "—"
                  )}
                </TD>
                <TD className="whitespace-nowrap text-right">
                  {c.has_signed_pdf && (
                    <button
                      className="mr-3 text-xs font-medium text-[var(--series-1)] hover:underline"
                      onClick={() =>
                        setPreview({
                          title: `${c.reference} (signed)`,
                          url: `/contracts/${c.id}/signed/`,
                          filename: `${c.reference} ${c.title} (signed).pdf`,
                        })
                      }
                    >
                      Signed PDF
                    </button>
                  )}
                  <button
                    className="text-xs font-medium text-[var(--series-1)] hover:underline"
                    onClick={() => setOpenId(c.id)}
                  >
                    Open
                  </button>
                </TD>
              </TR>
            ))}
            {!loading && contracts.length === 0 && (
              <TR>
                <td colSpan={6} className="px-4 py-6 text-center text-sm text-[var(--text-muted)]">
                  No contracts for this client yet.
                </td>
              </TR>
            )}
          </tbody>
        </Table>
      </div>

      {showNew && (
        <NewContractModal
          customerId={customerId}
          customerEmail={customerEmail}
          onClose={() => setShowNew(false)}
          onCreated={(c) => {
            setShowNew(false);
            setNotice(c.warning || (c.status === "sent" ? `${c.reference} emailed to ${c.sent_to}.` : `${c.reference} saved.`));
            load();
            setOpenId(c.id);
          }}
        />
      )}

      {openId !== null && (
        <ContractModal
          id={openId}
          customerEmail={customerEmail}
          onClose={() => setOpenId(null)}
          onChanged={load}
          onPreview={setPreview}
        />
      )}

      {preview && (
        <PdfPreviewModal
          title={preview.title}
          url={preview.url}
          filename={preview.filename}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  );
}

function NewContractModal({
  customerId,
  customerEmail,
  onClose,
  onCreated,
}: {
  customerId: number;
  customerEmail: string;
  onClose: () => void;
  onCreated: (c: Contract) => void;
}) {
  const [templates, setTemplates] = useState<ContractTemplate[]>([]);
  const [kind, setKind] = useState<"template" | "upload">("template");
  const [template, setTemplate] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [send, setSend] = useState(Boolean(customerEmail));
  const [email, setEmail] = useState(customerEmail);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    Promise.all([
      api.get<ContractTemplate[]>("/contract-templates/"),
      api.get<{ default_template: number | null }>("/contract-settings/"),
    ])
      .then(([t, s]) => {
        const active = t.data.filter((x) => x.is_active);
        setTemplates(active);
        const preferred = active.find((x) => x.id === s.data.default_template) ?? active[0];
        if (preferred) setTemplate(String(preferred.id));
        if (active.length === 0) setKind("upload");
      })
      .catch(() => setError("Couldn't load the contract templates."));
  }, []);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      const body = new FormData();
      body.append("customer", String(customerId));
      body.append("kind", kind);
      if (kind === "template") body.append("template", template);
      else {
        if (!file) throw new Error("Choose a PDF file.");
        body.append("file", file);
        body.append("title", title);
      }
      if (send) {
        body.append("send", "true");
        body.append("email", email.trim());
      }
      const res = await api.post<Contract>("/contracts/", body);
      onCreated(res.data);
    } catch (err) {
      setError(err instanceof Error && !("response" in err) ? err.message : apiErrorMessage(err, "Couldn't make the contract."));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title="New contract" onClose={onClose}>
      <form onSubmit={submit} className="space-y-4">
        <div className="flex gap-4 text-sm">
          <label className="flex items-center gap-2">
            <input type="radio" checked={kind === "template"} onChange={() => setKind("template")} />
            From a template
          </label>
          <label className="flex items-center gap-2">
            <input type="radio" checked={kind === "upload"} onChange={() => setKind("upload")} />
            Upload a PDF
          </label>
        </div>

        {kind === "template" ? (
          <FormField label="Template" required>
            {templates.length === 0 ? (
              <p className="text-sm text-[var(--text-muted)]">
                No template is switched on yet. Set one up under Configs → Contracts, or upload a PDF.
              </p>
            ) : (
              <select className={inputClass} value={template} onChange={(e) => setTemplate(e.target.value)} required>
                {templates.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            )}
            <p className="mt-1 text-xs text-[var(--text-muted)]">
              Filled in from this client's profile and plans as they are now.
            </p>
          </FormField>
        ) : (
          <>
            <FormField label="PDF" required>
              <input
                type="file"
                accept="application/pdf,.pdf"
                className="text-sm"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <p className="mt-1 text-xs text-[var(--text-muted)]">Up to 10 MB, no password.</p>
            </FormField>
            <FormField label="Title">
              <input
                className={inputClass}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Fibre service agreement (blank = the file name)"
              />
            </FormField>
          </>
        )}

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={send} onChange={(e) => setSend(e.target.checked)} />
          Email the signing link now
        </label>
        {send && (
          <FormField label="Send to" required>
            <input type="email" className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} required />
          </FormField>
        )}
        {!send && (
          <p className="text-xs text-[var(--text-muted)]">
            Saved as "Not sent". Open it to email it later, copy the link, or sign on this device.
          </p>
        )}

        {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}
        <div className="flex justify-end gap-2">
          <button type="button" className={btnSecondary} onClick={onClose}>
            Cancel
          </button>
          <button
            type="submit"
            className={btnPrimary}
            disabled={saving || (kind === "template" && !template) || (kind === "upload" && !file)}
          >
            {saving ? "Working…" : send ? "Make and send" : "Make contract"}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function ContractModal({
  id,
  customerEmail,
  onClose,
  onChanged,
  onPreview,
}: {
  id: number;
  customerEmail: string;
  onClose: () => void;
  onChanged: () => void;
  onPreview: (p: { title: string; url: string; filename: string }) => void;
}) {
  const [c, setC] = useState<Contract | null>(null);
  const [email, setEmail] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  function load() {
    api
      .get<Contract>(`/contracts/${id}/`)
      .then((res) => {
        setC(res.data);
        setEmail(res.data.sent_to || customerEmail);
      })
      .catch(() => setError("Couldn't load this contract."));
  }

  useEffect(load, [id]);

  async function run(label: string, fn: () => Promise<string | void>) {
    setBusy(label);
    setError("");
    setMessage("");
    try {
      const msg = await fn();
      if (msg) setMessage(msg);
      load();
      onChanged();
    } catch (err) {
      setError(apiErrorMessage(err, "That didn't work."));
    } finally {
      setBusy("");
    }
  }

  const open = c && c.status !== "signed" && c.status !== "cancelled";

  return (
    <Modal title={c ? `${c.reference} — ${c.title}` : "Contract"} onClose={onClose}>
      {!c ? (
        <p className="text-sm text-[var(--text-muted)]">{error || "Loading…"}</p>
      ) : (
        <div className="space-y-4 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            {contractBadge(c)}
            {c.expires_at && open && (
              <span className="text-xs text-[var(--text-muted)]">
                Link {c.is_expired ? "expired" : "valid until"} {formatIsoDateTime(c.expires_at)}
              </span>
            )}
          </div>

          {c.status === "signed" && (
            <div className="rounded-md border border-[var(--border-hairline)] bg-[var(--surface-2)] p-3 text-xs">
              <div>
                Signed by <strong>{c.signer_name}</strong>
                {c.signer_id_number ? ` (ID ${c.signer_id_number})` : ""} on{" "}
                {c.signed_at ? formatIsoDateTime(c.signed_at) : ""}
              </div>
              <div className="text-[var(--text-muted)]">
                {c.signed_in_person ? `In person, with ${c.witnessed_by_name}` : `Online from ${c.signer_ip ?? "?"}`}
              </div>
              <div className="mt-1 break-all text-[var(--text-muted)]">Signed PDF SHA-256 {c.signed_sha256}</div>
            </div>
          )}

          <div className="flex flex-wrap gap-2">
            <button
              className={btnSecondary}
              onClick={() =>
                onPreview({ title: c.reference, url: `/contracts/${c.id}/document/`, filename: c.document_filename })
              }
            >
              View document
            </button>
            {c.has_signed_pdf && (
              <button
                className={btnSecondary}
                onClick={() =>
                  onPreview({
                    title: `${c.reference} (signed)`,
                    url: `/contracts/${c.id}/signed/`,
                    filename: `${c.reference} ${c.title} (signed).pdf`,
                  })
                }
              >
                Signed PDF
              </button>
            )}
          </div>

          {open && (
            <div className="space-y-3 border-t border-[var(--border-hairline)] pt-3">
              <FormField label={c.sent_at ? "Send again to" : "Email the signing link to"}>
                <div className="flex gap-2">
                  <input type="email" className={inputClass} value={email} onChange={(e) => setEmail(e.target.value)} />
                  <button
                    className={btnPrimary}
                    disabled={!!busy || !email.trim()}
                    onClick={() =>
                      run("send", async () => {
                        const res = await api.post<{ sent_to: string }>(`/contracts/${c.id}/send/`, { email: email.trim() });
                        return `Emailed to ${res.data.sent_to}.`;
                      })
                    }
                  >
                    {busy === "send" ? "Sending…" : c.sent_at ? "Resend" : "Send"}
                  </button>
                </div>
              </FormField>
              <div className="flex flex-wrap gap-2">
                <button
                  className={btnSecondary}
                  disabled={!!busy}
                  title="Open the signing page in a new tab, for the client to sign here on this phone or tablet."
                  onClick={() =>
                    run("in-person", async () => {
                      const tab = window.open("about:blank", "_blank");
                      const res = await api.post<{ url: string }>(`/contracts/${c.id}/in-person/`, {});
                      if (tab) tab.location.href = res.data.url;
                      else window.location.href = res.data.url;
                    })
                  }
                >
                  Sign on this device
                </button>
                <button
                  className={btnSecondary}
                  disabled={!!busy}
                  title="For WhatsApp or SMS. Anyone with the link can sign as the client, so send it only to them."
                  onClick={() =>
                    run("link", async () => {
                      const res = await api.get<{ url: string }>(`/contracts/${c.id}/link/`);
                      await navigator.clipboard.writeText(res.data.url).catch(() => undefined);
                      return `Link copied: ${res.data.url}`;
                    })
                  }
                >
                  Copy link
                </button>
                <button
                  className={btnSecondary}
                  disabled={!!busy}
                  onClick={() => {
                    if (!window.confirm(`Cancel ${c.reference}? Its link stops working.`)) return;
                    run("cancel", async () => {
                      await api.post(`/contracts/${c.id}/cancel/`, {});
                      return "Cancelled.";
                    });
                  }}
                >
                  Cancel contract
                </button>
              </div>
            </div>
          )}

          {message && <p className="break-all text-sm text-[var(--text-secondary)]">{message}</p>}
          {error && <p className="text-sm text-[var(--status-critical)]">{error}</p>}

          <div className="border-t border-[var(--border-hairline)] pt-3">
            <p className="mb-2 text-xs font-medium uppercase tracking-wide text-[var(--text-muted)]">Audit trail</p>
            <ul className="max-h-56 space-y-1 overflow-y-auto text-xs">
              {(c.events ?? []).map((e) => (
                <li key={e.id} className="flex gap-2">
                  <span className="shrink-0 tabular-nums text-[var(--text-muted)]">{formatIsoDateTime(e.at)}</span>
                  <span>
                    <strong>{e.kind_display}</strong>
                    {e.user_name ? ` · ${e.user_name}` : ""}
                    {e.detail ? ` · ${e.detail}` : ""}
                    {e.ip ? ` · IP ${e.ip}` : ""}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
}
