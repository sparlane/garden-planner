import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Button, Col, Form, Row } from 'react-bootstrap'

import { getInventoryItems, getInventoryUnits, getStockReceipts } from '../api/inventory'
import { getLocations } from '../api/locations'
import { getSuppliers } from '../api/supplies'
import { queryKeys } from '../query'
import { StockReceiptStatus } from '../types/inventory'
import { ReceiptEditor } from './receipt_editor'
import { ReceiptTable } from './receipt_list'

function InventoryReceiptsView() {
  const [status, setStatus] = React.useState<StockReceiptStatus>('draft')
  const [editing, setEditing] = React.useState<number | 'new' | null>(null)

  const { data: units = [] } = useQuery({
    queryKey: queryKeys.inventory.units,
    queryFn: ({ signal }) => getInventoryUnits(signal)
  })
  const { data: items = [] } = useQuery({
    queryKey: queryKeys.inventory.items('', '', '', 'active'),
    queryFn: ({ signal }) => getInventoryItems({ active: true }, signal)
  })
  const { data: locations = [] } = useQuery({
    queryKey: queryKeys.locations.list('active'),
    queryFn: ({ signal }) => getLocations(signal, true)
  })
  const { data: suppliers = [] } = useQuery({
    queryKey: queryKeys.suppliers.all,
    queryFn: ({ signal }) => getSuppliers(signal)
  })
  // Seed packet drafts are never this screen's business: they have their own
  // editor, and the receipt API refuses to post them from here anyway.
  const { data: receipts = [], isPending } = useQuery({
    queryKey: queryKeys.inventory.receipts(status, 'false'),
    queryFn: ({ signal }) => getStockReceipts({ status, seed_packet: false }, signal)
  })

  const editingReceipt = typeof editing === 'number' ? receipts.find((receipt) => receipt.pk === editing) : undefined

  return (
    <main className="container py-3">
      <h1>Receiving</h1>
      <p>Grow media, fertilizer, labels, packaging, and pots arriving from a supplier. Seed packets and seed trays have their own receiving screens.</p>

      {editing !== null && (
        <ReceiptEditor key={editing} receipt={editingReceipt} items={items} locations={locations} suppliers={suppliers} units={units} onClosed={() => setEditing(null)} />
      )}

      <Row className="g-2 mb-3 align-items-end">
        <Col md={3}>
          <Form.Group controlId="receipt-filter-status">
            <Form.Label>Status</Form.Label>
            <Form.Select
              value={status}
              onChange={(event) => {
                // The editor reads its draft out of this list, so a filter that
                // no longer contains it would silently turn an edit into a new
                // receipt. Close it instead.
                setEditing(null)
                setStatus(event.target.value as StockReceiptStatus)
              }}
            >
              <option value="draft">Draft</option>
              <option value="posted">Posted</option>
              <option value="reversed">Reversed</option>
            </Form.Select>
          </Form.Group>
        </Col>
        <Col md={3}>
          <Button onClick={() => setEditing('new')} disabled={editing === 'new'}>
            Receive inventory
          </Button>
        </Col>
      </Row>

      {isPending ? (
        <div>Loading receipts…</div>
      ) : (
        <ReceiptTable
          receipts={receipts}
          items={items}
          locations={locations}
          suppliers={suppliers}
          onEdit={(pk) => setEditing(pk)}
          onCancelled={(pk) => setEditing((current) => (current === pk ? null : current))}
        />
      )}
    </main>
  )
}

export { InventoryReceiptsView }
