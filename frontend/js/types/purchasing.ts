import { PurchaseTaxTreatment } from './inventory'

type PurchasingStatus = 'draft' | 'confirmed' | 'closed' | 'cancelled'
type RequisitionStatus = 'draft' | 'reviewed' | 'ordered' | 'cancelled'

interface PurchaseRequisition {
  pk: number
  item: number
  required_on: string
  quantity: string
  unit_code: string
  preferred_supplier: number | null
  estimated_total_incl_tax: string
  status: RequisitionStatus
  notes: string
}

interface PurchaseOrderLineState {
  ordered: string
  received: string
  cancelled: string
  returned: string
  outstanding: string
  over_received: string
}

interface PurchaseOrderLine {
  pk: number
  item: number
  requisition: number | null
  description: string
  quantity: string
  unit_code: string
  base_quantity: string
  unit_price_ex_tax: string
  tax_rate: string
  freight_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  state: PurchaseOrderLineState
}

interface PurchaseOrder {
  pk: number
  order_number: string
  supplier: number
  supplier_name: string
  status: PurchasingStatus
  ordered_on: string
  expected_on: string | null
  currency_code: string
  subtotal_ex_tax: string
  freight_ex_tax: string
  tax_total: string
  total_incl_tax: string
  notes: string
  lines: Array<PurchaseOrderLine>
}

interface SupplierInvoiceLine {
  pk: number
  description: string
  purchase_order_line: number | null
  receipt_line: number | null
  expense_category: number | null
  is_freight: boolean
  subtotal_ex_tax: string
  tax_rate: string
  tax_total: string
  total_incl_tax: string
}

interface SupplierInvoiceState {
  net_total: string
  paid_total: string
  balance_due: string
  payment_state: 'unpaid' | 'part_paid' | 'paid'
  warnings: Array<string>
}

interface SupplierInvoice {
  pk: number
  supplier: number
  supplier_name: string
  purchase_order: number | null
  external_reference: string
  invoice_date: string
  due_date: string | null
  currency_code: string
  status: 'draft' | 'confirmed' | 'cancelled'
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  attachment_url: string
  notes: string
  lines: Array<SupplierInvoiceLine>
  state: SupplierInvoiceState
}

interface ExpenseCategory {
  pk: number
  name: string
  active: boolean
  notes: string
}

interface BusinessExpense {
  pk: number
  category: number
  supplier: number | null
  payee: string
  incurred_on: string
  currency_code: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  tax_treatment: PurchaseTaxTreatment
  claim_input_tax: boolean
  claimable_percentage: string
  apportionment_basis: string
  recoverable_tax: string
  deductible_amount: string
  supplier_invoice: number | null
  paid_on: string | null
  payment_state: 'paid' | 'unpaid' | 'part_paid'
  status: 'draft' | 'confirmed' | 'cancelled'
  allocation_type: string
  allocation_reference: string
  attachment_url: string
  notes: string
}

interface PurchasingWarning {
  code: string
  source_type: string
  source_id: number
  message: string
}

interface PurchasingSummary {
  as_of: string
  requisitions: Record<RequisitionStatus, number>
  committed_spend: string
  invoices: Array<{ invoice: number; reference: string; supplier: string; due_date: string | null; balance_due: string; payment_state: string }>
  overdue_invoices: Array<{ invoice: number; reference: string; supplier: string; due_date: string | null; balance_due: string }>
  cash_paid: string
  expenses: { subtotal_ex_tax: string; tax_total: string; total_incl_tax: string }
  warnings: Array<PurchasingWarning>
}

export { BusinessExpense, ExpenseCategory, PurchaseOrder, PurchaseOrderLine, PurchaseRequisition, PurchasingSummary, SupplierInvoice }
