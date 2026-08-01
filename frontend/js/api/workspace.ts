import { Workspace, WorkspaceUpdate } from '../types/workspace'
import { csrfPatch, fetchAsJson } from '../utils'

const WORKSPACE_URL = '/settings/workspace/'

function getWorkspace(signal?: AbortSignal): Promise<Workspace> {
  return fetchAsJson<Workspace>(WORKSPACE_URL, signal)
}

async function updateWorkspace(update: WorkspaceUpdate): Promise<Workspace> {
  const response = await csrfPatch(WORKSPACE_URL, update)
  return response.json() as Promise<Workspace>
}

export { getWorkspace, updateWorkspace }
