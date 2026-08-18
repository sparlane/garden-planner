import { ConfirmGardenGeometry, GardenArea, GardenAreaCreate, GardenBed, GardenBedCreate, GardenRow, GardenRowCreate, GardenSquare, GardenSquareCreate } from '../types/garden'
import { csrfDelete, csrfPost, fetchAsJson } from '../utils'

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

async function createGardenArea(area: GardenAreaCreate): Promise<GardenArea> {
  return csrfPost('/garden/areas/', area).then((response) => response.json() as Promise<GardenArea>)
}

async function createGardenBed(bed: GardenBedCreate): Promise<GardenBed> {
  return csrfPost('/garden/beds/', bed).then((response) => response.json() as Promise<GardenBed>)
}

// Rows and squares are posted as a list because a layout template is chosen as
// one thing. The server writes the whole batch in one transaction, so a
// template that collides on its last square leaves nothing behind.
async function createGardenRows(rows: Array<GardenRowCreate>): Promise<Array<GardenRow>> {
  return csrfPost('/garden/rows/', rows).then((response) => response.json() as Promise<Array<GardenRow>>)
}

async function createGardenSquares(squares: Array<GardenSquareCreate>): Promise<Array<GardenSquare>> {
  return csrfPost('/garden/squares/', squares).then((response) => response.json() as Promise<Array<GardenSquare>>)
}

async function deleteGardenBed(pk: number): Promise<Response> {
  return csrfDelete(`/garden/beds/${pk}/`)
}

async function confirmGardenGeometry(areaPk: number, data: ConfirmGardenGeometry): Promise<Response> {
  return csrfPost(`/garden/areas/${areaPk}/confirm-geometry/`, data)
}

export {
  confirmGardenGeometry,
  createGardenArea,
  createGardenBed,
  createGardenRows,
  createGardenSquares,
  deleteGardenBed,
  getGardenAreas,
  getGardenBeds,
  getGardenRows,
  getGardenSquares
}
