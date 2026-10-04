import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { useTranslation } from "react-i18next";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import type { HitlSessionPolicy } from "../../../api/types/hitl";
import * as chatStore from "./chatStore";
import { onSessionEvent } from "./chatStore";
import { formatThreadTitle } from "../utils/threadTitle";
import { parseHitlSessionPolicy } from "../utils/hitlSessionPolicy";
import { showApiError } from "../../../utils/showApiToast";

export interface Session {
  id: string;
  name: string;
  threadId: string;
  updatedAt: string | null;
  channelType: string;
  isActive?: boolean;
  hasActivity?: boolean;
  pinned?: boolean;
  modelRef?: string | null;
  reasoningMode?: "auto" | "enabled" | "disabled" | null;
  reasoningEffort?: string | null;
  conversationMode?: "ask" | "plan" | "craft" | null;
  pendingPlanPath?: string | null;
  hitlPolicy?: HitlSessionPolicy | null;
  artifacts?: string[];
}

/** Result of probing whether a thread exists for the current agent. */
export type ThreadProbeResult = "found" | "missing" | "unknown";

export interface UseSessionsOptions {
  /**
   * Owner-private project-task runtime (`runtime_kind=project_task_files`).
   * Its existing threads are readable, but the dashboard must never create a
   * new thread for it and a list gap must never rewrite the owner's explicit
   * deep link into a blank chat.
   */
  internal?: boolean;
}

export function toSession(row: {
  thread_id: string;
  title: string | null;
  last_active: number;
  created_at?: number;
  channel_type?: string;
  is_active?: boolean;
  has_messages?: boolean;
  pinned?: boolean;
  model_ref?: string | null;
  reasoning_mode?: "auto" | "enabled" | "disabled" | null;
  reasoning_effort?: string | null;
  conversation_mode?: "ask" | "plan" | "craft" | null;
  pending_plan_path?: string | null;
  hitl_policy?: HitlSessionPolicy | null;
  artifacts?: string[] | null;
}): Session {
  const hasActivity =
    Boolean(row.has_messages) || Boolean(row.title) || row.last_active > 0;
  // Match backend list order: empty threads (last_active=0) sort by created_at.
  const sortTs =
    row.last_active > 0
      ? row.last_active
      : typeof row.created_at === "number" && row.created_at > 0
      ? row.created_at
      : 0;
  return {
    id: row.thread_id,
    name: formatThreadTitle(row.title) || "New Chat",
    threadId: row.thread_id,
    updatedAt: sortTs > 0 ? new Date(sortTs * 1000).toISOString() : null,
    channelType: row.channel_type ?? "dashboard",
    isActive: row.is_active ?? false,
    hasActivity,
    pinned: Boolean(row.pinned),
    modelRef: row.model_ref ?? null,
    reasoningMode: row.reasoning_mode ?? null,
    reasoningEffort: row.reasoning_effort ?? null,
    conversationMode: row.conversation_mode ?? null,
    pendingPlanPath: row.pending_plan_path ?? null,
    hitlPolicy: row.hitl_policy
      ? parseHitlSessionPolicy(row.hitl_policy)
      : null,
    artifacts: Array.isArray(row.artifacts)
      ? row.artifacts.filter(
          (path): path is string =>
            typeof path === "string" && path.trim().length > 0,
        )
      : [],
  };
}

/** Mirror server order: pinned first, then recency (empty chats use created_at). */
export function sortSessions(sessions: Session[]): Session[] {
  return [...sessions].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    const ta = a.updatedAt ? Date.parse(a.updatedAt) : 0;
    const tb = b.updatedAt ? Date.parse(b.updatedAt) : 0;
    if (tb !== ta) return tb - ta;
    return b.id.localeCompare(a.id);
  });
}

