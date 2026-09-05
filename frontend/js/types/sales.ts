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
// Named for the mechanism rather than for what happens to be sold: 'unit' is
// anything individually numbered, a tray or a pot alike, 'lot_quantity' is
// anonymous stock sold by the count out of one lot, and 'cohort_quantity' is
// nursery plants sold by the count out of a block that was never given
// identities. The last two are both counted draws; what differs is the pool.
type SalesLineType = 'seedling' | 'unit' | 'lot_quantity' | 'cohort_quantity'
// What kind of supply a line is for GST. A rate of zero is three different
// things — a zero-rated export, an exempt supply, and something outside GST —
// and a return reports the first separately from the other two.
// 'unclassified' is the honest state for a zero-rated-looking line nobody has
// said which of the three it is; it is never counted as zero-rated by default.
type SalesTaxTreatment = 'standard' | 'zero_rated' | 'exempt' | 'out_of_scope' | 'unclassified'

type SalesDiscountType = 'none' | 'fixed' | 'percentage'
// 'shortfall' is distinct from 'released': a release gives stock back to the
// pool by choice, while this says the nursery could not supply what it sold.
type SalesAllocationStatus = 'pending' | 'reserved' | 'released' | 'expired' | 'fulfilled' | 'returned' | 'shortfall'
type CommerceStatus = 'posted' | 'reversed' | 'reversal'

interface ReservationEvent {
  pk: number
  event_type: 'reserved' | 'released' | 'expired' | 'cancelled' | 'fulfilled' | 'shortfall'
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
  stock_lot: number | null
  plant_cohort: number | null
  source_location: number | null
  // Null for a plant or a numbered unit, each of which is exactly one thing.
  quantity: number | null
  status: SalesAllocationStatus
  expires_at: string | null
  created_by: number | null
  created: string
  updated: string
  events: Array<ReservationEvent>
  competing_claims: Array<AllocationOrderReference>
}

interface SalesShortfall {
  pk: number
  allocation: number
  // The reserved remainder this shortfall re-promised, when only part of the
  // commitment failed. Null when the whole promise was given up.
  replacement: number | null
  quantity: number
  reason: string
  recorded_at: string
  created_by: number | null
  created: string
}

interface AllocationOrderReference {
  order: number
  order_number: string
  status: 'pending' | 'reserved'
  quantity?: number | null
}

interface SalesOrderLine {
  pk: number
  order: number
  line_type: SalesLineType
  variety: number | null
  item: number | null
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
  shortfalls: Array<SalesShortfall>
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
  // The part of the reservation that is a promise about the future: stock this
  // order holds in a block nobody has graded ready. Reported beside the
  // reservation rather than inside it, so "when can you deliver?" is answerable.
  committed_forward_quantity: number
  // What was promised and will never be supplied, given up with a reason.
  short_quantity: number
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
  item: number | null
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

// One counted draw on a lot standing somewhere, as the allocation surface
// takes it and as the preview answers for it.
interface LotDraw {
  lot: number
  location: number
  quantity: number
}

// What a counted preview says can be had. `id` is the lot, and `available` is
// the figure the refusal would have been measured against, so an operator sees
// why a draw fits rather than only that it does.
interface LotDrawPreview {
  id: number
  location: number
  quantity: number
  available: string | null
}

interface CohortDraw {
  cohort: number
  quantity: number
  // The revision the count was chosen against. A block that has been split,
  // moved or written down since the screen loaded is not the one the operator
  // read the figure from, so the server refuses rather than guessing.
  expected_revision: number
}

// What a cohort preview says can be had. `id` is the cohort, and `available`
// is what is standing unpromised in it, as a string like every other counted
// availability figure so one helper renders them all.
interface CohortDrawPreview {
  id: number
  quantity: number
  expected_revision: number
  available: string | null
}

interface SalesShortfallWrite {
  allocation: number
  quantity: number
  reason: string
}

interface AllocationPreview {
  selected: Array<number> | Array<LotDrawPreview> | Array<CohortDrawPreview>
  conflicts: Array<{ id: number; reason: string; location?: number; available?: string | null; order?: number; order_number?: string; status?: SalesAllocationStatus }>
  warnings: Array<{ id: number; reason: 'tentatively_claimed' } & AllocationOrderReference>
}

export {
  AllocationPreview,
  CohortDraw,
  CohortDrawPreview,
  Customer,
  LotDraw,
  LotDrawPreview,
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
  SalesReturn,
  SalesShortfall,
  SalesShortfallWrite
}
