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

interface SpecificPlant {
  pk: number
  cell_planting: number
  batch: number
  germinated: string
  notes?: string
  locations: Array<SpecificPlantLocation>
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

export {
  BatchAction,
  NewBatchInline,
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
  SpecificPlantLocation,
  SpecificPlantLocationCreate,
  SpecificPlantMove,
  SowingCorrection
}
