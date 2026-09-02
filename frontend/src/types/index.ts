export type Role = "admin" | "support" | "sales" | "technician" | "management" | "accounts" | "customer";

// Roles an admin can assign when creating/editing a staff account via
// Staff -> Users -- every Role except "customer" (those sign up/are
// created through the customer portal flow, not this admin tool). Mirrors
// the backend's accounts.models.STAFF_ROLES.
export const STAFF_ROLES: Exclude<Role, "customer">[] = [
  "admin",
  "support",
  "sales",
  "technician",
  "management",
  "accounts",
];

export const ROLE_LABELS: Record<Role, string> = {
  admin: "Administrator",
  support: "Support",
  sales: "Sales",
  technician: "Technician",
  management: "Management",
  accounts: "Accounts",
  customer: "Customer",
};

// One entry per sidebar tab that can be individually granted/denied per
// staff member -- kept in sync with the backend's User.Section choices
// and components/AdminLayout.tsx's NAV entries.
export type Section =
  | "scheduling"
  | "sales"
  | "customers"
  | "services"
  | "finance"
  | "inventory"
  | "networking"
  | "tickets"
  | "vehicles"
  | "bulk_email"
  | "configs"
  | "accountant";

export const SECTION_LABELS: Record<Section, string> = {
  scheduling: "Scheduling",
  // Leads and the pipeline. Its own gate rather than part of Customers: a
  // rep working enquiries has no reason to see every existing customer's
  // billing, and the support desk has no reason to see the pipeline.
  sales: "Sales / Leads",
  customers: "Customers",
  // Sits directly under Customers in the sidebar, so it sits directly under
  // it on the Configs -> Permissions checklist too. It remains its own
  // independent gate -- granting Customers does not grant Tickets, and
  // granting Tickets alone still shows the Tickets link.
  tickets: "Support Tickets",
  services: "Services",
  finance: "Finance",
  inventory: "Stock / Inventory",
  networking: "Networking",
  vehicles: "Vehicles",
  bulk_email: "Bulk Email",
  configs: "Configs",
  accountant: "Accountant",
};

export interface User {
  id: number;
  username: string;
  email: string;
  first_name: string;
  last_name: string;
  role: Role;
  phone: string;
  is_active: boolean;
  customer_id?: number;
  // Empty = unrestricted (sees every section) -- the default for every
  // account until an admin deliberately narrows it via Configs →
  // Permissions. Always empty in practice for Admin, who bypasses this
  // check entirely on the backend regardless of its contents.
  allowed_sections: Section[];
  // Reseller-partner visibility restriction, set by Management/Admin under
  // Staff -> Partners. Empty = unrestricted (sees every partner's
  // customers, plus direct/no-partner ones). Read-only from this type --
  // only ever written via /staff-permissions/.
  allowed_partners: number[];
  // This staff member's own personal narrowing of the above -- which of
  // their *allowed* partners they currently want shown by default on the
  // Customers page. Empty = show all of whichever partners they're
  // allowed to see. Self-service: PATCH /me/ with { visible_partners }.
  visible_partners: number[];
}

// Mirrors the backend's accounts.serializers.StaffPermissionsSerializer --
// the shape returned/accepted by GET/PATCH /api/staff-permissions/. This is
// deliberately a separate (slimmer) type from User: it's what the
// Configs -> Permissions admin UI lists and edits, one row per staff member.
export interface StaffPermissionEntry {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  role: Role;
  is_active: boolean;
  allowed_sections: Section[];
  allowed_partners: number[];
}

// Mirrors the backend's customers.serializers.PartnerSerializer -- reseller
// partners a Customer can be tagged to. Managed under Staff -> Partners.
export interface Partner {
  id: number;
  name: string;
  contact_person: string;
  email: string;
  phone: string;
  commission_rate: string | null;
  notes: string;
  is_active: boolean;
  created_at: string;
  customer_count: number;
}

// Mirrors the backend's accounts.serializers.StaffAccountsSerializer --
// the shape returned by GET/POST/PATCH /api/staff-accounts/. This is what
// the Staff -> Users admin UI lists, creates, edits, suspends and deletes.
// `password` is write-only on the backend and never appears in a response.
export interface StaffAccountEntry {
  id: number;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  phone: string;
  role: Role;
  is_active: boolean;
}

export interface Paginated<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Customer {
  id: number;
  // The customer's payment reference — printed on their statement, and what
  // bank-feed matching looks for in a transaction description. Writable (it
  // used to be read-only) so a customer migrated from another system can keep
  // the reference they already use on their EFTs. Send it blank on create and
  // the backend generates the next CUS-######; blank on update means
  // "unchanged", never "clear it".
  customer_id: string;
  customer_type: "individual" | "company";
  category: "residential" | "business";
  full_name: string;
  company_name: string;
  email: string;
  phone: string;
  address: string;
  city: string;
  zip_code: string;
  // Printed on the customer's side of a tax invoice. Both optional — a
  // residential customer usually has neither, and the invoice prints the
  // labels with nothing after them rather than hiding the rows, which is
  // what the document they're replacing did.
  id_number: string;
  vat_number: string;
  status: "new" | "active" | "suspended" | "bad_debt" | "inactive";
  balance: string;
  // Every public address this customer's non-terminated services are handing
  // out. Read-only, derived from the services — a list because a customer can
  // hold more than one line, and empty for a customer with no PPPoE service.
  public_ips: string[];
  // Whether this customer's own usage link shows the LIVE bandwidth graph.
  // Off by default and staff-controlled: that graph holds a connection open to
  // their router while it is watched, and the usage link needs no login.
  live_bandwidth_public: boolean;
  assigned_staff: number | null;
  assigned_staff_name: string | null;
  partner: number | null;
  partner_name: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
  user: number | null;
  // Unguessable key for the no-login "check your usage" page. Read-only --
  // changed only via POST /customers/<id>/regenerate-usage-link/, which
  // revokes the old link.
  usage_token: string;
}

// GET /customers/<id>/usage/ -- month-to-date totals plus live throughput,
// derived from RADIUS accounting. No router is contacted (see
// radiusauth/usage.py). The public customer page at /usage/<token> returns
// the same shape minus customer_id and the RADIUS usernames.
export interface CustomerUsageLiveSession {
  username?: string;
  started_at: string | null;
  ip_address: string | null;
  mac_address: string | null;
  download_bytes: number;
  upload_bytes: number;
  download_bps: number;
  upload_bps: number;
  rate_measured_at: string | null;
  // "router" = polled live from the router, accurate to the second.
  // "accounting" = derived from RADIUS counters, an average over the NAS's
  // reporting interval, so short bursts read low. null = neither source has
  // a reading yet.
  rate_source?: "router" | "accounting" | null;
}

