type SupplyTier = 'low' | 'standard' | 'full'
type SupplyDocumentStatus = 'issued' | 'part_credited' | 'credited'
type CorrectionType = 'credit' | 'debit'
type CorrectionReason = 'return' | 'discount' | 'wrong_rate' | 'cancellation' | 'partial_credit' | 'other'

interface SupplyDocumentCoverage {
  commercial_position: number
  fulfillment_line: number | null
}

interface SupplyDocumentLine {
  pk: number
  order_line: number
  description: string
  quantity: number
  unit_price: string
  tax_rate: string
  tax_treatment: string
  gross_ex_tax: string
  discount_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  coverage: Array<SupplyDocumentCoverage>
}

interface SupplyCorrectionLine {
  pk: number
  document_line: number
  quantity: number | null
  tax_rate: string
  tax_treatment: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
}

interface SupplyCorrection {
  pk: number
  document: number
  document_number: string
  correction_type: CorrectionType
  reason_code: CorrectionReason
  reason: string
  corrected_on: string
  sales_return: number | null
  refund: number | null
  currency_code: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  notes: string
  created_by: number | null
  created: string
  lines: Array<SupplyCorrectionLine>
}

interface SupplyDocumentState {
  status: SupplyDocumentStatus
  credited_total: string
  net_total_incl_tax: string
}

interface SupplyDocument {
  pk: number
  document_number: string
  order: number
  order_number: string
  issued_on: string
  taxable_supply: boolean
  tier: SupplyTier
  currency_code: string
  seller_legal_name: string
  seller_trading_name: string
  seller_address: string
  seller_gst_number: string
  seller_registration: number | null
  customer: number | null
  buyer_name: string
  buyer_address: string
  buyer_identifier: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  previously_invoiced: string
  paid_to_date: string
  balance_due: string
  overpaid_at_issue: string
  notes: string
  created_by: number | null
  created: string
  lines: Array<SupplyDocumentLine>
  corrections: Array<SupplyCorrection>
  state: SupplyDocumentState
}

interface InvoiceablePosition {
  position: number
  dispatched: boolean
  total_incl_tax: string
}

interface InvoiceableLine {
  order_line: number
  description: string
  quantity: number
  invoiced_positions: Array<number>
  returned_positions: Array<number>
  positions: Array<InvoiceablePosition>
}

interface Invoiceable {
  order: number
  order_number: string
  currency_code: string
  lines: Array<InvoiceableLine>
}

interface RequiredElement {
  code: string
  label: string
  satisfied: boolean
}

interface PrintableParty {
  legal_name?: string
  trading_name?: string
  name?: string
  address: string
  gst_number?: string
  identifier?: string
}

interface PrintableLine {
  pk: number
  order_line: number
  description: string
  quantity: number
  unit_price: string
  tax_rate: string
  tax_treatment: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  credited_total: string
  positions: Array<number>
  dispatched_positions: Array<number>
}

interface PrintableCorrection {
  pk: number
  document_number: string
  correction_type: CorrectionType
  reason_code: CorrectionReason
  reason: string
  corrected_on: string
  sales_return: number | null
  refund: number | null
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
}

interface PrintableTotals {
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  previously_invoiced: string
  paid_to_date: string
  balance_due: string
  overpaid_at_issue: string
  credited_total: string
  net_total_incl_tax: string
}

interface PrintableDocument {
  pk: number
  document_number: string
  title: string
  taxable_supply: boolean
  issued_on: string
  order: number
  order_number: string
  currency_code: string
  tier: SupplyTier
  tier_label: string
  required_information: Array<RequiredElement>
  notes: string
  lines: Array<PrintableLine>
  corrections: Array<PrintableCorrection>
  totals: PrintableTotals
  status: SupplyDocumentStatus
  issued_by: number | null
  issued_at: string
  seller: PrintableParty
  buyer: PrintableParty
}

interface SupplyDocumentWrite {
  operation_key: string
  order: number
  lines: Array<{ order_line: number; positions: Array<number> }>
  issued_on?: string
  buyer?: { buyer_name?: string; buyer_address?: string; buyer_identifier?: string }
  notes?: string
}

interface SupplyCorrectionWrite {
  operation_key: string
  correction_type: CorrectionType
  reason_code: CorrectionReason
  reason: string
  full?: boolean
  lines?: Array<{ document_line: number; amount: string; quantity?: number | null }>
  corrected_on?: string
  notes?: string
}

export {
  CorrectionReason,
  CorrectionType,
  Invoiceable,
  InvoiceableLine,
  InvoiceablePosition,
  PrintableCorrection,
  PrintableDocument,
  PrintableLine,
  PrintableParty,
  RequiredElement,
  SupplyCorrection,
  SupplyCorrectionLine,
  SupplyCorrectionWrite,
  SupplyDocument,
  SupplyDocumentCoverage,
  SupplyDocumentLine,
  SupplyDocumentState,
  SupplyDocumentStatus,
  SupplyDocumentWrite,
  SupplyTier
}
