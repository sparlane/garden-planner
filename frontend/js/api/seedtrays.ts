import { SeedTray, SeedTrayModel } from '../types/seedtrays'
import { fetchAsJson } from '../utils'

function getSeedTrayModels(): Promise<Array<SeedTrayModel>> {
  return fetchAsJson<Array<SeedTrayModel>>('/seedtrays/seedtraymodels/')
}

function getSeedTrays(): Promise<Array<SeedTray>> {
  return fetchAsJson<Array<SeedTray>>('/seedtrays/seedtrays/')
}

export { getSeedTrayModels, getSeedTrays }