export interface CustomerUsage {
  customer_id?: number;
  customer_name: string;
  period: { year: number; month: number; start: string; end: string };
  cap_bytes: number | null;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  sessions: number;
  live_sessions: CustomerUsageLiveSession[];
  // Present only when ?period= was asked for. The month-to-date fields above
  // come from RADIUS accounting and cover history from before per-hour
  // accumulation started, so they are kept rather than replaced.
  series?: UsageSeries;
  // When accumulation actually began. null if it never has.
  measuring_since?: string | null;
}

export type UsagePeriod = "day" | "week" | "month" | "year";

export interface UsageSeriesPoint {
  at: string;
  label: string;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
}

export interface UsageSeries {
  period: UsagePeriod;
  period_label: string;
  interval: "hour" | "day" | "month";
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  points: UsageSeriesPoint[];
}

// GET /offline-customers/ -- a support call list. See radiusauth/offline.py
// for who is deliberately left out of it.
export interface OfflineCustomerRow {
  customer: number;
  customer_ref: string;
  full_name: string;
  phone: string;
  email: string;
  username: string;
  last_seen: string;
  offline_seconds: number;
  last_ip: string | null;
  terminate_cause: string;
  terminate_reason: string;
  // false when the session simply stopped reporting -- no Accounting-Stop
  // ever arrived, which usually means power or a dead CPE.
  clean_disconnect: boolean;
  drops_in_period: number;
}

export interface OfflineCustomers {
  hours: number;
  count: number;
  results: OfflineCustomerRow[];
}

// GET /usage-report/ -- every customer's total for one period, heaviest first.
export interface UsageReportRow {
  customer: number;
  customer_ref: string;
  full_name: string;
  download_bytes: number;
  upload_bytes: number;
  total_bytes: number;
  cap_bytes: number | null;
  cap_used_pct: number | null;
}

export interface UsageReport {
  period: UsagePeriod;
  period_label: string;
  start: string;
  end: string;
  measuring_since: string | null;
  results: UsageReportRow[];
}

export interface Tariff {
  id: number;
  name: string;
  service_type: "internet" | "voice" | "bundle" | "other";
  price: string;
  billing_period: "monthly" | "quarterly" | "annually";
  // Kbps, matching a ConnectionRule's speed_down_kbps/speed_up_kbps so there
  // is one speed unit across the platform. 1 Mbps is 1024, so a 4 Mbps plan
  // is 4096. These were named *_mbps and labelled Mbps while holding Kbps
  // values, which meant the RADIUS rate limit was emitted as "4096M" — four
  // terabits, i.e. no throttle at all.
  speed_download_kbps: number | null;
  speed_upload_kbps: number | null;
  data_cap_gb: number | null;
  // Fair use — deliberately NOT the same thing as data_cap_gb above. A
  // cap is a bundle somebody bought and can run out of; this is a
  // threshold on an uncapped plan past which the line is slowed. null =
  // no fair-use shaping at all.
  fup_threshold_gb: number | null;
  fup_speed_pct: number;
  tax_rate_pct: string;
  is_active: boolean;
  description: string;
  // Read-only, annotated in SQL by TariffViewSet. How many services sit on
  // this plan — shown when editing, because the price and the speed both
  // reach every one of them.
  service_count: number;
  active_service_count: number;
}

export type ServiceConnectionType = "ovpn" | "pppoe";
export type IPAssignmentMode = "manual" | "pool" | "auto";

export const CONNECTION_TYPE_LABELS: Record<ServiceConnectionType, string> = {
  ovpn: "OVPN",
  pppoe: "PPPoE",
};

export const IP_ASSIGNMENT_MODE_LABELS: Record<IPAssignmentMode, string> = {
  manual: "Manual (type a static public IP)",
  pool: "Select from Customer IP Pool",
  auto: "Automatically assign from Customer IP Pool",
};

export interface Service {
  id: number;
  customer: number;
  customer_name: string;
  tariff: number;
  tariff_name: string;
  // A tariff change booked for a future date. Both are set together or
  // neither is -- the API rejects half of one, since a tariff with no date
  // would never apply and a date with no tariff looks scheduled but isn't.
  pending_tariff: number | null;
  pending_tariff_name: string | null;
  pending_tariff_price: string | null;
  pending_tariff_date: string | null;
  price: string;
  status: "active" | "suspended" | "terminated" | "pending";
  device: number | null;
  // Where the client PHYSICALLY connects -- the AP/sector, OLT or switch.
  // Deliberately not the same as `device` above, which is the NAS the line
  // terminates on and the box every RADIUS/shaper/blocking action is pushed
  // to. Nothing is enforced against these two fields; they document the
  // path so an outage on one device can name the customers behind it.
  access_device: number | null;
  access_device_name: string | null;
  access_device_type: string | null;
  access_site_name: string | null;
  access_detail: string;
  // Fair-use overrides for this one line. null = use the tariff's, so a
  // plan's policy stays in one place and an exception doesn't need a new
  // plan invented for one customer. 0 is a real threshold meaning "shape
  // from the first byte", which is why these are nullable not 0-defaulted.
  fup_threshold_gb: number | null;
  fup_speed_pct: number | null;
  fup_exempt: boolean;
  start_date: string | null;
  end_date: string | null;
  radius_username: string | null;
  radius_password_set: boolean;
  // PPPoE IP assignment -- ignored/irrelevant while radius_connection_type
  // is "ovpn" (the default, unchanged legacy behavior).
  radius_connection_type: ServiceConnectionType;
  ip_assignment_mode: IPAssignmentMode;
  static_ip: string | null;
  ip_pool: number | null;
  ip_pool_name: string | null;
  // Read-only: the IP this service is actually handing out right now, if any.
  assigned_ip: string | null;
  // Live-API shaper override -- see network.models.ConnectionRule. Must
  // belong to the same device as this service (enforced server-side).
  connection_rule: number | null;
  connection_rule_name: string | null;
  // What happened the last time a change to this line was pushed to the
  // router. null when nothing has been attempted yet.
  last_radius_action: {
    ok: boolean;
    action: "coa_rate" | "disconnect" | "none";
    transport: "coa" | "api" | "none";
    detail: string;
    at: string;
  } | null;
}

// Response shape of GET /services/{id}/live-bandwidth/ -- see
// network.mikrotik.get_service_live_bandwidth.
export interface ServiceLiveBandwidth {
  download_mbps: number;
  upload_mbps: number;
}

export type InvoiceItemType = "custom" | "product" | "tariff";

