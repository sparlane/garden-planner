import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Form, Table } from 'react-bootstrap'

import { getBatchCostBreakdown, recalculateBatchCosts } from '../api/costing'
import { queryKeys } from '../query'
import { BatchCostBreakdown, CostBasis, CostBucket, CostLayer, CostTargetType } from '../types/costing'
import { formatDateTime, formatMeasure, formatMoney } from '../utils'
import { costSourceLabel } from './cost_sources'
import { STATE_LABELS } from './lifecycle'

const BUCKET_LABELS: Record<CostBucket, string> = {
  plant_inventory: 'Held as plant inventory',
  cogs: 'Sold (cost of goods)',
  harvested_output: 'Left as harvested crop',
  production_loss: 'Production loss',
  unresolved: 'Not yet reaching a plant',
  unattributed: 'Not attributable to a plant'
}

// Ordered so the value that is still yours reads first and the value that is
// gone reads last, rather than alphabetically.
const BUCKET_ORDER: Array<CostBucket> = ['plant_inventory', 'cogs', 'harvested_output', 'unresolved', 'unattributed', 'production_loss']

const TARGET_LABELS: Record<CostTargetType, string> = {
  specific_plant: 'Plant',
  seed_tray_cell: 'Cell',
  batch_pool: 'Unclaimed',
  production_loss: 'Loss',
  unattributed: 'Unattributed'
}

const BASIS_LABELS: Record<CostBasis, string> = {
  seeds_sown: 'Seed sown into the cell',
  cell_volume: 'Cell volume filled',
  per_plant: 'Per plant',
  area: 'Ground area',
  equal_share: 'Equal share of a cell',
  direct: 'Whole source'
}

function targetLabel(layer: CostLayer): string {
  const kind = TARGET_LABELS[layer.target_type]
  if (layer.specific_plant !== null) return `${kind} #${layer.specific_plant}`
  if (layer.seed_tray_cell !== null) return `${kind} #${layer.seed_tray_cell}`
  return kind
}

// Exactly one of the two totals carries a number, because a batch is wholly
// provisional or wholly final. They are rendered as separate labelled rows and
// never added, since a provisional figure and a final one mean different things.
function TotalRow({ breakdown }: { breakdown: BatchCostBreakdown }) {
  return (
    <dl className="row mb-2">
      <dt className="col-sm-5">Provisional total</dt>
      <dd className="col-sm-7">{formatMoney(breakdown.provisional_total, breakdown.currency_code, '—')}</dd>
      <dt className="col-sm-5">Final total</dt>
      <dd className="col-sm-7">{formatMoney(breakdown.final_total, breakdown.currency_code, '—')}</dd>
      {BUCKET_ORDER.map((bucket) => (
        <React.Fragment key={bucket}>
          <dt className="col-sm-5 fw-normal text-muted">{BUCKET_LABELS[bucket]}</dt>
          <dd className="col-sm-7">{formatMoney(breakdown.totals[bucket], breakdown.currency_code)}</dd>
        </React.Fragment>
      ))}
    </dl>
  )
}

