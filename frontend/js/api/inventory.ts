import { InventoryItem, InventoryItemCreate, InventoryItemFilters, InventoryLocation, InventoryUnit, ItemUnitConversion, ItemUnitConversionCreate } from '../types/inventory'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'

const ITEMS_URL = '/inventory/items/'
const CONVERSIONS_URL = '/inventory/conversions/'

function getInventoryUnits(signal?: AbortSignal): Promise<Array<InventoryUnit>> {
  return fetchAsJson<Array<InventoryUnit>>('/inventory/units/', signal)
}

function getInventoryLocations(signal?: AbortSignal): Promise<Array<InventoryLocation>> {
  return fetchAsJson<Array<InventoryLocation>>('/inventory/locations/?active=true', signal)
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

export {
  createInventoryItem,
  createItemUnitConversion,
  getInventoryItems,
  getInventoryLocations,
  getInventoryUnits,
  getItemUnitConversions,
  setInventoryItemActive,
  setItemUnitConversionActive
}
