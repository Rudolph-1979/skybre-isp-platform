import { useEffect, useState, type FormEvent } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import { api } from "../../api/client";
import { useAuth } from "../../context/AuthContext";
import { PageHeader } from "../../components/PageHeader";
import { Table, THead, TH, TR, TD } from "../../components/Table";
import { StatusBadge } from "../../components/StatusBadge";
import { Modal, FormField, inputClass, btnPrimary, btnSecondary } from "../../components/Modal";
import { PdfPreviewModal } from "../../components/PdfPreviewModal";
import {
  ServiceConnectionFields, serviceConnectionPayload, emptyServiceConnectionValues,
  type ServiceConnectionValues,
} from "../../components/ServiceConnectionFields";
import {
  ServiceShapingFields, serviceShapingPayload, emptyServiceShapingValues,
  type ServiceShapingValues,
} from "../../components/ServiceShapingFields";
import { LiveBandwidthWidget } from "../../components/LiveBandwidthWidget";
import { CustomerUsageCard } from "../../components/CustomerUsageCard";
import { CustomerHistoryTab } from "../../components/CustomerHistoryTab";
import { SpeedNowBadge } from "../../components/SpeedNowBadge";
import { useViewAs } from "../../context/ViewAsContext";
import {
  RecurringBillingFieldsFormBody, recurringBillingFieldsToFormState, recurringBillingFormStateToPayload,
  EMPTY_RECURRING_BILLING_FORM, type RecurringBillingFormState,
} from "./ConfigsPage";
import type {
  Customer, Service, Invoice, Payment, Ticket, User, EmailTemplateKey, EmailLog,
  Tariff, IPPool, IPAddress, CustomerDeletionRequest, Partner, Device, ConnectionRule,
  PaymentMethod, BillingDefaultsConfig, CustomerBillingConfig as CustomerBillingConfigType,
} from "../../types";

type Tab = "overview" | "billing" | "email" | "history";

const TABS: { key: Tab; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "billing", label: "Billing" },
  { key: "email", label: "Email" },
  { key: "history", label: "History" },
];

const TEMPLATE_OPTIONS: { key: EmailTemplateKey; label: string }[] = [
  { key: "welcome", label: "Welcome message" },
  { key: "quote", label: "Quote" },
  { key: "proforma", label: "Pro forma invoice" },
  { key: "statement", label: "Statement" },
  { key: "invoice", label: "Invoice" },
  { key: "payment_reminder", label: "Payment reminder" },
  { key: "suspension", label: "Suspension notification" },
];

// These three each need one specific invoice/quote/pro forma selected --
// keep in sync with notifications/views.py's DOCUMENT_TEMPLATE_KEYS.
const DOCUMENT_TEMPLATE_KEYS: (EmailTemplateKey | "")[] = ["invoice", "quote", "proforma"];

// Wording follows the document's own status, matching the heading the PDF
// itself prints.
// Local date, not toISOString() -- that converts to UTC first, so in
// SAST (UTC+2) anything before 02:00 comes back as yesterday and the date
// input's `min` would silently permit a date the backend then rejects.
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function docNounFor(status: Invoice["status"]) {
  return status === "quote" ? "quote" : status === "proforma" ? "pro forma" : "invoice";
}

type NewServiceForm = { tariff: string; status: Service["status"] } & ServiceConnectionValues & ServiceShapingValues;

const emptyNewServiceForm: NewServiceForm = {
  tariff: "", status: "pending", ...emptyServiceConnectionValues, ...emptyServiceShapingValues,
};

type ServiceEditForm = {
  status: Service["status"];
  tariff: string;
  // The day their service STOPS. Cut-off is at 00:00 on this date, so they
  // have service through the end of the day before it. When it arrives the
  // service is terminated and, if it was their last one, the customer is set
  // to Cancelled -- see billing/cancellations.py.
  end_date: string;
  // A tariff change booked for later. Held separately from `tariff` on
  // purpose: setting `tariff` applies now, which bills and rate-limits them
  // on the new plan immediately -- wrong for a change agreed mid-month that
  // belongs at the start of the next billing period.
  pending_tariff: string;
  pending_tariff_date: string;
} & ServiceConnectionValues & ServiceShapingValues;

const emptyServiceEditForm: ServiceEditForm = {
  status: "pending", tariff: "", end_date: "", pending_tariff: "", pending_tariff_date: "",
  ...emptyServiceConnectionValues, ...emptyServiceShapingValues,
};

