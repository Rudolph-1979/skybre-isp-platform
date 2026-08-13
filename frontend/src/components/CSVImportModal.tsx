import { useState } from "react";
import { api } from "../api/client";
import { Modal, btnPrimary, btnSecondary } from "./Modal";

interface PreviewRow {
  row: number;
  data: Record<string, unknown>;
  errors: string[];
}

interface PreviewResult {
  total_rows: number;
  valid_count: number;
  invalid_count: number;
  rows: PreviewRow[];
}

interface CommitResult {
  created: number;
  skipped_count: number;
  skipped: { row: number; errors: string[] }[];
}

export function CSVImportModal({
  title,
  importUrlBase,
  templateHeaders,
  templateFilename,
  onClose,
  onImported,
}: {
  title: string;
  /** e.g. "/customers/" — the mixin adds import-preview/ and import-commit/ */
  importUrlBase: string;
  templateHeaders: string[];
  templateFilename: string;
  onClose: () => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<PreviewResult | null>(null);
  const [commitResult, setCommitResult] = useState<CommitResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function downloadTemplate() {
    const blob = new Blob([templateHeaders.join(",") + "\n"], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = templateFilename;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function handlePreview() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api.post<PreviewResult>(`${importUrlBase}import-preview/`, formData);
      setPreview(res.data);
    } catch {
      setError("Could not read that file. Make sure it's a valid CSV.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCommit() {
    if (!file) return;
    setLoading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await api.post<CommitResult>(`${importUrlBase}import-commit/`, formData);
      setCommitResult(res.data);
      onImported();
    } catch {
      setError("Import failed. Nothing was changed — please try again.");
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setFile(null);
    setPreview(null);
    setCommitResult(null);
    setError(null);
  }

  return (
    <Modal title={title} onClose={onClose}>
      {!commitResult ? (
        <>
          <p className="mb-3 text-sm text-[var(--text-secondary)]">
            Upload a CSV file. You'll see a preview of what will be created — and any rows with
            problems — before anything is saved.
          </p>

          <button type="button" className="mb-4 text-sm text-[var(--series-1)] underline" onClick={downloadTemplate}>
            Download CSV template
          </button>

          <input
            type="file"
            accept=".csv,text/csv"
            className="mb-4 block w-full text-sm"
            onChange={(e) => {
              reset();
              setFile(e.target.files?.[0] ?? null);
            }}
          />

          {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

          {preview && (
            <div className="mb-4 max-h-64 overflow-y-auto rounded-md border border-[var(--border-hairline)] p-3 text-sm">
              <p className="mb-2 font-medium text-[var(--text-primary)]">
                {preview.total_rows} row{preview.total_rows === 1 ? "" : "s"} found —{" "}
                <span className="text-[var(--status-good)]">{preview.valid_count} ready to import</span>
                {preview.invalid_count > 0 && (
                  <>
                    {", "}
                    <span className="text-[var(--status-critical)]">{preview.invalid_count} with problems</span>
                  </>
                )}
              </p>
              {preview.invalid_count > 0 && (
                <ul className="space-y-1">
                  {preview.rows
                    .filter((r) => r.errors.length > 0)
                    .slice(0, 30)
                    .map((r) => (
                      <li key={r.row} className="text-[var(--text-secondary)]">
                        <span className="font-medium">Row {r.row}:</span> {r.errors.join("; ")}
                      </li>
                    ))}
                  {preview.invalid_count > 30 && (
                    <li className="text-[var(--text-muted)]">…and {preview.invalid_count - 30} more.</li>
                  )}
                </ul>
              )}
            </div>
          )}

          <div className="mt-4 flex justify-end gap-2">
            <button type="button" className={btnSecondary} onClick={onClose}>
              Cancel
            </button>
            {!preview ? (
              <button type="button" disabled={!file || loading} className={btnPrimary} onClick={handlePreview}>
                {loading ? "Reading…" : "Preview"}
              </button>
            ) : (
              <button
                type="button"
                disabled={loading || preview.valid_count === 0}
                className={btnPrimary}
                onClick={handleCommit}
              >
                {loading ? "Importing…" : `Import ${preview.valid_count} row${preview.valid_count === 1 ? "" : "s"}`}
              </button>
            )}
          </div>
        </>
      ) : (
        <>
          <p className="mb-3 text-sm text-[var(--text-primary)]">
            <span className="font-medium text-[var(--status-good)]">{commitResult.created} created.</span>{" "}
            {commitResult.skipped_count > 0 && (
              <span className="text-[var(--status-critical)]">{commitResult.skipped_count} skipped.</span>
            )}
          </p>
          {commitResult.skipped_count > 0 && (
            <ul className="mb-4 max-h-48 space-y-1 overflow-y-auto text-sm text-[var(--text-secondary)]">
              {commitResult.skipped.slice(0, 30).map((r) => (
                <li key={r.row}>
                  <span className="font-medium">Row {r.row}:</span> {r.errors.join("; ")}
                </li>
              ))}
            </ul>
          )}
          <div className="mt-4 flex justify-end">
            <button type="button" className={btnPrimary} onClick={onClose}>
              Done
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
