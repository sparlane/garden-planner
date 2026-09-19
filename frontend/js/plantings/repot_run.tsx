import React from 'react'
import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'

import { getSerializedUnits } from '../api/inventory'
import { NumberRange, numberList, parseBenchNumbers, rangeNumbers } from '../inventory/bench_numbers'
import { InventoryItem, SerializedInventoryUnit } from '../types/inventory'
import { PlantRepotting } from '../types/plantings'
import { useNowDatetimeInput } from '../utils'
import { PotCodeField, potOptionLabel, potRefusal, useNumberedPotDestinations } from './pot_destinations'

// What one run may hold, mirroring `plantings.repotting.MAX_REPOTTED_PLANTS`.
// The server holds every plant and pot in the request under a row lock until
// the whole run commits, so the limit is real work rather than a formality.
const MAX_REPOTTED_PLANTS = 500

// One seedling waiting for a pot, named the way the screen it was selected on
// names it — a tray says which cell it came out of, and that is what the
// operator reads while pairing the tray with the bench in front of them.
interface RepotCandidate {
  plantPk: number
  label: string
}

interface RepotRunFormProps {
  candidates: Array<RepotCandidate>
  busy: boolean
  onRepot: (run: PlantRepotting) => Promise<unknown>
  onCancel: () => void
}

type PotAssignments = { [plantPk: number]: SerializedInventoryUnit }

interface PairingOutcome {
  assignments: PotAssignments
  messages: Array<string>
}

// Pair the pots that were just named with the seedlings still waiting for one,
// in the order both are read: the tray is emptied cell by cell into the bench
// as it runs. Pots that are not pots, are not on hand, or are already in this
// run are left out with the reason said out loud rather than dropped, because
// the operator is standing at the bench and needs to know which numbers did
// not take a plant.
function pairPots(
  candidates: Array<RepotCandidate>,
  assignments: PotAssignments,
  wanted: Array<number>,
  found: Array<SerializedInventoryUnit>,
  potItems: Array<InventoryItem>
): PairingOutcome {
  const byNumber = new Map(found.map((pot) => [pot.pk, pot]))
  const taken = new Set(Object.values(assignments).map((pot) => pot.pk))
  const paired: PotAssignments = { ...assignments }
  const waiting = candidates.filter((candidate) => !paired[candidate.plantPk])
  const missing: Array<number> = []
  const refused: Array<string> = []
  const spare: Array<number> = []
  let placed = 0
  for (const number of wanted) {
    const pot = byNumber.get(number)
    if (!pot) {
      missing.push(number)
      continue
    }
    if (taken.has(pot.pk)) {
      refused.push(`#${pot.pk} is already in this run`)
      continue
    }
    const refusal = potRefusal(pot, potItems)
    if (refusal) {
      refused.push(refusal)
      continue
    }
    const candidate = waiting[placed]
    if (!candidate) {
      spare.push(number)
      continue
    }
    paired[candidate.plantPk] = pot
    taken.add(pot.pk)
    placed += 1
  }
  const messages: Array<string> = []
  if (placed) messages.push(`Paired ${placed} seedling${placed === 1 ? '' : 's'} with ${placed === 1 ? 'its pot' : 'their pots'}.`)
  if (refused.length) messages.push(`Left out ${refused.length}: ${refused.join(' ')}`)
  if (missing.length) messages.push(`${numberList(missing)} ${missing.length === 1 ? 'is not a numbered pot' : 'are not numbered pots'}.`)
  if (spare.length) messages.push(`Every selected seedling already has a pot, so ${numberList(spare)} ${spare.length === 1 ? 'was' : 'were'} not needed.`)
  return { assignments: paired, messages }
}

