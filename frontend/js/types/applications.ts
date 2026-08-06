import { UnitCode } from './inventory'

type InputApplicationStatus = 'draft' | 'posted' | 'reversed'

type ApplicationUsageBasis = 'cell_volume' | 'surface_area' | 'per_unit' | 'fixed' | 'manual'

type ApplicationTargetType = 'batch' | 'seed_tray_cell' | 'specific_plant' | 'inventory_unit' | 'garden_area' | 'garden_bed' | 'garden_row' | 'garden_square'

const APPLICATION_STATUS_LABELS: Record<InputApplicationStatus, string> = {
  draft: 'Draft',
  posted: 'Posted',
  reversed: 'Reversed'
}

const TARGET_TYPE_LABELS: Record<ApplicationTargetType, string> = {
  batch: 'Production batch',
  seed_tray_cell: 'Tray cell',
  specific_plant: 'Plant',
  inventory_unit: 'Serialized unit',
  garden_area: 'Garden area',
  garden_bed: 'Garden bed',
  garden_row: 'Garden row',
  garden_square: 'Garden square'
}

interface InputApplicationTarget {
  pk: number
  target_type: ApplicationTargetType
  target: number
  label: string
  weight: string
  cell_volume_ml: number | null
  area_m2: string | null
}

interface InputApplicationLine {
  pk: number
  item: number
  lot: number
  usage_basis: ApplicationUsageBasis
  base_unit: UnitCode
  configured_rate: string | null
  configured_rate_unit: string
  configured_fixed_quantity: string | null
  fill_factor: string | null
  formula_basis_quantity: string | null
  formula_basis_unit: string
  calculated_base_quantity: string | null
  applied_quantity: string
  unit_code: UnitCode | null
  unit_conversion: number | null
  applied_base_quantity: string
  waste_quantity: string
  waste_base_quantity: string
  waste_reason: string
  override_reason: string
  notes: string
  consumption_movement: number | null
  waste_movement: number | null
  targets: Array<InputApplicationTarget>
}

interface InputApplication {
  pk: number
  status: InputApplicationStatus
  batch: number | null
  applied_at: string
  source_location: number
  notes: string
  target_summary: string
  revision: number
  created_by: number | null
  posted_at: string | null
  reversed_at: string | null
  reverse_reason: string
  reversed_by: number | null
  created: string
  updated: string
  lines: Array<InputApplicationLine>
}

interface ApplicationPreviewLine {
  pk: number
  item: number
  lot: number
  usage_basis: ApplicationUsageBasis
  basis_quantity: string | null
  basis_unit: string
  target_count: number
  formula: string
  calculated_base_quantity: string | null
  applied_base_quantity: string
  waste_base_quantity: string
  base_unit: UnitCode
  available_base_quantity: string
  available_after_base_quantity: string
  override_required: boolean
  short: boolean
}

interface ApplicationPreview {
  revision: number
  availability_digest: string
  target_summary: string
  lines: Array<ApplicationPreviewLine>
}

interface ApplicationTargetInput {
  target_type: ApplicationTargetType
  target: number
  weight?: string
}

interface ApplicationLineInput {
  item: number
  lot: number
  applied_quantity: string
  unit_code?: UnitCode | null
  unit_conversion?: number | null
  usage_basis?: ApplicationUsageBasis | ''
  fill_factor?: string | null
  waste_quantity?: string
  waste_reason?: string
  override_reason?: string
  notes?: string
  targets?: Array<ApplicationTargetInput>
  tray?: number | null
}

interface InputApplicationCreate {
  applied_at: string
  source_location: number
  batch?: number | null
  notes?: string
  lines: Array<ApplicationLineInput>
}

interface PostApplication {
  revision?: number
  availability_digest?: string
}

interface ReverseApplication {
  reason: string
}

interface InputApplicationFilters {
  status?: InputApplicationStatus | ''
  batch?: number | ''
  item?: number | ''
  applied_from?: string
  applied_to?: string
}

export {
  APPLICATION_STATUS_LABELS,
  TARGET_TYPE_LABELS,
  ApplicationLineInput,
  ApplicationPreview,
  ApplicationPreviewLine,
  ApplicationTargetInput,
  ApplicationTargetType,
  ApplicationUsageBasis,
  InputApplication,
  InputApplicationCreate,
  InputApplicationFilters,
  InputApplicationLine,
  InputApplicationStatus,
  InputApplicationTarget,
  PostApplication,
  ReverseApplication
}
