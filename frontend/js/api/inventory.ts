import {
  IndividualizationWrite,
  InventoryBalance,
  InventoryItem,
  InventoryItemCreate,
  InventoryItemFilters,
  InventoryUnit,
  InputTaxAdjustment,
  InputTaxAdjustmentWrite,
  ItemUnitConversion,
  ItemUnitConversionCreate,
  SerializedInventoryUnit,
  StockReceipt,
  StockReceiptFilters,
  StockReceiptWrite,
  Stocktake,
  StocktakeScope
} from '../types/inventory'
import { csrfDelete, csrfPatch, csrfPost, fetchAsJson } from '../utils'

const ITEMS_URL = '/inventory/items/'
const CONVERSIONS_URL = '/inventory/conversions/'
const RECEIPTS_URL = '/inventory/receipts/'
const INPUT_TAX_ADJUSTMENTS_URL = '/inventory/input-tax-adjustments/'
const STOCKTAKES_URL = '/inventory/stocktakes/'
const LOTS_URL = '/inventory/lots/'

function getStocktakes(signal?: AbortSignal): Promise<Array<Stocktake>> {
  return fetchAsJson<Array<Stocktake>>(STOCKTAKES_URL, signal)
}

function getStocktake(pk: number, signal?: AbortSignal): Promise<Stocktake> {
  return fetchAsJson<Stocktake>(`${STOCKTAKES_URL}${pk}/`, signal)
}

async function createStocktake(scope: StocktakeScope, notes: string): Promise<Stocktake> {
  const response = await csrfPost(STOCKTAKES_URL, { scope, notes, blind: true })
  return response.json() as Promise<Stocktake>
}

async function stocktakeAction(pk: number, action: string, data: object = {}): Promise<Stocktake> {
  const response = await csrfPost(`${STOCKTAKES_URL}${pk}/${action}/`, data)
  return response.json() as Promise<Stocktake>
}

async function countStocktakeTarget(pk: number, target: number, countedQuantity: string, notes: string): Promise<void> {
  await csrfPost(`${STOCKTAKES_URL}${pk}/count/`, { target, counted_quantity: countedQuantity, notes })
}

async function scanStocktakeTarget(pk: number, code: string): Promise<void> {
  await csrfPost(`${STOCKTAKES_URL}${pk}/scan-count/`, { code })
}

async function resolveStocktakeVariance(pk: number, variance: number, action: string, reason: string, acceptConflict: boolean, payload: object = {}): Promise<void> {
  await csrfPost(`${STOCKTAKES_URL}${pk}/resolve-variance/`, { variance, action, reason, accept_conflict: acceptConflict, payload })
}

function getInventoryBalances(item: number, signal?: AbortSignal): Promise<Array<InventoryBalance>> {
  return fetchAsJson<Array<InventoryBalance>>(`/inventory/balances/?item=${item}`, signal)
}

// Numbering posts no stock movement, so nothing about the lot's own history
// changes: what moves is how much of it is still anonymous, which the balance
// rows carry. Returns the new units so a caller can show their asset codes.
async function individualizeLotUnits(lot: number, values: IndividualizationWrite): Promise<Array<SerializedInventoryUnit>> {
  const response = await csrfPost(`${LOTS_URL}${lot}/individualize/`, values)
  return response.json() as Promise<Array<SerializedInventoryUnit>>
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

function getInputTaxAdjustments(receipt: number, signal?: AbortSignal): Promise<Array<InputTaxAdjustment>> {
  return fetchAsJson<Array<InputTaxAdjustment>>(`${INPUT_TAX_ADJUSTMENTS_URL}?receipt=${receipt}`, signal)
}

async function createInputTaxAdjustment(adjustment: InputTaxAdjustmentWrite): Promise<InputTaxAdjustment> {
  const response = await csrfPost(INPUT_TAX_ADJUSTMENTS_URL, adjustment)
  return response.json() as Promise<InputTaxAdjustment>
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

// Null clears a settlement recorded against the wrong receipt, so it is sent
// rather than omitted: the server tells the two apart and a missing key is an
// error there.
async function settleStockReceipt(pk: number, settledOn: string | null): Promise<StockReceipt> {
  const response = await csrfPost(`${RECEIPTS_URL}${pk}/settle/`, { settled_on: settledOn })
  return response.json() as Promise<StockReceipt>
}

function deleteStockReceipt(pk: number): Promise<Response> {
  return csrfDelete(`${RECEIPTS_URL}${pk}/`)
}

export {
  createInventoryItem,
  createInputTaxAdjustment,
  createItemUnitConversion,
  createStockReceipt,
  deleteStockReceipt,
  getInventoryBalances,
  getInventoryItems,
  individualizeLotUnits,
  getInventoryUnits,
  getInputTaxAdjustments,
  getItemUnitConversions,
  getStockReceipts,
  getStocktake,
  getStocktakes,
  postStockReceipt,
  reverseStockReceipt,
  settleStockReceipt,
  createStocktake,
  countStocktakeTarget,
  resolveStocktakeVariance,
  scanStocktakeTarget,
  stocktakeAction,
  setInventoryItemActive,
  setItemUnitConversionActive,
  updateStockReceipt
}
