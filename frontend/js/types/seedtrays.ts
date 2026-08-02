import { SerializedInventoryUnit, SerializedPhysicalState } from './inventory'

interface SeedTrayModelCreate {
  identifier: string
  description: string
  height: number
  x_size: number
  y_size: number
  x_cells: number
  y_cells: number
  cell_size_ml: number
}
interface SeedTrayModel extends SeedTrayModelCreate {
  pk: number
  inventory_item: number
}

interface SeedTrayReceiptCreate {
  supplier: number
  received_date: string
  supplier_reference?: string
  quantity: number
  line_cost_ex_tax: string
  destination: number
  tax_rate?: string
  tax_recoverable?: boolean
  notes?: string
}

interface SeedTray {
  pk: number
  model: number
  inventory_unit: number
  inventory: SerializedInventoryUnit
  created: string
  notes?: string
}

interface SeedTrayFilters {
  model?: number
  location?: number
  physical_state?: SerializedPhysicalState
  in_use?: boolean
}

interface SeedTrayReceiptResponse {
  receipt: number
  receipt_line: number
  trays: SeedTray[]
}

interface SeedTrayCell {
  pk: number
  tray: number
  x_position: number
  y_position: number
}

export { SeedTrayModel, SeedTray, SeedTrayCell, SeedTrayFilters, SeedTrayModelCreate, SeedTrayReceiptCreate, SeedTrayReceiptResponse }
