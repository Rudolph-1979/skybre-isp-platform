// Everything Conecto says, kept out of the component so the wording can be
// edited without touching behaviour.
//
// The two audiences are deliberately separate. Staff messages quote internal
// figures and internal vocabulary; a customer must never see either. Nothing
// is shared between the two sets — no "clever" reuse — because the failure
// mode is leaking a staff-facing number onto a customer's screen.

import { KB } from "./kb";

export type Pose = "idle" | "look" | "alert" | "think";

export interface ConectoMessage {
  id: string;
  text: string;
  pose: Pose;
  /** Where clicking the message takes you. Omit for advisory-only. */
  to?: string;
  /** Higher sorts first. Money and cut-offs outrank curiosities. */
  weight: number;
}

// ---------------------------------------------------------------------------
// Contextual help — keyed by route prefix, longest match wins.
// Written for someone who did not build the platform.
// ---------------------------------------------------------------------------

export const STAFF_HELP: { prefix: string; title: string; body: string }[] = [
  {
    prefix: "/admin/customers",
    title: "Customers",
    body: "Every account on the platform. Search finds names, references, public IPs and PPPoE usernames; the Connection and Payment status columns answer 'who is down' and 'who owes' at a glance. The payment reference is what customers type on their EFT — it's how Bank Feeds matches their money.",
  },
  {
    prefix: "/admin/finance",
    title: "Finance",
    body: "Quotes, pro formas and invoices each have a tab with + New quote / + New invoice. Recurring Billing is where each billing date is previewed and approved — the daily 15:00 run creates no invoices without an approved, still-matching preview.",
  },
  {
    prefix: "/admin/inventory",
    title: "Stock / Inventory",
    body: "Equipment invoices belong in Stock Receipts — they feed Input VAT, so don't capture them under Expenses too. Stock goes out through Stock Issues & Transfers; fix counts with Adjust, never by deleting.",
  },
  {
    prefix: "/admin/accountant",
    title: "Accountant",
    body: "VAT returns, expenses, the chart of accounts, Bank Feeds, attendance, leave and payroll. Input VAT comes from Expenses and from stock receipts, so an equipment invoice belongs in one place only. In Bank Feeds nothing becomes a payment or expense until someone clicks Confirm.",
  },
  {
    prefix: "/admin/networking",
    title: "Networking",
    body: "Routers, sites, IP pools, OpenVPN and live sessions. A pool reading 0 / 0 hands out nothing until you click Generate addresses. RADIUS clients now live under Configs → RADIUS.",
  },
  {
    prefix: "/admin/scheduling",
    title: "Scheduling",
    body: "Jobs, the job pool, staff shifts and weekly support rotations. A job only reaches a technician's phone once it has a time, an address and a Technician-role person assigned; the calendar refreshes every 5 seconds.",
  },
  {
    prefix: "/admin/services",
    title: "Services",
    body: "Every line on the platform, filterable by status. A client's own services, with Cancel service, signal and online state, are on their customer page. Changes write through to the router straight away — a plan change reaches the live session without disconnecting the client.",
  },
  {
    prefix: "/admin/tickets",
    title: "Support Tickets",
    body: "Customer tickets and technicians' job feedback. Accept a ticket to own it; only the customer resolves and only Management closes. Three separate days with tickets in one month flags a customer as high alert.",
  },
  {
    prefix: "/admin/vehicles",
    title: "Vehicles",
    body: "The fleet: km, services, tyres, fuel, trips and licence renewals. Webfleet adds trips and a km reading every 15 minutes; fuel consumption still comes from our own fuel log. Import the FNB Fleetcard statement with the button top right.",
  },
  {
    prefix: "/admin/bulk-email",
    title: "Email",
    body: "Send one template to a selected list, and Sent Email for everything the platform has sent. Invoices, quotes and pro formas go per customer, because each needs its own document attached.",
  },
  {
    prefix: "/admin/configs",
    title: "Configs",
    body: "Platform settings: tariffs, email templates and settings, contracts, users and permissions, partners, billing and the activity log. Which tabs you see depends on your role; Auto-suspension under Billing is the master switch for cutting off overdue customers.",
  },
  {
    prefix: "/admin/account",
    title: "Your account",
    body: "Your two-factor authentication. Turn it on with an authenticator app and keep the backup codes somewhere safe; they're shown once.",
  },
  {
    prefix: "/admin",
    title: "Dashboard",
    body: "Where things stand right now. Click any tile for the list behind its number; Arrange tiles puts them in your own order, saved to your profile.",
  },
  {
    prefix: "/admin/usage-report",
    title: "Usage report",
    body: "Every customer's data usage for one period, heaviest first, with how much of any cap they've used. The table shows the top 200; Export CSV gives you everyone.",
  },
  {
    prefix: "/admin/offline-customers",
    title: "Recently offline",
    body: "A call list: customers whose line went down and hasn't come back, longest first. Suspended and cancelled customers are left out, and the list re-checks itself every minute.",
  },
  {
    prefix: "/admin/finance/invoices",
    title: "Invoice / quote",
    body: "One document. Convert a quote to a pro forma or invoice here (one-way), send or preview the PDF, record a payment on a real invoice, and request deletion of a quote or pro forma — Management approves that.",
  },
  {
    prefix: "/bank-feeds/fnb-callback",
    title: "Connect FNB bank feed",
    body: "Where FNB returns you after approving a bank-feed connection. An Administrator must be signed in to finish it; then use Sync now on the account.",
  },
  {
    prefix: "/admin/administration/clients-per-partner",
    title: "Clients per partner",
    body: "Clients, live lines and monthly value per partner. A client with lines at two partners appears in both rows; the total counts each client once. Click a row to see its clients.",
  },
  {
    prefix: "/admin/administration/clients-per-tariff",
    title: "Clients per tariff",
    body: "Clients, live lines, monthly value, cost and markup per tariff. Lines on a tariff with no cost set are left out of cost and profit and flagged 'without a cost'.",
  },
  {
    prefix: "/admin/administration/revenue",
    title: "Revenue",
    body: "MRR and ARPU over time, top payers, payments and invoicing per period, and outstanding by payment method. Dashed MRR points are estimates; solid ones were recorded on the day.",
  },
  {
    prefix: "/admin/administration",
    title: "Administration",
    body: "Management reports: clients per partner, clients per tariff and revenue.",
  },
  {
    prefix: "/admin/networking/map",
    title: "Map",
    body: "Towers and links from UISP, technicians and Webfleet vehicles, and the 'Can we service it?' check. Click the map, paste GPS or search an address; line of sight is a guide, so confirm with a scan.",
  },
  {
    prefix: "/admin/networking/uisp",
    title: "Network (UISP)",
    body: "Backhaul, access points and core read from UISP every minute, read-only. Click a device for its graphs; MikroTiks only show whether they answer, so their detail is under Networking → Routers.",
  },
  {
    prefix: "/admin/networking/devices",
    title: "Router detail",
    body: "Charts, live sessions straight from the router, and who connects through this box, with phone numbers for outage calls. Test Connection and Poll Now need the RouterOS API enabled.",
  },
  {
    prefix: "/map-screen",
    title: "Map screen",
    body: "The map for a second screen or TV: towers, links and vehicles, with a panel listing sites and links that are down. Same sign-in and rights as Networking → Map.",
  },
  {
    prefix: "/admin/tickets/kpi-report",
    title: "Ticket KPI report",
    body: "Tickets accepted and resolved cleanly per staff member, by year, plus NOC. Score is tickets resolved ÷ average resolution hours; reopened tickets never count.",
  },
  {
    prefix: "/admin/leads",
    title: "Leads",
    body: "Enquiries from first contact to Accepted or Lost: qualify, check coverage, quote, then accept to create the client and an installation job in the pool. Keep a follow-up date on every open lead.",
  },
];

