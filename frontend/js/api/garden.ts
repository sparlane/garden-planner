import { GardenArea, GardenBed, GardenRow, GardenSquare } from '../types/garden'
import { fetchAsJson } from '../utils'

async function getGardenAreas(): Promise<Array<GardenArea>> {
  return fetchAsJson<Array<GardenArea>>('/garden/areas/')
}

async function getGardenBeds(): Promise<Array<GardenBed>> {
  return fetchAsJson<Array<GardenBed>>('/garden/beds/')
}

async function getGardenRows(): Promise<Array<GardenRow>> {
  return fetchAsJson<Array<GardenRow>>('/garden/rows/')
}

async function getGardenSquares(): Promise<Array<GardenSquare>> {
  return fetchAsJson<Array<GardenSquare>>('/garden/squares/')
}

export { getGardenAreas, getGardenBeds, getGardenRows, getGardenSquares }
