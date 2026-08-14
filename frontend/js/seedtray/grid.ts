import { SeedTrayCell, SeedTrayModel } from '../types/seedtrays'

function buildSeedTrayCellGrid(model: SeedTrayModel | undefined, cells: Array<SeedTrayCell>): (SeedTrayCell | undefined)[][] {
  if (!model) return []
  const grid: (SeedTrayCell | undefined)[][] = Array.from({ length: model.y_cells }, () => Array.from({ length: model.x_cells }, () => undefined))
  cells.forEach((cell) => {
    if (cell.x_position < 0 || cell.x_position >= model.x_cells) return
    if (cell.y_position < 0 || cell.y_position >= model.y_cells) return
    if (grid[cell.y_position][cell.x_position] === undefined) {
      grid[cell.y_position][cell.x_position] = cell
    }
  })
  return grid
}

export { buildSeedTrayCellGrid }
