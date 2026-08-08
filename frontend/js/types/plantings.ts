import { GardenSquare } from './garden'

type ProductionBatchStatus = 'planned' | 'active' | 'output_finalized' | 'completed' | 'cancelled'

type ProductionBatchRepairState = 'none' | 'needs_repair'

interface NewBatchInline {
  code: string
  planned_start?: string | null
  notes?: string
}

interface ProductionBatch {
  pk: number
  code: string
  variety: number
  variety_name: string
  plant_name: string
  status: ProductionBatchStatus
  planned_start: string | null
  actual_start: string | null
  output_finalized_at: string | null
  completed_at: string | null
  cancelled_at: string | null
  notes: string
  created_by: number | null
  repair_state: ProductionBatchRepairState
  repair_details: string
  sowing_count: number
  seeds_sown: number
  plants_observed: number
  plants_with_active_location: number
  final_outcomes: number
  unresolved_plants: Array<number>
  created: string
  updated: string
}

interface ProductionBatchCreate {
  code: string
  variety: number
  planned_start?: string | null
  notes?: string
}

interface ProductionBatchUpdate {
  code?: string
  variety?: number
  planned_start?: string | null
  notes?: string
}

interface ProductionBatchTransition {
  pk: number
  previous_status: ProductionBatchStatus | ''
  new_status: ProductionBatchStatus
  created_by: number | null
  reason: string
  created: string
}

interface ProductionBatchCell {
  pk: number
  cell: number
  x_position: number
  y_position: number
  quantity: number
  plants_observed: number
}

interface ProductionBatchSowing {
  pk: number
  sowing_type: string
  planted: string
  quantity: number
  removed: boolean
  seeds_used: number
  seed_lot: number | null
  seed_tray: number | null
  location: string | null
  notes: string | null
  cells: Array<ProductionBatchCell>
  plants_observed: number
}

interface ProductionBatchLocation {
  specific_plant: number
  location_type: 'seed_tray_cell' | 'garden_square'
  seed_tray_cell: number | null
  garden_square: number | null
  started: string
  label: string
}

interface ProductionBatchDetail extends ProductionBatch {
  sowings: Array<ProductionBatchSowing>
  current_locations: Array<ProductionBatchLocation>
  lifecycle_counts: Record<PlantLifecycleState, number>
  harvest_count: number
  harvest_totals: Array<HarvestFamilyTotal>
  plants_harvest_finished: number
  transitions: Array<ProductionBatchTransition>
}

interface BatchAction {
  reason?: string
  actual_start?: string
}

interface PlantingCreate {
  seeds_used: number
  batch?: number
  new_batch?: NewBatchInline
  quantity: number
  notes?: string
}

interface GardenRowDirectPlantingCreate extends PlantingCreate {
  location: number
}

interface GardenSquareDirectPlantingCreate extends PlantingCreate {
  location: number
}

interface SeedTrayPlantingCreate extends PlantingCreate {
  seed_tray?: number
  location?: string
  cell_plantings?: Array<{ cell: number; quantity: number }>
}

interface Planting {
  pk: number
  seeds_used: number
  batch: number
  quantity: number
  removed: boolean
  notes: string
}

interface GardenRowDirectPlanting extends Planting {
  location: number
  planted: string
}

interface GardenSquareDirectPlanting extends Planting {
  location: number
  planted: string
}

interface SeedTrayPlanting extends Planting {
  seed_tray?: number
  location?: string
  planted: string
  cell_plantings?: Array<{ pk: number; cell: number; quantity: number }>
}

interface GardenSquareTransplanting extends Planting {
  location: number
  transplanted: string
  original_planting: number
}

interface SeedTrayPlantingDetails {
  pk: number
  seeds_used: number
  batch: number
  batch_code: string
  plant: string
  variety: string
  planted: string
  quantity: number
  seed_tray?: number
  location?: string
  notes: string
  germination_date_early?: string
  germination_date_late?: string
  germinated_count: number
  transplanted_count: number
  cell_plantings?: Array<{ pk: number; cell: number; quantity: number }>
}

interface GardenSquarePlantingLocation extends Omit<GardenSquare, 'area' | 'bed'> {
  area: string
  bed: string
}

interface GardenSquarePlanting {
  specific_plant_pk?: number
  transplanting_pk?: number
  transplanted?: string
  planting_pk: number
  batch: number
  batch_code: string
  seeds_used?: number
  plant: string
  variety: string
  quantity: number
  planted: string
  location: GardenSquarePlantingLocation
  notes: string
  germination_date_early?: string
  germination_date_late?: string
  maturity_date_early?: string
  maturity_date_late?: string
}

