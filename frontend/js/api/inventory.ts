import {
  InventoryBalance,
  InventoryItem,
  InventoryItemCreate,
  InventoryItemFilters,
  InventoryUnit,
  ItemUnitConversion,
  ItemUnitConversionCreate,
  StockReceipt,
  StockReceiptFilters,
  StockReceiptWrite
} from '../types/inventory'
import { csrfDelete, csrfPatch, csrfPost, fetchAsJson } from '../utils'

const ITEMS_URL = '/inventory/items/'
const CONVERSIONS_URL = '/inventory/conversions/'
const RECEIPTS_URL = '/inventory/receipts/'

function getInventoryBalances(item: number, signal?: AbortSignal): Promise<Array<InventoryBalance>> {
  return fetchAsJson<Array<InventoryBalance>>(`/inventory/balances/?item=${item}`, signal)
}

function getInventoryUnits(signal?: AbortSignal): Promise<Array<InventoryUnit>> {
  return fetchAsJson<Array<InventoryUnit>>('/inventory/units/', signal)
}

function getInventoryItems(filters: InventoryItemFilters, signal?: AbortSignal): Promise<Array<InventoryItem>> {
  const params = new URLSearchParams()
  if (filters.search) params.set('search', filters.search)
  if (filters.category) params.set('category', filters.category)
  if (filters.tracking_mode) params.set('tracking_mode', filters.tracking_mode)
  if (filters.active !== undefined) params.set('active', String(filters.active))
  const query = params.size ? `?${params.toString()}` : ''
  return fetchAsJson<Array<InventoryItem>>(`${ITEMS_URL}${query}`, signal)
}

async function createInventoryItem(item: InventoryItemCreate): Promise<InventoryItem> {
  const response = await csrfPost(ITEMS_URL, item)
  return response.json() as Promise<InventoryItem>
}

async function setInventoryItemActive(pk: number, active: boolean): Promise<InventoryItem> {
  const response = await csrfPatch(`${ITEMS_URL}${pk}/`, { active })
  return response.json() as Promise<InventoryItem>
}

function getItemUnitConversions(item: number, signal?: AbortSignal): Promise<Array<ItemUnitConversion>> {
  const params = new URLSearchParams({ item: String(item) })
  return fetchAsJson<Array<ItemUnitConversion>>(`${CONVERSIONS_URL}?${params.toString()}`, signal)
}

async function createItemUnitConversion(conversion: ItemUnitConversionCreate): Promise<ItemUnitConversion> {
  const response = await csrfPost(CONVERSIONS_URL, conversion)
  return response.json() as Promise<ItemUnitConversion>
}

async function setItemUnitConversionActive(pk: number, active: boolean): Promise<ItemUnitConversion> {
  const response = await csrfPatch(`${CONVERSIONS_URL}${pk}/`, { active })
  return response.json() as Promise<ItemUnitConversion>
}

function getStockReceipts(filters: StockReceiptFilters, signal?: AbortSignal): Promise<Array<StockReceipt>> {
  const params = new URLSearchParams()
  if (filters.status) params.set('status', filters.status)
  if (filters.seed_packet !== undefined) params.set('seed_packet', String(filters.seed_packet))
  const query = params.size ? `?${params.toString()}` : ''
  return fetchAsJson<Array<StockReceipt>>(`${RECEIPTS_URL}${query}`, signal)
}

async function createStockReceipt(receipt: StockReceiptWrite): Promise<StockReceipt> {
  const response = await csrfPost(RECEIPTS_URL, receipt)
  return response.json() as Promise<StockReceipt>
}

async function updateStockReceipt(pk: number, receipt: StockReceiptWrite): Promise<StockReceipt> {
  const response = await csrfPatch(`${RECEIPTS_URL}${pk}/`, receipt)
  return response.json() as Promise<StockReceipt>
}

// The action reads no body and there is no revision or availability digest to
// echo back, unlike postInputApplication: posting a receipt only adds stock, so
// no third party's view of what is on hand can have gone stale underneath it.
async function postStockReceipt(pk: number): Promise<StockReceipt> {
  const response = await csrfPost(`${RECEIPTS_URL}${pk}/post/`, {})
  return response.json() as Promise<StockReceipt>
}

async function reverseStockReceipt(pk: number, reason: string): Promise<StockReceipt> {
  const response = await csrfPost(`${RECEIPTS_URL}${pk}/reverse/`, { reason })
  return response.json() as Promise<StockReceipt>
}

function deleteStockReceipt(pk: number): Promise<Response> {
  return csrfDelete(`${RECEIPTS_URL}${pk}/`)
}

export {
  createInventoryItem,
  createItemUnitConversion,
  createStockReceipt,
  deleteStockReceipt,
  getInventoryBalances,
  getInventoryItems,
  getInventoryUnits,
  getItemUnitConversions,
  getStockReceipts,
  postStockReceipt,
  reverseStockReceipt,
  setInventoryItemActive,
  setItemUnitConversionActive,
  updateStockReceipt
}
