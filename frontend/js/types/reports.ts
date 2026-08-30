import { CohortLossCause } from './plantings'

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
  loss_by_cause: LossByCause<string>
  gross_profit: string | null
  gross_margin: string | null
}

// Loss totalled the same way for anonymous cohort units and identified plants:
// units in the production report, money in the profitability one.
type LossByCause<Value = number> = Record<CohortLossCause, Value>

interface ProfitabilityTotals {
  currencies: Array<ProfitabilityCurrency>
  lost_units_by_cause: LossByCause
  lost_units: number
  provisional_rows: number
  unvalued_rows: number
  dimension_unattributed_rows: number
  finalized_margin_available: boolean
}

interface ProductionTotals {
  batches: number
  current_seedlings: number
  loss_by_cause: LossByCause
  loss_quantity: number
  provisional_batches: number
  unvalued_batches: number
}

export type { DashboardRow, LossByCause, ProductionTotals, ProfitabilityCurrency, ProfitabilityTotals, ReportEnvelope, ReportQualityFlag }
