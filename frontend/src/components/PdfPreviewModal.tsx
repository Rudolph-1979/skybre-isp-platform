import { useEffect, useState } from "react";
import { api } from "../api/client";

// Preview any of the platform's PDFs (invoice, quote, pro forma, statement)
// in a modal, with Download and Open-in-new-tab alongside.
//
// Why it fetches a blob instead of just pointing an <iframe> at the URL: the
// API is authenticated with a JWT held in localStorage, and a plain iframe
// navigation carries no Authorization header, so the endpoint would answer
// 401 and the frame would show an error page. The same reason the VAT report
// download on the Accountant page goes through axios.
//
// Deliberately its own overlay rather than the shared Modal: that one is
// max-w-lg, which is right for a form and far too narrow for a page of A4.
// Widening the shared component would have changed every modal in the app.

export function PdfPreviewModal({
  title,
  url,
  filename,
  onClose,
}: {
  title: string;
  /** API path, e.g. `/invoices/12/pdf/`. */
  url: string;
  /** Name used when the viewer saves it. */
  filename: string;
  onClose: () => void;
}) {
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let revoked = false;
    let created: string | null = null;

    api
      .get(url, { responseType: "blob" })
      .then((res) => {
        // Force the type: some browsers refuse to render a blob whose type
        // came back empty or as octet-stream, and show a download prompt
        // inside the frame instead of the document.
        created = window.URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
        if (revoked) {
          window.URL.revokeObjectURL(created);
          return;
        }
        setBlobUrl(created);
      })
      .catch(() => setError("Could not load this document. It may not have generated — please try again."));

    // Closing mid-fetch has to revoke whatever the fetch later creates,
    // otherwise the blob leaks for the life of the tab.
    return () => {
      revoked = true;
      if (created) window.URL.revokeObjectURL(created);
    };
  }, [url]);

  function handleDownload() {
    if (!blobUrl) return;
    // Saves the blob already in memory rather than re-requesting, so the
    // file you keep is exactly the one you just looked at.
    const link = document.createElement("a");
    link.href = blobUrl;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-scrim)] p-4"
      onClick={onClose}
    >
      <div
        className="flex h-[92vh] w-full max-w-4xl flex-col rounded-lg bg-[var(--surface-1)] p-4 shadow-lg sm:p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h2>
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="rounded-md border border-[var(--baseline)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--tint-hover)] disabled:opacity-50"
              disabled={!blobUrl}
              onClick={() => blobUrl && window.open(blobUrl, "_blank")}
            >
              Open in new tab
            </button>
            <button
              type="button"
              className="rounded-md bg-[var(--series-1)] px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
              disabled={!blobUrl}
              onClick={handleDownload}
            >
              Download
            </button>
            <button
              type="button"
              onClick={onClose}
              className="px-1 text-[var(--text-muted)] hover:text-[var(--text-primary)]"
              aria-label="Close preview"
            >
              ✕
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-hidden rounded border border-[var(--border-hairline)] bg-[var(--surface-2)]">
          {error ? (
            <p className="p-6 text-sm text-[var(--status-critical)]">{error}</p>
          ) : !blobUrl ? (
            <p className="p-6 text-sm text-[var(--text-muted)]">Generating the document…</p>
          ) : (
            <iframe src={blobUrl} title={title} className="h-full w-full" />
          )}
        </div>

        <p className="mt-2 text-xs text-[var(--text-muted)]">
          This is the same file that gets attached when you email this document — not a separate preview
          rendering, so what you see here is what the customer receives.
        </p>
      </div>
    </div>
  );
}
