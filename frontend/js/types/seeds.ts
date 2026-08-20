import { UnitCode } from './inventory'

type SeedQuantityCertainty = 'exact' | 'estimated' | 'unknown'

interface Seed {
  pk: number
  supplier: number
  plant_variety: number
  supplier_code: string
  url: string
  notes: string
  inventory_item: number | null
  base_unit: 'seed' | 'seed_cluster' | null
}

interface SeedCreate {
  // Omit to let the workspace's system-default supplier stand in — the
  // Basic Garden path for seed nobody bought from anyone.
  supplier?: number
  plant_variety: number
  base_unit: 'seed' | 'seed_cluster'
  supplier_code?: string
  url?: string
  notes?: string
}

interface SeedPacketInventory {
  lot: number
  lot_identifier: string
  supplier_lot_reference: string
  location: number
  quantity_certainty: SeedQuantityCertainty
  received_quantity: string | null
  sown_quantity: string
  adjustment_quantity: string
  remaining_quantity: string | null
  base_unit: UnitCode
  acquisition_total: string | null
  effective_base_unit_cost: string | null
  currency_code: string
  empty: boolean | null
  warnings: Array<string>
}

interface SeedPacket {
  pk: number
  seeds: number
  purchase_date: string | null
  sow_by: string | null
  empty: boolean | null
  notes: string
  inventory: SeedPacketInventory | null
}

interface SeedPacketReceiptCreate {
  seeds: number
  quantity_certainty: SeedQuantityCertainty
  quantity?: string | null
  line_price: string
  supplier_lot_reference?: string
  received_date: string
  sow_by?: string | null
  supplier_reference?: string
  tax_rate?: string
  tax_recoverable?: boolean
  notes?: string
}

interface SeedPacketReceiptDraft extends SeedPacketReceiptCreate {
  pk: number
  status: 'draft' | 'posted' | 'reversed'
  base_unit: UnitCode
  packet: number | null
}

interface SeedPacketReconciliation {
  counted_quantity: string
  quantity_certainty: Exclude<SeedQuantityCertainty, 'unknown'>
  reason: string
}

interface SeedPacketDetails {
  pk: number
  plant: string
  variety: string
  supplier: string
  purchase_date: string | null
  sow_by: string | null
  notes: string
  seeds_planted_trays: number
  seeds_planted_direct: number
  transplanted_count: number
  inventory: SeedPacketInventory | null
}

export { Seed, SeedCreate, SeedPacket, SeedPacketDetails, SeedPacketInventory, SeedPacketReceiptCreate, SeedPacketReceiptDraft, SeedPacketReconciliation, SeedQuantityCertainty }
