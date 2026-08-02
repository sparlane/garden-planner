import { GardenSquare } from './garden'

interface PlantingCreate {
  seeds_used: number
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
  Planting,
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
