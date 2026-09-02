import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./context/AuthContext";
import { ViewAsProvider } from "./context/ViewAsContext";
import { ProtectedRoute } from "./components/ProtectedRoute";
import { AdminLayout } from "./components/AdminLayout";
import { SectionRoute } from "./components/SectionRoute";
import { PortalLayout } from "./components/PortalLayout";
import { LoginPage } from "./pages/LoginPage";
import { ForgotPasswordPage } from "./pages/ForgotPasswordPage";
import { ResetPasswordPage } from "./pages/ResetPasswordPage";
import { PublicUsagePage } from "./pages/PublicUsagePage";

import { DashboardPage } from "./pages/admin/DashboardPage";
import { SchedulingPage } from "./pages/admin/SchedulingPage";
import { CustomersPage } from "./pages/admin/CustomersPage";
import { LeadsPage } from "./pages/admin/LeadsPage";
import { LeadDetailPage } from "./pages/admin/LeadDetailPage";
import { UsageReportPage } from "./pages/admin/UsageReportPage";
import { OfflineCustomersPage } from "./pages/admin/OfflineCustomersPage";
import { CustomerDetailPage } from "./pages/admin/CustomerDetailPage";
import { ConfigsPage } from "./pages/admin/ConfigsPage";
import { ServicesPage } from "./pages/admin/ServicesPage";
import { FinancePage } from "./pages/admin/FinancePage";
import { InvoiceDetailPage } from "./pages/admin/InvoiceDetailPage";
import { InventoryPage } from "./pages/admin/InventoryPage";
import { AccountSettingsPage } from "./pages/admin/AccountSettingsPage";
import { NetworkingPage } from "./pages/admin/NetworkingPage";
import { DeviceDetailPage } from "./pages/admin/DeviceDetailPage";
import { TicketsPage } from "./pages/admin/TicketsPage";
import { TicketDetailPage } from "./pages/admin/TicketDetailPage";
import { BulkEmailPage } from "./pages/admin/BulkEmailPage";
import { AccountantPage } from "./pages/admin/AccountantPage";
import { VehiclesPage } from "./pages/admin/VehiclesPage";
import { VehicleDetailPage } from "./pages/admin/VehicleDetailPage";

import { PortalDashboard } from "./pages/portal/PortalDashboard";
import { PortalInvoices } from "./pages/portal/PortalInvoices";
import { PortalTickets } from "./pages/portal/PortalTickets";

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <ViewAsProvider>
        <Routes>
          <Route path="/" element={<Navigate to="/login" replace />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password/:uid/:token" element={<ResetPasswordPage />} />
          {/* Public on purpose: customers open this from a link we send
              them, with no account. The token in the path IS the
              credential -- see PublicUsageView on the backend for what
              that means for what this page is allowed to show. */}
          <Route path="/usage/:token" element={<PublicUsagePage />} />

          <Route
            path="/admin"
            element={
              <ProtectedRoute staffOnly>
                <AdminLayout />
              </ProtectedRoute>
            }
          >
            <Route index element={<DashboardPage />} />
            <Route path="scheduling" element={<SectionRoute section="scheduling"><SchedulingPage /></SectionRoute>} />
            <Route path="leads" element={<SectionRoute section="sales"><LeadsPage /></SectionRoute>} />
            <Route path="leads/:id" element={<SectionRoute section="sales"><LeadDetailPage /></SectionRoute>} />
            <Route path="customers" element={<SectionRoute section="customers"><CustomersPage /></SectionRoute>} />
            <Route path="usage-report" element={<SectionRoute section="customers"><UsageReportPage /></SectionRoute>} />
            <Route path="offline-customers" element={<SectionRoute section="customers"><OfflineCustomersPage /></SectionRoute>} />
            <Route path="customers/:id" element={<SectionRoute section="customers"><CustomerDetailPage /></SectionRoute>} />
            <Route path="services" element={<SectionRoute section="services"><ServicesPage /></SectionRoute>} />
            <Route path="configs" element={<SectionRoute section="configs"><ConfigsPage /></SectionRoute>} />
            <Route path="finance" element={<SectionRoute section="finance"><FinancePage /></SectionRoute>} />
            <Route path="finance/invoices/:id" element={<SectionRoute section="finance"><InvoiceDetailPage /></SectionRoute>} />
            <Route path="inventory" element={<SectionRoute section="inventory"><InventoryPage /></SectionRoute>} />
            <Route path="account" element={<AccountSettingsPage />} />
            <Route path="networking" element={<SectionRoute section="networking"><NetworkingPage /></SectionRoute>} />
            <Route path="networking/devices/:id" element={<SectionRoute section="networking"><DeviceDetailPage /></SectionRoute>} />
            <Route path="tickets" element={<SectionRoute section="tickets"><TicketsPage /></SectionRoute>} />
            <Route path="tickets/:id" element={<SectionRoute section="tickets"><TicketDetailPage /></SectionRoute>} />
            <Route path="accountant" element={<SectionRoute section="accountant"><AccountantPage /></SectionRoute>} />
            <Route path="vehicles" element={<SectionRoute section="vehicles"><VehiclesPage /></SectionRoute>} />
            <Route path="vehicles/:id" element={<SectionRoute section="vehicles"><VehicleDetailPage /></SectionRoute>} />
            <Route path="bulk-email" element={<SectionRoute section="bulk_email"><BulkEmailPage /></SectionRoute>} />
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
        </ViewAsProvider>
      </AuthProvider>
    </BrowserRouter>
  );
}
