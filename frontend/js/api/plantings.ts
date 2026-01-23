import { fetchAsJson } from '../utils'
import { GardenRowDirectPlanting, GardenSquareDirectPlanting, SeedTrayPlanting, GardenSquareTransplanting, GardenSquarePlanting } from '../types/plantings'

function getPlantingDirectSowGardenRows(): Promise<Array<GardenRowDirectPlanting>> {
  return fetchAsJson<Array<GardenRowDirectPlanting>>('/plantings/directsowgardenrow/')
}

function getPlantingDirectSowGardenSquares(): Promise<Array<GardenSquareDirectPlanting>> {
  return fetchAsJson<Array<GardenSquareDirectPlanting>>('/plantings/directsowgardensquare/')
}

function getPlantingSeedTrays(): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>('/plantings/seedtray/')
}

function getPlantingTransplantedGardenSquares(): Promise<Array<GardenSquareTransplanting>> {
  return fetchAsJson<Array<GardenSquareTransplanting>>('/plantings/transplantedgardensquare/')
}

function getPlantingSeedTrayCurrent(): Promise<Array<SeedTrayPlanting>> {
  return fetchAsJson<Array<SeedTrayPlanting>>('/plantings/seedtray/current/')
}

function getPlantingGardenSquaresCurrent(): Promise<Array<GardenSquarePlanting>> {
  return fetchAsJson<{ plantings: Array<GardenSquarePlanting> }>('/plantings/garden/squares/current/').then((data) => data.plantings)
}

export {
  getPlantingDirectSowGardenRows,
  getPlantingDirectSowGardenSquares,
  getPlantingSeedTrays,
  getPlantingTransplantedGardenSquares,
  getPlantingSeedTrayCurrent,
  getPlantingGardenSquaresCurrent
}
