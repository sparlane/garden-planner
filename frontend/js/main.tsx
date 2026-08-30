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
import { GardenSetup } from './garden/setup.js'
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
import { StocktakeDetailView, StocktakeListView } from './inventory/stocktakes.js'
import { InputApplicationsView } from './applications/applications.js'
import { ProductionBatchDetailView, ProductionBatchTable } from './plantings/batches.js'
import { HarvestsView, YieldReportView } from './plantings/harvests.js'
import { NurseryRegisterView } from './plantings/register.js'
import { GardenRegisterDetailView, GardenRegisterView } from './plantings/garden_register.js'
import { PlantDetailView } from './plantings/plant_detail.js'
import { CohortDetailView, CohortRegisterView } from './plantings/cohorts.js'
import { GrowthCatalogsView } from './plantings/growth_catalogs.js'
import { ProductionPlanningView } from './plantings/production_planning.js'
import { LabelsView, ScannerView } from './labels.js'
import { WorkQueueView } from './work.js'
import { HealthView } from './health.js'
import { CustomerListView, SalesOrderDetailView, SalesOrderListView } from './sales.js'
import { SupplyDocumentListView, SupplyDocumentPrintView } from './billing/documents.js'
import { ReportsView } from './reports.js'
import { PurchasingView } from './purchasing.js'
import { BookkeepingView } from './bookkeeping.js'

function SeedTrayDetailsRoute() {
  const { trayId } = useParams()
  const seedTrayPk = Number(trayId)

  if (!trayId || !Number.isInteger(seedTrayPk) || seedTrayPk <= 0) {
    return <div>Seed tray not found.</div>
  }

  return <SeedTrayDetails key={seedTrayPk} seedTrayPk={seedTrayPk} />
}

function ProductionBatchDetailRoute({ workspace }: { workspace: Workspace }) {
  const { batchId } = useParams()
  const batchPk = Number(batchId)

  if (!batchId || !Number.isInteger(batchPk) || batchPk <= 0) {
    return <div>Batch not found.</div>
  }

  return <ProductionBatchDetailView batchPk={batchPk} workspace={workspace} />
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

function SalesOrderDetailRoute({ workspace }: { workspace: Workspace }) {
  const { orderId } = useParams()
  const orderPk = Number(orderId)
  if (!orderId || !Number.isInteger(orderPk) || orderPk <= 0) return <div>Sales order not found.</div>
  return <SalesOrderDetailView orderPk={orderPk} workspace={workspace} />
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
        <Route path="/gardens" element={<GardenDisplay workspace={workspace} />} />
        <Route path="/gardens/:areaId" element={<GardenDisplay workspace={workspace} />} />
        {/* Ungated: laying out ground matters in both profiles, and a nursery
            with no geometry is as stuck as a garden with none. */}
        <Route path="/setup" element={<GardenSetup workspace={workspace} />} />
        <Route path="/setup/:areaId" element={<GardenSetup workspace={workspace} />} />
        <Route path="/plants" element={<PlantsView />} />
        <Route path="/seeds/suppliers" element={<SeedSuppliersTable />} />
        <Route path="/seeds" element={<SeedTable />} />
        <Route path="/seeds/stock" element={<SeedStockTable workspace={workspace} />} />
        <Route path="/seedtrays/models" element={<SeedTrayModelsTable />} />
        <Route path="/seedtrays" element={<SeedTraysTable />} />
        <Route path="/seedtrays/:trayId" element={<SeedTrayDetailsRoute />} />
        <Route path="/plantings/batches" element={<ProductionBatchTable workspace={workspace} />} />
        <Route path="/plantings/batches/:batchId" element={<ProductionBatchDetailRoute workspace={workspace} />} />
        <Route path="/plantings/seedtrays" element={<SeedTrayPlantingTable workspace={workspace} />} />
        <Route path="/plantings/garden-squares" element={<GardenSquarePlantingTable workspace={workspace} />} />
        <Route
          path="/plantings/register"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <NurseryRegisterView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/plantings/garden-register"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['garden']}>
              <GardenRegisterView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/plantings/garden-register/:registerKey"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['garden']}>
              <GardenRegisterDetailView />
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
        <Route path="/inventory/stocktakes" element={<StocktakeListView />} />
        <Route path="/inventory/stocktakes/:stocktakeId" element={<StocktakeDetailView />} />
        <Route path="/purchasing" element={<PurchasingView workspace={workspace} />} />
        <Route
          path="/bookkeeping"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <BookkeepingView workspace={workspace} />
            </WorkspaceModeRoute>
          }
        />
        <Route path="/applications" element={<InputApplicationsView />} />
        <Route
          path="/reports"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <ReportsView page="dashboard" />
            </WorkspaceModeRoute>
          }
        />
        {(['inventory', 'production', 'germination', 'orders', 'profitability', 'traceability', 'gst'] as const).map((page) => (
          <Route
            key={page}
            path={`/reports/${page}`}
            element={
              <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
                <ReportsView page={page} />
              </WorkspaceModeRoute>
            }
          />
        ))}
        <Route
          path="/sales/orders"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <SalesOrderListView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/sales/orders/:orderId"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <SalesOrderDetailRoute workspace={workspace} />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/sales/documents"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <SupplyDocumentListView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/sales/documents/:documentId"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <SupplyDocumentPrintView />
            </WorkspaceModeRoute>
          }
        />
        <Route
          path="/sales/customers"
          element={
            <WorkspaceModeRoute workspace={workspace} enabledModes={['nursery']}>
              <CustomerListView />
            </WorkspaceModeRoute>
          }
        />
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
