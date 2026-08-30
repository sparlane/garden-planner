import { GardenSquare } from './garden'
import { ImageAttachment } from './attachments'

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
  code_is_generated: boolean
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
  seeds_used: number | null
  seed_lot: number | null
  seed_tray: number | null
  location: string | null
  notes: string | null
  cells: Array<ProductionBatchCell>
  plants_observed: number
}

interface ProductionBatchLocation {
  specific_plant: number
  location_type: PlantPlacementType
  seed_tray_cell: number | null
  garden_square: number | null
  location: number | null
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
  garden_planting_pk?: number
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
  source?: GardenPlantingSource
  perennial?: boolean
  quantity_is_approximate?: boolean
  date_is_approximate?: boolean
  germination_date_early?: string
  germination_date_late?: string
  maturity_date_early?: string
  maturity_date_late?: string
}

type GardenPlantingSource = 'direct_seed' | 'indoor_raised_seed' | 'purchased_plant' | 'cutting' | 'division' | 'bulb_tuber_corm' | 'bare_root' | 'volunteer' | 'existing_unknown'
type GardenPlantingTracking = 'aggregate' | 'individual'
type GardenPlantingDateBasis = 'planted' | 'first_observed'

interface GardenQuickAddEntry {
  plant: number
  variety?: number
  new_variety_name?: string
  batch?: number
  source: GardenPlantingSource
  tracking: GardenPlantingTracking
  quantity: number
  quantity_is_approximate?: boolean
  recorded_on: string
  date_basis: GardenPlantingDateBasis
  date_is_approximate?: boolean
  perennial?: boolean
  garden_square?: number
  location?: number
  seed_packet?: number
  seed_quantity_used?: string
  supplier?: number
  purchase_cost?: string
  individual_names?: Array<string>
  override_reason?: string
  notes?: string
}

interface GardenQuickAddWarning {
  entry: number
  code: 'possible_duplicate' | 'location_occupied'
  message: string
}

interface GardenQuickAddReview {
  entries: Array<GardenQuickAddEntry>
  warnings: Array<GardenQuickAddWarning>
  confirmation_token: string
}

interface GardenQuickAddedPlanting extends GardenQuickAddEntry {
  pk: number
  plant_name: string
  variety: number
  variety_name: string
  batch: number
  batch_code: string
  location_label: string
  individual_names: Array<string>
  finished_on: string | null
}

type GardenRegisterRecordType = 'aggregate' | 'individual'
type GardenRegisterState = 'current' | 'finished' | 'all' | PlantLifecycleState

interface GardenRegisterTask {
  id: number
  title: string
  due: string
  status: string
  url: string
}

interface GardenRegisterRow {
  key: string
  record_type: GardenRegisterRecordType
  record_id: number
  plant: number
  plant_name: string
  variety: number
  variety_name: string
  batch: number
  batch_code: string
  name: string
  source: GardenPlantingSource
  state: string
  quantity: number
  quantity_is_approximate: boolean
  perennial: boolean
  container: boolean
  planted_on: string
  date_is_approximate: boolean
  location: string
  location_label: string
  expected_harvest_early: string | null
  expected_harvest_late: string | null
  health_flag: boolean
  next_task: GardenRegisterTask | null
  finished_on: string | null
}

interface GardenRegisterFilters {
  crop?: number
  variety?: number
  location?: string
  source?: GardenPlantingSource
  state?: GardenRegisterState
  planted_from?: string
  planted_to?: string
  expected_harvest_from?: string
  expected_harvest_to?: string
  health?: boolean
  next_task?: boolean
  search?: string
  ordering?: 'planted' | '-planted' | 'crop' | '-crop' | 'location' | '-location' | 'expected_harvest' | '-expected_harvest'
  page?: number
}

interface GardenRegisterTotals {
  rows: number
  quantity: number
  aggregate_rows: number
  individual_plants: number
  perennials: number
  containers: number
  unplaced: number
  health_flags: number
}

interface GardenRegisterPage {
  count: number
  next: string | null
  previous: string | null
  results: Array<GardenRegisterRow>
  totals: GardenRegisterTotals
}

