type WorkspaceMode = 'garden' | 'nursery'
type MeasurementSystem = 'metric' | 'imperial'

interface Workspace {
  name: string
  mode: WorkspaceMode
  currency_code: string
  default_tax_rate: string
  timezone: string
  measurement_system: MeasurementSystem
  created: string
  updated: string
}

type WorkspaceUpdate = Pick<Workspace, 'name' | 'mode' | 'currency_code' | 'default_tax_rate' | 'timezone' | 'measurement_system'>

export { MeasurementSystem, Workspace, WorkspaceMode, WorkspaceUpdate }
