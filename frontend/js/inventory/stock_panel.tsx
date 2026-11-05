import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'

import { countLotStock, getInventoryBalances } from '../api/inventory'
import { queryKeys } from '../query'
import { InventoryBalance, InventoryItem } from '../types/inventory'
import { errorsByField, formatMeasure, formatMoney, formatQuantity } from '../utils'

interface StockRowProps {
  balance: InventoryBalance
  onCounted: (message: string) => void
}

// One row is one lot at one place, the same grain the ledger keeps. What is
// typed is what was counted, not a difference: the server works the gain or
// loss out under the lot lock, so stock that moved since the page loaded
// cannot make the correction wrong.
function StockRow({ balance, onCounted }: StockRowProps) {
  const queryClient = useQueryClient()
  const [count, setCount] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: () => countLotStock(balance.lot, balance.location, count, balance.base_unit, reason),
    onSuccess: async (result) => {
      const moved = result.movement
      onCounted(
        moved
          ? `${balance.lot_identifier} at ${balance.location_full_name}: ${moved.movement_type === 'adjustment_gain' ? 'gained' : 'lost'} ${formatMeasure(moved.quantity, balance.base_unit)}.`
          : `${balance.lot_identifier} at ${balance.location_full_name} already matched the count.`
      )
      setCount('')
      setReason('')
      setError(undefined)
      // A correction moves the item's totals, its low-stock flag and every
      // valuation derived from the ledger, so reports go with the balances.
      await Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all }), queryClient.invalidateQueries({ queryKey: queryKeys.reports.all })])
    },
    onError: (caught: unknown) => {
      const fields = errorsByField(caught)
      setError(fields.quantity ?? fields.reason ?? fields.location ?? fields.lot ?? (caught instanceof Error ? caught.message : String(caught)))
    }
  })

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (count && reason.trim()) mutation.mutate()
  }

  const reserved = Number(balance.reserved_quantity) > 0
  return (
    <tr>
      <td>{balance.lot_identifier}</td>
      <td>{balance.location_full_name}</td>
      <td>
        {formatMeasure(balance.bulk_quantity, balance.base_unit)}
        {balance.low_stock && <span className="badge text-bg-warning ms-2">Low</span>}
        {Number(balance.numbered_quantity) > 0 && <small className="d-block text-muted">plus {formatQuantity(balance.numbered_quantity)} numbered</small>}
      </td>
      <td>{reserved ? formatMeasure(balance.reserved_quantity, balance.base_unit) : '—'}</td>
      <td>{balance.valuation === null ? '—' : formatMoney(balance.valuation, balance.currency_code)}</td>
      <td>
        <Form onSubmit={submit} className="d-flex gap-2 flex-wrap">
          <Form.Control
            size="sm"
            type="number"
            min="0"
            step="any"
            style={{ width: '9rem' }}
            aria-label={`Counted quantity for ${balance.lot_identifier} at ${balance.location_full_name}`}
            placeholder={`Counted (${balance.base_unit})`}
            value={count}
            isInvalid={Boolean(error)}
            onChange={(event) => setCount(event.target.value)}
          />
          <Form.Control
            size="sm"
            style={{ width: '12rem' }}
            aria-label={`Reason for ${balance.lot_identifier} at ${balance.location_full_name}`}
            placeholder="Reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button size="sm" type="submit" disabled={!count || !reason.trim() || mutation.isPending}>
            {mutation.isPending ? 'Saving…' : 'Adjust'}
          </Button>
        </Form>
        {error && <small className="text-danger d-block">{error}</small>}
      </td>
    </tr>
  )
}

interface StockPanelProps {
  item: InventoryItem
}

// The inventory counterpart of the seed packet table: what is on hand, lot by
// lot and place by place, with a physical count beside each row. Numbered
// units are left out of the count — each one is its own record — so the figure
// compared is the loose stock.
function StockPanel({ item }: StockPanelProps) {
  const [notice, setNotice] = React.useState<string>()
  const { data: balances = [], isError } = useQuery({
    queryKey: queryKeys.inventory.balances(item.pk),
    queryFn: ({ signal }) => getInventoryBalances(item.pk, signal)
  })

  return (
    <Card className="mt-4">
      <Card.Body>
        <Card.Title>Stock on hand</Card.Title>
        <p>
          Count what is physically there and enter it with a reason. The difference from the ledger is posted as a gain or a loss, so the history shows what changed and why. For a
          whole location at once, use a stocktake.
        </p>
        {notice && (
          <Alert variant="success" dismissible onClose={() => setNotice(undefined)}>
            {notice}
          </Alert>
        )}
        {isError && <Alert variant="danger">Could not load the stock for {item.name}. Please try again.</Alert>}
        <Table responsive hover striped>
          <thead>
            <tr>
              <th>Lot</th>
              <th>Location</th>
              <th>On hand</th>
              <th>Reserved</th>
              <th>Value</th>
              <th>Physical count</th>
            </tr>
          </thead>
          <tbody>
            {balances.map((balance) => (
              <StockRow key={`${balance.lot}:${balance.location}`} balance={balance} onCounted={setNotice} />
            ))}
            {balances.length === 0 && (
              <tr>
                <td colSpan={6}>No stock of this item has been received yet.</td>
              </tr>
            )}
          </tbody>
        </Table>
      </Card.Body>
    </Card>
  )
}

export { StockPanel }