// Emptying a selection of seedlings into a bench of numbered pots, one pot per
// seedling. The pairing table is the request: a bench or a garden square is one
// place that many plants go to, but a pot holds one plant, so the run says
// which number each seedling went into rather than naming a single destination.
//
// Pots are named by number and by range, the way they are written on the bench
// card and in the same words `NumberedPotFills` fills them by. They are
// resolved against the server rather than against a fetched list: an item's
// pots outrun any page, and a screen that paired the first hundred of `81-500`
// would be potting into pots nobody chose.
function RepotRunForm({ candidates, busy, onRepot, onCancel }: RepotRunFormProps) {
  const [assignments, setAssignments] = React.useState<PotAssignments>({})
  const [typed, setTyped] = React.useState('')
  const [notice, setNotice] = React.useState<Array<string>>([])
  const date = useNowDatetimeInput()
  const [notes, setNotes] = React.useState('')
  const { potItems } = useNumberedPotDestinations({ choosing: true, item: '' })

  // A seedling deselected on the screen behind this form takes its pot with
  // it: the pairings are read off the current selection rather than off what
  // has been typed, so a pot freed that way is offered to the next seedling
  // instead of counting as spoken for.
  const pairings = candidates.map((candidate) => ({ candidate, pot: assignments[candidate.plantPk] as SerializedInventoryUnit | undefined }))
  const live: PotAssignments = {}
  pairings.forEach((pairing) => {
    if (pairing.pot) live[pairing.candidate.plantPk] = pairing.pot
  })
  const unpaired = pairings.length - Object.keys(live).length

  function take(outcome: PairingOutcome) {
    setAssignments(outcome.assignments)
    setNotice(outcome.messages)
  }

  const add = useMutation({
    mutationFn: (ranges: Array<NumberRange>) =>
      Promise.all(ranges.map((range) => getSerializedUnits({ number_from: range.from, number_to: range.to, page_size: MAX_REPOTTED_PLANTS }))),
    onSuccess: (pages, ranges) => {
      const outcome = pairPots(candidates, live, rangeNumbers(ranges), pages.flat(), potItems)
      take(outcome)
      if (Object.keys(outcome.assignments).length > Object.keys(live).length) setTyped('')
    }
  })

  function addTyped(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const bench = parseBenchNumbers(typed)
    if (bench.unreadable.length) {
      setNotice([`${bench.unreadable.join(', ')} ${bench.unreadable.length === 1 ? 'is not a number' : 'are not numbers'} or a range of them.`])
      return
    }
    // A range wider than one run is refused before it is asked for: the server
    // answers one page, so `81-5000` would come back as the first few hundred
    // pots and every number past them reported as no pot at all.
    if (bench.count > MAX_REPOTTED_PLANTS) {
      setNotice([`One run pairs at most ${MAX_REPOTTED_PLANTS} pots; that names ${bench.count}.`])
      return
    }
    if (bench.ranges.length) add.mutate(bench.ranges)
  }

  function clear(plantPk: number) {
    setAssignments((current) => {
      const remaining = { ...current }
      delete remaining[plantPk]
      return remaining
    })
  }

  async function save() {
    const parsed = date.instant()
    if (!parsed) return
    await onRepot({
      placements: pairings.filter((pairing) => pairing.pot).map((pairing) => ({ plant: pairing.candidate.plantPk, container_unit: (pairing.pot as SerializedInventoryUnit).pk })),
      started: parsed.toISOString(),
      notes
    })
    setAssignments({})
    setNotes('')
    setNotice([])
  }

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>
          Repot {candidates.length} seedling{candidates.length === 1 ? '' : 's'}
        </Card.Title>
        <p className="mb-2">
          Pair each selected seedling with the pot it is going into. A pot&apos;s number is the one written on it when it was numbered. They are paired in the order the cells were
          selected, so a run of pots typed as a range lands on the seedlings in the same order.
        </p>
        <Form onSubmit={addTyped} className="mb-2">
          <Form.Label htmlFor="repot-pot-numbers">Pot numbers</Form.Label>
          <div className="d-flex gap-2">
            <Form.Control id="repot-pot-numbers" value={typed} placeholder="81-88, 92" onChange={(event) => setTyped(event.target.value)} />
            <Button type="submit" variant="outline-secondary" disabled={add.isPending || potItems.length === 0 || typed.trim() === ''}>
              {add.isPending ? 'Finding…' : 'Pair'}
            </Button>
          </div>
          <Form.Text>A range like 81-88, single numbers, or both separated by commas.</Form.Text>
        </Form>
        <PotCodeField potItems={potItems} onFound={(pot) => take(pairPots(candidates, live, [pot.pk], [pot], potItems))} />
        {potItems.length === 0 && (
          <Alert variant="secondary" className="mt-2 mb-0">
            No pot item is numbered individually yet. A plant stands in a pot by the code printed on it, so number some pots on the inventory item first.
          </Alert>
        )}
        {notice.length > 0 && (
          <Alert className="mt-2" variant="info">
            {notice.map((message) => (
              <div key={message}>{message}</div>
            ))}
          </Alert>
        )}
        <Table responsive size="sm" className="mt-3">
          <thead>
            <tr>
              <th>Seedling</th>
              <th>Pot</th>
              <th aria-label="Unpair" />
            </tr>
          </thead>
          <tbody>
            {pairings.map(({ candidate, pot }) => (
              <tr key={candidate.plantPk}>
                <td>{candidate.label}</td>
                <td className={pot ? undefined : 'text-muted'}>{pot ? potOptionLabel(pot) : 'No pot yet'}</td>
                <td>
                  {pot && (
                    <Button size="sm" variant="link" className="p-0" onClick={() => clear(candidate.plantPk)} aria-label={`Unpair the pot from ${candidate.label}`}>
                      Unpair
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
        <div className="d-flex gap-3 flex-wrap align-items-end">
          <Form.Group controlId="repot-date">
            <Form.Label>When</Form.Label>
            <Form.Control type="datetime-local" value={date.value} onChange={(event) => date.change(event.target.value)} />
          </Form.Group>
          <Form.Group controlId="repot-notes" className="flex-grow-1">
            <Form.Label>Notes (optional)</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Form.Group>
        </div>
        {unpaired > 0 && (
          <p className="text-muted mt-2 mb-0">
            {unpaired} seedling{unpaired === 1 ? ' still needs a pot' : 's still need pots'}. The run moves every selected seedling at once, or none of them.
          </p>
        )}
        <div className="d-flex gap-2 mt-2">
          <Button onClick={save} disabled={busy || !date.value || unpaired > 0 || candidates.length === 0 || candidates.length > MAX_REPOTTED_PLANTS}>
            {busy ? 'Repotting…' : `Repot ${candidates.length} seedling${candidates.length === 1 ? '' : 's'}`}
          </Button>
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
        </div>
        {candidates.length > MAX_REPOTTED_PLANTS && (
          <Alert variant="warning" className="mt-2 mb-0">
            One run repots at most {MAX_REPOTTED_PLANTS} seedlings; this one selects {candidates.length}.
          </Alert>
        )}
      </Card.Body>
    </Card>
  )
}

export { MAX_REPOTTED_PLANTS, RepotCandidate, RepotRunForm }
