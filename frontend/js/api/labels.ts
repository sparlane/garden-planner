import { csrfPost, fetchAsJson } from '../utils'
import { LabelIdentity, LabelPayloadMode, LabelPrintJob, LabelResolution, LabelTemplate } from '../types/labels'

function getLabelIdentities(signal?: AbortSignal): Promise<Array<LabelIdentity>> {
  return fetchAsJson<Array<LabelIdentity>>('/labels/identities/', signal)
}

function getLabelTemplates(signal?: AbortSignal): Promise<Array<LabelTemplate>> {
  return fetchAsJson<Array<LabelTemplate>>('/labels/templates/', signal)
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

export { createLabelPrintJob, getLabelIdentities, getLabelTemplates, markLabelPrintJobPrinted, previewLabels, resolveLabel }
