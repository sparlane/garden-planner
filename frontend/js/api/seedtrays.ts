import { SeedTray, SeedTrayCell, SeedTrayCreate, SeedTrayModel, SeedTrayModelCreate } from '../types/seedtrays'
import { csrfPost, fetchAsJson } from '../utils'

function getSeedTrayModels(signal?: AbortSignal): Promise<Array<SeedTrayModel>> {
  return fetchAsJson<Array<SeedTrayModel>>('/seedtrays/seedtraymodels/', signal)
}

function addSeedTrayModel(model: SeedTrayModelCreate) {
  return csrfPost('/seedtrays/seedtraymodels/', model)
}

function getSeedTrays(signal?: AbortSignal): Promise<Array<SeedTray>> {
  return fetchAsJson<Array<SeedTray>>('/seedtrays/seedtrays/', signal)
}

function addSeedTray(tray: SeedTrayCreate) {
  return csrfPost('/seedtrays/seedtrays/', tray)
}

function getSeedTrayCells(trayPk: number, signal?: AbortSignal): Promise<Array<SeedTrayCell>> {
  return fetchAsJson<Array<SeedTrayCell>>(`/seedtrays/seedtrays/${trayPk}/cells/`, signal)
}

export { getSeedTrayModels, getSeedTrays, addSeedTrayModel, addSeedTray, getSeedTrayCells }
