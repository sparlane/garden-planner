import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Col, Form, Row, Tab, Table, Tabs } from 'react-bootstrap'

import {
  addStockLine,
  captureIncomeYear,
  createAsset,
  createDepreciationSchedule,
  createEntry,
  createIncomeYear,
  createLiability,
  finalizeIncomeYear,
  getAssets,
  getEntries,
  getIncomeYears,
  getLiabilities,
  incomeYearExportUrl,
  reverseEntry
} from './api/bookkeeping'
import { queryKeys } from './query'
import { Workspace } from './types/workspace'
import { formatMoney } from './utils'

const nextYearEnd = () => {
  const today = new Date()
  const year = today.getMonth() < 3 ? today.getFullYear() : today.getFullYear() + 1
  return `${year}-03-31`
}

function EntryPanel({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.bookkeeping.entries, queryFn: ({ signal }) => getEntries(signal) })
  const liabilities = useQuery({ queryKey: queryKeys.bookkeeping.liabilities, queryFn: ({ signal }) => getLiabilities(signal) })
  const [form, setForm] = React.useState({
    kind: 'other_income',
    occurred_on: new Date().toISOString().slice(0, 10),
    description: '',
    amount_ex_tax: '',
    tax_amount: '0.0000',
    account_reference: '',
    external_reference: '',
    liability: ''
  })
  const create = useMutation({
    mutationFn: () =>
      createEntry({
        ...form,
        total_incl_tax: (Number(form.amount_ex_tax || 0) + Number(form.tax_amount || 0)).toFixed(4),
        tax_treatment: form.kind === 'other_income' ? 'unknown' : 'out_of_scope',
        currency_code: workspace.currency_code
      }),
    onSuccess: async () => {
      setForm({ ...form, description: '', amount_ex_tax: '', tax_amount: '0.0000' })
      await queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all })
    }
  })
  const reverse = useMutation({
    mutationFn: ({ pk, reason }: { pk: number; reason: string }) => reverseEntry(pk, reason),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all })
  })
  return (
    <>
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Record money outside sales and purchasing</Card.Title>
          <Row className="g-2">
            <Col md={3}>
              <Form.Select aria-label="Entry type" value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value })}>
                <option value="other_income">Other income</option>
                <option value="owner_contribution">Owner contribution</option>
                <option value="owner_drawing">Owner drawing</option>
                <option value="cash_adjustment">Cash adjustment</option>
                <option value="liability_advance">Liability advance</option>
                <option value="liability_repayment">Liability repayment</option>
              </Form.Select>
            </Col>
            {form.kind.startsWith('liability_') && (
              <Col md={3}>
                <Form.Select aria-label="Liability" value={form.liability} onChange={(event) => setForm({ ...form, liability: event.target.value })}>
                  <option value="">Choose liability</option>
                  {(liabilities.data ?? [])
                    .filter((row) => row.active)
                    .map((row) => (
                      <option value={row.id} key={row.id}>
                        {row.code} · {row.name}
                      </option>
                    ))}
                </Form.Select>
              </Col>
            )}
            <Col md={2}>
              <Form.Control type="date" value={form.occurred_on} onChange={(event) => setForm({ ...form, occurred_on: event.target.value })} />
            </Col>
            <Col md={3}>
              <Form.Control placeholder="Description" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} />
            </Col>
            <Col md={2}>
              <Form.Control
                type="number"
                step="0.0001"
                placeholder="Amount excl tax"
                value={form.amount_ex_tax}
                onChange={(event) => setForm({ ...form, amount_ex_tax: event.target.value })}
              />
            </Col>
            <Col md={2}>
              <Form.Control type="number" step="0.0001" placeholder="Tax" value={form.tax_amount} onChange={(event) => setForm({ ...form, tax_amount: event.target.value })} />
            </Col>
            <Col md={4}>
              <Form.Control
                placeholder="Bank or cash account reference"
                value={form.account_reference}
                onChange={(event) => setForm({ ...form, account_reference: event.target.value })}
              />
            </Col>
            <Col md={4}>
              <Form.Control
                placeholder="External transaction reference"
                value={form.external_reference}
                onChange={(event) => setForm({ ...form, external_reference: event.target.value })}
              />
            </Col>
            <Col md={4}>
              <Button onClick={() => create.mutate()} disabled={!form.description || !form.amount_ex_tax || create.isPending}>
                Record entry
              </Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>
      <Table responsive striped>
        <thead>
          <tr>
            <th>Date</th>
            <th>Type</th>
            <th>Description</th>
            <th>Amount</th>
            <th>Reference</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(query.data ?? []).map((row) => (
            <tr key={row.id}>
              <td>{row.occurred_on}</td>
              <td>{row.kind.replaceAll('_', ' ')}</td>
              <td>{row.description}</td>
              <td>{formatMoney(row.total_incl_tax, row.currency_code)}</td>
              <td>{row.account_reference || row.external_reference || '—'}</td>
              <td>
                {!row.reversal_of && (
                  <Button
                    size="sm"
                    variant="outline-danger"
                    onClick={() => {
                      const reason = window.prompt('Reason for reversal')
                      if (reason) reverse.mutate({ pk: row.id, reason })
                    }}
                  >
                    Reverse
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function AssetPanel({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.bookkeeping.assets, queryFn: ({ signal }) => getAssets(signal) })
  const [form, setForm] = React.useState({ code: '', name: '', category: '', acquired_on: '', cost_incl_tax: '', recoverable_tax: '0.0000', evidence_url: '' })
  const create = useMutation({
    mutationFn: () => createAsset({ ...form, tax_cost: (Number(form.cost_incl_tax) - Number(form.recoverable_tax)).toFixed(4), currency_code: workspace.currency_code }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all })
  })
  const schedule = useMutation({ mutationFn: createDepreciationSchedule, onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all }) })
  return (
    <>
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Add tax asset</Card.Title>
          <p className="text-muted">Record the rate and annual claim from your accountant or Inland Revenue schedule; the app does not choose tax treatment.</p>
          <Row className="g-2">
            {(['code', 'name', 'category'] as const).map((field) => (
              <Col md={3} key={field}>
                <Form.Control placeholder={field.replace('_', ' ')} value={form[field]} onChange={(event) => setForm({ ...form, [field]: event.target.value })} />
              </Col>
            ))}
            <Col md={3}>
              <Form.Control type="date" value={form.acquired_on} onChange={(event) => setForm({ ...form, acquired_on: event.target.value })} />
            </Col>
            <Col md={3}>
              <Form.Control
                type="number"
                step="0.0001"
                placeholder="Cost incl tax"
                value={form.cost_incl_tax}
                onChange={(event) => setForm({ ...form, cost_incl_tax: event.target.value })}
              />
            </Col>
            <Col md={3}>
              <Form.Control
                type="number"
                step="0.0001"
                placeholder="Recoverable GST"
                value={form.recoverable_tax}
                onChange={(event) => setForm({ ...form, recoverable_tax: event.target.value })}
              />
            </Col>
            <Col md={4}>
              <Form.Control type="url" placeholder="Evidence URL" value={form.evidence_url} onChange={(event) => setForm({ ...form, evidence_url: event.target.value })} />
            </Col>
            <Col md={2}>
              <Button onClick={() => create.mutate()} disabled={!form.code || !form.name || !form.acquired_on || !form.cost_incl_tax}>
                Add asset
              </Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>
      <Table responsive striped>
        <thead>
          <tr>
            <th>Code</th>
            <th>Asset</th>
            <th>Acquired</th>
            <th>Tax cost</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {(query.data ?? []).map((asset) => (
            <tr key={asset.id}>
              <td>{asset.code}</td>
              <td>
                {asset.name}
                <small className="d-block text-muted">{asset.category}</small>
              </td>
              <td>{asset.acquired_on}</td>
              <td>{formatMoney(asset.tax_cost, asset.currency_code)}</td>
              <td>{asset.disposed_on ? `Disposed ${asset.disposed_on}` : 'Active'}</td>
              <td>
                <Button
                  size="sm"
                  variant="outline-primary"
                  onClick={() => {
                    const year = window.prompt('Income year end (YYYY-03-31)')
                    const method = window.prompt('Method: dv, sl, or none', 'dv')
                    const rate = window.prompt('Rate percent')
                    const opening = window.prompt('Opening adjusted tax value', asset.tax_cost)
                    const claim = window.prompt('Depreciation claimed')
                    if (year && method && rate && opening && claim)
                      schedule.mutate({
                        asset: asset.id,
                        income_year_end: year,
                        method,
                        rate_percent: rate,
                        business_use_percent: '100.0000',
                        months_used: 12,
                        opening_tax_value: opening,
                        depreciation_claimed: claim,
                        disposal_adjustment: '0.0000',
                        closing_tax_value: (Number(opening) - Number(claim)).toFixed(4)
                      })
                  }}
                >
                  Add schedule
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function LiabilityPanel({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.bookkeeping.liabilities, queryFn: ({ signal }) => getLiabilities(signal) })
  const [form, setForm] = React.useState({ code: '', name: '', counterparty: '', opened_on: '' })
  const create = useMutation({
    mutationFn: () => createLiability({ ...form, currency_code: workspace.currency_code }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all })
  })
  return (
    <>
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Add liability</Card.Title>
          <Row className="g-2">
            {(['code', 'name', 'counterparty'] as const).map((field) => (
              <Col md={3} key={field}>
                <Form.Control placeholder={field} value={form[field]} onChange={(event) => setForm({ ...form, [field]: event.target.value })} />
              </Col>
            ))}
            <Col md={2}>
              <Form.Control type="date" value={form.opened_on} onChange={(event) => setForm({ ...form, opened_on: event.target.value })} />
            </Col>
            <Col md={1}>
              <Button onClick={() => create.mutate()} disabled={!form.code || !form.name || !form.counterparty || !form.opened_on}>
                Add
              </Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>
      <Table responsive striped>
        <thead>
          <tr>
            <th>Code</th>
            <th>Name</th>
            <th>Counterparty</th>
            <th>Opened</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {(query.data ?? []).map((row) => (
            <tr key={row.id}>
              <td>{row.code}</td>
              <td>{row.name}</td>
              <td>{row.counterparty}</td>
              <td>{row.opened_on}</td>
              <td>{row.active ? 'Active' : 'Closed'}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function IncomeYearPanel({ workspace }: { workspace: Workspace }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: queryKeys.bookkeeping.years, queryFn: ({ signal }) => getIncomeYears(signal) })
  const [yearEnd, setYearEnd] = React.useState(nextYearEnd())
  const [basis, setBasis] = React.useState<'cash' | 'accrual'>('accrual')
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.bookkeeping.all })
  const create = useMutation({ mutationFn: () => createIncomeYear({ year_end: yearEnd, basis }), onSuccess: refresh })
  const capture = useMutation({ mutationFn: captureIncomeYear, onSuccess: refresh })
  const finalize = useMutation({
    mutationFn: (pk: number) => finalizeIncomeYear(pk, window.confirm('Confirm opening stock is zero if no prior finalized year exists?')),
    onSuccess: refresh
  })
  const manual = useMutation({ mutationFn: ({ pk, data }: { pk: number; data: object }) => addStockLine(pk, data), onSuccess: refresh })
  return (
    <>
      <Card className="mb-3">
        <Card.Body>
          <Card.Title>Income-tax year</Card.Title>
          <p>Normal New Zealand balance date: 31 March. Values use the workspace timezone ({workspace.timezone}).</p>
          <Row className="g-2">
            <Col md={3}>
              <Form.Control type="date" value={yearEnd} onChange={(event) => setYearEnd(event.target.value)} />
            </Col>
            <Col md={3}>
              <Form.Select value={basis} onChange={(event) => setBasis(event.target.value as 'cash' | 'accrual')}>
                <option value="accrual">Accrual</option>
                <option value="cash">Cash</option>
              </Form.Select>
            </Col>
            <Col md={3}>
              <Button onClick={() => create.mutate()}>Create working paper</Button>
            </Col>
          </Row>
        </Card.Body>
      </Card>
      {(query.data ?? []).map((year) => (
        <Card className="mb-3" key={year.id}>
          <Card.Body>
            <div className="d-flex flex-wrap justify-content-between gap-2">
              <Card.Title>
                {year.year_end} · revision {year.revision} · {year.basis}
              </Card.Title>
              <div>
                {year.status === 'draft' && (
                  <>
                    <Button size="sm" className="me-2" onClick={() => capture.mutate(year.id)}>
                      Capture inventory
                    </Button>
                    <Button size="sm" variant="success" onClick={() => finalize.mutate(year.id)}>
                      Finalize
                    </Button>
                  </>
                )}{' '}
                <Button size="sm" variant="outline-secondary" href={incomeYearExportUrl(year.id)}>
                  Export CSV
                </Button>
              </div>
            </div>
            {year.live_report.data_quality.map((issue) => (
              <Alert variant="warning" key={issue.code}>
                {issue.message}
              </Alert>
            ))}
            <Row>
              {Object.entries(year.live_report.totals).map(([label, amount]) => (
                <Col sm={6} lg={4} key={label}>
                  <strong>{label.replaceAll('_', ' ')}</strong>
                  <div>{formatMoney(amount, year.live_report.currency_code)}</div>
                </Col>
              ))}
            </Row>
            <h3 className="h6 mt-3">Closing stock</h3>
            <Table responsive size="sm">
              <thead>
                <tr>
                  <th>Category</th>
                  <th>Description</th>
                  <th>Method</th>
                  <th>Value</th>
                  <th>Source</th>
                </tr>
              </thead>
              <tbody>
                {year.stock_lines.map((line) => (
                  <tr key={line.id}>
                    <td>{line.category.replaceAll('_', ' ')}</td>
                    <td>
                      {line.description}
                      {line.provisional && <span className="text-warning"> · provisional</span>}
                    </td>
                    <td>{line.method.replaceAll('_', ' ')}</td>
                    <td>{formatMoney(line.value, line.currency_code)}</td>
                    <td>{line.derived ? `${line.source_type} #${line.source_id}` : 'Manual'}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
            {year.status === 'draft' && (
              <Button
                size="sm"
                variant="outline-primary"
                onClick={() => {
                  const description = window.prompt('Manual stock description')
                  const value = window.prompt('Value')
                  const evidence = window.prompt('Evidence URL')
                  if (description && value && evidence)
                    manual.mutate({
                      pk: year.id,
                      data: {
                        category: 'other',
                        description,
                        source_type: 'manual',
                        source_id: crypto.randomUUID(),
                        method: 'cost',
                        value,
                        currency_code: workspace.currency_code,
                        evidence_url: evidence
                      }
                    })
                }}
              >
                Add manual stock
              </Button>
            )}
          </Card.Body>
        </Card>
      ))}
    </>
  )
}

function BookkeepingView({ workspace }: { workspace: Workspace }) {
  return (
    <main className="container py-3">
      <h1>Income-tax bookkeeping</h1>
      <p className="text-muted">Working papers and reconciliations only—this does not prepare or file a tax return.</p>
      <Tabs defaultActiveKey="years" className="mb-3">
        <Tab eventKey="years" title="Income years">
          <IncomeYearPanel workspace={workspace} />
        </Tab>
        <Tab eventKey="entries" title="Other money">
          <EntryPanel workspace={workspace} />
        </Tab>
        <Tab eventKey="assets" title="Tax assets">
          <AssetPanel workspace={workspace} />
        </Tab>
        <Tab eventKey="liabilities" title="Liabilities">
          <LiabilityPanel workspace={workspace} />
        </Tab>
      </Tabs>
    </main>
  )
}

export { BookkeepingView }
