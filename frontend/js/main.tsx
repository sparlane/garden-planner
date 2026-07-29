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
import { NoProps } from './types/others.js'

interface FrontEndPageState {
  selectedView: string
}

class FrontEndPage extends React.Component<NoProps, FrontEndPageState> {
  constructor(props: NoProps) {
    super(props)

    this.state = {
      selectedView: 'gardens'
    }

    this.updateSelectedView = this.updateSelectedView.bind(this)
  }

  updateSelectedView(view: string) {
    this.setState({
      selectedView: view
    })
  }

  render() {
    let view = <></>
    if (this.state.selectedView === 'plants') {
      view = <PlantsView />
    } else if (this.state.selectedView === 'seeds-supplier') {
      view = <SeedSuppliersTable />
    } else if (this.state.selectedView === 'seeds-seed') {
      view = <SeedTable />
    } else if (this.state.selectedView === 'seeds-stock') {
      view = <SeedStockTable />
    } else if (this.state.selectedView === 'seedtrays-models') {
      view = <SeedTrayModelsTable />
    } else if (this.state.selectedView === 'seedtrays') {
      view = <SeedTraysTable />
    } else if (this.state.selectedView === 'planting-seedtrays') {
      view = <SeedTrayPlantingTable />
    } else if (this.state.selectedView === 'planting-gardensquare') {
      view = <GardenSquarePlantingTable />
    } else {
      view = <GardenDisplay />
    }
    return (
      <>
        <GPTopBar setView={this.updateSelectedView} />
        {view}
      </>
    )
  }
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
