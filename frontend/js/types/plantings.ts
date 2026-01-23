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
}

interface GardenSquareTransplantingCreate {
  quantity: number
  location: number
  original_planting: number
  notes?: string
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
}

interface GardenSquareTransplanting extends Planting {
  location: number
  transplanted: string
  original_planting: number
}

interface SeedTrayPlantingDetails {
  pk: number
  plant: string
  variety: string
  planted: string
  quantity: number
  seed_tray?: number
  location?: string
  notes: string
  germination_date_early?: string
  germination_date_late?: string
  transplanted_count: number
}

interface GardenSquarePlanting {
  transplanting_pk?: number
  transplanted?: string
  planting_pk: number
  plant: number
  variety: number
  quantity: number
  planted: string
  location: GardenSquare
  notes: string
  germination_date_early?: string
  germination_date_late?: string
  maturity_date_early?: string
  maturity_date_late?: string
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
  GardenSquareTransplantingCreate
}
