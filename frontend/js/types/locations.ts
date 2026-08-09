type LocationType = 'receiving' | 'storage' | 'growing' | 'dispatch' | 'quarantine' | 'adjustment' | 'seed_packet'

interface Location {
  pk: number
  name: string
  code: string
  location_type: LocationType
  active: boolean
  notes: string
  created: string
  updated: string
}

export { Location, LocationType }
