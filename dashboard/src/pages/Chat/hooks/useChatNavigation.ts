import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useLocation, useNavigate } from "react-router-dom";
import type { Location, NavigateOptions } from "react-router-dom";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import { octopAgentsApi } from "../../../api/modules/octopAgents";
import * as chatStore from "./chatStore";
import {
  pickPreferredSession,
  isPendingThread,
  clearPendingThread,
  type Session,
  type ThreadProbeResult,
} from "./useSessions";
import { EMPTY_CHAT_SESSION_KEY } from "../constants";

export interface NavigationToken {
  lifetime: number;
  intent: number;
}
interface RouteReadToken extends NavigationToken {
  route: number;
}
export interface ChatNavigationOwner {
  revision: number;
  lifetime: () => number;
  begin: (kind: "explicit" | "preferred") => NavigationToken;
  current: (token: NavigationToken) => boolean;
  navigate: (
    token: NavigationToken,
    path: string,
    options?: NavigateOptions,
  ) => boolean;
  release: (token: NavigationToken) => void;
  captureRoute: () => RouteReadToken;
  currentRoute: (token: RouteReadToken) => boolean;
  preferredPending: () => boolean;
}

/** One owner per Chat component lifetime; both navigation consumers share it. */
export function useChatNavigationOwner(
  location: Pick<Location, "key" | "pathname">,
  routeAgentId?: string,
  threadId?: string,
): ChatNavigationOwner {
  const navigate = useNavigate();
  const [revision, setRevision] = useState(0);
  const state = useRef({
    live: false,
    lifetime: 0,
    intent: 0,
    route: 0,
    observed: JSON.stringify([location.key, routeAgentId, threadId]),
    kind: null as "explicit" | "preferred" | null,
    expected: null as { token: NavigationToken; path: string } | null,
  });
  const current = useCallback(
    (token: NavigationToken) =>
      state.current.live &&
      token.lifetime === state.current.lifetime &&
      token.intent === state.current.intent,
    [],
  );
  const begin = useCallback((kind: "explicit" | "preferred") => {
    const owner = state.current;
    owner.intent++;
    owner.kind = kind;
    owner.expected = null;
    setRevision((value) => value + 1);
    return { lifetime: owner.lifetime, intent: owner.intent };
  }, []);
  const release = useCallback(
    (token: NavigationToken) => {
      if (!current(token)) return;
      state.current.kind = null;
      setRevision((value) => value + 1);
    },
    [current],
  );
  const navigateCurrent = useCallback(
    (token: NavigationToken, path: string, options?: NavigateOptions) => {
      if (!current(token)) return false;
      state.current.expected = { token, path };
      navigate(path, options);
      return true;
    },
    [current, navigate],
  );
  const captureRoute = useCallback(
    () => ({
      lifetime: state.current.lifetime,
      intent: state.current.intent,
      route: state.current.route,
    }),
    [],
  );
  const currentRoute = useCallback(
    (token: RouteReadToken) =>
      current(token) && token.route === state.current.route,
    [current],
  );
  const preferredPending = useCallback(
    () => state.current.kind === "preferred",
    [],
  );
  const lifetime = useCallback(() => state.current.lifetime, []);

  useLayoutEffect(() => {
    const owner = state.current;
    owner.live = true;
    owner.lifetime++;
    return () => {
      owner.live = false;
      owner.intent++;
      owner.route++;
      owner.kind = null;
      owner.expected = null;
    };
  }, []);
  useLayoutEffect(() => {
    const owner = state.current;
    const next = JSON.stringify([location.key, routeAgentId, threadId]);
    if (next === owner.observed) return;
    owner.observed = next;
    owner.route++;
    if (
      owner.expected &&
      current(owner.expected.token) &&
      owner.expected.path === location.pathname
    ) {
      owner.expected = null;
    } else {
      owner.intent++;
      owner.kind = null;
      owner.expected = null;
      setRevision((value) => value + 1);
    }
  }, [location.key, location.pathname, routeAgentId, threadId, current]);
  return {
    revision,
    lifetime,
    begin,
    current,
    navigate: navigateCurrent,
    release,
    captureRoute,
    currentRoute,
    preferredPending,
  };
}

