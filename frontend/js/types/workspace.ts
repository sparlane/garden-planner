type WorkspaceMode = 'garden' | 'nursery'
type MeasurementSystem = 'metric' | 'imperial'

// How much stock, costing, and traceability detail Garden-profile screens
// show. Meaningful only while mode is 'garden' — a Nursery workspace behaves
// as Advanced regardless of this value. Use isAdvanced() from workspace_mode
// rather than comparing this field directly.
type GardenExperience = 'basic' | 'advanced'

// Whether guided garden setup has been finished or declined. It records the
// gardener's answer, not the state of their data: an established workspace is
// never offered setup because it already has a garden.
type GardenSetupState = 'pending' | 'skipped' | 'complete'

interface Workspace {
  name: string
  legal_name: string
  trading_name: string
  business_address: string
  mode: WorkspaceMode
  garden_experience: GardenExperience
  currency_code: string
  default_tax_rate: string
  sales_prices_include_tax: boolean
  timezone: string
  measurement_system: MeasurementSystem
  override_tolerance_percent: string
  override_tolerance_floor: string
  assumption_tolerance_percent: string
  assumption_minimum_samples: number
  stocktake_two_person_required: boolean
  garden_setup_state: GardenSetupState
  created: string
  updated: string
}

type WorkspaceUpdate = Pick<
  Workspace,
  | 'name'
  | 'legal_name'
  | 'trading_name'
  | 'business_address'
  | 'mode'
  | 'garden_experience'
  | 'currency_code'
  | 'default_tax_rate'
  | 'sales_prices_include_tax'
  | 'timezone'
  | 'measurement_system'
  | 'override_tolerance_percent'
  | 'override_tolerance_floor'
  | 'assumption_tolerance_percent'
  | 'assumption_minimum_samples'
  | 'stocktake_two_person_required'
  | 'garden_setup_state'
>

export { GardenExperience, GardenSetupState, MeasurementSystem, Workspace, WorkspaceMode, WorkspaceUpdate }
