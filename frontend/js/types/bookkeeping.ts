interface BookkeepingEntry {
  id: number
  kind: 'other_income' | 'owner_contribution' | 'owner_drawing' | 'liability_advance' | 'liability_repayment' | 'cash_adjustment'
  occurred_on: string
  description: string
  amount_ex_tax: string
  tax_amount: string
  total_incl_tax: string
  currency_code: string
  account_reference: string
  external_reference: string
  reversal_of: number | null
}

interface StockValuationLine {
  id: number
  category: string
  description: string
  source_type: string
  source_id: string
  quantity: string | null
  unit_code: string
  method: string
  value: string
  currency_code: string
  evidence_url: string
  derived: boolean
  provisional: boolean
}

interface IncomeTaxReport {
  basis: 'cash' | 'accrual'
  date_from: string
  date_to: string
  currency_code: string
  totals: Record<string, string>
  data_quality: Array<{ code: string; message: string; count?: number }>
}

interface IncomeTaxYear {
  id: number
  year_end: string
  basis: 'cash' | 'accrual'
  revision: number
  status: 'draft' | 'finalized'
  retain_until: string
  stock_lines: Array<StockValuationLine>
  live_report: IncomeTaxReport
}

interface TaxAsset {
  id: number
  code: string
  name: string
  category: string
  acquired_on: string
  tax_cost: string
  currency_code: string
  disposed_on: string | null
}

interface Liability {
  id: number
  code: string
  name: string
  counterparty: string
  opened_on: string
  closed_on: string | null
  currency_code: string
  active: boolean
}

export { BookkeepingEntry, IncomeTaxReport, IncomeTaxYear, Liability, StockValuationLine, TaxAsset }
