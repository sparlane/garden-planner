import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import * as ReactDOM from 'react-dom/client'

import { GPTopBar } from './menu.js'
import { PlantsView } from './plants.js'
import { SeedStockTable, SeedSuppliersTable, SeedTable } from './seeds.js'
import { GardenSquarePlantingTable, SeedTrayPlantingTable } from './planting.js'
import { GardenDisplay } from './garden.js'
import { SeedTrayModelsTable, SeedTraysTable } from './seedtrays.js'
import { ApiErrorAlert } from './api_error_alert.js'

function FrontEndPage() {
  const [selectedView, setSelectedView] = React.useState('gardens')

  let view = <></>
  if (selectedView === 'plants') {
    view = <PlantsView />
  } else if (selectedView === 'seeds-supplier') {
    view = <SeedSuppliersTable />
  } else if (selectedView === 'seeds-seed') {
    view = <SeedTable />
  } else if (selectedView === 'seeds-stock') {
    view = <SeedStockTable />
  } else if (selectedView === 'seedtrays-models') {
    view = <SeedTrayModelsTable />
  } else if (selectedView === 'seedtrays') {
    view = <SeedTraysTable />
  } else if (selectedView === 'planting-seedtrays') {
    view = <SeedTrayPlantingTable />
  } else if (selectedView === 'planting-gardensquare') {
    view = <GardenSquarePlantingTable />
  } else {
    view = <GardenDisplay />
  }

  return (
    <>
      <GPTopBar setView={setSelectedView} />
      <ApiErrorAlert />
      {view}
    </>
  )
}

function createFrontEnd(elementId: string) {
  const element = document.getElementById(elementId)
  if (!element) {
    throw new Error(`Cannot create frontend: element #${elementId} was not found`)
  }
  const div = ReactDOM.createRoot(element)
  div.render(<FrontEndPage />)
}

;(globalThis as Record<string, unknown>).createFrontEnd = createFrontEnd