/** Pick the thread bound to the dashboard session_key, else best history candidate. */
export function pickPreferredSession(sessions: Session[]): Session | null {
  if (sessions.length === 0) return null;
  return (
    sessions.find((s) => s.isActive) ??
    sessions.find((s) => s.hasActivity && s.name !== "New Chat") ??
    sessions.find((s) => s.hasActivity) ??
    sessions.find((s) => s.name !== "New Chat") ??
    sessions[0]
  );
}

let _sessions: Session[] = [];
let _loading = true;
let _hasMore = false;
let _loadingMore = false;
export const SESSION_PAGE_SIZE = 10;
let _storeAgentId: string | null = null;
const _loadedLimitByAgent = new Map<string, number>();
const _listeners = new Set<() => void>();
let _storeGeneration = 0;
let _initialFetchGeneration = -1;
let _readSequence = 0;
let _publishedRead = 0;
let _loadingMoreRead = 0;

export type SessionMutationResult =
  | { status: "saved"; value: string | boolean }
  | { status: "failed" }
  | { status: "ignored"; reason: "busy" | "invalid" | "stale" | "restricted" };
type SessionField = "name" | "pinned";
let _sessionRevision = 0;
const _sessionWrites = new Map<string, { revision: number; token?: object }>();
const _deletedSessionIds = new Set<string>();

function sessionWriteKey(agentId: string, id: string, field: SessionField) {
  return JSON.stringify([agentId, id, field]);
}

export function sessionMutationPending(
  agentId: string,
  id: string,
  field: SessionField,
) {
  return Boolean(
    _sessionWrites.get(sessionWriteKey(agentId, id, field))?.token,
  );
}

export function forgetDeletedSession(id: string) {
  _deletedSessionIds.add(id);
}

export function captureSessionRevision() {
  return _sessionRevision;
}

/** Preserve only fields edited during a read (or still awaiting persistence). */
export function mergeSessionMetadata(
  agentId: string,
  incoming: Session[],
  current: Session[],
  revision: number,
  fieldIsCurrent: (id: string, field: SessionField) => boolean = () => true,
) {
  const byId = new Map(current.map((session) => [session.id, session]));
  return sortSessions(
    incoming
      .filter((session) => !_deletedSessionIds.has(session.id))
      .map((session) => {
        const existing = byId.get(session.id);
        if (!existing) return session;
        const next = { ...session };
        const nameWrite = _sessionWrites.get(
          sessionWriteKey(agentId, session.id, "name"),
        );
        const pinWrite = _sessionWrites.get(
          sessionWriteKey(agentId, session.id, "pinned"),
        );
        if (
          fieldIsCurrent(session.id, "name") &&
          nameWrite &&
          (nameWrite.token || nameWrite.revision > revision)
        )
          next.name = existing.name;
        if (
          fieldIsCurrent(session.id, "pinned") &&
          pinWrite &&
          (pinWrite.token || pinWrite.revision > revision)
        )
          next.pinned = existing.pinned;
        return next;
      }),
  );
}

/** All three metadata entry points share a field lane until the real request settles. */
export async function persistSessionMetadata(options: {
  agentId: string | null;
  id: string;
  field: SessionField;
  value: string | boolean;
  previous?: string | boolean;
  isCurrent: () => boolean;
  apply?: (value: string | boolean) => void;
  onError: (error: unknown) => void;
}): Promise<SessionMutationResult> {
  const { agentId, id, field, value, previous, isCurrent, apply, onError } =
    options;
  if (!agentId || !id || id === "__pending__" || _deletedSessionIds.has(id))
    return { status: "ignored", reason: "invalid" };
  if (!isCurrent()) return { status: "ignored", reason: "stale" };
  const key = sessionWriteKey(agentId, id, field);
  if (_sessionWrites.get(key)?.token)
    return { status: "ignored", reason: "busy" };
  if (value === previous) return { status: "ignored", reason: "invalid" };
  const token = {};
  _sessionWrites.set(key, { revision: ++_sessionRevision, token });
  const current = () =>
    _sessionWrites.get(key)?.token === token &&
    !_deletedSessionIds.has(id) &&
    isCurrent();
  apply?.(value);
  try {
    const response =
      field === "name"
        ? await octopThreadsApi.rename(agentId, id, value as string)
        : await octopThreadsApi.patch(agentId, id, {
            pinned: value as boolean,
          });
    if (!current()) return { status: "ignored", reason: "stale" };
    const saved =
      field === "name"
        ? formatThreadTitle(response.title) || "New Chat"
        : typeof response.pinned === "boolean"
        ? response.pinned
        : value;
    apply?.(saved);
    return { status: "saved", value: saved };
  } catch (error) {
    if (!current()) return { status: "ignored", reason: "stale" };
    if (previous !== undefined) apply?.(previous);
    onError(error);
    return { status: "failed" };
  } finally {
    if (_sessionWrites.get(key)?.token === token) {
      _sessionWrites.set(key, { revision: ++_sessionRevision });
    }
  }
}

