import { fetchAsJson, csrfDelete, csrfPatch, csrfPost } from '../utils'
import { ApplicationPreview, InputApplication, InputApplicationCreate, InputApplicationFilters, PostApplication, ReverseApplication } from '../types/applications'

const ROOT = '/applications/input-applications/'

function applicationQuery(filters: InputApplicationFilters): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') {
      query.set(key, String(value))
    }
  }
  return query.size > 0 ? `?${query.toString()}` : ''
}

function getInputApplications(filters: InputApplicationFilters = {}, signal?: AbortSignal): Promise<Array<InputApplication>> {
  return fetchAsJson<Array<InputApplication>>(`${ROOT}${applicationQuery(filters)}`, signal)
}

function getInputApplication(applicationPk: number, signal?: AbortSignal): Promise<InputApplication> {
  return fetchAsJson<InputApplication>(`${ROOT}${applicationPk}/`, signal)
}

function previewInputApplication(applicationPk: number, signal?: AbortSignal): Promise<ApplicationPreview> {
  return fetchAsJson<ApplicationPreview>(`${ROOT}${applicationPk}/preview/`, signal)
}

function addInputApplication(data: InputApplicationCreate): Promise<InputApplication> {
  return csrfPost(ROOT, data).then((response) => response.json() as Promise<InputApplication>)
}

function updateInputApplication(applicationPk: number, data: Partial<InputApplicationCreate>): Promise<InputApplication> {
  return csrfPatch(`${ROOT}${applicationPk}/`, data).then((response) => response.json() as Promise<InputApplication>)
}

function deleteInputApplication(applicationPk: number): Promise<Response> {
  return csrfDelete(`${ROOT}${applicationPk}/`)
}

function postInputApplication(applicationPk: number, data: PostApplication = {}): Promise<InputApplication> {
  return csrfPost(`${ROOT}${applicationPk}/post/`, data).then((response) => response.json() as Promise<InputApplication>)
}

function reverseInputApplication(applicationPk: number, data: ReverseApplication): Promise<InputApplication> {
  return csrfPost(`${ROOT}${applicationPk}/reverse/`, data).then((response) => response.json() as Promise<InputApplication>)
}

export {
  addInputApplication,
  deleteInputApplication,
  getInputApplication,
  getInputApplications,
  postInputApplication,
  previewInputApplication,
  reverseInputApplication,
  updateInputApplication
}
