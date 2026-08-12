interface PlantFamily {
  pk: number
  name: string
  notes: string | null
}

interface PlantFamilyCreate {
  name: string
  notes?: string
}

interface PlantingDetails {
  spacing?: number | null
  inter_row_spacing?: number | null
  plants_per_square_foot?: number | null
  germination_days_min?: number | null
  germination_days_max?: number | null
  maturity_days_min?: number | null
  maturity_days_max?: number | null
}

type MaturityBasis = 'seed' | 'transplanting'

interface Plant extends PlantingDetails {
  pk: number
  family: number
  name: string
  notes: string | null
  maturity_basis: MaturityBasis
}

interface PlantCreate extends PlantingDetails {
  family: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis
}

interface PlantVariety extends PlantingDetails {
  pk: number
  plant: number
  name: string
  notes: string | null
  maturity_basis: MaturityBasis | null
  effective_maturity_basis: MaturityBasis
}

interface PlantVarietyCreate extends PlantingDetails {
  plant: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis | null
}

export { MaturityBasis, PlantFamily, Plant, PlantVariety, PlantingDetails, PlantFamilyCreate, PlantCreate, PlantVarietyCreate }