interface SpecificPlantLocation {
  pk: number
  specific_plant: number
  location_type: 'seed_tray_cell' | 'garden_square'
  seed_tray_cell?: number
  garden_square?: number
  started: string
  ended?: string
  notes?: string
}

interface SpecificPlantLocationCreate {
  specific_plant: number
  location_type: 'seed_tray_cell' | 'garden_square'
  seed_tray_cell?: number
  garden_square?: number
  started: string
  notes?: string
}

interface SpecificPlantMove {
  location_type: 'seed_tray_cell' | 'garden_square'
  seed_tray_cell?: number
  garden_square?: number
  started?: string
  notes?: string
}

type PlantLifecycleEventType = 'germinated' | 'ready' | 'transplanted' | 'retained' | 'failed' | 'culled' | 'donated' | 'harvest_finished' | 'corrected'

type PlantLifecycleState = 'growing' | 'available' | 'retained' | 'donated' | 'failed' | 'culled' | 'harvested'

type PlantOutcomeAction = 'ready' | 'retain' | 'fail' | 'cull' | 'donate' | 'finish-harvest'

interface PlantLifecycleEvent {
  pk: number
  plant: number
  batch: number
  event_type: PlantLifecycleEventType
  occurred_at: string
  reason: string
  reference: string
  created_by: number | null
  reversal_of: number | null
  reversed_by: number | null
  created: string
}

interface PlantOutcome {
  occurred_at?: string
  reason?: string
  reference?: string
}

interface BulkPlantOutcome extends PlantOutcome {
  plants: Array<number>
  event_type: PlantLifecycleEventType
}

interface ReversePlantEvent {
  event: number
  reason: string
  occurred_at?: string
}

interface SpecificPlant {
  pk: number
  cell_planting: number
  batch: number
  germinated: string
  notes?: string
  locations: Array<SpecificPlantLocation>
  lifecycle_state: PlantLifecycleState
  sellable: boolean
  final_outcome: PlantLifecycleEventType | null
  final_outcome_at: string | null
}

interface SpecificPlantDetail extends SpecificPlant {
  lifecycle_events: Array<PlantLifecycleEvent>
}

interface SpecificPlantCreate {
  cell_planting: number
  germinated: string
  notes?: string
}

interface SowingCorrection {
  seeds_used?: number
  quantity?: number
  reason: string
}

// The nursery plant register. Rows are a projection of current plants, so a
// row carries where a plant is now rather than everywhere it has been.
interface NurseryRegisterRow {
  pk: number
  batch: number
  batch_code: string
  variety: number
  variety_name: string
  plant_name: string
  germinated: string
  age_days: number
  lifecycle_state: PlantLifecycleState
  sellable: boolean
  final_outcome: PlantLifecycleEventType | null
  final_outcome_at: string | null
  location_type: 'seed_tray_cell' | 'garden_square' | null
  location_label: string
  seed_tray: number | null
  seed_tray_cell: number | null
  garden_square: number | null
  located_since: string | null
  // Projected from the crop's maturity range, not an observed readiness date.
  expected_ready_early: string | null
  expected_ready_late: string | null
  cost: string | null
  currency_code: string
}

type NurseryRegisterOrdering = 'age' | '-age' | 'variety' | '-variety' | 'location' | '-location' | 'cost' | '-cost' | 'state' | '-state' | 'batch' | '-batch'

// Keys are the query-parameter names the register endpoint validates.
interface NurseryRegisterFilters {
  variety?: number
  batch?: number
  state?: Array<PlantLifecycleState>
  sellable?: boolean
  germinated_from?: string
  germinated_to?: string
  location_type?: 'seed_tray_cell' | 'garden_square' | 'none'
  seed_tray?: number
  garden_square?: number
  search?: string
  ordering?: NurseryRegisterOrdering
  page?: number
  page_size?: number
}

// Counts describe the whole filtered selection, never the visible page.
type NurseryRegisterTotals = Record<PlantLifecycleState, number> & {
  total: number
  unresolved: number
}

interface NurseryRegisterPage {
  count: number
  next: string | null
  previous: string | null
  totals: NurseryRegisterTotals
  results: Array<NurseryRegisterRow>
}

interface NurseryRegisterSelection {
  count: number
  plants: Array<number>
}

// The five units a yield may be measured in. The backend rejects the seed and
// area codes the inventory registry also publishes, so this list is the
// contract rather than /inventory/units/.
type HarvestUnitCode = 'each' | 'g' | 'kg' | 'ml' | 'l'