interface GardenRegisterDetail extends GardenRegisterRow {
  links: Record<'garden' | 'batch' | 'plant' | 'harvest' | 'care' | 'health' | 'tasks', string | null>
  origin: { seed_packet: number | null; supplier: number | null; purchase_cost: string | null; notes: string } | null
  history: Array<{ id: number; type: string; occurred_on: string; reason: string; reversal_of: number | null }>
}

// Where a plant is. A plant in a tray records the cell rather than the bench
// the tray stands on, because the tray's own placement already says that.
type PlantPlacementType = 'seed_tray_cell' | 'garden_square' | 'location'

interface SpecificPlantLocation {
  pk: number
  specific_plant: number
  location_type: PlantPlacementType
  seed_tray_cell?: number
  garden_square?: number
  location?: number
  started: string
  ended?: string
  notes?: string
  override_reason: string
}

interface SpecificPlantLocationCreate {
  specific_plant: number
  location_type: PlantPlacementType
  seed_tray_cell?: number
  garden_square?: number
  location?: number
  started: string
  notes?: string
}

interface SpecificPlantMove {
  location_type: PlantPlacementType
  seed_tray_cell?: number
  garden_square?: number
  location?: number
  started?: string
  notes?: string
  override_reason?: string
}

// These mirror plantings.models.PlantLifecycleEvent.EventType and
// plantings.lifecycle.LifecycleState in full, including the commerce facts the
// server records from sales and health rather than from a button here.
type PlantLifecycleEventType =
  | 'germinated'
  | 'ready'
  | 'transplanted'
  | 'retained'
  | 'failed'
  | 'lost'
  | 'culled'
  | 'donated'
  | 'harvest_finished'
  | 'sold'
  | 'returned_available'
  | 'returned_quarantined'
  | 'returned_discarded'
  | 'released_available'
  | 'held_back'
  | 'retention_ended'
  | 'corrected'

type PlantLifecycleState = 'growing' | 'available' | 'retained' | 'donated' | 'failed' | 'lost' | 'culled' | 'harvested' | 'sold' | 'quarantined' | 'discarded'

type PlantOutcomeAction = 'ready' | 'retain' | 'fail' | 'cull' | 'donate' | 'finish-harvest' | 'hold-back' | 'end-retention'

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
  label_code: string
  cell_planting: number
  batch: number
  germinated: string
  notes?: string
  locations: Array<SpecificPlantLocation>
  lifecycle_state: PlantLifecycleState
  sellable: boolean
  quarantined: boolean
  final_outcome: PlantLifecycleEventType | null
  final_outcome_at: string | null
  state_since: string | null
  first_ready_at: string | null
}

// One span a plant spent on offer. A plant held back and graded ready again
// has more than one, which is what the latest state alone cannot show.
interface AvailabilityInterval {
  started: string
  ended: string | null
}

interface SpecificPlantDetail extends SpecificPlant {
  lifecycle_events: Array<PlantLifecycleEvent>
  availability_intervals: Array<AvailabilityInterval>
  growth: NurseryGrowth
  nursery_observations: Array<NurseryObservation>
  attachments: Array<ImageAttachment>
  allocation_status: PlantAllocationStatus
  allocation_orders: Array<PlantAllocationOrder>
}

type PlantAllocationStatus = 'none' | 'tentative' | 'reserved'

interface PlantAllocationOrder {
  order: number
  order_number: string
  status: 'pending' | 'reserved'
}

interface GrowthCatalogValue {
  pk: number
  code: string
  name: string
  display_order: number
  active: boolean
  target_days?: number | null
}

interface NurseryGrowth {
  stage: number | null
  stage_name: string | null
  grade: number | null
  grade_name: string | null
  container: number | null
  container_name: string | null
  container_size: string | null
  container_count: number | null
  height_cm: string | null
  spread_cm: string | null
  root_condition: string | null
  expected_ready: string | null
}

interface NurseryObservation extends NurseryGrowth {
  pk: number
  plants: Array<number>
  cohort: number | null
  occurred_at: string
  photo_url: string
  notes: string
  corrects: number | null
  input_application: number | null
  attachments: Array<ImageAttachment>
}

interface SpecificPlantCreate {
  cell_planting: number
  germinated: string
  notes?: string
}

interface SowingCorrection {
  seeds_used?: number
  quantity?: number
  container_count?: number
  reason: string
}

