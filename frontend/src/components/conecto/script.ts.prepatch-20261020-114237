// Everything Conecto says, kept out of the component so the wording can be
// edited without touching behaviour.
//
// The two audiences are deliberately separate. Staff messages quote internal
// figures and internal vocabulary; a customer must never see either. Nothing
// is shared between the two sets — no "clever" reuse — because the failure
// mode is leaking a staff-facing number onto a customer's screen.

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
    body:
      "Every account on the platform. The payment reference here is what customers type on " +
      "their EFT — it's how Bank Feeds matches their money to them, so it's worth getting right.",
  },
  {
    prefix: "/admin/finance",
    title: "Finance",
    body:
      "Invoices, payments and credits. Quotes and pro formas are not tax invoices and carry no " +
      "VAT liability until they become real invoices — only issued invoices count toward Output VAT.",
  },
  {
    prefix: "/admin/inventory",
    title: "Stock / Inventory",
    body:
      "Stock receipts are where equipment invoices belong — they now feed Input VAT on your VAT " +
      "return, so the same invoice should not also be entered under Expenses.",
  },
  {
    prefix: "/admin/accountant",
    title: "Accountant",
    body:
      "Input VAT comes from two places: Expenses for non-stock costs, and stock receipts for " +
      "equipment. If an invoice appears in both, the duplicate warning will tell you.",
  },
  {
    prefix: "/admin/networking",
    title: "Networking",
    body:
      "Routers, RADIUS clients, IP pools and OpenVPN. A suspended PPPoE customer keeps their " +
      "address but gets routed to the walled garden; OVPN sessions are rejected outright. An IP " +
      "pool with no addresses generated hands out nothing — which looks like a RADIUS fault but isn't.",
  },
  {
    prefix: "/admin/scheduling",
    title: "Scheduling",
    body: "Jobs and shifts. The day view scrolls, and the red line is the current time.",
  },
  {
    prefix: "/admin/services",
    title: "Services",
    body:
      "A service is what a customer actually pays for. Suspending one writes through to the " +
      "router immediately via RADIUS — there's no separate 'apply' step.",
  },
  {
    prefix: "/admin/tickets",
    title: "Tickets",
    body: "Support, billing, sales and abuse contacts. Three in a month flags a customer as high alert.",
  },
  {
    prefix: "/admin/vehicles",
    title: "Fleet",
    body: "Vehicles, fuel and driver details. Fuel entries feed the expense side of the accounts.",
  },
  {
    prefix: "/admin/bulk-email",
    title: "Bulk email",
    body:
      "Sends one template to a selected list. Document templates (invoice, quote, pro forma) are " +
      "excluded here — those need a specific document attached, so they go out per customer.",
  },
  {
    prefix: "/admin/configs",
    title: "Configs",
    body:
      "Platform settings, permissions, users and RADIUS. Auto-suspension lives here — it's the " +
      "master switch that decides whether overdue customers actually get cut off.",
  },
  {
    prefix: "/admin/account",
    title: "Your account",
    body: "Your own login, password and two-factor settings.",
  },
  {
    prefix: "/admin",
    title: "Dashboard",
    body: "Where things stand right now. Any tile showing a number above zero is worth a look.",
  },
];

