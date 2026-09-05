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
  GerminationClose,
  GardenQuickAddEntry,
  GardenQuickAddReview,
  GardenQuickAddedPlanting,
  GardenRegisterDetail,
  GardenRegisterFilters,
  GardenRegisterPage,
  DirectSownEventCreate,
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
  PlantCohort,
  GrowthCatalogValue,
  NurseryPlanDemand,
  NurseryPlanVariance,
  NurseryPlanningAssumption,
  NurseryPlanningStageAssumption,
  NurseryProductionPlan
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

// Declaring a sowing finished germinating. The remainder is not sent: the
// server counts it under a lock, so two operators closing the same tray cannot
// disagree about how many seeds never came up.
function closeSowingGermination(plantingPk: number, data: GerminationClose): Promise<SeedTrayPlanting> {
  return csrfPost(`/plantings/seedtray/${plantingPk}/close-germination/`, data).then((response) => response.json() as Promise<SeedTrayPlanting>)
}

// Withdrawing a close recorded in error. A seedling that genuinely came up
// late is not this: it is an ordinary germination carrying a reason.
function reopenSowingGermination(plantingPk: number, reason: string): Promise<SeedTrayPlanting> {
  return csrfPost(`/plantings/seedtray/${plantingPk}/reopen-germination/`, { reason }).then((response) => response.json() as Promise<SeedTrayPlanting>)
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

function previewGardenQuickAdd(entries: Array<GardenQuickAddEntry>): Promise<GardenQuickAddReview> {
  return csrfPost('/plantings/garden-quick-add/preview/', { entries }).then((response) => response.json() as Promise<GardenQuickAddReview>)
}

function createGardenQuickAdd(review: GardenQuickAddReview): Promise<Array<GardenQuickAddedPlanting>> {
  return csrfPost('/plantings/garden-quick-add/', {
    entries: review.entries,
    confirmation_token: review.confirmation_token
  }).then((response) => response.json() as Promise<Array<GardenQuickAddedPlanting>>)
}

function getGardenQuickAddedPlantings(signal?: AbortSignal): Promise<Array<GardenQuickAddedPlanting>> {
  return fetchAsJson<Array<GardenQuickAddedPlanting>>('/plantings/garden-quick-add/', signal)
}

function gardenRegisterQuery(filters: GardenRegisterFilters): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value))
  }
  return query.size > 0 ? `?${query.toString()}` : ''
}

function getGardenRegister(filters: GardenRegisterFilters = {}, signal?: AbortSignal): Promise<GardenRegisterPage> {
  return fetchAsJson<GardenRegisterPage>(`/plantings/garden-register/${gardenRegisterQuery(filters)}`, signal)
}

function getGardenRegisterDetail(key: string, signal?: AbortSignal): Promise<GardenRegisterDetail> {
  return fetchAsJson<GardenRegisterDetail>(`/plantings/garden-register/${key}/`, signal)
}

function finishGardenRegisterCrop(key: string, eventType: 'finished' | 'failed', reason = ''): Promise<Response> {
  return csrfPost(`/plantings/garden-register/${key}/finish/`, { event_type: eventType, reason })
}

function correctGardenRegisterStatus(key: string, event: number, reason: string): Promise<Response> {
  return csrfPost(`/plantings/garden-register/${key}/correct-status/`, { event, reason })
}

function recordDirectSownEvent(key: string, data: DirectSownEventCreate): Promise<Response> {
  return csrfPost(`/plantings/garden-register/${key}/direct-event/`, data)
}

function individualizeDirectSownCrop(key: string, quantity: number, notes: string): Promise<Response> {
  return csrfPost(`/plantings/garden-register/${key}/individualize/`, { quantity, names: [], notes })
}

