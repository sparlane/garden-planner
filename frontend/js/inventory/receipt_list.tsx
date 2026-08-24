import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Form, Table } from 'react-bootstrap'

import { deleteStockReceipt, postStockReceipt, reverseStockReceipt } from '../api/inventory'
import { ReceiptSettlement } from './settlement'
import { formatMeasure } from '../utils'
import { InventoryItem, StockReceipt, StockReceiptLine } from '../types/inventory'
import { Location } from '../types/locations'
import { Supplier } from '../types/suppliers'
import { queryKeys } from '../query'

const STATUS_VARIANTS: Record<string, string> = {
  draft: 'secondary',
  posted: 'success',
  reversed: 'warning'
}

const STATUS_LABELS: Record<string, string> = {
  draft: 'Draft',
  posted: 'Posted',
  reversed: 'Reversed'
}

// One prefix covers receipts, lots, balances, and items: posting a receipt
// creates a lot and a movement, and the first post of an item also stamps its
// stock_history_started_at, which the catalog renders. Applications go too,
// because their cached preview carries an availability digest that a stock
// change invalidates. Seeds and seed trays deliberately do not: the receipt
// API now refuses their items outright, so neither cache can have moved.
function invalidateReceipts(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all }), queryClient.invalidateQueries({ queryKey: queryKeys.applications.all })])
}

// Document-level failures come back as {"lines": "Add at least one receipt
// line."} — one string for the whole document — while field failures come back
// as one object per submitted line. Telling them apart by shape is what keeps a
// document error from being rendered against an arbitrary row.
function documentErrors(body: unknown): Array<string> {
  if (typeof body !== 'object' || body === null) return []
  return Object.entries(body as Record<string, unknown>).flatMap(([field, value]) => {
    if (field === 'lines' && typeof value === 'object' && value !== null) return []
    const messages = Array.isArray(value) ? value : [value]
    return messages.filter((message): message is string => typeof message === 'string')
  })
}

// DRF 3.18 reports nested list errors as an object keyed by the index of each
// line that failed — {"0": {...}} — where earlier versions returned one entry
// per submitted line. Both are flattened back to a positional array so the
// editor can keep rendering errors against the row they belong to.
function positionalLines(lines: unknown): Array<unknown> {
  if (Array.isArray(lines)) return lines
  if (typeof lines !== 'object' || lines === null) return []
  const positional: Array<unknown> = []
  for (const [index, entry] of Object.entries(lines as Record<string, unknown>)) {
    if (!/^\d+$/.test(index)) return []
    positional[Number(index)] = entry
  }
  return positional
}

// Positional, because the server returns line errors aligned with the lines the
// client submitted. DRF spells a single message as either a string or a list of
// one, so both are flattened to the first message.
function lineFieldErrors(body: unknown): Array<Record<string, string>> {
  if (typeof body !== 'object' || body === null) return []
  return positionalLines((body as Record<string, unknown>).lines).map((entry) => {
    if (typeof entry !== 'object' || entry === null) return {}
    return Object.fromEntries(
      Object.entries(entry as Record<string, unknown>).map(([field, value]) => {
        const message = Array.isArray(value) ? value[0] : value
        return [field, typeof message === 'string' ? message : String(message)]
      })
    )
  })
}

function localErrorMessage(caught: unknown) {
  return caught instanceof Error ? caught.message : String(caught)
}

function ReverseReceiptButton({ receipt }: { receipt: StockReceipt }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: () => reverseStockReceipt(receipt.pk, reason),
    onSuccess: () => {
      setOpen(false)
      setReason('')
      setError(undefined)
      return invalidateReceipts(queryClient)
    },
    onError: (caught: unknown) => setError(localErrorMessage(caught))
  })

  if (!open) {
    return (
      <Button size="sm" variant="outline-danger" onClick={() => setOpen(true)}>
        Reverse
      </Button>
    )
  }
  return (
    <div className="d-flex flex-column gap-1">
      <Form.Control size="sm" value={reason} placeholder="Why was this wrong?" onChange={(event) => setReason(event.target.value)} />
      <div className="d-flex gap-1">
        <Button size="sm" variant="danger" disabled={!reason.trim() || mutation.isPending} onClick={() => mutation.mutate()}>
          {mutation.isPending ? 'Reversing…' : 'Confirm'}
        </Button>
        <Button size="sm" variant="outline-secondary" onClick={() => setOpen(false)}>
          Keep
        </Button>
      </div>
      {error && (
        <Alert className="mb-0 py-1 px-2" variant="danger">
          {error}
        </Alert>
      )}
    </div>
  )
}

interface DraftActionsProps {
  receipt: StockReceipt
  onEdit: (pk: number) => void
  onCancelled: (pk: number) => void
}

