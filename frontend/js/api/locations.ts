import { Location, LocationCreate, LocationOccupancy, LocationUpdate } from '../types/locations'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'

const LOCATIONS_URL = '/locations/'

// Pickers only ever offer places a workflow can still use, so the default asks
// for active ones. The catalog screen passes false to review retirements.
function getLocations(signal?: AbortSignal, active?: boolean): Promise<Array<Location>> {
  const query = active === undefined ? '' : `?active=${String(active)}`
  return fetchAsJson<Array<Location>>(`${LOCATIONS_URL}${query}`, signal)
}

function getLocationOccupancy(pk: number, signal?: AbortSignal): Promise<LocationOccupancy> {
  return fetchAsJson<LocationOccupancy>(`${LOCATIONS_URL}${pk}/occupancy/`, signal)
}

function createLocation(location: LocationCreate): Promise<Response> {
  return csrfPost(LOCATIONS_URL, location)
}

function updateLocation(pk: number, changes: LocationUpdate): Promise<Response> {
  return csrfPatch(`${LOCATIONS_URL}${pk}/`, changes)
}

export { createLocation, getLocationOccupancy, getLocations, updateLocation }