function PlantValueTable({ breakdown }: { breakdown: BatchCostBreakdown }) {
  if (breakdown.plants.length === 0) {
    return <div className="text-muted">No cost has reached an individual seedling yet.</div>
  }
  return (
    <Table size="sm" responsive>
      <thead>
        <tr>
          <th>Plant</th>
          <th>Production value</th>
          <th>State</th>
          <th>Value sits in</th>
        </tr>
      </thead>
      <tbody>
        {breakdown.plants.map((row) => (
          <tr key={row.plant}>
            <td>#{row.plant}</td>
            <td>{formatMoney(row.cost, breakdown.currency_code, 'Unknown')}</td>
            <td>{row.state ? STATE_LABELS[row.state] : '—'}</td>
            <td>{row.disposition ? BUCKET_LABELS[row.disposition] : '—'}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

function LayerTable({ breakdown }: { breakdown: BatchCostBreakdown }) {
  if (breakdown.layers.length === 0) {
    return <div className="text-muted">Nothing has been allocated to this batch yet.</div>
  }
  return (
    <Table size="sm" responsive>
      <thead>
        <tr>
          <th>Source</th>
          <th>Lot</th>
          <th>Movement</th>
          <th>Basis</th>
          <th>Quantity</th>
          <th>Went to</th>
          <th>Amount</th>
        </tr>
      </thead>
      <tbody>
        {breakdown.layers.map((layer) => (
          <tr key={layer.allocation}>
            <td>{costSourceLabel(layer)}</td>
            <td>{layer.lot === null ? '—' : `#${layer.lot}`}</td>
            <td>{layer.movement === null ? '—' : `#${layer.movement}`}</td>
            <td>{BASIS_LABELS[layer.basis]}</td>
            <td>{formatMeasure(layer.base_quantity, layer.base_unit)}</td>
            <td>{targetLabel(layer)}</td>
            <td>{formatMoney(layer.amount, layer.currency_code, 'Unknown')}</td>
          </tr>
        ))}
      </tbody>
    </Table>
  )
}

function RecalculateForm({ batchPk }: { batchPk: number }) {
  const queryClient = useQueryClient()
  const [reason, setReason] = React.useState('')
  const [outcome, setOutcome] = React.useState('')
  const mutation = useMutation({
    mutationFn: () => recalculateBatchCosts(batchPk, reason),
    onSuccess: (response) => {
      setReason('')
      setOutcome(
        response.run === null
          ? 'Everything already matched the recorded facts, so nothing was posted.'
          : `Reversed ${response.run.reversed_count} and posted ${response.run.posted_count} allocations.`
      )
      return queryClient.invalidateQueries({ queryKey: queryKeys.costing.batch(batchPk) })
    }
  })

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    mutation.mutate()
  }

  return (
    <Form onSubmit={submit} className="mt-3">
      <Form.Group controlId={`batch-cost-recalculate-${batchPk}`}>
        <Form.Label>Repost from corrected facts</Form.Label>
        <Form.Control required value={reason} placeholder="Why is this being recalculated?" onChange={(event) => setReason(event.target.value)} />
        <Form.Text className="text-muted">
          Nothing is edited: a layer that no longer matches is reversed and its replacement posted beside it. A batch whose output is finalized keeps its frozen shares — reopen it
          to change those.
        </Form.Text>
      </Form.Group>
      <Button className="mt-2" type="submit" disabled={mutation.isPending}>
        Recalculate
      </Button>
      {outcome && <div className="mt-2 text-muted">{outcome}</div>}
    </Form>
  )
}

function BatchCosts({ batchPk }: { batchPk: number }) {
  const { data: breakdown } = useQuery({
    queryKey: queryKeys.costing.batch(batchPk),
    queryFn: ({ signal }) => getBatchCostBreakdown(batchPk, signal)
  })

  return (
    <Card className="mb-3">
      <Card.Body>
        <Card.Title>Costing {breakdown && <Badge bg={breakdown.provisional ? 'warning' : 'success'}>{breakdown.provisional ? 'Provisional' : 'Final'}</Badge>}</Card.Title>
        {!breakdown && <div className="text-muted">Loading…</div>}
        {breakdown && (
          <>
            {breakdown.provisional && (
              <Alert variant="info">
                Output has not been finalized, so cost still sitting in cells may yet reach a seedling. These figures are provisional and are kept apart from final ones.
              </Alert>
            )}
            {breakdown.unknown_cost && (
              <Alert variant="warning">Some of this batch drew on a lot with no recorded unit cost, so these totals understate the real figure rather than guessing at it.</Alert>
            )}
            <TotalRow breakdown={breakdown} />
            {breakdown.last_run && (
              <div className="text-muted mb-3">
                Last recalculated {formatDateTime(breakdown.last_run.created)} ({breakdown.last_run.trigger}): reversed {breakdown.last_run.reversed_count}, posted{' '}
                {breakdown.last_run.posted_count}.
              </div>
            )}
            <h6>Per plant</h6>
            <PlantValueTable breakdown={breakdown} />
            <h6>Every allocation</h6>
            <LayerTable breakdown={breakdown} />
            <RecalculateForm batchPk={batchPk} />
          </>
        )}
      </Card.Body>
    </Card>
  )
}

export { BatchCosts }
