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
  InventoryTrackingMode,
  InventoryUnit,
  InventoryUsageBasis,
  ItemUnitConversion,
  ItemUnitConversionCreate,
  UnitCode,
  UnitDimension
}