const HARVEST_UNIT_LABELS: Record<HarvestUnitCode, string> = {
  each: 'each',
  g: 'g',
  kg: 'kg',
  ml: 'ml',
  l: 'L'
}

type HarvestStatus = 'posted' | 'reversed'

type HarvestGrade = 'ungraded' | 'premium' | 'standard' | 'seconds'

interface Harvest {
  pk: number
  batch: number
  batch_code: string
  variety: number
  variety_name: string
  plant_name: string
  harvested_at: string
  quantity: string
  unit_code: HarvestUnitCode
  garden_square: number | null
  garden_row: number | null
  location_label: string | null
  quality_rating: number | null
  grade: HarvestGrade
  notes: string
  status: HarvestStatus
  posted_at: string
  reversed_at: string | null
  reverse_reason: string
  created_by: number | null
  reversed_by: number | null
  created: string
  plants: Array<number>
  finished_plants: Array<number>
}

interface HarvestCreate {
  batch: number
  harvested_at: string
  quantity: string
  unit_code: HarvestUnitCode
  garden_square?: number | null
  garden_row?: number | null
  quality_rating?: number | null
  grade?: HarvestGrade
  notes?: string
  plants?: Array<number>
  finish_plants?: boolean
  finish_reason?: string
}

interface ReverseHarvest {
  reason: string
}

interface HarvestFilters {
  batch?: number
  variety?: number
  garden_square?: number
  garden_row?: number
  plant?: number
  status?: HarvestStatus | ''
  harvested_from?: string
  harvested_to?: string
}

// One total per unit family. Count, mass, and volume are never summed
// together, so a report row carries one of these per dimension it measured.
interface HarvestFamilyTotal {
  conversion_family: string
  dimension: string
  unit_code: HarvestUnitCode
  quantity: string
  harvest_count: number
}

type HarvestReportGroupBy = 'plant' | 'variety' | 'batch' | 'garden_square' | 'garden_row' | 'month' | 'year'

interface HarvestReportRow {
  group_by: HarvestReportGroupBy
  key: number | string | null
  label: string
  harvest_count: number
  first_harvested_at: string
  last_harvested_at: string
  totals: Array<HarvestFamilyTotal>
  // Only ever populated when grouping by plant: harvests shared with other
  // plants, reported beside the total rather than added into it.
  shared_totals: Array<HarvestFamilyTotal>
  seeds_sown: number | null
  plants_observed: number | null
  plants_harvest_finished: number | null
}

interface HarvestReportFilters {
  group_by: HarvestReportGroupBy
  batch?: number
  variety?: number
  garden_square?: number
  garden_row?: number
  harvested_from?: string
  harvested_to?: string
}

export {
  BatchAction,
  BulkPlantOutcome,
  HARVEST_UNIT_LABELS,
  Harvest,
  HarvestCreate,
  HarvestFamilyTotal,
  HarvestFilters,
  HarvestGrade,
  HarvestReportFilters,
  HarvestReportGroupBy,
  HarvestReportRow,
  HarvestStatus,
  HarvestUnitCode,
  ReverseHarvest,
  PlantLifecycleEvent,
  PlantLifecycleEventType,
  PlantLifecycleState,
  PlantOutcome,
  PlantOutcomeAction,
  ReversePlantEvent,
  NewBatchInline,
  NurseryRegisterFilters,
  NurseryRegisterOrdering,
  NurseryRegisterPage,
  NurseryRegisterRow,
  NurseryRegisterSelection,
  NurseryRegisterTotals,
  Planting,
  ProductionBatch,
  ProductionBatchCell,
  ProductionBatchCreate,
  ProductionBatchDetail,
  ProductionBatchLocation,
  ProductionBatchRepairState,
  ProductionBatchSowing,
  ProductionBatchStatus,
  ProductionBatchTransition,
  ProductionBatchUpdate,
  GardenRowDirectPlanting,
  GardenSquareDirectPlanting,
  SeedTrayPlanting,
  GardenSquareTransplanting,
  SeedTrayPlantingDetails,
  GardenSquarePlanting,
  GardenRowDirectPlantingCreate,
  GardenSquareDirectPlantingCreate,
  SeedTrayPlantingCreate,
  SpecificPlant,
  SpecificPlantCreate,
  SpecificPlantDetail,
  SpecificPlantLocation,
  SpecificPlantLocationCreate,
  SpecificPlantMove,
  SowingCorrection
}
