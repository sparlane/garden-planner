interface SeedTrayModelCreate {
  identifier: string
  description: string
  height: number
  x_size: number
  y_size: number
  x_cells: number
  y_cells: number
  cell_size_ml: number
}
interface SeedTrayModel extends SeedTrayModelCreate {
  pk: number
}

interface SeedTrayCreate {
  model?: number
  notes?: string
}

interface SeedTray extends SeedTrayCreate {
  pk: number
  created: string
}

interface SeedTrayCell {
  pk: number
  tray: number
  x_position: number
  y_position: number
}

export { SeedTrayModel, SeedTray, SeedTrayCell, SeedTrayModelCreate, SeedTrayCreate }
