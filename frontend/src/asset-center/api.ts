import { api, json } from '../api';

export type LibraryRights = {
  id: string; source_type: string; license_kind: string; scope: string; statement?: string;
  subject?: string; allowed_project_ids: string[]; permitted_uses: string[]; commercial_allowed: boolean;
  status: string; expires_at: string | null; revoked_at: string | null; snapshot_hash?: string;
};
export type LibraryVersion = {
  id: string; ordinal: number; parent_version_id: string | null; filename: string; kind: string;
  content_type: string; content_sha256: string; byte_size: number; width: number | null; height: number | null;
  duration_s: number | null; source_mode: string; prompt: string; workflow_revision_hash: string | null; rights_snapshot_hash?: string;
  references_manifest: Array<Record<string, unknown>>; rights: LibraryRights | null;
  provenance: Record<string, any>; qc: Record<string, any> | null; status: string; is_mock: boolean;
  media_url: string;
};
export type LibraryAsset = {
  id: string; title: string; kind: string; category: string; tags: string[]; collection: string | null;
  favorite: boolean; status: string; state_version: number; version_count: number;
  current_version: LibraryVersion | null; versions?: LibraryVersion[]; usage?: Array<Record<string, any>>;
};
export type LibraryTask = {
  id: string; kind: 'image' | 'voice'; mode: string; status: string; state_version: number; error_code: string | null;
  external_outcome: string; external_prompt_id: string | null; result_version_id: string | null;
  can_new_attempt?: boolean;
  capability: { state: string; can_generate?: boolean; reason?: string } | null; candidate_index: number | null;
  attempts: Array<Record<string, any>>; created_at: string; updated_at: string;
};
export type CapabilityReport = {
  workspaces: string[];
  image_capabilities: Record<string, { state: string; can_generate?: boolean; can_validate?: boolean; reason: string; max_total_images?: number }>;
  voice_capabilities: Record<string, { state: string; reason: string; can_generate?: boolean; can_queue?: boolean }>;
  t2i_runtime: Record<string, any>;
};

export function getCapabilities() {
  return api<CapabilityReport>('/asset-center/capabilities');
}
export function listLibraryAssets(q = '', category = '') {
  const params = new URLSearchParams({ q, limit: '100' });
  if (category) params.set('category', category);
  return api<{ items: LibraryAsset[]; limit: number; offset: number }>(`/asset-center/assets?${params}`);
}
export function getLibraryAsset(id: string) {
  return api<LibraryAsset>(`/asset-center/assets/${id}`);
}
export function createLibraryAsset(body: Record<string, unknown>) {
  return api<LibraryAsset>('/asset-center/assets', json('POST', body));
}
export function patchLibraryAsset(id: string, body: Record<string, unknown>) {
  return api<LibraryAsset>(`/asset-center/assets/${id}`, json('PATCH', body));
}
export function archiveLibraryAsset(id: string) {
  return api<LibraryAsset>(`/asset-center/assets/${id}/archive`, json('POST', {}));
}
export function uploadLibraryMedia(file: File, purpose: 'source' | 'reference' | 'mask' | 'audio_import' | 'voice_sample') {
  const form = new FormData();
  form.set('file', file);
  form.set('asset_kind', purpose === 'audio_import' || purpose === 'voice_sample' ? 'audio' : 'image');
  form.set('purpose', purpose);
  return api<Record<string, any>>('/asset-center/uploads', { method: 'POST', body: form });
}
export function createImageJobs(body: Record<string, unknown>) {
  return api<{ items: LibraryTask[]; capability: any; notice: string }>('/asset-center/image-jobs', json('POST', body));
}
export function createVoiceJob(body: Record<string, unknown>) {
  return api<{ item: LibraryTask; capability: any; notice: string }>('/asset-center/voice-jobs', json('POST', body));
}
export function getLibraryTask(id: string, kind: 'image' | 'voice' = 'image') {
  return api<LibraryTask>(`/asset-center/${kind}-jobs/${id}`);
}
export function reconcileLibraryImageTask(id: string) {
  return api<{ next_action?: string }>(`/asset-center/image-jobs/${id}/reconcile`, json('POST', { evidence: '用户在任务卡片中明确触发只读对账。' }));
}
export function newLibraryImageAttempt(id: string) {
  return api<LibraryTask>(`/asset-center/image-jobs/${id}/new-attempt`, json('POST', { confirmed: true, idempotency_key: crypto.randomUUID() }));
}
export function qcLibraryVersion(id: string) {
  return api<LibraryVersion>(`/asset-center/versions/${id}/qc`, json('POST', {}));
}
export function grantVersionPromotion(id: string) {
  return api<{ grant_id: string; snapshot: string }>(`/asset-center/versions/${id}/grant`, json('POST', {}));
}
export function promoteLibraryVersion(id: string, body: { grant_id: string; snapshot: string; evidence: string }) {
  return api<LibraryVersion>(`/asset-center/versions/${id}/promote`, json('POST', body));
}
export function listLibraryVoices() {
  return api<{ items: Array<Record<string, any>> }>('/asset-center/voice-profiles');
}
export function createVoiceFreezeGrant(id: string) {
  return api<{ grant_id: string; snapshot: string }>(`/asset-center/voice-profiles/${id}/grant`, json('POST', {}));
}
export function freezeLibraryVoice(id: string, body: Record<string, unknown>) {
  return api<Record<string, any>>(`/asset-center/voice-profiles/${id}/freeze`, json('POST', body));
}
export function assignLibraryVersion(body: Record<string, unknown>) {
  return api<Record<string, any>>('/asset-center/assignments', json('POST', body));
}
export function listAssignments(projectId?: string) {
  const query = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  return api<{ items: Array<Record<string, any>> }>(`/asset-center/assignments${query}`);
}
export function searchLibraryForProject(projectId: string, q: string, category?: string,
                                         scope: 'project' | 'library' = 'project') {
  const query = new URLSearchParams({ q, scope });
  if (category) query.set('category', category);
  return api<{ items: Array<{ asset: LibraryAsset; version: LibraryVersion }> }>(`/projects/${projectId}/asset-center/search?${query}`);
}
export function saveProjectAssetToLibrary(projectId: string, assetId: string, body: Record<string, unknown>) {
  return api<LibraryAsset>(`/projects/${projectId}/assets/${assetId}/save-to-library`, json('POST', body));
}