function DraftActions({ receipt, onEdit, onCancelled }: DraftActionsProps) {
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = React.useState(false)
  const [error, setError] = React.useState<string>()

  const postMutation = useMutation({
    mutationFn: () => postStockReceipt(receipt.pk),
    onSuccess: () => {
      setError(undefined)
      onCancelled(receipt.pk)
      return invalidateReceipts(queryClient)
    },
    onError: (caught: unknown) => setError(localErrorMessage(caught))
  })
  const cancelMutation = useMutation({
    mutationFn: () => deleteStockReceipt(receipt.pk),
    onSuccess: () => {
      setConfirming(false)
      setError(undefined)
      onCancelled(receipt.pk)
      return invalidateReceipts(queryClient)
    },
    onError: (caught: unknown) => setError(localErrorMessage(caught))
  })
  const busy = postMutation.isPending || cancelMutation.isPending

  return (
    <div className="d-flex flex-column gap-1">
      <div className="d-flex gap-1">
        <Button size="sm" variant="outline-secondary" disabled={busy} onClick={() => onEdit(receipt.pk)}>
          Edit
        </Button>
        <Button size="sm" disabled={busy || receipt.lines.length === 0} onClick={() => postMutation.mutate()}>
          {postMutation.isPending ? 'Posting…' : 'Post receipt'}
        </Button>
      </div>
      {confirming ? (
        <div className="d-flex gap-1">
          <Button size="sm" variant="danger" disabled={busy} onClick={() => cancelMutation.mutate()}>
            {cancelMutation.isPending ? 'Cancelling…' : 'Confirm cancel'}
          </Button>
          <Button size="sm" variant="outline-secondary" onClick={() => setConfirming(false)}>
            Keep draft
          </Button>
        </div>
      ) : (
        <Button size="sm" variant="outline-danger" disabled={busy} onClick={() => setConfirming(true)}>
          Cancel draft
        </Button>
      )}
      {error && (
        <Alert className="mb-0 py-1 px-2" variant="danger">
          {error}
        </Alert>
      )}
    </div>
  )
}

// The base quantity is what the ledger will actually hold, so it is shown
// beside the operator's own number rather than instead of it. An unknown
// quantity says so: it will claim no balance at all when this posts.
function LineQuantity({ line }: { line: StockReceiptLine }) {
  if (line.quantity_certainty === 'unknown') {
    return <span className="text-muted">Unknown — claims no balance</span>
  }
  return (
    <>
      <div>{formatMeasure(line.base_quantity, line.base_unit)}</div>
      {line.quantity_certainty === 'estimated' && <div className="text-muted small">estimated</div>}
    </>
  )
}

interface ReceiptLinesTableProps {
  receipt: StockReceipt
  items: Array<InventoryItem>
  locations: Array<Location>
}

function ReceiptLinesTable({ receipt, items, locations }: ReceiptLinesTableProps) {
  if (receipt.lines.length === 0) {
    return <span className="text-muted">No lines yet.</span>
  }
  return (
    <Table size="sm" className="mb-0">
      <thead>
        <tr>
          <th>Item</th>
          <th>Quantity</th>
          <th>Destination</th>
          <th>Cost</th>
          <th>Lot</th>
        </tr>
      </thead>
      <tbody>
        {receipt.lines.map((line) => (
          <tr key={line.pk}>
            <td>{items.find((item) => item.pk === line.item)?.name ?? `#${line.item}`}</td>
            <td>
              <LineQuantity line={line} />
            </td>
            <td>{locations.find((location) => location.pk === line.destination)?.name ?? `#${line.destination}`}</td>
            <td>
              {line.line_cost_ex_tax} {receipt.currency_code}
            </td>
            <td className="small">{line.lot !== null ? `#${line.lot}` : '—'}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

interface ReceiptTableProps {
  receipts: Array<StockReceipt>
  items: Array<InventoryItem>
  locations: Array<Location>
  suppliers: Array<Supplier>
  onEdit: (pk: number) => void
  onCancelled: (pk: number) => void
}

function ReceiptTable({ receipts, items, locations, suppliers, onEdit, onCancelled }: ReceiptTableProps) {
  if (receipts.length === 0) {
    return <p className="text-muted mb-0">No receipts here yet.</p>
  }
  return (
    <Table>
      <thead>
        <tr>
          <th>Received</th>
          <th>Supplier</th>
          <th>Reference</th>
          <th>Status</th>
          <th>Paid</th>
          <th>Lines</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {receipts.map((receipt) => (
          <tr key={receipt.pk} className={receipt.status === 'draft' ? 'table-warning' : undefined}>
            <td>{receipt.received_date}</td>
            <td>{suppliers.find((supplier) => supplier.pk === receipt.supplier)?.name ?? `#${receipt.supplier}`}</td>
            <td>{receipt.supplier_reference || <span className="text-muted">—</span>}</td>
            <td>
              <Badge bg={STATUS_VARIANTS[receipt.status]}>{STATUS_LABELS[receipt.status]}</Badge>
            </td>
            <td>{receipt.status === 'posted' ? <ReceiptSettlement receipt={receipt.pk} settledOn={receipt.settled_on} /> : <span className="text-muted">—</span>}</td>
            <td>
              <ReceiptLinesTable receipt={receipt} items={items} locations={locations} />
            </td>
            <td>
              {receipt.status === 'draft' && <DraftActions receipt={receipt} onEdit={onEdit} onCancelled={onCancelled} />}
              {receipt.status === 'posted' && <ReverseReceiptButton receipt={receipt} />}
              {receipt.status === 'reversed' && <span className="text-muted">—</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

export { ReceiptTable, ReverseReceiptButton, documentErrors, invalidateReceipts, lineFieldErrors, localErrorMessage }
