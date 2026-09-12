import { csrfPost, fetchAsJson } from '../utils'
import { LabelIdentity, LabelPayloadMode, LabelPrintJob, LabelResolution, LabelTemplate } from '../types/labels'

// Narrowing is the server's, because it answers on the content type behind
// the generic target rather than on anything the row carries. Fetching the lot
// and filtering here would also mean paging through every plant, tray and
// location in the workspace to find the containers numbered this morning.
function getLabelIdentities(targetType?: string, signal?: AbortSignal): Promise<Array<LabelIdentity>> {
  const query = targetType ? `?target_type=${encodeURIComponent(targetType)}` : ''
  return fetchAsJson<Array<LabelIdentity>>(`/labels/identities/${query}`, signal)
}

function getLabelTemplates(signal?: AbortSignal): Promise<Array<LabelTemplate>> {
  return fetchAsJson<Array<LabelTemplate>>('/labels/templates/', signal)
}

function createLabelTemplate(data: Omit<LabelTemplate, 'pk' | 'built_in' | 'active'>): Promise<LabelTemplate> {
  return csrfPost('/labels/templates/', data).then((response) => response.json() as Promise<LabelTemplate>)
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

export { createLabelPrintJob, createLabelTemplate, getLabelIdentities, getLabelTemplates, markLabelPrintJobPrinted, previewLabels, resolveLabel }