export const CUSTOMER_HELP: { prefix: string; title: string; body: string }[] = [
  {
    prefix: "/portal/invoices",
    title: "Invoices & Payments",
    body: "Everything billed to you and every payment we received, newest first. Click View PDF for a copy, or View statement for your whole account.",
  },
  {
    prefix: "/portal/tickets",
    title: "Support",
    body: "Log anything that isn't working and we'll pick it up. When it's fixed, click Mark as resolved; if it comes back, reopen it.",
  },
  {
    prefix: "/portal",
    title: "Your account",
    body: "Your account at a glance: what you owe, your services, and how much data you've used this month. Your account reference is under Welcome back; use it on every payment.",
  },
];

// ---------------------------------------------------------------------------
// First-run tour. Runs once, then never again unless reset.
// ---------------------------------------------------------------------------

export interface TourStep {
  title: string;
  body: string;
  pose: Pose;
  to?: string;
}

export const STAFF_TOUR: TourStep[] = [
  {
    title: "Hello — I'm Conecto",
    body:
      "I'll point out anything that needs attention. Click me any time for a note about the page " +
      "you're on, or send me away with the ✕ — I'll stay gone.",
    pose: "idle",
  },
  {
    title: "The dashboard is the morning check",
    body:
      "Blocking soon, unpaid invoices, offline routers, open tickets. If every tile reads zero, " +
      "there's nothing to chase.",
    pose: "look",
    to: "/admin",
  },
  {
    title: "Customers and their references",
    body:
      "The payment reference is the one field that decides whether a customer's EFT matches itself. " +
      "For anyone migrated across, set it to whatever they already use.",
    pose: "think",
    to: "/admin/customers",
  },
  {
    title: "Money in, money out",
    body:
      "Invoices and Payments on one side, Stock receipts and Expenses on the other. Accountant pulls " +
      "both together into the VAT return.",
    pose: "look",
    to: "/admin/accountant",
  },
  {
    title: "The network side",
    body:
      "Routers, IP pools, VPN clients, live sessions, UISP and the network map. RADIUS clients are " +
      "under Configs → RADIUS. Suspending a service reaches the router straight away — worth knowing " +
      "before you click it.",
    pose: "alert",
    to: "/admin/networking",
  },
  {
    title: "Ask me anything about Vexar",
    body:
      "Click me and type what you need — \"how do I cancel a service\", \"why wasn't he cut off\", " +
      "\"import fleetcard\". I answer from written notes about how Vexar works, so I never make " +
      "things up; if I don't know, I'll say so.",
    pose: "think",
  },
];

