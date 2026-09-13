import { useQuery } from '@tanstack/react-query'

import { getInventoryItems, getSerializedUnits } from '../api/inventory'
import { queryKeys } from '../query'
import { InventoryItem, SerializedInventoryUnit } from '../types/inventory'

// `choosing` is whether a pot is the kind of place being named at all, so a
// screen that merely has the picker in it costs nothing until an operator asks
// for a pot; `item` is the catalog item whose pots are then listed.
interface NumberedPotQuery {
  choosing: boolean
  item: number | ''
}

interface NumberedPotDestinations {
  potItems: Array<InventoryItem>
  pots: Array<SerializedInventoryUnit>
  potsLoading: boolean
}

// A numbered pot is reached through the item it came from, the way a cell is
// reached through its tray: the pots of one catalog item are what an operator
// is choosing between, and every numbered container in the nursery at once
// would not be. The bulk move on the register and the move on a tray page
// offer the same choice, so which pots can be stood in is decided here rather
// than once per screen — a second screen filtering on `active` alone would
// offer pots that have left the nursery, and one listing the whole pot catalog
// would offer pots that have no individual identity to stand a plant in.
function useNumberedPotDestinations({ choosing, item }: NumberedPotQuery): NumberedPotDestinations {
  // Keyed by the filters rather than by this caller, so the active pot catalog
  // is fetched once however many screens ask for it. The tracking filter is
  // applied below instead of on the server for the same reason: the repot
  // picker wants the rest of the catalog out of the same cache entry.
  const itemsQuery = useQuery({
    queryKey: queryKeys.inventory.items('', 'pot_container', '', 'active'),
    queryFn: ({ signal }) => getInventoryItems({ category: 'pot_container', active: true }, signal),
    enabled: choosing
  })
  // Only pots still on hand can be stood in. A sold or wasted one keeps its
  // identity forever, so filtering on `active` alone would offer containers
  // that have left the nursery.
  const potsQuery = useQuery({
    queryKey: queryKeys.inventory.serializedUnits(item, 'available'),
    queryFn: ({ signal }) => getSerializedUnits({ item: item as number, active: true, physical_state: 'available' }, signal),
    enabled: choosing && item !== ''
  })
  return {
    // A pot has to be numbered before a plant can be recorded as standing in
    // it, which is what mixed tracking means; the rest of the pot catalog is
    // bought and consumed by the boxful.
    potItems: (itemsQuery.data ?? []).filter((entry) => entry.tracking_mode === 'mixed'),
    pots: potsQuery.data ?? [],
    potsLoading: choosing && item !== '' && potsQuery.isPending
  }
}

// What a pot is called while it is being chosen: the code printed on it, then
// where it is standing, then whether something is already growing in it. A pot
// in use is still offered — several plants legitimately share one, the way a
// multigerm cell holds several seedlings — so the option says so rather than
// disappearing from the list.
function potOptionLabel(pot: SerializedInventoryUnit): string {
  const standing = pot.current_location_full_name ? ` — ${pot.current_location_full_name}` : ''
  return `${pot.asset_code}${standing}${pot.in_use ? ' (already holding a plant)' : ''}`
}

export { potOptionLabel, useNumberedPotDestinations }
