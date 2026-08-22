import { GstRegistration, GstRegistrationCreate, GstRegistrationCreated, GstStatus } from '../types/tax'
import { csrfPost, fetchAsJson } from '../utils'

const ROOT = '/tax/'

function getGstStatus(signal?: AbortSignal): Promise<GstStatus> {
  return fetchAsJson<GstStatus>(`${ROOT}gst/status/`, signal)
}

function getGstRegistrations(signal?: AbortSignal): Promise<GstRegistration[]> {
  return fetchAsJson<GstRegistration[]>(`${ROOT}gst/registrations/`, signal)
}

// There is no update counterpart on purpose: the server refuses PATCH and PUT.
// Correcting an arrangement means posting a new one with `supersedes` set, so
// the mistake and the correction both stay readable.
async function createGstRegistration(registration: Partial<GstRegistrationCreate>): Promise<GstRegistrationCreated> {
  const response = await csrfPost(`${ROOT}gst/registrations/`, registration)
  return response.json() as Promise<GstRegistrationCreated>
}

export { createGstRegistration, getGstRegistrations, getGstStatus }
