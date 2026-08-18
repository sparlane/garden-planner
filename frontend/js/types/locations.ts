type LocationType =
  | 'site'
  | 'greenhouse'
  | 'tunnel'
  | 'bench'
  | 'container'
  | 'bay'
  | 'receiving'
  | 'storage'
  | 'growing'
  | 'dispatch'
  | 'hold'
  | 'staging'
  | 'quarantine'
  | 'adjustment'
  | 'seed_packet'

// The dimension a location's usable space is measured in. Only this one
// dimension is compared against capacity_value; unlike dimensions are never
// compared. 'area' is recorded for planning and enforces nothing yet, because
// nothing records a footprint to measure against it.
type CapacityBasis = 'none' | 'trays' | 'containers' | 'plants' | 'area'

interface Location {
  pk: number
  name: string
  code: string
  location_type: LocationType
  parent: number | null
  path: string
  // The name with its ancestors in front, because a bare "Bay 2" is ambiguous
  // across three greenhouses.
  full_name: string
  depth: number
  display_order: number
  capacity_basis: CapacityBasis
  capacity_value: string | null
  active: boolean
  notes: string
  created: string
  updated: string
}

interface LocationCreate {
  name: string
  code: string
  location_type: LocationType
  parent?: number
  display_order?: number
  capacity_basis?: CapacityBasis
  capacity_value?: string
  notes?: string
}

type LocationUpdate = Partial<LocationCreate> & { active?: boolean }

interface OccupancyCounts {
  trays: number
  plants: number
  containers: number
}

interface LocationOccupancy {
  location: number
  capacity_basis: CapacityBasis
  capacity_value: string | null
  // What stands directly here, and what stands anywhere below it. A greenhouse
  // aisle and its benches are different answers to "what is in here".
  here: OccupancyCounts
  subtree: OccupancyCounts
  remaining: string | null
  active_health_alerts: number
}

export { CapacityBasis, Location, LocationCreate, LocationOccupancy, LocationType, LocationUpdate, OccupancyCounts }
