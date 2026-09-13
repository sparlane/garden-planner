import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { PotFill, openNumberedPotFills } from '../api/container_fills'
import { InputApplicationForm } from '../applications/application_form'
import { getSerializedUnits } from '../api/inventory'
import { PotCodeField, useNumberedPotDestinations } from '../plantings/pot_destinations'
import { queryKeys } from '../query'
import { InventoryItem, SerializedInventoryUnit } from '../types/inventory'
import { messagesByField } from '../utils'

// What the server will fill in one claim. It holds every pot in the request
// under a row lock until the whole claim commits, so the limit is real work
// rather than a formality; saying it here stops a wide range being typed,
// fetched and sent only to come back refused.
const MAX_BENCH_POTS = 500

// How many numbers a message spells out before it starts counting instead. A
// dozen is enough to go and look at the pots; two hundred is a wall.
const NAMED_IN_A_MESSAGE = 12

interface NumberRange {
  from: number
  to: number
}

interface TypedBench {
  ranges: Array<NumberRange>
  unreadable: Array<string>
  count: number
}

// A bench is described the way it is written on the bench card: `81-123`, or
// `81-123, 130`, or one number on its own. An en dash is accepted because a
// phone keyboard and a copied spreadsheet cell both produce one, and `to`
// because that is how a range gets read out loud.
function parseBenchNumbers(text: string): TypedBench {
  const ranges: Array<NumberRange> = []
  const unreadable: Array<string> = []
  for (const part of text.split(',').map((entry) => entry.trim())) {
    if (!part) continue
    const match = /^#?(\d+)(?:\s*(?:-|–|—|to)\s*#?(\d+))?$/.exec(part)
    const from = match ? Number(match[1]) : 0
    const to = match?.[2] === undefined ? from : Number(match[2])
    if (!match || to < from) unreadable.push(part)
    else ranges.push({ from, to })
  }
  return { ranges, unreadable, count: ranges.reduce((running, range) => running + range.to - range.from + 1, 0) }
}

function rangeNumbers(ranges: Array<NumberRange>): Array<number> {
  const numbers = new Set<number>()
  for (const range of ranges) {
    for (let number = range.from; number <= range.to; number += 1) numbers.add(number)
  }
  return [...numbers].sort((first, second) => first - second)
}

function numberList(numbers: Array<number>): string {
  const named = numbers
    .slice(0, NAMED_IN_A_MESSAGE)
    .map((number) => `#${number}`)
    .join(', ')
  return numbers.length > NAMED_IN_A_MESSAGE ? `${named} and ${numbers.length - NAMED_IN_A_MESSAGE} more` : named
}

// Why a pot that exists still cannot be filled. The same conditions are
// enforced by the server under a lock, which is what makes them true; saying
// them here is what keeps an operator from selecting forty pots and having the
// whole bench refused because two of them hold plants.
function unfillableReason(pot: SerializedInventoryUnit): string | undefined {
  if (!pot.active || pot.physical_state !== 'available') return 'not on hand'
  if (pot.in_use) return 'already holding a plant'
  return undefined
}

interface BenchOutcome {
  added: Array<number>
  unfillable: Array<string>
  missing: Array<number>
}

function benchOutcome(wanted: Array<number>, found: Array<SerializedInventoryUnit>): BenchOutcome & { pots: Array<SerializedInventoryUnit> } {
  const byNumber = new Map(found.map((pot) => [pot.pk, pot]))
  const outcome: BenchOutcome & { pots: Array<SerializedInventoryUnit> } = { added: [], unfillable: [], missing: [], pots: [] }
  for (const number of wanted) {
    const pot = byNumber.get(number)
    if (!pot) {
      outcome.missing.push(number)
      continue
    }
    const reason = unfillableReason(pot)
    if (reason) {
      outcome.unfillable.push(`#${number} is ${reason}`)
      continue
    }
    outcome.added.push(number)
    outcome.pots.push(pot)
  }
  return outcome
}

function outcomeMessages(item: InventoryItem, outcome: BenchOutcome): Array<string> {
  const messages: Array<string> = []
  if (outcome.added.length) messages.push(`Added ${outcome.added.length} pot${outcome.added.length === 1 ? '' : 's'}: ${numberList(outcome.added)}.`)
  if (outcome.unfillable.length) messages.push(`Left out ${outcome.unfillable.length}: ${outcome.unfillable.slice(0, NAMED_IN_A_MESSAGE).join(', ')}.`)
  if (outcome.missing.length)
    messages.push(`${numberList(outcome.missing)} ${outcome.missing.length === 1 ? 'is not a' : 'are not'} ${item.name} ${outcome.missing.length === 1 ? 'pot' : 'pots'}.`)
  return messages
}

