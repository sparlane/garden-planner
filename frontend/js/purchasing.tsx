import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Nav, Row, Table } from 'react-bootstrap'

import {
  confirmBusinessExpense,
  confirmPurchaseOrder,
  confirmSupplierInvoice,
  correctSupplierInvoice,
  createBusinessExpense,
  createExpenseCategory,
  createPurchaseOrder,
  createRequisition,
  createSupplierInvoice,
  createSupplierPayment,
  getBusinessExpenses,
  getExpenseCategories,
  getPurchaseOrders,
  getPurchasingSummary,
  getRequisitions,
  getSupplierInvoices,
  matchPurchaseReceipt,
  replaceSupplierInvoiceDraft,
  reviewRequisition
} from './api/purchasing'
import { getInventoryItems, getStockReceipts } from './api/inventory'
import { getSuppliers } from './api/supplies'
import { queryKeys } from './query'
import { InventoryItem, PurchaseTaxTreatment, StockReceipt, StockReceiptLine } from './types/inventory'
import { PurchaseOrder, PurchaseOrderLine, SupplierInvoice } from './types/purchasing'
import { Workspace } from './types/workspace'
import { errorsByField, formatMoney, sumMoney } from './utils'

type PurchasingTab = 'dashboard' | 'requisitions' | 'orders' | 'invoices' | 'expenses'
type AmountBasis = 'incl' | 'excl'

function localToday() {
  const date = new Date()
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}

function ErrorMessage({ error }: { error: unknown }) {
  if (!error) return null
  return <Alert variant="danger">{error instanceof Error ? error.message : String(error)}</Alert>
}

function itemName(pk: number, items: Array<InventoryItem>) {
  return items.find((item) => item.pk === pk)?.name ?? `Item #${pk}`
}

function receiptLineLabel(receipt: StockReceipt, line: StockReceiptLine, items: Array<InventoryItem>) {
  const seed = receipt.is_seed_packet_draft ? 'seed packet' : 'stock'
  return `Receipt #${receipt.pk} · ${seed} · ${itemName(line.item, items)} · ${line.supplier_cost_incl_tax} ${receipt.currency_code}`
}

interface ReceiptChoice {
  receipt: StockReceipt
  line: StockReceiptLine
}

// One row of an invoice being built. A supplier document charges for received
// stock, its freight, and non-stock costs in whatever mixture the supplier
// chose, and `SupplierInvoiceLine.clean` lets each line carry exactly one of
// those targets -- so the kind is what the row is for, not what the invoice is.
type InvoiceLineKind = 'receipt' | 'freight' | 'expense'

interface DraftInvoiceLine {
  key: string
  kind: InvoiceLineKind
  receiptLine: number | null
  expenseCategory: number | ''
  description: string
  subtotal: string
  taxRate: string
  tax: string
}

const LINE_KIND_LABELS: Record<InvoiceLineKind, string> = {
  receipt: 'Received stock',
  freight: 'Freight',
  expense: 'Other charge'
}

function Dashboard({ workspace }: { workspace: Workspace }) {
  const { data: summary, isPending } = useQuery({ queryKey: queryKeys.purchasing.summary, queryFn: ({ signal }) => getPurchasingSummary(signal) })
  if (isPending || !summary) return <p>Loading purchasing summary…</p>
  return (
    <>
      <Row className="g-3 mb-3">
        <Col md={3}>
          <Card body>
            <div className="text-muted">Committed spend</div>
            <strong>{formatMoney(summary.committed_spend, workspace.currency_code)}</strong>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Cash paid</div>
            <strong>{formatMoney(summary.cash_paid, workspace.currency_code)}</strong>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Confirmed expenses</div>
            <strong>{formatMoney(summary.expenses.total_incl_tax, workspace.currency_code)}</strong>
          </Card>
        </Col>
        <Col md={3}>
          <Card body>
            <div className="text-muted">Overdue invoices</div>
            <strong>{summary.overdue_invoices.length}</strong>
          </Card>
        </Col>
      </Row>
      <h2 className="h5">Data-quality findings</h2>
      {summary.warnings.length === 0 ? (
        <Alert variant="success">Orders, receipts, and invoices reconcile.</Alert>
      ) : (
        summary.warnings.map((warning, index) => (
          <Alert key={`${warning.code}-${warning.source_id}-${index}`} variant="warning" className="py-2">
            {warning.message}
          </Alert>
        ))
      )}
    </>
  )
}

function InvoicePayment({ invoice, workspace, onSaved }: { invoice: SupplierInvoice; workspace: Workspace; onSaved: () => Promise<void> }) {
  const [shown, setShown] = React.useState(false)
  const [paidOn, setPaidOn] = React.useState(localToday())
  const [amount, setAmount] = React.useState(invoice.state.balance_due)
  const [reference, setReference] = React.useState('')
  const mutation = useMutation({
    mutationFn: () =>
      createSupplierPayment({
        supplier: invoice.supplier,
        paid_on: paidOn,
        amount,
        currency_code: workspace.currency_code,
        method: 'bank_transfer',
        external_reference: reference,
        notes: '',
        allocations: [{ invoice: invoice.pk, amount }]
      }),
    onSuccess: async () => {
      setShown(false)
      await onSaved()
    }
  })
  if (!shown)
    return (
      <Button size="sm" variant="outline-success" disabled={invoice.state.payment_state === 'paid'} onClick={() => setShown(true)}>
        Record payment
      </Button>
    )
  return (
    <div className="d-flex flex-column gap-1">
      <Form.Control size="sm" type="date" value={paidOn} onChange={(event) => setPaidOn(event.target.value)} />
      <Form.Control size="sm" type="number" min="0.0001" step="0.0001" value={amount} onChange={(event) => setAmount(event.target.value)} />
      <Form.Control size="sm" placeholder="Payment reference" value={reference} onChange={(event) => setReference(event.target.value)} />
      <div>
        <Button size="sm" disabled={mutation.isPending || !paidOn || !amount} onClick={() => mutation.mutate()}>
          Save
        </Button>{' '}
        <Button size="sm" variant="secondary" onClick={() => setShown(false)}>
          Cancel
        </Button>
      </div>
      <ErrorMessage error={mutation.error} />
    </div>
  )
}

