import { Seed, SeedCreate, SeedPacket, SeedPacketCreate, SeedPacketDetails } from '../types/seeds'
import { csrfPost, fetchAsJson } from '../utils'

function getSeeds(signal?: AbortSignal): Promise<Array<Seed>> {
  return fetchAsJson<Array<Seed>>('/seeds/seeds/', signal)
}

function addSeed(seed: SeedCreate) {
  return csrfPost('/seeds/seeds/', seed)
}

function getSeedPackets(signal?: AbortSignal): Promise<Array<SeedPacket>> {
  return fetchAsJson<Array<SeedPacket>>('/seeds/packets/', signal)
}

function addSeedPacket(packet: SeedPacketCreate) {
  return csrfPost('/seeds/packets/', packet)
}

function getSeedPacketsCurrent(signal?: AbortSignal): Promise<Array<SeedPacketDetails>> {
  return fetchAsJson<{ packets: Array<SeedPacketDetails> }>('/seeds/packets/current/', signal).then((data) => data.packets)
}

function emptySeedPacket(pk: number) {
  return csrfPost('/seeds/packets/empty/', { packet: pk })
}

export { getSeeds, getSeedPackets, getSeedPacketsCurrent, addSeed, addSeedPacket, emptySeedPacket }
