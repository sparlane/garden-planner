import { SeedTray, SeedTrayCell, SeedTrayCreate, SeedTrayModel, SeedTrayModelCreate } from '../types/seedtrays'
import { csrfPost, fetchAsJson } from '../utils'

function getSeedTrayModels(): Promise<Array<SeedTrayModel>> {
  return fetchAsJson<Array<SeedTrayModel>>('/seedtrays/seedtraymodels/')
}

function addSeedTrayModel(model: SeedTrayModelCreate) {
  return csrfPost('/seedtrays/seedtraymodels/', model)
}

function getSeedTrays(): Promise<Array<SeedTray>> {
  return fetchAsJson<Array<SeedTray>>('/seedtrays/seedtrays/')
}

function addSeedTray(tray: SeedTrayCreate) {
  return csrfPost('/seedtrays/seedtrays/', tray)
}

function getSeedTrayCells(trayPk: number): Promise<Array<SeedTrayCell>> {
  return fetchAsJson<Array<SeedTrayCell>>(`/seedtrays/seedtrays/${trayPk}/cells/`)
}

export { getSeedTrayModels, getSeedTrays, addSeedTrayModel, addSeedTray, getSeedTrayCells }
