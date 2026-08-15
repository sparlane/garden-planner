import { fetchAsJson } from '../utils'
import { ReportEnvelope } from '../types/reports'

function reportQuery(filters: URLSearchParams): string {
  const query = filters.toString()
  return query ? `?${query}` : ''
}

function getReport<Row = Record<string, unknown>, Totals = Record<string, unknown>>(
  report: string,
  filters: URLSearchParams,
  signal?: AbortSignal
): Promise<ReportEnvelope<Row, Totals>> {
  return fetchAsJson<ReportEnvelope<Row, Totals>>(`/reports/${report}/${reportQuery(filters)}`, signal)
}

function reportExportUrl(report: string, filters: URLSearchParams): string {
  const exportFilters = new URLSearchParams(filters)
  exportFilters.delete('page')
  exportFilters.delete('section')
  return `/reports/${report}/export/${reportQuery(exportFilters)}`
}

export { getReport, reportExportUrl }