function InvoiceCorrection({ invoice, onClosed, onSaved }: { invoice: SupplierInvoice; onClosed: () => void; onSaved: () => Promise<void> }) {
  const [kind, setKind] = React.useState<'credit' | 'debit'>('credit')
  const [reference, setReference] = React.useState('')
  const [correctedOn, setCorrectedOn] = React.useState(localToday())
  const [subtotal, setSubtotal] = React.useState('0')
  const [tax, setTax] = React.useState('0')
  const [total, setTotal] = React.useState('0')
  const [reason, setReason] = React.useState('')
  const mutation = useMutation({
    mutationFn: () =>
      correctSupplierInvoice(invoice.pk, {
        kind,
        external_reference: reference,
        corrected_on: correctedOn,
        subtotal_ex_tax: subtotal,
        tax_total: tax,
        total_incl_tax: total,
        reason
      }),
    onSuccess: async () => {
      onClosed()
      await onSaved()
    }
  })
  return (
    <Card body className="mb-3">
      <h3 className="h6">Correct {invoice.external_reference}</h3>
      <Row className="g-2">
        <Col md={2}>
          <Form.Select value={kind} onChange={(event) => setKind(event.target.value as 'credit' | 'debit')}>
            <option value="credit">Credit</option>
            <option value="debit">Debit</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Control placeholder="Correction reference" value={reference} onChange={(event) => setReference(event.target.value)} />
        </Col>
        <Col md={2}>
          <Form.Control type="date" value={correctedOn} onChange={(event) => setCorrectedOn(event.target.value)} />
        </Col>
        <Col md={1}>
          <Form.Control type="number" placeholder="Ex tax" value={subtotal} onChange={(event) => setSubtotal(event.target.value)} />
        </Col>
        <Col md={1}>
          <Form.Control type="number" placeholder="Tax" value={tax} onChange={(event) => setTax(event.target.value)} />
        </Col>
        <Col md={1}>
          <Form.Control type="number" placeholder="Total" value={total} onChange={(event) => setTotal(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Control placeholder="Reason" value={reason} onChange={(event) => setReason(event.target.value)} />
        </Col>
      </Row>
      <div className="mt-2">
        <Button size="sm" disabled={mutation.isPending || !reference || !reason} onClick={() => mutation.mutate()}>
          Issue correction
        </Button>{' '}
        <Button size="sm" variant="secondary" onClick={onClosed}>
          Cancel
        </Button>
      </div>
      <ErrorMessage error={mutation.error} />
    </Card>
  )
}

function Invoices({
  workspace,
  suppliers,
  items,
  receipts
}: {
  workspace: Workspace
  suppliers: Awaited<ReturnType<typeof getSuppliers>>
  items: Array<InventoryItem>
  receipts: Array<StockReceipt>
}) {
  const queryClient = useQueryClient()
  const { data: invoices = [] } = useQuery({ queryKey: queryKeys.purchasing.invoices, queryFn: ({ signal }) => getSupplierInvoices(signal) })
  const { data: categories = [] } = useQuery({ queryKey: queryKeys.purchasing.categories, queryFn: ({ signal }) => getExpenseCategories(signal) })
  const [supplier, setSupplier] = React.useState<number | ''>('')
  const [reference, setReference] = React.useState('')
  const [invoiceDate, setInvoiceDate] = React.useState(localToday())
  const [dueDate, setDueDate] = React.useState('')
  const [attachmentUrl, setAttachmentUrl] = React.useState('')
  const [lines, setLines] = React.useState<Array<DraftInvoiceLine>>([])
  const [correcting, setCorrecting] = React.useState<SupplierInvoice | null>(null)
  const nextKey = React.useRef(0)
  // An invoice is created as a draft and then confirmed, and only the confirm
  // step can discover that a line was claimed by someone else in between. The
  // draft it leaves behind already holds the invoice number, so a retry has to
  // replace that draft rather than post a second one against a taken reference.
  const draftPk = React.useRef<number | null>(null)
  // A receipt line stays claimable until an invoice that has not been cancelled
  // holds it: `confirm_invoice` refuses a second confirmed claim on one line,
  // and a draft is on its way to becoming that claim.
  const claimed = new Set(
    invoices
      .filter((invoice) => invoice.status !== 'cancelled')
      .flatMap((invoice) => invoice.lines.map((line) => line.receipt_line))
      .filter((pk): pk is number => pk !== null)
  )
  const drafted = new Set(lines.map((line) => line.receiptLine).filter((pk): pk is number => pk !== null))
  const choices: Array<ReceiptChoice> = receipts
    .flatMap((receipt) => receipt.lines.map((line) => ({ receipt, line })))
    .filter(({ receipt, line }) => receipt.supplier === supplier && !claimed.has(line.pk) && !drafted.has(line.pk))
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.purchasing.all })
    await queryClient.invalidateQueries({ queryKey: queryKeys.inventory.receiptsAll })
  }
  function addLine(line: Omit<DraftInvoiceLine, 'key'>) {
    nextKey.current += 1
    setLines((current) => [...current, { ...line, key: `line-${nextKey.current}` }])
  }
  function updateLine(key: string, changes: Partial<DraftInvoiceLine>) {
    setLines((current) => current.map((line) => (line.key === key ? { ...line, ...changes } : line)))
  }
  function removeLine(key: string) {
    setLines((current) => current.filter((line) => line.key !== key))
  }
  // The amounts a receipt already recorded are the ones the supplier document is
  // most likely to repeat, so a stock line arrives filled in and is corrected
  // only where the invoice actually disagrees with what was received.
  function addReceiptLine(value: string) {
    const choice = choices.find(({ line }) => line.pk === Number(value))
    if (!choice) return
    addLine({
      kind: 'receipt',
      receiptLine: choice.line.pk,
      expenseCategory: '',
      description: itemName(choice.line.item, items),
      subtotal: choice.line.line_cost_ex_tax,
      taxRate: choice.line.tax_rate,
      tax: choice.line.input_tax_amount
    })
  }
  function chooseSupplier(value: string) {
    const pk = value ? Number(value) : ''
    setSupplier(pk)
    // A receipt line can only be invoiced by the supplier it was received from,
    // so lines staged against the previous choice cannot survive the change.
    setLines((current) => current.filter((line) => line.kind !== 'receipt'))
  }
  const subtotalTotal = sumMoney(lines.map((line) => line.subtotal))
  const taxTotal = sumMoney(lines.map((line) => line.tax))
  const invoiceTotal = sumMoney([subtotalTotal, taxTotal])
  const create = useMutation({
    mutationFn: async () => {
      const payload = {
        supplier,
        external_reference: reference,
        invoice_date: invoiceDate,
        due_date: dueDate || null,
        currency_code: workspace.currency_code,
        attachment_url: attachmentUrl,
        notes: '',
        lines: lines.map((line) => ({
          description: line.description,
          receipt_line: line.receiptLine,
          purchase_order_line: null,
          expense_category: line.kind === 'expense' ? line.expenseCategory : null,
          is_freight: line.kind === 'freight',
          subtotal_ex_tax: line.subtotal,
          tax_rate: line.taxRate,
          tax_total: line.tax,
          total_incl_tax: sumMoney([line.subtotal, line.tax])
        }))
      }
      const draft = draftPk.current === null ? await createSupplierInvoice(payload) : await replaceSupplierInvoiceDraft(draftPk.current, payload)
      draftPk.current = draft.pk
      const confirmed = await confirmSupplierInvoice(draft.pk)
      draftPk.current = null
      return confirmed
    },
    onSuccess: async () => {
      setLines([])
      setReference('')
      setDueDate('')
      setAttachmentUrl('')
      await refresh()
    }
  })
  const fieldErrors = errorsByField(create.error)
  const unclassified = lines.some((line) => line.kind === 'expense' && !line.expenseCategory)
  const incomplete = !supplier || !reference || !invoiceDate || lines.length === 0 || unclassified
  return (
    <>
      <Card body className="mb-3">
        <h2 className="h5">Create supplier invoice</h2>
        <p className="text-muted">
          One supplier document, however many lines it has. Add every posted receipt line it covers — seed packets included — alongside its freight and any non-stock charge.
          Confirming the invoice records the liability without changing the cost any posted lot was valued at.
        </p>
        <Row className="g-2">
          <Col md={3}>
            <Form.Label>Supplier</Form.Label>
            <Form.Select value={supplier} onChange={(event) => chooseSupplier(event.target.value)} isInvalid={'supplier' in fieldErrors}>
              <option value="">Select…</option>
              {suppliers.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>Invoice number</Form.Label>
            <Form.Control value={reference} onChange={(event) => setReference(event.target.value)} isInvalid={'external_reference' in fieldErrors} />
            {fieldErrors.external_reference && <Form.Control.Feedback type="invalid">{fieldErrors.external_reference}</Form.Control.Feedback>}
          </Col>
          <Col md={2}>
            <Form.Label>Invoice date</Form.Label>
            <Form.Control type="date" value={invoiceDate} onChange={(event) => setInvoiceDate(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Label>Due date</Form.Label>
            <Form.Control type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} isInvalid={'due_date' in fieldErrors} />
            {fieldErrors.due_date && <Form.Control.Feedback type="invalid">{fieldErrors.due_date}</Form.Control.Feedback>}
          </Col>
          <Col md={2}>
            <Form.Label>Evidence URL</Form.Label>
            <Form.Control type="url" value={attachmentUrl} onChange={(event) => setAttachmentUrl(event.target.value)} />
          </Col>
        </Row>
        <h3 className="h6 mt-3">Invoice lines</h3>
        {lines.length === 0 && <p className="text-muted">No lines yet. Add the received stock this invoice charges for, then its freight or other charges.</p>}
        {lines.length > 0 && (
          <Table responsive size="sm" className="align-middle">
            <thead>
              <tr>
                <th>Line</th>
                <th>Description</th>
                <th>Subtotal ex tax</th>
                <th>Tax rate (%)</th>
                <th>Tax</th>
                <th>Total</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.key}>
                  <td>
                    <Badge bg="secondary">{LINE_KIND_LABELS[line.kind]}</Badge>
                    {line.kind === 'expense' && (
                      <Form.Select
                        className="mt-1"
                        size="sm"
                        value={line.expenseCategory}
                        onChange={(event) => updateLine(line.key, { expenseCategory: event.target.value ? Number(event.target.value) : '' })}
                      >
                        <option value="">Category…</option>
                        {categories
                          .filter((entry) => entry.active)
                          .map((entry) => (
                            <option key={entry.pk} value={entry.pk}>
                              {entry.name}
                            </option>
                          ))}
                      </Form.Select>
                    )}
                  </td>
                  <td>
                    <Form.Control size="sm" value={line.description} onChange={(event) => updateLine(line.key, { description: event.target.value })} />
                  </td>
                  <td>
                    <Form.Control size="sm" type="number" step="0.0001" value={line.subtotal} onChange={(event) => updateLine(line.key, { subtotal: event.target.value })} />
                  </td>
                  <td>
                    <Form.Control size="sm" type="number" step="0.0001" value={line.taxRate} onChange={(event) => updateLine(line.key, { taxRate: event.target.value })} />
                  </td>
                  <td>
                    <Form.Control size="sm" type="number" step="0.0001" value={line.tax} onChange={(event) => updateLine(line.key, { tax: event.target.value })} />
                  </td>
                  <td>{formatMoney(sumMoney([line.subtotal, line.tax]), workspace.currency_code)}</td>
                  <td>
                    <Button size="sm" variant="outline-danger" onClick={() => removeLine(line.key)}>
                      Remove
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr>
                <th colSpan={2}>Invoice total</th>
                <th>{formatMoney(subtotalTotal, workspace.currency_code)}</th>
                <th />
                <th>{formatMoney(taxTotal, workspace.currency_code)}</th>
                <th>{formatMoney(invoiceTotal, workspace.currency_code)}</th>
                <th />
              </tr>
            </tfoot>
          </Table>
        )}
        <Row className="g-2 align-items-end">
          <Col md={6}>
            <Form.Label>Add received item</Form.Label>
            <Form.Select value="" disabled={!supplier} onChange={(event) => addReceiptLine(event.target.value)}>
              <option value="">{supplier ? 'Select a posted receipt line…' : 'Choose a supplier first…'}</option>
              {choices.map(({ receipt, line }) => (
                <option key={line.pk} value={line.pk}>
                  {receiptLineLabel(receipt, line, items)}
                </option>
              ))}
            </Form.Select>
            {supplier !== '' && choices.length === 0 && <Form.Text>Every posted receipt line from this supplier is already invoiced or already on this draft.</Form.Text>}
          </Col>
          <Col md="auto">
            <Button
              variant="outline-secondary"
              onClick={() =>
                addLine({ kind: 'freight', receiptLine: null, expenseCategory: '', description: 'Freight', subtotal: '0', taxRate: workspace.default_tax_rate, tax: '0' })
              }
            >
              Add freight
            </Button>
          </Col>
          <Col md="auto">
            <Button
              variant="outline-secondary"
              disabled={categories.length === 0}
              onClick={() => addLine({ kind: 'expense', receiptLine: null, expenseCategory: '', description: '', subtotal: '0', taxRate: workspace.default_tax_rate, tax: '0' })}
            >
              Add other charge
            </Button>
          </Col>
        </Row>
        {unclassified && (
          <Alert variant="warning" className="mt-3">
            Give every other charge an expense category.
          </Alert>
        )}
        {fieldErrors.lines && (
          <Alert variant="danger" className="mt-3">
            {fieldErrors.lines}
          </Alert>
        )}
        {fieldErrors.non_field_errors && (
          <Alert variant="danger" className="mt-3">
            {fieldErrors.non_field_errors}
          </Alert>
        )}
        <Button className="mt-3" disabled={create.isPending || incomplete} onClick={() => create.mutate()}>
          Create and confirm invoice
        </Button>
        <ErrorMessage error={create.error} />
      </Card>
      {correcting && <InvoiceCorrection invoice={correcting} onClosed={() => setCorrecting(null)} onSaved={refresh} />}
      <Table responsive striped>
        <thead>
          <tr>
            <th>Invoice</th>
            <th>Supplier</th>
            <th>Date / due</th>
            <th>Total</th>
            <th>Payment state</th>
            <th>Warnings</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {invoices.map((invoice) => (
            <tr key={invoice.pk}>
              <td>
                {invoice.external_reference}
                <br />
                <small>{invoice.status}</small>
                <ul className="list-unstyled small text-muted mb-0">
                  {invoice.lines.map((line) => (
                    <li key={line.pk}>
                      {line.description} · {formatMoney(line.total_incl_tax, invoice.currency_code)}
                    </li>
                  ))}
                </ul>
              </td>
              <td>{invoice.supplier_name}</td>
              <td>
                {invoice.invoice_date}
                <br />
                {invoice.due_date ?? 'No due date'}
              </td>
              <td>
                {formatMoney(invoice.state.net_total, invoice.currency_code)}
                <br />
                <small>{formatMoney(invoice.state.balance_due, invoice.currency_code)} due</small>
              </td>
              <td>
                <Badge bg={invoice.state.payment_state === 'paid' ? 'success' : invoice.state.payment_state === 'part_paid' ? 'warning' : 'secondary'}>
                  {invoice.state.payment_state.replace('_', ' ')}
                </Badge>
              </td>
              <td>{invoice.state.warnings.join(' ') || '—'}</td>
              <td>
                <InvoicePayment invoice={invoice} workspace={workspace} onSaved={refresh} />{' '}
                <Button size="sm" variant="outline-secondary" disabled={invoice.status !== 'confirmed'} onClick={() => setCorrecting(invoice)}>
                  Correct
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function Requisitions({ suppliers, items }: { suppliers: Awaited<ReturnType<typeof getSuppliers>>; items: Array<InventoryItem> }) {
  const queryClient = useQueryClient()
  const { data: rows = [] } = useQuery({ queryKey: queryKeys.purchasing.requisitions, queryFn: ({ signal }) => getRequisitions(signal) })
  const [item, setItem] = React.useState<number | ''>('')
  const [supplier, setSupplier] = React.useState<number | ''>('')
  const [requiredOn, setRequiredOn] = React.useState(localToday())
  const [quantity, setQuantity] = React.useState('1')
  const [estimate, setEstimate] = React.useState('0')
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.purchasing.all })
  const create = useMutation({
    mutationFn: () =>
      createRequisition({
        item,
        required_on: requiredOn,
        quantity,
        unit_code: items.find((entry) => entry.pk === item)?.base_unit,
        preferred_supplier: supplier || null,
        estimated_total_incl_tax: estimate,
        notes: ''
      }),
    onSuccess: refresh
  })
  const review = useMutation({ mutationFn: reviewRequisition, onSuccess: refresh })
  return (
    <>
      <Card body className="mb-3">
        <h2 className="h5">New purchase requisition</h2>
        <Row className="g-2">
          <Col md={3}>
            <Form.Select value={item} onChange={(event) => setItem(Number(event.target.value))}>
              <option value="">Item…</option>
              {items.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Control type="date" value={requiredOn} onChange={(event) => setRequiredOn(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Control type="number" min="0.000000001" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
          </Col>
          <Col md={3}>
            <Form.Select value={supplier} onChange={(event) => setSupplier(Number(event.target.value))}>
              <option value="">Preferred supplier…</option>
              {suppliers.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Control type="number" min="0" value={estimate} onChange={(event) => setEstimate(event.target.value)} />
          </Col>
        </Row>
        <Button className="mt-2" disabled={!item || !requiredOn || create.isPending} onClick={() => create.mutate()}>
          Save requisition
        </Button>
        <ErrorMessage error={create.error || review.error} />
      </Card>
      <Table striped>
        <thead>
          <tr>
            <th>Item</th>
            <th>Required</th>
            <th>Quantity</th>
            <th>Supplier</th>
            <th>Estimate</th>
            <th>Status</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.pk}>
              <td>{itemName(row.item, items)}</td>
              <td>{row.required_on}</td>
              <td>
                {row.quantity} {row.unit_code}
              </td>
              <td>{suppliers.find((entry) => entry.pk === row.preferred_supplier)?.name ?? 'Any'}</td>
              <td>{row.estimated_total_incl_tax}</td>
              <td>{row.status}</td>
              <td>
                {row.status === 'draft' && (
                  <Button size="sm" onClick={() => review.mutate(row.pk)}>
                    Review
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

function ReceiptMatcher({
  order,
  line,
  receipts,
  items,
  onSaved
}: {
  order: PurchaseOrder
  line: PurchaseOrderLine
  receipts: Array<StockReceipt>
  items: Array<InventoryItem>
  onSaved: () => Promise<void>
}) {
  const choices = receipts
    .flatMap((receipt) => receipt.lines.map((receiptLine) => ({ receipt, line: receiptLine })))
    .filter(({ receipt, line: receiptLine }) => receipt.supplier === order.supplier && receiptLine.item === line.item)
  const [receiptLine, setReceiptLine] = React.useState<number | ''>('')
  const chosen = choices.find((choice) => choice.line.pk === receiptLine)
  const [quantity, setQuantity] = React.useState('')
  const mutation = useMutation({
    mutationFn: () => matchPurchaseReceipt(order.pk, { order_line: line.pk, receipt_line: receiptLine, base_quantity: quantity }),
    onSuccess: onSaved
  })
  return (
    <div className="d-flex gap-1">
      <Form.Select
        size="sm"
        value={receiptLine}
        onChange={(event) => {
          const pk = Number(event.target.value)
          setReceiptLine(pk)
          setQuantity(choices.find((choice) => choice.line.pk === pk)?.line.base_quantity ?? '')
        }}
      >
        <option value="">Match receipt…</option>
        {choices.map((choice) => (
          <option key={choice.line.pk} value={choice.line.pk}>
            {receiptLineLabel(choice.receipt, choice.line, items)}
          </option>
        ))}
      </Form.Select>
      <Form.Control size="sm" type="number" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
      <Button size="sm" disabled={!chosen || !quantity || mutation.isPending} onClick={() => mutation.mutate()}>
        Match
      </Button>
    </div>
  )
}

function Orders({
  workspace,
  suppliers,
  items,
  receipts
}: {
  workspace: Workspace
  suppliers: Awaited<ReturnType<typeof getSuppliers>>
  items: Array<InventoryItem>
  receipts: Array<StockReceipt>
}) {
  const queryClient = useQueryClient()
  const { data: orders = [] } = useQuery({ queryKey: queryKeys.purchasing.orders, queryFn: ({ signal }) => getPurchaseOrders(signal) })
  const { data: requisitions = [] } = useQuery({ queryKey: queryKeys.purchasing.requisitions, queryFn: ({ signal }) => getRequisitions(signal) })
  const [requisition, setRequisition] = React.useState<number | ''>('')
  const [supplier, setSupplier] = React.useState<number | ''>('')
  const [item, setItem] = React.useState<number | ''>('')
  const [number, setNumber] = React.useState('')
  const [expected, setExpected] = React.useState('')
  const [quantity, setQuantity] = React.useState('1')
  const [unitPrice, setUnitPrice] = React.useState('0')
  const [taxRate, setTaxRate] = React.useState(workspace.default_tax_rate)
  const [freight, setFreight] = React.useState('0')
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: queryKeys.purchasing.all })
  }
  function chooseRequisition(pk: string) {
    const value = pk ? Number(pk) : ''
    setRequisition(value)
    const selected = requisitions.find((entry) => entry.pk === value)
    if (!selected) return
    setItem(selected.item)
    setQuantity(selected.quantity)
    setSupplier(selected.preferred_supplier ?? '')
    setExpected(selected.required_on)
  }
  const create = useMutation({
    mutationFn: async () =>
      createPurchaseOrder({
        order_number: number,
        supplier,
        ordered_on: localToday(),
        expected_on: expected || null,
        currency_code: workspace.currency_code,
        notes: '',
        lines: [
          {
            item,
            requisition: requisition || null,
            description: itemName(Number(item), items),
            quantity,
            unit_code: items.find((entry) => entry.pk === item)?.base_unit,
            unit_price_ex_tax: unitPrice,
            tax_rate: taxRate,
            freight_ex_tax: freight
          }
        ]
      }),
    onSuccess: refresh
  })
  const confirm = useMutation({ mutationFn: confirmPurchaseOrder, onSuccess: refresh })
  return (
    <>
      <Card body className="mb-3">
        <h2 className="h5">New purchase order</h2>
        <Row className="g-2">
          <Col md={2}>
            <Form.Control placeholder="Order number" value={number} onChange={(event) => setNumber(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Select value={requisition} onChange={(event) => chooseRequisition(event.target.value)}>
              <option value="">Reviewed requisition…</option>
              {requisitions
                .filter((entry) => entry.status === 'reviewed')
                .map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    #{entry.pk} {itemName(entry.item, items)}
                  </option>
                ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Select value={supplier} onChange={(event) => setSupplier(Number(event.target.value))}>
              <option value="">Supplier…</option>
              {suppliers.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Select value={item} onChange={(event) => setItem(Number(event.target.value))}>
              <option value="">Item…</option>
              {items.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Control type="date" value={expected} onChange={(event) => setExpected(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Control type="number" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Control type="number" placeholder="Unit price ex tax" value={unitPrice} onChange={(event) => setUnitPrice(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Control type="number" placeholder="Tax %" value={taxRate} onChange={(event) => setTaxRate(event.target.value)} />
          </Col>
          <Col md={2}>
            <Form.Control type="number" placeholder="Freight ex tax" value={freight} onChange={(event) => setFreight(event.target.value)} />
          </Col>
        </Row>
        <Button className="mt-2" disabled={!number || !supplier || !item || create.isPending} onClick={() => create.mutate()}>
          Save draft order
        </Button>
        <ErrorMessage error={create.error || confirm.error} />
      </Card>
      {orders.map((order) => (
        <Card key={order.pk} body className="mb-2">
          <div className="d-flex justify-content-between">
            <h3 className="h6">
              {order.order_number} · {order.supplier_name} <Badge bg="secondary">{order.status}</Badge>
            </h3>
            <strong>{formatMoney(order.total_incl_tax, order.currency_code)}</strong>
          </div>
          {order.lines.map((line) => (
            <div key={line.pk} className="mb-2">
              <div>
                {line.description}: {line.state.received}/{line.state.ordered} received, {line.state.outstanding} outstanding
                {Number(line.state.over_received) > 0 && `, ${line.state.over_received} over`}
              </div>
              {order.status === 'confirmed' && <ReceiptMatcher order={order} line={line} receipts={receipts} items={items} onSaved={refresh} />}
            </div>
          ))}
          {order.status === 'draft' && (
            <Button size="sm" onClick={() => confirm.mutate(order.pk)}>
              Confirm order
            </Button>
          )}
        </Card>
      ))}
    </>
  )
}

// GST-inclusive is how a receipt is usually written, GST-exclusive how a
// supplier invoice usually is, so the form takes whichever pair of figures the
// document actually shows and derives the third. The arithmetic runs through
// sumMoney rather than Number so the preview cannot disagree with the totals
// the server then stores from the same strings.
function expenseAmounts(basis: AmountBasis, amount: string, tax: string) {
  const taxTotal = sumMoney([tax])
  const entered = sumMoney([amount])
  const subtotal = basis === 'incl' ? sumMoney([entered, `-${taxTotal}`]) : entered
  const total = basis === 'incl' ? entered : sumMoney([entered, taxTotal])
  return { subtotal, taxTotal, total, reconciles: sumMoney([subtotal, taxTotal]) === total }
}

function Expenses({ workspace, suppliers }: { workspace: Workspace; suppliers: Awaited<ReturnType<typeof getSuppliers>> }) {
  const queryClient = useQueryClient()
  const { data: categories = [] } = useQuery({ queryKey: queryKeys.purchasing.categories, queryFn: ({ signal }) => getExpenseCategories(signal) })
  const { data: expenses = [] } = useQuery({ queryKey: queryKeys.purchasing.expenses, queryFn: ({ signal }) => getBusinessExpenses(signal) })
  const [categoryName, setCategoryName] = React.useState('')
  const [category, setCategory] = React.useState<number | ''>('')
  const [supplier, setSupplier] = React.useState<number | ''>('')
  const [payee, setPayee] = React.useState('')
  const [incurredOn, setIncurredOn] = React.useState(localToday())
  const [paidOn, setPaidOn] = React.useState('')
  const [amountBasis, setAmountBasis] = React.useState<AmountBasis>('incl')
  const [amount, setAmount] = React.useState('0')
  const [tax, setTax] = React.useState('0')
  const [taxTreatment, setTaxTreatment] = React.useState<PurchaseTaxTreatment>('unknown')
  const [claimInputTax, setClaimInputTax] = React.useState(false)
  const [claimablePercentage, setClaimablePercentage] = React.useState('0')
  const [apportionmentBasis, setApportionmentBasis] = React.useState('')
  const [allocationType, setAllocationType] = React.useState('')
  const [allocationReference, setAllocationReference] = React.useState('')
  const refresh = () => queryClient.invalidateQueries({ queryKey: queryKeys.purchasing.all })
  const addCategory = useMutation({
    mutationFn: () => createExpenseCategory({ name: categoryName, active: true, notes: '' }),
    onSuccess: async (created) => {
      setCategory(created.pk)
      setCategoryName('')
      await refresh()
    }
  })
  const { subtotal, taxTotal, total, reconciles } = expenseAmounts(amountBasis, amount, tax)
  const claimedPercentage = claimInputTax ? claimablePercentage : '0'
  // Only a partial claim has to be explained, and the server draws that line at
  // the two unapportioned percentages rather than at the text of the field, so
  // the same comparison is made numerically here: "100.00" is a full claim too.
  const apportioned = claimInputTax && Number(claimedPercentage) !== 100
  const create = useMutation({
    mutationFn: async () => {
      const draft = await createBusinessExpense({
        category,
        supplier: supplier || null,
        payee,
        incurred_on: incurredOn,
        paid_on: paidOn || null,
        currency_code: workspace.currency_code,
        subtotal_ex_tax: subtotal,
        tax_total: taxTotal,
        total_incl_tax: total,
        tax_treatment: taxTreatment,
        claim_input_tax: claimInputTax,
        claimable_percentage: claimedPercentage,
        apportionment_basis: apportioned ? apportionmentBasis : '',
        allocation_type: allocationType,
        allocation_reference: allocationReference,
        attachment_url: '',
        notes: ''
      })
      return confirmBusinessExpense(draft.pk)
    },
    onSuccess: refresh
  })
  const fieldErrors = errorsByField(create.error)
  function fieldError(field: string) {
    return fieldErrors[field] ? <Form.Control.Feedback type="invalid">{fieldErrors[field]}</Form.Control.Feedback> : null
  }
  // Zero-rated, exempt and out-of-scope purchases carry no GST to record, and
  // only a standard-rated one carries GST that can be claimed as input tax --
  // the model has no second-hand or customs input-tax source to claim from.
  const taxless = taxTreatment === 'zero_rated' || taxTreatment === 'exempt' || taxTreatment === 'out_of_scope'
  function changeTreatment(value: PurchaseTaxTreatment) {
    setTaxTreatment(value)
    if (value === 'zero_rated' || value === 'exempt' || value === 'out_of_scope') setTax('0')
    if (value !== 'standard') {
      setClaimInputTax(false)
      setClaimablePercentage('0')
      setApportionmentBasis('')
    }
  }
  const incomplete =
    !category ||
    (!supplier && !payee) ||
    !incurredOn ||
    !reconciles ||
    subtotal.startsWith('-') ||
    (claimInputTax && !(Number(claimablePercentage) > 0)) ||
    (apportioned && !apportionmentBasis.trim())
  return (
    <>
      <Card body className="mb-3">
        <h2 className="h5">Expense categories</h2>
        <div className="d-flex gap-2">
          <Form.Control value={categoryName} placeholder="New category" onChange={(event) => setCategoryName(event.target.value)} />
          <Button disabled={!categoryName} onClick={() => addCategory.mutate()}>
            Add
          </Button>
        </div>
        <hr />
        <h2 className="h5">Record business expense</h2>
        <Row className="g-2">
          <Col md={2}>
            <Form.Label htmlFor="expense-category">Category</Form.Label>
            <Form.Select id="expense-category" value={category} isInvalid={'category' in fieldErrors} onChange={(event) => setCategory(Number(event.target.value))}>
              <option value="">Category…</option>
              {categories
                .filter((entry) => entry.active)
                .map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
            </Form.Select>
            {fieldError('category')}
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-supplier">Supplier</Form.Label>
            <Form.Select id="expense-supplier" value={supplier} onChange={(event) => setSupplier(Number(event.target.value))}>
              <option value="">Supplier (optional)…</option>
              {suppliers.map((entry) => (
                <option key={entry.pk} value={entry.pk}>
                  {entry.name}
                </option>
              ))}
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-payee">Payee</Form.Label>
            <Form.Control id="expense-payee" placeholder="Payee" value={payee} isInvalid={'payee' in fieldErrors} onChange={(event) => setPayee(event.target.value)} />
            {fieldError('payee')}
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-incurred-on">Incurred on</Form.Label>
            <Form.Control
              id="expense-incurred-on"
              type="date"
              value={incurredOn}
              isInvalid={'incurred_on' in fieldErrors}
              onChange={(event) => setIncurredOn(event.target.value)}
            />
            {fieldError('incurred_on')}
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-paid-on">Paid on</Form.Label>
            <Form.Control id="expense-paid-on" type="date" value={paidOn} isInvalid={'paid_on' in fieldErrors} onChange={(event) => setPaidOn(event.target.value)} />
            {fieldError('paid_on')}
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-treatment">Tax treatment</Form.Label>
            <Form.Select id="expense-treatment" value={taxTreatment} onChange={(event) => changeTreatment(event.target.value as PurchaseTaxTreatment)}>
              <option value="unknown">Unknown</option>
              <option value="standard">Standard-rated</option>
              <option value="zero_rated">Zero-rated</option>
              <option value="exempt">Exempt</option>
              <option value="out_of_scope">Out of scope</option>
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-basis">Amounts entered</Form.Label>
            <Form.Select id="expense-basis" value={amountBasis} onChange={(event) => setAmountBasis(event.target.value as AmountBasis)}>
              <option value="incl">Including GST</option>
              <option value="excl">Excluding GST</option>
            </Form.Select>
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-amount">{amountBasis === 'incl' ? 'Total incl GST' : 'Amount excl GST'}</Form.Label>
            <Form.Control
              id="expense-amount"
              type="number"
              min="0"
              step="0.0001"
              value={amount}
              isInvalid={'total_incl_tax' in fieldErrors || 'subtotal_ex_tax' in fieldErrors}
              onChange={(event) => setAmount(event.target.value)}
            />
            {fieldError('subtotal_ex_tax')}
            {fieldError('total_incl_tax')}
          </Col>
          <Col md={2}>
            <Form.Label htmlFor="expense-tax">GST</Form.Label>
            <Form.Control
              id="expense-tax"
              type="number"
              min="0"
              step="0.0001"
              value={tax}
              disabled={taxless}
              isInvalid={'tax_total' in fieldErrors}
              onChange={(event) => setTax(event.target.value)}
            />
            {fieldError('tax_total')}
          </Col>
          <Col md={3}>
            <Form.Label>Recorded as</Form.Label>
            <div className="form-control-plaintext" aria-live="polite">
              {formatMoney(subtotal, workspace.currency_code)} + {formatMoney(taxTotal, workspace.currency_code)} GST = {formatMoney(total, workspace.currency_code)}
            </div>
          </Col>
          {taxTreatment === 'standard' && (
            <Col md={3}>
              <Form.Check
                className="mt-4"
                type="checkbox"
                id="expense-claim-input-tax"
                label="Claim the GST as input tax"
                checked={claimInputTax}
                onChange={(event) => {
                  setClaimInputTax(event.target.checked)
                  setClaimablePercentage(event.target.checked ? '100' : '0')
                  if (!event.target.checked) setApportionmentBasis('')
                }}
              />
            </Col>
          )}
          {claimInputTax && (
            <Col md={2}>
              <Form.Label htmlFor="expense-claimable">Claimable %</Form.Label>
              <Form.Control
                id="expense-claimable"
                type="number"
                min="0"
                max="100"
                step="0.0001"
                value={claimablePercentage}
                isInvalid={'claimable_percentage' in fieldErrors}
                onChange={(event) => setClaimablePercentage(event.target.value)}
              />
              {fieldError('claimable_percentage')}
            </Col>
          )}
          {apportioned && (
            <Col md={4}>
              <Form.Label htmlFor="expense-apportionment">Apportionment basis</Form.Label>
              <Form.Control
                id="expense-apportionment"
                placeholder="Why this share is business use"
                value={apportionmentBasis}
                isInvalid={'apportionment_basis' in fieldErrors}
                onChange={(event) => setApportionmentBasis(event.target.value)}
              />
              {fieldError('apportionment_basis')}
            </Col>
          )}
          <Col md={2}>
            <Form.Label htmlFor="expense-allocation-type">Allocation type</Form.Label>
            <Form.Control id="expense-allocation-type" placeholder="Allocation type" value={allocationType} onChange={(event) => setAllocationType(event.target.value)} />
          </Col>
          <Col md={3}>
            <Form.Label htmlFor="expense-allocation-reference">Allocation reference</Form.Label>
            <Form.Control
              id="expense-allocation-reference"
              placeholder="Allocation reference"
              value={allocationReference}
              onChange={(event) => setAllocationReference(event.target.value)}
            />
          </Col>
        </Row>
        {!reconciles && (
          <Alert className="mt-2 mb-0" variant="warning">
            Enter the GST as a positive figure so the amount, the GST, and the total agree.
          </Alert>
        )}
        {subtotal.startsWith('-') && (
          <Alert className="mt-2 mb-0" variant="warning">
            The GST is larger than the amount entered.
          </Alert>
        )}
        <Button className="mt-2" disabled={incomplete || create.isPending} onClick={() => create.mutate()}>
          Record and confirm expense
        </Button>
        <ErrorMessage error={create.error || addCategory.error} />
      </Card>
      <Table striped>
        <thead>
          <tr>
            <th>Incurred</th>
            <th>Category</th>
            <th>Payee</th>
            <th>Total</th>
            <th>GST claimed</th>
            <th>Deductible</th>
            <th>Status</th>
            <th>Payment</th>
            <th>Allocation</th>
          </tr>
        </thead>
        <tbody>
          {expenses.map((expense) => (
            <tr key={expense.pk}>
              <td>{expense.incurred_on}</td>
              <td>{categories.find((entry) => entry.pk === expense.category)?.name}</td>
              <td>{suppliers.find((entry) => entry.pk === expense.supplier)?.name ?? expense.payee}</td>
              <td>{formatMoney(expense.total_incl_tax, expense.currency_code)}</td>
              <td>{expense.status === 'draft' ? 'Not yet frozen' : formatMoney(expense.recoverable_tax, expense.currency_code)}</td>
              <td>{expense.status === 'draft' ? 'Not yet frozen' : formatMoney(expense.deductible_amount, expense.currency_code)}</td>
              <td>{expense.status}</td>
              <td>{expense.payment_state.replace('_', ' ')}</td>
              <td>{expense.allocation_type ? `${expense.allocation_type}: ${expense.allocation_reference}` : 'Whole business'}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </>
  )
}

function PurchasingView({ workspace }: { workspace: Workspace }) {
  const [tab, setTab] = React.useState<PurchasingTab>('dashboard')
  const { data: suppliers = [] } = useQuery({ queryKey: queryKeys.suppliers.all, queryFn: ({ signal }) => getSuppliers(signal) })
  const { data: items = [] } = useQuery({ queryKey: queryKeys.inventory.items('', '', '', 'active'), queryFn: ({ signal }) => getInventoryItems({ active: true }, signal) })
  const { data: receipts = [] } = useQuery({ queryKey: queryKeys.inventory.receipts('posted', ''), queryFn: ({ signal }) => getStockReceipts({ status: 'posted' }, signal) })
  return (
    <main className="container-fluid py-3">
      <h1>Purchasing and expenses</h1>
      <Nav variant="tabs" activeKey={tab} onSelect={(key) => key && setTab(key as PurchasingTab)} className="mb-3">
        {(['dashboard', 'requisitions', 'orders', 'invoices', 'expenses'] as const).map((key) => (
          <Nav.Item key={key}>
            <Nav.Link eventKey={key}>{key[0].toUpperCase() + key.slice(1)}</Nav.Link>
          </Nav.Item>
        ))}
      </Nav>
      {tab === 'dashboard' && <Dashboard workspace={workspace} />}
      {tab === 'requisitions' && <Requisitions suppliers={suppliers} items={items} />}
      {tab === 'orders' && <Orders workspace={workspace} suppliers={suppliers} items={items} receipts={receipts} />}
      {tab === 'invoices' && <Invoices workspace={workspace} suppliers={suppliers} items={items} receipts={receipts} />}
      {tab === 'expenses' && <Expenses workspace={workspace} suppliers={suppliers} />}
    </main>
  )
}

export { PurchasingView }
