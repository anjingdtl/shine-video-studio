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

/** 导演包选定版本也是同一份项目级事实：切页/重挂载后仍回到人选的那一版，
 *  而不是每次跳回最新版。版本台账本身在服务端（director_package_versions），
 *  这里只记住「人在看哪一版」这一个选择。 */
const PACKAGE_KEY = (projectId: string) => `shine.directorPackage.${projectId}`;

export function storedDirectorPackage(projectId: string): string {
  try { return localStorage.getItem(PACKAGE_KEY(projectId)) ?? ''; } catch { return ''; }
}

export function rememberDirectorPackage(projectId: string, packageId: string): void {
  try {
    if (packageId) localStorage.setItem(PACKAGE_KEY(projectId), packageId);
    else localStorage.removeItem(PACKAGE_KEY(projectId));
  } catch {
    /* a private browser may refuse storage; the page still works this session */
  }
}

/** 拍摄方案选择（模型/加速/画幅/分辨率/二次精修/超分）也是项目级事实：
 *  导演包页切走再回来（组件重挂载）仍恢复人选的配置，而不是每次回默认。
 *  服务端开拍时仍做最终裁决，这里只记住人的选择。 */
const SHOOT_KEY = (projectId: string) => `shine.shootStrategy.${projectId}`;

export function storedShootStrategy(projectId: string): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(SHOOT_KEY(projectId));
    return raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
  } catch { return null; }
}

export function rememberShootStrategy(projectId: string, strategy: Record<string, unknown>): void {
  try { localStorage.setItem(SHOOT_KEY(projectId), JSON.stringify(strategy)); } catch {
    /* a private browser may refuse storage; the page still works this session */
  }
}
