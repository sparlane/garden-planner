import { SerializedInventoryUnit, SerializedPhysicalState } from './inventory'

interface SeedTrayModelCreate {
  identifier: string
  description: string
  height: number
  x_size: number
  y_size: number
  x_cells: number
  y_cells: number
  cell_size_ml: number
}
interface SeedTrayModel extends SeedTrayModelCreate {
  pk: number
  inventory_item: number
}

interface SeedTrayReceiptCreate {
  supplier: number
  received_date: string
  supplier_reference?: string
  quantity: number
  line_cost_ex_tax: string
  destination: number
  tax_rate?: string
  tax_recoverable?: boolean
  notes?: string
}

interface SeedTray {
  pk: number
  model: number
  inventory_unit: number
  inventory: SerializedInventoryUnit
  created: string
  notes?: string
  // Null when the tray is empty: nothing has been filled into it yet, or the
  // last fill has been cleaned out.
  active_generation: number | null
  generation_review_required: boolean
}

type SeedTrayGenerationStatus = 'open' | 'closed'
type SeedTrayGenerationOrigin = 'operator' | 'legacy'
type SeedTrayGenerationReviewState = 'none' | 'needs_review'
type SeedTrayGenerationEventType = 'opened' | 'closed' | 'reopened' | 'reviewed'
type ResidualKind = 'media' | 'seed'
type SeedDispositionChoice = 'removed' | 'returned'
type MediaDispositionChoice = 'waste' | 'reclaimed'
type CleanPlantOutcome = 'retained' | 'failed' | 'culled' | 'donated'

interface SeedTrayGenerationEvent {
  pk: number
  event_type: SeedTrayGenerationEventType
  occurred_at: string
  reason: string
  created_by: number | null
  created: string
}

interface SeedTrayGenerationResidual {
  pk: number
  kind: ResidualKind
  disposition: SeedDispositionChoice | MediaDispositionChoice
  lot: number
  sowing: number | null
  base_quantity: string
  base_unit: string
  unit_cost: string | null
  movement: number | null
  reason: string
  created: string
}

interface SeedTrayGeneration {
  pk: number
  tray: number
  code: string
  sequence: number
  status: SeedTrayGenerationStatus
  origin: SeedTrayGenerationOrigin
  review_state: SeedTrayGenerationReviewState
  review_details: string
  opened_at: string
  closed_at: string | null
  close_reason: string
  notes: string
  created_by: number | null
  closed_by: number | null
  created: string
  updated: string
  events: Array<SeedTrayGenerationEvent>
  residuals: Array<SeedTrayGenerationResidual>
}

interface GenerationContentsSowing {
  pk: number
  planted: string
  batch: number
  quantity: number
  seeds_used: number
}

interface GenerationContentsPlant {
  pk: number
  cell_planting: number
  cell: number
  germinated: string
}

interface GenerationContentsSeed {
  sowing: number
  seeds_used: number
  quantity: number
}

interface GenerationContentsMedia {
  lot: number
  item: number
  base_quantity: string
  base_unit: string
  unit_cost: string | null
}

interface SeedTrayGenerationContents {
  generation: number
  code: string
  status: SeedTrayGenerationStatus
  review_state: SeedTrayGenerationReviewState
  cell_count: number
  // Echoed back when the clean is confirmed, so a confirmation prepared against
  // contents that have since moved is refused rather than applied blind.
  digest: string
  sowings: Array<GenerationContentsSowing>
  plants: Array<GenerationContentsPlant>
  seeds: Array<GenerationContentsSeed>
  media: Array<GenerationContentsMedia>
}

interface CleanPlantDisposition {
  plant: number
  outcome: CleanPlantOutcome
  reason: string
}

interface CleanSeedDisposition {
  sowing: number
  quantity: string
  disposition: SeedDispositionChoice
  reason: string
  destination?: number | null
}

interface CleanMediaDisposition {
  lot: number
  quantity: string
  disposition: MediaDispositionChoice
  reason: string
  destination?: number | null
}

interface CleanGenerationRequest {
  reason: string
  digest: string
  plants: Array<CleanPlantDisposition>
  seeds: Array<CleanSeedDisposition>
  media: Array<CleanMediaDisposition>
  open_next: boolean
}

interface CleanGenerationResponse {
  generation: SeedTrayGeneration
  next_generation: SeedTrayGeneration | null
}

interface GenerationCostCell {
  cell: number
  x_position: number
  y_position: number
  cost: string | null
  plants: Array<number>
  per_plant_cost: string | null
  provisional: boolean
}

interface GenerationCostMedia {
  line: number
  application: number
  lot: number
  item: number
  base_quantity: string
  base_unit: string
  unit_cost: string | null
  cost: string | null
}

interface GenerationCostBreakdown {
  generation: number
  code: string
  status: SeedTrayGenerationStatus
  currency_code: string
  // True when a lot has no recorded unit cost. The totals then understate the
  // real figure, so the screen says so rather than showing them as complete.
  unknown_cost: boolean
  media: Array<GenerationCostMedia>
  applied_cost: string
  recovered_cost: string
  wasted_cost: string
  cells: Array<GenerationCostCell>
  plants: Array<{ plant: number; cost: string | null }>
  allocated_cost: string
  unallocated_cost: string
  production_loss: string
}

interface SeedTrayFilters {
  model?: number
  location?: number
  physical_state?: SerializedPhysicalState
  in_use?: boolean
}

interface SeedTrayReceiptResponse {
  receipt: number
  receipt_line: number
  trays: SeedTray[]
}

interface SeedTrayCell {
  pk: number
  tray: number
  x_position: number
  y_position: number
}

export {
  CleanGenerationRequest,
  CleanGenerationResponse,
  CleanMediaDisposition,
  CleanPlantDisposition,
  CleanPlantOutcome,
  CleanSeedDisposition,
  GenerationCostBreakdown,
  MediaDispositionChoice,
  SeedDispositionChoice,
  SeedTray,
  SeedTrayCell,
  SeedTrayFilters,
  SeedTrayGeneration,
  SeedTrayGenerationContents,
  SeedTrayGenerationEvent,
  SeedTrayModel,
  SeedTrayModelCreate,
  SeedTrayReceiptCreate,
  SeedTrayReceiptResponse
}
