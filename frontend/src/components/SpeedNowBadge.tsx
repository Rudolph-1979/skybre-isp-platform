import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ServiceSpeedNow } from "../types";

/**
 * What this line is running at, right now, and why.
 *
 * Exists because otherwise "my internet is slow" has no answer anybody
 * can give without reading code: the speed depends on the plan, a
 * possible Connection Rule override, whichever speed window happens to be
 * on, and month-to-date usage against a fair-use threshold. A support
 * agent needs one sentence, not four places to look.
 *
 * Renders nothing at all when the line is simply at its plan speed — the
 * overwhelmingly common case, where a badge saying "normal" on every
 * service row would be pure noise and would train people to ignore it.
 */

function mbps(kbps: number) {
  return `${(kbps / 1024).toFixed(kbps < 1024 ? 1 : 0)} Mbps`;
}

const TONE: Record<ServiceSpeedNow["reason"], { bg: string; text: string }> = {
  plan: { bg: "var(--tint-hover)", text: "var(--text-muted)" },
  window: {
    bg: "color-mix(in srgb, var(--status-good) 14%, transparent)",
    text: "var(--status-good)",
  },
  // Boosted despite being over — good news right now, so it reads as the
  // boost rather than as the shaping.
  "window over fup": {
    bg: "color-mix(in srgb, var(--status-good) 14%, transparent)",
    text: "var(--status-good)",
  },
  fup: {
    bg: "color-mix(in srgb, var(--status-warning) 16%, transparent)",
    text: "var(--status-warning)",
  },
};

export function SpeedNowBadge({ serviceId }: { serviceId: number }) {
  const [state, setState] = useState<ServiceSpeedNow | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get<ServiceSpeedNow>(`/services/${serviceId}/speed-now/`)
      .then((res) => {
        if (!cancelled) setState(res.data);
      })
      .catch(() => {
        // A readout that can't load is not worth an error on a page about
        // something else. The line is unaffected either way.
      });
    return () => {
      cancelled = true;
    };
  }, [serviceId]);

  if (!state || state.reason === "plan") return null;

  const tone = TONE[state.reason];
  return (
    <span
      className="mt-0.5 inline-flex flex-wrap items-baseline gap-1.5 rounded px-1.5 py-0.5 text-xs"
      style={{ background: tone.bg, color: tone.text }}
      title={state.explanation}
    >
      <span className="font-semibold">
        {state.reason === "fup" ? "Shaped" : "Boosted"} · {mbps(state.download_kbps)}
      </span>
      <span className="opacity-80">
        {state.reason === "fup"
          ? `of ${mbps(state.plan_download_kbps)} — ${state.used_gb} GB used of ${state.threshold_gb} GB`
          : `by “${state.window_name}”`}
      </span>
      {/* A line that is boosted AND over its threshold looked identical to
          one that is simply boosted — and the difference is the whole
          call: this customer goes back to shaped the moment the window
          closes, which is what they will phone about next. */}
      {state.reason === "window over fup" && (
        <span className="opacity-80">
          · over fair use ({state.used_gb} of {state.threshold_gb} GB), shaped again when it ends
        </span>
      )}
    </span>
  );
}
