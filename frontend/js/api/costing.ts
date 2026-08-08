import { fetchAsJson, csrfPost } from '../utils'
import { BatchCostBreakdown, CostLayer, PlantCostBreakdown, RecalculateCostsResponse } from '../types/costing'

const ROOT = '/costing/'

function getBatchCostBreakdown(batchPk: number, signal?: AbortSignal): Promise<BatchCostBreakdown> {
  return fetchAsJson<BatchCostBreakdown>(`${ROOT}batches/${batchPk}/`, signal)
}

function getPlantCostBreakdown(plantPk: number, signal?: AbortSignal): Promise<PlantCostBreakdown> {
  return fetchAsJson<PlantCostBreakdown>(`${ROOT}plants/${plantPk}/`, signal)
}

// The full history, reversals included. `effective=true` narrows it to the
// layers that still count; without the flag the reversed ones come back too,
// because they are how a screen explains why a figure changed.
function getCostAllocations(batchPk: number, effective = false, signal?: AbortSignal): Promise<Array<CostLayer>> {
  const query = effective ? '&effective=true' : ''
  return fetchAsJson<Array<CostLayer>>(`${ROOT}allocations/?batch=${batchPk}${query}`, signal)
}

// Reposts the batch's allocations from whatever the source facts now say. Never
// edits an amount: a layer that no longer matches is reversed and its
// replacement posted beside it, so `run` comes back null when nothing had to
// change.
async function recalculateBatchCosts(batchPk: number, reason: string): Promise<RecalculateCostsResponse> {
  const response = await csrfPost(`${ROOT}batches/${batchPk}/recalculate/`, { reason })
  return response.json() as Promise<RecalculateCostsResponse>
}

export { getBatchCostBreakdown, getCostAllocations, getPlantCostBreakdown, recalculateBatchCosts }
