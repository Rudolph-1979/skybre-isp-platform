import type { ReactNode } from "react";

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[var(--overlay-scrim)] p-4" onClick={onClose}>
      <div
        className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-lg bg-[var(--surface-1)] p-4 shadow-lg sm:p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-[var(--text-primary)]">{title}</h2>
          <button onClick={onClose} className="text-[var(--text-muted)] hover:text-[var(--text-primary)]">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

export function FormField({
  label,
  children,
  required = false,
  hint,
}: {
  label: string;
  children: ReactNode;
  /** Shows the marker only. Enforcement stays on the input itself, so
   *  this can never claim a field is required when it isn't. */
  required?: boolean;
  /** One line under the control, for what people would otherwise have to
   *  guess -- usually what leaving it blank actually does. */
  hint?: string;
}) {
  return (
    <label className="mb-3 block text-sm">
      <span className="mb-1 block font-medium text-[var(--text-secondary)]">
        {label}
        {required && <span className="ml-0.5 text-[var(--status-critical)]">*</span>}
      </span>
      {children}
      {hint && <span className="mt-1 block text-xs text-[var(--text-muted)]">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]";

// Compact, content-width variant for inline filter controls (e.g. filter <select>s next to
// a "+ New ..." button). Deliberately does NOT include w-full: appending "w-auto" to
// inputClass doesn't work because Tailwind's compiled stylesheet defines .w-full after
// .w-auto, so .w-full always wins the cascade when both classes are present on one element.
export const filterSelectClass =
  "w-auto rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--text-primary)] focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]";

export const btnPrimary =
  "rounded-md bg-[var(--series-1)] px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50";

export const btnSecondary =
  "rounded-md border border-[var(--baseline)] px-4 py-2 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--tint-hover)]";