export const CUSTOMER_HELP: { prefix: string; title: string; body: string }[] = [
  {
    prefix: "/portal/invoices",
    title: "Your invoices",
    body: "Everything billed to you, and what's still outstanding. Use your payment reference on any EFT.",
  },
  {
    prefix: "/portal/tickets",
    title: "Support",
    body: "Log anything that isn't working and we'll pick it up. The more detail, the faster we can help.",
  },
  {
    prefix: "/portal",
    title: "Your account",
    body:
      "Your account at a glance — how much data you've used this month, what you owe, and how to " +
      "reach us. Speeds shown are live from your connection.",
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
      "Blocking tomorrow, unpaid invoices, offline routers, open tickets. If every tile reads zero, " +
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
      "Routers, RADIUS and IP pools. Suspending a service here reaches the router straight away — " +
      "worth knowing before you click it.",
    pose: "alert",
    to: "/admin/networking",
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

export const KB: KbEntry[] = [
  {
    id: "ref-matching",
    title: "A customer's payment isn't matching in Bank Feeds",
    body:
      "Matching looks for the customer's payment reference inside the bank description. If they use " +
      "an old reference from a previous system, set their reference to that — it's editable on the " +
      "customer record. References under 4 characters (or under 6 if all digits) are never " +
      "auto-matched, because short numbers collide with amounts and dates in bank narrations.",
    keywords: ["payment", "not matching", "unmatched", "bank", "feed", "reference", "eft",
               "deposit", "money", "allocate", "statement", "splynx"],
    to: "/admin/customers",
    audience: "staff",
  },
  {
    id: "change-ref",
    title: "Change a customer's payment reference",
    body:
      "Open the customer, Edit, and change Payment reference. Leave it blank on a new customer and " +
      "the next CUS-###### is generated. Changing it on an existing customer means payments still " +
      "arriving with the old reference stop matching automatically.",
    keywords: ["reference", "change", "edit", "customer id", "account number", "cus"],
    to: "/admin/customers",
    audience: "staff",
  },
  {
    id: "vat-stock",
    title: "VAT on equipment I bought",
    body:
      "Enter the supplier invoice as a stock receipt in Stock/Inventory — not under Expenses. Tick " +
      "'unit costs include VAT' if the invoice quotes inclusive prices and type them exactly as " +
      "printed. Stock receipts feed Input VAT on the VAT return, so entering the same invoice in " +
      "both places would claim the VAT twice (the return warns you if that happens).",
    keywords: ["vat", "input vat", "equipment", "stock", "receipt", "supplier invoice", "claim",
               "inclusive", "exclusive", "sars", "router purchase"],
    to: "/admin/inventory",
    audience: "staff",
  },
  {
    id: "vat-return",
    title: "Where the VAT return numbers come from",
    body:
      "Output VAT comes from invoices actually issued in the period (not payments received). Input " +
      "VAT comes from two places: Expenses for non-stock costs like rent and bandwidth, and stock " +
      "receipts for equipment. Credit notes are shown for information only — they aren't netted " +
      "off automatically, because credit requests don't record a VAT rate of their own.",
    keywords: ["vat", "return", "vat201", "output", "input", "sars", "period", "accountant"],
    to: "/admin/accountant",
    audience: "staff",
  },
  {
    id: "supplier-vat",
    title: "A supplier isn't registered for VAT",
    body:
      "Open the supplier in Stock/Inventory → Suppliers and untick 'Registered for VAT'. Their " +
      "receipt lines then default to 0% and no Input VAT is claimed. Getting this wrong claims VAT " +
      "you were never charged, which is the direction SARS penalises.",
    keywords: ["supplier", "vat", "registered", "cash", "not registered", "zero rated"],
    to: "/admin/inventory",
    audience: "staff",
  },
  {
    id: "blocking",
    title: "Why a customer is about to be cut off",
    body:
      "Blocking needs BOTH an invoice overdue longer than their blocking period AND a balance worse " +
      "than their minimum balance — the minimum balance acts as a credit cushion that can excuse a " +
      "technically-overdue invoice. Nothing is ever suspended while Auto-suspension is off " +
      "(Configs → Billing), whatever an individual customer's settings say.",
    keywords: ["block", "blocking", "cut off", "suspend", "suspension", "overdue", "disconnect",
               "tomorrow", "minimum balance", "why"],
    to: "/admin?panel=blocks",
    audience: "staff",
  },
  {
    id: "suspend-effect",
    title: "What happens when I suspend a service",
    body:
      "It reaches the router immediately through RADIUS — there's no separate apply step. A PPPoE " +
      "customer keeps an address but is routed to the walled garden; an OpenVPN session is rejected " +
      "outright. Un-suspending reverses it the same way.",
    keywords: ["suspend", "walled garden", "pppoe", "ovpn", "openvpn", "radius", "disconnect",
               "unsuspend", "service"],
    to: "/admin/services",
    audience: "staff",
  },
  {
    id: "ip-pool",
    title: "A customer connects but gets no IP address",
    body:
      "Check the IP pool has had its addresses generated — a pool with none configured hands out " +
      "nothing, so RADIUS returns Access-Accept with no Framed-IP-Address. It looks like a RADIUS " +
      "fault but isn't. Also check the pool's category matches what you're allocating: allocation " +
      "reads the category, not the pool's name.",
    keywords: ["ip", "pool", "address", "no ip", "framed", "dhcp", "allocation", "empty"],
    to: "/admin/networking",
    audience: "staff",
  },
  {
    id: "radius-silent",
    title: "RADIUS isn't authenticating anyone",
    body:
      "Both an unknown client IP and a wrong shared secret cause a SILENT discard — no error, no " +
      "log. Work through it in this order: firewall, then the client's IP in the RADIUS client " +
      "list, then the shared secret. An empty log is itself the diagnosis: the packet never " +
      "arrived. Note that reloading FreeRADIUS does not re-read clients — it needs a restart.",
    keywords: ["radius", "auth", "not working", "reject", "silent", "secret", "nas", "freeradius",
               "no log", "cannot connect"],
    to: "/admin/configs",
    audience: "staff",
  },
  {
    id: "live-usage",
    title: "Live speed vs data used",
    body:
      "They come from different places. Data totals come from RADIUS accounting and are " +
      "authoritative for billing. Live speed comes from polling the router, which is accurate to " +
      "the second — a router's counters reset every session, so they can measure speed but never " +
      "consumption. If a speed reads as an average it's because the router poll wasn't available.",
    keywords: ["usage", "speed", "live", "bandwidth", "mbps", "data", "accounting", "traffic",
               "how much"],
    audience: "staff",
  },
  {
    id: "usage-link",
    title: "Send a customer a link to check their own usage",
    body:
      "On the customer's record, copy their usage link. It needs no login, so treat it like a " +
      "password — anyone holding it sees that line's usage and nothing else. 'Issue new link' " +
      "revokes the old one immediately.",
    keywords: ["usage link", "share", "customer link", "no login", "revoke", "send"],
    to: "/admin/customers",
    audience: "staff",
  },
  {
    id: "view-as",
    title: "See what a customer sees",
    body:
      "On the customer's record, use the option to view their portal. You keep your own login the " +
      "whole time — no customer password is involved and nothing is changed on their account.",
    keywords: ["view as", "impersonate", "customer portal", "what they see", "login as"],
    to: "/admin/customers",
    audience: "staff",
  },
  {
    id: "quote-vs-invoice",
    title: "Quotes, pro formas and invoices",
    body:
      "A quote or pro forma is not a tax invoice and carries no VAT liability. Only a real issued " +
      "invoice counts toward Output VAT, and only a real invoice activates the tariff services on " +
      "it. Converting a pro forma to an invoice is what makes both of those happen.",
    keywords: ["quote", "proforma", "pro forma", "invoice", "difference", "tax invoice", "vat"],
    to: "/admin/finance",
    audience: "staff",
  },
  {
    id: "permissions",
    title: "A staff member can't see a section",
    body:
      "Access is per section, set under Configs → Permissions. Separately, a staff member can be " +
      "restricted to particular partners — they then only see customers belonging to those " +
      "partners, including inside dashboard totals.",
    keywords: ["permission", "access", "section", "cannot see", "hidden", "role", "staff",
               "partner", "restrict"],
    to: "/admin/configs",
    audience: "staff",
  },
  {
    id: "backups",
    title: "Backups",
    body:
      "The database is dumped hourly and daily on the server. Two known gaps worth remembering: " +
      "every copy currently sits on the same machine, and uploaded files (expense receipts and " +
      "attachments) are not included — only the database.",
    keywords: ["backup", "restore", "dump", "disaster", "lost data", "recovery"],
    audience: "staff",
  },
  {
    id: "data-cap",
    title: "How much data have I used?",
    body:
      "Your usage page shows what you've used this month, and your current download and upload " +
      "speed. Monthly totals come from your connection's own reporting, so they can lag a few " +
      "minutes behind.",
    keywords: ["data", "usage", "cap", "used", "how much", "gb", "speed", "slow", "limit"],
    to: "/portal",
    audience: "customer",
  },
  {
    id: "pay-invoice",
    title: "How do I pay?",
    body:
      "Your invoices page lists what's outstanding. Use your payment reference on the EFT — it's " +
      "what matches your payment to your account. Without it, allocating your payment takes longer.",
    keywords: ["pay", "payment", "invoice", "reference", "eft", "bank", "owe", "bill", "account"],
    to: "/portal/invoices",
    audience: "customer",
  },
  {
    id: "internet-down",
    title: "My internet isn't working",
    body:
      "Check your usage page first — if it shows you as connected, the problem is likely inside " +
      "your home network rather than the line. If it shows you as not connected, log a support " +
      "ticket and we'll pick it up.",
    keywords: ["not working", "down", "offline", "no internet", "slow", "broken", "help",
               "disconnected"],
    to: "/portal/tickets",
    audience: "customer",
  },
];

// Ranked keyword search. Deliberately simple and predictable: exact keyword
// hits score highest, then title words, then body words. Returns nothing
// rather than a bad guess when the score is zero.
export function searchKb(query: string, audience: "staff" | "customer"): KbEntry[] {
  const q = query.toLowerCase().trim();
  if (q.length < 2) return [];
  const words = q.split(/\s+/).filter((w) => w.length > 1);
  if (!words.length) return [];

  const scored = KB
    .filter((e) => e.audience === audience || e.audience === "both")
    .map((e) => {
      let score = 0;
      const title = e.title.toLowerCase();
      const body = e.body.toLowerCase();
      for (const kw of e.keywords) {
        if (q.includes(kw)) score += 12;                      // whole phrase present
        else if (words.some((w) => kw.includes(w) && w.length > 2)) score += 5;
      }
      for (const w of words) {
        if (title.includes(w)) score += 4;
        if (body.includes(w)) score += 1;
      }
      return { e, score };
    })
    .filter((r) => r.score >= 5)
    .sort((a, b) => b.score - a.score);

  return scored.slice(0, 3).map((r) => r.e);
}