export function CustomerDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user: currentUser } = useAuth();
  const { startViewing } = useViewAs();
  const canDecideDeletion = currentUser?.role === "admin" || currentUser?.role === "management";
  const [tab, setTab] = useState<Tab>("overview");
  const [copied, setCopied] = useState(false);
  const [customer, setCustomer] = useState<Customer | null>(null);
  const [services, setServices] = useState<Service[]>([]);
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  // One preview modal serves both kinds of document on this page: an
  // invoice/quote/pro forma (identified by its row) and the customer's
  // statement (which has no row of its own).
  const [pdfFor, setPdfFor] = useState<Invoice | null>(null);
  const [showStatementPdf, setShowStatementPdf] = useState(false);
  const [payments, setPayments] = useState<Payment[]>([]);
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [staff, setStaff] = useState<User[]>([]);
  const [partners, setPartners] = useState<Partner[]>([]);
  // Reseller partners this staff member is allowed to assign a customer to
  // -- empty allowed_partners on their account means unrestricted (every
  // partner is fair game), same convention as the Customers list page.
  const accessiblePartners =
    currentUser && currentUser.allowed_partners.length > 0
      ? partners.filter((p) => currentUser.allowed_partners.includes(p.id))
      : partners;

  const [showEdit, setShowEdit] = useState(false);
  const [form, setForm] = useState<Partial<Customer>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // -- Deletion request (deleting a customer needs Management approval --
  // see CustomerDeletionRequest on the backend) --
  const [deletionRequest, setDeletionRequest] = useState<CustomerDeletionRequest | null>(null);
  const [showRequestDeletion, setShowRequestDeletion] = useState(false);
  const [deletionReason, setDeletionReason] = useState("");
  const [deletionSaving, setDeletionSaving] = useState(false);
  const [deletionError, setDeletionError] = useState<string | null>(null);
  const [rejectingDeletion, setRejectingDeletion] = useState(false);
  const [rejectDecisionNote, setRejectDecisionNote] = useState("");
  const [deletionBusy, setDeletionBusy] = useState(false);

  // -- Services (PPPoE/OVPN service creation + editing lives here, on the
  // customer's own page, rather than only on the standalone Services page) --
  const [tariffs, setTariffs] = useState<Tariff[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [connectionRules, setConnectionRules] = useState<ConnectionRule[]>([]);
  const [customerPools, setCustomerPools] = useState<IPPool[]>([]);
  const [newPoolAddresses, setNewPoolAddresses] = useState<IPAddress[]>([]);
  const [poolAddresses, setPoolAddresses] = useState<IPAddress[]>([]);
  const [showNewService, setShowNewService] = useState(false);
  const [newServiceForm, setNewServiceForm] = useState<NewServiceForm>(emptyNewServiceForm);
  const [newServiceSaving, setNewServiceSaving] = useState(false);
  const [newServiceError, setNewServiceError] = useState<string | null>(null);
  const [editingService, setEditingService] = useState<Service | null>(null);
  const [serviceEditForm, setServiceEditForm] = useState<ServiceEditForm>(emptyServiceEditForm);
  const [serviceEditSaving, setServiceEditSaving] = useState(false);
  const [serviceEditError, setServiceEditError] = useState<string | null>(null);

  // -- Billing config (recurring invoicing/reminders/auto-suspension for
  // this one customer -- see billing.models.CustomerBillingConfig) --
  const [billingConfig, setBillingConfig] = useState<CustomerBillingConfigType | null>(null);
  const [billingLoading, setBillingLoading] = useState(true);
  const [billingLoadFailed, setBillingLoadFailed] = useState(false);
  const [billingEnabled, setBillingEnabled] = useState(false);
  const [billingAddressForm, setBillingAddressForm] = useState({
    billing_name: "", billing_street: "", billing_zip: "", billing_city: "",
  });
  const [billingForm, setBillingForm] = useState<RecurringBillingFormState>(EMPTY_RECURRING_BILLING_FORM);
  const [billingSaving, setBillingSaving] = useState(false);
  const [billingError, setBillingError] = useState("");
  const [billingSaved, setBillingSaved] = useState(false);
  const [paymentMethods, setPaymentMethods] = useState<PaymentMethod[]>([]);
  // Admin-only: pulls the org-wide template in to overwrite the form below
  // (not saved until "Save billing config" is clicked) -- see
  // ConfigsPage.tsx's Billing Defaults tab for the same template.
  const [resettingDefaults, setResettingDefaults] = useState(false);

  function refetchBillingConfig() {
    if (!id) return;
    setBillingLoading(true);
    setBillingLoadFailed(false);
    api
      .get<CustomerBillingConfigType>(`/customer-billing-config/${id}/`)
      .then((res) => {
        setBillingConfig(res.data);
        setBillingEnabled(res.data.billing_enabled);
        setBillingAddressForm({
          billing_name: res.data.billing_name,
          billing_street: res.data.billing_street,
          billing_zip: res.data.billing_zip,
          billing_city: res.data.billing_city,
        });
        setBillingForm(recurringBillingFieldsToFormState(res.data));
      })
      .catch(() => setBillingLoadFailed(true))
      .finally(() => setBillingLoading(false));
  }

  async function handleSaveBilling(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setBillingSaving(true);
    setBillingError("");
    setBillingSaved(false);
    try {
      const res = await api.patch<CustomerBillingConfigType>(`/customer-billing-config/${id}/`, {
        billing_enabled: billingEnabled,
        ...billingAddressForm,
        ...recurringBillingFormStateToPayload(billingForm),
      });
      setBillingConfig(res.data);
      setBillingSaved(true);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setBillingError(typeof firstError === "string" ? firstError : "Could not save this customer's billing config — please try again.");
    } finally {
      setBillingSaving(false);
    }
  }

  async function handleResetBillingDefaults() {
    setResettingDefaults(true);
    try {
      const res = await api.get<BillingDefaultsConfig>("/billing-defaults/");
      setBillingForm(recurringBillingFieldsToFormState(res.data));
    } catch {
      setBillingError("Could not load the org-wide Billing Defaults to reset from.");
    } finally {
      setResettingDefaults(false);
    }
  }

  const [emailLogs, setEmailLogs] = useState<EmailLog[]>([]);
  const [sendTemplate, setSendTemplate] = useState<EmailTemplateKey | "">("");
  const [sendInvoiceId, setSendInvoiceId] = useState("");
  const [preview, setPreview] = useState<{ subject: string; body_html: string; will_attach_pdf: boolean } | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState<{ ok: boolean; message: string } | null>(null);

  function refetchCustomer() {
    if (!id) return;
    api.get<Customer>(`/customers/${id}/`).then((res) => setCustomer(res.data));
  }

  function refetchServices() {
    if (!id) return;
    api.get<{ results: Service[] }>(`/services/?customer=${id}`).then((res) => setServices(res.data.results));
  }

  function refetchEmailLogs() {
    if (!id) return;
    api.get<{ results: EmailLog[] }>(`/email-logs/?customer=${id}`).then((res) => setEmailLogs(res.data.results));
  }

  function refetchDeletionRequest() {
    if (!id) return;
    api
      .get<{ results: CustomerDeletionRequest[] }>(`/customer-deletion-requests/?customer=${id}&status=pending`)
      .then((res) => setDeletionRequest(res.data.results[0] ?? null));
  }

  useEffect(() => {
    if (!id) return;
    refetchCustomer();
    refetchServices();
    api.get<{ results: Invoice[] }>(`/invoices/?customer=${id}&ordering=-date_created`).then((res) => setInvoices(res.data.results));
    api.get<{ results: Payment[] }>(`/payments/?customer=${id}&ordering=-date`).then((res) => setPayments(res.data.results));
    api.get<{ results: Ticket[] }>(`/tickets/?customer=${id}`).then((res) => setTickets(res.data.results));
    api.get<{ results: User[] }>("/staff-users/?page_size=100").then((res) => setStaff(res.data.results));
    api.get<{ results: Tariff[] }>("/tariffs/?page_size=100").then((res) => setTariffs(res.data.results));
    api.get<{ results: Partner[] }>("/partners/?page_size=200&ordering=name").then((res) => setPartners(res.data.results));
    // Used by the Router & shaping section of the service create/edit
    // modals below -- fetched once up front the same way tariffs are.
    api.get<{ results: Device[] }>("/devices/?page_size=200").then((res) => setDevices(res.data.results));
    api.get<{ results: ConnectionRule[] }>("/connection-rules/?page_size=500").then((res) => setConnectionRules(res.data.results));
    // Used by the PPPoE IP-assignment section of the service edit modal
    // below -- fetched once up front the same way tariffs are.
    api.get<{ results: IPPool[] }>("/ip-pools/?category=customer&page_size=200").then((res) => setCustomerPools(res.data.results));
    api.get<{ results: PaymentMethod[] }>("/payment-methods/?page_size=200&ordering=name").then((res) => setPaymentMethods(res.data.results));
    refetchEmailLogs();
    refetchDeletionRequest();
    refetchBillingConfig();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // Free addresses in the pool chosen on the "New service" form.
  useEffect(() => {
    if (newServiceForm.ip_assignment_mode === "pool" && newServiceForm.ip_pool) {
      api.get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${newServiceForm.ip_pool}&page_size=500`).then((res) =>
        setNewPoolAddresses(res.data.results)
      );
    } else {
      setNewPoolAddresses([]);
    }
  }, [newServiceForm.ip_assignment_mode, newServiceForm.ip_pool]);

  // Free (or already-held-by-this-service) addresses in the chosen pool,
  // for the "Select from Customer IP Pool" address dropdown -- refetched
  // whenever the pool selection changes while in pool mode.
  useEffect(() => {
    if (serviceEditForm.ip_assignment_mode === "pool" && serviceEditForm.ip_pool) {
      api.get<{ results: IPAddress[] }>(`/ip-addresses/?pool=${serviceEditForm.ip_pool}&page_size=500`).then((res) =>
        setPoolAddresses(res.data.results)
      );
    } else {
      setPoolAddresses([]);
    }
  }, [serviceEditForm.ip_assignment_mode, serviceEditForm.ip_pool]);

  // What the currently chosen template would attach, or null for the
  // text-only templates (welcome / payment reminder / suspension).
  const attachmentPreview: { kind: "statement" } | { kind: "document"; invoice: Invoice } | null = (() => {
    if (sendTemplate === "statement") return { kind: "statement" };
    if (!DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) || !sendInvoiceId) return null;
    const chosen = invoices.find((inv) => String(inv.id) === String(sendInvoiceId));
    return chosen ? { kind: "document", invoice: chosen } : null;
  })();

  function selectTemplate(key: EmailTemplateKey | "") {
    setSendTemplate(key);
    setSendInvoiceId("");
    setPreview(null);
    setSendResult(null);
  }

  async function handlePreview() {
    if (!id || !sendTemplate) return;
    setPreviewing(true);
    setSendResult(null);
    try {
      const res = await api.post("/email-preview/", {
        template_key: sendTemplate,
        customer: Number(id),
        invoice: DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) ? Number(sendInvoiceId) : undefined,
      });
      setPreview(res.data);
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setSendResult({ ok: false, message: typeof firstError === "string" ? firstError : "Could not render a preview." });
    } finally {
      setPreviewing(false);
    }
  }

  async function handleSend() {
    if (!id || !sendTemplate) return;
    setSending(true);
    setSendResult(null);
    try {
      await api.post(`/customers/${id}/send-email/`, {
        template_key: sendTemplate,
        invoice: DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) ? Number(sendInvoiceId) : undefined,
      });
      setSendResult({ ok: true, message: "Email sent." });
      setPreview(null);
      refetchEmailLogs();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setSendResult({
        ok: false,
        message: typeof firstError === "string" ? firstError : "Could not send this email — see the log below for details.",
      });
      refetchEmailLogs();
    } finally {
      setSending(false);
    }
  }

  function openEdit() {
    if (!customer) return;
    setForm({
      full_name: customer.full_name,
      company_name: customer.company_name,
      email: customer.email,
      phone: customer.phone,
      address: customer.address,
      city: customer.city,
      zip_code: customer.zip_code,
      id_number: customer.id_number,
      vat_number: customer.vat_number,
      customer_type: customer.customer_type,
      category: customer.category,
      status: customer.status,
      assigned_staff: customer.assigned_staff,
      partner: customer.partner,
      notes: customer.notes,
      customer_id: customer.customer_id,
    });
    setError("");
    setShowEdit(true);
  }

  async function handleSave(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    setError("");
    try {
      // Painted straight from the PATCH response rather than waiting on a
      // second GET. The header badge used to keep showing the old status
      // after an edit, because the only thing updating it was that follow-up
      // request -- and if it was slow, or failed, or the save itself timed
      // out on something slower downstream, the write had gone through but
      // the screen never caught up. The response already carries the saved
      // customer, so use it.
      const res = await api.patch<Customer>(`/customers/${id}/`, form);
      setCustomer(res.data);
      setShowEdit(false);
      // Still refetched, because a status change has knock-on effects the
      // PATCH response can't show: services suspended or restored alongside
      // it (see customers/signals.py on the backend).
      refetchCustomer();
      refetchServices();
    } catch (err) {
      const data = (err as { response?: { data?: Record<string, unknown> } })?.response?.data;
      const firstError = data ? Object.values(data).flat()[0] : null;
      setError(typeof firstError === "string" ? firstError : "Could not save changes — please check the fields and try again.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRequestDeletion(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setDeletionSaving(true);
    setDeletionError(null);
    try {
      await api.post("/customer-deletion-requests/", { customer: Number(id), reason: deletionReason });
      setShowRequestDeletion(false);
      setDeletionReason("");
      refetchDeletionRequest();
    } catch (err) {
      const data = (err as { response?: { data?: unknown } })?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setDeletionError(message || "Couldn't submit this deletion request.");
    } finally {
      setDeletionSaving(false);
    }
  }

  async function handleApproveDeletion() {
    if (!deletionRequest || !customer) return;
    if (
      !confirm(
        `Permanently delete ${customer.full_name}? This deletes ALL of their services, invoices, payments, ` +
          "credit requests, tickets, and email history, and can't be undone."
      )
    )
      return;
    setDeletionBusy(true);
    try {
      await api.post(`/customer-deletion-requests/${deletionRequest.id}/approve/`);
      navigate("/admin/customers");
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      alert(detail || "Couldn't approve this deletion request.");
      setDeletionBusy(false);
    }
  }

  async function handleRejectDeletion(e: FormEvent) {
    e.preventDefault();
    if (!deletionRequest) return;
    setDeletionBusy(true);
    try {
      await api.post(`/customer-deletion-requests/${deletionRequest.id}/reject/`, { decision_note: rejectDecisionNote });
      setRejectingDeletion(false);
      setRejectDecisionNote("");
      refetchDeletionRequest();
    } finally {
      setDeletionBusy(false);
    }
  }

  async function handleWithdrawDeletion() {
    if (!deletionRequest) return;
    if (!confirm("Withdraw this deletion request?")) return;
    await api.delete(`/customer-deletion-requests/${deletionRequest.id}/`);
    refetchDeletionRequest();
  }

  function openNewService() {
    setNewServiceForm(emptyNewServiceForm);
    setNewServiceError(null);
    setShowNewService(true);
  }

  async function handleNewServiceSubmit(e: FormEvent) {
    e.preventDefault();
    if (!id) return;
    setNewServiceSaving(true);
    setNewServiceError(null);
    try {
      await api.post("/services/", {
        customer: Number(id),
        tariff: newServiceForm.tariff,
        status: newServiceForm.status,
        start_date: new Date().toISOString().slice(0, 10),
        ...serviceConnectionPayload(newServiceForm),
        ...serviceShapingPayload(newServiceForm),
      });
      setShowNewService(false);
      refetchServices();
      refetchCustomer();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setNewServiceError(message || "Couldn't create this service.");
    } finally {
      setNewServiceSaving(false);
    }
  }

  function openEditService(service: Service) {
    setEditingService(service);
    setServiceEditError(null);
    setServiceEditForm({
      status: service.status,
      tariff: String(service.tariff),
      end_date: service.end_date ?? "",
      pending_tariff: service.pending_tariff ? String(service.pending_tariff) : "",
      pending_tariff_date: service.pending_tariff_date ?? "",
      radius_username: service.radius_username ?? "",
      radius_password: "",
      radius_connection_type: service.radius_connection_type,
      ip_assignment_mode: service.ip_assignment_mode,
      static_ip: service.static_ip ?? "",
      ip_pool: service.ip_pool ? String(service.ip_pool) : "",
      ip_address: "",
      device: service.device ? String(service.device) : "",
      connection_rule: service.connection_rule ? String(service.connection_rule) : "",
      access_device: service.access_device ? String(service.access_device) : "",
      access_detail: service.access_detail ?? "",
      fup_threshold_gb: service.fup_threshold_gb == null ? "" : String(service.fup_threshold_gb),
      fup_speed_pct: service.fup_speed_pct == null ? "" : String(service.fup_speed_pct),
      fup_exempt: service.fup_exempt ?? false,
    });
  }

  // Derived rather than its own useState: a booking is defined by
  // pending_tariff_date being set, so a separate flag could drift out of step
  // with the data and show "Change now" over a live booking.
  const tariffWhen: "now" | "later" = serviceEditForm.pending_tariff_date ? "later" : "now";

  async function handleEditServiceSubmit(e: FormEvent) {
    e.preventDefault();
    if (!editingService) return;
    setServiceEditSaving(true);
    setServiceEditError(null);
    try {
      await api.patch(`/services/${editingService.id}/`, {
        status: serviceEditForm.status,
        tariff: Number(serviceEditForm.tariff),
        // null, not "", so clearing it really removes the cancellation rather
        // than failing validation on an empty string.
        end_date: serviceEditForm.end_date || null,
        // Sent as null rather than omitted when cleared, so cancelling a
        // booked change actually removes it instead of leaving it in place.
        pending_tariff: serviceEditForm.pending_tariff ? Number(serviceEditForm.pending_tariff) : null,
        pending_tariff_date: serviceEditForm.pending_tariff_date || null,
        ...serviceConnectionPayload(serviceEditForm),
        ...serviceShapingPayload(serviceEditForm),
      });
      setEditingService(null);
      refetchServices();
      // The customer too, not just the service. Restoring a suspended
      // customer's line now lifts the customer with it (customers/signals.py),
      // and without this refetch the badge in the header carries on saying
      // Suspended directly above a service the same screen shows as Active.
      refetchCustomer();
    } catch (err: any) {
      const data = err?.response?.data;
      const message = data && typeof data === "object" ? Object.values(data).flat().join(" ") : null;
      setServiceEditError(message || "Couldn't save this service.");
    } finally {
      setServiceEditSaving(false);
    }
  }

  async function handleDeleteService(service: Service) {
    if (
      !confirm(
        `Delete the ${service.tariff_name} service? This also releases its RADIUS login and any assigned IP ` +
          "address. This can't be undone."
      )
    )
      return;
    try {
      await api.delete(`/services/${service.id}/`);
      if (editingService?.id === service.id) setEditingService(null);
      refetchServices();
    } catch (err: any) {
      alert(err?.response?.data?.detail || "Couldn't delete this service.");
    }
  }

  async function copyEmail() {
    if (!customer?.email) return;
    try {
      await navigator.clipboard.writeText(customer.email);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be blocked by the browser (e.g. no HTTPS,
      // no permission) — the mailto link below still works either way.
    }
  }

  if (!customer) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <Link to="/admin/customers" className="mb-4 inline-block text-sm text-[var(--series-1)] hover:underline">
        ← Back to customers
      </Link>
      <PageHeader
        title={customer.full_name}
        subtitle={`${customer.customer_id} · ${customer.email} · ${customer.phone}`}
        actions={
          <>
            <StatusBadge status={customer.status} />
            <button
              type="button"
              className={btnSecondary}
              title="Open the customer portal as this customer sees it. Read only — nothing is recorded as the customer."
              onClick={() => {
                startViewing({ id: customer.id, name: customer.full_name });
                navigate("/portal");
              }}
            >
              View customer portal
            </button>
            <button className={btnPrimary} onClick={openEdit}>
              Edit customer
            </button>
            {!deletionRequest && (
              <button
                type="button"
                className="text-sm font-medium text-red-600 hover:underline dark:text-red-400"
                onClick={() => {
                  setDeletionReason("");
                  setDeletionError(null);
                  setShowRequestDeletion(true);
                }}
              >
                Request deletion
              </button>
            )}
          </>
        }
      />

      {deletionRequest && (
        <div className="mb-6 rounded-lg border border-red-300 bg-red-50 p-4 text-sm dark:border-red-900 dark:bg-red-950">
          <p className="font-medium text-red-800 dark:text-red-200">
            Deletion requested by {deletionRequest.requested_by_name ?? "a staff member"} — awaiting Management
            approval.
          </p>
          <p className="mt-1 text-red-700 dark:text-red-300">Reason: {deletionRequest.reason}</p>
          <div className="mt-3 flex gap-2">
            {canDecideDeletion && (
              <>
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={deletionBusy}
                  onClick={handleApproveDeletion}
                >
                  {deletionBusy ? "Deleting…" : "Approve & delete"}
                </button>
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={deletionBusy}
                  onClick={() => {
                    setRejectingDeletion(true);
                    setRejectDecisionNote("");
                  }}
                >
                  Reject
                </button>
              </>
            )}
            <button type="button" className={btnSecondary} onClick={handleWithdrawDeletion}>
              Withdraw
            </button>
          </div>
        </div>
      )}

      <div className="mb-6 flex gap-1 border-b border-[var(--border-hairline)]">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className={`px-4 py-2 text-sm font-medium ${
              tab === t.key
                ? "border-b-2 border-[var(--series-1)] text-[var(--text-primary)]"
                : "text-[var(--text-muted)] hover:text-[var(--text-primary)]"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <>
          <div className="mb-6 grid grid-cols-1 gap-4 text-sm sm:grid-cols-4">
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Balance owed</p>
              <p className="tabular-nums mt-1 text-lg font-semibold">R {parseFloat(customer.balance).toFixed(2)}</p>
            </div>
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Address</p>
              <p className="mt-1">{customer.address || "—"}, {customer.city}</p>
            </div>
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Assigned staff</p>
              <p className="mt-1">{customer.assigned_staff_name ?? "Unassigned"}</p>
            </div>
            <div className="rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-4">
              <p className="text-[var(--text-muted)]">Partner</p>
              <p className="mt-1">{customer.partner_name ?? "Direct (no partner)"}</p>
            </div>
          </div>

          <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            <h2 className="mb-4 text-sm font-semibold">Data usage</h2>
            <CustomerUsageCard
              customerId={customer.id}
              usageToken={customer.usage_token ?? null}
              onTokenChanged={(token) => setCustomer({ ...customer, usage_token: token })}
              liveBandwidthPublic={customer.live_bandwidth_public}
              onLiveBandwidthPublicChanged={(value) =>
                setCustomer({ ...customer, live_bandwidth_public: value })
              }
            />
          </div>

          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold">Services</h2>
            <button className={btnPrimary} onClick={openNewService}>
              + New service
            </button>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Tariff</TH>
                <TH>Price</TH>
                <TH>Status</TH>
                <TH>Start date</TH>
                <TH>Connection</TH>
                <TH>Connects to</TH>
                <TH>Assigned IP</TH>
                <TH></TH>
              </tr>
            </THead>
            <tbody>
              {services.map((s) => (
                <TR key={s.id}>
                  <TD>
                    {s.tariff_name}
                    {/* Surfaced on the row, not just inside the edit form:
                        a change nobody can see is a change nobody remembers
                        agreeing to. */}
                    {s.pending_tariff_name && s.pending_tariff_date && (
                      <span className="mt-0.5 block text-xs text-[var(--series-1)]">
                        → {s.pending_tariff_name} on {s.pending_tariff_date}
                      </span>
                    )}
                    {/* Only shown when the last push FAILED. A success here
                        would be noise on every row; a failure is the thing
                        that used to be invisible — the screen said Saved
                        while the customer kept their old speed, and nothing
                        anywhere connected the two. */}
                    {/* Only rendered when the line ISN'T at its plan
                        speed, so it reads as an exception rather than as
                        a label on every row. */}
                    <SpeedNowBadge serviceId={s.id} />
                    {s.last_radius_action && !s.last_radius_action.ok && (
                      <span
                        className="mt-0.5 block text-xs text-[var(--status-critical)]"
                        title={s.last_radius_action.detail}
                      >
                        ⚠ Last change didn't reach the router — still on the old settings
                      </span>
                    )}
                  </TD>
                  <TD className="tabular-nums">R {parseFloat(s.price).toFixed(2)}</TD>
                  <TD><StatusBadge status={s.status} /></TD>
                  <TD>{s.start_date ?? "—"}</TD>
                  <TD className="uppercase">{s.radius_connection_type}</TD>
                  {/* On the row rather than only inside the edit form: the
                      moment this matters is an outage, and opening five
                      services one at a time to find out which tower they
                      are on is the opposite of useful. */}
                  <TD>
                    {s.access_device_name ? (
                      <>
                        <span className="text-[var(--text-primary)]">{s.access_device_name}</span>
                        <span className="block text-xs text-[var(--text-muted)]">
                          {[s.access_site_name, s.access_detail].filter(Boolean).join(" · ") ||
                            s.access_device_type}
                        </span>
                      </>
                    ) : (
                      <span className="text-[var(--text-muted)]">Not recorded</span>
                    )}
                  </TD>
                  <TD>{s.assigned_ip ?? "—"}</TD>
                  <TD>
                    <div className="flex gap-3">
                      <button className="text-[var(--series-1)] hover:underline" onClick={() => openEditService(s)}>
                        Edit
                      </button>
                      <button
                        className="text-red-600 hover:underline dark:text-red-400"
                        onClick={() => handleDeleteService(s)}
                      >
                        Delete
                      </button>
                    </div>
                  </TD>
                </TR>
              ))}
              {services.length === 0 && <TR><TD className="text-[var(--text-muted)]">No services yet.</TD></TR>}
            </tbody>
          </Table>

          <div className="mb-2 mt-6 flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Invoices</h2>
            <button type="button" className={btnSecondary} onClick={() => setShowStatementPdf(true)}>
              Preview statement
            </button>
          </div>
          <Table>
            <THead>
              <tr>
                <TH>Number</TH>
                <TH>Due</TH>
                <TH>Total</TH>
                <TH>Paid</TH>
                <TH>Status</TH>
                <TH>Preview</TH>
              </tr>
            </THead>
            <tbody>
              {invoices.map((inv) => (
                <TR key={inv.id}>
                  <TD><Link to={`/admin/finance/invoices/${inv.id}`} className="text-[var(--series-1)] hover:underline">{inv.number}</Link></TD>
                  <TD>{inv.date_due}</TD>
                  <TD className="tabular-nums">R {parseFloat(inv.total).toFixed(2)}</TD>
                  <TD className="tabular-nums">R {parseFloat(inv.paid_amount).toFixed(2)}</TD>
                  <TD><StatusBadge status={inv.status} /></TD>
                  <TD>
                    <button
                      type="button"
                      className="text-sm font-medium text-[var(--series-1)] hover:underline"
                      onClick={() => setPdfFor(inv)}
                    >
                      Preview
                    </button>
                  </TD>
                </TR>
              ))}
              {invoices.length === 0 && <TR><TD className="text-[var(--text-muted)]">No invoices yet.</TD></TR>}
            </tbody>
          </Table>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Payments</h2>
          <Table>
            <THead>
              <tr>
                <TH>Date</TH>
                <TH>Amount</TH>
                <TH>Method</TH>
                <TH>Received by</TH>
              </tr>
            </THead>
            <tbody>
              {payments.map((p) => (
                <TR key={p.id}>
                  <TD>{new Date(p.date).toLocaleDateString()}</TD>
                  <TD className="tabular-nums">R {parseFloat(p.amount).toFixed(2)}</TD>
                  <TD className="capitalize">{p.method.replace("_", " ")}</TD>
                  <TD>{p.received_by_name ?? "—"}</TD>
                </TR>
              ))}
              {payments.length === 0 && <TR><TD className="text-[var(--text-muted)]">No payments yet.</TD></TR>}
            </tbody>
          </Table>

          <h2 className="mb-2 mt-6 text-sm font-semibold">Tickets</h2>
          <Table>
            <THead>
              <tr>
                <TH>Ticket</TH>
                <TH>Subject</TH>
                <TH>Status</TH>
                <TH>Priority</TH>
              </tr>
            </THead>
            <tbody>
              {tickets.map((t) => (
                <TR key={t.id}>
                  <TD><Link to={`/admin/tickets/${t.id}`} className="text-[var(--series-1)] hover:underline">{t.ticket_number}</Link></TD>
                  <TD>{t.subject}</TD>
                  <TD><StatusBadge status={t.status} /></TD>
                  <TD><StatusBadge status={t.priority} /></TD>
                </TR>
              ))}
              {tickets.length === 0 && <TR><TD className="text-[var(--text-muted)]">No tickets yet.</TD></TR>}
            </tbody>
          </Table>
        </>
      )}

      {tab === "billing" && (
        <div>
          {billingLoading ? (
            <p className="text-[var(--text-muted)]">Loading…</p>
          ) : billingLoadFailed || !billingConfig ? (
            <p className="text-[var(--text-muted)]">
              Billing configuration isn't available for your account — this needs Finance section access.
            </p>
          ) : (
            <form onSubmit={handleSaveBilling} className="max-w-2xl">
              <div className="mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={billingEnabled} onChange={(e) => setBillingEnabled(e.target.checked)} />
                  <span className="font-medium text-[var(--text-primary)]">
                    Enable recurring invoicing/reminders/auto-suspension for this customer
                  </span>
                </label>
                <p className="mt-1 text-xs text-[var(--text-muted)]">
                  Off by default for every customer — turning this on is always a specific, deliberate choice, never a
                  side effect of a global settings change.
                </p>
              </div>

              <div className={`mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5 ${billingEnabled ? "" : "opacity-60"}`}>
                <h2 className="mb-3 text-sm font-semibold text-[var(--text-primary)]">Billing address override</h2>
                <p className="mb-3 text-xs text-[var(--text-muted)]">
                  Leave any field blank to use this customer's own address/city on finance documents instead.
                </p>
                <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
                  <FormField label="Billing name">
                    <input
                      className={inputClass}
                      value={billingAddressForm.billing_name}
                      onChange={(e) => setBillingAddressForm({ ...billingAddressForm, billing_name: e.target.value })}
                    />
                  </FormField>
                  <FormField label="Street">
                    <input
                      className={inputClass}
                      value={billingAddressForm.billing_street}
                      onChange={(e) => setBillingAddressForm({ ...billingAddressForm, billing_street: e.target.value })}
                    />
                  </FormField>
                  <FormField label="Zip / postal code">
                    <input
                      className={inputClass}
                      value={billingAddressForm.billing_zip}
                      onChange={(e) => setBillingAddressForm({ ...billingAddressForm, billing_zip: e.target.value })}
                    />
                  </FormField>
                  <FormField label="City">
                    <input
                      className={inputClass}
                      value={billingAddressForm.billing_city}
                      onChange={(e) => setBillingAddressForm({ ...billingAddressForm, billing_city: e.target.value })}
                    />
                  </FormField>
                </div>
              </div>

              <div className={`mb-6 rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5 ${billingEnabled ? "" : "opacity-60"}`}>
                <div className="mb-3 flex items-center justify-between">
                  <h2 className="text-sm font-semibold text-[var(--text-primary)]">Billing cycle</h2>
                  {currentUser?.role === "admin" && (
                    <button type="button" className={btnSecondary} disabled={resettingDefaults} onClick={handleResetBillingDefaults}>
                      {resettingDefaults ? "Loading…" : "Reset to defaults"}
                    </button>
                  )}
                </div>
                <RecurringBillingFieldsFormBody form={billingForm} onChange={setBillingForm} paymentMethods={paymentMethods} />
              </div>

              {billingConfig.next_billing_date && (
                <p className="mb-4 text-xs text-[var(--text-muted)]">
                  Next scheduled bill: {new Date(billingConfig.next_billing_date).toLocaleDateString()}
                </p>
              )}

              {billingError && <p className="mb-3 text-sm text-[var(--status-critical)]">{billingError}</p>}
              {billingSaved && !billingError && <p className="mb-3 text-sm text-[#0ca30c]">Billing config saved.</p>}

              <div className="flex justify-end">
                <button type="submit" disabled={billingSaving} className={btnPrimary}>
                  {billingSaving ? "Saving…" : "Save billing config"}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {tab === "email" && (
        <div className="space-y-6">
          <div className="max-w-md rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
            {customer.email ? (
              <>
                <p className="text-sm text-[var(--text-muted)]">Email address</p>
                <p className="mt-1 mb-4 text-lg font-medium text-[var(--text-primary)]">{customer.email}</p>
                <div className="flex gap-2">
                  <a href={`mailto:${customer.email}`} className={btnPrimary}>
                    Send email
                  </a>
                  <button type="button" className={btnSecondary} onClick={copyEmail}>
                    {copied ? "Copied!" : "Copy address"}
                  </button>
                </div>
              </>
            ) : (
              <p className="text-sm text-[var(--text-muted)]">
                No email address on file for this customer.{" "}
                <button type="button" className="text-[var(--series-1)] hover:underline" onClick={openEdit}>
                  Add one
                </button>
                .
              </p>
            )}
          </div>

          {customer.email && (
            <div className="max-w-2xl rounded-lg border border-[var(--border-hairline)] bg-[var(--surface-1)] p-5">
              <h2 className="mb-3 text-sm font-semibold">Send a templated email</h2>
              <div className="flex flex-wrap items-end gap-3">
                <FormField label="Template">
                  <select
                    className={inputClass}
                    value={sendTemplate}
                    onChange={(e) => selectTemplate(e.target.value as EmailTemplateKey | "")}
                  >
                    <option value="">Choose a template…</option>
                    {TEMPLATE_OPTIONS.map((t) => (
                      <option key={t.key} value={t.key}>{t.label}</option>
                    ))}
                  </select>
                </FormField>
                {sendTemplate && DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) && (
                  <FormField label={sendTemplate === "quote" ? "Quote" : sendTemplate === "proforma" ? "Pro forma invoice" : "Invoice"}>
                    <select
                      className={inputClass}
                      value={sendInvoiceId}
                      onChange={(e) => {
                        setSendInvoiceId(e.target.value);
                        setPreview(null);
                      }}
                    >
                      <option value="">Choose one…</option>
                      {invoices
                        .filter((inv) =>
                          sendTemplate === "invoice" ? inv.status !== "quote" && inv.status !== "proforma" : inv.status === sendTemplate
                        )
                        .map((inv) => (
                          <option key={inv.id} value={inv.id}>{inv.number} — R {parseFloat(inv.total).toFixed(2)}</option>
                        ))}
                    </select>
                  </FormField>
                )}
                <button
                  type="button"
                  className={btnSecondary}
                  disabled={!sendTemplate || (DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) && !sendInvoiceId) || previewing}
                  onClick={handlePreview}
                >
                  {previewing ? "Loading…" : "Preview email"}
                </button>
                {/* The email text and the PDF it carries are two different
                    things to check, so they get two buttons. This one is
                    only offered for the templates that actually attach a
                    document. */}
                {attachmentPreview && (
                  <button
                    type="button"
                    className={btnSecondary}
                    onClick={() => {
                      if (attachmentPreview.kind === "statement") setShowStatementPdf(true);
                      else setPdfFor(attachmentPreview.invoice);
                    }}
                  >
                    Preview PDF
                  </button>
                )}
                <button
                  type="button"
                  className={btnPrimary}
                  disabled={!sendTemplate || (DOCUMENT_TEMPLATE_KEYS.includes(sendTemplate) && !sendInvoiceId) || sending}
                  onClick={handleSend}
                >
                  {sending ? "Sending…" : "Send"}
                </button>
              </div>

              {sendResult && (
                <p className={`mt-3 text-sm ${sendResult.ok ? "text-[var(--status-good)]" : "text-[var(--status-critical)]"}`}>
                  {sendResult.message}
                </p>
              )}

              {preview && (
                <div className="mt-4 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-4">
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Subject</p>
                  <p className="mb-3 font-medium text-[var(--text-primary)]">{preview.subject}</p>
                  <p className="text-xs uppercase tracking-wide text-[var(--text-muted)]">Body</p>
                  <div
                    className="mt-1 rounded bg-[var(--surface-1)] p-3 text-sm text-[var(--text-primary)]"
                    dangerouslySetInnerHTML={{ __html: preview.body_html }}
                  />
                  {preview.will_attach_pdf && (
                    <p className="mt-2 text-xs text-[var(--text-muted)]">
                      A PDF will be attached to this email.{" "}
                      {attachmentPreview && (
                        <button
                          type="button"
                          className="font-medium text-[var(--series-1)] hover:underline"
                          onClick={() => {
                            if (attachmentPreview.kind === "statement") setShowStatementPdf(true);
                            else setPdfFor(attachmentPreview.invoice);
                          }}
                        >
                          See it
                        </button>
                      )}
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          <div>
            <h2 className="mb-2 text-sm font-semibold">Sent emails</h2>
            <Table>
              <THead>
                <tr>
                  <TH>Date</TH>
                  <TH>Template</TH>
                  <TH>Subject</TH>
                  <TH>Status</TH>
                </tr>
              </THead>
              <tbody>
                {emailLogs.map((log) => (
                  <TR key={log.id}>
                    <TD>{new Date(log.created_at).toLocaleString()}</TD>
                    <TD>{log.template_name}</TD>
                    <TD className="max-w-xs truncate">
                      <span title={log.status === "failed" ? log.error_message : log.subject}>
                        {log.subject || <span className="text-[var(--text-muted)]">—</span>}
                      </span>
                    </TD>
                    <TD>
                      <StatusBadge status={log.status} />
                      {log.status === "failed" && log.error_message && (
                        <span className="ml-2 text-xs text-[var(--text-muted)]">{log.error_message}</span>
                      )}
                    </TD>
                  </TR>
                ))}
                {emailLogs.length === 0 && <TR><TD className="text-[var(--text-muted)]">No emails sent to this customer yet.</TD></TR>}
              </tbody>
            </Table>
          </div>
        </div>
      )}

      {tab === "history" && <CustomerHistoryTab customerId={customer.id} />}

      {showEdit && (
        <Modal title="Edit customer" onClose={() => setShowEdit(false)}>
          <form onSubmit={handleSave}>
            <FormField label="Full name">
              <input
                className={inputClass}
                required
                value={form.full_name ?? ""}
                onChange={(e) => setForm({ ...form, full_name: e.target.value })}
              />
            </FormField>
            {/* The payment reference. Editable so a customer migrated from
                another system can keep the reference they already put on
                their EFTs -- that string is what bank-feed matching looks
                for, so an auto-generated one they never type means their
                payments never match. */}
            <FormField label="Payment reference">
              <input
                className={inputClass}
                value={form.customer_id ?? ""}
                onChange={(e) => setForm({ ...form, customer_id: e.target.value })}
              />
            </FormField>
            <p className="-mt-2 mb-3 text-xs text-[var(--text-muted)]">
              What this customer types as the reference on their EFT. It's printed on their statement
              and it's what matches their payments in Bank Feeds.
              {form.customer_id !== customer.customer_id && (
                <span className="mt-1 block font-medium text-[#b3852e]">
                  Changing this from <span className="font-mono">{customer.customer_id}</span> means
                  payments still sent with the old reference will stop matching automatically — they'll
                  land in the review queue to be assigned by hand.
                </span>
              )}
            </p>
            <FormField label="Company name">
              <input
                className={inputClass}
                value={form.company_name ?? ""}
                onChange={(e) => setForm({ ...form, company_name: e.target.value })}
              />
            </FormField>
            <FormField label="Email">
              <input
                type="email"
                className={inputClass}
                value={form.email ?? ""}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
              />
            </FormField>
            <FormField label="Phone">
              <input
                className={inputClass}
                value={form.phone ?? ""}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
              />
            </FormField>
            <FormField label="Address">
              <input
                className={inputClass}
                value={form.address ?? ""}
                onChange={(e) => setForm({ ...form, address: e.target.value })}
              />
            </FormField>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="City">
                <input
                  className={inputClass}
                  value={form.city ?? ""}
                  onChange={(e) => setForm({ ...form, city: e.target.value })}
                />
              </FormField>
              <FormField label="Zip code">
                <input
                  className={inputClass}
                  value={form.zip_code ?? ""}
                  onChange={(e) => setForm({ ...form, zip_code: e.target.value })}
                />
              </FormField>
            </div>
            {/* Both printed on the customer's side of their tax invoice. Left
                blank the invoice still prints the labels with nothing after
                them, matching the document these replace. */}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="ID / registration number">
                <input
                  className={inputClass}
                  value={form.id_number ?? ""}
                  onChange={(e) => setForm({ ...form, id_number: e.target.value })}
                  placeholder="ID number, or company reg. no."
                />
              </FormField>
              <FormField label="VAT number">
                <input
                  className={inputClass}
                  value={form.vat_number ?? ""}
                  onChange={(e) => setForm({ ...form, vat_number: e.target.value })}
                  placeholder="Their own VAT registration, if any"
                />
              </FormField>
            </div>
            <p className="-mt-1 mb-1 text-xs text-[var(--text-muted)]">
              Both appear on this customer's tax invoices. Leave blank for a residential customer.
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Customer type">
                <select
                  className={inputClass}
                  value={form.customer_type ?? "individual"}
                  onChange={(e) => setForm({ ...form, customer_type: e.target.value as Customer["customer_type"] })}
                >
                  <option value="individual">Individual</option>
                  <option value="company">Company</option>
                </select>
              </FormField>
              <FormField label="Category">
                <select
                  className={inputClass}
                  value={form.category ?? "residential"}
                  onChange={(e) => setForm({ ...form, category: e.target.value as Customer["category"] })}
                >
                  <option value="residential">Residential</option>
                  <option value="business">Business</option>
                </select>
              </FormField>
            </div>
            {form.status === "suspended" && customer?.status !== "suspended" && (
              <p className="mb-2 rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
                Saving this suspends the customer's active services and disconnects them from the router — they lose
                internet straight away, not at their next reconnection. Setting them back to Active restores only the
                services this took down, so anything suspended for non-payment stays suspended.
              </p>
            )}
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <FormField label="Status">
                <select
                  className={inputClass}
                  value={form.status ?? "new"}
                  onChange={(e) => setForm({ ...form, status: e.target.value as Customer["status"] })}
                >
                  <option value="new">New</option>
                  <option value="active">Active</option>
                  <option value="suspended">Suspended</option>
                  <option value="bad_debt">Bad Debt</option>
                  <option value="inactive">Inactive</option>
                </select>
              </FormField>
              <FormField label="Assigned staff">
                <select
                  className={inputClass}
                  value={form.assigned_staff ?? ""}
                  onChange={(e) => setForm({ ...form, assigned_staff: e.target.value ? Number(e.target.value) : null })}
                >
                  <option value="">Unassigned</option>
                  {staff.map((s) => (
                    <option key={s.id} value={s.id}>{s.first_name || s.username}</option>
                  ))}
                </select>
              </FormField>
              {accessiblePartners.length > 0 && (
                <FormField label="Partner (reselling)">
                  <select
                    className={inputClass}
                    value={form.partner ?? ""}
                    onChange={(e) => setForm({ ...form, partner: e.target.value ? Number(e.target.value) : null })}
                  >
                    <option value="">Direct (no partner)</option>
                    {accessiblePartners.map((p) => (
                      <option key={p.id} value={p.id}>{p.name}</option>
                    ))}
                  </select>
                </FormField>
              )}
            </div>
            <FormField label="Notes">
              <textarea
                className={inputClass}
                rows={3}
                value={form.notes ?? ""}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </FormField>

            {error && <p className="mb-3 text-sm text-[var(--status-critical)]">{error}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowEdit(false)}>
                Cancel
              </button>
              <button type="submit" disabled={saving} className={btnPrimary}>
                {saving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showNewService && (
        <Modal title="New service" onClose={() => setShowNewService(false)}>
          <form onSubmit={handleNewServiceSubmit}>
            <FormField label="Tariff">
              <select
                className={inputClass}
                required
                value={newServiceForm.tariff}
                onChange={(e) => setNewServiceForm({ ...newServiceForm, tariff: e.target.value })}
              >
                <option value="">Select tariff…</option>
                {tariffs.map((t) => (
                  <option key={t.id} value={t.id}>{t.name} — R{t.price}</option>
                ))}
              </select>
            </FormField>
            <FormField label="Status">
              <select
                className={inputClass}
                value={newServiceForm.status}
                onChange={(e) => setNewServiceForm({ ...newServiceForm, status: e.target.value as Service["status"] })}
              >
                <option value="pending">Pending Activation</option>
                <option value="active">Active</option>
                <option value="suspended">Suspended</option>
                <option value="terminated">Terminated</option>
              </select>
            </FormField>

            <ServiceConnectionFields
              values={newServiceForm}
              onChange={(patch) => setNewServiceForm({ ...newServiceForm, ...patch })}
              customerPools={customerPools}
              poolAddresses={newPoolAddresses}
            />

            <ServiceShapingFields
              values={newServiceForm}
              onChange={(patch) => setNewServiceForm({ ...newServiceForm, ...patch })}
              devices={devices}
              connectionRules={connectionRules}
            />

            {newServiceError && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{newServiceError}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowNewService(false)}>Cancel</button>
              <button type="submit" disabled={newServiceSaving} className={btnPrimary}>
                {newServiceSaving ? "Saving…" : "Create service"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {editingService && (
        <Modal title={`Edit service — ${editingService.tariff_name}`} onClose={() => setEditingService(null)}>
          <form onSubmit={handleEditServiceSubmit}>
            {/* One tariff picker and one question about WHEN, rather than
                two separate controls that can contradict each other.

                The old shape had "Tariff (applies immediately)" above a
                "Schedule a tariff change" box, and it read as though the box
                was where tariff changes were made -- so the only apparent way
                to move somebody was to pick a date, and the earliest date
                that felt safe was tomorrow. Nobody should have to infer that
                editing the field above is the immediate path. */}
            <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3">
              <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">Tariff</p>

              <FormField label="Plan">
                <select
                  className={inputClass}
                  required
                  value={tariffWhen === "now" ? serviceEditForm.tariff : serviceEditForm.pending_tariff}
                  onChange={(e) =>
                    setServiceEditForm(
                      tariffWhen === "now"
                        ? { ...serviceEditForm, tariff: e.target.value, pending_tariff: "", pending_tariff_date: "" }
                        : { ...serviceEditForm, pending_tariff: e.target.value }
                    )
                  }
                >
                  {tariffWhen === "later" && <option value="">Choose a plan…</option>}
                  {tariffs.map((t) => (
                    <option key={t.id} value={t.id}>{t.name} — R{t.price}</option>
                  ))}
                </select>
              </FormField>

              <div className="mt-2 flex flex-wrap items-center gap-4">
                <label className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                  <input
                    type="radio"
                    name="tariff-when"
                    checked={tariffWhen === "now"}
                    onChange={() =>
                      setServiceEditForm({
                        ...serviceEditForm,
                        // Picking "now" cancels any booking, so the two can
                        // never both be live and disagree about the plan.
                        pending_tariff: "",
                        pending_tariff_date: "",
                      })
                    }
                  />
                  Change now
                </label>
                <label className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                  <input
                    type="radio"
                    name="tariff-when"
                    checked={tariffWhen === "later"}
                    onChange={() =>
                      setServiceEditForm({
                        ...serviceEditForm,
                        // Deliberately blank, not seeded with the current
                        // tariff: booking somebody onto the plan they are
                        // already on is rejected by the backend, so seeding it
                        // would hand them a form that looks ready and isn't.
                        // Blank + `required` makes the browser ask first.
                        pending_tariff: "",
                        pending_tariff_date: serviceEditForm.pending_tariff_date || todayIso(),
                      })
                    }
                  />
                  On a date
                </label>
                {tariffWhen === "later" && (
                  <input
                    type="date"
                    className={`${inputClass} w-auto`}
                    // Today is a valid answer -- "change it now and bill this
                    // period on the new plan" is a real request, and the
                    // backend accepts it.
                    min={todayIso()}
                    value={serviceEditForm.pending_tariff_date}
                    onChange={(e) =>
                      setServiceEditForm({ ...serviceEditForm, pending_tariff_date: e.target.value })
                    }
                  />
                )}
              </div>

              <p className="mt-2 text-xs text-[var(--text-muted)]">
                {tariffWhen === "now" ? (
                  <>
                    Takes effect as soon as you save: the new speed goes to the router on the customer's live
                    session — <strong>without disconnecting them</strong> — and the next invoice bills the new price.
                  </>
                ) : (
                  <>
                    The service switches over on its own that morning. <strong>Not prorated</strong>: pick the first
                    day of a billing period, or the whole period bills at the new price. Switch back to
                    <em> Change now</em> to cancel a booking.
                  </>
                )}
              </p>

              {editingService.pending_tariff_name && (
                <p className="mt-2 text-xs text-[var(--series-1)]">
                  Currently booked: <strong>{editingService.pending_tariff_name}</strong> from{" "}
                  <strong>{editingService.pending_tariff_date}</strong>.
                </p>
              )}
            </div>

            <FormField label="Status">
              <select
                className={inputClass}
                value={serviceEditForm.status}
                onChange={(e) => setServiceEditForm({ ...serviceEditForm, status: e.target.value as Service["status"] })}
              >
                <option value="pending">Pending Activation</option>
                <option value="active">Active</option>
                <option value="suspended">Suspended</option>
                <option value="terminated">Terminated</option>
              </select>
            </FormField>

            <div className="mb-3 rounded-md border border-[var(--border-hairline)] bg-[var(--tint-subtle)] p-3">
              <FormField label="Billing end date (leave blank for no end)">
                <input
                  type="date"
                  className={inputClass}
                  value={serviceEditForm.end_date}
                  onChange={(e) => setServiceEditForm({ ...serviceEditForm, end_date: e.target.value })}
                />
              </FormField>
              <p className="mt-1 text-xs text-[var(--text-muted)]">
                The service stops at <strong>00:00 on this date</strong> — they have service through
                the end of the day before. On the day it arrives the service is terminated, their
                RADIUS login stops working and their session is dropped. If it's their only service,
                the customer is set to <strong>Cancelled</strong>.
              </p>
              {serviceEditForm.end_date && (
                <p className="mt-2 text-xs text-[var(--status-warning)]">
                  {new Date(`${serviceEditForm.end_date}T00:00:00`) <= new Date()
                    ? "That date has already passed — this will be cancelled on the next nightly run, or immediately if one is due."
                    : `Last full day of service: ${new Date(
                        new Date(`${serviceEditForm.end_date}T00:00:00`).getTime() - 86400000
                      ).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" })}.`}
                </p>
              )}
            </div>

            {/* Said before the save, not discovered after it: the customer
                badge moving on its own is surprising unless you were told it
                would. Only shown when it will actually happen. */}
            {serviceEditForm.status === "active" &&
              editingService.status !== "active" &&
              customer?.status === "suspended" && (
                <p className="rounded-md border border-[var(--status-warning)] bg-[#fff6e5] p-2 text-xs text-[#a5730a]">
                  This customer is marked <strong>Suspended</strong>. Bringing this service back also sets the
                  customer to Active, so the badge at the top of the page stops contradicting the line below it.
                  Their other suspended services are left alone.
                </p>
              )}

            <ServiceConnectionFields
              values={serviceEditForm}
              onChange={(patch) => setServiceEditForm({ ...serviceEditForm, ...patch })}
              customerPools={customerPools}
              poolAddresses={poolAddresses}
              passwordIsSet={editingService.radius_password_set}
              currentServiceId={editingService.id}
              currentAssignedIp={editingService.assigned_ip}
            />

            <ServiceShapingFields
              values={serviceEditForm}
              onChange={(patch) => setServiceEditForm({ ...serviceEditForm, ...patch })}
              devices={devices}
              connectionRules={connectionRules}
            />

            {serviceEditForm.device && <LiveBandwidthWidget serviceId={editingService.id} />}

            {serviceEditError && <p className="mt-3 text-sm text-red-700 dark:text-red-300">{serviceEditError}</p>}

            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setEditingService(null)}>Cancel</button>
              <button type="submit" disabled={serviceEditSaving} className={btnPrimary}>
                {serviceEditSaving ? "Saving…" : "Save changes"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showRequestDeletion && (
        <Modal title="Request customer deletion" onClose={() => setShowRequestDeletion(false)}>
          <form onSubmit={handleRequestDeletion}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Deleting {customer.full_name} removes ALL of their services, invoices, payments, credit requests,
              tickets, and email history. Because it's irreversible, a Management (or Admin) user must approve it
              before anything is actually deleted.
            </p>
            <FormField label="Reason">
              <textarea
                className={inputClass}
                rows={3}
                required
                value={deletionReason}
                onChange={(e) => setDeletionReason(e.target.value)}
                placeholder="e.g. Duplicate record, customer requested account closure, non-payment write-off…"
              />
            </FormField>
            {deletionError && <p className="mb-3 text-sm text-red-700 dark:text-red-300">{deletionError}</p>}
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setShowRequestDeletion(false)}>Cancel</button>
              <button type="submit" disabled={deletionSaving} className={btnPrimary}>
                {deletionSaving ? "Submitting…" : "Submit request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {rejectingDeletion && (
        <Modal title="Reject deletion request" onClose={() => setRejectingDeletion(false)}>
          <form onSubmit={handleRejectDeletion}>
            <p className="mb-3 text-sm text-[var(--text-secondary)]">
              Rejecting the deletion request for {customer.full_name}. They'll remain on the platform.
            </p>
            <FormField label="Note (optional)">
              <input
                className={inputClass}
                value={rejectDecisionNote}
                onChange={(e) => setRejectDecisionNote(e.target.value)}
              />
            </FormField>
            <div className="mt-4 flex justify-end gap-2">
              <button type="button" className={btnSecondary} onClick={() => setRejectingDeletion(false)}>Cancel</button>
              <button type="submit" className={btnPrimary} disabled={deletionBusy}>
                {deletionBusy ? "Rejecting…" : "Reject request"}
              </button>
            </div>
          </form>
        </Modal>
      )}

      {pdfFor && (
        <PdfPreviewModal
          title={`${pdfFor.number} — ${docNounFor(pdfFor.status)}`}
          url={`/invoices/${pdfFor.id}/pdf/`}
          filename={`${pdfFor.number}.pdf`}
          onClose={() => setPdfFor(null)}
        />
      )}

      {showStatementPdf && (
        <PdfPreviewModal
          title={`Statement — ${customer.full_name}`}
          url={`/customers/${customer.id}/statement/pdf/`}
          filename={`Statement-${customer.customer_id}.pdf`}
          onClose={() => setShowStatementPdf(false)}
        />
      )}
    </div>
  );
}
