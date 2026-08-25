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
  reorder_level: string | null
  container_size_label: string
  container_volume_ml: number | null
  container_footprint_m2: string | null
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
  container_size_label?: string
  container_volume_ml?: number
  container_footprint_m2?: string
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

interface InventoryBalance {
  lot: number
  lot_identifier: string
  item: number
  item_name: string
  location: number
  location_name: string
  physical_quantity: string
  reserved_quantity: string
  available_quantity: string
  base_unit: UnitCode
  base_unit_cost: string | null
  valuation: string | null
  currency_code: string
  expires_on: string | null
  low_stock: boolean
}

type QuantityCertainty = 'exact' | 'estimated' | 'unknown'
type StockReceiptStatus = 'draft' | 'posted' | 'reversed'
type PurchaseTaxTreatment = 'standard' | 'zero_rated' | 'exempt' | 'out_of_scope' | 'unknown'
type InputTaxSource = 'none' | 'supplier' | 'customs' | 'second_hand'
type ReceiptDocumentType = 'none' | 'taxable_supply' | 'invoice' | 'receipt' | 'buyer_created' | 'customs_entry' | 'contract' | 'bank_record' | 'other'

interface InputTaxWarning {
  code: string
  message: string
  line_id: number | null
}

interface StockReceiptLine {
  pk: number
  item: number
  supplier_lot_reference: string
  expires_on: string | null
  // Null only when the certainty is 'unknown'. A missing number is the honest
  // answer for a sealed sack nobody has weighed; it is not a zero.
  quantity: string | null
  quantity_certainty: QuantityCertainty
  // Exactly one of these is set: a controlled unit or one of the item's own
  // package units.
  unit_code: UnitCode | null
  unit_conversion: number | null
  base_quantity: string | null
  base_unit: UnitCode
  line_cost_ex_tax: string
  supplier_cost_incl_tax: string
  tax_treatment: PurchaseTaxTreatment
  tax_rate: string
  input_tax_source: InputTaxSource
  input_tax_amount: string
  claim_input_tax: boolean
  claimable_percentage: string
  apportionment_basis: string
  recoverable_input_tax: string
  non_recoverable_tax: string
  acquisition_amount: string
  legacy_tax_classification: boolean
  destination: number
  lot: number | null
  created: string
  updated: string
}

// Every field is present on every write. PATCHing `lines` replaces the whole
// set server-side, so a partial line would be stored as a whole one.
interface StockReceiptLineWrite {
  item: number
  supplier_lot_reference: string
  expires_on: string | null
  quantity: string | null
  quantity_certainty: QuantityCertainty
  unit_code: UnitCode | null
  unit_conversion: number | null
  supplier_cost_incl_tax: string
  tax_treatment: PurchaseTaxTreatment
  tax_rate: string
  input_tax_source: InputTaxSource
  input_tax_amount: string
  claim_input_tax: boolean
  claimable_percentage: string
  apportionment_basis: string
  destination: number
}

interface StockReceipt {
  pk: number
  supplier: number
  status: StockReceiptStatus
  received_date: string
  supplier_reference: string
  invoice_date: string | null
  source_document_type: ReceiptDocumentType
  source_document_number: string
  evidence_reference: string
  evidence_url: string
  supplier_name_snapshot: string
  supplier_address_snapshot: string
  supplier_gst_status: 'registered' | 'unregistered' | 'unknown'
  supplier_gst_number: string
  currency_code: string
  notes: string
  created_by: number | null
  posted_at: string | null
  reversed_at: string | null
  // The date the supplier was paid. Recorded after posting through its own
  // action, which is why it is absent from StockReceiptWrite.
  settled_on: string | null
  is_seed_packet_draft: boolean
  created: string
  updated: string
  lines: Array<StockReceiptLine>
  movement_ids: Array<number>
  tax_warnings: Array<InputTaxWarning>
}

