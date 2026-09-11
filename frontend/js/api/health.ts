import { catalogSearchQuery } from './catalog'
import { csrfPatch, csrfPost, fetchAsJson } from '../utils'
import { HealthCatalogKind, HealthCatalogValue, HealthObservation, HealthObservationCreate, HealthPreview, HealthReport, HealthScope, QuarantineCase } from '../types/health'

function getHealthObservationTypes(signal?: AbortSignal, search?: string): Promise<Array<HealthCatalogValue>> {
  return fetchAsJson(`/health/observation-types/${catalogSearchQuery(search)}`, signal)
}

function getHealthDiagnoses(signal?: AbortSignal, search?: string): Promise<Array<HealthCatalogValue>> {
  return fetchAsJson(`/health/diagnoses/${catalogSearchQuery(search)}`, signal)
}

function addHealthCatalogValue(kind: HealthCatalogKind, value: object): Promise<HealthCatalogValue> {
  return csrfPost(`/health/${kind}/`, value).then((response) => response.json() as Promise<HealthCatalogValue>)
}

// Only what should read differently is sent. A stable code is not among the
// things that can, so a screen posting the whole record back would be offering
// the server a code to refuse on every unrelated edit.
function updateHealthCatalogValue(kind: HealthCatalogKind, pk: number, changes: Partial<HealthCatalogValue>): Promise<HealthCatalogValue> {
  return csrfPatch(`/health/${kind}/${pk}/`, changes).then((response) => response.json() as Promise<HealthCatalogValue>)
}

function getHealthObservations(signal?: AbortSignal): Promise<Array<HealthObservation>> {
  return fetchAsJson('/health/observations/', signal)
}

function previewHealthObservation(scopes: Array<HealthScope>): Promise<HealthPreview> {
  return csrfPost('/health/observations/preview/', { scopes }).then((response) => response.json() as Promise<HealthPreview>)
}

function createHealthObservation(data: HealthObservationCreate): Promise<HealthObservation> {
  return csrfPost('/health/observations/', data).then((response) => response.json() as Promise<HealthObservation>)
}

function correctHealthObservation(observation: number, data: object): Promise<HealthObservation> {
  return csrfPost(`/health/observations/${observation}/correct/`, data).then((response) => response.json() as Promise<HealthObservation>)
}

function quarantineHealthObservation(observation: number, data: object): Promise<QuarantineCase> {
  return csrfPost(`/health/observations/${observation}/quarantine/`, data).then((response) => response.json() as Promise<QuarantineCase>)
}

function linkHealthTreatment(observation: number, data: object): Promise<object> {
  return csrfPost(`/health/observations/${observation}/treatment/`, data).then((response) => response.json() as Promise<object>)
}

function recordHealthFollowUp(observation: number, data: object): Promise<object> {
  return csrfPost(`/health/observations/${observation}/follow-up/`, data).then((response) => response.json() as Promise<object>)
}

function getQuarantineCases(signal?: AbortSignal): Promise<Array<QuarantineCase>> {
  return fetchAsJson('/health/quarantines/', signal)
}

function actOnQuarantine(casePk: number, action: 'release' | 'escalate' | 'cull', data: object): Promise<QuarantineCase> {
  return csrfPost(`/health/quarantines/${casePk}/${action}/`, data).then((response) => response.json() as Promise<QuarantineCase>)
}

function getHealthReport(signal?: AbortSignal): Promise<HealthReport> {
  return fetchAsJson('/health/observations/reports/', signal)
}

export {
  actOnQuarantine,
  addHealthCatalogValue,
  correctHealthObservation,
  createHealthObservation,
  getHealthDiagnoses,
  getHealthObservationTypes,
  getHealthObservations,
  getHealthReport,
  getQuarantineCases,
  linkHealthTreatment,
  previewHealthObservation,
  quarantineHealthObservation,
  recordHealthFollowUp,
  updateHealthCatalogValue
}
