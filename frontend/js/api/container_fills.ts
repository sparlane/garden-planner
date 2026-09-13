import { csrfPost, fetchAsJson } from '../utils'
import { CleanMediaDisposition, SeedTrayGenerationEvent } from '../types/seedtrays'

export type PotFillTarget = { inventory_unit: number } | { stock_lot: number; source_location: number }

export interface PotFill {
  pk: number
  stock_lot: number | null
  inventory_unit: number | null
  code: string
  container_count: number
  status: 'open' | 'closed'
  opened_at: string
  closed_at: string | null
  close_reason: string
  notes: string
  events: SeedTrayGenerationEvent[]
  residuals: Array<{ pk: number; lot: number; base_quantity: string; base_unit: string; disposition: string; reason: string; correction_event: number | null }>
}

export function plantPotFill(pk: number, data: { plants: number[]; override_reason: string }): Promise<Response> {
  return csrfPost(`/seedtrays/container-fills/${pk}/plant/`, data)
}

export interface PotFillPage {
  count: number
  next: string | null
  previous: string | null
  results: PotFill[]
}

export interface PotFillContents {
  digest: string
  status: 'open' | 'closed'
  plants: number[]
  media: Array<{ lot: number; base_quantity: string; base_unit: string }>
  costs: {
    currency_code: string
    unknown_cost: boolean
    unknown_allocation: boolean
    applied_cost: string | null
    departed_cost: string | null
    held_cost: string | null
    production_loss: string | null
    recovered_cost: string | null
    rounding_difference: string | null
  }
}

export function getPotFill(pk: number, signal?: AbortSignal): Promise<PotFill> {
  return fetchAsJson<PotFill>(`/seedtrays/container-fills/${pk}/`, signal)
}

export function cleanPotFill(pk: number, data: { reason: string; digest: string; media: CleanMediaDisposition[] }): Promise<PotFill> {
  return csrfPost(`/seedtrays/container-fills/${pk}/clean/`, data).then((response) => response.json() as Promise<PotFill>)
}

export function reopenPotFill(pk: number, reason: string): Promise<PotFill> {
  return csrfPost(`/seedtrays/container-fills/${pk}/reopen/`, { reason }).then((response) => response.json() as Promise<PotFill>)
}

export function getPotFills(target: PotFillTarget, page: number, signal?: AbortSignal): Promise<PotFillPage> {
  const params = new URLSearchParams({ ...Object.fromEntries(Object.entries(target).map(([key, value]) => [key, String(value)])), page: String(page) })
  return fetchAsJson<PotFillPage>(`/seedtrays/container-fills/?${params}`, signal, true)
}

export function openPotFill(data: PotFillTarget & { container_count: number; notes: string }): Promise<PotFill> {
  return csrfPost('/seedtrays/container-fills/', data).then((response) => response.json() as Promise<PotFill>)
}

// A bench of numbered pots, filled as one claim. Every pot gets its own fill —
// a numbered pot owns its media history and its cost — but one refusal refuses
// the request, so the answer is either every fill or a list of the pots in the
// way, never a half-filled bench nobody can see the edge of.
export function openNumberedPotFills(data: { inventory_units: number[]; notes: string }): Promise<PotFill[]> {
  return csrfPost('/seedtrays/container-fills/open-numbered/', data).then((response) => response.json() as Promise<PotFill[]>)
}

export function getPotFillContents(pk: number, signal?: AbortSignal): Promise<PotFillContents> {
  return fetchAsJson<PotFillContents>(`/seedtrays/container-fills/${pk}/contents/`, signal)
}
