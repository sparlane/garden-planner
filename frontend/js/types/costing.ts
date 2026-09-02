import { PlantLifecycleState, ProductionBatchStatus } from './plantings'

// Where a layer's cost came from. Each value is also the name of the column
// holding it on the server, so a layer can be walked back to its document.
type CostSourceType = 'application_line' | 'sowing_posting' | 'generation_residual' | 'garden_planting' | 'container_unit'

// What the cost was allocated to. The last three name no individual thing: a
// pool has not reached one yet, a loss never will, and unattributed cost never
// could — a direct-sown row produces a crop rather than a set of seedlings.
type CostTargetType = 'seed_tray_cell' | 'specific_plant' | 'batch_pool' | 'production_loss' | 'unattributed'

// How a layer's share of its source was arrived at.
type CostBasis = 'seeds_sown' | 'cell_volume' | 'per_plant' | 'area' | 'equal_share' | 'direct'

// The buckets a batch's value sits in. `cogs` stays at zero until orders exist;
// it is reported anyway so a screen never has to guess whether a missing figure
// means zero or means unsupported.
type CostBucket = 'plant_inventory' | 'cogs' | 'harvested_output' | 'production_loss' | 'unresolved' | 'unattributed'

// One immutable layer of cost. `amount` is null when the lot it came from has
// no recorded unit cost: unknown, which is not the same as zero and must never
// be rendered as one.
interface CostLayer {
  allocation: number
  run: number
  source_type: CostSourceType
  source: number
  application: number | null
  application_line: number | null
  sowing_posting: number | null
  generation_residual: number | null
  // The numbered pot a sold specimen left inside. It is an asset while it
  // merely holds a plant and becomes one of its inputs when it leaves with it.
  container_unit: number | null
  movement: number | null
  lot: number | null
  item: number | null
  receipt_line: number | null
  target_type: CostTargetType
  seed_tray_cell: number | null
  seed_tray_generation: number | null
  specific_plant: number | null
  basis: CostBasis
  basis_weight: string
  base_quantity: string
  base_unit: string
  unit_cost: string | null
  amount: string | null
  currency_code: string
}

interface CostRunSummary {
  run: number
  trigger: string
  reason: string
  posted_count: number
  reversed_count: number
  created: string
}

interface CostAllocationRun {
  pk: number
  batch: number
  trigger: string
  reason: string
  posted_count: number
  reversed_count: number
  froze_output: boolean
  created_by: number | null
  created: string
}

interface CostPlantValue {
  plant: number
  cost: string
  state: PlantLifecycleState | null
  disposition: CostBucket | null
}

// Exactly one of `provisional_total` and `final_total` carries a number. A batch
// is wholly one or the other, so there is never anything in both to add
// together — which is the point, because a provisional figure and a final one
// mean different things and must not be summed.
interface BatchCostBreakdown {
  batch: number
  code: string
  status: ProductionBatchStatus
  currency_code: string
  provisional: boolean
  output_finalized_at: string | null
  unknown_cost: boolean
  provisional_total: string | null
  final_total: string | null
  totals: Record<CostBucket, string>
  layers: Array<CostLayer>
  plants: Array<CostPlantValue>
  last_run: CostRunSummary | null
}

interface PlantCostBreakdown {
  plant: number
  batch: number
  currency_code: string
  provisional: boolean
  unknown_cost: boolean
  state: PlantLifecycleState | null
  disposition: CostBucket | null
  provisional_value: string | null
  final_value: string | null
  layers: Array<CostLayer>
}

interface RecalculateCostsResponse {
  run: CostAllocationRun | null
  breakdown: BatchCostBreakdown
}

export type {
  BatchCostBreakdown,
  CostAllocationRun,
  CostBasis,
  CostBucket,
  CostLayer,
  CostPlantValue,
  CostRunSummary,
  CostSourceType,
  CostTargetType,
  PlantCostBreakdown,
  RecalculateCostsResponse
}
