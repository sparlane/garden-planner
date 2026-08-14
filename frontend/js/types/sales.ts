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
type SalesDiscountType = 'none' | 'fixed' | 'percentage'
type SalesAllocationStatus = 'pending' | 'reserved' | 'released' | 'expired' | 'fulfilled'

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
  SalesAllocation,
  SalesAllocationStatus,
  SalesDiscountType,
  SalesLineType,
  SalesMargin,
  SalesOrder,
  SalesOrderLine,
  SalesOrderLineWrite,
  SalesOrderStatus
}