/** Thread ids mid-create; stale-thread redirect must ignore these until listed. */
const _pendingThreadIds = new Set<string>();

export function markPendingThread(threadId: string) {
  _pendingThreadIds.add(threadId);
}

export function clearPendingThread(threadId: string) {
  _pendingThreadIds.delete(threadId);
}

/** Patch HITL bypass for one thread in the module session store. */
export function syncSessionHitlPolicy(
  threadId: string,
  policy: HitlSessionPolicy | null | undefined,
) {
  if (!threadId) return;
  const nextPolicy = policy ? parseHitlSessionPolicy(policy) : null;
  setModuleSessions((prev) => {
    const idx = prev.findIndex((s) => s.id === threadId);
    if (idx < 0) return prev;
    const current = prev[idx];
    const currentPolicy = current.hitlPolicy ?? null;
    if (
      (currentPolicy?.mode ?? "ask") === (nextPolicy?.mode ?? "ask") &&
      JSON.stringify(currentPolicy?.tools ?? []) ===
        JSON.stringify(nextPolicy?.tools ?? [])
    ) {
      return prev;
    }
    const next = [...prev];
    next[idx] = { ...current, hitlPolicy: nextPolicy };
    return next;
  });
}

/** Patch conversation-mode fields for one thread in the module session store. */
export function syncSessionConversationMode(
  threadId: string,
  conversationMode: "ask" | "plan" | "craft" | null | undefined,
  pendingPlanPath: string | null | undefined,
) {
  if (!threadId) return;
  const mode = conversationMode ?? null;
  const path = (pendingPlanPath || "").trim() || null;
  setModuleSessions((prev) => {
    const idx = prev.findIndex((s) => s.id === threadId);
    if (idx < 0) return prev;
    const current = prev[idx];
    if (
      (current.conversationMode ?? null) === mode &&
      (current.pendingPlanPath ?? null) === path
    ) {
      return prev;
    }
    const next = [...prev];
    next[idx] = { ...current, conversationMode: mode, pendingPlanPath: path };
    return next;
  });
}

/** Patch artifacts for one thread in the module session store. */
export function syncSessionArtifacts(threadId: string, artifacts: string[]) {
  if (!threadId) return;
  const normalized = artifacts.filter(
    (path): path is string =>
      typeof path === "string" && path.trim().length > 0,
  );
  setModuleSessions((prev) => {
    const idx = prev.findIndex((s) => s.id === threadId);
    if (idx < 0) return prev;
    const current = prev[idx].artifacts ?? [];
    if (
      current.length === normalized.length &&
      current.every((p, i) => p === normalized[i])
    ) {
      return prev;
    }
    const next = [...prev];
    next[idx] = { ...next[idx], artifacts: normalized };
    return next;
  });
}

