import { Seed, SeedPacket, SeedPacketDetails } from '../types/seeds'
import { fetchAsJson } from '../utils'

function getSeeds(): Promise<Array<Seed>> {
  return fetchAsJson<Array<Seed>>('/seeds/seeds/')
}

function getSeedPackets(): Promise<Array<SeedPacket>> {
  return fetchAsJson<Array<SeedPacket>>('/seeds/packets/')
}

function getSeedPacketsCurrent(): Promise<Array<SeedPacketDetails>> {
  return fetchAsJson<{ packets: Array<SeedPacketDetails> }>('/seeds/packets/current/').then((data) => data.packets)
}

export { getSeeds, getSeedPackets, getSeedPacketsCurrent }