export interface InvoiceItem {
  id: number;
  invoice: number;
  service: number | null;
  item_type: InvoiceItemType;
  product: number | null;
  product_name: string | null;
  tariff: number | null;
  tariff_name: string | null;
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
  // Only meaningful for item_type "tariff" -- the contract/service period
  // quoted. Set on the item once activated into a real Service, see
  // Invoice.activate_tariff_services() on the backend.
  period_start: string | null;
  period_end: string | null;
  total: string;
}

export type InvoiceStatus = "quote" | "proforma" | "draft" | "unpaid" | "paid" | "overdue" | "cancelled";

export interface Invoice {
  id: number;
  number: string;
  customer: number;
  customer_name: string;
  status: InvoiceStatus;
  date_created: string;
  date_due: string;
  subtotal: string;
  tax_total: string;
  total: string;
  paid_amount: string;
  balance_due: string;
  note: string;
  items: InvoiceItem[];
  // Whether the "Convert to pro forma" / "Convert to invoice" actions are
  // currently allowed for this record -- see billing/models.py's
  // Invoice.can_convert_to_proforma()/can_convert_to_invoice().
  can_convert_to_proforma: boolean;
  can_convert_to_invoice: boolean;
}

export interface Payment {
  id: number;
  customer: number;
  customer_name: string;
  invoice: number | null;
  amount: string;
  method: "cash" | "card" | "bank_transfer" | "mobile_money" | "manual";
  date: string;
  note: string;
  received_by: number | null;
  received_by_name: string | null;
}

export type CreditStatus = "pending" | "approved" | "rejected";

export interface CreditRequest {
  id: number;
  customer: number;
  customer_name: string;
  amount: string;
  reason: string;
  status: CreditStatus;
  requested_by: number | null;
  requested_by_name: string | null;
  decided_by: number | null;
  decided_by_name: string | null;
  decision_note: string;
  decided_at: string | null;
  created_at: string;
}

export type InvoiceDeletionStatus = "pending" | "approved" | "rejected";

export interface InvoiceDeletionRequest {
  id: number;
  // Null once approved -- the quote/pro forma itself has been deleted by
  // then; invoice_number/invoice_status/customer_name still read from the
  // snapshotted display fields.
  invoice: number | null;
  invoice_number: string | null;
  invoice_status: InvoiceStatus | null;
  customer_name: string | null;
  reason: string;
  status: InvoiceDeletionStatus;
  requested_by: number | null;
  requested_by_name: string | null;
  decided_by: number | null;
  decided_by_name: string | null;
  decision_note: string;
  decided_at: string | null;
  created_at: string;
}

// Mirrors billing.models.PaymentMethod -- a named payment convention (e.g.
// "EFT 1st", "Cash") tagged onto a customer's billing config for
// reference; doesn't drive any automated timing on its own.
export interface PaymentMethod {
  id: number;
  name: string;
  is_active: boolean;
}

export type RecurringPaymentPeriod = "monthly" | "quarterly" | "biannually" | "annually";
export type ProformaTarget = "current_month" | "next_month";

// Fields shared by BillingDefaultsConfig (the org-wide template) and
// CustomerBillingConfig (one customer's actual settings) -- mirrors the
// backend's RecurringBillingFieldsMixin / _RECURRING_BILLING_FIELDS.
export interface RecurringBillingFields {
  payment_period: RecurringPaymentPeriod;
  payment_method: number | null;
  payment_method_name: string | null;
  billing_day: number;
  use_date_of_customer_creation: boolean;
  payment_due_days: number | null;
  blocking_period_days: number | null;
  // Configurable, but not yet wired to any action -- reserved for later.
  deactivation_period_days: number | null;
  minimum_balance: string;
  auto_create_invoices: boolean;
  send_billing_notifications: boolean;
  auto_proforma_enabled: boolean;
  proforma_day: number | null;
  proforma_payment_period: RecurringPaymentPeriod | "";
  create_proforma_for: ProformaTarget;
  reminder_enabled: boolean;
  reminder_1_day: number | null;
  reminder_2_day: number | null;
  reminder_3_day: number | null;
}

// Mirrors GET/PATCH /api/billing-defaults/ -- the org-wide recurring
// billing template. Deliberately has no billing_enabled field (see the
// backend model's docstring) -- that's always a per-customer decision.
export interface BillingDefaultsConfig extends RecurringBillingFields {
  vat_number: string;
  // Company identity and banking, as printed on a tax invoice. A valid tax
  // invoice has to carry the supplier's registered name, address and VAT
  // number, so these are not decoration.
  company_legal_name: string;
  company_address: string;
  company_city: string;
  company_postal_code: string;
  company_country: string;
  company_phone: string;
  company_email: string;
  bank_name: string;
  bank_account_number: string;
  bank_branch_code: string;
  // The logo is WRITTEN as a file (multipart PATCH, field name `logo`) and
  // READ back as these two. logo_url is a signed, short-lived link because
  // /media/ rejects unsigned requests — so it is fine in an <img> right
  // after a load, and expires a few minutes later. Re-fetch to refresh.
  logo_url: string | null;
  logo_name: string | null;
  updated_at: string;
}

// Mirrors GET/PATCH /api/customer-billing-config/{customer_id}/.
export interface CustomerBillingConfig extends RecurringBillingFields {
  id: number;
  customer: number;
  billing_enabled: boolean;
  billing_name: string;
  billing_street: string;
  billing_zip: string;
  billing_city: string;
  next_billing_date: string | null;
  created_at: string;
  updated_at: string;
}

// Mirrors GET/PATCH /api/reminder-settings/ -- global kill-switches; a
// customer's own reminder_N_day only fires if the matching switch here is
// also on.
export interface ReminderSettingsConfig {
  static_days: boolean;
  reminder_1_enabled: boolean;
  reminder_2_enabled: boolean;
  reminder_3_enabled: boolean;
  updated_at: string;
}

// Mirrors GET/PATCH /api/suspension-settings/ -- the platform-wide
// auto-suspension master switch. A customer's own blocking_period_days
// only actually suspends anyone if this is also on.
export interface SuspensionSettingsConfig {
  auto_suspend_enabled: boolean;
  updated_at: string;
}

export type RecurringBillingRunStatus = "processed" | "failed";

// Mirrors GET /api/recurring-billing-runs/ -- one row per committed Run
// (never a Preview) of the recurring-billing engine.
export interface RecurringBillingRun {
  id: number;
  run_date: string;
  created_at: string;
  status: RecurringBillingRunStatus;
  status_message: string;
  partners: number[];
  partner_names: string;
  invoices_created_count: number;
  proforma_invoices_created_count: number;
  reminders_sent_count: number;
  suspensions_applied_count: number;
  triggered_by: number | null;
  triggered_by_name: string | null;
}

