import React from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Form } from 'react-bootstrap'

import { settleStockReceipt } from '../api/inventory'
import { errorsByField } from '../utils'
import { queryKeys } from '../query'

// Settling moves no stock, so the applications digest cannot have gone stale
// and only the receipt itself changes. The reports prefix goes because this is
// the date the payments and hybrid bases claim input tax on: a GST period the
// user is about to open reads differently the moment a payment is recorded.
// The seeds prefix goes because a packet renders its receipt's paid state as
// part of its own provenance.
function invalidateSettlement(queryClient: ReturnType<typeof useQueryClient>) {
  return Promise.all([
    queryClient.invalidateQueries({ queryKey: queryKeys.inventory.all }),
    queryClient.invalidateQueries({ queryKey: queryKeys.reports.all }),
    queryClient.invalidateQueries({ queryKey: queryKeys.seeds.packets.all })
  ])
}

interface ReceiptSettlementProps {
  receipt: number
  settledOn: string | null
}

// Takes the receipt's id and paid date rather than a StockReceipt, because a
// seed packet reaches its receipt through provenance and never holds the
// document itself. One control for both screens, so a date that decides a GST
// claim is recorded the same way wherever it is entered from.
function ReceiptSettlement({ receipt, settledOn }: ReceiptSettlementProps) {
  const queryClient = useQueryClient()
  const [open, setOpen] = React.useState(false)
  const [draft, setDraft] = React.useState('')
  const [error, setError] = React.useState<string>()
  const mutation = useMutation({
    mutationFn: (value: string | null) => settleStockReceipt(receipt, value),
    onSuccess: () => {
      setOpen(false)
      setError(undefined)
      return invalidateSettlement(queryClient)
    },
    // The two rejections worth reading are a future date and a receipt that is
    // not posted, and both name their field. Falling back on the bare status
    // would tell somebody who mistyped a year only that the request failed.
    onError: (caught: unknown) => {
      const fields = errorsByField(caught)
      setError(fields.settled_on ?? fields.status ?? (caught instanceof Error ? caught.message : String(caught)))
    }
  })

  if (!open) {
    return (
      <div className="d-flex flex-column align-items-start">
        {settledOn ?? <span className="text-muted">Not recorded</span>}
        <Button
          size="sm"
          variant="link"
          className="p-0"
          onClick={() => {
            setDraft(settledOn ?? '')
            setOpen(true)
          }}
        >
          {settledOn ? 'Change' : 'Record payment'}
        </Button>
      </div>
    )
  }
  return (
    <div className="d-flex flex-column gap-1">
      <Form.Control size="sm" type="date" aria-label="Supplier paid on" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <div className="d-flex gap-1">
        <Button size="sm" disabled={!draft || mutation.isPending} onClick={() => mutation.mutate(draft)}>
          {mutation.isPending ? 'Saving…' : 'Save'}
        </Button>
        {settledOn && (
          <Button size="sm" variant="outline-danger" disabled={mutation.isPending} onClick={() => mutation.mutate(null)}>
            Clear
          </Button>
        )}
        <Button size="sm" variant="outline-secondary" onClick={() => setOpen(false)}>
          Cancel
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

export { ReceiptSettlement }
