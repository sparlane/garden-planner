interface SeedTrayModel {
  pk: number
  identifier: string
  description: string
  height: number
  x_size: number
  y_size: number
  x_cells: number
  y_cells: number
  cell_size_ml: number
}

interface SeedTray {
  pk: number
  model: number
  created: string
  notes: string
}

interface SeedTrayCell {
  pk: number
  tray: number
  x_position: number
  y_position: number
}

export { SeedTrayModel, SeedTray, SeedTrayCell }
