import React from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Badge, Button, Card, Col, Form, Row, Table } from 'react-bootstrap'
import { Link } from 'react-router'

import {
  allocateOrderLine,
  closeAllocations,
  createCustomer,
  createSalesOrder,
  createSalesOrderLine,
  editableOrder,
  getAvailableSerializedUnits,
  getCustomers,
  getFulfillments,
  getPayments,
  getRefunds,
  getReturns,
  getSalesOrder,
  getSalesOrders,
  orderAction,
  postFulfillment,
  postPayment,
  postRefund,
  postReturn,
  previewAllocation,
  reverseCommerce,
  updateCustomer,
  updateSalesOrder
} from './api/sales'
import { SupplyDocumentPanel } from './billing/order_panel'
import { getInventoryBalances, getInventoryItems } from './api/inventory'
import { getLocations } from './api/locations'
import { getHealthObservationTypes } from './api/health'
import { getPlantVarieties } from './api/plants'
import { queryClient, queryKeys } from './query'
import { AllocationPreview, Customer, SalesDiscountType, SalesLineType, SalesOrder, SalesOrderLine, SalesTaxTreatment } from './types/sales'
import { Workspace } from './types/workspace'
import { formatDate, formatDateTime, formatHoldRemaining, formatMoney, localDatetimeInputValue } from './utils'

// 'Not yet classified' is shown as its own state rather than folded into
// zero-rated: a GST return reports zero-rated supplies in their own box, and
// counting an unclassified line there would be a guess nobody made.
const TAX_TREATMENT_LABELS: Record<SalesTaxTreatment, string> = {
  standard: 'standard-rated',
  zero_rated: 'zero-rated',
  exempt: 'exempt',
  out_of_scope: 'outside GST',
  unclassified: 'not yet classified'
}

function invalidateSales(orderPk?: number) {
  void queryClient.invalidateQueries({ queryKey: queryKeys.sales.all })
  void queryClient.invalidateQueries({ queryKey: queryKeys.plantings.registerAll })
  if (orderPk !== undefined) void queryClient.invalidateQueries({ queryKey: queryKeys.sales.order(orderPk) })
}

