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

// What a caller wants out of the printable list. Every field narrows on the
// server: it answers on the content type behind a generic foreign key rather
// than on anything a row carries, and the list is paged, so a browser filtering
// what it was sent would be filtering one page of an arbitrary hundred.
interface LabelIdentityQuery {
  targetType?: string
  objectId?: number
  page?: number
}

// How many printable labels each kind of record holds, over the whole
// workspace rather than the page being read. It is what the type picker is
// built from, so it stays the same as the filter narrows the list beneath it.
interface LabelTargetTypeCount {
  target_type: string
  count: number
}

interface LabelIdentityPage {
  count: number
  next: string | null
  previous: string | null
  results: Array<LabelIdentity>
  target_types: Array<LabelTargetTypeCount>
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

export {
  LabelFormat,
  LabelIdentity,
  LabelIdentityPage,
  LabelIdentityQuery,
  LabelLayout,
  LabelPayloadMode,
  LabelPrintItem,
  LabelPrintJob,
  LabelResolution,
  LabelResolutionStatus,
  LabelTargetTypeCount,
  LabelTemplate
}
