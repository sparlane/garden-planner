import { fetchAsJson, csrfPost, csrfPatch } from '../utils'
import {
  GardenRowDirectPlanting,
  GardenSquareDirectPlanting,
  SeedTrayPlanting,
  GardenSquareTransplanting,
  GardenSquarePlanting,
  GardenRowDirectPlantingCreate,
  GardenSquareDirectPlantingCreate,
  GardenSquareTransplantingCreate,
  SeedTrayPlantingCreate,
  SeedTrayPlantingDetails,
  SpecificPlant,
  SpecificPlantCreate,
  SpecificPlantLocationCreate
} from '../types/plantings'

function getPlantingDirectSowGardenRows(): Promise<Array<GardenRowDirectPlanting>> {
  return fetchAsJson<Array<GardenRowDirectPlanting>>('/plantings/directsowgardenrow/')
}

function addPlantingDirectSowGardenRow(data: GardenRowDirectPlantingCreate) {
  return csrfPost('/plantings/directsowgardenrow/', data)
}

function getPlantingDirectSowGardenSquares(): Promise<Array<GardenSquareDirectPlanting>> {
  return fetchAsJson<Array<GardenSquareDirectPlanting>>('/plantings/directsowgardensquare/')
}

function addPlantingDirectSowGardenSquare(data: GardenSquareDirectPlantingCreate) {
  return csrfPost('/plantings/directsowgardensquare/', data)
}

function completePlantingDirectSowGardenSquare(plantingPk: number) {
  return csrfPost('/plantings/garden/squares/complete/', {
    planting: plantingPk
  })
}

function getPlantingSeedTrays(): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>('/plantings/seedtray/')
}

function getPlantingSeedTray(seedTrayPk: number): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>(`/plantings/seedtray-data/${seedTrayPk}/plantings/`)
}

function addPlantingSeedTray(data: SeedTrayPlantingCreate) {
  return csrfPost('/plantings/seedtray/', data)
}

function completePlantingSeedTray(plantingPk: number) {
  return csrfPost('/plantings/seedtray/complete/', {
    planting: plantingPk
  })
}

function getPlantingTransplantedGardenSquares(): Promise<Array<GardenSquareTransplanting>> {
  return fetchAsJson<Array<GardenSquareTransplanting>>('/plantings/transplantedgardensquare/')
}

function addPlantingTransplantedGardenSquare(data: GardenSquareTransplantingCreate) {
  return csrfPost('/plantings/transplantedgardensquare/', data)
}

function completePlantingTransplantedGardenSquare(plantingPk: number) {
  return csrfPost('/plantings/garden/squares/transplant/complete/', {
    planting: plantingPk
  })
}

function getPlantingSeedTrayCurrent(): Promise<Array<SeedTrayPlantingDetails>> {
  return fetchAsJson<{ plantings: Array<SeedTrayPlantingDetails> }>('/plantings/seedtray/current/').then((data) => data.plantings)
}

function getPlantingGardenSquaresCurrent(): Promise<Array<GardenSquarePlanting>> {
  return fetchAsJson<{ plantings: Array<GardenSquarePlanting> }>('/plantings/garden/squares/current/').then((data) => data.plantings)
}

function getSpecificPlantsBySeedTray(seedTrayPk: number): Promise<Array<SpecificPlant>> {
  return fetchAsJson<Array<SpecificPlant>>(`/plantings/seedtray-data/${seedTrayPk}/specificplants/`)
}

function addSpecificPlant(data: SpecificPlantCreate): Promise<SpecificPlant> {
  return csrfPost('/plantings/specificplants/', data).then((r) => r.json() as Promise<SpecificPlant>)
}

function addSpecificPlantLocation(data: SpecificPlantLocationCreate): Promise<Response> {
  return csrfPost('/plantings/specificplantlocations/', data)
}

function endSpecificPlantLocation(locationPk: number, ended?: string): Promise<Response> {
  return csrfPatch(`/plantings/specificplantlocations/${locationPk}/`, ended ? { ended } : {})
}

export {
  getPlantingDirectSowGardenRows,
  addPlantingDirectSowGardenRow,
  getPlantingDirectSowGardenSquares,
  addPlantingDirectSowGardenSquare,
  completePlantingDirectSowGardenSquare,
  getPlantingSeedTrays,
  getPlantingSeedTray,
  addPlantingSeedTray,
  completePlantingSeedTray,
  getPlantingTransplantedGardenSquares,
  addPlantingTransplantedGardenSquare,
  completePlantingTransplantedGardenSquare,
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  getSpecificPlantsBySeedTray,
  addSpecificPlant,
  addSpecificPlantLocation,
  endSpecificPlantLocation
}
