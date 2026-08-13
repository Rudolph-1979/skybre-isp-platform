export type Role = "admin" | "staff" | "technician" | "customer";

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
  status: "new" | "active" | "blocked" | "inactive";
  balance: string;
  assigned_staff: number | null;
  assigned_staff_name: string | null;
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
}

export interface InvoiceItem {
  id: number;
  invoice: number;
  service: number | null;
  description: string;
  quantity: string;
  unit_price: string;
  tax_rate_pct: string;
  total: string;
}

export interface Invoice {
  id: number;
  number: string;
  customer: number;
  customer_name: string;
  status: "draft" | "unpaid" | "paid" | "overdue" | "cancelled";
  date_created: string;
  date_due: string;
  subtotal: string;
  tax_total: string;
  total: string;
  paid_amount: string;
  balance_due: string;
  note: string;
  items: InvoiceItem[];
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

export interface Device {
  id: number;
  name: string;
  device_type: "router" | "switch" | "olt" | "access_point" | "server" | "onu";
  ip_address: string;
  location: string;
  vendor: string;
  model_name: string;
  status: "online" | "offline" | "unknown";
  snmp_community: string;
  snmp_version: string;
  latest_reading: MonitoringReading | null;
}

export interface IPPool {
  id: number;
  name: string;
  network_cidr: string;
  gateway: string | null;
  pool_type: "ipv4" | "ipv6";
  description: string;
  free_count: number;
  total_count: number;
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
