// Where a catalog record came from, and which of its figures still say
// exactly what that reference set supplied. Both are read-only: editing a
// figure is what makes it the gardener's, so there is nothing to set.
interface ReferencedRecord {
  reference_source: string
  reference_fields: Array<string>
}

interface PlantFamily extends ReferencedRecord {
  pk: number
  name: string
  notes: string | null
  active: boolean
  merged_into: number | null
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

interface Plant extends PlantingDetails, ReferencedRecord {
  pk: number
  family: number
  name: string
  notes: string | null
  maturity_basis: MaturityBasis
  active: boolean
  merged_into: number | null
}

interface PlantCreate extends PlantingDetails {
  family: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis
  active?: boolean
}

interface PlantVariety extends PlantingDetails, ReferencedRecord {
  pk: number
  plant: number
  name: string
  notes: string | null
  maturity_basis: MaturityBasis | null
  effective_maturity_basis: MaturityBasis
  active: boolean
  merged_into: number | null
}

interface PlantVarietyCreate extends PlantingDetails {
  plant: number
  name: string
  notes?: string
  maturity_basis?: MaturityBasis | null
  active?: boolean
}

// What the starter set covers, whether it was created now, adopted from what
// the gardener already had, or left alone.
interface StarterCrops {
  families: Array<PlantFamily>
  plants: Array<Plant>
  varieties: Array<PlantVariety>
}

export { MaturityBasis, PlantFamily, Plant, PlantVariety, PlantingDetails, PlantFamilyCreate, PlantCreate, PlantVarietyCreate, ReferencedRecord, StarterCrops }
