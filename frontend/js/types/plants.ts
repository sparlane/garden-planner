interface PlantFamily {
  pk: number
  name: string
  notes: string
}

interface PlantFamilyCreate {
  name: string
  notes?: string
}

interface PlantingDetails {
  spacing?: number
  inter_row_spacing?: number
  plants_per_square_foot?: number
  germination_days_min?: number
  germination_days_max?: number
  maturity_days_min?: number
  maturity_days_max?: number
}

interface Plant extends PlantingDetails {
  pk: number
  family: number
  name: string
  notes: string
}

interface PlantCreate extends PlantingDetails {
  family: number
  name: string
  notes?: string
}

interface PlantVariety extends PlantingDetails {
  pk: number
  plant: number
  name: string
  notes: string
}

interface PlantVarietyCreate extends PlantingDetails {
  plant: number
  name: string
  notes?: string
}

export { PlantFamily, Plant, PlantVariety, PlantingDetails, PlantFamilyCreate, PlantCreate, PlantVarietyCreate }