function reverseDirectSownEvent(key: string, event: number, notes: string): Promise<Response> {
  return csrfPost(`/plantings/garden-register/${key}/reverse-direct-event/`, { event, notes })
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

function getGrowthStages(signal?: AbortSignal): Promise<Array<GrowthCatalogValue>> {
  return fetchAsJson<Array<GrowthCatalogValue>>('/plantings/growth-stages/', signal)
}

function getPlantGrades(signal?: AbortSignal): Promise<Array<GrowthCatalogValue>> {
  return fetchAsJson<Array<GrowthCatalogValue>>('/plantings/plant-grades/', signal)
}

function saveGrowthCatalog(kind: 'growth-stages' | 'plant-grades', value: Partial<GrowthCatalogValue> & { name: string; code?: string }): Promise<GrowthCatalogValue> {
  const path = `/plantings/${kind}/${value.pk === undefined ? '' : `${value.pk}/`}`
  const request = value.pk === undefined ? csrfPost(path, value) : csrfPatch(path, value)
  return request.then((response) => response.json() as Promise<GrowthCatalogValue>)
}

function getPlanningAssumptions(signal?: AbortSignal): Promise<Array<NurseryPlanningAssumption>> {
  return fetchAsJson<Array<NurseryPlanningAssumption>>('/plantings/planning-assumptions/', signal)
}

function addPlanningAssumption(data: object): Promise<NurseryPlanningAssumption> {
  return csrfPost('/plantings/planning-assumptions/', data).then((response) => response.json() as Promise<NurseryPlanningAssumption>)
}

function addPlanningStageAssumption(data: object): Promise<NurseryPlanningStageAssumption> {
  return csrfPost('/plantings/planning-stage-assumptions/', data).then((response) => response.json() as Promise<NurseryPlanningStageAssumption>)
}

function getProductionPlans(signal?: AbortSignal): Promise<Array<NurseryProductionPlan>> {
  return fetchAsJson<Array<NurseryProductionPlan>>('/plantings/production-plans/', signal)
}

function addProductionPlan(data: object): Promise<NurseryProductionPlan> {
  return csrfPost('/plantings/production-plans/', data).then((response) => response.json() as Promise<NurseryProductionPlan>)
}

function addPlanDemand(data: object): Promise<NurseryPlanDemand> {
  return csrfPost('/plantings/production-plan-demand/', data).then((response) => response.json() as Promise<NurseryPlanDemand>)
}

function postPlanAction(planPk: number, actionName: 'calculate' | 'approve' | 'revise'): Promise<NurseryProductionPlan> {
  return csrfPost(`/plantings/production-plans/${planPk}/${actionName}/`, {}).then((response) => response.json() as Promise<NurseryProductionPlan>)
}

// The plan takes on the commitments already sold for delivery in a window,
// rather than an operator retyping them as a forecast beside the orders.
function importPlanDemand(planPk: number, window: { ready_from: string; ready_until: string }): Promise<NurseryProductionPlan> {
  return csrfPost(`/plantings/production-plans/${planPk}/import-demand/`, window).then((response) => response.json() as Promise<NurseryProductionPlan>)
}

function getPlanVariance(planPk: number, signal?: AbortSignal): Promise<Array<NurseryPlanVariance>> {
  return fetchAsJson<Array<NurseryPlanVariance>>(`/plantings/production-plans/${planPk}/variance/`, signal)
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
  closeSowingGermination,
  reopenSowingGermination,
  getPlantingTransplantedGardenSquares,
  completePlantingTransplantedGardenSquare,
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  previewGardenQuickAdd,
  createGardenQuickAdd,
  getGardenQuickAddedPlantings,
  getGardenRegister,
  getGardenRegisterDetail,
  finishGardenRegisterCrop,
  correctGardenRegisterStatus,
  recordDirectSownEvent,
  individualizeDirectSownCrop,
  reverseDirectSownEvent,
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
  getGrowthStages,
  getPlantGrades,
  saveGrowthCatalog,
  getPlanningAssumptions,
  addPlanningAssumption,
  addPlanningStageAssumption,
  getProductionPlans,
  addProductionPlan,
  addPlanDemand,
  importPlanDemand,
  postPlanAction,
  getPlanVariance,
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
