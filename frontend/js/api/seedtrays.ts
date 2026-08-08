import { SerializedStockMovement } from '../types/inventory'
import {
  CleanGenerationRequest,
  CleanGenerationResponse,
  GenerationCostBreakdown,
  SeedTray,
  SeedTrayCell,
  SeedTrayFilters,
  SeedTrayGeneration,
  SeedTrayGenerationContents,
  SeedTrayModel,
  SeedTrayModelCreate,
  SeedTrayReceiptCreate,
  SeedTrayReceiptResponse
} from '../types/seedtrays'
import { csrfPost, fetchAsJson } from '../utils'

function getSeedTrayModels(signal?: AbortSignal): Promise<Array<SeedTrayModel>> {
  return fetchAsJson<Array<SeedTrayModel>>('/seedtrays/seedtraymodels/', signal)
}

function addSeedTrayModel(model: SeedTrayModelCreate) {
  return csrfPost('/seedtrays/seedtraymodels/', model)
}

function getSeedTrays(signal?: AbortSignal, filters: SeedTrayFilters = {}): Promise<Array<SeedTray>> {
  const params = new URLSearchParams()
  if (filters.model) params.set('model', String(filters.model))
  if (filters.location) params.set('location', String(filters.location))
  if (filters.physical_state) params.set('physical_state', filters.physical_state)
  if (filters.in_use !== undefined) params.set('in_use', String(filters.in_use))
  const query = params.size ? `?${params.toString()}` : ''
  return fetchAsJson<Array<SeedTray>>(`/seedtrays/seedtrays/${query}`, signal)
}

async function receiveSeedTrays(model: number, receipt: SeedTrayReceiptCreate): Promise<SeedTrayReceiptResponse> {
  const response = await csrfPost(`/seedtrays/seedtraymodels/${model}/receive/`, receipt)
  return response.json() as Promise<SeedTrayReceiptResponse>
}

function getSeedTrayCells(trayPk: number, signal?: AbortSignal): Promise<Array<SeedTrayCell>> {
  return fetchAsJson<Array<SeedTrayCell>>(`/seedtrays/seedtrays/${trayPk}/cells/`, signal)
}

function getSerializedUnitMovements(unitPk: number, signal?: AbortSignal): Promise<Array<SerializedStockMovement>> {
  return fetchAsJson<Array<SerializedStockMovement>>(`/inventory/movements/?unit=${unitPk}`, signal)
}

async function postSerializedUnitAction(unitPk: number, action: 'transfer' | 'loss' | 'retire' | 'return' | 'reconcile-opening', data: object): Promise<void> {
  await csrfPost(`/inventory/serialized-units/${unitPk}/${action}/`, data)
}

function getSeedTrayGenerations(trayPk: number, signal?: AbortSignal): Promise<Array<SeedTrayGeneration>> {
  return fetchAsJson<Array<SeedTrayGeneration>>(`/seedtrays/seedtraygenerations/?tray=${trayPk}`, signal)
}

async function openSeedTrayGeneration(trayPk: number, notes: string): Promise<SeedTrayGeneration> {
  const response = await csrfPost('/seedtrays/seedtraygenerations/', { tray: trayPk, notes })
  return response.json() as Promise<SeedTrayGeneration>
}

function getSeedTrayGenerationContents(generationPk: number, signal?: AbortSignal): Promise<SeedTrayGenerationContents> {
  return fetchAsJson<SeedTrayGenerationContents>(`/seedtrays/seedtraygenerations/${generationPk}/contents/`, signal)
}

async function cleanSeedTrayGeneration(generationPk: number, request: CleanGenerationRequest): Promise<CleanGenerationResponse> {
  const response = await csrfPost(`/seedtrays/seedtraygenerations/${generationPk}/close/`, request)
  return response.json() as Promise<CleanGenerationResponse>
}

async function reopenSeedTrayGeneration(generationPk: number, reason: string): Promise<SeedTrayGeneration> {
  const response = await csrfPost(`/seedtrays/seedtraygenerations/${generationPk}/reopen/`, { reason })
  return response.json() as Promise<SeedTrayGeneration>
}

async function reviewSeedTrayGeneration(generationPk: number, reason: string): Promise<SeedTrayGeneration> {
  const response = await csrfPost(`/seedtrays/seedtraygenerations/${generationPk}/review/`, { reason })
  return response.json() as Promise<SeedTrayGeneration>
}

function getSeedTrayGenerationCost(generationPk: number, signal?: AbortSignal): Promise<GenerationCostBreakdown> {
  return fetchAsJson<GenerationCostBreakdown>(`/seedtrays/seedtraygenerations/${generationPk}/cost-breakdown/`, signal)
}

export {
  addSeedTrayModel,
  cleanSeedTrayGeneration,
  getSeedTrayCells,
  getSeedTrayGenerationContents,
  getSeedTrayGenerationCost,
  getSeedTrayGenerations,
  getSeedTrayModels,
  getSeedTrays,
  getSerializedUnitMovements,
  openSeedTrayGeneration,
  postSerializedUnitAction,
  receiveSeedTrays,
  reopenSeedTrayGeneration,
  reviewSeedTrayGeneration
}