/** Fetch thread artifacts from history API and sync into the session store. */
export async function fetchAndSyncSessionArtifacts(
  agentId: string,
  threadId: string,
): Promise<string[]> {
  if (!agentId || !threadId) return [];
  try {
    const history = await octopThreadsApi.history(agentId, threadId, {
      limit: 1,
      offset: 0,
    });
    const artifacts = Array.isArray(history.artifacts)
      ? history.artifacts.filter(
          (path): path is string =>
            typeof path === "string" && path.trim().length > 0,
        )
      : [];
    syncSessionArtifacts(threadId, artifacts);
    return artifacts;
  } catch {
    return [];
  }
}

export function isPendingThread(threadId: string): boolean {
  return threadId === "__pending__" || _pendingThreadIds.has(threadId);
}

function notifyListeners() {
  for (const cb of _listeners) cb();
}

function setModuleSessions(
  updater: Session[] | ((prev: Session[]) => Session[]),
) {
  _sessions = typeof updater === "function" ? updater(_sessions) : updater;
  _snapshot = {
    sessions: _sessions,
    loading: _loading,
    hasMore: _hasMore,
    loadingMore: _loadingMore,
  };
  notifyListeners();
}

function setModuleLoading(value: boolean) {
  _loading = value;
  _snapshot = {
    sessions: _sessions,
    loading: _loading,
    hasMore: _hasMore,
    loadingMore: _loadingMore,
  };
  notifyListeners();
}

function subscribeSessionStore(cb: () => void) {
  _listeners.add(cb);
  return () => {
    _listeners.delete(cb);
    if (_listeners.size === 0) syncStoreToAgent(null);
  };
}

let _snapshot = {
  sessions: _sessions,
  loading: _loading,
  hasMore: _hasMore,
  loadingMore: _loadingMore,
};

function getSessionSnapshot() {
  return _snapshot;
}

export function isTempSessionId(id: string): boolean {
  void id;
  return false;
}

function getLoadedLimit(agentId: string): number {
  return _loadedLimitByAgent.get(agentId) ?? SESSION_PAGE_SIZE;
}

function resetSessionPagination(agentId: string) {
  _loadedLimitByAgent.set(agentId, SESSION_PAGE_SIZE);
  _hasMore = false;
  _loadingMore = false;
}

function visibleSessionsForAgent(
  sessions: Session[],
  agentId: string,
  activeThreadId?: string,
): Session[] {
  const limit = getLoadedLimit(agentId);
  let visible = sessions.slice(0, limit);
  if (!activeThreadId || visible.some((s) => s.id === activeThreadId)) {
    return visible;
  }
  const active = sessions.find((s) => s.id === activeThreadId);
  if (!active) return visible;
  visible = [active, ...visible.filter((s) => s.id !== activeThreadId)];
  return visible.slice(0, limit);
}

async function fetchSessionsPage(
  agentId: string,
  limit: number,
): Promise<{
  sessions: Session[];
  hasMore: boolean;
  read: { revision: number; generation: number; sequence: number };
}> {
  const read = {
    revision: captureSessionRevision(),
    generation: _storeGeneration,
    sequence: ++_readSequence,
  };
  const rows = await octopThreadsApi.list(agentId, limit + 1);
  const hasMore = rows.length > limit;
  const sessions = sortSessions(rows.slice(0, limit).map(toSession));
  return { sessions, hasMore, read };
}

function currentSessionRead(
  read: { generation: number; sequence: number },
  expansionLimit?: number,
) {
  return (
    read.generation === _storeGeneration &&
    (read.sequence >= _publishedRead ||
      Boolean(
        _storeAgentId &&
          expansionLimit &&
          expansionLimit > getLoadedLimit(_storeAgentId),
      ))
  );
}

function applySessionPage(
  allSessions: Session[],
  hasMore: boolean,
  limit: number,
  agentId: string,
  read: { revision: number; sequence: number },
  activeThreadId?: string,
) {
  if (read.sequence < _publishedRead) {
    const current = new Map(_sessions.map((session) => [session.id, session]));
    allSessions = allSessions.map(
      (session) => current.get(session.id) ?? session,
    );
  }
  _publishedRead = Math.max(_publishedRead, read.sequence);
  _loadedLimitByAgent.set(agentId, limit);
  _hasMore = hasMore;
  setModuleSessions(
    visibleSessionsForAgent(
      mergeSessionMetadata(agentId, allSessions, _sessions, read.revision),
      agentId,
      activeThreadId,
    ),
  );
}

