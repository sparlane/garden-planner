import { csrfPost, fetchAsJson } from '../utils'
import { BookkeepingEntry, IncomeTaxYear, Liability, TaxAsset } from '../types/bookkeeping'

const ROOT = '/bookkeeping/'

async function postJson<T>(url: string, data: object): Promise<T> {
  const response = await csrfPost(url, data)
  return response.json() as Promise<T>
}

const getEntries = (signal?: AbortSignal) => fetchAsJson<Array<BookkeepingEntry>>(`${ROOT}entries/`, signal)
const createEntry = (data: object) => postJson<BookkeepingEntry>(`${ROOT}entries/`, data)
const reverseEntry = (pk: number, reason: string) => postJson<BookkeepingEntry>(`${ROOT}entries/${pk}/reverse/`, { reason })
const getAssets = (signal?: AbortSignal) => fetchAsJson<Array<TaxAsset>>(`${ROOT}assets/`, signal)
const createAsset = (data: object) => postJson<TaxAsset>(`${ROOT}assets/`, data)
const createDepreciationSchedule = (data: object) => postJson(`${ROOT}depreciation-schedules/`, data)
const getLiabilities = (signal?: AbortSignal) => fetchAsJson<Array<Liability>>(`${ROOT}liabilities/`, signal)
const createLiability = (data: object) => postJson<Liability>(`${ROOT}liabilities/`, data)
const getIncomeYears = (signal?: AbortSignal) => fetchAsJson<Array<IncomeTaxYear>>(`${ROOT}income-years/`, signal)
const createIncomeYear = (data: object) => postJson<IncomeTaxYear>(`${ROOT}income-years/`, data)
const captureIncomeYear = (pk: number) => postJson<IncomeTaxYear>(`${ROOT}income-years/${pk}/capture/`, {})
const finalizeIncomeYear = (pk: number, confirmZeroOpening: boolean) => postJson<IncomeTaxYear>(`${ROOT}income-years/${pk}/finalize/`, { confirm_zero_opening: confirmZeroOpening })
const addStockLine = (pk: number, data: object) => postJson(`${ROOT}income-years/${pk}/stock-lines/`, data)
const incomeYearExportUrl = (pk: number) => `${ROOT}income-years/${pk}/export/`

export {
  addStockLine,
  captureIncomeYear,
  createAsset,
  createDepreciationSchedule,
  createEntry,
  createIncomeYear,
  createLiability,
  finalizeIncomeYear,
  getAssets,
  getEntries,
  getIncomeYears,
  getLiabilities,
  incomeYearExportUrl,
  reverseEntry
}
