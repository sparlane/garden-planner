import { csrfPost, fetchAsJson } from '../utils'

export type PotFillTarget = { inventory_unit: number } | { stock_lot: number; source_location: number }

export interface PotFill {
  pk: number
  code: string
  container_count: number
  status: 'open' | 'closed'
  opened_at: string
  closed_at: string | null
  close_reason: string
  notes: string
}

export interface PotFillPage {
  count: number
  next: string | null
  previous: string | null
  results: PotFill[]
}

export interface PotFillContents {
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

export function getPotFills(target: PotFillTarget, page: number, signal?: AbortSignal): Promise<PotFillPage> {
  const params = new URLSearchParams({ ...Object.fromEntries(Object.entries(target).map(([key, value]) => [key, String(value)])), page: String(page) })
  return fetchAsJson<PotFillPage>(`/seedtrays/container-fills/?${params}`, signal, true)
}

export function openPotFill(data: PotFillTarget & { container_count: number; notes: string }): Promise<PotFill> {
  return csrfPost('/seedtrays/container-fills/', data).then((response) => response.json() as Promise<PotFill>)
}

export function getPotFillContents(pk: number, signal?: AbortSignal): Promise<PotFillContents> {
  return fetchAsJson<PotFillContents>(`/seedtrays/container-fills/${pk}/contents/`, signal)
}
