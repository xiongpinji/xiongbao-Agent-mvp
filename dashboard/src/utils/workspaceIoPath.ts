/**
 * Shared path helpers for dashboard → workspace download / file / preview APIs.
 *
 * Rule: host-absolute paths stay absolute (``file://…``) so
 * ``BackendWorkspace`` materialize failback can find virtual ``root_dir`` nests.
 * Only canonicalize for dock tab keys / list identity — not for I/O URLs.
 */

/** Normalize ``file://`` and backslashes; keep abs vs relative shape. */
export function normalizeIoPath(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return "";
  if (trimmed.toLowerCase().startsWith("file://")) {
    let abs = trimmed.slice("file://".length);
    if (abs.startsWith("//")) abs = abs.slice(1);
    return abs.startsWith("/") || /^[A-Za-z]:/.test(abs) ? abs : `/${abs}`;
  }
  return trimmed.replace(/\\/g, "/");
}

/**
 * True for host filesystem absolutes (``/…``, ``file://``, drive letter).
 * Leading ``/outbound`` / ``/inbound`` are workspace keys, not host abs.
 */
export function isHostAbsolutePath(path: string): boolean {
  const raw = path.trim().replace(/\\/g, "/");
  if (!raw) return false;
  if (raw.toLowerCase().startsWith("file://")) return true;
  if (
    raw === "/outbound" ||
    raw === "/inbound" ||
    raw.startsWith("/outbound/") ||
    raw.startsWith("/inbound/")
  ) {
    return false;
  }
  if (/^[A-Za-z]:/.test(raw) || raw.startsWith("\\\\")) return true;
  return raw.startsWith("/");
}

/**
 * Strip only the virtual root prefix ``/workspace/…``.
 * Does not touch host paths that contain a ``workspace`` directory segment.
 */
export function stripVirtualWorkspaceRoot(posix: string): string {
  const p = posix.replace(/\\/g, "/");
  if (p === "/workspace") return "";
  if (p.startsWith("/workspace/")) return p.slice("/workspace/".length);
  return p;
}

/**
 * Path form for agent workspace download / file APIs
 * (absolute → ``file://``, legacy ``/outbound`` → relative).
 */
export function toWorkspaceApiPath(resolvedPath: string): string {
  const raw = resolvedPath.trim();
  if (!raw) return raw;
  if (raw.toLowerCase().startsWith("file://")) {
    return raw;
  }
  const posix = raw.replace(/\\/g, "/");
  if (
    posix.startsWith("/outbound/") ||
    posix.startsWith("/inbound/") ||
    posix === "/outbound" ||
    posix === "/inbound"
  ) {
    return posix.replace(/^\//, "");
  }
  if (posix.startsWith("/") || /^[A-Za-z]:/.test(raw)) {
    return posix.startsWith("/") ? `file://${posix}` : `file:///${posix}`;
  }
  return posix;
}

/**
 * Dock / chat download API path.
 *
 * Host-absolute tool paths are **not** collapsed to workspace-relative keys —
 * that would break virtual ``root_dir`` nests. Relative keys pass through.
 */
export function toDockWorkspaceApiPath(raw: string): string {
  const normalized = normalizeIoPath(raw);
  if (!normalized) return "";
  return toWorkspaceApiPath(normalized);
}

/**
 * Managed-root-relative key for an owner-private 030 project-task runtime
 * (true-mode ``from_workspace=true`` file I/O), or ``null`` when the input
 * cannot be proven safe.
 *
 * True-mode contract: a leading POSIX slash is a dashboard workspace-root key
 * confined under the managed root — never a host path. Refused **without**
 * collapsing (no substring matching of host paths, no filesystem access):
 * ``file:`` / URL schemes, Windows drive letters, UNC shares, ``~`` home
 * prefixes, ``..`` traversal segments and NUL bytes. The managed-relative key
 * is re-checked after virtual-root / leading-separator stripping, so a prefix
 * cannot smuggle in a ``file:`` URL, drive letter or ``~``; a relative name
 * that merely contains a colon (``my:file.txt``) is not a host shape and
 * stays accepted. The server remains the final authority; this only prevents
 * the UI from silently rewriting a refused shape into a private path or
 * firing a doomed request.
 */
export function toPrivateWorkspaceRelPath(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  if (trimmed.includes("\0")) return null;
  // Any URL scheme (``file:``, ``http:``, …) — also covers drive letters.
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(trimmed)) return null;
  if (trimmed.startsWith("~")) return null;
  const posix = trimmed.replace(/\\/g, "/");
  if (posix.startsWith("//")) return null; // UNC (after backslash normalize)
  if (posix.split("/").includes("..")) return null;
  const rel = stripVirtualWorkspaceRoot(posix).replace(/^\/+/, "");
  if (!rel) return null;
  // Host shapes that only surface once virtual-root / leading separators are
  // gone (``/file:…``, ``/C:…``, ``/~…``) must not become a private key.
  if (rel.startsWith("~")) return null;
  if (/^[A-Za-z]:/.test(rel)) return null;
  if (rel.toLowerCase().startsWith("file:")) return null;
  return rel;
}
