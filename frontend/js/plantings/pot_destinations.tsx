import React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Button, Form } from 'react-bootstrap'

import { getInventoryItems, getSerializedUnits } from '../api/inventory'
import { findPotByCode } from '../inventory/code_lookup'
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

// What a pot is called while it is being chosen: its number, then the code
// printed on it, then where it is standing, then whether something is already
// growing in it. The number comes first because it is what an operator reads
// off a bench and types into a range, and it is the pot's own identity — issued
// once, never reused, unique across the nursery — rather than a second name for
// it. It is also the pot's name everywhere below the label — its own inventory
// page, its movement history, the URL of both — so a pot chosen here can be
// carried to those without reading the code back off the container. A pot in
// use is still offered — several plants legitimately share one, the way a
// multigerm cell holds several seedlings — so the option says so rather than
// disappearing from the list.
function potOptionLabel(pot: SerializedInventoryUnit): string {
  const standing = pot.current_location_full_name ? ` — ${pot.current_location_full_name}` : ''
  return `#${pot.pk} ${pot.asset_code}${standing}${pot.in_use ? ' (already holding a plant)' : ''}`
}

// What a typed or scanned code turned out to be: the pot, when it is one that
// can be stood in, and in every other case the reason it is not, in words that
// name what was found rather than just refusing.
interface PotLookup {
  pot?: SerializedInventoryUnit
  message: string
}

// Resolution is shared with navigation; only choosing a destination applies
// these placement refusals.
async function choosePotByCode(value: string, potItems: Array<InventoryItem>): Promise<PotLookup> {
  const found = await findPotByCode(value)
  if (found.target) return { message: `${found.target.display} is not a pot.` }
  if (!found.unit) return { message: found.message }
  return standablePot(found.unit, potItems)
}

// Why a pot that exists still cannot be stood in, in the words the operator
// needs: they are holding the container, so a dropped scan has to say what was
// wrong with it. A pot already holding a plant is not one of these — several
// plants legitimately share one — which is why that is said in the label
// instead of refused here.
function potRefusal(unit: SerializedInventoryUnit, potItems: Array<InventoryItem>): string | undefined {
  if (!potItems.some((item) => item.pk === unit.item)) {
    return `${unit.item_name} #${unit.pk} is not a pot a plant can stand in.`
  }
  if (!unit.active || unit.physical_state !== 'available') {
    return `#${unit.pk} ${unit.asset_code} is not on hand.`
  }
  return undefined
}

// The checks every way of naming a pot shares: that it is a pot at all, and
// that it is still in the nursery.
function standablePot(unit: SerializedInventoryUnit | undefined, potItems: Array<InventoryItem>): PotLookup {
  if (!unit) return { message: 'No pot carries that number.' }
  const refusal = potRefusal(unit, potItems)
  if (refusal) return { message: refusal }
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
    mutationFn: (code: string) => choosePotByCode(code, potItems),
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
      <Form.Label htmlFor="pot-code">Pot number or code</Form.Label>
      <div className="d-flex gap-2">
        <Form.Control id="pot-code" value={value} placeholder="Scan a code, or type a number" onChange={(event) => setValue(event.target.value)} />
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

export { PotCodeField, potOptionLabel, potRefusal, useNumberedPotDestinations }
