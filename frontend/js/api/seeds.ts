import { Seed, SeedCreate, SeedPacket, SeedPacketCreate, SeedPacketDetails } from '../types/seeds'
import { csrfPost, fetchAsJson } from '../utils'

function getSeeds(): Promise<Array<Seed>> {
  return fetchAsJson<Array<Seed>>('/seeds/seeds/')
}

function addSeed(seed: SeedCreate) {
  return csrfPost('/seeds/seeds/', seed)
}

function getSeedPackets(): Promise<Array<SeedPacket>> {
  return fetchAsJson<Array<SeedPacket>>('/seeds/packets/')
}

function addSeedPacket(packet: SeedPacketCreate) {
  return csrfPost('/seeds/packets/', packet)
}

function getSeedPacketsCurrent(): Promise<Array<SeedPacketDetails>> {
  return fetchAsJson<{ packets: Array<SeedPacketDetails> }>('/seeds/packets/current/').then((data) => data.packets)
}

function emptySeedPacket(pk: number) {
  return csrfPost('/seeds/packets/empty/', { packet: pk })
}

export { getSeeds, getSeedPackets, getSeedPacketsCurrent, addSeed, addSeedPacket, emptySeedPacket }
