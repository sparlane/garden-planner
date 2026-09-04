import { BusinessExpense, ExpenseCategory, PurchaseOrder, PurchaseRequisition, PurchasingSummary, SupplierInvoice } from '../types/purchasing'
import { csrfPost, csrfPut, fetchAsJson } from '../utils'

const ROOT = '/purchasing/'

function listResource<T>(resource: string, signal?: AbortSignal): Promise<Array<T>> {
  return fetchAsJson<Array<T>>(`${ROOT}${resource}/`, signal)
}

async function postJson<T>(url: string, data: object): Promise<T> {
  const response = await csrfPost(url, data)
  return response.json() as Promise<T>
}

async function putJson<T>(url: string, data: object): Promise<T> {
  const response = await csrfPut(url, data)
  return response.json() as Promise<T>
}

const getRequisitions = (signal?: AbortSignal) => listResource<PurchaseRequisition>('requisitions', signal)
const getPurchaseOrders = (signal?: AbortSignal) => listResource<PurchaseOrder>('orders', signal)
const getSupplierInvoices = (signal?: AbortSignal) => listResource<SupplierInvoice>('invoices', signal)
const getExpenseCategories = (signal?: AbortSignal) => listResource<ExpenseCategory>('expense-categories', signal)
const getBusinessExpenses = (signal?: AbortSignal) => listResource<BusinessExpense>('expenses', signal)
const getPurchasingSummary = (signal?: AbortSignal) => fetchAsJson<PurchasingSummary>(`${ROOT}summary/`, signal)

const createRequisition = (data: object) => postJson<PurchaseRequisition>(`${ROOT}requisitions/`, data)
const reviewRequisition = (pk: number) => postJson<PurchaseRequisition>(`${ROOT}requisitions/${pk}/review/`, {})
const createPurchaseOrder = (data: object) => postJson<PurchaseOrder>(`${ROOT}orders/`, data)
const confirmPurchaseOrder = (pk: number) => postJson<PurchaseOrder>(`${ROOT}orders/${pk}/confirm/`, {})
const matchPurchaseReceipt = (pk: number, data: object) => postJson(`${ROOT}orders/${pk}/match-receipt/`, data)
const createSupplierInvoice = (data: object) => postJson<SupplierInvoice>(`${ROOT}invoices/`, data)
// Replaces every line and editable header value on a draft, which is what lets a
// rejected confirmation be retried against the invoice number already taken.
const replaceSupplierInvoiceDraft = (pk: number, data: object) => putJson<SupplierInvoice>(`${ROOT}invoices/${pk}/`, data)
const confirmSupplierInvoice = (pk: number) => postJson<SupplierInvoice>(`${ROOT}invoices/${pk}/confirm/`, {})
const correctSupplierInvoice = (pk: number, data: object) => postJson(`${ROOT}invoices/${pk}/correct/`, data)
const createSupplierPayment = (data: object) => postJson(`${ROOT}payments/`, data)
const createExpenseCategory = (data: object) => postJson<ExpenseCategory>(`${ROOT}expense-categories/`, data)
const createBusinessExpense = (data: object) => postJson<BusinessExpense>(`${ROOT}expenses/`, data)
const confirmBusinessExpense = (pk: number) => postJson<BusinessExpense>(`${ROOT}expenses/${pk}/confirm/`, {})

export {
  confirmBusinessExpense,
  confirmPurchaseOrder,
  confirmSupplierInvoice,
  correctSupplierInvoice,
  createBusinessExpense,
  createExpenseCategory,
  createPurchaseOrder,
  createRequisition,
  createSupplierInvoice,
  createSupplierPayment,
  getBusinessExpenses,
  getExpenseCategories,
  getPurchaseOrders,
  getPurchasingSummary,
  getRequisitions,
  getSupplierInvoices,
  matchPurchaseReceipt,
  replaceSupplierInvoiceDraft,
  reviewRequisition
}
