type WorkspaceMode = 'garden' | 'nursery'
type MeasurementSystem = 'metric' | 'imperial'

// Whether guided garden setup has been finished or declined. It records the
// gardener's answer, not the state of their data: an established workspace is
// never offered setup because it already has a garden.
type GardenSetupState = 'pending' | 'skipped' | 'complete'

interface Workspace {
  name: string
  mode: WorkspaceMode
  currency_code: string
  default_tax_rate: string
  sales_prices_include_tax: boolean
  timezone: string
  measurement_system: MeasurementSystem
  override_tolerance_percent: string
  override_tolerance_floor: string
  stocktake_two_person_required: boolean
  garden_setup_state: GardenSetupState
  created: string
  updated: string
}

type WorkspaceUpdate = Pick<
  Workspace,
  | 'name'
  | 'mode'
  | 'currency_code'
  | 'default_tax_rate'
  | 'sales_prices_include_tax'
  | 'timezone'
  | 'measurement_system'
  | 'override_tolerance_percent'
  | 'override_tolerance_floor'
  | 'stocktake_two_person_required'
  | 'garden_setup_state'
>

export { GardenSetupState, MeasurementSystem, Workspace, WorkspaceMode, WorkspaceUpdate }
