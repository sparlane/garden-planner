import 'bootstrap'
import 'bootstrap/dist/css/bootstrap.css'

import React from 'react'
import * as ReactDOM from 'react-dom/client'
import { QueryClientProvider, useQuery } from '@tanstack/react-query'
import { HashRouter, Navigate, Route, Routes, useParams } from 'react-router'

import { GPTopBar } from './menu.js'
import { PlantsView } from './plants.js'
import { SeedStockTable, SeedSuppliersTable, SeedTable } from './seeds.js'
import { GardenSquarePlantingTable, SeedTrayPlantingTable } from './planting.js'
import { GardenDisplay } from './garden.js'
import { SeedTrayModelsTable, SeedTraysTable } from './seedtrays.js'
import { ApiErrorAlert } from './api_error_alert.js'
import { queryClient, queryKeys } from './query.js'
import { SeedTrayDetails } from './seedtray/seedtray_details.js'
import { getWorkspace } from './api/workspace.js'
import { Workspace } from './types/workspace.js'
import { WorkspaceModeRoute, WorkspaceSettings } from './workspace.js'
import { InventoryCatalog } from './inventory.js'
import { LocationsCatalog } from './locations.js'
import { InventoryReceiptsView } from './inventory/receipts.js'
import { InputApplicationsView } from './applications/applications.js'
import { ProductionBatchDetailView, ProductionBatchTable } from './plantings/batches.js'
import { HarvestsView, YieldReportView } from './plantings/harvests.js'
import { NurseryRegisterView } from './plantings/register.js'
import { PlantDetailView } from './plantings/plant_detail.js'
import { CohortDetailView, CohortRegisterView } from './plantings/cohorts.js'
import { GrowthCatalogsView } from './plantings/growth_catalogs.js'
import { ProductionPlanningView } from './plantings/production_planning.js'
import { LabelsView, ScannerView } from './labels.js'
import { WorkQueueView } from './work.js'
import { HealthView } from './health.js'

function SeedTrayDetailsRoute() {
  const { trayId } = useParams()
  const seedTrayPk = Number(trayId)

  if (!trayId || !Number.isInteger(seedTrayPk) || seedTrayPk <= 0) {
    return <div>Seed tray not found.</div>
  }

  return <SeedTrayDetails seedTrayPk={seedTrayPk} />
}

function ProductionBatchDetailRoute() {
  const { batchId } = useParams()
  const batchPk = Number(batchId)

  if (!batchId || !Number.isInteger(batchPk) || batchPk <= 0) {
    return <div>Batch not found.</div>
  }

  return <ProductionBatchDetailView batchPk={batchPk} />
}

function PlantDetailRoute({ workspace }: { workspace: Workspace }) {
  const { plantId } = useParams()
  const plantPk = Number(plantId)

  if (!plantId || !Number.isInteger(plantPk) || plantPk <= 0) {
    return <div>Plant not found.</div>
  }

  return <PlantDetailView plantPk={plantPk} workspace={workspace} />
}

function CohortDetailRoute() {
  const { cohortId } = useParams()
  const cohortPk = Number(cohortId)
  if (!cohortId || !Number.isInteger(cohortPk) || cohortPk <= 0) return <div>Cohort not found.</div>
  return <CohortDetailView cohortPk={cohortPk} />
}

function FrontEndPage() {
  const { data: workspace, isPending } = useQuery({
    queryKey: queryKeys.workspace.current,
    queryFn: ({ signal }) => getWorkspace(signal)
  })

  if (isPending) {
    return <div className="container py-3">Loading workspace…</div>
  }
  if (!workspace) {
    return (
      <div className="container py-3">
        <ApiErrorAlert />
        <div>Workspace settings are unavailable.</div>
      </div>
    )
  }

  return (
    <>
      <GPTopBar workspace={workspace} />
      <ApiErrorAlert />
      <Routes>
        <Route path="/gardens" element={<GardenDisplay />} />
        <Route path="/gardens/:areaId" element={<GardenDisplay />} />
        <Route path="/plants" element={<PlantsView />} />
        <Route path="/seeds/suppliers" element={<SeedSuppliersTable />} />
        <Route path="/seeds" element={<SeedTable />} />
        <Route path="/seeds/stock" element={<SeedStockTable />} />
        <Route path="/seedtrays/models" element={<SeedTrayModelsTable />} />
        <Route path="/seedtrays" element={<SeedTraysTable />} />
        <Route path="/seedtrays/:trayId" element={<SeedTrayDetailsRoute />} />
        <Route path="/plantings/batches" element={<ProductionBatchTable />} />
        <Route path="/plantings/batches/:batchId" element={<ProductionBatchDetailRoute />} />
        <Route path="/plantings/seedtrays" element={<SeedTrayPlantingTable />} />
        <Route path="/plantings/garden-squares" element={<GardenSquarePlantingTable />} />
        <Route
          path="/plantings/register"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <NurseryRegisterView />
            </WorkspaceModeRoute>
          }
        />
        <Route path="/plantings/plants/:plantId" element={<PlantDetailRoute workspace={workspace} />} />
        <Route
          path="/plantings/cohorts"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <CohortRegisterView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/plantings/cohorts/:cohortId"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <CohortDetailRoute />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/plantings/growth-setup"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <GrowthCatalogsView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/plantings/production-planning"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <ProductionPlanningView />
            </WorkspaceModeRoute>
          }
        />
        <Route path="/plantings/harvests" element={<HarvestsView />} />
        <Route path="/plantings/yield" element={<YieldReportView />} />
        <Route path="/locations" element={<LocationsCatalog />} />
        <Route path="/inventory" element={<InventoryCatalog />} />
        <Route path="/inventory/receipts" element={<InventoryReceiptsView />} />
        <Route path="/applications" element={<InputApplicationsView />} />
        <Route path="/labels" element={<LabelsView />} />
        <Route path="/scan" element={<ScannerView />} />
        <Route path="/scan/:code" element={<ScannerView />} />
        <Route
          path="/health"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <HealthView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/work"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <WorkQueueView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/settings"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['garden', 'nursery']}>
              <WorkspaceSettings workspace={workspace} />
            </WorkspaceModeRoute>
          }
        />
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