interface StockReceiptWrite {
  supplier: number
  received_date: string
  supplier_reference?: string
  invoice_date?: string | null
  source_document_type?: ReceiptDocumentType
  source_document_number?: string
  evidence_reference?: string
  evidence_url?: string
  // Omit this and the server applies the workspace currency.
  currency_code?: string
  notes?: string
  lines: Array<StockReceiptLineWrite>
}

interface InputTaxAdjustment {
  pk: number
  receipt_line: number
  adjustment_date: string
  previous_claimable_percentage: string
  revised_claimable_percentage: string
  tax_adjustment: string
  apportionment_basis: string
  reason: string
  evidence_reference: string
  evidence_url: string
  created_by: number | null
  created: string
}

interface InputTaxAdjustmentWrite {
  receipt_line: number
  adjustment_date: string
  revised_claimable_percentage: string
  apportionment_basis: string
  reason: string
  evidence_reference?: string
  evidence_url?: string
}

interface StockReceiptFilters {
  status?: StockReceiptStatus
  seed_packet?: boolean
}

type StocktakeStatus = 'open' | 'paused' | 'review' | 'approved' | 'posted' | 'reversed'
type StocktakeTargetType = 'lot' | 'seed_packet' | 'tray' | 'cohort' | 'plant'

interface StocktakeVariance {
  pk: number
  kind: 'quantity' | 'missing' | 'excess' | 'misplaced' | 'state_mismatch'
  expected: Record<string, unknown>
  observed: Record<string, unknown>
  source_changed: boolean
  conflict_resolution: string
  resolution_action: string
  resolution_reason: string
  variance_value: string | null
  currency: string | null
}

interface StocktakeCount {
  pk: number
  counted_quantity: string | null
  observed_location: number | null
  observed_state: string
  code_snapshot: string
  notes: string
  counter: number | null
  created: string
}

interface StocktakeTarget {
  pk: number
  target_type: StocktakeTargetType
  target_object_id: number | null
  display: string
  expected_location: number | null
  expected_quantity: string | null
  expected_state: string
  unexpected: boolean
  count_status: 'pending' | 'counted' | 'recount'
  accepted_count: StocktakeCount | null
  counts: Array<StocktakeCount>
  variances: Array<StocktakeVariance>
  reconciliations: Array<{ pk: number; phase: 'post' | 'reverse'; domain: string; result: { app: string; model: string; object_id: number } }>
}

interface Stocktake {
  pk: number
  status: StocktakeStatus
  blind: boolean
  scope: Record<string, unknown>
  notes: string
  counted_at: string
  progress: { counted: number; total: number }
  targets: Array<StocktakeTarget>
  attachments: Array<{ pk: number; target: number | null; url: string; label: string }>
}

interface StocktakeScope {
  location: number
  include_descendants?: boolean
  target_types?: Array<StocktakeTargetType>
  item?: number
  category?: InventoryCategory
  variety?: number
  stage?: number
  tray_state?: string
}

export {
  InventoryBalance,
  InventoryCategory,
  InventoryItem,
  InventoryItemCreate,
  InventoryItemFilters,
  InventoryTrackingMode,
  InventoryUnit,
  InventoryUsageBasis,
  InputTaxAdjustment,
  InputTaxAdjustmentWrite,
  InputTaxSource,
  InputTaxWarning,
  ItemUnitConversion,
  ItemUnitConversionCreate,
  QuantityCertainty,
  PurchaseTaxTreatment,
  ReceiptDocumentType,
  SerializedInventoryUnit,
  SerializedPhysicalState,
  SerializedStockMovement,
  StockReceipt,
  StockReceiptFilters,
  StockReceiptLine,
  StockReceiptLineWrite,
  StockReceiptStatus,
  StockReceiptWrite,
  Stocktake,
  StocktakeCount,
  StocktakeScope,
  StocktakeStatus,
  StocktakeTarget,
  StocktakeTargetType,
  StocktakeVariance,
  UnitCode,
  UnitDimension
}
