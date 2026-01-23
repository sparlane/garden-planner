import { fetchAsJson, csrfPost } from '../utils'
import { PlantFamily, PlantVariety, Plant, PlantFamilyCreate, PlantCreate, PlantVarietyCreate } from '../types/plants'

function getPlantFamilies(): Promise<Array<PlantFamily>> {
  return fetchAsJson<Array<PlantFamily>>('/plants/family/')
}

function addPlantFamily(data: PlantFamilyCreate) {
  return csrfPost('/plants/family/', data)
}

function getPlantVarieties(): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>('/plants/variety/')
}

function addPlantVariety(data: PlantVarietyCreate) {
  return csrfPost('/plants/variety/', data)
}

function getPlants(): Promise<Array<Plant>> {
  return fetchAsJson<Array<Plant>>('/plants/plant/')
}

function addPlant(data: PlantCreate) {
  return csrfPost('/plants/plant/', data)
}

export { getPlantFamilies, getPlantVarieties, getPlants, addPlantFamily, addPlantVariety, addPlant }
