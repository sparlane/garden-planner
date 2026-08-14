type LabelFormat = 'qr' | 'code128'
type LabelPayloadMode = 'code' | 'url'
type LabelLayout = 'single' | 'sheet' | 'roll'
type LabelResolutionStatus = 'active' | 'inactive' | 'replaced' | 'wrong_workspace' | 'unknown'

interface LabelIdentity {
  identity: number
  target_type: string
  object_id: number
  display: string
  code: string
  variety?: string | null
  batch?: string | null
  sowing_date?: string | null
  expected_ready?: string | null
  inventory_unit?: number
}

interface LabelTemplate {
  pk: number
  name: string
  format: LabelFormat
  payload_mode: LabelPayloadMode
  layout: LabelLayout
  fields: Array<string>
  dimensions: Record<string, number>
  built_in: boolean
  active: boolean
}

interface LabelPrintItem {
  position: number
  identity: number
  code: string
  payload: string
  target: LabelIdentity & Record<string, unknown>
  is_reprint: boolean
}

interface LabelPrintJob {
  job: number | null
  printed_at: string | null
  template: Omit<LabelTemplate, 'pk' | 'built_in' | 'active'>
  items: Array<LabelPrintItem>
}

interface LabelResolution {
  status: LabelResolutionStatus
  message: string
  code?: string
  current_code?: string | null
  target?: LabelIdentity
  deep_link?: string | null
  capabilities?: Array<string>
}

export { LabelFormat, LabelIdentity, LabelLayout, LabelPayloadMode, LabelPrintItem, LabelPrintJob, LabelResolution, LabelResolutionStatus, LabelTemplate }
