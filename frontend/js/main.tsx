import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import * as ReactDOM from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { HashRouter, Navigate, Route, Routes } from 'react-router'

import { GPTopBar } from './menu.js'
import { PlantsView } from './plants.js'
import { SeedStockTable, SeedSuppliersTable, SeedTable } from './seeds.js'
import { GardenSquarePlantingTable, SeedTrayPlantingTable } from './planting.js'
import { GardenDisplay } from './garden.js'
import { SeedTrayModelsTable, SeedTraysTable } from './seedtrays.js'
import { ApiErrorAlert } from './api_error_alert.js'
import { queryClient } from './query.js'

function FrontEndPage() {
  return (
    <>
      <GPTopBar />
      <ApiErrorAlert />
      <Routes>
        <Route path="/gardens" element={<GardenDisplay />} />
        <Route path="/plants" element={<PlantsView />} />
        <Route path="/seeds/suppliers" element={<SeedSuppliersTable />} />
        <Route path="/seeds" element={<SeedTable />} />
        <Route path="/seeds/stock" element={<SeedStockTable />} />
        <Route path="/seedtrays/models" element={<SeedTrayModelsTable />} />
        <Route path="/seedtrays" element={<SeedTraysTable />} />
        <Route path="/plantings/seedtrays" element={<SeedTrayPlantingTable />} />
        <Route path="/plantings/garden-squares" element={<GardenSquarePlantingTable />} />
        <Route path="*" element={<Navigate to="/gardens" replace />} />
      </Routes>
    </>
  )
}

function createFrontEnd(elementId: string) {
  const element = document.getElementById(elementId)
  if (!element) {
    throw new Error(`Cannot create frontend: element #${elementId} was not found`)
  }
  const div = ReactDOM.createRoot(element)
  div.render(
    <QueryClientProvider client={queryClient}>
      <HashRouter>
        <FrontEndPage />
      </HashRouter>
    </QueryClientProvider>
  )
}

;(globalThis as Record<string, unknown>).createFrontEnd = createFrontEnd
