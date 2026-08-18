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

// Installs the ordinary places a household garden needs — a shed, a seed store,
// a potting bench, a holding area. Asking twice creates nothing the second
// time, so the setup wizard can be left and resumed at this step.
function installHouseholdLocations(): Promise<Array<Location>> {
  return csrfPost('/garden/setup/household-locations/', {}).then((response) => response.json() as Promise<Array<Location>>)
}

export { createLocation, getLocationOccupancy, getLocations, installHouseholdLocations, updateLocation }
