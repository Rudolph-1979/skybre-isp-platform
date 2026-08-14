import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AdminLayout } from "./components/AdminLayout";
import { PortalLayout } from "./components/PortalLayout";
import { LoginPage } from "./pages/LoginPage";

import { DashboardPage } from "./pages/admin/DashboardPage";
import { SchedulingPage } from "./pages/admin/SchedulingPage";
import { CustomersPage } from "./pages/admin/CustomersPage";
import { CustomerDetailPage } from "./pages/admin/CustomerDetailPage";
import { TariffsPage } from "./pages/admin/TariffsPage";
import { ServicesPage } from "./pages/admin/ServicesPage";
import { InvoicesPage } from "./pages/admin/InvoicesPage";
import { InvoiceDetailPage } from "./pages/admin/InvoiceDetailPage";
import { PaymentsPage } from "./pages/admin/PaymentsPage";
import { InventoryPage } from "./pages/admin/InventoryPage";
import { AccountSettingsPage } from "./pages/admin/AccountSettingsPage";
import { DevicesPage } from "./pages/admin/DevicesPage";
import { DeviceDetailPage } from "./pages/admin/DeviceDetailPage";
import { IPPoolsPage } from "./pages/admin/IPPoolsPage";
import { TicketsPage } from "./pages/admin/TicketsPage";
import { TicketDetailPage } from "./pages/admin/TicketDetailPage";
import { EmailTemplatesPage } from "./pages/admin/EmailTemplatesPage";
import { BulkEmailPage } from "./pages/admin/BulkEmailPage";

import { PortalDashboard } from "./pages/portal/PortalDashboard";
import { PortalInvoices } from "./pages/portal/PortalInvoices";
import { PortalTickets } from "./pages/portal/PortalTickets";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<LoginPage />} />

          <Route
            path="/admin"
            element={
              <ProtectedRoute staffOnly>
                <AdminLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="scheduling" element={<SchedulingPage />} />
            <Route path="customers" element={<CustomersPage />} />
            <Route path="customers/:id" element={<CustomerDetailPage />} />
            <Route path="services" element={<ServicesPage />} />
            <Route path="tariffs" element={<TariffsPage />} />
            <Route path="invoices" element={<InvoicesPage />} />
            <Route path="invoices/:id" element={<InvoiceDetailPage />} />
            <Route path="payments" element={<PaymentsPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="account" element={<AccountSettingsPage />} />
            <Route path="devices" element={<DevicesPage />} />
            <Route path="devices/:id" element={<DeviceDetailPage />} />
            <Route path="ip-pools" element={<IPPoolsPage />} />
            <Route path="tickets" element={<TicketsPage />} />
            <Route path="tickets/:id" element={<TicketDetailPage />} />
            <Route path="email-templates" element={<EmailTemplatesPage />} />
            <Route path="bulk-email" element={<BulkEmailPage />} />
          </Route>

          <Route
            path="/portal"
            element={
              <ProtectedRoute>
                <PortalLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<PortalDashboard />} />
            <Route path="invoices" element={<PortalInvoices />} />
            <Route path="tickets" element={<PortalTickets />} />
          </Route>

          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
