import { csrfPatch, csrfPost, fetchAsJson } from '../utils'
import { AllocationPreview, Customer, SalesAllocation, SalesOrder, SalesOrderLine, SalesOrderLineWrite, SalesOrderStatus } from '../types/sales'

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

async function allocateOrderLine(pk: number, line: number, plantIds: Array<number>, unitIds: Array<number>, expiresAt: string | null): Promise<Array<SalesAllocation>> {
  const response = await csrfPost(`${ORDERS_URL}${pk}/allocate/`, { line, plant_ids: plantIds, unit_ids: unitIds, expires_at: expiresAt })
  return response.json() as Promise<Array<SalesAllocation>>
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

export {
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
}
