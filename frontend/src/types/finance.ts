// Finance → Dashboard (GET /api/finance-dashboard/)
//
// In its own module rather than appended to types/index.ts. That file is
// shared by every page, so a package that ships a whole copy of it
// overwrites whatever else landed there in the meantime -- which is
// exactly how the StockSite types got wiped on the first attempt at this
// deployment. A new file can only ever add.
//
// Money arrives as strings, the way every other decimal in this API does.
// Parsing to a JS number in transit would quietly round the figures this
// page exists to state precisely.

export interface FinanceCountAmount {
  count: number;
  amount: string;
}

// Unpaid invoices carry two amounts because two questions are being asked
// of the same rows: `amount` is what is still OWED (total − paid_amount,
// floored at zero so an overpaid invoice can't subtract from everyone
// else's debt), and `invoiced` is what was BILLED. The tile shows the
// first; Total income uses the second, because part-paying an invoice
// doesn't reduce the revenue that was earned.
export interface FinanceUnpaidAmount extends FinanceCountAmount {
  invoiced: string;
}

export type FinancePeriod = "month" | "year" | "lifetime";

export interface FinancePeriodFigures {
  payments: FinanceCountAmount;
  paid_invoices: FinanceCountAmount;
  unpaid_invoices: FinanceUnpaidAmount;
  credit_notes: FinanceCountAmount;
  paid_proformas: FinanceCountAmount;
  unpaid_proformas: FinanceCountAmount;
  // Accrual, net of credits: paid + unpaid − approved credits. The Payments
  // figure above it is the cash view; having both is the point.
  total_income: string;
}

export interface FinanceOverdueBucket extends FinanceCountAmount {
  label: string;
}

export interface FinanceDashboardData {
  as_of: string;
  // Which period the six tiles cover. The month panels below them are
  // always months regardless.
  period: FinancePeriod;
  // Which partners the figures cover. Empty means ALL of them — the same
  // "empty means unrestricted" convention as allowed_partners and
  // visible_partners elsewhere in the platform.
  partners: number[];
  tiles: FinancePeriodFigures;
  last_month: FinancePeriodFigures;
  current_month: FinancePeriodFigures;
  // No debit/credit transactions ledger exists here, so the forecast is
  // built from active services at their tariff price — the same rule the
  // recurring billing run bills on. Excludes VAT.
  next_month: {
    services: number;
    customers: number;
    planned_income: string;
  };
  overdue: {
    buckets: FinanceOverdueBucket[];
    total: FinanceCountAmount;
  };
  recurring: {
    mrr: string;
    paying_customers: number;
    arpu: string;
  };
}
