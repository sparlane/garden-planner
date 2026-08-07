import { Seed, SeedCreate, SeedPacket, SeedPacketDetails, SeedPacketReceiptCreate, SeedPacketReceiptDraft, SeedPacketReconciliation } from '../types/seeds'
import { csrfDelete, csrfPatch, csrfPost, fetchAsJson } from '../utils'

function getSeeds(signal?: AbortSignal): Promise<Array<Seed>> {
  return fetchAsJson<Array<Seed>>('/seeds/seeds/', signal)
}

function addSeed(seed: SeedCreate) {
  return csrfPost('/seeds/seeds/', seed)
}

function getSeedPackets(signal?: AbortSignal): Promise<Array<SeedPacket>> {
  return fetchAsJson<Array<SeedPacket>>('/seeds/packets/', signal)
}

function getAllSeedPackets(signal?: AbortSignal): Promise<Array<SeedPacket>> {
  return fetchAsJson<Array<SeedPacket>>('/seeds/packets/all/', signal)
}

function getSeedPacketReceipts(signal?: AbortSignal): Promise<Array<SeedPacketReceiptDraft>> {
  return fetchAsJson<Array<SeedPacketReceiptDraft>>('/seeds/packet-receipts/', signal)
}

async function createSeedPacketReceipt(receipt: SeedPacketReceiptCreate): Promise<SeedPacketReceiptDraft> {
  const response = await csrfPost('/seeds/packet-receipts/', receipt)
  return response.json() as Promise<SeedPacketReceiptDraft>
}

async function updateSeedPacketReceipt(pk: number, receipt: SeedPacketReceiptCreate): Promise<SeedPacketReceiptDraft> {
  const response = await csrfPatch(`/seeds/packet-receipts/${pk}/`, receipt)
  return response.json() as Promise<SeedPacketReceiptDraft>
}

async function postSeedPacketReceipt(pk: number): Promise<SeedPacket> {
  const response = await csrfPost(`/seeds/packet-receipts/${pk}/post/`, {})
  return response.json() as Promise<SeedPacket>
}

function cancelSeedPacketReceipt(pk: number): Promise<Response> {
  return csrfDelete(`/seeds/packet-receipts/${pk}/`)
}

async function reconcileSeedPacket(pk: number, data: SeedPacketReconciliation): Promise<SeedPacket> {
  const response = await csrfPost(`/seeds/packets/${pk}/reconcile/`, data)
  return response.json() as Promise<SeedPacket>
}

function getSeedPacketsCurrent(signal?: AbortSignal): Promise<Array<SeedPacketDetails>> {
  return fetchAsJson<{ packets: Array<SeedPacketDetails> }>('/seeds/packets/current/', signal).then((data) => data.packets)
}

export {
  addSeed,
  cancelSeedPacketReceipt,
  createSeedPacketReceipt,
  getAllSeedPackets,
  getSeedPacketReceipts,
  getSeedPackets,
  getSeedPacketsCurrent,
  getSeeds,
  postSeedPacketReceipt,
  reconcileSeedPacket,
  updateSeedPacketReceipt
}
