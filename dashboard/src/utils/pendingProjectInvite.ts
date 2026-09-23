const KEY = "xiongbao:pending-project-invite";

/** Keep the bearer link in this tab while the user completes login. */
export function rememberProjectInviteFromLocation(
  pathname: string,
  search: string,
): void {
  if (pathname !== "/projects") return;
  const token = new URLSearchParams(search).get("invite")?.trim();
  if (!token || token.length > 200) return;
  sessionStorage.setItem(KEY, token);
}

export function clearPendingProjectInvite(): void {
  sessionStorage.removeItem(KEY);
}

/** Consume once so a later unrelated login does not replay an old invitation. */
export function consumePendingProjectInviteDestination(): string | null {
  const token = sessionStorage.getItem(KEY);
  clearPendingProjectInvite();
  return token ? `/projects?invite=${encodeURIComponent(token)}` : null;
}