// Counts returned by both POST /recurring-billing/preview/ and
// /recurring-billing/run/ (the latter nested inside the full
// RecurringBillingRun on a Run).
export interface RecurringBillingCounts {
  invoices_created: number;
  proforma_invoices_created: number;
  reminders_sent: number;
  suspensions_applied: number;
}

export type CustomerDeletionStatus = "pending" | "approved" | "rejected";

export interface CustomerDeletionRequest {
  id: number;
  // Null once approved -- the customer itself has been deleted by then;
  // customer_name still reads from the snapshotted display fields.
  customer: number | null;
  customer_name: string | null;
  reason: string;
  status: CustomerDeletionStatus;
  requested_by: number | null;
  requested_by_name: string | null;
  decided_by: number | null;
  decided_by_name: string | null;
  decision_note: string;
  decided_at: string | null;
  created_at: string;
}

export interface Device {
  id: number;
  name: string;
  device_type: "router" | "switch" | "olt" | "access_point" | "server" | "onu";
  ip_address: string;
  location: string;
  site: number | null;
  site_name: string | null;
  // Live customer lines that connect THROUGH this box
  // (Service.access_device). Read-only.
  access_service_count: number;
  vendor: string;
  model_name: string;
  status: "online" | "offline" | "unknown";
  snmp_community: string;
  snmp_version: string;
  latest_reading: MonitoringReading | null;
  api_enabled: boolean;
  api_port: number;
  api_username: string;
  api_password_set: boolean;
  api_use_ssl: boolean;
  api_wan_interface: string;
  // Partners whose staff can see this device -- empty = visible to
  // everyone with Networking access (see network.models.Device docstring).
  visible_partners: number[];
  visible_partner_names: string[];
  // --- Live-API config push (blocking / shaper / wireless) --------------
  block_disabled_customers: boolean;
  enable_shaper: boolean;
  shaping_type: string;
  enable_wireless_access_list: boolean;
  enable_mpsk: boolean;
  wireless_interface: string;
}

// One row from GET /devices/{id}/wireless-access-list/ -- a raw RouterOS
// /interface/wireless/access-list entry (passed through largely as-is).
export interface WirelessAccessListEntry {
  ".id": string;
  interface?: string;
  "mac-address"?: string;
  comment?: string;
  "private-passphrase"?: string;
  [key: string]: string | undefined;
}

// Mirrors network.serializers.NetworkSiteSerializer -- a physical
// tower/site location hardware (Device rows) can be mounted at.
export interface NetworkSite {
  id: number;
  title: string;
  contact_person: string;
  phone: string;
  address: string;
  location: string;
  // Empty means ALL partners, the same convention as a router's
  // visible_partners and a staff account's allowed_partners.
  partners: number[];
  partner_names: string[];
  notes: string;
  created_at: string;
  hardware_count: number;
}

// Mirrors network.serializers.ConnectionRuleSerializer -- a named
// speed-shaping profile scoped to one router.
export interface ConnectionRule {
  id: number;
  device: number;
  title: string;
  speed_down_kbps: number;
  speed_up_kbps: number;
  guaranteed_pct: number;
  created_at: string;
}

export interface MikrotikTestConnectionResult {
  identity: string | null;
  routeros_version: string | null;
  board_name: string | null;
  uptime: string | null;
  cpu_load_pct: number | null;
  free_memory: number | null;
  total_memory: number | null;
}

export interface MikrotikPppSession {
  ".id": string;
  name?: string;
  address?: string;
  uptime?: string;
  "caller-id"?: string;
  service?: string;
  [key: string]: string | undefined;
}

export type PoolCategory = "customer" | "network" | "walled_garden";

export const POOL_CATEGORY_LABELS: Record<PoolCategory, string> = {
  customer: "Customer IP Pool",
  network: "Net IP Pool",
  walled_garden: "Walled Garden (no internet)",
};

export type NetworkType = "endnet" | "rootnet";

export const NETWORK_TYPE_LABELS: Record<NetworkType, string> = {
  endnet: "EndNet",
  rootnet: "RootNet",
};

export interface IPPool {
  id: number;
  name: string;
  network_cidr: string;
  gateway: string | null;
  pool_type: "ipv4" | "ipv6";
  category: PoolCategory;
  // Purely organizational grouping (Splynx's RootNet/EndNet concept) --
  // doesn't affect PPPoE/OVPN IP assignment, which only ever reads
  // `category` above.
  network_type: NetworkType;
  root_net: number | null;
  root_net_name: string | null;
  description: string;
  free_count: number;
  total_count: number;
  used_pct: number;
}

export interface IPAddress {
  id: number;
  pool: number;
  address: string;
  status: "free" | "assigned" | "reserved";
  assigned_service: number | null;
}

export interface MonitoringReading {
  id?: number;
  device?: number;
  timestamp: string;
  is_up: boolean;
  latency_ms: number | null;
  packet_loss_pct: number | null;
  bandwidth_in_mbps: number | null;
  bandwidth_out_mbps: number | null;
  cpu_pct: number | null;
  memory_pct: number | null;
}

export interface RadiusNasClient {
  id: number;
  name: string;
  ip_address: string;
  shortname: string;
  secret_set: boolean;
  // Site/region tag for reporting only -- this platform runs a single
  // FreeRADIUS server, so this does not change live authentication
  // routing (see the backend model's docstring).
  realm: string;
  description: string;
  is_active: boolean;
  created_at: string;
}

// One entry per RadiusNasClient, from GET /radius-nas-clients/ping-status/
// -- a live ICMP reachability check run right when that endpoint is
// called, not a persisted field on RadiusNasClient itself. "online"
// means the NAS host answered a ping just now; it does not confirm
// FreeRADIUS or RADIUS auth itself is working -- see the backend
// pingcheck.py docstring.
export interface RadiusNasClientPingStatus {
  id: number;
  ip_address: string;
  status: "online" | "offline";
}

// An outbound OpenVPN *client* tunnel this platform's own VPS dials out
// on -- modeled on Splynx's own Config -> Tools -> VPN -> OpenVPN page.
// Config-only record; the actual OS-level OpenVPN client process runs on
// the VPS host, not here -- see the backend model's docstring and the
// "config" download action.
export interface OvpnClientConnection {
  id: number;
  name: string;
  comment: string;
  remote_ip: string;
  remote_port: number;
  username: string;
  password_set: boolean;
  routes: string;
  is_enabled: boolean;
  created_at: string;
  updated_at: string;
}

// From GET /ovpn-client-connections/ping-status/ -- live ping to
// remote_ip, same caveats as RadiusNasClientPingStatus (network
// reachability only, not confirmation the tunnel itself is up).
export interface OvpnClientConnectionPingStatus {
  id: number;
  remote_ip: string;
  status: "online" | "offline";
}

