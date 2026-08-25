import { ImageAttachment } from './attachments'

type HealthSeverity = 'low' | 'moderate' | 'high' | 'critical'
type HealthScopeType = 'plant' | 'cohort' | 'tray' | 'generation' | 'batch' | 'location'

interface HealthCatalogValue {
  pk: number
  code: string
  name: string
  display_order: number
  active: boolean
  category?: 'pest' | 'disease' | 'damage' | 'vigor' | 'other'
}

interface HealthScope {
  type: HealthScopeType
  id: number
  label?: string
}

interface HealthPreview {
  scopes: Array<Required<HealthScope>>
  plants: Array<number>
  cohorts: Array<{ cohort: number; quantity: number }>
  affected_count: number
  digest: string
}

interface HealthDiagnosisAssessment {
  diagnosis: number
  name?: string
  category?: string
  certainty: 'suspected' | 'confirmed'
}

interface HealthObservation {
  pk: number
  observation_type: number
  observation_type_name: string
  severity: HealthSeverity
  occurred_at: string
  follow_up_due_at: string | null
  notes: string
  scopes: Array<Required<HealthScope>>
  affected: Array<{ type: 'plant' | 'cohort'; id: number; quantity: number }>
  affected_count: number
  diagnoses: Array<HealthDiagnosisAssessment>
  evidence: Array<{ url: string; label: string }>
  attachments: Array<ImageAttachment>
  corrects: number | null
  correction_reason: string
  quarantine_cases: Array<{ pk: number; active: boolean; reason: string }>
  treatments: Array<{ pk: number; application: number; application_status: string; follow_up_due_at: string | null; notes: string }>
  follow_ups: Array<{
    pk: number
    treatment: number | null
    occurred_at: string
    result: string
    effectiveness: string
    notes: string
    corrects: number | null
    correction_reason: string
  }>
  created_by: number | null
  created: string
}

interface HealthObservationCreate {
  scopes: Array<HealthScope>
  reviewed_digest: string
  observation_type: number
  severity: HealthSeverity
  diagnoses?: Array<{ diagnosis: number; certainty: 'suspected' | 'confirmed' }>
  evidence?: Array<{ url: string; label: string }>
  occurred_at?: string
  follow_up_due_at?: string | null
  notes?: string
}

interface QuarantineCase {
  pk: number
  observation: number
  observation_summary: string
  reason: string
  active: boolean
  members: Array<{ type: 'plant' | 'cohort'; id: number; quantity: number }>
  actions: Array<{ pk: number; action: string; occurred_at: string; reason: string; destination: number | null; created_by: number | null }>
  created_by: number | null
  created: string
}

interface HealthReport {
  summary: {
    observations: number
    by_issue: Record<string, number>
    by_severity: Record<string, number>
    by_diagnosis: Record<string, number>
    by_outcome: Record<string, number>
  }
  results: Array<{
    observation: number
    occurred_at: string
    observation_type: string
    severity: HealthSeverity
    affected: Array<{ type: string; id: number; quantity: number }>
    batches: Array<{ batch: number; code: string; variety: number; variety_name: string }>
    seed_sources: Array<{ seed_packet: number; seed_product: number; supplier: number; supplier_name: string }>
    treatments: Array<{ treatment: number; application: number; status: string; items: Array<string> }>
    outcomes: Array<{ follow_up: number; result: string; effectiveness: string }>
  }>
}

export {
  HealthCatalogValue,
  HealthDiagnosisAssessment,
  HealthObservation,
  HealthObservationCreate,
  HealthPreview,
  HealthReport,
  HealthScope,
  HealthScopeType,
  HealthSeverity,
  QuarantineCase
}
