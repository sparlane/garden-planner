import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link, useSearchParams } from 'react-router'

import { getReport, reportExportUrl } from './api/reports'
import { queryKeys } from './query'
import { DashboardRow, ProfitabilityTotals, ReportEnvelope } from './types/reports'
import { formatDateTime } from './utils'

type ReportPage = 'dashboard' | 'inventory' | 'production' | 'orders' | 'profitability' | 'traceability'

const INVENTORY_SECTIONS: Record<string, string> = {
  balances: 'inventory-balances',
  trays: 'serialized-trays',
  movements: 'inventory-movements',
  stocktakes: 'stocktake-variances'
}

function displayValue(value: unknown): React.ReactNode {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'object') return <code className="text-wrap">{JSON.stringify(value)}</code>
  return String(value)
}

function QualityWarnings({ report }: { report: ReportEnvelope }) {
  return report.data_quality.map((flag) => (
    <Alert variant="warning" key={flag.code}>
      <strong>
        {flag.count} warning{flag.count === 1 ? '' : 's'}:
      </strong>{' '}
      {flag.message}
    </Alert>
  ))
}

function ResultTable({ report }: { report: ReportEnvelope }) {
  const rows = report.results
  if (rows.length === 0) return <Alert variant="secondary">No source records match these filters.</Alert>
  const columns = Object.keys(rows[0]).filter((column) => !['input_layers', 'reconciliation', 'remaining_balances', 'commerce', 'fulfillments'].includes(column))
  return (
    <div className="table-responsive">
      <Table striped hover size="sm">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column.replaceAll('_', ' ')}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={String(row.source_id ?? row.movement_id ?? row.batch_id ?? row.order_id ?? row.lot_id ?? index)}>
              {columns.map((column) => (
                <td key={column}>{displayValue(row[column])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  )
}

function Pagination({ report, params, setParams }: { report: ReportEnvelope; params: URLSearchParams; setParams: ReturnType<typeof useSearchParams>[1] }) {
  const page = Number(params.get('page') ?? '1')
  function move(next: number) {
    const updated = new URLSearchParams(params)
    updated.set('page', String(next))
    setParams(updated)
  }
  return (
    <div className="d-flex align-items-center gap-2 my-3">
      <Button size="sm" variant="outline-secondary" disabled={!report.previous} onClick={() => move(page - 1)}>
        Previous
      </Button>
      <span>
        Page {page} · {report.count} rows
      </span>
      <Button size="sm" variant="outline-secondary" disabled={!report.next} onClick={() => move(page + 1)}>
        Next
      </Button>
    </div>
  )
}

function FilterField({
  label,
  name,
  type = 'text',
  params,
  update
}: {
  label: string
  name: string
  type?: string
  params: URLSearchParams
  update: (name: string, value: string) => void
}) {
  return (
    <Col md={3}>
      <Form.Label>{label}</Form.Label>
      <Form.Control type={type} value={params.get(name) ?? ''} onChange={(event) => update(name, event.target.value)} />
    </Col>
  )
}

function ReportFilters({ page, params, setParams }: { page: ReportPage; params: URLSearchParams; setParams: ReturnType<typeof useSearchParams>[1] }) {
  function update(name: string, value: string) {
    const updated = new URLSearchParams(params)
    if (value) updated.set(name, value)
    else updated.delete(name)
    updated.delete('page')
    setParams(updated)
  }
  const dated = ['dashboard', 'production', 'orders', 'profitability'].includes(page)
  return (
    <Card body className="mb-3">
      <Row className="g-2">
        {dated && <FilterField label="From" name="date_from" type="date" params={params} update={update} />}
        {dated && <FilterField label="To" name="date_to" type="date" params={params} update={update} />}
        {['production', 'profitability'].includes(page) && <FilterField label="Variety ID" name="variety" type="number" params={params} update={update} />}
        {['production', 'profitability'].includes(page) && <FilterField label="Batch ID" name="batch" type="number" params={params} update={update} />}
        {page === 'orders' && <FilterField label="Order reference" name="order" params={params} update={update} />}
        {page === 'profitability' && <FilterField label="Customer ID" name="customer" type="number" params={params} update={update} />}
        {page === 'inventory' && params.get('section') === 'balances' && <FilterField label="Item ID" name="item" type="number" params={params} update={update} />}
        {page === 'inventory' && params.get('section') === 'balances' && <FilterField label="Lot ID" name="lot" type="number" params={params} update={update} />}
      </Row>
    </Card>
  )
}

function Dashboard({ report }: { report: ReportEnvelope<DashboardRow> }) {
  const row = report.results[0]
  if (!row) return <Alert variant="secondary">No dashboard data is available.</Alert>
  const metrics = [
    ['Low stock', row.low_stock_lot_locations],
    ['Expiring stock', row.expiring_lot_locations],
    ['Active batches', row.active_batches],
    ['Available seedlings', row.available_seedlings],
    ['Open orders', row.open_orders],
    ['Provisional batches', row.provisional_batches]
  ] as const
  return (
    <>
      <Row className="g-3 mb-3">
        {metrics.map(([label, value]) => (
          <Col md={4} lg={2} key={label}>
            <Card body className="h-100">
              <div className="text-muted small">{label}</div>
              <div className="fs-3">{value}</div>
            </Card>
          </Col>
        ))}
      </Row>
      <Card body>
        <h2 className="h5">Recent fulfillment</h2>
        {row.recent_fulfillments.length === 0 && <div className="text-muted">No fulfillment has posted yet.</div>}
        {row.recent_fulfillments.map((fulfillment) => (
          <div key={fulfillment.fulfillment_id}>
            <Link to={`/sales/orders/${fulfillment.order_id}`}>{fulfillment.fulfillment_number}</Link> · {fulfillment.order_number} · {formatDateTime(fulfillment.fulfilled_at)}
          </div>
        ))}
      </Card>
    </>
  )
}

function ProfitabilitySummary({ totals }: { totals: ProfitabilityTotals }) {
  return (
    <Row className="g-3 mb-3">
      {totals.currencies.map((currency) => (
        <Col lg={12} key={currency.currency_code}>
          <Card body>
            <h2 className="h5">{currency.currency_code} direct-cost P&amp;L</h2>
            <Row>
              <Col>
                Gross sales
                <br />
                <strong>{currency.gross_sales}</strong>
              </Col>
              <Col>
                Net sales
                <br />
                <strong>{currency.net_sales}</strong>
              </Col>
              <Col>
                Direct COGS
                <br />
                <strong>{currency.direct_cogs}</strong>
              </Col>
              <Col>
                Production loss
                <br />
                <strong>{currency.production_loss}</strong>
              </Col>
              <Col>
                Gross profit
                <br />
                <strong>{currency.gross_profit ?? 'Unavailable'}</strong>
              </Col>
              <Col>
                Margin
                <br />
                <strong>{currency.gross_margin === null ? 'Unavailable' : `${(Number(currency.gross_margin) * 100).toFixed(2)}%`}</strong>
              </Col>
            </Row>
          </Card>
        </Col>
      ))}
    </Row>
  )
}

function ReportsView({ page }: { page: ReportPage }) {
  const [params, setParams] = useSearchParams()
  React.useEffect(() => {
    if (page === 'inventory' && !params.get('section')) {
      const updated = new URLSearchParams(params)
      updated.set('section', 'balances')
      setParams(updated, { replace: true })
    }
  }, [page, params, setParams])
  const traceType = params.get('trace_type') ?? 'plant'
  const traceId = params.get('trace_id') ?? ''
  const section = params.get('section') ?? 'balances'
  const reportName =
    page === 'inventory'
      ? INVENTORY_SECTIONS[section]
      : page === 'production'
        ? 'production-batches'
        : page === 'traceability'
          ? traceId
            ? `traceability/${traceType === 'lot' ? 'lots' : 'plants'}/${traceId}`
            : ''
          : page
  const apiParams = new URLSearchParams(params)
  ;['section', 'trace_type', 'trace_id'].forEach((key) => apiParams.delete(key))
  const query = useQuery({
    queryKey: queryKeys.reports.detail(reportName, apiParams.toString()),
    queryFn: ({ signal }) => getReport(reportName, apiParams, signal),
    enabled: Boolean(reportName)
  })
  function updateFrontend(name: string, value: string) {
    const updated = new URLSearchParams(params)
    if (value) updated.set(name, value)
    else updated.delete(name)
    updated.delete('page')
    setParams(updated)
  }
  return (
    <main className="container-fluid py-3">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h1 className="mb-0">{page === 'dashboard' ? 'Nursery dashboard' : `Nursery ${page}`}</h1>
        {reportName && (
          <Button as="a" variant="outline-primary" href={reportExportUrl(reportName, apiParams)}>
            Export CSV
          </Button>
        )}
      </div>
      {page === 'inventory' && (
        <Form.Select className="mb-3" value={section} onChange={(event) => setParams(new URLSearchParams({ section: event.target.value }))}>
          <option value="balances">Balances and valuation</option>
          <option value="trays">Serialized trays</option>
          <option value="movements">Movement history</option>
          <option value="stocktakes">Stocktake variances</option>
        </Form.Select>
      )}
      {page === 'traceability' && (
        <Card body className="mb-3">
          <Row className="g-2">
            <Col md={3}>
              <Form.Label>Trace direction</Form.Label>
              <Form.Select value={traceType} onChange={(event) => updateFrontend('trace_type', event.target.value)}>
                <option value="plant">Sold plant backward</option>
                <option value="lot">Input lot forward</option>
              </Form.Select>
            </Col>
            <Col md={3}>
              <Form.Label>{traceType === 'lot' ? 'Lot ID' : 'Plant ID'}</Form.Label>
              <Form.Control type="number" min={1} value={traceId} onChange={(event) => updateFrontend('trace_id', event.target.value)} />
            </Col>
          </Row>
        </Card>
      )}
      {page !== 'traceability' && <ReportFilters page={page} params={params} setParams={setParams} />}
      {query.isPending && reportName && <div>Loading report…</div>}
      {!reportName && <Alert variant="info">Choose an exact plant or lot to trace.</Alert>}
      {query.data && (
        <>
          <QualityWarnings report={query.data} />
          {page === 'dashboard' ? <Dashboard report={query.data as unknown as ReportEnvelope<DashboardRow>} /> : null}
          {page === 'profitability' ? <ProfitabilitySummary totals={query.data.totals as unknown as ProfitabilityTotals} /> : null}
          {page !== 'dashboard' && <ResultTable report={query.data} />}
          {page !== 'dashboard' && <Pagination report={query.data} params={params} setParams={setParams} />}
        </>
      )}
    </main>
  )
}

export { ReportsView }
