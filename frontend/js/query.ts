import { QueryClient } from '@tanstack/react-query'

const queryKeys = {
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
    }
  },
  seedTrays: {
    all: ['seedTrays'] as const,
    models: ['seedTrays', 'models'] as const,
    trays: ['seedTrays', 'trays'] as const,
    cells: (trayPk: number) => ['seedTrays', 'cells', trayPk] as const
  },
  plantings: {
    all: ['plantings'] as const,
    directGardenRows: ['plantings', 'directGardenRows'] as const,
    directGardenSquares: ['plantings', 'directGardenSquares'] as const,
    transplantedGardenSquares: ['plantings', 'transplantedGardenSquares'] as const,
    seedTrays: ['plantings', 'seedTrays'] as const,
    seedTray: (trayPk: number) => ['plantings', 'seedTrays', trayPk] as const,
    currentSeedTrays: ['plantings', 'currentSeedTrays'] as const,
    currentGardenSquares: ['plantings', 'currentGardenSquares'] as const,
    specificPlants: (trayPk: number) => ['plantings', 'specificPlants', trayPk] as const
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
