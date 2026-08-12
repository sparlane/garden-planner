import { fetchAsJson, csrfPatch, csrfPost } from '../utils'
import { PlantFamily, PlantVariety, Plant, PlantFamilyCreate, PlantCreate, PlantVarietyCreate } from '../types/plants'

function getPlantFamilies(signal?: AbortSignal): Promise<Array<PlantFamily>> {
  return fetchAsJson<Array<PlantFamily>>('/plants/family/', signal)
}

function addPlantFamily(data: PlantFamilyCreate) {
  return csrfPost('/plants/family/', data)
}

async function updatePlantFamily(pk: number, data: PlantFamilyCreate): Promise<PlantFamily> {
  const response = await csrfPatch(`/plants/family/${pk}/`, data)
  return response.json() as Promise<PlantFamily>
}

function getPlantVarieties(signal?: AbortSignal): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>('/plants/variety/', signal)
}

function addPlantVariety(data: PlantVarietyCreate) {
  return csrfPost('/plants/variety/', data)
}

async function updatePlantVariety(pk: number, data: PlantVarietyCreate): Promise<PlantVariety> {
  const response = await csrfPatch(`/plants/variety/${pk}/`, data)
  return response.json() as Promise<PlantVariety>
}

function getPlants(signal?: AbortSignal): Promise<Array<Plant>> {
  return fetchAsJson<Array<Plant>>('/plants/plant/', signal)
}

function addPlant(data: PlantCreate) {
  return csrfPost('/plants/plant/', data)
}

async function updatePlant(pk: number, data: PlantCreate): Promise<Plant> {
  const response = await csrfPatch(`/plants/plant/${pk}/`, data)
  return response.json() as Promise<Plant>
}

export { getPlantFamilies, getPlantVarieties, getPlants, addPlantFamily, addPlantVariety, addPlant, updatePlantFamily, updatePlantVariety, updatePlant }
