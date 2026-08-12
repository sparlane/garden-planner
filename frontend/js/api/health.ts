import { csrfPost, fetchAsJson } from '../utils'
import { HealthCatalogValue, HealthObservation, HealthObservationCreate, HealthPreview, HealthReport, HealthScope, QuarantineCase } from '../types/health'

function getHealthObservationTypes(signal?: AbortSignal): Promise<Array<HealthCatalogValue>> {
  return fetchAsJson('/health/observation-types/', signal)
}

function getHealthDiagnoses(signal?: AbortSignal): Promise<Array<HealthCatalogValue>> {
  return fetchAsJson('/health/diagnoses/', signal)
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
  recordHealthFollowUp
}
