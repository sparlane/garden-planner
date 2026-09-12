import { catalogSearchQuery } from './catalog'
import { fetchAsJson, csrfPatch, csrfPost } from '../utils'
import { PlantFamily, PlantVariety, Plant, PlantFamilyCreate, PlantCreate, PlantVarietyCreate, InstalledCatalog, ReferenceSet } from '../types/plants'

function getPlantFamilies(signal?: AbortSignal, search?: string): Promise<Array<PlantFamily>> {
  return fetchAsJson<Array<PlantFamily>>(`/plants/family/${catalogSearchQuery(search)}`, signal)
}

// One record, for the page that is about that record. A detail route asks for
// the record it is showing rather than picking it out of a collection, because
// varieties are paginated and the one being looked at need not be on the page
// the collection would send.
function getPlantFamily(pk: number, signal?: AbortSignal): Promise<PlantFamily> {
  return fetchAsJson<PlantFamily>(`/plants/family/${pk}/`, signal)
}

function addPlantFamily(data: PlantFamilyCreate) {
  return csrfPost('/plants/family/', data)
}

async function updatePlantFamily(pk: number, data: Partial<PlantFamilyCreate>): Promise<PlantFamily> {
  const response = await csrfPatch(`/plants/family/${pk}/`, data)
  return response.json() as Promise<PlantFamily>
}

function getPlantVarieties(signal?: AbortSignal, search?: string): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>(`/plants/variety/${catalogSearchQuery(search)}`, signal)
}

// One crop's varieties. Families and crops are short enough to be sent whole
// and filed in the browser; varieties are the level that is not, so a crop's
// own page asks the collection for them rather than filtering a page of the
// catalog that may not hold them.
function getCropVarieties(plant: number, signal?: AbortSignal): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>(`/plants/variety/?plant=${plant}`, signal)
}

function getPlantVariety(pk: number, signal?: AbortSignal): Promise<PlantVariety> {
  return fetchAsJson<PlantVariety>(`/plants/variety/${pk}/`, signal)
}

function addPlantVariety(data: PlantVarietyCreate) {
  return csrfPost('/plants/variety/', data)
}

async function updatePlantVariety(pk: number, data: Partial<PlantVarietyCreate>): Promise<PlantVariety> {
  const response = await csrfPatch(`/plants/variety/${pk}/`, data)
  return response.json() as Promise<PlantVariety>
}

function getPlants(signal?: AbortSignal, search?: string): Promise<Array<Plant>> {
  return fetchAsJson<Array<Plant>>(`/plants/plant/${catalogSearchQuery(search)}`, signal)
}

function getPlant(pk: number, signal?: AbortSignal): Promise<Plant> {
  return fetchAsJson<Plant>(`/plants/plant/${pk}/`, signal)
}

function addPlant(data: PlantCreate) {
  return csrfPost('/plants/plant/', data)
}

async function updatePlant(pk: number, data: Partial<PlantCreate>): Promise<Plant> {
  const response = await csrfPatch(`/plants/plant/${pk}/`, data)
  return response.json() as Promise<Plant>
}

// Installing the starter crops is a POST because it writes, and asking twice
// creates nothing the second time and argues with nothing the gardener has
// done in between, so a screen may offer it whether or not the catalog is
// empty.
async function installStarterCrops(): Promise<InstalledCatalog> {
  const response = await csrfPost('/plants/starters/', {})
  return response.json() as Promise<InstalledCatalog>
}

// The catalog as a document, to be carried into another garden. It is what
// this garden now says rather than what any set once said about it, so a
// figure measured here travels and the provenance stays behind.
function exportReferenceSet(signal?: AbortSignal): Promise<ReferenceSet> {
  return fetchAsJson<ReferenceSet>('/plants/reference-set/', signal)
}

// The same document read the other way. It is checked by the server before
// anything is written, so a document with one bad crop in it installs none of
// itself and there is nothing for the browser to validate first.
async function importReferenceSet(document: ReferenceSet): Promise<InstalledCatalog> {
  const response = await csrfPost('/plants/reference-set/', document)
  return response.json() as Promise<InstalledCatalog>
}

export {
  getCropVarieties,
  getPlant,
  getPlantFamilies,
  getPlantFamily,
  getPlantVarieties,
  getPlantVariety,
  getPlants,
  addPlantFamily,
  addPlantVariety,
  addPlant,
  installStarterCrops,
  exportReferenceSet,
  importReferenceSet,
  updatePlantFamily,
  updatePlantVariety,
  updatePlant
}
