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
  | "customers"
  | "services"
  | "finance"
  | "inventory"
  | "networking"
  | "tickets"
  | "staff"
  | "vehicles"
  | "bulk_email"
  | "configs";

export const SECTION_LABELS: Record<Section, string> = {
  scheduling: "Scheduling",
  customers: "Customers",
  services: "Services",
  finance: "Finance",
  inventory: "Stock / Inventory",
  networking: "Networking",
  tickets: "Support Tickets",
  staff: "Staff",
  vehicles: "Vehicles",
  bulk_email: "Bulk Email",
  configs: "Configs",
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
  status: "new" | "active" | "suspended" | "inactive";
  balance: string;
  assigned_staff: number | null;
  assigned_staff_name: string | null;
  partner: number | null;
  partner_name: string | null;
  notes: string;
  created_at: string;
  updated_at: string;
  user: number | null;
}

export interface Tariff {
  id: number;
  name: string;
  service_type: "internet" | "voice" | "bundle" | "other";
  price: string;
  billing_period: "monthly" | "quarterly" | "annually";
  speed_download_mbps: number | null;
  speed_upload_mbps: number | null;
  data_cap_gb: number | null;
  tax_rate_pct: string;
  is_active: boolean;
  description: string;
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
  price: string;
  status: "active" | "suspended" | "terminated" | "pending";
  device: number | null;
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
  partner: number | null;
  partner_name: string | null;
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

export interface RadAcctSession {
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
  quantity_on_hand: number;
  is_low_stock: boolean;
  created_at: string;
}

export interface SerializedUnit {
  id: number;
  product: number;
  product_name: string;
  serial_number: string;
  mac_address: string;
  status: "in_stock" | "issued" | "faulty" | "returned_to_supplier";
  notes: string;
  created_at: string;
}

export interface StockReceiptLine {
  id: number;
  receipt: number;
  product: number;
  product_name: string;
  quantity: number;
  serial_numbers: string;
  unit_cost: string | null;
  serial_count: number;
  created_at: string;
}

export interface StockReceipt {
  id: number;
  supplier: number;
  supplier_name: string;
  invoice_number: string;
  invoice_date: string;
  attachment: string | null;
  received_by: number | null;
  received_by_name: string | null;
  notes: string;
  lines: StockReceiptLine[];
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
  staff_count: number;
}

export type ServiceStatus = "ok" | "due_soon" | "overdue";
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

export interface DashboardSummary {
  customers_total: number;
  customers_active: number;
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
  match_method: BankTransactionMatchMethod;
  confirmed_by: number | null;
  confirmed_by_name: string | null;
  confirmed_at: string | null;
  created_payment: number | null;
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
