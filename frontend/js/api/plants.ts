import { fetchAsJson, csrfPost } from '../utils'
import { PlantFamily, PlantVariety, Plant, PlantFamilyCreate, PlantCreate, PlantVarietyCreate } from '../types/plants'

function getPlantFamilies(signal?: AbortSignal): Promise<Array<PlantFamily>> {
  return fetchAsJson<Array<PlantFamily>>('/plants/family/', signal)
}

function addPlantFamily(data: PlantFamilyCreate) {
  return csrfPost('/plants/family/', data)
}

function getPlantVarieties(signal?: AbortSignal): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>('/plants/variety/', signal)
}

function addPlantVariety(data: PlantVarietyCreate) {
  return csrfPost('/plants/variety/', data)
}

function getPlants(signal?: AbortSignal): Promise<Array<Plant>> {
  return fetchAsJson<Array<Plant>>('/plants/plant/', signal)
}

function addPlant(data: PlantCreate) {
  return csrfPost('/plants/plant/', data)
}

export { getPlantFamilies, getPlantVarieties, getPlants, addPlantFamily, addPlantVariety, addPlant }
