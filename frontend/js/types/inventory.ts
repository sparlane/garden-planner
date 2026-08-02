type UnitCode = 'each' | 'seed' | 'seed_cluster' | 'ml' | 'l' | 'g' | 'kg' | 'm2'
type UnitDimension = 'count' | 'volume' | 'mass' | 'area'
type InventoryCategory = 'seed' | 'growing_media' | 'fertilizer_treatment' | 'label' | 'packaging' | 'pot_container' | 'tray' | 'other'
type InventoryTrackingMode = 'lot' | 'serialized'
type InventoryUsageBasis = 'cell_volume' | 'surface_area' | 'per_unit' | 'fixed' | 'manual'

interface InventoryUnit {
  code: UnitCode
  label: string
  dimension: UnitDimension
  reference_unit: UnitCode
  to_reference_multiplier: string
}

type SerializedPhysicalState = 'available' | 'quarantined' | 'lost' | 'retired' | 'dispatched' | 'returned'

interface InventoryLocation {
  pk: number
  name: string
  code: string
  location_type: 'receiving' | 'storage' | 'growing' | 'dispatch' | 'quarantine' | 'adjustment' | 'seed_packet'
  active: boolean
  notes: string
  created: string
  updated: string
}

interface SerializedInventoryUnit {
  pk: number
  item: number
  item_name: string
  source_lot: number
  receipt_line: number | null
  asset_code: string
  acquisition_cost: string | null
  currency_code: string
  current_location: number | null
  physical_state: SerializedPhysicalState
  in_use: boolean
  reconciliation_required: boolean
  active: boolean
  movement_ids: number[]
  created: string
  updated: string
}

interface SerializedStockMovement {
  pk: number
  lot: number
  unit: number | null
  movement_type: string
  quantity: string
  source: number | null
  destination: number | null
  occurred_at: string
  reason: string
  reference: string
  reversal_of: number | null
  reversed_by: number | null
  created: string
}

interface InventoryItem {
  pk: number
  name: string
  sku: string
  category: InventoryCategory
  description: string
  active: boolean
  base_unit: UnitCode
  base_unit_dimension: UnitDimension
  tracking_mode: InventoryTrackingMode
  default_usage_basis: InventoryUsageBasis
  default_usage_rate: string | null
  usage_rate_unit: UnitCode | null
  usage_rate_unit_dimension: UnitDimension | null
  default_fixed_quantity: string | null
  stock_history_started_at: string | null
  created: string
  updated: string
}

interface InventoryItemCreate {
  name: string
  sku?: string
  category: InventoryCategory
  description?: string
  base_unit: UnitCode
  tracking_mode: InventoryTrackingMode
  default_usage_basis: InventoryUsageBasis
  default_usage_rate?: string
  usage_rate_unit?: UnitCode
  default_fixed_quantity?: string
}

interface InventoryItemFilters {
  search?: string
  category?: InventoryCategory
  tracking_mode?: InventoryTrackingMode
  active?: boolean
}

interface ItemUnitConversion {
  pk: number
  item: number
  label: string
  multiplier: string
  active: boolean
  base_unit: UnitCode
  base_unit_dimension: UnitDimension
  created: string
  updated: string
}

interface ItemUnitConversionCreate {
  item: number
  label: string
  multiplier: string
}

export {
  InventoryCategory,
  InventoryItem,
  InventoryItemCreate,
  InventoryItemFilters,
  InventoryLocation,
  InventoryTrackingMode,
  InventoryUnit,
  InventoryUsageBasis,
  ItemUnitConversion,
  ItemUnitConversionCreate,
  SerializedInventoryUnit,
  SerializedPhysicalState,
  SerializedStockMovement,
  UnitCode,
  UnitDimension
}
