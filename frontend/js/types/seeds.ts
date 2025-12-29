interface Seed {
  pk: number
  supplier: number
  plant_variety: number
  supplier_code: string
  url: string
  notes: string
}

interface SeedPacket {
  pk: number
  seeds: number
  purchase_date: string
  sow_by: string
  empty: boolean
  notes: string
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
}

export { Seed, SeedPacket, SeedPacketDetails }
