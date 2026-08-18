import { Workspace, WorkspaceUpdate } from '../types/workspace'
import { csrfPatch, fetchAsJson } from '../utils'

const WORKSPACE_URL = '/settings/workspace/'

function getWorkspace(signal?: AbortSignal): Promise<Workspace> {
  return fetchAsJson<Workspace>(WORKSPACE_URL, signal)
}

// PATCH, so a caller sends only what it is changing. The settings screen sends
// the whole profile; the setup wizard sends one field at a time.
async function updateWorkspace(update: Partial<WorkspaceUpdate>): Promise<Workspace> {
  const response = await csrfPatch(WORKSPACE_URL, update)
  return response.json() as Promise<Workspace>
}

export { getWorkspace, updateWorkspace }
