import React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button, Form } from 'react-bootstrap'

import { getInventoryItems, getSerializedUnit, getSerializedUnits } from '../api/inventory'
import { resolveLabel } from '../api/labels'
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

// What a typed or scanned code turned out to be: the pot, when it is one that
// can be stood in, and in every other case the reason it is not, in words that
// name what was found rather than just refusing.
interface PotLookup {
  pot?: SerializedInventoryUnit
  message: string
}

// Find the pot a code belongs to, trying the two codes a pot answers to. The
// label code is what is printed on the pot, and `/labels/resolve/` is asked
// first because it is also what a QR scan pastes in and what knows about a
// code that has been replaced or belongs to another workspace. An asset code
// is the identity the unit itself carries, which is what the pickers show and
// what the container's own page prints, so it is tried when no label uses the
// value.
//
// `potItems` is the gate on what the code is allowed to be. A numbered seed
// tray is an inventory unit with a label too, and the placement rules check
// only that a container is individually numbered, so nothing downstream would
// stop a tray's code from standing a plant "in" the tray: a plant in a tray is
// described by its cell.
async function findPotByCode(value: string, potItems: Array<InventoryItem>): Promise<PotLookup> {
  const typed = value.trim()
  const resolution = await resolveLabel(typed)
  let unit: SerializedInventoryUnit | undefined
  if (resolution.status === 'unknown') {
    // The asset-code filter matches on a fragment, so a partial code that
    // names two pots has to be refused rather than guessed at.
    const matches = await getSerializedUnits({ asset_code: typed })
    const exact = matches.find((candidate) => candidate.asset_code.toUpperCase() === typed.toUpperCase())
    if (!exact && matches.length > 1) {
      return { message: `${matches.length} pots match ${typed}. Type or scan the whole code.` }
    }
    unit = exact ?? matches[0]
    if (!unit) return { message: `No label or pot carries the code ${typed}.` }
  } else if (resolution.status !== 'active' || !resolution.target) {
    return { message: resolution.message }
  } else if (resolution.target.target_type !== 'inventoryunit') {
    return { message: `${resolution.target.display} is not a pot.` }
  } else {
    unit = await getSerializedUnit(resolution.target.object_id)
  }
  if (!potItems.some((item) => item.pk === unit.item)) {
    return { message: `${unit.item_name} ${unit.asset_code} is not a pot a plant can stand in.` }
  }
  if (!unit.active || unit.physical_state !== 'available') {
    return { message: `${unit.asset_code} is not on hand.` }
  }
  return { pot: unit, message: `Selected ${potOptionLabel(unit)}.` }
}

interface PotCodeFieldProps {
  potItems: Array<InventoryItem>
  // The screen puts its own pickers on the pot that was found, so the code box
  // never becomes a second answer to where the plant is going.
  onFound: (pot: SerializedInventoryUnit) => void
}

// The way into a pot that somebody is holding rather than scrolling for: the
// code goes in, the item and the pot come out already chosen. A nursery numbers
// more pots of one item than a dropdown can usefully list, and the operator is
// standing in front of the one they mean.
function PotCodeField({ potItems, onFound }: PotCodeFieldProps) {
  const [value, setValue] = React.useState('')
  const lookup = useMutation({
    mutationFn: (code: string) => findPotByCode(code, potItems),
    onSuccess: (found) => {
      if (found.pot) onFound(found.pot)
    }
  })
  return (
    <Form
      onSubmit={(event) => {
        event.preventDefault()
        const typed = value.trim()
        if (typed) lookup.mutate(typed)
      }}
    >
      <Form.Label htmlFor="pot-code">Pot code</Form.Label>
      <div className="d-flex gap-2">
        <Form.Control id="pot-code" value={value} placeholder="Scan or type a code" onChange={(event) => setValue(event.target.value)} />
        <Button type="submit" variant="outline-secondary" disabled={lookup.isPending || value.trim() === ''}>
          {lookup.isPending ? 'Finding…' : 'Find'}
        </Button>
      </div>
      {lookup.data && (
        <Form.Text className={lookup.data.pot ? 'text-success' : 'text-danger'} as="p">
          {lookup.data.message}
        </Form.Text>
      )}
    </Form>
  )
}

export { PotCodeField, potOptionLabel, useNumberedPotDestinations }
