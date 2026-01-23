import { fetchAsJson } from '../utils'
import { PlantFamily, PlantVariety, Plant } from '../types/plants'

function getPlantFamilies(): Promise<Array<PlantFamily>> {
  return fetchAsJson<Array<PlantFamily>>('/plants/family/')
}

function getPlantVarieties(): Promise<Array<PlantVariety>> {
  return fetchAsJson<Array<PlantVariety>>('/plants/variety/')
}

function getPlants(): Promise<Array<Plant>> {
  return fetchAsJson<Array<Plant>>('/plants/plant/')
}

export { getPlantFamilies, getPlantVarieties, getPlants }
