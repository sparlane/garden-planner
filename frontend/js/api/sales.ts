import { csrfPatch, csrfPost, fetchAsJson } from '../utils'
import {
  AllocationPreview,
  CohortDraw,
  Customer,
  LotDraw,
  Fulfillment,
  SalesAllocation,
  SalesOrder,
  SalesOrderLine,
  SalesOrderLineWrite,
  SalesOrderStatus,
  SalesPayment,
  SalesRefund,
  SalesReturn,
  SalesShortfall,
  SalesShortfallWrite
} from '../types/sales'

const CUSTOMERS_URL = '/sales/customers/'
const ORDERS_URL = '/sales/orders/'
const LINES_URL = '/sales/order-lines/'

function getCustomers(signal?: AbortSignal): Promise<Array<Customer>> {
  return fetchAsJson<Array<Customer>>(CUSTOMERS_URL, signal)
}

async function createCustomer(data: Partial<Customer>): Promise<Customer> {
  const response = await csrfPost(CUSTOMERS_URL, data)
  return response.json() as Promise<Customer>
}

async function updateCustomer(pk: number, data: Partial<Customer>): Promise<Customer> {
  const response = await csrfPatch(`${CUSTOMERS_URL}${pk}/`, data)
  return response.json() as Promise<Customer>
}

function getSalesOrders(signal?: AbortSignal): Promise<Array<SalesOrder>> {
  return fetchAsJson<Array<SalesOrder>>(ORDERS_URL, signal)
}

function getSalesOrder(pk: number, signal?: AbortSignal): Promise<SalesOrder> {
  return fetchAsJson<SalesOrder>(`${ORDERS_URL}${pk}/`, signal)
}

async function createSalesOrder(data: { status: 'quote' | 'draft'; customer?: number | null; requested_date?: string | null; notes?: string }): Promise<SalesOrder> {
  const response = await csrfPost(ORDERS_URL, data)
  return response.json() as Promise<SalesOrder>
}

async function updateSalesOrder(pk: number, data: Partial<Pick<SalesOrder, 'customer' | 'requested_date' | 'notes' | 'prices_include_tax'>>): Promise<SalesOrder> {
  const response = await csrfPatch(`${ORDERS_URL}${pk}/`, data)
  return response.json() as Promise<SalesOrder>
}

async function createSalesOrderLine(data: SalesOrderLineWrite): Promise<SalesOrderLine> {
  const response = await csrfPost(LINES_URL, data)
  return response.json() as Promise<SalesOrderLine>
}

async function orderAction(pk: number, action: 'to-draft' | 'confirm' | 'cancel', data: object = {}): Promise<SalesOrder> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/${action}/`, data)
  return response.json() as Promise<SalesOrder>
}

async function previewAllocation(pk: number, data: object): Promise<AllocationPreview> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/allocation-preview/`, data)
  return response.json() as Promise<AllocationPreview>
}

// The four selections are separate rather than one bag, because the server
// refuses a plant offered to a unit line before it takes any lock, and losing
// that would turn a clear rejection into a mysterious one.
async function allocateOrderLine(
  pk: number,
  line: number,
  selection: { plantIds?: Array<number>; unitIds?: Array<number>; lotDraws?: Array<LotDraw>; cohortDraws?: Array<CohortDraw> },
  expiresAt: string | null
): Promise<Array<SalesAllocation>> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/allocate/`, {
    line,
    plant_ids: selection.plantIds ?? [],
    unit_ids: selection.unitIds ?? [],
    lot_requests: selection.lotDraws ?? [],
    cohort_requests: selection.cohortDraws ?? [],
    expires_at: expiresAt
  })
  return response.json() as Promise<Array<SalesAllocation>>
}

// A shortfall is not a release: it says the nursery could not supply what it
// sold, so the reason is mandatory and the commitment is closed short rather
// than handed back by choice.
async function postShortfall(pk: number, data: SalesShortfallWrite): Promise<SalesShortfall> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/shortfall/`, data)
  return response.json() as Promise<SalesShortfall>
}

async function closeAllocations(pk: number, action: 'release' | 'expire', allocations: Array<number>, reason: string): Promise<Array<SalesAllocation>> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/${action}/`, { allocations, reason })
  return response.json() as Promise<Array<SalesAllocation>>
}

function getAvailableSerializedUnits(item: number, signal?: AbortSignal): Promise<Array<{ pk: number; item: number; asset_code: string }>> {
  return fetchAsJson(`/inventory/serialized-units/?item=${item}&physical_state=available`, signal)
}

function editableOrder(status: SalesOrderStatus): boolean {
  return status === 'quote' || status === 'draft'
}

function getFulfillments(order: number, signal?: AbortSignal): Promise<Array<Fulfillment>> {
  return fetchAsJson(`${ORDERS_URL}${order}/fulfillments/`, signal)
}

async function postFulfillment(order: number, data: object): Promise<Fulfillment> {
  const response = await csrfPost(`${ORDERS_URL}${order}/fulfillments/`, data)
  return response.json() as Promise<Fulfillment>
}

function getPayments(order: number, signal?: AbortSignal): Promise<Array<SalesPayment>> {
  return fetchAsJson(`${ORDERS_URL}${order}/payments/`, signal)
}

async function postPayment(order: number, data: object): Promise<SalesPayment> {
  const response = await csrfPost(`${ORDERS_URL}${order}/payments/`, data)
  return response.json() as Promise<SalesPayment>
}

function getReturns(order: number, signal?: AbortSignal): Promise<Array<SalesReturn>> {
  return fetchAsJson(`${ORDERS_URL}${order}/returns/`, signal)
}

async function postReturn(order: number, data: object): Promise<SalesReturn> {
  const response = await csrfPost(`${ORDERS_URL}${order}/returns/`, data)
  return response.json() as Promise<SalesReturn>
}

function getRefunds(order: number, signal?: AbortSignal): Promise<Array<SalesRefund>> {
  return fetchAsJson(`${ORDERS_URL}${order}/refunds/`, signal)
}

async function postRefund(order: number, data: object): Promise<SalesRefund> {
  const response = await csrfPost(`${ORDERS_URL}${order}/refunds/`, data)
  return response.json() as Promise<SalesRefund>
}

async function reverseCommerce(order: number, kind: 'fulfillments' | 'payments' | 'returns' | 'refunds', record: number, reason: string): Promise<object> {
  const response = await csrfPost(`${ORDERS_URL}${order}/${kind}/${record}/reverse/`, {
    operation_key: crypto.randomUUID(),
    reason
  })
  return response.json() as Promise<object>
}

export {
  allocateOrderLine,
  closeAllocations,
  createCustomer,
  createSalesOrder,
  createSalesOrderLine,
  editableOrder,
  getFulfillments,
  getAvailableSerializedUnits,
  getCustomers,
  getSalesOrder,
  getSalesOrders,
  getPayments,
  getRefunds,
  getReturns,
  orderAction,
  previewAllocation,
  postFulfillment,
  postPayment,
  postRefund,
  postReturn,
  postShortfall,
  reverseCommerce,
  updateCustomer,
  updateSalesOrder
}