interface NumberedPotFillsProps {
  item: InventoryItem
}

// Filling a bench of numbered pots, which is one job done once rather than a
// visit to each pot's own page. Every pot still gets its own fill — a numbered
// pot owns its media history and its cost, and that is the whole reason it is
// numbered — but the claim over them is one request, so the bench is either
// filled or refused with the pots that stopped it named.
//
// Pots are named by number, because the number is what is written on a bench
// card and the codes are opaque by design. The range is resolved against the
// server rather than against a list held in the browser: an item's pots outrun
// any page, and a screen that quietly filled the first hundred of `81-500`
// would be filling pots nobody chose.
function NumberedPotFills({ item }: NumberedPotFillsProps) {
  const cache = useQueryClient()
  const [selected, setSelected] = React.useState<Array<SerializedInventoryUnit>>([])
  const [typed, setTyped] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const [refused, setRefused] = React.useState<Array<string>>([])
  const [notice, setNotice] = React.useState<Array<string>>([])
  const [opened, setOpened] = React.useState<Array<PotFill>>([])
  const [posted, setPosted] = React.useState<number>()
  const { potItems } = useNumberedPotDestinations({ choosing: true, item: '' })

  function choose(pots: Array<SerializedInventoryUnit>) {
    setSelected((current) => {
      const known = new Set(current.map((pot) => pot.pk))
      return [...current, ...pots.filter((pot) => !known.has(pot.pk))].sort((first, second) => first.pk - second.pk)
    })
  }

  const add = useMutation({
    mutationFn: (ranges: Array<NumberRange>) =>
      Promise.all(ranges.map((range) => getSerializedUnits({ item: item.pk, number_from: range.from, number_to: range.to, page_size: MAX_BENCH_POTS }))),
    onSuccess: (pages, ranges) => {
      const outcome = benchOutcome(rangeNumbers(ranges), pages.flat())
      choose(outcome.pots)
      setNotice(outcomeMessages(item, outcome))
      if (outcome.added.length) setTyped('')
    }
  })

  const fill = useMutation({
    mutationFn: () => openNumberedPotFills({ inventory_units: selected.map((pot) => pot.pk), notes }),
    onSuccess: async (fills) => {
      setOpened(fills)
      setPosted(undefined)
      setSelected([])
      setNotes('')
      setRefused([])
      await Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.containerFills.all }),
        cache.invalidateQueries({ queryKey: queryKeys.inventory.all }),
        cache.invalidateQueries({ queryKey: queryKeys.reports.all })
      ])
    },
    onError: (error: unknown) => {
      const fields = messagesByField(error)
      setRefused(fields.inventory_units ?? Object.values(fields).flat())
    }
  })

  // The code field finds any pot a plant could stand in, which is a wider net
  // than one bench: a pot of another size, or one already holding a plant, is
  // a real pot and still not one of these. Saying which it is beats dropping
  // the scan, because the operator is holding the pot and needs to know why it
  // was not taken.
  function scanned(pot: SerializedInventoryUnit) {
    const reason = pot.item === item.pk ? unfillableReason(pot) : `a ${pot.item_name}, not a ${item.name}`
    if (reason) setNotice([`#${pot.pk} cannot join this bench: it is ${reason}.`])
    else {
      choose([pot])
      setNotice([`Added #${pot.pk}.`])
    }
  }

  function addTyped(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const bench = parseBenchNumbers(typed)
    setOpened([])
    if (bench.unreadable.length) {
      setNotice([`${bench.unreadable.join(', ')} ${bench.unreadable.length === 1 ? 'is not a number' : 'are not numbers'} or a range of them.`])
      return
    }
    if (selected.length + bench.count > MAX_BENCH_POTS) {
      setNotice([`A bench holds at most ${MAX_BENCH_POTS} pots in one fill; that would make ${selected.length + bench.count}.`])
      return
    }
    if (bench.ranges.length) add.mutate(bench.ranges)
  }

  return (
    <Card className="mt-4">
      <Card.Body>
        <Card.Title>Fill numbered pots</Card.Title>
        <p>
          Open a fill on each of these pots at once. A pot&apos;s number is the one it was given when it was numbered, and it is shown on the pot&apos;s own page beside the code
          printed on it. Growing media is recorded against each fill afterwards; opening a fill alone records no media use.
        </p>
        <Form onSubmit={addTyped} className="mb-2">
          <Form.Label htmlFor="bench-numbers">Pot numbers</Form.Label>
          <div className="d-flex gap-2">
            <Form.Control id="bench-numbers" value={typed} placeholder="81-123, 130" onChange={(event) => setTyped(event.target.value)} />
            <Button type="submit" variant="outline-secondary" disabled={add.isPending || typed.trim() === ''}>
              {add.isPending ? 'Finding…' : 'Add'}
            </Button>
          </div>
          <Form.Text>A range like 81-123, single numbers, or both separated by commas.</Form.Text>
        </Form>
        <PotCodeField potItems={potItems} onFound={scanned} />
        {notice.length > 0 && (
          <Alert className="mt-2" variant="info">
            {notice.map((message) => (
              <div key={message}>{message}</div>
            ))}
          </Alert>
        )}
        <h3 className="h6 mt-3">
          {selected.length} pot{selected.length === 1 ? '' : 's'} on this bench
        </h3>
        <Table responsive size="sm">
          <thead>
            <tr>
              <th>Pot</th>
              <th>Code</th>
              <th>Standing at</th>
              <th aria-label="Remove" />
            </tr>
          </thead>
          <tbody>
            {selected.map((pot) => (
              <tr key={pot.pk}>
                <td>
                  <Link to={`/inventory/serialized-units/${pot.pk}`}>#{pot.pk}</Link>
                </td>
                <td>{pot.asset_code}</td>
                <td>{pot.current_location_full_name}</td>
                <td>
                  <Button size="sm" variant="link" className="p-0" onClick={() => setSelected(selected.filter((entry) => entry.pk !== pot.pk))}>
                    Remove
                  </Button>
                </td>
              </tr>
            ))}
            {selected.length === 0 && (
              <tr>
                <td colSpan={4}>Add the pots you have filled by number, or by scanning one.</td>
              </tr>
            )}
          </tbody>
        </Table>
        <Form
          onSubmit={(event) => {
            event.preventDefault()
            setOpened([])
            fill.mutate()
          }}
        >
          <Form.Group controlId="bench-notes" className="mb-2">
            <Form.Label>Notes (optional)</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Form.Group>
          <div className="d-flex gap-2">
            <Button type="submit" disabled={selected.length === 0 || fill.isPending}>
              {fill.isPending ? 'Opening…' : `Open ${selected.length} fill${selected.length === 1 ? '' : 's'}`}
            </Button>
            <Button variant="outline-secondary" disabled={selected.length === 0 || fill.isPending} onClick={() => setSelected([])}>
              Clear the bench
            </Button>
          </div>
        </Form>
        {refused.length > 0 && (
          <Alert className="mt-2" variant="danger">
            <p>No fills were opened. The bench is filled as one claim, so these pots have to be sorted out or taken off it first:</p>
            {refused.map((message) => (
              <div key={message}>{message}</div>
            ))}
          </Alert>
        )}
        {opened.length > 0 && (
          <>
            <Alert className="mt-2" variant="success">
              Opened {opened.length} fill{opened.length === 1 ? '' : 's'} on {numberList(opened.map((entry) => entry.inventory_unit ?? 0))}.
            </Alert>
            {/* The mix that went into the bench is one document over all of it,
                because that is how it was mixed and how it was used: the server
                divides the quantity and its cost across the fills named here,
                so each pot still carries its own share of what it cost. */}
            <InputApplicationForm
              title="Record the growing media these pots were filled with"
              targets={opened.map((entry) => ({ key: `container_fill:${entry.pk}`, target_type: 'container_fill' as const, pk: entry.pk, label: `Pot #${entry.inventory_unit}` }))}
              defaultTargetKeys={opened.map((entry) => `container_fill:${entry.pk}`)}
              onPosted={(application) => setPosted(application.pk)}
            />
            {posted !== undefined && (
              <Alert className="mt-2" variant="success">
                Media application #{posted} posted.
              </Alert>
            )}
          </>
        )}
      </Card.Body>
    </Card>
  )
}

export { NumberedPotFills }
