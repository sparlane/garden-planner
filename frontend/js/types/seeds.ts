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

// Where a packet came from. `brand` is whose catalog the seed is in; `supplier`
// is who sold this packet. They differ whenever a branded packet was bought
// retail. A null receipt with origin 'opening' is a packet nothing recorded a
// purchase for, which is a fact rather than missing data.
interface SeedPacketProvenance {
  origin: 'receipt' | 'opening' | null
  brand: number
  brand_name: string
  receipt: number | null
  supplier: number | null
  supplier_name: string | null
  received_date: string | null
  line_cost_ex_tax: string | null
  currency_code: string | null
  tax_rate: string | null
  tax_recoverable: boolean | null
  settled_on: string | null
}

interface SeedPacket {
  pk: number
  seeds: number
  purchase_date: string | null
  sow_by: string | null
  empty: boolean | null
  notes: string
  inventory: SeedPacketInventory | null
  provenance: SeedPacketProvenance
}

interface SeedPacketReceiptCreate {
  seeds: number
  quantity_certainty: SeedQuantityCertainty
  quantity?: string | null
  line_price: string
  supplier_lot_reference?: string
  received_date: string
  sow_by?: string | null
  // Who sold the packet, as opposed to the brand on Seed.supplier. Omit and
  // the server records the brand, which is right when it was bought direct.
  supplier?: number
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

export {
  Seed,
  SeedCreate,
  SeedPacket,
  SeedPacketDetails,
  SeedPacketProvenance,
  SeedPacketInventory,
  SeedPacketReceiptCreate,
  SeedPacketReceiptDraft,
  SeedPacketReconciliation,
  SeedQuantityCertainty
}
