import { QueryClient } from '@tanstack/react-query'

const queryKeys = {
  workspace: {
    all: ['workspace'] as const,
    current: ['workspace', 'current'] as const
  },
  inventory: {
    all: ['inventory'] as const,
    units: ['inventory', 'units'] as const,
    locations: ['inventory', 'locations'] as const,
    items: (search: string, category: string, trackingMode: string, status: string) => ['inventory', 'items', search, category, trackingMode, status] as const,
    conversions: (itemPk: number) => ['inventory', 'conversions', itemPk] as const
  },
  garden: {
    all: ['garden'] as const,
    areas: ['garden', 'areas'] as const,
    beds: ['garden', 'beds'] as const,
    rows: ['garden', 'rows'] as const,
    squares: ['garden', 'squares'] as const
  },
  plants: {
    all: ['plants'] as const,
    families: ['plants', 'families'] as const,
    plants: ['plants', 'plants'] as const,
    varieties: ['plants', 'varieties'] as const
  },
  suppliers: {
    all: ['suppliers'] as const
  },
  seeds: {
    all: ['seeds'] as const,
    catalog: ['seeds', 'catalog'] as const,
    packets: {
      all: ['seeds', 'packets'] as const,
      raw: ['seeds', 'packets', 'raw'] as const,
      current: ['seeds', 'packets', 'current'] as const
    },
    packetReceipts: ['seeds', 'packetReceipts'] as const
  },
  seedTrays: {
    all: ['seedTrays'] as const,
    models: ['seedTrays', 'models'] as const,
    trays: ['seedTrays', 'trays'] as const,
    movements: (unitPk: number) => ['seedTrays', 'movements', unitPk] as const,
    cells: (trayPk: number) => ['seedTrays', 'cells', trayPk] as const
  },
  plantings: {
    all: ['plantings'] as const,
    batchesAll: ['plantings', 'batches'] as const,
    batches: (status: string, variety: number | '', code: string, needsRepair: boolean) => ['plantings', 'batches', status, variety, code, needsRepair] as const,
    batch: (batchPk: number) => ['plantings', 'batches', 'detail', batchPk] as const,
    directGardenRows: ['plantings', 'directGardenRows'] as const,
    directGardenSquares: ['plantings', 'directGardenSquares'] as const,
    transplantedGardenSquares: ['plantings', 'transplantedGardenSquares'] as const,
    seedTrays: ['plantings', 'seedTrays'] as const,
    seedTray: (trayPk: number) => ['plantings', 'seedTrays', trayPk] as const,
    currentSeedTrays: ['plantings', 'currentSeedTrays'] as const,
    currentGardenSquares: ['plantings', 'currentGardenSquares'] as const,
    specificPlantsAll: ['plantings', 'specificPlants'] as const,
    specificPlants: (trayPk: number) => ['plantings', 'specificPlants', trayPk] as const,
    plantLifecycleAll: ['plantings', 'plantLifecycle'] as const,
    plantLifecycle: (plantPk: number) => ['plantings', 'plantLifecycle', plantPk] as const,
    harvestsAll: ['plantings', 'harvests'] as const,
    harvests: (batch: number | '', variety: number | '', square: number | '', row: number | '', status: string, from: string, to: string) =>
      ['plantings', 'harvests', batch, variety, square, row, status, from, to] as const,
    harvest: (harvestPk: number) => ['plantings', 'harvests', 'detail', harvestPk] as const,
    harvestReportAll: ['plantings', 'harvestReport'] as const,
    harvestReport: (groupBy: string, batch: number | '', variety: number | '', from: string, to: string) =>
      ['plantings', 'harvestReport', groupBy, batch, variety, from, to] as const
  },
  applications: {
    all: ['applications'] as const,
    listAll: ['applications', 'list'] as const,
    list: (status: string, batch: number | '', item: number | '', from: string, to: string) => ['applications', 'list', status, batch, item, from, to] as const,
    detail: (applicationPk: number) => ['applications', 'detail', applicationPk] as const,
    preview: (applicationPk: number) => ['applications', 'preview', applicationPk] as const
  }
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 0,
      refetchOnWindowFocus: true,
      retry: false
    }
  }
})

export { queryClient, queryKeys }
