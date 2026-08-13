import React from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, ListGroup, ProgressBar, Row, Table } from 'react-bootstrap'
import { Link, useParams } from 'react-router'

import { countStocktakeTarget, createStocktake, getStocktake, getStocktakes, resolveStocktakeVariance, scanStocktakeTarget, stocktakeAction } from '../api/inventory'
import { getLocations } from '../api/locations'
import { queryClient, queryKeys } from '../query'
import { StocktakeTarget, StocktakeVariance } from '../types/inventory'

function StocktakeListView() {
  const [location, setLocation] = React.useState<number | ''>('')
  const [notes, setNotes] = React.useState('')
  const locations = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const sessions = useQuery({ queryKey: queryKeys.inventory.stocktakes, queryFn: ({ signal }) => getStocktakes(signal) })
  const create = useMutation({
    mutationFn: () => createStocktake({ location: location as number, include_descendants: true }, notes),
    onSuccess: async () => {
      setNotes('')
      await queryClient.invalidateQueries({ queryKey: queryKeys.inventory.stocktakes })
    }
  })
  return (
    <main className="container py-3">
      <h1>Stocktakes</h1>
      <p>Freeze a physical area, count without expected quantities, then review and post each variance.</p>
      <Card body className="mb-3">
        <Card.Title>Open a blind stocktake</Card.Title>
        <Row className="g-2 align-items-end">
          <Col md={5}>
            <Form.Label>Location and descendants</Form.Label>
            <Form.Select value={location} onChange={(event) => setLocation(Number(event.target.value) || '')}>
              <option value="">Select location</option>
              {(locations.data ?? []).map((row) => (
                <option value={row.pk} key={row.pk}>
                  {row.full_name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={5}>
            <Form.Label>Notes</Form.Label>
            <Form.Control value={notes} onChange={(event) => setNotes(event.target.value)} />
          </Col>
          <Col md={2}>
            <Button size="lg" disabled={!location || create.isPending} onClick={() => create.mutate()}>
              Open
            </Button>
          </Col>
        </Row>
      </Card>
      <Table responsive hover>
        <thead>
          <tr>
            <th>Session</th>
            <th>Status</th>
            <th>Progress</th>
            <th>Opened</th>
          </tr>
        </thead>
        <tbody>
          {(sessions.data ?? []).map((row) => (
            <tr key={row.pk}>
              <td>
                <Link to={`/inventory/stocktakes/${row.pk}`}>Stocktake {row.pk}</Link>
              </td>
              <td>
                <Badge bg={row.status === 'posted' ? 'success' : 'secondary'}>{row.status}</Badge>
              </td>
              <td>
                {row.progress.counted} / {row.progress.total}
              </td>
              <td>{new Date(row.counted_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </main>
  )
}

function CountTarget({ stocktakePk, target, refresh }: { stocktakePk: number; target: StocktakeTarget; refresh: () => Promise<unknown> }) {
  const [quantity, setQuantity] = React.useState('')
  const [notes, setNotes] = React.useState('')
  const count = useMutation({
    mutationFn: () => countStocktakeTarget(stocktakePk, target.pk, quantity, notes),
    onSuccess: () => refresh()
  })
  const quantityTarget = ['lot', 'seed_packet', 'cohort'].includes(target.target_type)
  return (
    <ListGroup.Item>
      <div className="d-flex justify-content-between gap-2 align-items-center flex-wrap">
        <div>
          <strong>{target.display}</strong>
          <br />
          <small>{target.target_type.replaceAll('_', ' ')}</small>
        </div>
        {target.accepted_count ? (
          <Badge bg="success">Counted {target.accepted_count.counted_quantity ?? 'present'}</Badge>
        ) : quantityTarget ? (
          <div className="d-flex gap-2">
            <Form.Control inputMode="decimal" aria-label={`Count ${target.display}`} value={quantity} onChange={(event) => setQuantity(event.target.value)} placeholder="Count" />
            <Form.Control aria-label={`Notes for ${target.display}`} value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Notes" />
            <Button size="lg" disabled={!quantity || count.isPending} onClick={() => count.mutate()}>
              Save
            </Button>
          </div>
        ) : (
          <Badge bg="warning" text="dark">
            Scan identity
          </Badge>
        )}
      </div>
    </ListGroup.Item>
  )
}

function ReviewVariance({ stocktakePk, variance, refresh }: { stocktakePk: number; variance: StocktakeVariance; refresh: () => Promise<unknown> }) {
  const [action, setAction] = React.useState('')
  const [reason, setReason] = React.useState('')
  const resolve = useMutation({
    mutationFn: () => resolveStocktakeVariance(stocktakePk, variance.pk, action, reason, variance.source_changed),
    onSuccess: () => refresh()
  })
  if (variance.resolution_action)
    return (
      <Badge bg="success">
        {variance.resolution_action}: {variance.resolution_reason}
      </Badge>
    )
  return (
    <div>
      {variance.source_changed && <Alert variant="warning">Source changed after opening. Saving explicitly accepts this conflict.</Alert>}
      <div className="d-flex gap-2 flex-wrap">
        <Form.Select value={action} onChange={(event) => setAction(event.target.value)} aria-label="Correction action">
          <option value="">Choose correction</option>
          <option value="adjust">Adjust quantity</option>
          <option value="move">Move</option>
          <option value="lost">Record lost</option>
          <option value="state_correct">Correct state</option>
          <option value="no_change">No change</option>
        </Form.Select>
        <Form.Control value={reason} onChange={(event) => setReason(event.target.value)} placeholder="Required reason" />
        <Button disabled={!action || !reason.trim() || resolve.isPending} onClick={() => resolve.mutate()}>
          Resolve
        </Button>
      </div>
    </div>
  )
}

function StocktakeDetailView() {
  const { stocktakeId } = useParams()
  const pk = Number(stocktakeId)
  const [scan, setScan] = React.useState('')
  const session = useQuery({ queryKey: queryKeys.inventory.stocktake(pk), queryFn: ({ signal }) => getStocktake(pk, signal), enabled: Number.isInteger(pk) })
  const refresh = async () => session.refetch()
  const transition = useMutation({ mutationFn: ({ action, data = {} }: { action: string; data?: object }) => stocktakeAction(pk, action, data), onSuccess: () => refresh() })
  const scanMutation = useMutation({
    mutationFn: () => scanStocktakeTarget(pk, scan),
    onSuccess: async () => {
      setScan('')
      await refresh()
    }
  })
  const row = session.data
  if (!row) return <main className="container py-3">Loading stocktake…</main>
  const percent = row.progress.total ? (row.progress.counted / row.progress.total) * 100 : 0
  return (
    <main className="container py-3">
      <h1>Stocktake {row.pk}</h1>
      <Badge bg="secondary" className="mb-2">
        {row.status}
      </Badge>
      <ProgressBar now={percent} label={`${row.progress.counted}/${row.progress.total}`} className="mb-3" />
      {row.status === 'open' && (
        <div className="d-flex gap-2 mb-3">
          <Button variant="outline-secondary" onClick={() => transition.mutate({ action: 'pause' })}>
            Pause
          </Button>
          <Button onClick={() => transition.mutate({ action: 'begin-review' })}>Begin review</Button>
        </div>
      )}
      {row.status === 'paused' && (
        <Button className="mb-3" onClick={() => transition.mutate({ action: 'resume' })}>
          Resume
        </Button>
      )}
      {row.status === 'review' && (
        <Button className="mb-3" onClick={() => transition.mutate({ action: 'approve' })}>
          Approve resolved review
        </Button>
      )}
      {row.status === 'approved' && (
        <Button className="mb-3" variant="success" onClick={() => transition.mutate({ action: 'post' })}>
          Post corrections
        </Button>
      )}
      {row.status === 'posted' && (
        <Button className="mb-3" variant="outline-danger" onClick={() => transition.mutate({ action: 'reverse', data: { reason: 'Stocktake reversed by reviewer' } })}>
          Reverse stocktake
        </Button>
      )}
      {['open', 'paused'].includes(row.status) && (
        <Card body className="mb-3">
          <Form.Label>Scan tray, cohort, or plant</Form.Label>
          <div className="d-flex gap-2">
            <Form.Control size="lg" value={scan} onChange={(event) => setScan(event.target.value)} />
            <Button size="lg" disabled={!scan.trim()} onClick={() => scanMutation.mutate()}>
              Count scan
            </Button>
          </div>
        </Card>
      )}
      <ListGroup>
        {row.targets.map((target) => (
          <CountTarget stocktakePk={pk} target={target} refresh={refresh} key={target.pk} />
        ))}
      </ListGroup>
      {['review', 'approved', 'posted', 'reversed'].includes(row.status) && (
        <section className="mt-4">
          <h2>Variances</h2>
          {row.targets.flatMap((target) =>
            target.variances.map((variance) => (
              <Card body className="mb-2" key={variance.pk}>
                <strong>
                  {target.display}: {variance.kind}
                </strong>
                <p>
                  Value: {variance.variance_value ?? 'Unknown'} {variance.currency ?? ''}
                </p>
                <ReviewVariance stocktakePk={pk} variance={variance} refresh={refresh} />
              </Card>
            ))
          )}
        </section>
      )}
    </main>
  )
}

export { StocktakeDetailView, StocktakeListView }
