import { PlantLifecycleState, ProductionBatchStatus } from './plantings'

// Where a layer's cost came from. Each value is also the name of the column
// holding it on the server, so a layer can be walked back to its document.
type CostSourceType = 'application_line' | 'sowing_posting' | 'generation_residual' | 'garden_planting' | 'container_unit' | 'container_dispatch' | 'business_expense'

// What the cost was allocated to. `cohort_sale` and `cohort_loss` name the
// block a quantity was sold or lost out of: anonymous stock keeps no identity
// to carry its cost away with, so the units that left stay an output of their
// batch under their own type. The last three name no individual thing: a pool
// has not reached one yet, a loss never will, and unattributed cost never
// could — a direct-sown row produces a crop rather than a set of seedlings.
type CostTargetType = 'seed_tray_cell' | 'specific_plant' | 'plant_cohort' | 'cohort_sale' | 'cohort_loss' | 'batch_pool' | 'production_loss' | 'unattributed'

// How a layer's share of its source was arrived at.
type CostBasis = 'seeds_sown' | 'cell_volume' | 'per_plant' | 'area' | 'equal_share' | 'direct'

// The buckets a batch's value sits in. Every one is reported even when it is
// zero, so a screen never has to guess whether a missing figure means zero or
// means unsupported.
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
  business_expense: number | null
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
  plant_cohort: number | null
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
  // Null when this plant's own inputs were bought in more than one currency,
  // exactly as the batch's totals are, and then there is no code either.
  cost: string | null
  currency_code: string | null
  state: PlantLifecycleState | null
  disposition: CostBucket | null
}

// One currency's own figures. Amounts are never added across two of them: a
// cost layer carries the currency of the lot it drew from and no exchange rate
// exists in this application to combine them, so the sides are listed instead.
interface CostCurrencyAmount {
  currency_code: string
  amount: string
}

interface BatchCostCurrencyTotal extends CostCurrencyAmount {
  totals: Record<CostBucket, string>
}

// Exactly one of `provisional_total` and `final_total` carries a number. A batch
// is wholly one or the other, so there is never anything in both to add
// together — which is the point, because a provisional figure and a final one
// mean different things and must not be summed.
//
// Two currencies are kept apart the same way. When `mixed_currency` is true
// there is no single figure to show: `currency_code`, both totals and every
// bucket are null, and `currencies` carries one complete set of figures per
// currency for the screen to list side by side.
interface BatchCostProjection {
  currencies: Array<{
    currency_code: string
    committed_subtotal: string
    pending_media_subtotal: string
    projected_total: string | null
    unknown_cost: boolean
    not_yet_allocatable: boolean
  }>
  projected_total: string | null
  currency_code: string | null
  mixed_currency: boolean
  unknown_cost: boolean
  not_yet_allocatable: boolean
}

interface BatchCostBreakdown {
  projection: BatchCostProjection
  batch: number
  code: string
  status: ProductionBatchStatus
  currency_code: string | null
  mixed_currency: boolean
  currencies: Array<BatchCostCurrencyTotal>
  provisional: boolean
  output_finalized_at: string | null
  unknown_cost: boolean
  provisional_total: string | null
  final_total: string | null
  totals: Record<CostBucket, string | null>
  layers: Array<CostLayer>
  plants: Array<CostPlantValue>
  last_run: CostRunSummary | null
}

interface PendingPlantCost {
  kind: 'held_media' | 'pot'
  amount: string | null
  currency_code: string
  unknown_cost: boolean
  not_yet_allocatable: boolean
  reason: string
}

interface PlantCostBreakdown {
  pending: Array<PendingPlantCost>
  pot_requires_plants: Array<number>
  with_pot_available: boolean
  sale_without_pot: string | null
  sale_with_pot: string | null
  plant: number
  batch: number
  // Null when this plant was raised on inputs bought in more than one
  // currency; `currencies` then holds what it cost in each, and both committed
  // values and both sale projections are null.
  currency_code: string | null
  mixed_currency: boolean
  currencies: Array<CostCurrencyAmount>
  // Why the two sale projections are null, when they are: a missing rate
  // between two currencies, a committed cost in a currency the pending pot and
  // media shares are not in, or an ordinary unpriced input.
  sale_blocked: 'mixed_currency' | 'foreign_currency' | 'unknown_cost' | null
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
  BatchCostCurrencyTotal,
  CostAllocationRun,
  CostBasis,
  CostBucket,
  CostCurrencyAmount,
  CostLayer,
  CostPlantValue,
  CostRunSummary,
  CostSourceType,
  CostTargetType,
  PlantCostBreakdown,
  PendingPlantCost,
  RecalculateCostsResponse
}
