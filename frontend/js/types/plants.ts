interface PlantFamily {
  pk: number
  name: string
  notes: string | null
  active: boolean
}

interface PlantFamilyCreate {
  name: string
  notes?: string
  active?: boolean
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
  active: boolean
}

interface PlantCreate extends PlantingDetails {
  family: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis
  active?: boolean
}

interface PlantVariety extends PlantingDetails {
  pk: number
  plant: number
  name: string
  notes: string | null
  maturity_basis: MaturityBasis | null
  effective_maturity_basis: MaturityBasis
  active: boolean
}

interface PlantVarietyCreate extends PlantingDetails {
  plant: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis | null
  active?: boolean
}

export { MaturityBasis, PlantFamily, Plant, PlantVariety, PlantingDetails, PlantFamilyCreate, PlantCreate, PlantVarietyCreate }