export interface RadAcctSession {
  // Real elapsed time. acctsessiontime only moves on an interim update, so on
  // a 5-minute interim a live session reports 0 for its first five minutes.
  duration_seconds: number | null;
  // Byte counters read from the router itself when a live reader is running --
  // current to the second, where the accounting ones lag by the interim
  // interval. null when there is no fresh router reading.
  live_input_octets: number | null;
  live_output_octets: number | null;
  radacctid: number;
  username: string | null;
  nasipaddress: string;
  // Resolved server-side from RadiusNasClient.realm by matching nasipaddress
  // -- null if this NAS isn't tagged with a realm.
  realm: string | null;
  framedipaddress: string | null;
  callingstationid: string | null;
  acctstarttime: string | null;
  acctupdatetime: string | null;
  acctstoptime: string | null;
  acctsessiontime: number | null;
  acctinputoctets: number | null;
  acctoutputoctets: number | null;
  acctterminatecause: string | null;
  is_active: boolean;
}

export interface TicketComment {
  id: number;
  ticket: number;
  author: number | null;
  author_name: string | null;
  message: string;
  is_internal: boolean;
  created_at: string;
}

export interface Ticket {
  id: number;
  ticket_number: string;
  customer: number;
  customer_name: string;
  subject: string;
  description: string;
  department: "support" | "billing" | "sales" | "abuse";
  status: "open" | "pending" | "resolved" | "closed";
  priority: "low" | "medium" | "high" | "urgent";
  assigned_to: number | null;
  assigned_to_name: string | null;
  created_at: string;
  updated_at: string;
  comments: TicketComment[];
}

export interface Job {
  id: number;
  customer: number | null;
  customer_name: string | null;
  ticket: number | null;
  ticket_number: string | null;
  assigned_to: number | null;
  assigned_to_name: string | null;
  job_type: "installation" | "repair" | "maintenance" | "site_visit" | "office_task" | "other";
  title: string;
  description: string;
  status: "scheduled" | "in_progress" | "completed" | "cancelled";
  start: string;
  end: string;
  location: string;
  created_at: string;
  updated_at: string;
}

export interface Shift {
  id: number;
  staff: number;
  staff_name: string;
  start: string;
  end: string;
  role_note: string;
  status: "planned" | "confirmed" | "cancelled";
  notes: string;
  created_at: string;
}

export interface Supplier {
  id: number;
  name: string;
  contact_person: string;
  phone: string;
  email: string;
  address: string;
  notes: string;
  created_at: string;
  // VAT. These only supply the *default* rate on a new stock receipt line —
  // the rate that counts is stored per line, since one invoice can mix
  // standard-rated and zero-rated items, and a supplier can register or
  // deregister without that rewriting receipts already captured.
  is_vat_registered: boolean;
  vat_number: string;
  default_vat_rate_pct: string;
  // How this supplier normally quotes prices. Pre-fills the receipt's own
  // prices_include_vat toggle; the receipt stores its own copy, so an
  // unusual invoice can be entered the other way without changing this.
  default_prices_include_vat: boolean;
  // Read-only: default_vat_rate_pct while registered, "0.00" otherwise.
  effective_vat_rate_pct: string;
}

// Mirrors the backend's expenses.models.Expense.Category choices --
// Accountant -> Expenses (the Input VAT side of the VAT Returns report).
export type ExpenseCategory =
  | "bandwidth"
  | "equipment"
  | "fuel"
  | "rent"
  | "utilities"
  | "software"
  | "professional"
  | "salaries"
  | "other";

export const EXPENSE_CATEGORY_LABELS: Record<ExpenseCategory, string> = {
  bandwidth: "Bandwidth / Transit",
  equipment: "Equipment / Hardware",
  fuel: "Fuel / Vehicles",
  rent: "Rent / Facilities",
  utilities: "Utilities",
  software: "Software / Subscriptions",
  professional: "Professional fees (legal / accounting)",
  salaries: "Salaries / Payroll",
  other: "Other",
};

// Mirrors GET/POST/PATCH /api/expenses/{id}/. `attachment` is write-only
// on the way in (a File via multipart) and a signed download URL (or
// null) on the way out -- same shape as StockReceipt's attachment.
export interface Expense {
  id: number;
  supplier: number | null;
  supplier_name: string;
  supplier_display_name: string;
  category: ExpenseCategory;
  description: string;
  invoice_number: string;
  date: string;
  amount_excl_vat: string;
  vat_rate_pct: string;
  vat_amount: string;
  amount_incl_vat: string;
  attachment: string | null;
  notes: string;
  created_by: number | null;
  created_by_name: string;
  from_bank_feed: boolean;
  created_at: string;
  updated_at: string;
}

// Mirrors GET /api/vat-return/ (and /api/vat-return/pdf/, same params,
// same numbers, rendered as a PDF instead of JSON) -- see
// expenses.views.build_vat_return on the backend.
export interface VatReturnResult {
  period_start: string;
  period_end: string;
  basis: string;
  vat_category: string;
  company_name: string;
  vat_number: string;
  output: {
    standard_rated_supplies: number;
    zero_rated_supplies: number;
    output_vat: number;
    invoice_count: number;
  };
  input: {
    // Combined across both sources below.
    purchases_excl_vat: number;
    input_vat: number;
    expense_count: number;
    // Input VAT comes from two places: Expenses (rent, bandwidth, fuel…)
    // and stock receipts (equipment bought for stock). Broken out so each
    // can be reconciled against its own tab.
    expenses: {
      purchases_excl_vat: number;
      input_vat: number;
      count: number;
    };
    stock: {
      purchases_excl_vat: number;
      input_vat: number;
      count: number;
      // Receipts with at least one line captured before VAT tracking
      // existed. Those claim no VAT, so the figure may be understated.
      receipts_missing_vat: number;
    };
  };
  credit_notes: {
    total_amount: number;
    count: number;
  };
  // A supplier invoice captured BOTH as a stock receipt and as an expense,
  // which would claim its Input VAT twice. Empty in the normal case. These
  // do NOT alter any figure above — they only warn.
  duplicate_claims: {
    invoice_number: string;
    supplier: string;
    receipt_id: number;
    receipt_date: string;
    receipt_vat: number;
    expense_id: number;
    expense_date: string;
    expense_description: string;
    expense_vat: number;
  }[];
  net_vat: number;
  net_vat_direction: "payable" | "refundable" | "even";
}

