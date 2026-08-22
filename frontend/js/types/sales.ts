type Customer = {
  pk: number
  name: string
  email: string
  phone: string
  billing_address: string
  delivery_address: string
  notes: string
  active: boolean
  created: string
  updated: string
}

type SalesOrderStatus = 'quote' | 'draft' | 'confirmed' | 'partially_fulfilled' | 'fulfilled' | 'cancelled'
type SalesLineType = 'seedling' | 'tray'
// What kind of supply a line is for GST. A rate of zero is three different
// things — a zero-rated export, an exempt supply, and something outside GST —
// and a return reports the first separately from the other two.
// 'unclassified' is the honest state for a zero-rated-looking line nobody has
// said which of the three it is; it is never counted as zero-rated by default.
type SalesTaxTreatment = 'standard' | 'zero_rated' | 'exempt' | 'out_of_scope' | 'unclassified'

type SalesDiscountType = 'none' | 'fixed' | 'percentage'
type SalesAllocationStatus = 'pending' | 'reserved' | 'released' | 'expired' | 'fulfilled' | 'returned'
type CommerceStatus = 'posted' | 'reversed' | 'reversal'

interface ReservationEvent {
  pk: number
  event_type: 'reserved' | 'released' | 'expired' | 'cancelled' | 'fulfilled'
  occurred_at: string
  reason: string
  created_by: number | null
  created: string
}

interface SalesAllocation {
  pk: number
  plant: number | null
  inventory_unit: number | null
  asset_code: string | null
  status: SalesAllocationStatus
  expires_at: string | null
  created_by: number | null
  created: string
  updated: string
  events: Array<ReservationEvent>
}

interface SalesOrderLine {
  pk: number
  order: number
  line_type: SalesLineType
  variety: number | null
  tray_item: number | null
  description: string
  quantity: number
  unit_price: string
  tax_rate: string
  tax_treatment: SalesTaxTreatment
  discount_type: SalesDiscountType
  discount_value: string
  prices_include_tax: boolean
  gross_ex_tax: string
  discount_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  allocations: Array<SalesAllocation>
  created: string
  updated: string
}

interface SalesMargin {
  allocation_complete: boolean
  cost_complete: boolean
  provisional: boolean
  cost_total: string | null
  estimated_margin: string | null
  currency_code: string
}

interface SalesOrder {
  pk: number
  order_number: string
  customer: number | null
  status: SalesOrderStatus
  quote_date: string | null
  order_date: string | null
  requested_date: string | null
  currency_code: string
  prices_include_tax: boolean
  notes: string
  gross_ex_tax: string
  discount_total_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  created_by: number | null
  created: string
  updated: string
  lines: Array<SalesOrderLine>
  margin: SalesMargin
  commerce: SalesCommerceSummary
}

interface SalesCommerceSummary {
  requested_quantity: number
  reserved_quantity: number
  fulfilled_quantity: number
  returned_quantity: number
  fulfilled_total_incl_tax: string
  refunded_total_incl_tax: string
  paid_total: string
  net_paid_total: string
  outstanding_total: string
  overpaid_total: string
  payment_status: 'unpaid' | 'partially_paid' | 'paid' | 'overpaid'
  currency_code: string
}

interface FulfillmentLine {
  pk: number
  allocation: number
  commercial_position: number
  gross_ex_tax: string
  discount_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
  tax_treatment: SalesTaxTreatment
  cogs_amount: string | null
  cogs_provisional: boolean
  currency_code: string
  lifecycle_event: number | null
  stock_movement: number | null
}

interface FulfillmentPackagingLine {
  pk: number
  lot: number
  source: number
  quantity: string
  base_unit: string
  unit_cost: string | null
  cogs_amount: string | null
  currency_code: string
  stock_movement: number
}

interface Fulfillment {
  pk: number
  fulfillment_number: string
  fulfilled_at: string
  status: CommerceStatus
  notes: string
  operation_key: string
  reversal_of: number | null
  lines: Array<FulfillmentLine>
  packaging_lines: Array<FulfillmentPackagingLine>
}

interface SalesPayment {
  pk: number
  paid_on: string
  amount: string
  currency_code: string
  method: 'cash' | 'card' | 'bank_transfer' | 'other'
  external_reference: string
  notes: string
  status: CommerceStatus
  operation_key: string
  reversal_of: number | null
}

interface SalesReturnLine {
  pk: number
  fulfillment_line: number
  outcome: 'available' | 'quarantined' | 'discarded'
  destination: number | null
  lifecycle_event: number | null
  return_movement: number | null
  discard_movement: number | null
}

interface SalesReturn {
  pk: number
  returned_at: string
  reason: string
  notes: string
  status: CommerceStatus
  health_observation: number | null
  quarantine_case: number | null
  operation_key: string
  reversal_of: number | null
  lines: Array<SalesReturnLine>
}

interface SalesRefundLine {
  pk: number
  fulfillment_line: number
  gross_ex_tax: string
  discount_ex_tax: string
  subtotal_ex_tax: string
  tax_total: string
  total_incl_tax: string
}

interface SalesRefund {
  pk: number
  payment: number
  sales_return: number | null
  refunded_at: string
  amount: string
  currency_code: string
  reason: string
  notes: string
  status: CommerceStatus
  operation_key: string
  reversal_of: number | null
  lines: Array<SalesRefundLine>
}

interface SalesOrderLineWrite {
  order: number
  line_type: SalesLineType
  variety: number | null
  tray_item: number | null
  description: string
  quantity: number
  unit_price: string
  tax_rate?: string
  // Omitted for a rated line: the server derives 'standard' from the rate.
  // A zero-rated line has to say which of zero-rated, exempt or out-of-scope
  // it is, or it is stored 'unclassified' and reported as a gap.
  tax_treatment?: SalesTaxTreatment
  discount_type: SalesDiscountType
  discount_value: string
}

interface AllocationPreview {
  selected: Array<number>
  conflicts: Array<{ id: number; reason: string }>
}

export {
  AllocationPreview,
  Customer,
  Fulfillment,
  FulfillmentLine,
  FulfillmentPackagingLine,
  SalesAllocation,
  SalesAllocationStatus,
  SalesDiscountType,
  SalesLineType,
  SalesTaxTreatment,
  SalesCommerceSummary,
  SalesMargin,
  SalesOrder,
  SalesOrderLine,
  SalesOrderLineWrite,
  SalesOrderStatus,
  SalesPayment,
  SalesRefund,
  SalesReturn
}
