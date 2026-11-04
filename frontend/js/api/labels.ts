import { csrfDelete, csrfPatch, csrfPost, fetchAsJson } from '../utils'
import { LabelIdentityPage, LabelIdentityQuery, LabelPayloadMode, LabelPrintJob, LabelResolution, LabelTemplate } from '../types/labels'

function getLabelIdentities(query: LabelIdentityQuery = {}, signal?: AbortSignal): Promise<LabelIdentityPage> {
  const params = new URLSearchParams()
  if (query.targetType) params.set('target_type', query.targetType)
  if (query.objectId !== undefined) params.set('object_id', String(query.objectId))
  if (query.potType !== undefined) params.set('pot_type', String(query.potType))
  if (query.numberFrom !== undefined) params.set('number_from', String(query.numberFrom))
  if (query.numberTo !== undefined) params.set('number_to', String(query.numberTo))
  if (query.pageSize !== undefined) params.set('page_size', String(query.pageSize))
  if (query.page !== undefined && query.page > 1) params.set('page', String(query.page))
  const suffix = params.size > 0 ? `?${params.toString()}` : ''
  return fetchAsJson<LabelIdentityPage>(`/labels/identities/${suffix}`, signal)
}

function getLabelTemplates(signal?: AbortSignal): Promise<Array<LabelTemplate>> {
  return fetchAsJson<Array<LabelTemplate>>('/labels/templates/', signal)
}

function createLabelTemplate(data: Omit<LabelTemplate, 'pk' | 'built_in' | 'active'>): Promise<LabelTemplate> {
  return csrfPost('/labels/templates/', data).then((response) => response.json() as Promise<LabelTemplate>)
}

function updateLabelTemplate(pk: number, data: Omit<LabelTemplate, 'pk' | 'built_in' | 'active'>): Promise<LabelTemplate> {
  return csrfPatch(`/labels/templates/${pk}/`, data).then((response) => response.json() as Promise<LabelTemplate>)
}

// The server retires a template rather than erasing it, because print jobs
// keep pointing at the one they were made from.
function deleteLabelTemplate(pk: number): Promise<Response> {
  return csrfDelete(`/labels/templates/${pk}/`)
}

function resolveLabel(value: string, signal?: AbortSignal): Promise<LabelResolution> {
  return fetchAsJson<LabelResolution>(`/labels/resolve/?value=${encodeURIComponent(value)}`, signal)
}

function previewLabels(template: number, identities: Array<number>, payloadMode: LabelPayloadMode): Promise<LabelPrintJob> {
  return csrfPost('/labels/print-jobs/preview/', { template, identities, payload_mode: payloadMode }).then((response) => response.json() as Promise<LabelPrintJob>)
}

function createLabelPrintJob(template: number, identities: Array<number>, payloadMode: LabelPayloadMode): Promise<LabelPrintJob> {
  return csrfPost('/labels/print-jobs/', { template, identities, payload_mode: payloadMode }).then((response) => response.json() as Promise<LabelPrintJob>)
}

function markLabelPrintJobPrinted(job: number): Promise<{ pk: number; printed_at: string }> {
  return csrfPost(`/labels/print-jobs/${job}/printed/`, {}).then((response) => response.json() as Promise<{ pk: number; printed_at: string }>)
}

export {
  createLabelPrintJob,
  createLabelTemplate,
  deleteLabelTemplate,
  getLabelIdentities,
  getLabelTemplates,
  markLabelPrintJobPrinted,
  previewLabels,
  resolveLabel,
  updateLabelTemplate
}