export interface Product {
  id: number;
  name: string;
  sku: string;
  category: "router" | "ont" | "cable" | "connector" | "tool" | "other";
  tracking_type: "serialized" | "quantity";
  unit: string;
  low_stock_threshold: number | null;
  description: string;
  is_active: boolean;
  // What a customer is charged for one, excluding VAT. Ex-VAT to match
  // InvoiceItem.unit_price, so a stock line on an invoice can be filled
  // straight from here with no conversion. Null = not for resale.
  sell_price: string | null;
  sell_tax_rate_pct: string;
  // Read-only, derived from what was actually paid on receipts. Cost is
  // never typed against a product — the same router bought a year apart
  // cost two different amounts, so it lives per delivery.
  latest_cost_excl_vat: string | null;
  average_cost_excl_vat: string | null;
  // Gross margin as a share of the SELL price, against the latest cost.
  // Margin, not markup: buy at 100 and sell at 150 and the markup is 50%
  // while the margin is 33.3%.
  margin_pct: string | null;
  quantity_on_hand: number;
  is_low_stock: boolean;
  created_at: string;
}

export interface SerializedUnit {
  id: number;
  product: number;
  product_name: string;
  // Stored canonically — serial upper-cased, MAC as AA:BB:CC:DD:EE:FF —
  // so a MAC copied off a router or a RADIUS log pastes straight into the
  // search box and matches. MAC is optional; blank means "not recorded".
  serial_number: string;
  mac_address: string;
  status: "in_stock" | "issued" | "faulty" | "returned_to_supplier";
  notes: string;
  created_at: string;
  // Where the unit came from, read through the receipt line it arrived on.
  // Null if that receipt line was since deleted — the unit is still real,
  // its paperwork just isn't.
  supplier_id: number | null;
  supplier_name: string | null;
  receipt_id: number | null;
  receipt_invoice_number: string | null;
  received_on: string | null;
}

export interface StockReceiptLine {
  id: number;
  receipt: number;
  product: number;
  product_name: string;
  quantity: number;
  serial_numbers: string;
  // Stored exactly as typed. Whether it is VAT-inclusive is decided by the
  // parent receipt's prices_include_vat, not per line.
  unit_cost: string | null;
  // null = VAT was never recorded (a line captured before VAT tracking
  // existed). Deliberately distinct from "0.00", which means genuinely
  // zero-rated — a null line claims no VAT and is flagged in the UI.
  vat_rate_pct: string | null;
  serial_count: number;
  unit_cost_excl_vat: string | null;
  line_excl_vat: string;
  line_vat: string;
  line_incl_vat: string;
  vat_recorded: boolean;
  created_at: string;
}

export interface StockReceipt {
  id: number;
  supplier: number;
  supplier_name: string;
  supplier_is_vat_registered: boolean;
  invoice_number: string;
  invoice_date: string;
  // Which convention the unit costs on this receipt were typed in.
  prices_include_vat: boolean;
  attachment: string | null;
  received_by: number | null;
  received_by_name: string | null;
  notes: string;
  lines: StockReceiptLine[];
  // Rounded once per receipt, so these agree to the cent with the Input VAT
  // the VAT return claims for this receipt.
  total_excl_vat: string;
  vat_total: string;
  total_incl_vat: string;
  has_unrecorded_vat: boolean;
  created_at: string;
}

export interface StockIssueLine {
  id: number;
  issue: number;
  product: number;
  product_name: string;
  quantity: number;
  serial_unit: number | null;
  serial_number: string | null;
  mac_address: string | null;
}

export interface StockIssue {
  id: number;
  job: number | null;
  job_title: string | null;
  customer_name: string | null;
  issued_to: number | null;
  issued_to_name: string | null;
  issued_at: string;
  notes: string;
  lines: StockIssueLine[];
}

export type EmailTemplateKey =
  | "welcome" | "quote" | "proforma" | "statement" | "invoice" | "payment_reminder" | "suspension" | "payment_received";

export interface EmailTemplate {
  id: number;
  key: EmailTemplateKey;
  name: string;
  subject: string;
  body_html: string;
  has_attachment: boolean;
  updated_at: string;
}

// Mirrors the backend's notifications.serializers.EmailSettingsSerializer
// -- GET/PATCH /api/email-settings/. Every field is optional: blank/null
// means "fall back to the server's .env default" (see the backend's
// notifications/email_settings.py). smtp_password is never returned by
// the API (write-only) -- smtp_password_set says whether one is stored,
// without ever exposing the value.
export interface EmailSettingsConfig {
  smtp_host: string;
  smtp_port: number | null;
  smtp_username: string;
  smtp_password_set: boolean;
  use_tls: boolean | null;
  use_ssl: boolean | null;
  default_from_email: string;
  company_name: string;
  site_url: string;
  updated_at: string;
}

// Mirrors radiusauth.serializers.OvpnSettingsSerializer -- platform-wide
// OVPN/FreeRADIUS defaults, edited from Configs -> OVPN.
export interface OvpnSettingsConfig {
  freeradius_ip: string;
  notes: string;
  updated_at: string;
}

export interface EmailLog {
  id: number;
  customer: number | null;
  customer_name: string | null;
  template_key: EmailTemplateKey;
  template_name: string;
  recipient_email: string;
  subject: string;
  status: "sent" | "failed";
  error_message: string;
  sent_by: number | null;
  sent_by_name: string | null;
  batch_id: string;
  created_at: string;
}

export type PayType = "salary" | "hourly";

export interface StaffProfile {
  id: number;
  user: number;
  staff_name: string;
  username: string;
  role: Role;
  phone: string;
  employee_number: string;
  id_number: string;
  license_number: string;
  pay_type: PayType;
  monthly_salary: string | null;
  hourly_rate: string | null;
  standard_daily_hours: string;
  overtime_multiplier: string;
  annual_leave_balance: string;
  sick_leave_balance: string;
  family_responsibility_leave_balance: string;
  is_active: boolean;
  created_at: string;
}

export type LeaveType = "annual" | "sick" | "family_responsibility";
export type LeaveStatus = "pending" | "approved" | "rejected";

