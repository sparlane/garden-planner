type WorkspaceMode = 'garden' | 'nursery'
type MeasurementSystem = 'metric' | 'imperial'

interface Workspace {
  name: string
  mode: WorkspaceMode
  currency_code: string
  default_tax_rate: string
  timezone: string
  measurement_system: MeasurementSystem
  override_tolerance_percent: string
  override_tolerance_floor: string
  created: string
  updated: string
}

type WorkspaceUpdate = Pick<
  Workspace,
  'name' | 'mode' | 'currency_code' | 'default_tax_rate' | 'timezone' | 'measurement_system' | 'override_tolerance_percent' | 'override_tolerance_floor'
>

export { MeasurementSystem, Workspace, WorkspaceMode, WorkspaceUpdate }