function CustomerForm({ customer }: { customer?: Customer }) {
  const [name, setName] = React.useState(customer?.name ?? '')
  const [email, setEmail] = React.useState(customer?.email ?? '')
  const [phone, setPhone] = React.useState(customer?.phone ?? '')
  const mutation = useMutation({
    mutationFn: () => (customer ? updateCustomer(customer.pk, { name, email, phone }) : createCustomer({ name, email, phone })),
    onSuccess: () => invalidateSales()
  })
  return (
    <Form
      className="mb-3"
      onSubmit={(event) => {
        event.preventDefault()
        mutation.mutate()
      }}
    >
      <Row className="g-2 align-items-end">
        <Col md={4}>
          <Form.Label>Name</Form.Label>
          <Form.Control required value={name} onChange={(event) => setName(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Label>Email</Form.Label>
          <Form.Control type="email" value={email} onChange={(event) => setEmail(event.target.value)} />
        </Col>
        <Col md={3}>
          <Form.Label>Phone</Form.Label>
          <Form.Control value={phone} onChange={(event) => setPhone(event.target.value)} />
        </Col>
        <Col md={2}>
          <Button type="submit" disabled={!name.trim() || mutation.isPending}>
            {customer ? 'Save' : 'Add customer'}
          </Button>
        </Col>
      </Row>
    </Form>
  )
}

function CustomerListView() {
  const customers = useQuery({ queryKey: queryKeys.sales.customers, queryFn: ({ signal }) => getCustomers(signal) })
  const deactivate = useMutation({ mutationFn: (customer: Customer) => updateCustomer(customer.pk, { active: false }), onSuccess: () => invalidateSales() })
  return (
    <main className="container py-3">
      <h1>Customers</h1>
      <p>Keep historical customers and deactivate them when they should no longer be selected.</p>
      <Card body className="mb-3">
        <CustomerForm />
      </Card>
      {(customers.data ?? []).map((customer) => (
        <Card body className="mb-2" key={customer.pk}>
          <CustomerForm customer={customer} />
          <div className="d-flex justify-content-between">
            <span>{customer.active ? <Badge bg="success">Active</Badge> : <Badge bg="secondary">Inactive</Badge>}</span>
            {customer.active && (
              <Button size="sm" variant="outline-secondary" onClick={() => deactivate.mutate(customer)}>
                Deactivate
              </Button>
            )}
          </div>
        </Card>
      ))}
    </main>
  )
}

function SalesOrderListView() {
  const orders = useQuery({ queryKey: queryKeys.sales.orders, queryFn: ({ signal }) => getSalesOrders(signal) })
  const customers = useQuery({ queryKey: queryKeys.sales.customers, queryFn: ({ signal }) => getCustomers(signal) })
  const [status, setStatus] = React.useState<'quote' | 'draft'>('draft')
  const [customer, setCustomer] = React.useState<number | ''>('')
  const create = useMutation({
    mutationFn: () => createSalesOrder({ status, customer: customer === '' ? null : customer }),
    onSuccess: (order) => {
      invalidateSales(order.pk)
      window.location.hash = `#/sales/orders/${order.pk}`
    }
  })
  return (
    <main className="container py-3">
      <h1>Sales orders</h1>
      <Card body className="mb-3">
        <Row className="g-2 align-items-end">
          <Col md={3}>
            <Form.Label>Start as</Form.Label>
            <Form.Select value={status} onChange={(event) => setStatus(event.target.value as 'quote' | 'draft')}>
              <option value="draft">Draft order</option>
              <option value="quote">Quote</option>
            </Form.Select>
          </Col>
          <Col md={5}>
            <Form.Label>Customer</Form.Label>
            <Form.Select value={customer} onChange={(event) => setCustomer(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Walk-in / no customer</option>
              {(customers.data ?? [])
                .filter((entry) => entry.active)
                .map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
            </Form.Select>
          </Col>
          <Col md={4}>
            <Button onClick={() => create.mutate()}>Create</Button>
          </Col>
        </Row>
      </Card>
      <Table striped responsive>
        <thead>
          <tr>
            <th>Order</th>
            <th>Status</th>
            <th>Customer</th>
            <th>Requested</th>
            <th>Total</th>
          </tr>
        </thead>
        <tbody>
          {(orders.data ?? []).map((order) => (
            <tr key={order.pk}>
              <td>
                <Link to={`/sales/orders/${order.pk}`}>{order.order_number}</Link>
              </td>
              <td>{order.status.replaceAll('_', ' ')}</td>
              <td>{customers.data?.find((entry) => entry.pk === order.customer)?.name ?? 'Walk-in'}</td>
              <td>{order.requested_date ? formatDate(order.requested_date) : 'Not set'}</td>
              <td>{formatMoney(order.total_incl_tax, order.currency_code)}</td>
            </tr>
          ))}
        </tbody>
      </Table>
    </main>
  )
}

function LineForm({ order, workspace }: { order: SalesOrder; workspace: Workspace }) {
  const varieties = useQuery({ queryKey: queryKeys.plants.varieties, queryFn: ({ signal }) => getPlantVarieties(signal) })
  const trayItems = useQuery({
    queryKey: ['inventory', 'sales-trays'],
    queryFn: ({ signal }) => getInventoryItems({ category: 'tray', tracking_mode: 'serialized', active: true }, signal)
  })
  const [lineType, setLineType] = React.useState<SalesLineType>('seedling')
  const [target, setTarget] = React.useState<number | ''>('')
  const [description, setDescription] = React.useState('')
  const [quantity, setQuantity] = React.useState(1)
  const [unitPrice, setUnitPrice] = React.useState('')
  const [taxRate, setTaxRate] = React.useState(workspace.default_tax_rate)
  // Only asked for when the rate is zero. A rate above zero is a standard-rated
  // supply by definition and the server derives it; a rate of zero is three
  // different kinds of supply that a GST return reports in different boxes.
  const [taxTreatment, setTaxTreatment] = React.useState<SalesTaxTreatment>('unclassified')
  const [discountType, setDiscountType] = React.useState<SalesDiscountType>('none')
  const [discountValue, setDiscountValue] = React.useState('0')
  const mutation = useMutation({
    mutationFn: () =>
      createSalesOrderLine({
        order: order.pk,
        line_type: lineType,
        variety: lineType === 'seedling' ? Number(target) : null,
        tray_item: lineType === 'tray' ? Number(target) : null,
        description,
        quantity,
        unit_price: unitPrice,
        tax_rate: taxRate,
        tax_treatment: Number(taxRate) > 0 ? undefined : taxTreatment,
        discount_type: discountType,
        discount_value: discountType === 'none' ? '0' : discountValue
      }),
    onSuccess: () => invalidateSales(order.pk)
  })
  const targets = lineType === 'seedling' ? (varieties.data ?? []) : (trayItems.data ?? [])
  return (
    <Card body className="mb-3">
      <Card.Title>Add line</Card.Title>
      <Row className="g-2 align-items-end">
        <Col md={2}>
          <Form.Label>Type</Form.Label>
          <Form.Select
            value={lineType}
            onChange={(event) => {
              setLineType(event.target.value as SalesLineType)
              setTarget('')
            }}
          >
            <option value="seedling">Seedling</option>
            <option value="tray">Serialized tray</option>
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label>{lineType === 'seedling' ? 'Variety' : 'Tray item'}</Form.Label>
          <Form.Select value={target} onChange={(event) => setTarget(event.target.value === '' ? '' : Number(event.target.value))}>
            <option value="">Select…</option>
            {targets.map((entry) => (
              <option key={entry.pk} value={entry.pk}>
                {entry.name}
              </option>
            ))}
          </Form.Select>
        </Col>
        <Col md={2}>
          <Form.Label>Description snapshot</Form.Label>
          <Form.Control value={description} onChange={(event) => setDescription(event.target.value)} />
        </Col>
        <Col md={1}>
          <Form.Label>Quantity</Form.Label>
          <Form.Control type="number" min={1} value={quantity} onChange={(event) => setQuantity(Number(event.target.value))} />
        </Col>
        <Col md={1}>
          <Form.Label>Unit price</Form.Label>
          <Form.Control type="number" min={0} step="0.0001" value={unitPrice} onChange={(event) => setUnitPrice(event.target.value)} />
        </Col>
        <Col md={1}>
          <Form.Label>Tax %</Form.Label>
          <Form.Control type="number" min={0} max={100} step="0.0001" value={taxRate} onChange={(event) => setTaxRate(event.target.value)} />
        </Col>
        {Number(taxRate) === 0 && (
          <Col md={2}>
            <Form.Label htmlFor="line-tax-treatment">Kind of supply</Form.Label>
            <Form.Select
              id="line-tax-treatment"
              value={taxTreatment}
              onChange={(event) => setTaxTreatment(event.target.value as SalesTaxTreatment)}
              aria-describedby="line-tax-treatment-help"
            >
              <option value="unclassified">Not yet classified</option>
              <option value="zero_rated">Zero-rated</option>
              <option value="exempt">Exempt</option>
              <option value="out_of_scope">Outside GST</option>
            </Form.Select>
            <Form.Text id="line-tax-treatment-help">A GST return reports zero-rated supplies separately from exempt ones.</Form.Text>
          </Col>
        )}
        <Col md={1}>
          <Form.Label>Discount</Form.Label>
          <Form.Select value={discountType} onChange={(event) => setDiscountType(event.target.value as SalesDiscountType)}>
            <option value="none">None</option>
            <option value="fixed">Fixed</option>
            <option value="percentage">Percent</option>
          </Form.Select>
        </Col>
        <Col md={1}>
          <Form.Label>Value</Form.Label>
          <Form.Control disabled={discountType === 'none'} type="number" min={0} step="0.0001" value={discountValue} onChange={(event) => setDiscountValue(event.target.value)} />
        </Col>
        <Col md={1}>
          <Button disabled={target === '' || !description.trim() || !unitPrice} onClick={() => mutation.mutate()}>
            Add
          </Button>
        </Col>
      </Row>
      <Form.Text>Entered prices and fixed discounts {order.prices_include_tax ? 'include' : 'exclude'} tax / GST.</Form.Text>
    </Card>
  )
}

function AllocationPanel({ order, line }: { order: SalesOrder; line: SalesOrderLine }) {
  const [plantIds, setPlantIds] = React.useState('')
  const [batch, setBatch] = React.useState('')
  const [tray, setTray] = React.useState('')
  const [location, setLocation] = React.useState('')
  const [readyTo, setReadyTo] = React.useState('')
  const [selectedUnits, setSelectedUnits] = React.useState<Array<number>>([])
  const [expiresAt, setExpiresAt] = React.useState('')
  const [preview, setPreview] = React.useState<AllocationPreview>()
  const units = useQuery({
    queryKey: queryKeys.sales.availableUnits(line.tray_item ?? 0),
    queryFn: ({ signal }) => getAvailableSerializedUnits(line.tray_item as number, signal),
    enabled: line.line_type === 'tray'
  })
  const previewMutation = useMutation({
    mutationFn: () => {
      const ids = plantIds
        .split(',')
        .map((value) => Number(value.trim()))
        .filter((value) => Number.isInteger(value) && value > 0)
      if (line.line_type === 'tray') return previewAllocation(order.pk, { line: line.pk, unit_ids: selectedUnits })
      if (ids.length) return previewAllocation(order.pk, { line: line.pk, plant_ids: ids })
      return previewAllocation(order.pk, {
        line: line.pk,
        filters: {
          state: ['available'],
          reserved: false,
          quarantined: false,
          ...(batch ? { batch } : {}),
          ...(tray ? { seed_tray: tray } : {}),
          ...(location ? { location } : {}),
          ...(readyTo ? { expected_ready_to: readyTo } : {})
        }
      })
    },
    onSuccess: setPreview
  })
  const allocate = useMutation({
    mutationFn: () =>
      allocateOrderLine(
        order.pk,
        line.pk,
        line.line_type === 'seedling' ? (preview?.selected ?? []) : [],
        line.line_type === 'tray' ? (preview?.selected ?? []) : [],
        expiresAt ? new Date(expiresAt).toISOString() : null
      ),
    onSuccess: () => {
      setPreview(undefined)
      invalidateSales(order.pk)
    }
  })
  const close = useMutation({
    mutationFn: ({ allocation, action }: { allocation: number; action: 'release' | 'expire' }) =>
      closeAllocations(order.pk, action, [allocation], `${action === 'release' ? 'Released' : 'Expired'} by operator.`),
    onSuccess: () => invalidateSales(order.pk)
  })
  const active = line.allocations.filter((allocation) => allocation.status === 'pending' || allocation.status === 'reserved').length
  return (
    <Card body className="mt-2">
      <div className="d-flex justify-content-between">
        <strong>
          Allocations {active}/{line.quantity}
        </strong>
        {active === line.quantity && <Badge bg="success">Complete</Badge>}
      </div>
      <ul className="mb-2">
        {line.allocations.map((allocation) => (
          <li key={allocation.pk}>
            {allocation.plant ? `Plant #${allocation.plant}` : allocation.asset_code} · {allocation.status}
            {allocation.status === 'reserved' && ` · hold ${formatHoldRemaining(allocation.expires_at)}`}
            {allocation.expires_at && ` · expiry ${formatDateTime(allocation.expires_at)}`}
            {allocation.status === 'reserved' && (
              <>
                {' '}
                <Button size="sm" variant="link" onClick={() => close.mutate({ allocation: allocation.pk, action: 'release' })}>
                  Release
                </Button>
                {allocation.expires_at && new Date(allocation.expires_at) <= new Date() && (
                  <Button size="sm" variant="link" onClick={() => close.mutate({ allocation: allocation.pk, action: 'expire' })}>
                    Expire
                  </Button>
                )}
              </>
            )}
            {allocation.competing_claims.map((claim) => (
              <div className="text-warning" key={`${claim.order}:${claim.status}`}>
                Also {claim.status === 'pending' ? 'claimed' : 'reserved'} by <Link to={`/sales/orders/${claim.order}`}>{claim.order_number}</Link>
              </div>
            ))}
          </li>
        ))}
      </ul>
      {active < line.quantity && order.status !== 'cancelled' && order.status !== 'fulfilled' && (
        <>
          {line.line_type === 'seedling' ? (
            <Row className="g-2">
              <Col md={3}>
                <Form.Control placeholder="Plant IDs, comma-separated" value={plantIds} onChange={(event) => setPlantIds(event.target.value)} />
              </Col>
              <Col md={2}>
                <Form.Control placeholder="Batch ID filter" value={batch} onChange={(event) => setBatch(event.target.value)} />
              </Col>
              <Col md={2}>
                <Form.Control placeholder="Tray ID filter" value={tray} onChange={(event) => setTray(event.target.value)} />
              </Col>
              <Col md={2}>
                <Form.Control placeholder="Location ID filter" value={location} onChange={(event) => setLocation(event.target.value)} />
              </Col>
              <Col md={3}>
                <Form.Control type="date" title="Expected ready by" value={readyTo} onChange={(event) => setReadyTo(event.target.value)} />
              </Col>
            </Row>
          ) : (
            <Form.Select
              multiple
              value={selectedUnits.map(String)}
              onChange={(event) => setSelectedUnits(Array.from(event.target.selectedOptions).map((option) => Number(option.value)))}
            >
              {(units.data ?? []).map((unit) => (
                <option key={unit.pk} value={unit.pk}>
                  {unit.asset_code}
                </option>
              ))}
            </Form.Select>
          )}
          <Row className="g-2 align-items-end mt-1">
            <Col md={4}>
              <Form.Label>Optional reservation expiry</Form.Label>
              <Form.Control type="datetime-local" min={localDatetimeInputValue()} value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} />
            </Col>
            <Col md={8}>
              <Button variant="outline-primary" onClick={() => previewMutation.mutate()}>
                Preview exact stock
              </Button>
            </Col>
          </Row>
          {preview && (
            <Alert variant={preview.conflicts.length || preview.warnings.length ? 'warning' : 'success'} className="mt-2">
              {preview.selected.length} eligible. {preview.conflicts.length} conflicts. {preview.warnings.length} warnings.
              {preview.conflicts.map((conflict) => (
                <div key={`${conflict.id}:${conflict.reason}`}>
                  #{conflict.id}: {conflict.reason.replaceAll('_', ' ')}
                  {conflict.order_number && (
                    <>
                      {' '}
                      by <Link to={`/sales/orders/${conflict.order}`}>{conflict.order_number}</Link>
                    </>
                  )}
                </div>
              ))}
              {preview.warnings.map((warning) => (
                <div key={`${warning.id}:${warning.order}`}>
                  #{warning.id}: tentatively claimed by <Link to={`/sales/orders/${warning.order}`}>{warning.order_number}</Link>; allocation is still allowed.
                </div>
              ))}
              <Button className="mt-2" disabled={preview.selected.length === 0 || active + preview.selected.length > line.quantity} onClick={() => allocate.mutate()}>
                Allocate eligible stock
              </Button>
            </Alert>
          )}
        </>
      )}
    </Card>
  )
}

function OrderTotals({ order }: { order: SalesOrder }) {
  return (
    <Card body className="mb-3">
      <Row>
        <Col>
          Gross ex tax
          <br />
          <strong>{formatMoney(order.gross_ex_tax, order.currency_code)}</strong>
        </Col>
        <Col>
          Discount ex tax
          <br />
          <strong>{formatMoney(order.discount_total_ex_tax, order.currency_code)}</strong>
        </Col>
        <Col>
          Subtotal ex tax
          <br />
          <strong>{formatMoney(order.subtotal_ex_tax, order.currency_code)}</strong>
        </Col>
        <Col>
          Tax / GST
          <br />
          <strong>{formatMoney(order.tax_total, order.currency_code)}</strong>
        </Col>
        <Col>
          Total incl tax
          <br />
          <strong>{formatMoney(order.total_incl_tax, order.currency_code)}</strong>
        </Col>
        <Col>
          Margin preview
          <br />
          <strong>{formatMoney(order.margin.estimated_margin, order.currency_code, order.margin.cost_complete ? 'Allocate every unit' : 'Unknown cost')}</strong>
          {order.margin.provisional && <div className="text-warning small">Provisional production cost</div>}
        </Col>
      </Row>
    </Card>
  )
}

function CommercePanel({ order }: { order: SalesOrder }) {
  const fulfillments = useQuery({ queryKey: ['sales', order.pk, 'fulfillments'], queryFn: ({ signal }) => getFulfillments(order.pk, signal) })
  const payments = useQuery({ queryKey: ['sales', order.pk, 'payments'], queryFn: ({ signal }) => getPayments(order.pk, signal) })
  const returns = useQuery({ queryKey: ['sales', order.pk, 'returns'], queryFn: ({ signal }) => getReturns(order.pk, signal) })
  const refunds = useQuery({ queryKey: ['sales', order.pk, 'refunds'], queryFn: ({ signal }) => getRefunds(order.pk, signal) })
  const locations = useQuery({ queryKey: queryKeys.locations.list('active'), queryFn: ({ signal }) => getLocations(signal, true) })
  const healthTypes = useQuery({ queryKey: queryKeys.health.types, queryFn: ({ signal }) => getHealthObservationTypes(signal) })
  const packagingItems = useQuery({
    queryKey: ['inventory', 'sales-packaging'],
    queryFn: ({ signal }) => getInventoryItems({ category: 'packaging', tracking_mode: 'lot', active: true }, signal)
  })
  const [selectedAllocations, setSelectedAllocations] = React.useState<Array<number>>([])
  const [packagingItem, setPackagingItem] = React.useState<number | ''>('')
  const packagingBalances = useQuery({
    queryKey: ['inventory', 'sales-packaging-balances', packagingItem],
    queryFn: ({ signal }) => getInventoryBalances(packagingItem as number, signal),
    enabled: packagingItem !== ''
  })
  const [packagingBalance, setPackagingBalance] = React.useState('')
  const [packagingQuantity, setPackagingQuantity] = React.useState('')
  const [paymentAmount, setPaymentAmount] = React.useState('')
  const [paymentMethod, setPaymentMethod] = React.useState<'cash' | 'card' | 'bank_transfer' | 'other'>('cash')
  const [returnLine, setReturnLine] = React.useState<number | ''>('')
  const [returnOutcome, setReturnOutcome] = React.useState<'available' | 'quarantined' | 'discarded'>('available')
  const [returnDestination, setReturnDestination] = React.useState<number | ''>('')
  const [returnReason, setReturnReason] = React.useState('')
  const [healthType, setHealthType] = React.useState<number | ''>('')
  const [healthSeverity, setHealthSeverity] = React.useState<'low' | 'moderate' | 'high' | 'critical'>('moderate')
  const [refundPayment, setRefundPayment] = React.useState<number | ''>('')
  const [refundLine, setRefundLine] = React.useState<number | ''>('')
  const [refundAmount, setRefundAmount] = React.useState('')
  const [refundReason, setRefundReason] = React.useState('')
  const reserved = order.lines.flatMap((line) => line.allocations.filter((allocation) => allocation.status === 'reserved'))
  const activeFulfillmentLines = (fulfillments.data ?? []).filter((entry) => entry.status === 'posted').flatMap((entry) => entry.lines)
  const activePayments = (payments.data ?? []).filter((entry) => entry.status === 'posted')

  function refreshCommerce() {
    invalidateSales(order.pk)
    void fulfillments.refetch()
    void payments.refetch()
    void returns.refetch()
    void refunds.refetch()
  }

  const fulfill = useMutation({
    mutationFn: () => {
      const balance = packagingBalances.data?.find((entry) => `${entry.lot}:${entry.location}` === packagingBalance)
      return postFulfillment(order.pk, {
        operation_key: crypto.randomUUID(),
        allocation_ids: selectedAllocations,
        packaging: balance && Number(packagingQuantity) > 0 ? [{ lot: balance.lot, source: balance.location, quantity: packagingQuantity }] : []
      })
    },
    onSuccess: () => {
      setSelectedAllocations([])
      setPackagingQuantity('')
      refreshCommerce()
    }
  })
  const pay = useMutation({
    mutationFn: () =>
      postPayment(order.pk, {
        operation_key: crypto.randomUUID(),
        paid_on: new Date().toISOString().slice(0, 10),
        amount: paymentAmount,
        method: paymentMethod
      }),
    onSuccess: () => {
      setPaymentAmount('')
      refreshCommerce()
    }
  })
  const returnMutation = useMutation({
    mutationFn: () =>
      postReturn(order.pk, {
        operation_key: crypto.randomUUID(),
        reason: returnReason,
        items: [
          {
            fulfillment_line: returnLine,
            outcome: returnOutcome,
            destination: returnOutcome === 'discarded' ? null : returnDestination
          }
        ],
        ...(returnOutcome === 'quarantined' ? { observation_type: healthType, severity: healthSeverity } : {})
      }),
    onSuccess: () => {
      setReturnLine('')
      setReturnReason('')
      refreshCommerce()
    }
  })
  const refund = useMutation({
    mutationFn: () =>
      postRefund(order.pk, {
        operation_key: crypto.randomUUID(),
        payment: refundPayment,
        fulfillment_lines: [refundLine],
        amount: refundAmount,
        reason: refundReason
      }),
    onSuccess: () => {
      setRefundAmount('')
      setRefundReason('')
      refreshCommerce()
    }
  })
  const reverse = useMutation({
    mutationFn: ({ kind, pk }: { kind: 'fulfillments' | 'payments' | 'returns' | 'refunds'; pk: number }) => reverseCommerce(order.pk, kind, pk, 'Reversed by operator.'),
    onSuccess: refreshCommerce
  })

  const audit = [
    ...(fulfillments.data ?? []).map((entry) => ({
      kind: 'fulfillments' as const,
      pk: entry.pk,
      at: entry.fulfilled_at,
      label: `${entry.fulfillment_number} · ${entry.lines.length} dispatched`,
      status: entry.status
    })),
    ...(payments.data ?? []).map((entry) => ({
      kind: 'payments' as const,
      pk: entry.pk,
      at: entry.paid_on,
      label: `Payment ${formatMoney(entry.amount, entry.currency_code)} · ${entry.method.replaceAll('_', ' ')}`,
      status: entry.status
    })),
    ...(returns.data ?? []).map((entry) => ({
      kind: 'returns' as const,
      pk: entry.pk,
      at: entry.returned_at,
      label: `Return · ${entry.lines.map((line) => line.outcome).join(', ')}`,
      status: entry.status
    })),
    ...(refunds.data ?? []).map((entry) => ({
      kind: 'refunds' as const,
      pk: entry.pk,
      at: entry.refunded_at,
      label: `Refund ${formatMoney(entry.amount, entry.currency_code)}`,
      status: entry.status
    }))
  ].sort((left, right) => left.at.localeCompare(right.at))

  return (
    <>
      <Card body className="mb-3">
        <Card.Title>Commerce status</Card.Title>
        <Row>
          <Col>
            Dispatched
            <br />
            <strong>{order.commerce.fulfilled_quantity}</strong>
          </Col>
          <Col>
            Returned
            <br />
            <strong>{order.commerce.returned_quantity}</strong>
          </Col>
          <Col>
            Paid
            <br />
            <strong>{formatMoney(order.commerce.net_paid_total, order.currency_code)}</strong>
          </Col>
          <Col>
            Outstanding
            <br />
            <strong>{formatMoney(order.commerce.outstanding_total, order.currency_code)}</strong>
          </Col>
          <Col>
            Status
            <br />
            <Badge bg={order.commerce.payment_status === 'paid' ? 'success' : 'secondary'}>{order.commerce.payment_status.replaceAll('_', ' ')}</Badge>
          </Col>
        </Row>
      </Card>
      {reserved.length > 0 && (
        <Card body className="mb-3">
          <Card.Title>Post fulfillment</Card.Title>
          {reserved.map((allocation) => (
            <Form.Check
              key={allocation.pk}
              label={allocation.plant ? `Plant #${allocation.plant}` : (allocation.asset_code ?? `Unit #${allocation.inventory_unit}`)}
              checked={selectedAllocations.includes(allocation.pk)}
              onChange={() => setSelectedAllocations((current) => (current.includes(allocation.pk) ? current.filter((pk) => pk !== allocation.pk) : [...current, allocation.pk]))}
            />
          ))}
          <Row className="g-2 align-items-end mt-1">
            <Col md={3}>
              <Form.Label>Optional packaging item</Form.Label>
              <Form.Select
                value={packagingItem}
                onChange={(event) => {
                  setPackagingItem(event.target.value === '' ? '' : Number(event.target.value))
                  setPackagingBalance('')
                }}
              >
                <option value="">None</option>
                {(packagingItems.data ?? []).map((item) => (
                  <option key={item.pk} value={item.pk}>
                    {item.name}
                  </option>
                ))}
              </Form.Select>
            </Col>
            <Col md={4}>
              <Form.Label>Exact lot and location</Form.Label>
              <Form.Select disabled={packagingItem === ''} value={packagingBalance} onChange={(event) => setPackagingBalance(event.target.value)}>
                <option value="">Select balance</option>
                {(packagingBalances.data ?? [])
                  .filter((balance) => Number(balance.available_quantity) > 0)
                  .map((balance) => (
                    <option key={`${balance.lot}:${balance.location}`} value={`${balance.lot}:${balance.location}`}>
                      {balance.lot_identifier} · {balance.location_name} · {balance.available_quantity} {balance.base_unit}
                    </option>
                  ))}
              </Form.Select>
            </Col>
            <Col md={2}>
              <Form.Label>Quantity</Form.Label>
              <Form.Control type="number" min="0" step="0.000000001" value={packagingQuantity} onChange={(event) => setPackagingQuantity(event.target.value)} />
            </Col>
            <Col md={3}>
              <Button disabled={!selectedAllocations.length || fulfill.isPending} onClick={() => fulfill.mutate()}>
                Post dispatch
              </Button>
            </Col>
          </Row>
        </Card>
      )}
      <Row className="g-3 mb-3">
        <Col lg={4}>
          <Card body className="h-100">
            <Card.Title>Record payment</Card.Title>
            <Form.Control
              className="mb-2"
              type="number"
              min="0.0001"
              step="0.0001"
              placeholder="Amount"
              value={paymentAmount}
              onChange={(event) => setPaymentAmount(event.target.value)}
            />
            <Form.Select className="mb-2" value={paymentMethod} onChange={(event) => setPaymentMethod(event.target.value as typeof paymentMethod)}>
              <option value="cash">Cash</option>
              <option value="card">Card</option>
              <option value="bank_transfer">Bank transfer</option>
              <option value="other">Other</option>
            </Form.Select>
            <Button disabled={!paymentAmount || pay.isPending} onClick={() => pay.mutate()}>
              Record payment
            </Button>
          </Card>
        </Col>
        <Col lg={4}>
          <Card body className="h-100">
            <Card.Title>Return exact item</Card.Title>
            <Form.Select className="mb-2" value={returnLine} onChange={(event) => setReturnLine(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Fulfillment item</option>
              {activeFulfillmentLines.map((line) => (
                <option key={line.pk} value={line.pk}>
                  Allocation #{line.allocation} · {formatMoney(line.total_incl_tax, line.currency_code)}
                </option>
              ))}
            </Form.Select>
            <Form.Select className="mb-2" value={returnOutcome} onChange={(event) => setReturnOutcome(event.target.value as typeof returnOutcome)}>
              <option value="available">Available</option>
              <option value="quarantined">Quarantined</option>
              <option value="discarded">Discarded</option>
            </Form.Select>
            {returnOutcome !== 'discarded' && (
              <Form.Select className="mb-2" value={returnDestination} onChange={(event) => setReturnDestination(event.target.value === '' ? '' : Number(event.target.value))}>
                <option value="">Destination</option>
                {(locations.data ?? [])
                  .filter((location) => returnOutcome !== 'quarantined' || location.location_type === 'quarantine')
                  .map((location) => (
                    <option key={location.pk} value={location.pk}>
                      {location.full_name}
                    </option>
                  ))}
              </Form.Select>
            )}
            {returnOutcome === 'quarantined' && (
              <>
                <Form.Select className="mb-2" value={healthType} onChange={(event) => setHealthType(event.target.value === '' ? '' : Number(event.target.value))}>
                  <option value="">Observation type</option>
                  {(healthTypes.data ?? [])
                    .filter((entry) => entry.active)
                    .map((entry) => (
                      <option key={entry.pk} value={entry.pk}>
                        {entry.name}
                      </option>
                    ))}
                </Form.Select>
                <Form.Select className="mb-2" value={healthSeverity} onChange={(event) => setHealthSeverity(event.target.value as typeof healthSeverity)}>
                  <option value="low">Low</option>
                  <option value="moderate">Moderate</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </Form.Select>
              </>
            )}
            <Form.Control className="mb-2" placeholder="Return reason" value={returnReason} onChange={(event) => setReturnReason(event.target.value)} />
            <Button
              disabled={
                returnLine === '' || !returnReason.trim() || (returnOutcome !== 'discarded' && returnDestination === '') || (returnOutcome === 'quarantined' && healthType === '')
              }
              onClick={() => returnMutation.mutate()}
            >
              Post return
            </Button>
          </Card>
        </Col>
        <Col lg={4}>
          <Card body className="h-100">
            <Card.Title>Refund paid value</Card.Title>
            <Form.Select className="mb-2" value={refundPayment} onChange={(event) => setRefundPayment(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Payment</option>
              {activePayments.map((payment) => (
                <option key={payment.pk} value={payment.pk}>
                  {formatMoney(payment.amount, payment.currency_code)} · {payment.paid_on}
                </option>
              ))}
            </Form.Select>
            <Form.Select className="mb-2" value={refundLine} onChange={(event) => setRefundLine(event.target.value === '' ? '' : Number(event.target.value))}>
              <option value="">Fulfillment item</option>
              {activeFulfillmentLines.map((line) => (
                <option key={line.pk} value={line.pk}>
                  Allocation #{line.allocation}
                </option>
              ))}
            </Form.Select>
            <Form.Control
              className="mb-2"
              type="number"
              min="0.0001"
              step="0.0001"
              placeholder="Amount"
              value={refundAmount}
              onChange={(event) => setRefundAmount(event.target.value)}
            />
            <Form.Control className="mb-2" placeholder="Refund reason" value={refundReason} onChange={(event) => setRefundReason(event.target.value)} />
            <Button disabled={refundPayment === '' || refundLine === '' || !refundAmount || !refundReason.trim()} onClick={() => refund.mutate()}>
              Post refund
            </Button>
          </Card>
        </Col>
      </Row>
      <Card body className="mb-3">
        <Card.Title>Chronological audit history</Card.Title>
        {audit.length === 0 && <p className="text-muted mb-0">No posted commerce yet.</p>}
        {audit.map((entry) => (
          <div className="d-flex justify-content-between border-bottom py-2" key={`${entry.kind}:${entry.pk}`}>
            <span>
              {formatDate(entry.at)} · {entry.label} · {entry.status}
            </span>
            {entry.status === 'posted' && (
              <Button size="sm" variant="outline-danger" onClick={() => reverse.mutate({ kind: entry.kind, pk: entry.pk })}>
                Reverse
              </Button>
            )}
          </div>
        ))}
      </Card>
    </>
  )
}

function SalesOrderDetailView({ orderPk, workspace }: { orderPk: number; workspace: Workspace }) {
  const client = useQueryClient()
  const orderQuery = useQuery({ queryKey: queryKeys.sales.order(orderPk), queryFn: ({ signal }) => getSalesOrder(orderPk, signal) })
  const customers = useQuery({ queryKey: queryKeys.sales.customers, queryFn: ({ signal }) => getCustomers(signal) })
  const order = orderQuery.data
  const mutateOrder = useMutation({
    mutationFn: (data: object) => updateSalesOrder(orderPk, data),
    onSuccess: (updated) => client.setQueryData(queryKeys.sales.order(orderPk), updated)
  })
  const action = useMutation({
    mutationFn: ({ name, data = {} }: { name: 'to-draft' | 'confirm' | 'cancel'; data?: object }) => orderAction(orderPk, name, data),
    onSuccess: () => invalidateSales(orderPk)
  })
  if (!order) return <main className="container py-3">Loading order…</main>
  const editable = editableOrder(order.status)
  return (
    <main className="container py-3">
      <div className="d-flex justify-content-between align-items-center">
        <h1>{order.order_number}</h1>
        <Badge bg={order.status === 'confirmed' ? 'success' : order.status === 'cancelled' ? 'secondary' : 'primary'}>{order.status.replaceAll('_', ' ')}</Badge>
      </div>
      <Card body className="mb-3">
        <Row className="g-2">
          <Col md={4}>
            <Form.Label>Customer</Form.Label>
            <Form.Select
              disabled={!editable}
              value={order.customer ?? ''}
              onChange={(event) => mutateOrder.mutate({ customer: event.target.value === '' ? null : Number(event.target.value) })}
            >
              <option value="">Walk-in / no customer</option>
              {(customers.data ?? [])
                .filter((entry) => entry.active || entry.pk === order.customer)
                .map((entry) => (
                  <option key={entry.pk} value={entry.pk}>
                    {entry.name}
                  </option>
                ))}
            </Form.Select>
          </Col>
          <Col md={3}>
            <Form.Label>Requested date</Form.Label>
            <Form.Control
              disabled={!editable}
              type="date"
              value={order.requested_date ?? ''}
              onChange={(event) => mutateOrder.mutate({ requested_date: event.target.value || null })}
            />
          </Col>
          <Col md={5} className="d-flex align-items-end">
            <Form.Check
              disabled={!editable}
              label="Entered prices and fixed discounts include tax / GST"
              checked={order.prices_include_tax}
              onChange={(event) => mutateOrder.mutate({ prices_include_tax: event.target.checked })}
            />
          </Col>
        </Row>
        {editable && order.lines.length > 0 && (
          <Alert variant="warning" className="mt-2 mb-0">
            Changing the tax-entry mode reinterprets existing entered numbers; it does not convert them.
          </Alert>
        )}
      </Card>
      <OrderTotals order={order} />
      {['confirmed', 'partially_fulfilled', 'fulfilled'].includes(order.status) && <CommercePanel order={order} />}
      {['confirmed', 'partially_fulfilled', 'fulfilled'].includes(order.status) && <SupplyDocumentPanel order={order} />}
      {editable && <LineForm order={order} workspace={workspace} />}
      {order.lines.map((line) => (
        <Card body className="mb-3" key={line.pk}>
          <div className="d-flex justify-content-between">
            <strong>{line.description}</strong>
            <span>
              {line.quantity} × {formatMoney(line.unit_price, order.currency_code)} ({line.prices_include_tax ? 'incl' : 'excl'} tax)
            </span>
          </div>
          <div className="text-muted">
            Discount {line.discount_type === 'percentage' ? `${line.discount_value}%` : formatMoney(line.discount_value, order.currency_code)} · Tax {line.tax_rate}% (
            {TAX_TREATMENT_LABELS[line.tax_treatment]}) · Total {formatMoney(line.total_incl_tax, order.currency_code)}
          </div>
          <AllocationPanel order={order} line={line} />
        </Card>
      ))}
      <div className="d-flex gap-2">
        {order.status === 'quote' && <Button onClick={() => action.mutate({ name: 'to-draft' })}>Accept as draft</Button>}
        {order.status === 'draft' && (
          <Button variant="success" disabled={order.lines.length === 0 || !order.margin.allocation_complete} onClick={() => action.mutate({ name: 'confirm' })}>
            Confirm and reserve
          </Button>
        )}
        {!['cancelled', 'fulfilled'].includes(order.status) && (
          <Button variant="outline-danger" onClick={() => action.mutate({ name: 'cancel', data: { reason: 'Cancelled by operator.' } })}>
            Cancel order
          </Button>
        )}
      </div>
    </main>
  )
}

export { CustomerListView, SalesOrderDetailView, SalesOrderListView }