function setModuleLoadingMore(value: boolean) {
  _loadingMore = value;
  _snapshot = {
    sessions: _sessions,
    loading: _loading,
    hasMore: _hasMore,
    loadingMore: _loadingMore,
  };
  notifyListeners();
}

/**
 * Drop previous-agent threads before the first paint of a new agent.
 * Clearing only in useEffect leaks stale sessions for one render, and
 * chat nav can then write `/chat/{newAgent}/{oldThread}` into the URL.
 */
function syncStoreToAgent(agentId: string | null) {
  if (_storeAgentId === agentId) return;
  _storeGeneration++;
  _storeAgentId = agentId;
  _sessions = [];
  _loading = agentId != null;
  _hasMore = false;
  _loadingMore = false;
  _snapshot = {
    sessions: _sessions,
    loading: _loading,
    hasMore: _hasMore,
    loadingMore: _loadingMore,
  };
}

/** Reset module session store between vitest cases. */
export function resetSessionStoreForTests() {
  _storeGeneration++;
  _initialFetchGeneration = -1;
  _readSequence = 0;
  _publishedRead = 0;
  _loadingMoreRead = 0;
  _sessionRevision = 0;
  _sessionWrites.clear();
  _deletedSessionIds.clear();
  _storeAgentId = null;
  _sessions = [];
  _loading = true;
  _hasMore = false;
  _loadingMore = false;
  _loadedLimitByAgent.clear();
  _pendingThreadIds.clear();
  _snapshot = {
    sessions: _sessions,
    loading: _loading,
    hasMore: _hasMore,
    loadingMore: _loadingMore,
  };
}

