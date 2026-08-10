import { fetchAsJson, csrfPatch, csrfPost } from '../utils'
import {
  BatchAction,
  BulkPlantOperation,
  BulkPlantOperationRequest,
  BulkPlantPreview,
  BulkPlantOutcome,
  Harvest,
  HarvestCreate,
  HarvestFilters,
  HarvestReportFilters,
  HarvestReportRow,
  ReverseHarvest,
  NurseryRegisterFilters,
  NurseryRegisterPage,
  NurseryRegisterSelection,
  PlantLifecycleEvent,
  PlantOutcome,
  PlantOutcomeAction,
  ReversePlantEvent,
  ProductionBatch,
  ProductionBatchCreate,
  ProductionBatchDetail,
  ProductionBatchStatus,
  ProductionBatchUpdate,
  GardenRowDirectPlanting,
  GardenSquareDirectPlanting,
  SeedTrayPlanting,
  GardenSquareTransplanting,
  GardenSquarePlanting,
  GardenRowDirectPlantingCreate,
  GardenSquareDirectPlantingCreate,
  SeedTrayPlantingCreate,
  SeedTrayPlantingDetails,
  SpecificPlant,
  SpecificPlantCreate,
  SpecificPlantDetail,
  SpecificPlantLocationCreate,
  SpecificPlantMove,
  SowingCorrection,
  CohortAction,
  CohortAvailability,
  CohortFilters,
  CohortMerge,
  CohortObservation,
  CohortPage,
  PlantCohort
} from '../types/plantings'

interface ProductionBatchFilters {
  status?: ProductionBatchStatus | ''
  variety?: number
  code?: string
  needsRepair?: boolean
}

function getProductionBatches(filters: ProductionBatchFilters = {}, signal?: AbortSignal): Promise<Array<ProductionBatch>> {
  const query = new URLSearchParams()
  if (filters.status) {
    query.set('status', filters.status)
  }
  if (filters.variety !== undefined) {
    query.set('variety', String(filters.variety))
  }
  if (filters.code) {
    query.set('code', filters.code)
  }
  if (filters.needsRepair) {
    query.set('needs_repair', 'true')
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  return fetchAsJson<Array<ProductionBatch>>(`/plantings/batches/${suffix}`, signal)
}

function getProductionBatch(batchPk: number, signal?: AbortSignal): Promise<ProductionBatchDetail> {
  return fetchAsJson<ProductionBatchDetail>(`/plantings/batches/${batchPk}/`, signal)
}

function addProductionBatch(data: ProductionBatchCreate): Promise<ProductionBatch> {
  return csrfPost('/plantings/batches/', data).then((response) => response.json() as Promise<ProductionBatch>)
}

function updateProductionBatch(batchPk: number, data: ProductionBatchUpdate): Promise<ProductionBatch> {
  return csrfPatch(`/plantings/batches/${batchPk}/`, data).then((response) => response.json() as Promise<ProductionBatch>)
}

function postProductionBatchAction(batchPk: number, batchAction: string, data: BatchAction = {}): Promise<ProductionBatchDetail> {
  return csrfPost(`/plantings/batches/${batchPk}/${batchAction}/`, data).then((response) => response.json() as Promise<ProductionBatchDetail>)
}

function getPlantingDirectSowGardenRows(signal?: AbortSignal): Promise<Array<GardenRowDirectPlanting>> {
  return fetchAsJson<Array<GardenRowDirectPlanting>>('/plantings/directsowgardenrow/', signal)
}

function addPlantingDirectSowGardenRow(data: GardenRowDirectPlantingCreate) {
  return csrfPost('/plantings/directsowgardenrow/', data)
}

function getPlantingDirectSowGardenSquares(signal?: AbortSignal): Promise<Array<GardenSquareDirectPlanting>> {
  return fetchAsJson<Array<GardenSquareDirectPlanting>>('/plantings/directsowgardensquare/', signal)
}

function addPlantingDirectSowGardenSquare(data: GardenSquareDirectPlantingCreate) {
  return csrfPost('/plantings/directsowgardensquare/', data)
}

function completePlantingDirectSowGardenSquare(plantingPk: number) {
  return csrfPost('/plantings/garden/squares/complete/', {
    planting: plantingPk
  })
}

function correctGardenSquareSowing(plantingPk: number, data: SowingCorrection) {
  return csrfPost(`/plantings/directsowgardensquare/${plantingPk}/correct-sowing/`, data)
}

function getPlantingSeedTrays(signal?: AbortSignal): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>('/plantings/seedtray/', signal)
}

function getPlantingSeedTray(seedTrayPk: number, signal?: AbortSignal): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>(`/plantings/seedtray-data/${seedTrayPk}/plantings/`, signal)
}

function addPlantingSeedTray(data: SeedTrayPlantingCreate) {
  return csrfPost('/plantings/seedtray/', data)
}

