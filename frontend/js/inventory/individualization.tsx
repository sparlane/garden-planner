import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'

import { Link } from 'react-router'

import { getInventoryBalances, individualizeLotUnits } from '../api/inventory'
import { queryKeys } from '../query'
import { InventoryBalance, InventoryItem } from '../types/inventory'
import { errorsByField, formatQuantity } from '../utils'

// Numbering writes no stock movement, so the lot's own history and every
// valuation derived from it are untouched. What changes is the split between
// what is still loose and what now has an asset code, which the balance rows
// carry and the reports read, so both prefixes go.
function invalidateNumbering(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all }), queryClient.invalidateQueries({ queryKey: queryKeys.reports.all })])
}

interface NumberedUnitsPanelProps {
  item: InventoryItem
}

// Numbering is one-way for stock that has been used, so the count is entered
// against one lot at one place rather than against the item as a whole: pots
// from two deliveries cost different amounts, and the asset carries the cost
// of the lot it came out of.
function NumberedUnitsPanel({ item }: NumberedUnitsPanelProps) {
  const queryClient = useQueryClient()
  const [selected, setSelected] = React.useState<string>()
  const [count, setCount] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string>()
  const [numbered, setNumbered] = React.useState<Array<{ pk: number; asset_code: string }>>([])
  const { data: balances = [] } = useQuery({
    queryKey: queryKeys.inventory.balances(item.pk),
    queryFn: ({ signal }) => getInventoryBalances(item.pk, signal)
  })
  const mutation = useMutation({
    mutationFn: ({ lot, location }: { lot: number; location: number }) => individualizeLotUnits(lot, { location, count: Number(count), reason }),
    onSuccess: async (units) => {
      setNumbered(units.map((unit) => ({ pk: unit.pk, asset_code: unit.asset_code })))
      setCount('')
      setReason('')
      setError(undefined)
      await invalidateNumbering(queryClient)
    },
    // A count above what is loose and a lot of unknown quantity both name
    // their field. Falling back on the status alone would tell somebody who
    // asked for sixty pots out of ten only that the request failed.
    onError: (caught: unknown) => {
      const fields = errorsByField(caught)
      setError(fields.count ?? fields.lot ?? fields.location ?? (caught instanceof Error ? caught.message : String(caught)))
    }
  })

  function rowKey(balance: InventoryBalance) {
    return `${balance.lot}:${balance.location}`
  }

  const chosen = balances.find((balance) => rowKey(balance) === selected)

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (chosen) {
      mutation.mutate({ lot: chosen.lot, location: chosen.location })
    }
  }

  return (
    <Card className="mt-4">
      <Card.Body>
        <Card.Title>Numbered units</Card.Title>
        <p>
          Give individual asset codes to some of {item.name}. Numbering takes nothing out of stock — it records that those containers are now tracked one by one, and the rest stay
          loose. It cannot be undone once a numbered container has moved, been labelled or been promised to an order.
        </p>
        <Table responsive hover striped>
          <thead>
            <tr>
              <th aria-label="Select" />
              <th>Lot</th>
              <th>Location</th>
              <th>Loose</th>
              <th>Numbered</th>
              <th>On hand</th>
            </tr>
          </thead>
          <tbody>
            {balances.map((balance) => (
              <tr key={rowKey(balance)} className={selected === rowKey(balance) ? 'table-primary' : undefined}>
                <td>
                  <Form.Check
                    type="radio"
                    name="numbering-lot"
                    aria-label={`Number units from ${balance.lot_identifier} at ${balance.location_full_name}`}
                    checked={selected === rowKey(balance)}
                    onChange={() => setSelected(rowKey(balance))}
                  />
                </td>
                <td>{balance.lot_identifier}</td>
                <td>{balance.location_full_name}</td>
                <td>{formatQuantity(balance.bulk_quantity)}</td>
                <td>{formatQuantity(balance.numbered_quantity)}</td>
                <td>{formatQuantity(balance.physical_quantity)}</td>
              </tr>
            ))}
            {balances.length === 0 && (
              <tr>
                <td colSpan={6}>No stock of this item has been received yet.</td>
              </tr>
            )}
          </tbody>
        </Table>
        <Form onSubmit={submit} className="d-flex align-items-end gap-2 flex-wrap">
          <Form.Group>
            <Form.Label>How many to number</Form.Label>
            <Form.Control type="number" min={1} value={count} isInvalid={Boolean(error)} onChange={(event) => setCount(event.target.value)} />
          </Form.Group>
          <Form.Group className="flex-grow-1">
            <Form.Label>Reason (optional)</Form.Label>
            <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} />
          </Form.Group>
          <Button type="submit" disabled={!chosen || !count || mutation.isPending}>
            {mutation.isPending ? 'Numbering…' : 'Number units'}
          </Button>
        </Form>
        {error && (
          <Alert className="mt-2 mb-0" variant="danger">
            {error}
          </Alert>
        )}
        {numbered.length > 0 && (
          <Alert className="mt-2 mb-0" variant="success">
            Numbered {numbered.length}. Each one now has a code to print:{' '}
            {numbered.map((unit, index) => (
              <React.Fragment key={unit.pk}>
                {index > 0 && ', '}
                <Link to={`/inventory/serialized-units/${unit.pk}`}>{unit.asset_code}</Link>
              </React.Fragment>
            ))}
          </Alert>
        )}
      </Card.Body>
    </Card>
  )
}

export { NumberedUnitsPanel }
