import { ConfirmGardenGeometry, GardenArea, GardenBed, GardenRow, GardenSquare } from '../types/garden'
import { csrfPost, fetchAsJson } from '../utils'

async function getGardenAreas(signal?: AbortSignal): Promise<Array<GardenArea>> {
  return fetchAsJson<Array<GardenArea>>('/garden/areas/', signal)
}

async function getGardenBeds(signal?: AbortSignal): Promise<Array<GardenBed>> {
  return fetchAsJson<Array<GardenBed>>('/garden/beds/', signal)
}

async function getGardenRows(signal?: AbortSignal): Promise<Array<GardenRow>> {
  return fetchAsJson<Array<GardenRow>>('/garden/rows/', signal)
}

async function getGardenSquares(signal?: AbortSignal): Promise<Array<GardenSquare>> {
  return fetchAsJson<Array<GardenSquare>>('/garden/squares/', signal)
}

async function confirmGardenGeometry(areaPk: number, data: ConfirmGardenGeometry): Promise<Response> {
  return csrfPost(`/garden/areas/${areaPk}/confirm-geometry/`, data)
}

export { confirmGardenGeometry, getGardenAreas, getGardenBeds, getGardenRows, getGardenSquares }