// ---------------------------------------------------------------------------
// Ask Conecto — a searchable index of how this platform actually behaves.
//
// This is a keyword search over hand-written answers, NOT a language model.
// It cannot invent an answer, which is the point: every reply here is
// something known to be true about this platform. If nothing matches, it says
// so rather than guessing.
//
// `keywords` exist so a question can be phrased naturally: someone typing
// "why isn't his payment showing" should reach the bank-matching answer even
// though it shares no words with the title.
// ---------------------------------------------------------------------------

export interface KbEntry {
  id: string;
  title: string;
  body: string;
  keywords: string[];
  to?: string;
  audience: "staff" | "customer" | "both";
}

export { KB };

// Ranked keyword search. Deliberately simple and predictable: exact keyword
// hits score highest, then title words, then body words. Returns nothing
// rather than a bad guess when the score is zero.
// Words that say nothing about WHAT is being asked. Without this, "how do I"
// in a question scored points against every title that also starts "How".
const STOP = new Set([
  "a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "could", "do", "does", "did", "for",
  "from", "get", "has", "have", "how", "i", "if", "in", "into", "is", "it", "its", "me", "my", "of",
  "on", "or", "our", "should", "so", "that", "the", "their", "them", "there", "they", "this", "to",
  "was", "we", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your",
  "he", "she", "him", "her", "his", "wasn", "isn", "didn", "doesn", "don", "won", "not", "t", "s", "just",
  "please", "need", "want", "help", "about",
]);

const tokenCache = new Map<string, { kw: string[]; title: string[]; body: string[] }>();
function tokens(text: string) {
  return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
}
function tokensOf(e: KbEntry) {
  let t = tokenCache.get(e.id);
  if (!t) {
    t = { kw: tokens(e.keywords.join(" ")), title: tokens(e.title), body: tokens(e.body) };
    tokenCache.set(e.id, t);
  }
  return t;
}
// "suspended" -> "suspend", "emails" -> "email": the query word is matched
// as the START of a word, so trimming the ending finds every form of it.
function stem(w: string) {
  if (w.length > 6 && w.endsWith("ing")) return w.slice(0, -3);
  if (w.length > 5 && w.endsWith("ed")) return w.slice(0, -2);
  if (w.length > 4 && w.endsWith("s") && !w.endsWith("ss")) return w.slice(0, -1);
  return w;
}
// A whole word, or the start of one for longer words ("suspend" finds
// "suspended") -- never the middle of one ("line" must not find "offline").
function hit(list: string[], w: string) {
  return list.some((t) => t === w || (w.length > 3 && t.startsWith(w)));
}

export function searchKb(query: string, audience: "staff" | "customer", limit = 5): KbEntry[] {
  const q = query.toLowerCase().trim();
  if (q.length < 2) return [];
  const words = [...new Set(tokens(q).filter((w) => w.length > 1 && !STOP.has(w)).map(stem))];
  if (!words.length) return [];

  const scored = KB
    .filter((e) => e.audience === audience || e.audience === "both")
    .map((e) => {
      const t = tokensOf(e);
      let score = 0;
      let matched = 0;
      for (const kw of e.keywords) {
        if (kw.includes(" ") && q.includes(kw)) score += 12;   // a whole phrase, e.g. "cut off"
      }
      for (const w of words) {
        let any = false;
        if (hit(t.kw, w)) { score += 5; any = true; }
        if (hit(t.title, w)) { score += 4; any = true; }
        if (hit(t.body, w)) { score += 1; any = true; }
        if (any) matched += 1;
      }
      // Answers that cover MORE of the question rank higher: "cancel
      // service" should beat everything that merely mentions "service".
      score += 4 * matched * matched;
      return { e, score };
    })
    .filter((r) => r.score >= 9)
    .sort((a, b) => b.score - a.score);

  return scored.slice(0, limit).map((r) => r.e);
}
