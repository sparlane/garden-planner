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

// Which rate a foreign amount is converted at. Mirrors the three methods an
// operator chooses between in `workspaces.conversion`; the fourth the server
// records, `base_currency`, is the identity conversion of an amount already in
// the workspace's own currency and is not a policy anybody sets.
type ConversionPolicy = 'spot' | 'period_end' | 'published'

interface Workspace {
  name: string
  legal_name: string
  trading_name: string
  business_address: string
  mode: WorkspaceMode
  garden_experience: GardenExperience
  currency_code: string
  // Whether a record may be entered in a currency other than currency_code.
  // Off hides every currency input; a record already in another currency
  // keeps it and is still displayed in it. Use multiCurrency() from
  // workspace_mode rather than reading this field in a screen.
  multi_currency_enabled: boolean
  // Which rate a foreign amount is converted into currency_code at. It governs
  // what a recorded conversion may claim, not how one is calculated. Use
  // conversionPolicy() from workspace_mode rather than reading this field.
  conversion_policy: ConversionPolicy
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
  | 'multi_currency_enabled'
  | 'conversion_policy'
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

export { ConversionPolicy, GardenExperience, GardenSetupState, MeasurementSystem, Workspace, WorkspaceMode, WorkspaceUpdate }