export interface LeaveRequest {
  id: number;
  staff: number;
  staff_name: string;
  employee_number: string;
  leave_type: LeaveType;
  start_date: string;
  end_date: string;
  days_requested: number;
  reason: string;
  attachment: string | null;
  status: LeaveStatus;
  decision_note: string;
  decided_by: number | null;
  decided_by_name: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface AttendanceRecord {
  id: number;
  staff: number;
  staff_name: string;
  date: string;
  clock_in: string;
  clock_out: string | null;
  notes: string;
  is_manual: boolean;
  worked_hours: string;
  overtime_hours: string;
  created_at: string;
}

export type PayrollRunStatus = "draft" | "finalized";

export interface PayrollRunLine {
  id: number;
  staff: number;
  staff_name: string;
  employee_number: string;
  pay_type: PayType;
  regular_hours: string;
  overtime_hours: string;
  hourly_rate: string;
  overtime_rate: string;
  base_pay: string;
  overtime_pay: string;
  gross_pay: string;
  id_number: string;
  // Typed in by a person. PAYE is NOT calculated — it depends on the annual
  // SARS tables, age rebates and medical credits, none of which this system
  // knows, so the figure comes from whoever does the tax.
  paye: string;
  additional_amount: string;
  additional_description: string;
  other_deduction_amount: string;
  other_deduction_description: string;
  notes: string;
  // Calculated from remuneration against PayrollSettings — read-only.
  uif_employee: string;
  uif_employer: string;
  sdl: string;
  // Derived on the model so the payslip, the CSV export and these totals
  // can't disagree about anyone's net pay.
  total_earnings: string;
  total_deductions: string;
  net_pay: string;
  employer_contributions: string;
  cost_to_company: string;
}

// Mirrors GET/PATCH /api/payroll-settings/ — the statutory rates and employer
// details a payslip needs. Editable rather than hardcoded because every one
// of these numbers is set by legislation and moves.
export interface PayrollSettingsConfig {
  uif_rate_pct: string;
  uif_monthly_ceiling: string;
  sdl_rate_pct: string;
  sdl_applicable: boolean;
  employer_name: string;
  employer_address: string;
  paye_reference: string;
  uif_reference: string;
  payslip_note: string;
  updated_at: string;
}

export interface PayrollRun {
  id: number;
  period_start: string;
  period_end: string;
  status: PayrollRunStatus;
  generated_by: number | null;
  generated_by_name: string | null;
  created_at: string;
  finalized_at: string | null;
  lines: PayrollRunLine[];
  total_regular_hours: string;
  total_overtime_hours: string;
  total_gross_pay: string;
  total_paye: string;
  total_uif_employee: string;
  total_deductions: string;
  total_net_pay: string;
  total_cost_to_company: string;
  staff_count: number;
}

// "in_service" means the vehicle is AT THE WORKSHOP right now, and it
// overrides the km-derived states -- a bakkie booked in today is still
// past its due mark, but "Overdue" reads as nobody having dealt with it.
export type ServiceStatus = "ok" | "due_soon" | "overdue" | "in_service";
export type FuelType = "petrol" | "diesel";

export interface Vehicle {
  id: number;
  make: string;
  model: string;
  year: number;
  registration_number: string;
  fuel_type: FuelType;
  assigned_to: number | null;
  assigned_to_name: string | null;
  service_interval_km: number;
  is_active: boolean;
  notes: string;
  created_at: string;
  current_odometer_km: number;
  last_service_date: string | null;
  last_service_km: number;
  next_service_due_km: number;
  km_until_due: number;
  service_status: ServiceStatus;
  // Date it went into the workshop. Set = currently in for a service;
  // cleared automatically when the service is logged.
  in_service_since: string | null;
  days_in_service: number | null;
  total_litres_used: string;
  average_km_per_litre: number | null;
}

export interface OdometerReading {
  id: number;
  vehicle: number;
  reading_km: number;
  recorded_at: string;
  recorded_by: number | null;
  recorded_by_name: string | null;
  notes: string;
  created_at: string;
}

export interface ServiceRecord {
  id: number;
  vehicle: number;
  service_date: string;
  odometer_km: number;
  notes: string;
  logged_by: number | null;
  logged_by_name: string | null;
  created_at: string;
}

export interface FuelLog {
  id: number;
  vehicle: number;
  filled_at: string;
  litres: string;
  odometer_km: number;
  notes: string;
  logged_by: number | null;
  logged_by_name: string | null;
  created_at: string;
}

// Mirrors GET /api/customer-growth/?months=N -- see the backend's
// accounts.views.CustomerGrowthView. Aggregated server-side because the
// customer list endpoint caps page_size at 500 and there are more
// customers than that, so this can't be counted in the browser.
export interface CustomerGrowth {
  months: number;
  period_start: string;
  period_end: string;
  total_new: number;
  // Customers in this window with no real signup_date, so they're being
  // reported on the date their record was created instead. Non-zero after
  // a migration -- surfaced so an import spike isn't read as real trading.
  estimated_from_created_at: number;
  buckets: { month: string; label: string; new_customers: number }[];
}

// Mirrors GET /api/high-alert-customers/ -- customers who logged
// `min_tickets`+ tickets in a single calendar month at any point in the
// window. `peak_*` is their worst single month; `months_breached` is how
// many separate months they crossed the threshold, which is what
// separates a persistently unhappy account from one bad week.
export interface HighAlertCustomer {
  customer: number;
  customer_ref: string;
  full_name: string;
  status: string;
  peak_count: number;
  peak_month: string | null;
  peak_month_label: string;
  months_breached: number;
  total_tickets: number;
}

export interface HighAlertCustomers {
  months: number;
  min_tickets: number;
  period_start: string;
  period_end: string;
  count: number;
  results: HighAlertCustomer[];
}

// Mirrors GET /api/upcoming-blocks/?days=N — customers heading for an
// auto-suspension. See billing.views.UpcomingBlocksView.
export interface UpcomingBlock {
  customer: number;
  reference: string;
  name: string;
  // The earliest date a billing run would suspend this customer's services.
  block_date: string;
  // 0 means tomorrow (the headline figure), 1 the day after, and so on.
  days_until: number;
  balance: string;
  oldest_invoice_due: string;
  invoices_owing: number;
  active_services: number;
  blocking_period_days: number;
}

export interface UpcomingBlocks {
  from_date: string;
  horizon_days: number;
  // The platform-wide master switch (Configs → Billing → Auto-suspension).
  // The list is deliberately NOT filtered by this — when it's off these
  // customers still qualify, they just won't actually be cut off, and that
  // is worth seeing. The UI has to say which it is.
  auto_suspend_enabled: boolean;
  count_tomorrow: number;
  count_horizon: number;
  results: UpcomingBlock[];
}

export interface DashboardSummary {
  customers_total: number;
  customers_active: number;
  // Customers who have left. "Inactive" in the data; staff call it cancelled,
  // so the tile says that and links to the same status filter.
  customers_cancelled: number;
  // Count for the tile's headline; the value for its sublabel, because the
  // money is the part anyone reacts to.
  customers_bad_debt: number;
  customers_bad_debt_value: number;
  // Open leads whose follow-up date has arrived or passed.
  leads_follow_up_due: number;
  leads_open: number;
  customers_cancelled_recently: number;
  services_active: number;
  revenue_this_month: number;
  outstanding_balance: number;
  invoices_unpaid: number;
  invoices_overdue: number;
  devices_total: number;
  devices_online: number;
  devices_offline: number;
  tickets_open: number;
  tickets_urgent: number;
}

// -- Bank feeds ---------------------------------------------------------

// Mirrors bankfeeds.serializers.BankAccountSerializer. api_client_secret
// is write-only on the way in (never returned) -- api_client_secret_set
// tells the frontend whether one is currently stored, without exposing it.
export interface BankAccount {
  id: number;
  name: string;
  account_number: string;
  branch_code: string;
  is_active: boolean;
  api_base_url: string;
  api_client_id: string;
  api_client_secret_set: boolean;
  last_synced_at: string | null;
  last_sync_status: "success" | "failed" | "";
  last_sync_message: string;
  created_at: string;
  updated_at: string;
}

export type BankTransactionSource = "api" | "csv_import";
export type BankTransactionStatus = "unmatched" | "matched" | "confirmed" | "ignored";
export type BankTransactionMatchMethod = "reference" | "manual" | "";

// Mirrors bankfeeds.serializers.BankTransactionSerializer -- read-only,
// every state change happens through BankTransactionViewSet's
// assign/confirm/ignore/unmatch actions (see api client wiring below).
export interface BankTransaction {
  id: number;
  account: number;
  account_name: string;
  source: BankTransactionSource;
  external_id: string;
  date: string;
  description: string;
  amount: string;
  status: BankTransactionStatus;
  matched_customer: number | null;
  matched_customer_name: string | null;
  matched_supplier: number | null;
  matched_supplier_name: string | null;
  match_method: BankTransactionMatchMethod;
  confirmed_by: number | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  created_payment: number | null;
  created_expense: number | null;
  created_at: string;
}

// Mirrors GET /bank-feed-sync-logs/ -- one row per sync attempt (hourly
// cron or a manual "Sync now"), never per CSV import.
export interface BankFeedSyncLog {
  id: number;
  account: number | null;
  account_name: string | null;
  status: "success" | "failed";
  status_message: string;
  transactions_fetched: number;
  transactions_new: number;
  transactions_matched: number;
  triggered_by: number | null;
  triggered_by_name: string | null;
  created_at: string;
}

// Row shape returned by POST /bank-transactions/import-preview/.
export interface BankStatementImportRow {
  row: number;
  date: string | null;
  description: string;
  amount: string | null;
  external_id: string | null;
  errors: string[];
  already_imported: boolean;
}

export interface BankStatementImportPreview {
  total_rows: number;
  valid_count: number;
  invalid_count: number;
  already_imported_count: number;
  rows: BankStatementImportRow[];
}

export interface BankStatementImportResult {
  created: number;
  duplicates_skipped: number;
  matched: number;
  invalid_skipped: number;
  skipped: { row: number; errors: string[] }[];
}

// --- Audit trail -----------------------------------------------------------

export interface AuditChange {
  field: string;
  label: string;
  from: string;
  to: string;
}

export interface AuditEvent {
  id: number;
  action: "created" | "updated" | "deleted" | "login" | "login_failed" | "logout";
  action_display: string;
  actor: number | null;
  // The snapshot taken when the event happened, not the live account name.
  // Deliberately still populated when `actor` is null -- a deleted staff
  // account is exactly the case where the name matters most.
  actor_name: string;
  target_type: string;
  target_id: string;
  target_label: string;
  customer: number | null;
  customer_name: string;
  changes: AuditChange[];
  detail: string;
  ip_address: string | null;
  created_at: string;
}

export interface CustomerSession {
  session_id: string;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  ip_address: string | null;
  download_bytes: number;
  upload_bytes: number;
  terminate_cause: string;
  username: string;
  active: boolean;
}

// --- Sales / leads ---------------------------------------------------------

export type LeadStatus = "new" | "contacted" | "qualified" | "quoted" | "won" | "lost";

// Pipeline order, used for the stage strip. Won and Lost are included so
// the strip shows where deals ended up, not only where they're stuck.
export const LEAD_STAGES: LeadStatus[] = [
  "new",
  "contacted",
  "qualified",
  "quoted",
  "won",
  "lost",
];

export interface Lead {
  id: number;
  full_name: string;
  company_name: string;
  email: string;
  phone: string;
  address: string;
  city: string;
  zip_code: string;
  source: string;
  source_display: string;
  source_detail: string;
  partner: number | null;
  partner_name: string;
  assigned_to: number | null;
  assigned_to_name: string;
  interested_tariff: number | null;
  tariff_name: string;
  // Explicit override. null means nobody has said, which is different
  // from "0" — a free trial is a real answer.
  estimated_monthly_value: string | null;
  // What the pipeline counts: the override, else the tariff price, else 0.
  value: string;
  status: LeadStatus;
  status_display: string;
  lost_reason: string;
  lost_reason_display: string;
  next_follow_up: string | null;
  // Due today OR overdue, and still open. Overdue is not a separate state
  // on purpose — see the backend model.
  follow_up_is_due: boolean;
  customer: number | null;
  customer_name: string;
  customer_reference: string;
  notes: string;
  note_count: number;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
}

export interface LeadNote {
  id: number;
  lead: number;
  kind: "note" | "call" | "email" | "meeting" | "system";
  kind_display: string;
  body: string;
  author: number | null;
  author_name: string;
  created_at: string;
}

export interface PipelineStage {
  status: LeadStatus;
  label: string;
  count: number;
  value: string;
  is_open: boolean;
}

export interface PipelineSummary {
  stages: PipelineStage[];
  open_count: number;
  open_value: string;
  due_count: number;
  // Open leads with no follow-up date at all. Not overdue — invisible,
  // which is worse.
  unscheduled_count: number;
}

// --- Speed policy: fair use and time-of-day bursting -----------------------

export interface SpeedWindow {
  id: number;
  name: string;
  // null = applies to every tariff. Most networks want one network-wide
  // window; making staff attach it per plan is how a new plan silently
  // ends up outside the schedule.
  tariff: number | null;
  tariff_name: string | null;
  start_time: string;
  end_time: string;
  // 0=Mon … 6=Sun. Empty = every day.
  weekdays: number[];
  // Percent of the plan speed while the window is on. 200 = double.
  speed_pct: number;
  // Off (the default): traffic in this window doesn't count toward fair
  // use, which is the whole reason to run an off-peak window.
  counts_toward_fup: boolean;
  spans_midnight: boolean;
  is_active: boolean;
  created_at: string;
}

// GET /services/<id>/speed-now/ — what this line is running at right now,
// and why. Exists so "my internet is slow" has an answer a support agent
// can give without reading code.
export interface ServiceSpeedNow {
  service_id: number;
  plan_upload_kbps: number;
  plan_download_kbps: number;
  upload_kbps: number;
  download_kbps: number;
  rate_limit: string;
  reason: "plan" | "window" | "fup" | "window over fup";
  window_name: string;
  shaped: boolean;
  used_gb: number;
  threshold_gb: number | null;
  explanation: string;
  last_pushed_rate_limit: string;
}

export const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