function completePlantingSeedTray(plantingPk: number) {
  return csrfPost('/plantings/seedtray/complete/', {
    planting: plantingPk
  })
}

function correctSeedTraySowing(plantingPk: number, data: SowingCorrection) {
  return csrfPost(`/plantings/seedtray/${plantingPk}/correct-sowing/`, data)
}

function getPlantingTransplantedGardenSquares(signal?: AbortSignal): Promise<Array<GardenSquareTransplanting>> {
  return fetchAsJson<Array<GardenSquareTransplanting>>('/plantings/transplantedgardensquare/', signal)
}

function completePlantingTransplantedGardenSquare(plantingPk: number) {
  return csrfPost('/plantings/garden/squares/transplant/complete/', {
    planting: plantingPk
  })
}

function getPlantingSeedTrayCurrent(signal?: AbortSignal): Promise<Array<SeedTrayPlantingDetails>> {
  return fetchAsJson<{ plantings: Array<SeedTrayPlantingDetails> }>('/plantings/seedtray/current/', signal).then((data) => data.plantings)
}

function getPlantingGardenSquaresCurrent(signal?: AbortSignal): Promise<Array<GardenSquarePlanting>> {
  return fetchAsJson<{ plantings: Array<GardenSquarePlanting> }>('/plantings/garden/squares/current/', signal).then((data) => data.plantings)
}

function getSpecificPlantsBySeedTray(seedTrayPk: number, signal?: AbortSignal): Promise<Array<SpecificPlant>> {
  return fetchAsJson<Array<SpecificPlant>>(`/plantings/seedtray-data/${seedTrayPk}/specificplants/`, signal)
}

function addSpecificPlant(data: SpecificPlantCreate): Promise<SpecificPlant> {
  return csrfPost('/plantings/specificplants/', data).then((r) => r.json() as Promise<SpecificPlant>)
}

function addSpecificPlantLocation(data: SpecificPlantLocationCreate): Promise<Response> {
  return csrfPost('/plantings/specificplantlocations/', data)
}

function endSpecificPlantLocation(locationPk: number): Promise<Response> {
  return csrfPost(`/plantings/specificplantlocations/${locationPk}/end/`, {})
}

function moveSpecificPlant(plantPk: number, data: SpecificPlantMove): Promise<Response> {
  return csrfPost(`/plantings/specificplants/${plantPk}/move/`, data)
}

function getSpecificPlant(plantPk: number, signal?: AbortSignal): Promise<SpecificPlantDetail> {
  return fetchAsJson<SpecificPlantDetail>(`/plantings/specificplants/${plantPk}/`, signal)
}

function getSpecificPlantLifecycleEvents(plantPk: number, signal?: AbortSignal): Promise<Array<PlantLifecycleEvent>> {
  return fetchAsJson<Array<PlantLifecycleEvent>>(`/plantings/specificplants/${plantPk}/lifecycle-events/`, signal)
}

function postSpecificPlantOutcome(plantPk: number, outcome: PlantOutcomeAction, data: PlantOutcome = {}): Promise<PlantLifecycleEvent> {
  return csrfPost(`/plantings/specificplants/${plantPk}/${outcome}/`, data).then((response) => response.json() as Promise<PlantLifecycleEvent>)
}

function reverseSpecificPlantEvent(plantPk: number, data: ReversePlantEvent): Promise<PlantLifecycleEvent> {
  return csrfPost(`/plantings/specificplants/${plantPk}/reverse-event/`, data).then((response) => response.json() as Promise<PlantLifecycleEvent>)
}

function postBulkPlantOutcome(data: BulkPlantOutcome): Promise<Array<PlantLifecycleEvent>> {
  return csrfPost('/plantings/specificplants/bulk-outcome/', data).then((response) => response.json() as Promise<Array<PlantLifecycleEvent>>)
}

function previewBulkPlantOperation(data: BulkPlantOperationRequest): Promise<BulkPlantPreview> {
  return csrfPost('/plantings/bulk-operations/preview/', data).then((response) => response.json() as Promise<BulkPlantPreview>)
}

function postBulkPlantOperation(data: BulkPlantOperationRequest): Promise<BulkPlantOperation> {
  return csrfPost('/plantings/bulk-operations/', data).then((response) => response.json() as Promise<BulkPlantOperation>)
}

function harvestQuery(filters: HarvestFilters | HarvestReportFilters): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') {
      query.set(key, String(value))
    }
  }
  return query.size > 0 ? `?${query.toString()}` : ''
}

function getHarvests(filters: HarvestFilters = {}, signal?: AbortSignal): Promise<Array<Harvest>> {
  return fetchAsJson<Array<Harvest>>(`/plantings/harvests/${harvestQuery(filters)}`, signal)
}

function getHarvest(harvestPk: number, signal?: AbortSignal): Promise<Harvest> {
  return fetchAsJson<Harvest>(`/plantings/harvests/${harvestPk}/`, signal)
}

