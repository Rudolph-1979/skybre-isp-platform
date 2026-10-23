import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { canAccessSection } from "../../utils/permissions";
import type { DashboardSummary, UpcomingBlocks, CustomerUsage } from "../../types";
import {
  CUSTOMER_HELP, STAFF_HELP, STAFF_TOUR, searchKb,
  type ConectoMessage, type KbEntry, type Pose, type TourStep,
} from "./script";

import idleImg from "../../assets/conecto/conecto-idle.webp";
import lookImg from "../../assets/conecto/conecto-look.webp";
import alertImg from "../../assets/conecto/conecto-alert.webp";
import thinkImg from "../../assets/conecto/conecto-think.webp";

// Conecto — the in-platform assistant.
//
// He is rendered from the actual 3D model: five stills shot from one fixed
// camera at different yaw/pitch angles, so swapping between them reads as him
// looking around rather than cutting to a different picture. The model has no
// skeleton, so there are no real poses (no wave, no pointing, no blink) — the
// life comes from the angle changes plus CSS, and the speech bubble carries
// the personality.
//
// Deliberately adds NO backend endpoints. Everything he says comes from data
// the platform already serves, so he can never be the reason an API call
// fails: /dashboard-summary/ and /upcoming-blocks/ for staff,
// /customers/<id>/usage/ for a customer. Every fetch fails soft — if a call
// errors or 403s, that message simply doesn't exist.

const POSE_SRC: Record<Pose, string> = {
  idle: idleImg,
  look: lookImg,
  alert: alertImg,
  think: thinkImg,
};

// Preload the non-idle frames once he's first opened, so a pose change is
// instant rather than a visible pop.
let preloaded = false;
function preload() {
  if (preloaded) return;
  preloaded = true;
  Object.values(POSE_SRC).forEach((src) => { const i = new Image(); i.src = src; });
}

const DISMISSED_KEY = "conecto:dismissed";
const TOUR_KEY = "conecto:tour-done";
const REFRESH_MS = 120_000;
// He glances around on this cadence while idle. Slow on purpose: a mascot
// that moves constantly is the kind of thing people disable on day two.
const GLANCE_MS = 9_000;

function readFlag(key: string) {
  try { return localStorage.getItem(key) === "1"; } catch { return false; }
}
function writeFlag(key: string) {
  try { localStorage.setItem(key, "1"); } catch { /* private mode — fine */ }
}

function helpFor(pathname: string, staff: boolean) {
  const table = staff ? STAFF_HELP : CUSTOMER_HELP;
  // Longest prefix wins, so /admin/customers beats /admin.
  return [...table].sort((a, b) => b.prefix.length - a.prefix.length)
    .find((h) => pathname.startsWith(h.prefix));
}