// The nursery plant register. Rows are a projection of current plants, so a
// row carries where a plant is now rather than everywhere it has been.
interface NurseryRegisterRow {
  pk: number
  label_code: string | null
  batch: number
  batch_code: string
  variety: number
  variety_name: string
  plant_name: string
  germinated: string
  age_days: number
  lifecycle_state: PlantLifecycleState
  // When the current state began, and when the plant was first put on offer.
  // A plant held back yesterday and one held back in March both read
  // `growing`; these are what tell them apart.
  state_since: string | null
  first_ready_at: string | null
  sellable: boolean
  quarantined: boolean
  reserved: boolean
  reserved_until: string | null
  allocation_status: PlantAllocationStatus
  allocation_orders: Array<PlantAllocationOrder>
  final_outcome: PlantLifecycleEventType | null
  final_outcome_at: string | null
  location_type: PlantPlacementType | null
  location_label: string
  seed_tray: number | null
  seed_tray_cell: number | null
  garden_square: number | null
  location: number | null
  // Where the plant is physically standing. For a plant in a tray that is
  // wherever the tray has been wheeled, which is why it differs from location.
  standing_at: number | null
  standing_at_label: string
  located_since: string | null
  stage: number | null
  stage_name: string | null
  stage_overdue: boolean
  grade: number | null
  grade_name: string | null
  container: number | null
  container_name: string | null
  container_size: string | null
  container_count: number | null
  expected_ready: string | null
  cost: string | null
  currency_code: string
}

type NurseryRegisterOrdering =
  | 'age'
  | '-age'
  | 'variety'
  | '-variety'
  | 'location'
  | '-location'
  | 'standing_at'
  | '-standing_at'
  | 'cost'
  | '-cost'
  | 'state'
  | '-state'
  | 'state_since'
  | '-state_since'
  | 'first_ready'
  | '-first_ready'
  | 'batch'
  | '-batch'
  | 'expected_ready'
  | '-expected_ready'

// Keys are the query-parameter names the register endpoint validates.
interface NurseryRegisterFilters {
  variety?: number
  batch?: number
  state?: Array<PlantLifecycleState>
  sellable?: boolean
  quarantined?: boolean
  reserved?: boolean
  allocation_status?: PlantAllocationStatus
  germinated_from?: string
  germinated_to?: string
  location_type?: PlantPlacementType | 'none'
  seed_tray?: number
  generation?: number
  garden_square?: number
  // Matches the location or anything below it, so a greenhouse answers for
  // the bays inside it.
  location?: number
  stage?: number
  grade?: number
  container?: number
  expected_ready_from?: string
  expected_ready_to?: string
  stage_overdue?: boolean
  search?: string
  ordering?: NurseryRegisterOrdering
  page?: number
  page_size?: number
}

