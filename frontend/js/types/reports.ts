interface ReportQualityFlag {
  code: string
  count: number
  message: string
  drill_down: string
}

interface ReportEnvelope<Row = Record<string, unknown>, Totals = Record<string, unknown>> {
  report: string
  version: string
  generated_at: string
  filters: Record<string, unknown>
  totals: Totals
  reconciliation: Record<string, unknown>
  data_quality: Array<ReportQualityFlag>
  count: number
  next: string | null
  previous: string | null
  results: Array<Row>
}

interface DashboardRow {
  expiring_lot_locations: number
  low_stock_lot_locations: number
  active_batches: number
  available_seedlings: number
  open_orders: number
  provisional_batches: number
  period_profitability: {
    currencies?: Array<ProfitabilityCurrency>
    finalized_margin_available?: boolean
  }
  recent_fulfillments: Array<{
    fulfillment_id: number
    fulfillment_number: string
    fulfilled_at: string
    order_id: number
    order_number: string
  }>
}

interface ProfitabilityCurrency {
  currency_code: string
  gross_sales: string
  discounts: string
  refunds: string
  net_sales: string
  direct_cogs: string
  production_loss: string
  gross_profit: string | null
  gross_margin: string | null
}

interface ProfitabilityTotals {
  currencies: Array<ProfitabilityCurrency>
  provisional_rows: number
  unvalued_rows: number
  dimension_unattributed_rows: number
  finalized_margin_available: boolean
}

export type { DashboardRow, ProfitabilityCurrency, ProfitabilityTotals, ReportEnvelope, ReportQualityFlag }
