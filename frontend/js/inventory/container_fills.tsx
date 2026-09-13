import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Form, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import { getPotFill, getPotFillContents, getPotFills, openPotFill, PotFillTarget } from '../api/container_fills'
import { getInventoryBalances } from '../api/inventory'
import { InputApplicationForm } from '../applications/application_form'
import { queryKeys } from '../query'
import { InventoryItem } from '../types/inventory'
import { errorsByField, formatMoney, formatQuantity } from '../utils'
import { PotFillClean } from './pot_fill_clean'
import { PotFillPlant } from './pot_fill_plant'
import { PotFillNumber } from './pot_fill_number'

function FillContents({ pk }: { pk: number }) {
  const [posted, setPosted] = React.useState<number>()
  const history = useQuery({ queryKey: queryKeys.containerFills.detail(pk), queryFn: ({ signal }) => getPotFill(pk, signal) })
  const { data, isPending, isError } = useQuery({
    queryKey: queryKeys.containerFills.contents(pk),
    queryFn: ({ signal }) => getPotFillContents(pk, signal)
  })
  if (isPending) return <p>Loading fill contents…</p>
  if (isError || !data) return <Alert variant="danger">Could not load fill contents.</Alert>
  const totals = [
    ['Applied', data.costs.applied_cost],
    ['Taken by departed plants', data.costs.departed_cost],
    ['Held in the fill', data.costs.held_cost],
    ['Discarded', data.costs.production_loss],
    ['Recovered', data.costs.recovered_cost],
    ['Rounding difference', data.costs.rounding_difference]
  ]
  return (
    <div className="mt-3">
      {data.status === 'open' && (
        <InputApplicationForm
          title="Add growing media to this fill"
          onPosted={(application) => setPosted(application.pk)}
          targets={[{ key: `container_fill:${pk}`, target_type: 'container_fill', pk, label: `Fill #${pk} (all pots)` }]}
          defaultTargetKeys={[`container_fill:${pk}`]}
        />
      )}
      {posted !== undefined && <Alert variant="success">Media application #{posted} posted.</Alert>}
      {data.status === 'open' && history.data?.stock_lot != null && <PotFillPlant pk={pk} currentPlants={data.plants} />}
      {data.status === 'open' && history.data?.stock_lot != null && <PotFillNumber pk={pk} contents={data} />}
      <h3 className="h6">Current contents</h3>
      <p>{data.plants.length} plants in this fill.</p>
      {data.plants.map((plant) => (
        <Link className="me-2" key={plant} to={`/plantings/plants/${plant}`}>
          Plant #{plant}
        </Link>
      ))}
      <Table size="sm" responsive>
        <thead>
          <tr>
            <th>Media lot</th>
            <th>Remaining quantity</th>
          </tr>
        </thead>
        <tbody>
          {data.media.map((media) => (
            <tr key={media.lot}>
              <td>#{media.lot}</td>
              <td>
                {formatQuantity(media.base_quantity)} {media.base_unit}
              </td>
            </tr>
          ))}
          {data.media.length === 0 && (
            <tr>
              <td colSpan={2}>No media remains in this fill.</td>
            </tr>
          )}
        </tbody>
      </Table>
      {(data.costs.unknown_cost || data.costs.unknown_allocation) && <Alert variant="warning">Some costs or historical plant shares are unknown.</Alert>}
      <Table size="sm">
        <tbody>
          {totals.map(([label, value]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td>{formatMoney(value, data.costs.currency_code, 'Unknown')}</td>
            </tr>
          ))}
        </tbody>
      </Table>
      <PotFillClean key={`${data.status}:${data.digest}:${data.plants.join(',')}`} pk={pk} contents={data} />
      <h3 className="h6 mt-3">Clean and correction history</h3>
      {history.isPending && <p>Loading history…</p>}
      {history.isError && <Alert variant="danger">Could not load clean history.</Alert>}
      {history.data && (
        <>
          {history.data.events
            .filter((event) => event.event_type === 'closed' || event.event_type === 'reopened')
            .map((event) => (
              <p key={event.pk}>
                #{event.pk} · {new Date(event.occurred_at).toLocaleString()} · {event.event_type === 'closed' ? 'Cleaned' : 'Clean corrected'} — {event.reason}
              </p>
            ))}
          {history.data.residuals.map((residual) => (
            <p key={residual.pk}>
              Lot #{residual.lot} · {residual.base_quantity} {residual.base_unit} · {residual.disposition} — {residual.reason}
              {residual.correction_event !== null && ` (reversed by correction #${residual.correction_event})`}
            </p>
          ))}
          {history.data.events.every((event) => event.event_type !== 'closed' && event.event_type !== 'reopened') && <p>No cleans recorded.</p>}
        </>
      )}
    </div>
  )
}

function PotFillPanel({ target, canOpen = true }: { target: PotFillTarget; canOpen?: boolean }) {
  const cache = useQueryClient()
  const [page, setPage] = React.useState(1)
  const [selected, setSelected] = React.useState<number>()
  const [count, setCount] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const numbered = 'inventory_unit' in target
  const history = useQuery({
    queryKey: queryKeys.containerFills.list(target, page),
    queryFn: ({ signal }) => getPotFills(target, page, signal)
  })
  const mutation = useMutation({
    mutationFn: openPotFill,
    onSuccess: async (fill) => {
      setSelected(fill.pk)
      setPage(1)
      setCount('')
      setNotes('')
      await Promise.all([
        cache.invalidateQueries({ queryKey: queryKeys.containerFills.all }),
        cache.invalidateQueries({ queryKey: queryKeys.inventory.all }),
        cache.invalidateQueries({ queryKey: queryKeys.reports.all })
      ])
    }
  })
  const fields = errorsByField(mutation.error)
  return (
    <Card className="mt-4">
      <Card.Body>
        <Card.Title>Pot fills</Card.Title>
        <p>Open a fill when these pots are filled. Record growing media separately against the fill; opening alone records no media use.</p>
        {canOpen && (
          <Form
            onSubmit={(event) => {
              event.preventDefault()
              mutation.mutate({ ...target, container_count: numbered ? 1 : Number(count), notes })
            }}
          >
            {!numbered && (
              <Form.Group controlId="pot-fill-count" className="mb-2">
                <Form.Label>Number of pots to fill</Form.Label>
                <Form.Control required type="number" min={1} step={1} value={count} isInvalid={'container_count' in fields} onChange={(event) => setCount(event.target.value)} />
                <Form.Control.Feedback type="invalid">{fields.container_count}</Form.Control.Feedback>
              </Form.Group>
            )}
            <Form.Group controlId="pot-fill-notes" className="mb-2">
              <Form.Label>Notes (optional)</Form.Label>
              <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
            </Form.Group>
            <Button type="submit" disabled={mutation.isPending}>
              {mutation.isPending ? 'Opening…' : 'Open fill'}
            </Button>
          </Form>
        )}
        {mutation.isError && (
          <Alert className="mt-2" variant="danger">
            {Object.values(fields).join(' ') || mutation.error.message}
          </Alert>
        )}
        {mutation.isSuccess && (
          <Alert className="mt-2" variant="success">
            Opened fill {mutation.data.code}.
          </Alert>
        )}
        <h3 className="h6 mt-3">Fill history</h3>
        {history.isPending && <p>Loading fills…</p>}
        {history.isError && <Alert variant="danger">Could not load fill history.</Alert>}
        {history.data && (
          <>
            <Table responsive size="sm">
              <thead>
                <tr>
                  <th>Fill</th>
                  <th>Pots</th>
                  <th>Opened</th>
                  <th>Cleaned</th>
                  <th>Notes</th>
                </tr>
              </thead>
              <tbody>
                {history.data.results.map((fill) => (
                  <tr key={fill.pk} className={selected === fill.pk ? 'table-primary' : undefined}>
                    <td>
                      <Button variant="link" className="p-0" onClick={() => setSelected(fill.pk)}>
                        {fill.code}
                      </Button>
                    </td>
                    <td>{fill.container_count}</td>
                    <td>{new Date(fill.opened_at).toLocaleString()}</td>
                    <td>
                      {fill.closed_at ? (
                        <>
                          {new Date(fill.closed_at).toLocaleString()} — {fill.close_reason}
                        </>
                      ) : (
                        'Open'
                      )}
                    </td>
                    <td>{fill.notes}</td>
                  </tr>
                ))}
                {history.data.results.length === 0 && (
                  <tr>
                    <td colSpan={5}>No fills recorded.</td>
                  </tr>
                )}
              </tbody>
            </Table>
            <div className="d-flex align-items-center gap-2">
              <Button size="sm" variant="outline-secondary" disabled={!history.data.previous} onClick={() => setPage(page - 1)}>
                Previous
              </Button>
              <span>
                Page {page} · {history.data.count} fills
              </span>
              <Button size="sm" variant="outline-secondary" disabled={!history.data.next} onClick={() => setPage(page + 1)}>
                Next
              </Button>
            </div>
          </>
        )}
        {selected !== undefined && <FillContents key={selected} pk={selected} />}
      </Card.Body>
    </Card>
  )
}

function CountedPotFills({ item }: { item: InventoryItem }) {
  const [selected, setSelected] = React.useState('')
  const balances = useQuery({ queryKey: queryKeys.inventory.balances(item.pk), queryFn: ({ signal }) => getInventoryBalances(item.pk, signal) })
  const chosen = balances.data?.find((balance) => `${balance.lot}:${balance.location}` === selected)
  return (
    <div className="mt-4">
      <Form.Group controlId="pot-fill-stock">
        <Form.Label>Fill pots from a lot and location</Form.Label>
        <Form.Select value={selected} onChange={(event) => setSelected(event.target.value)}>
          <option value="">Choose stock</option>
          {(balances.data ?? []).map((balance) => (
            <option key={`${balance.lot}:${balance.location}`} value={`${balance.lot}:${balance.location}`}>
              {balance.lot_identifier} — {balance.location_full_name}
            </option>
          ))}
        </Form.Select>
        <Form.Text>Available empty pots are checked when you open the fill.</Form.Text>
      </Form.Group>
      {balances.isPending && <p>Loading stock…</p>}
      {balances.isError && <Alert variant="danger">Could not load stock.</Alert>}
      {balances.data?.length === 0 && <p>No stock has been received for this item.</p>}
      {chosen && <PotFillPanel key={selected} target={{ stock_lot: chosen.lot, source_location: chosen.location }} canOpen={item.active} />}
    </div>
  )
}

export { CountedPotFills, PotFillPanel }