// Counts describe the whole filtered selection, never the visible page.
type NurseryRegisterTotals = Record<PlantLifecycleState, number> & {
  total: number
  unresolved: number
  quarantined: number
  reserved: number
  tentative: number
  stage_counts: Record<string, number>
  grade_counts: Record<string, number>
  container_counts: Record<string, number>
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

type BulkPlantAction = 'germinate' | 'move' | 'stage' | 'grade' | 'repot' | 'ready' | 'retain' | 'donate' | 'fail' | 'cull' | 'finish_harvest' | 'hold_back' | 'end_retention'
type BulkPlantAtomicity = 'all_or_nothing' | 'eligible_only'

interface BulkPlantOperationRequest {
  idempotency_key?: string
  action: BulkPlantAction
  atomicity: BulkPlantAtomicity
  occurred_at: string
  reason?: string
  plants: Array<number>
  selection_source?: Record<string, unknown>
  action_payload?: Record<string, unknown>
}

interface BulkPlantPreviewRow {
  plant: number
  eligible: boolean
  conflicts: Array<string>
  before: { lifecycle_state: PlantLifecycleState }
  after: { lifecycle_state: PlantLifecycleState; location_type: PlantPlacementType | null }
}

interface BulkPlantPreview {
  action: BulkPlantAction
  selected: number
  eligible: number
  conflicts: number
  plants: Array<BulkPlantPreviewRow>
  capacity: Array<{
    location: number
    basis: string
    capacity: string
    used: number
    available: number
  }>
  source?: {
    cell_planting?: number
    cell_plantings?: Array<number>
    quantity: number | null
    germinations?: Array<{ cell_planting: number; quantity: number }>
    conflicts: Array<string>
  }
}

interface BulkPlantOperationResult {
  plant: number
  status: 'applied' | 'skipped'
  errors: Array<string>
  lifecycle_event: number | null
  location: number | null
}

interface BulkPlantOperation {
  pk: number
  idempotency_key: string
  action: BulkPlantAction
  atomicity: BulkPlantAtomicity
  occurred_at: string
  reason: string
  selection_source: Record<string, unknown>
  action_payload: Record<string, unknown>
  created_by: number | null
  created: string
  results: Array<BulkPlantOperationResult>
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
  attachments: Array<ImageAttachment>
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

type CohortLifecycleState = 'growing' | 'available' | 'retained' | 'depleted'
type CohortLossCause = 'failed' | 'lost' | 'culled' | 'donated' | 'unspecified'

interface CohortEvent {
  pk: number
  action: string
  occurred_at: string
  reason: string
  loss_cause: CohortLossCause | ''
  quantity_before: number
  quantity_delta: number
  quantity_after: number
  state_before: CohortLifecycleState
  state_after: CohortLifecycleState
  location_before: number | null
  location_after: number | null
  source_cohorts: Array<number>
  created: string
}

interface PlantCohort {
  pk: number
  batch: number
  batch_code: string
  variety: number
  variety_name: string
  plant_name: string
  source_sowing: number | null
  quantity: number
  lifecycle_state: CohortLifecycleState
  quarantined: boolean
  location: number | null
  location_name: string | null
  observed_at: string
  revision: number
  notes: string
  label_code: string
  cost: string | null
  currency_code: string
  stage: number | null
  stage_name: string | null
  grade: number | null
  grade_name: string | null
  container: number | null
  container_name: string | null
  container_size: string | null
  container_count: number | null
  expected_ready: string | null
  created: string
  updated: string
  events?: Array<CohortEvent>
  promoted_plants?: Array<number>
}

interface CohortTotals {
  cohort_count: number
  quantity: number
  growing: number
  available: number
  retained: number
  depleted: number
}

interface CohortPage {
  count: number
  next: string | null
  previous: string | null
  results: Array<PlantCohort>
  cohort_totals: CohortTotals
}

interface CohortFilters {
  search?: string
  batch?: number
  variety?: number
  location?: number
  state?: CohortLifecycleState
  active?: boolean
  quarantined?: boolean
  stage?: number
  grade?: number
  container?: number
  expected_ready_from?: string
  expected_ready_to?: string
  stage_overdue?: boolean
  loss_cause?: CohortLossCause
  page?: number
  page_size?: number
}

interface CohortAvailability {
  cohort_quantity: number
  individual_count: number
  combined_total: number
}

interface CohortObservation {
  batch: number
  source_sowing?: number | null
  quantity: number
  location?: number | null
  notes?: string
  idempotency_key: string
}

interface CohortAction {
  expected_revision: number
  idempotency_key: string
  quantity?: number
  container_count?: number
  location?: number | null
  loss_cause?: CohortLossCause
  reason?: string
}

interface CohortMerge {
  target: number
  sources: Array<number>
  revisions: Record<string, number>
  reason: string
  idempotency_key: string
}

type NurseryPlanStatus = 'draft' | 'approved'
type NurseryPlanDirection = 'backward' | 'forward'
type NurseryDemandSource = 'confirmed_order' | 'forecast' | 'manual'

interface NurseryPlanMilestone {
  pk: number
  stage: number
  stage_name: string
  sequence: number
  planned_date: string
  input_quantity: number
  expected_output: number
  location: number | null
  location_name: string | null
  capacity_basis: string
  capacity_required: string
}

interface NurseryPlanRequirement {
  pk: number
  assumption: number
  required_seeds: number
  required_clusters: number
  required_trays: number
  expected_finished: number
  sowing_date: string
  expected_ready_from: string
  expected_ready_until: string
  assumption_snapshot: Record<string, unknown>
  batch: number | null
  milestones: Array<NurseryPlanMilestone>
  inputs: Array<{ pk: number; item: number; item_name: string; quantity: string; base_unit: string }>
}

interface NurseryPlanDemand {
  pk: number
  plan: number
  variety: number
  variety_name: string
  product_reference: string
  target_quantity: number
  ready_from: string
  ready_until: string
  source: NurseryDemandSource
  priority: number
  customer_reference: string
  order_reference: string
  source_line_reference: string
  notes: string
  requirement: NurseryPlanRequirement | null
}

interface NurseryPlanIssue {
  pk: number
  demand: number | null
  kind: 'seed' | 'input' | 'tray' | 'capacity' | 'assumption'
  message: string
  required_quantity: string | null
  available_quantity: string | null
}

interface NurseryProductionPlan {
  pk: number
  code: string
  version: number
  status: NurseryPlanStatus
  direction: NurseryPlanDirection
  sowing_date: string | null
  supersedes: number | null
  notes: string
  approved_at: string | null
  approved_by: number | null
  created_by: number | null
  created: string
  updated: string
  demand_lines: Array<NurseryPlanDemand>
  issues: Array<NurseryPlanIssue>
}

interface NurseryPlanningStageAssumption {
  pk: number
  assumption: number
  stage: number
  stage_name: string
  sequence: number
  lead_days: number
  loss_rate: string
  location: number | null
  location_name: string | null
  capacity_basis: string
  capacity_per_plant: string
}

interface NurseryPlanningAssumption {
  pk: number
  variety: number
  variety_name: string
  effective_from: string
  effective_until: string | null
  germination_rate: string
  seeds_per_cluster: number
  tray_density: number
  notes: string
  stages: Array<NurseryPlanningStageAssumption>
  inputs: Array<{ pk: number; item: number; item_name: string; quantity_per_plant: string; base_unit: string }>
  created: string
}

interface NurseryPlanVariance {
  demand: number
  batch: number | null
  planned_sowing_date: string
  actual_sowing_date: string | null
  planned_seeds: number
  actual_seeds: number
  seed_variance: number
  planned_germinated: number
  actual_germinated: number
  germination_variance: number
  planned_losses: number
  actual_losses: number
  loss_variance: number
  planned_output: number
  current_output: number
  output_variance: number
  final_availability: number
  planned_ready_date: string
  actual_ready_date: string | null
  ready_variance_days: number | null
  stage_output: Array<{ stage: number; stage_name: string; planned_output: number; actual_output: number; variance: number }>
  batch_status: ProductionBatchStatus | null
}

export {
  AvailabilityInterval,
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
  PlantAllocationStatus,
  PlantOutcome,
  PlantOutcomeAction,
  ReversePlantEvent,
  NewBatchInline,
  NurseryRegisterFilters,
  NurseryRegisterOrdering,
  NurseryRegisterPage,
  NurseryRegisterRow,
  NurseryRegisterSelection,
  BulkPlantAction,
  BulkPlantAtomicity,
  BulkPlantOperationRequest,
  BulkPlantPreview,
  BulkPlantOperation,
  GrowthCatalogValue,
  NurseryGrowth,
  NurseryObservation,
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
  GardenPlantingSource,
  GardenPlantingTracking,
  GardenPlantingDateBasis,
  GardenQuickAddEntry,
  GardenQuickAddReview,
  GardenQuickAddWarning,
  GardenQuickAddedPlanting,
  GardenRegisterDetail,
  GardenRegisterFilters,
  GardenRegisterPage,
  GardenRegisterRow,
  GardenRegisterState,
  GardenRegisterTotals,
  GardenRowDirectPlantingCreate,
  GardenSquareDirectPlantingCreate,
  SeedTrayPlantingCreate,
  SpecificPlant,
  SpecificPlantCreate,
  SpecificPlantDetail,
  PlantPlacementType,
  SpecificPlantLocation,
  SpecificPlantLocationCreate,
  SpecificPlantMove,
  SowingCorrection,
  CohortAction,
  CohortAvailability,
  CohortEvent,
  CohortFilters,
  CohortLifecycleState,
  CohortLossCause,
  CohortMerge,
  CohortObservation,
  CohortPage,
  CohortTotals,
  PlantCohort,
  NurseryDemandSource,
  NurseryPlanDirection,
  NurseryPlanStatus,
  NurseryPlanDemand,
  NurseryPlanIssue,
  NurseryPlanMilestone,
  NurseryPlanRequirement,
  NurseryPlanVariance,
  NurseryPlanningAssumption,
  NurseryPlanningStageAssumption,
  NurseryProductionPlan
}