export function useSessions(
  agentId: string | null,
  options: UseSessionsOptions = {},
) {
  const internal = options.internal === true;
  const { t } = useTranslation();
  syncStoreToAgent(agentId);
  const ownerRef = useRef({
    agentId,
    generation: _storeGeneration,
    live: true,
  });
  if (ownerRef.current.agentId !== agentId)
    ownerRef.current = { agentId, generation: _storeGeneration, live: true };
  const owner = ownerRef.current;
  const isCurrentOwner = useCallback(
    () =>
      owner.live &&
      owner.generation === _storeGeneration &&
      _storeAgentId === agentId,
    [owner, agentId],
  );
  const { sessions, loading, hasMore, loadingMore } = useSyncExternalStore(
    subscribeSessionStore,
    getSessionSnapshot,
  );

  const fetchSessions = useCallback(
    async (activeThreadId?: string) => {
      if (!isCurrentOwner()) return _sessions;
      if (!agentId) {
        setModuleSessions([]);
        setModuleLoading(false);
        return [];
      }
      try {
        const limit = getLoadedLimit(agentId);
        const {
          sessions: valid,
          hasMore: more,
          read,
        } = await fetchSessionsPage(agentId, limit);
        if (!isCurrentOwner() || !currentSessionRead(read)) return _sessions;
        applySessionPage(valid, more, limit, agentId, read, activeThreadId);
        return _sessions;
      } catch {
        return _sessions;
      } finally {
        if (isCurrentOwner()) {
          setModuleLoading(false);
        }
      }
    },
    [agentId, isCurrentOwner],
  );

  const loadMoreSessions = useCallback(
    async (activeThreadId?: string) => {
      if (!agentId || !isCurrentOwner() || _loadingMore || !_hasMore) return;
      const loadingToken = ++_loadingMoreRead;
      setModuleLoadingMore(true);
      try {
        const nextLimit = getLoadedLimit(agentId) + SESSION_PAGE_SIZE;
        const {
          sessions: valid,
          hasMore: more,
          read,
        } = await fetchSessionsPage(agentId, nextLimit);
        if (!isCurrentOwner() || !currentSessionRead(read, nextLimit)) return;
        applySessionPage(valid, more, nextLimit, agentId, read, activeThreadId);
      } catch {
        /* ignore */
      } finally {
        if (isCurrentOwner() && loadingToken === _loadingMoreRead)
          setModuleLoadingMore(false);
      }
    },
    [agentId, isCurrentOwner],
  );

  const fetchAllSessions = useCallback(
    async (activeThreadId?: string) => {
      if (!agentId || !isCurrentOwner()) return;
      try {
        const {
          sessions: valid,
          hasMore: more,
          read,
        } = await fetchSessionsPage(agentId, 50);
        if (!isCurrentOwner() || !currentSessionRead(read)) return;
        applySessionPage(
          valid,
          more,
          valid.length,
          agentId,
          read,
          activeThreadId,
        );
      } catch {
        /* ignore */
      }
    },
    [agentId, isCurrentOwner],
  );

  const ensureThreadInList = useCallback(
    async (threadId: string): Promise<ThreadProbeResult> => {
      if (!agentId || !threadId) return "missing";
      if (!isCurrentOwner()) return "unknown";
      if (_sessions.some((s) => s.id === threadId)) return "found";
      try {
        const limit = getLoadedLimit(agentId);
        const probeLimit = Math.max(limit + 1, 50);
        const {
          sessions: valid,
          hasMore: more,
          read,
        } = await fetchSessionsPage(agentId, probeLimit);
        // Agent switched while the probe was in flight — do not rewrite URL.
        if (!isCurrentOwner() || !currentSessionRead(read)) return "unknown";
        const found = valid.some(
          (s) => s.id === threadId && !_deletedSessionIds.has(s.id),
        );
        // An internal runtime has no "create a new thread" fallback, so a list
        // gap must never be treated as a deleted thread.
        if (!found) return internal ? "unknown" : "missing";
        applySessionPage(
          valid,
          more || valid.length > limit,
          limit,
          agentId,
          read,
          threadId,
        );
        return "found";
      } catch {
        // Network/API failure — keep the URL until a later successful probe.
        return "unknown";
      }
    },
    [agentId, internal, isCurrentOwner],
  );

  // Fetch only: agent switches are synced in-render via syncStoreToAgent.
  useEffect(() => {
    syncStoreToAgent(agentId);
    owner.live = true;
    owner.generation = _storeGeneration;
    if (!agentId || _initialFetchGeneration === _storeGeneration)
      return () => {
        owner.live = false;
      };
    _initialFetchGeneration = _storeGeneration;
    resetSessionPagination(agentId);
    setModuleLoading(true);
    void (async () => {
      const requestedAgent = agentId;
      const requestedGeneration = _storeGeneration;
      try {
        const {
          sessions: valid,
          hasMore: more,
          read,
        } = await fetchSessionsPage(requestedAgent, SESSION_PAGE_SIZE);
        if (_storeAgentId !== requestedAgent || !currentSessionRead(read))
          return;
        applySessionPage(valid, more, SESSION_PAGE_SIZE, requestedAgent, read);
      } catch {
        /* ignore */
      } finally {
        if (
          _storeAgentId === requestedAgent &&
          _storeGeneration === requestedGeneration
        ) {
          setModuleLoading(false);
        }
      }
    })();
    return () => {
      owner.live = false;
    };
  }, [agentId, owner, isCurrentOwner]);

  useEffect(() => {
    return onSessionEvent((event) => {
      if (event.kind !== "sessionDeleted") return;
      const { sessionId } = event;
      forgetDeletedSession(sessionId);
      setModuleSessions((prev) => prev.filter((s) => s.id !== sessionId));
    });
  }, []);

  const createSession = useCallback((): {
    session: Session;
    resolvedId: Promise<string>;
  } => {
    // A private file task must never open a brand-new thread: return an inert
    // placeholder instead of POSTing to the internal runtime.
    if (!agentId || internal) {
      const empty: Session = {
        id: "",
        name: "New Chat",
        threadId: "",
        updatedAt: null,
        channelType: "dashboard",
      };
      return { session: empty, resolvedId: Promise.resolve("") };
    }
    const placeholder: Session = {
      id: "__pending__",
      name: "New Chat",
      threadId: "",
      updatedAt: new Date().toISOString(),
      channelType: "dashboard",
    };
    setModuleSessions((prev) => sortSessions([placeholder, ...prev]));
    const resolvedId = octopThreadsApi
      .create(agentId)
      .then((created) => {
        markPendingThread(created.thread_id);
        const now = Math.floor(Date.now() / 1000);
        const session = toSession({
          thread_id: created.thread_id,
          title: null,
          // last_active stays 0 server-side until first turn; use created_at for sort.
          last_active: 0,
          created_at: now,
          channel_type: "dashboard",
        });
        setModuleSessions((prev) =>
          sortSessions([
            session,
            ...prev.filter((s) => s.id !== "__pending__"),
          ]),
        );
        return created.thread_id;
      })
      .catch(() => {
        setModuleSessions((prev) => prev.filter((s) => s.id !== "__pending__"));
        return "";
      });
    return { session: placeholder, resolvedId };
  }, [agentId, internal]);

  const deleteSession = useCallback(
    async (id: string) => {
      if (!agentId || !id) return false;
      try {
        await octopThreadsApi.delete(agentId, id);
        setModuleSessions((prev) => prev.filter((s) => s.id !== id));
        chatStore.removeSession(id);
        chatStore.emitSessionEvent({ kind: "sessionDeleted", sessionId: id });
        return true;
      } catch {
        return false;
      }
    },
    [agentId],
  );

  const pinSession = useCallback(
    async (id: string, pinned: boolean): Promise<SessionMutationResult> => {
      const existing = _sessions.find((s) => s.id === id);
      if (!existing) return { status: "ignored", reason: "invalid" };
      return persistSessionMetadata({
        agentId,
        id,
        field: "pinned",
        value: pinned,
        previous: Boolean(existing.pinned),
        isCurrent: () => isCurrentOwner() && _sessions.some((s) => s.id === id),
        apply: (value) =>
          setModuleSessions((prev) =>
            sortSessions(
              prev.map((s) =>
                s.id === id ? { ...s, pinned: value as boolean } : s,
              ),
            ),
          ),
        onError: (error) => showApiError(error, t("common.saveFailed"), t),
      });
    },
    [agentId, isCurrentOwner, t],
  );

  const renameSession = useCallback(
    async (id: string, name: string): Promise<SessionMutationResult> => {
      const next = formatThreadTitle(name) || name.trim();
      const existing = _sessions.find((s) => s.id === id);
      if (!next || !existing) return { status: "ignored", reason: "invalid" };
      return persistSessionMetadata({
        agentId,
        id,
        field: "name",
        value: next,
        previous: existing.name,
        isCurrent: () => isCurrentOwner() && _sessions.some((s) => s.id === id),
        apply: (value) =>
          setModuleSessions((prev) =>
            prev.map((s) =>
              s.id === id ? { ...s, name: value as string } : s,
            ),
          ),
        onError: (error) => showApiError(error, t("common.saveFailed"), t),
      });
    },
    [agentId, isCurrentOwner, t],
  );

  const syncSession = useCallback(
    async (localId: string): Promise<string | null> => {
      void localId;
      return null;
    },
    [],
  );

  return {
    sessions,
    loading,
    hasMore,
    loadingMore,
    createSession,
    deleteSession,
    renameSession,
    pinSession,
    fetchSessions,
    loadMoreSessions,
    fetchAllSessions,
    ensureThreadInList,
    syncSession,
  };
}
