import type { Lead, LeadStatus, PipelineSummary } from "../types";

/** Shared pieces between the leads list and a single lead's page. */

export const LEAD_SOURCES: { key: string; label: string }[] = [
  { key: "walk_in", label: "Walk-in" },
  { key: "phone", label: "Phone enquiry" },
  { key: "referral", label: "Referral" },
  { key: "website", label: "Website" },
  { key: "social", label: "Social media" },
  { key: "reseller", label: "Reseller" },
  { key: "campaign", label: "Campaign" },
  { key: "existing_customer", label: "Existing customer" },
  { key: "other", label: "Other" },
];

export const LOST_REASONS: { key: string; label: string }[] = [
  // First, because it's the one worth acting on: not a sales failure, a
  // coverage gap.
  { key: "no_coverage", label: "No coverage" },
  { key: "price", label: "Price" },
  { key: "competitor", label: "Went with a competitor" },
  { key: "went_quiet", label: "Went quiet" },
  { key: "not_ready", label: "Not ready yet" },
  { key: "duplicate", label: "Duplicate enquiry" },
  { key: "other", label: "Other" },
];

export const LEAD_NOTE_KINDS: { key: string; label: string }[] = [
  { key: "note", label: "Note" },
  { key: "call", label: "Call" },
  { key: "email", label: "Email" },
  { key: "meeting", label: "Meeting" },
];

// Won borrows the same green as an active customer and Lost the same red
// as a cancelled one, so the palette means the same thing here as it does
// everywhere else in the platform.
//
// Deliberately NOT a warm rainbow through the middle: orange next to
// yellow is the one pairing people genuinely cannot tell apart at a
// glance, and it made Qualified look like a step backwards from
// Contacted. Blue → teal reads as progress; amber is kept for Quoted
// alone, where it earns its meaning -- the ball is in the customer's
// court and nothing moves until they answer.
const STAGE_STYLE: Record<LeadStatus, { bg: string; text: string }> = {
  new: { bg: "var(--tint-hover)", text: "var(--text-secondary)" },
  contacted: { bg: "color-mix(in srgb, var(--series-1) 14%, transparent)", text: "var(--series-1)" },
  qualified: { bg: "color-mix(in srgb, var(--series-3) 14%, transparent)", text: "var(--series-3)" },
  quoted: { bg: "color-mix(in srgb, var(--status-warning) 16%, transparent)", text: "var(--status-warning)" },
  won: { bg: "color-mix(in srgb, var(--status-good) 14%, transparent)", text: "var(--status-good)" },
  lost: { bg: "color-mix(in srgb, var(--status-critical) 12%, transparent)", text: "var(--status-critical)" },
};

/**
 * A follow-up date, marked when it has come due.
 *
 * A colour change alone was not enough: --status-serious is a pale salmon
 * that sits too close to ordinary text on a dark background, so the one
 * thing the column exists to show was the thing you couldn't see. A chip
 * reads at a glance in both themes.
 */
export function FollowUpCell({ lead }: { lead: Lead }) {
  const label = lead.next_follow_up
    ? new Date(`${lead.next_follow_up}T00:00:00`).toLocaleDateString(undefined, {
        day: "2-digit",
        month: "short",
      })
    : "—";
  if (!lead.follow_up_is_due) {
    return <span className="tabular-nums text-[var(--text-secondary)]">{label}</span>;
  }
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded px-2 py-0.5 text-xs font-semibold tabular-nums"
      style={{
        background: "color-mix(in srgb, var(--status-warning) 18%, transparent)",
        color: "var(--status-warning)",
      }}
    >
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--status-warning)" }} />
      {label}
    </span>
  );
}

export function LeadStatusBadge({ lead }: { lead: Lead }) {
  const style = STAGE_STYLE[lead.status];
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="rounded px-2 py-0.5 text-xs font-medium"
        style={{ background: style.bg, color: style.text }}
      >
        {lead.status_display}
      </span>
      {lead.status === "lost" && lead.lost_reason_display && (
        <span className="text-xs text-[var(--text-muted)]">{lead.lost_reason_display}</span>
      )}
    </span>
  );
}

function money(value: string | number) {
  const n = typeof value === "string" ? parseFloat(value) : value;
  if (!Number.isFinite(n)) return "R 0";
  return `R ${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

/**
 * The stage strip: where the deals and the money are sitting, and the
 * filter, in one row. This is the part of a pipeline board actually worth
 * having — clicking a stage filters the list below it.
 */
export function LeadStageStrip({
  summary,
  active,
  onPick,
}: {
  summary: PipelineSummary;
  active: LeadStatus | "";
  onPick: (status: LeadStatus | "") => void;
}) {
  return (
    <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
      {summary.stages.map((stage) => {
        const isActive = active === stage.status;
        const style = STAGE_STYLE[stage.status];
        return (
          <button
            key={stage.status}
            type="button"
            onClick={() => onPick(isActive ? "" : stage.status)}
            className={`rounded-lg border px-3 py-2.5 text-left transition-colors ${
              isActive
                ? "border-[var(--series-1)] bg-[var(--series-1)]/8"
                : "border-[var(--border-hairline)] bg-[var(--surface-1)] hover:bg-[var(--tint-subtle)]"
            }`}
          >
            <span
              className="text-[11px] font-semibold uppercase tracking-wide"
              style={{ color: style.text }}
            >
              {stage.label}
            </span>
            <span className="mt-0.5 block text-lg font-semibold tabular-nums text-[var(--text-primary)]">
              {stage.count}
            </span>
            <span className="block text-xs tabular-nums text-[var(--text-muted)]">
              {money(stage.value)}/mo
            </span>
          </button>
        );
      })}
    </div>
  );
}