function addHarvest(data: HarvestCreate): Promise<Harvest> {
  return csrfPost('/plantings/harvests/', data).then((response) => response.json() as Promise<Harvest>)
}

function reverseHarvest(harvestPk: number, data: ReverseHarvest): Promise<Harvest> {
  return csrfPost(`/plantings/harvests/${harvestPk}/reverse/`, data).then((response) => response.json() as Promise<Harvest>)
}

function getHarvestReport(filters: HarvestReportFilters, signal?: AbortSignal): Promise<Array<HarvestReportRow>> {
  return fetchAsJson<Array<HarvestReportRow>>(`/plantings/harvest-report/${harvestQuery(filters)}`, signal)
}

// `state` repeats rather than joining, because the backend reads it with
// getlist so that selecting two states asks for both rather than for neither.
function registerQuery(filters: NurseryRegisterFilters): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value === undefined || value === '') {
      continue
    }
    if (Array.isArray(value)) {
      for (const entry of value) {
        query.append(key, entry)
      }
    } else {
      query.set(key, String(value))
    }
  }
  return query.size > 0 ? `?${query.toString()}` : ''
}

function getNurseryRegister(filters: NurseryRegisterFilters = {}, signal?: AbortSignal): Promise<NurseryRegisterPage> {
  return fetchAsJson<NurseryRegisterPage>(`/plantings/register/${registerQuery(filters)}`, signal)
}

// Resolves a filter to the plants it currently selects. Bulk actions call this
// at the moment they act, so a selection never goes stale on screen.
function getNurseryRegisterSelection(filters: NurseryRegisterFilters = {}, signal?: AbortSignal): Promise<NurseryRegisterSelection> {
  return fetchAsJson<NurseryRegisterSelection>(`/plantings/register/ids/${registerQuery(filters)}`, signal)
}

function cohortQuery(filters: CohortFilters): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return query.size > 0 ? `?${query.toString()}` : ''
}

function getCohorts(filters: CohortFilters = {}, signal?: AbortSignal): Promise<CohortPage> {
  return fetchAsJson<CohortPage>(`/plantings/cohorts/${cohortQuery(filters)}`, signal)
}

function getCohort(cohortPk: number, signal?: AbortSignal): Promise<PlantCohort> {
  return fetchAsJson<PlantCohort>(`/plantings/cohorts/${cohortPk}/`, signal)
}

function getCohortAvailability(filters: CohortFilters = {}, signal?: AbortSignal): Promise<CohortAvailability> {
  return fetchAsJson<CohortAvailability>(`/plantings/cohorts/availability/${cohortQuery(filters)}`, signal)
}

function observeCohort(data: CohortObservation): Promise<PlantCohort> {
  return csrfPost('/plantings/cohorts/observe/', data).then((response) => response.json() as Promise<PlantCohort>)
}

function postCohortAction(cohortPk: number, actionName: string, data: CohortAction): Promise<PlantCohort | { operation: number; plants: Array<number> }> {
  return csrfPost(`/plantings/cohorts/${cohortPk}/${actionName}/`, data).then((response) => response.json() as Promise<PlantCohort | { operation: number; plants: Array<number> }>)
}

function mergeCohorts(data: CohortMerge): Promise<PlantCohort> {
  return csrfPost('/plantings/cohorts/merge/', data).then((response) => response.json() as Promise<PlantCohort>)
}

export {
  ProductionBatchFilters,
  getProductionBatches,
  getProductionBatch,
  addProductionBatch,
  updateProductionBatch,
  postProductionBatchAction,
  getPlantingDirectSowGardenRows,
  addPlantingDirectSowGardenRow,
  getPlantingDirectSowGardenSquares,
  addPlantingDirectSowGardenSquare,
  completePlantingDirectSowGardenSquare,
  correctGardenSquareSowing,
  getPlantingSeedTrays,
  getPlantingSeedTray,
  addPlantingSeedTray,
  completePlantingSeedTray,
  correctSeedTraySowing,
  getPlantingTransplantedGardenSquares,
  completePlantingTransplantedGardenSquare,
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  getSpecificPlantsBySeedTray,
  addSpecificPlant,
  addSpecificPlantLocation,
  endSpecificPlantLocation,
  moveSpecificPlant,
  getSpecificPlant,
  getSpecificPlantLifecycleEvents,
  postSpecificPlantOutcome,
  reverseSpecificPlantEvent,
  postBulkPlantOutcome,
  previewBulkPlantOperation,
  postBulkPlantOperation,
  getHarvests,
  getHarvest,
  addHarvest,
  reverseHarvest,
  getHarvestReport,
  getNurseryRegister,
  getNurseryRegisterSelection,
  getCohorts,
  getCohort,
  getCohortAvailability,
  observeCohort,
  postCohortAction,
  mergeCohorts
}
