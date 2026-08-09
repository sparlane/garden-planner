import { Location } from '../types/locations'
import { fetchAsJson } from '../utils'

const LOCATIONS_URL = '/locations/'

// Pickers only ever offer places a workflow can still use, so the default asks
// for active ones. The catalog screen passes active=false to review retirements.
function getLocations(signal?: AbortSignal, active: boolean = true): Promise<Array<Location>> {
  return fetchAsJson<Array<Location>>(`${LOCATIONS_URL}?active=${String(active)}`, signal)
}

export { getLocations }
