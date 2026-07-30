import { fetchAsJson, csrfPost } from '../utils'
import {
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
  SpecificPlantLocationCreate,
  SpecificPlantMove
} from '../types/plantings'

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
  completePlantingTransplantedGardenSquare,
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent,
  getSpecificPlantsBySeedTray,
  addSpecificPlant,
  addSpecificPlantLocation,
  endSpecificPlantLocation,
  moveSpecificPlant
}
