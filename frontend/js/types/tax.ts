// GST accounting basis. Hybrid is one choice rather than two independent
// fields because nothing in the tax rules combines output and input treatment
// freely — hybrid is invoice for output tax and payments for input tax.
type GstBasis = 'payments' | 'invoice' | 'hybrid'

type GstFrequency = 'monthly' | 'two_monthly' | 'six_monthly'

// One dated change to how the workspace accounts for GST. Arrangements are
// append-only: the server refuses PATCH and PUT, and a correction is a new
// record carrying `supersedes`. A superseded record is still listed, because
// it is what a return filed before the correction was filed under.
interface GstRegistration {
  pk: number
  registered: boolean
  effective_from: string
  gst_number: string
  basis: GstBasis | ''
  filing_frequency: GstFrequency | ''
  // A month a taxable period ends in, 1 through 12. Null on a deregistration.
  period_anchor_month: number | null
  taxable_activity_start: string | null
  reason: string
  notes: string
  supersedes: number | null
  superseded: boolean
  created_by: number | null
  created: string
}

interface GstTaxablePeriod {
  label: string
  start: string
  end: string
  cycle_start: string
  cycle_end: string
  // True when the period covers less than its whole cycle, which a mid-cycle
  // registration or basis change produces.
  clipped: boolean
  frequency: GstFrequency
  basis: GstBasis
  registration: number
}

interface GstStatus {
  as_at: string
  registered: boolean
  // Distinguishes "never registered" from "no longer registered"; both report
  // no taxable period, and they are not the same situation.
  has_history: boolean
  registration: GstRegistration | null
  taxable_period: GstTaxablePeriod | null
}

type GstRegistrationCreate = Pick<
  GstRegistration,
  'registered' | 'effective_from' | 'gst_number' | 'basis' | 'filing_frequency' | 'period_anchor_month' | 'taxable_activity_start' | 'reason' | 'notes' | 'supersedes'
>

export { GstBasis, GstFrequency, GstRegistration, GstRegistrationCreate, GstStatus, GstTaxablePeriod }
