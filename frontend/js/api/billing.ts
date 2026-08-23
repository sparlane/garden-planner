import { Invoiceable, PrintableDocument, SupplyCorrection, SupplyCorrectionWrite, SupplyDocument, SupplyDocumentWrite } from '../types/billing'
import { csrfPost, fetchAsJson } from '../utils'

const DOCUMENTS_URL = '/billing/supply-documents/'
const INVOICEABLE_URL = '/billing/invoiceable/'

function getSupplyDocuments(params: { order?: number } = {}, signal?: AbortSignal): Promise<Array<SupplyDocument>> {
  const query = params.order === undefined ? '' : `?order=${params.order}`
  return fetchAsJson<Array<SupplyDocument>>(`${DOCUMENTS_URL}${query}`, signal)
}

function getPrintableDocument(pk: number, signal?: AbortSignal): Promise<PrintableDocument> {
  return fetchAsJson<PrintableDocument>(`${DOCUMENTS_URL}${pk}/print/`, signal)
}

function getInvoiceable(order: number, signal?: AbortSignal): Promise<Invoiceable> {
  return fetchAsJson<Invoiceable>(`${INVOICEABLE_URL}${order}/`, signal)
}

// There is no update counterpart on purpose: an issued document is evidence of
// what was handed over, and the server refuses PATCH, PUT and DELETE.
// Correcting one means issuing a credit or debit note against it.
async function issueSupplyDocument(data: SupplyDocumentWrite): Promise<SupplyDocument> {
  const response = await csrfPost(DOCUMENTS_URL, data)
  return response.json() as Promise<SupplyDocument>
}

async function issueCorrection(pk: number, data: SupplyCorrectionWrite): Promise<SupplyCorrection> {
  const response = await csrfPost(`${DOCUMENTS_URL}${pk}/corrections/`, data)
  return response.json() as Promise<SupplyCorrection>
}

export { getInvoiceable, getPrintableDocument, getSupplyDocuments, issueCorrection, issueSupplyDocument }
