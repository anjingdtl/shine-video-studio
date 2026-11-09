/** One explicit production-profile choice per project, shared by both panels.
 *
 * The production page and the director-package page used to keep separate
 * local state: a profile chosen on one page could be replaced by the other
 * page's initial value.  The person's choice is the same fact on both pages,
 * so it lives in one project-scoped place; the server still validates it.
 */
const KEY = (projectId: string) => `shine.productionProfile.${projectId}`;

export function storedProductionProfile(projectId: string): string {
  try { return localStorage.getItem(KEY(projectId)) ?? ''; } catch { return ''; }
}

export function rememberProductionProfile(projectId: string, profileId: string): void {
  try {
    if (profileId) localStorage.setItem(KEY(projectId), profileId);
    else localStorage.removeItem(KEY(projectId));
  } catch {
    /* a private browser may refuse storage; the page still works this session */
  }
}