interface UseChatNavigationParams {
  navigationOwner: ChatNavigationOwner;
  routeAgentId: string | undefined;
  threadId: string | undefined;
  resolvedAgentId: string | null | undefined;
  activeThreadId: string | null;
  sessions: Session[];
  sessionsLoading: boolean;
  prefillInputRef: React.MutableRefObject<string>;
  loadHistory: (threadId: string) => Promise<void>;
  clearMessages: () => void;
  ensureThreadInList: (threadId: string) => Promise<ThreadProbeResult>;
  fetchSessions: (activeId?: string) => Promise<Session[]>;
  refreshAgents: (opts?: { silent?: boolean }) => Promise<void>;
  internalTask?: boolean;
}

export function useChatNavigation({
  navigationOwner,
  routeAgentId,
  threadId,
  resolvedAgentId,
  activeThreadId,
  sessions,
  sessionsLoading,
  prefillInputRef,
  loadHistory,
  clearMessages,
  ensureThreadInList,
  fetchSessions,
  refreshAgents,
  internalTask = false,
}: UseChatNavigationParams) {
  const location = useLocation();
  const {
    begin,
    current,
    navigate: navigateCurrent,
    release,
    captureRoute,
    currentRoute,
    preferredPending,
    lifetime,
    revision,
  } = navigationOwner;
  const sessionsRef = useRef(sessions);
  sessionsRef.current = sessions;
  // One-shot blank-chat intent from minimal nav "+" on non-chat routes.
  const preferEmptyChatRef = useRef(false);

  useLayoutEffect(() => {
    if (!(location.state as { newChat?: boolean } | null)?.newChat) return;
    const token = begin("explicit");
    preferEmptyChatRef.current = true;
    navigateCurrent(token, location.pathname, { replace: true, state: null });
    release(token);
  }, [location.state, begin, navigateCurrent, release, location.pathname]);

  useEffect(() => {
    if (!resolvedAgentId) return;
    if (activeThreadId) {
      if (isPendingThread(activeThreadId)) return;
      void loadHistory(activeThreadId);
    } else {
      const emptySnap = chatStore.getSnapshot(EMPTY_CHAT_SESSION_KEY);
      if (emptySnap.messages.length === 0 && !emptySnap.isStreaming) {
        clearMessages();
      }
    }
  }, [activeThreadId, resolvedAgentId, loadHistory, clearMessages]);

  const markedAgentReadRef = useRef<string | null>(null);
  useEffect(() => {
    if (!resolvedAgentId || internalTask) return;
    if (markedAgentReadRef.current === resolvedAgentId) return;
    markedAgentReadRef.current = resolvedAgentId;
    void octopAgentsApi
      .markRead(resolvedAgentId)
      .then(() => refreshAgents({ silent: true }))
      .catch(() => {});
  }, [resolvedAgentId, refreshAgents, internalTask]);

  useEffect(() => {
    const refreshBadges = () => void refreshAgents({ silent: true });
    refreshBadges();
    const intervalId = window.setInterval(refreshBadges, 10_000);
    const onVisibility = () => {
      if (document.visibilityState === "visible") refreshBadges();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [refreshAgents]);

  useEffect(() => {
    return chatStore.onStreamEvent((event) => {
      if (
        event.kind === "streamEnd" &&
        event.sessionId === (activeThreadId ?? "")
      ) {
        void fetchSessions(activeThreadId ?? undefined);
      }
    });
  }, [activeThreadId, fetchSessions]);

  // A proactive run (cron) created or wrote to a thread outside the chat socket:
  // refresh the list — and the open thread's history — without a page reload.
  useEffect(() => {
    return chatStore.onSessionEvent((event) => {
      if (event.kind !== "sessionsChanged") return;
      if (event.agentId && resolvedAgentId && event.agentId !== resolvedAgentId)
        return;
      void fetchSessions(activeThreadId ?? undefined);
      if (!activeThreadId || event.sessionId !== activeThreadId) return;
      chatStore.invalidateHistory(activeThreadId);
      void loadHistory(activeThreadId);
    });
  }, [activeThreadId, resolvedAgentId, fetchSessions, loadHistory]);

  const initialNavDone = useRef<string | null>(null);
  const navLifetime = useRef(0);
  const chatUrlStateRef = useRef<{ agentId?: string; threadId?: string }>({});

  useEffect(() => {
    if (navLifetime.current === lifetime()) return;
    navLifetime.current = lifetime();
    initialNavDone.current = null;
    ensureThreadAttemptRef.current = null;
  });

  useEffect(() => {
    const prev = chatUrlStateRef.current;
    if (
      routeAgentId &&
      prev.agentId &&
      prev.agentId !== routeAgentId &&
      threadId &&
      threadId === prev.threadId
    ) {
      initialNavDone.current = null;
      const token = begin("explicit");
      navigateCurrent(token, `/chat/${routeAgentId}`, { replace: true });
      release(token);
      clearMessages();
    }
    chatUrlStateRef.current = { agentId: routeAgentId, threadId };
  }, [routeAgentId, threadId, begin, navigateCurrent, release, clearMessages]);

  useEffect(() => {
    if (sessionsLoading || prefillInputRef.current) return;
    const agent = resolvedAgentId;
    if (!agent) return;
    if (threadId) {
      initialNavDone.current = agent;
      return;
    }
    if (initialNavDone.current === agent) return;
    if (preferredPending()) return;
    initialNavDone.current = agent;
    if (preferEmptyChatRef.current) {
      preferEmptyChatRef.current = false;
      return;
    }
    if (sessions.length > 0) {
      const preferred = pickPreferredSession(sessionsRef.current);
      if (preferred) {
        const token = begin("explicit");
        if (!current(token)) return;
        void octopThreadsApi.rebind(agent, preferred.id).catch(() => {});
        navigateCurrent(token, `/chat/${agent}/${preferred.id}`, {
          replace: true,
        });
        release(token);
      }
    } else if (!routeAgentId) {
      const token = begin("explicit");
      navigateCurrent(token, `/chat/${agent}`, { replace: true });
      release(token);
    }
  }, [
    sessions,
    sessionsLoading,
    threadId,
    resolvedAgentId,
    routeAgentId,
    begin,
    current,
    navigateCurrent,
    release,
    preferredPending,
    revision,
    prefillInputRef,
  ]);

  const ensureThreadAttemptRef = useRef<string | null>(null);
  useEffect(() => {
    ensureThreadAttemptRef.current = null;
  }, [resolvedAgentId]);

  useEffect(() => {
    if (!resolvedAgentId || !threadId || sessionsLoading) return;
    if (isPendingThread(threadId)) {
      if (sessions.some((s) => s.id === threadId)) {
        clearPendingThread(threadId);
      }
      return;
    }
    if (sessions.some((s) => s.id === threadId)) {
      ensureThreadAttemptRef.current = null;
      return;
    }
    // Thread missing from the visible page (or list is empty after load).
    // Probe the API — only rewrite the URL when the probe confirms absence.
    // Do not treat a still-loading / failed list as "deleted".
    const read = captureRoute();
    const attemptKey = JSON.stringify([
      read.lifetime,
      read.intent,
      read.route,
      resolvedAgentId,
      threadId,
    ]);
    if (ensureThreadAttemptRef.current === attemptKey) {
      return;
    }
    ensureThreadAttemptRef.current = attemptKey;
    void ensureThreadInList(threadId).then((result) => {
      if (ensureThreadAttemptRef.current !== attemptKey || !currentRoute(read))
        return;
      // Only rewrite when the probe confirms absence — keep URL on found/unknown.
      if (result !== "missing") return;
      const preferred = pickPreferredSession(sessionsRef.current);
      const token = begin("explicit");
      if (!current(token)) return;
      if (preferred) {
        void octopThreadsApi
          .rebind(resolvedAgentId, preferred.id)
          .catch(() => {});
        navigateCurrent(token, `/chat/${resolvedAgentId}/${preferred.id}`, {
          replace: true,
        });
      } else {
        navigateCurrent(token, `/chat/${resolvedAgentId}`, { replace: true });
      }
      release(token);
    });
  }, [
    resolvedAgentId,
    threadId,
    sessions,
    sessionsLoading,
    begin,
    current,
    navigateCurrent,
    release,
    captureRoute,
    currentRoute,
    revision,
    ensureThreadInList,
  ]);

  const resetNavForAgentSwitch = () => {
    initialNavDone.current = null;
    ensureThreadAttemptRef.current = null;
    preferEmptyChatRef.current = false;
  };

  const markInitialNavDone = (agentId: string) => {
    initialNavDone.current = agentId;
  };

  return { resetNavForAgentSwitch, markInitialNavDone };
}