export function Conecto({ audience }: { audience: "staff" | "customer" }) {
  const { user } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const staff = audience === "staff";

  const [dismissed, setDismissed] = useState(() => readFlag(DISMISSED_KEY));
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<ConectoMessage[]>([]);
  const [glancing, setGlancing] = useState(false);
  const [tourStep, setTourStep] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const bubbleRef = useRef<HTMLDivElement | null>(null);

  // ---- what he has to say -------------------------------------------------
  const loadStaff = useCallback(async () => {
    const found: ConectoMessage[] = [];

    // Both of these are permission-gated server-side. A 403 just means this
    // staff member doesn't get that message.
    const [summary, blocks] = await Promise.all([
      api.get<DashboardSummary>("/dashboard-summary/").then((r) => r.data).catch(() => null),
      canAccessSection(user, "finance")
        ? api.get<UpcomingBlocks>("/upcoming-blocks/?days=7").then((r) => r.data).catch(() => null)
        : Promise.resolve(null),
    ]);

    if (blocks && blocks.count_tomorrow > 0) {
      found.push({
        id: "blocks",
        weight: 100,
        pose: "alert",
        // Deep-links straight into the open list, not just the dashboard.
        to: "/admin?panel=blocks",
        text: blocks.auto_suspend_enabled
          ? `${blocks.count_tomorrow} customer${blocks.count_tomorrow === 1 ? "" : "s"} will be cut off tomorrow.`
          : `${blocks.count_tomorrow} customer${blocks.count_tomorrow === 1 ? "" : "s"} are overdue enough to be cut off — auto-suspension is off, so nothing will happen automatically.`,
      });
    } else if (blocks && blocks.count_horizon > 0) {
      found.push({
        id: "blocks-soon",
        weight: 70,
        pose: "think",
        to: "/admin?panel=blocks",
        text: `${blocks.count_horizon} customer${blocks.count_horizon === 1 ? "" : "s"} heading for a cut-off in the next week.`,
      });
    }

    if (summary) {
      if (summary.devices_offline > 0) {
        found.push({
          id: "devices",
          weight: 95,
          pose: "alert",
          to: "/admin/networking",
          text: `${summary.devices_offline} of ${summary.devices_total} routers ${summary.devices_offline === 1 ? "is" : "are"} offline.`,
        });
      }
      if (summary.tickets_urgent > 0) {
        found.push({
          id: "tickets-urgent",
          weight: 90,
          pose: "alert",
          to: "/admin/tickets",
          text: `${summary.tickets_urgent} urgent ticket${summary.tickets_urgent === 1 ? "" : "s"} open.`,
        });
      }
      if (summary.invoices_overdue > 0) {
        found.push({
          id: "overdue",
          weight: 60,
          pose: "think",
          to: "/admin/finance",
          text: `${summary.invoices_overdue} invoice${summary.invoices_overdue === 1 ? "" : "s"} overdue.`,
        });
      }
      if (summary.tickets_open > 0 && summary.tickets_urgent === 0) {
        found.push({
          id: "tickets",
          weight: 30,
          pose: "look",
          to: "/admin/tickets",
          text: `${summary.tickets_open} ticket${summary.tickets_open === 1 ? "" : "s"} waiting.`,
        });
      }
    }

    found.sort((a, b) => b.weight - a.weight);
    setMessages(found);
  }, [user]);

  const loadCustomer = useCallback(async () => {
    if (!user?.customer_id) return;
    const usage = await api
      .get<CustomerUsage>(`/customers/${user.customer_id}/usage/`)
      .then((r) => r.data)
      .catch(() => null);
    if (!usage) return;

    const found: ConectoMessage[] = [];
    const cap = usage.cap_bytes;
    if (cap && cap > 0) {
      const pct = Math.round((usage.total_bytes / cap) * 100);
      if (pct >= 90) {
        found.push({ id: "cap", weight: 100, pose: "alert",
          text: `You've used ${pct}% of your data this month.` });
      } else if (pct >= 75) {
        found.push({ id: "cap", weight: 60, pose: "think",
          text: `You're at ${pct}% of your data for the month.` });
      }
    }
    if (usage.live_sessions.length === 0) {
      found.push({ id: "offline", weight: 40, pose: "think",
        text: "I can't see your connection at the moment. If your internet is working, this page may just be a few minutes behind." });
    }
    found.sort((a, b) => b.weight - a.weight);
    setMessages(found);
  }, [user]);

  useEffect(() => {
    if (dismissed) return;
    const load = staff ? loadStaff : loadCustomer;
    load();
    const t = setInterval(load, REFRESH_MS);
    return () => clearInterval(t);
  }, [dismissed, staff, loadStaff, loadCustomer]);

  // ---- first-run tour -----------------------------------------------------
  useEffect(() => {
    if (dismissed || !staff) return;
    if (readFlag(TOUR_KEY)) return;
    const t = setTimeout(() => { setTourStep(0); setOpen(true); preload(); }, 1400);
    return () => clearTimeout(t);
  }, [dismissed, staff]);

  function endTour() {
    writeFlag(TOUR_KEY);
    setTourStep(null);
  }
  function nextTourStep() {
    if (tourStep === null) return;
    const next = tourStep + 1;
    if (next >= STAFF_TOUR.length) return endTour();
    setTourStep(next);
    const step: TourStep = STAFF_TOUR[next];
    if (step.to) navigate(step.to);
  }

  // ---- idle glance --------------------------------------------------------
  useEffect(() => {
    if (dismissed) return;
    const t = setInterval(() => {
      setGlancing(true);
      setTimeout(() => setGlancing(false), 1500);
    }, GLANCE_MS);
    return () => clearInterval(t);
  }, [dismissed]);

  const help = useMemo(() => helpFor(location.pathname, staff), [location.pathname, staff]);
  const results: KbEntry[] = useMemo(
    () => searchKb(query, staff ? "staff" : "customer"),
    [query, staff]
  );
  const searching = query.trim().length >= 2;
  const top = messages[0];
  const tour = tourStep !== null ? STAFF_TOUR[tourStep] : null;

  const pose: Pose = tour ? tour.pose
    : searching ? "think"
    : open ? (top?.pose ?? "look")
    : top ? top.pose
    : glancing ? "look"
    : "idle";

  if (!user) return null;

  // Hidden: leave one small, low-contrast way back. A dismissed assistant
  // with no route back means the only fix is the browser console, which is
  // not a reasonable thing to ask of anyone.
  if (dismissed) {
    return (
      <button
        type="button"
        onClick={() => { try { localStorage.removeItem(DISMISSED_KEY); } catch { /* ignore */ } setDismissed(false); }}
        className="fixed bottom-3 right-3 z-40 rounded-full border border-[var(--border-hairline)] bg-[var(--surface-1)] px-2.5 py-1 text-[11px] text-[var(--text-muted)] opacity-40 shadow-sm transition hover:opacity-100 hover:text-[var(--text-secondary)] sm:bottom-4 sm:right-4"
      >
        Show Conecto
      </button>
    );
  }

  const badge = messages.filter((m) => m.weight >= 90).length;

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-40 flex items-end gap-2 sm:bottom-6 sm:right-6">
      {/* speech bubble */}
      {(open || tour) && (
        <div
          ref={bubbleRef}
          className="pointer-events-auto mb-2 max-h-[70vh] w-[17rem] overflow-y-auto rounded-xl border border-[var(--border-hairline)] bg-[var(--surface-1)] p-3 shadow-lg sm:w-80"
          role="dialog"
          aria-label="Conecto"
        >
          {tour ? (
            <>
              <p className="text-sm font-semibold text-[var(--text-primary)]">{tour.title}</p>
              <p className="mt-1 text-xs leading-relaxed text-[var(--text-secondary)]">{tour.body}</p>
              <div className="mt-3 flex items-center justify-between">
                <span className="text-xs text-[var(--text-muted)]">
                  {tourStep! + 1} of {STAFF_TOUR.length}
                </span>
                <div className="flex gap-2">
                  <button type="button" onClick={endTour}
                    className="text-xs text-[var(--text-muted)] hover:underline">Skip</button>
                  <button type="button" onClick={nextTourStep}
                    className="rounded-md bg-[var(--series-1)] px-2.5 py-1 text-xs font-medium text-white">
                    {tourStep! + 1 === STAFF_TOUR.length ? "Done" : "Next"}
                  </button>
                </div>
              </div>
            </>
          ) : (
            <>
              {/* Ask box. A keyword search over written answers, not a
                  language model -- so it can never invent one. */}
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={staff ? "What do you need? e.g. \"payment not matching\"" : "What do you need help with?"}
                className="mb-2.5 w-full rounded-md border border-[var(--baseline)] bg-[var(--surface-2)] px-2.5 py-1.5 text-xs text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:border-[var(--series-1)] focus:outline-none focus:ring-1 focus:ring-[var(--series-1)]"
              />

              {searching ? (
                results.length > 0 ? (
                  <ul className="space-y-2.5">
                    {results.map((r) => (
                      <li key={r.id}>
                        <p className="text-xs font-semibold text-[var(--text-primary)]">{r.title}</p>
                        <p className="mt-0.5 text-xs leading-relaxed text-[var(--text-secondary)]">{r.body}</p>
                        {r.to && (
                          <button type="button"
                            onClick={() => { navigate(r.to!); setOpen(false); setQuery(""); }}
                            className="mt-1 text-xs font-medium text-[var(--series-1)] hover:underline">
                            Take me there →
                          </button>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs leading-relaxed text-[var(--text-secondary)]">
                    I don't have an answer for that one. Try a different word — or log a ticket and a
                    person will pick it up.
                  </p>
                )
              ) : messages.length > 0 ? (
                <ul className="space-y-1.5">
                  {messages.slice(0, 4).map((m) => (
                    <li key={m.id}>
                      {m.to ? (
                        <button type="button"
                          onClick={() => { navigate(m.to!); setOpen(false); }}
                          className="w-full text-left text-xs leading-relaxed text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:underline">
                          {m.text}
                        </button>
                      ) : (
                        <span className="text-xs leading-relaxed text-[var(--text-secondary)]">{m.text}</span>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs leading-relaxed text-[var(--text-secondary)]">
                  {staff ? "Nothing needs attention right now." : "Everything looks fine on your line."}
                </p>
              )}

              {/* Suppressed while searching -- the answer they asked for
                  shouldn't compete with a note about the current page. */}
              {help && !searching && (
                <div className="mt-2.5 border-t border-[var(--border-hairline)] pt-2.5">
                  <p className="text-xs font-semibold text-[var(--text-primary)]">{help.title}</p>
                  <p className="mt-0.5 text-xs leading-relaxed text-[var(--text-muted)]">{help.body}</p>
                </div>
              )}

              <div className="mt-2.5 flex items-center justify-between border-t border-[var(--border-hairline)] pt-2">
                <button type="button"
                  onClick={() => { setDismissed(true); writeFlag(DISMISSED_KEY); }}
                  className="text-xs text-[var(--text-muted)] hover:underline">
                  Hide Conecto
                </button>
                <button type="button" onClick={() => setOpen(false)}
                  className="text-xs text-[var(--text-muted)] hover:underline">Close</button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Conecto himself */}
      <button
        type="button"
        onClick={() => { preload(); setOpen((o) => !o); }}
        aria-label={open ? "Close Conecto" : "Open Conecto"}
        className="pointer-events-auto relative h-[76px] w-[76px] shrink-0 overflow-hidden rounded-full border-2 border-[var(--surface-1)] bg-[var(--surface-2)] shadow-lg transition hover:scale-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--series-1)] sm:h-[88px] sm:w-[88px]"
        style={{ animation: "conecto-bob 4.5s ease-in-out infinite" }}
      >
        {/* Frames are cropped to one shared box, so switching src cannot make
            him jump. The circular mask hides the un-posed arm stubs. */}
        {/* top/width were set by eye against a rendered preview at the real
            88px size: anything tighter crops to his forehead. */}
        <img
          src={POSE_SRC[pose]}
          alt="Conecto"
          draggable={false}
          className="absolute left-1/2 top-0 w-[112%] max-w-none -translate-x-1/2 select-none transition-opacity duration-200"
        />
        {badge > 0 && !open && (
          <span className="absolute right-0.5 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-[var(--status-critical)] text-[10px] font-bold text-white ring-2 ring-[var(--surface-1)]">
            {badge}
          </span>
        )}
      </button>

      <style>{`
        @keyframes conecto-bob {
          0%, 100% { transform: translateY(0) rotate(0deg); }
          50%      { transform: translateY(-5px) rotate(-1.5deg); }
        }
        @media (prefers-reduced-motion: reduce) {
          @keyframes conecto-bob { 0%, 100% { transform: none; } }
        }
      `}</style>
    </div>
  );
}
