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
  getSalesOrder,
  getSalesOrders,
  orderAction,
  previewAllocation,
  updateCustomer,
  updateSalesOrder
} from './api/sales'
import { getInventoryItems } from './api/inventory'
import { getPlantVarieties } from './api/plants'
import { queryClient, queryKeys } from './query'
import { AllocationPreview, Customer, SalesDiscountType, SalesLineType, SalesOrder, SalesOrderLine } from './types/sales'
import { Workspace } from './types/workspace'
import { formatDate, formatMoney, localDatetimeInputValue } from './utils'

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
            {allocation.expires_at && ` · expires ${formatDate(allocation.expires_at)}`}
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
            <Alert variant={preview.conflicts.length ? 'warning' : 'success'} className="mt-2">
              {preview.selected.length} eligible. {preview.conflicts.length} conflicts.
              {preview.conflicts.map((conflict) => (
                <div key={`${conflict.id}:${conflict.reason}`}>
                  #{conflict.id}: {conflict.reason.replaceAll('_', ' ')}
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
            Discount {line.discount_type === 'percentage' ? `${line.discount_value}%` : formatMoney(line.discount_value, order.currency_code)} · Tax {line.tax_rate}% · Total{' '}
            {formatMoney(line.total_incl_tax, order.currency_code)}
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
