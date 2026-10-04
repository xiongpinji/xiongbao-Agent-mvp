import { useCallback } from "react";
import type { ChatNavigationOwner } from "./useChatNavigation";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import * as chatStore from "./chatStore";
import { EMPTY_CHAT_SESSION_KEY } from "../constants";
import { pickPreferredSession, toSession, type Session } from "./useSessions";

interface UseChatSessionActionsParams {
  navigationOwner: ChatNavigationOwner;
  resolvedAgentId: string | null | undefined;
  activeThreadId: string | null;
  sessions: Session[];
  isMobile: boolean;
  setActiveAgent: (id: string) => void;
  setSidebarOpen: (open: boolean) => void;
  setSelectedModel: (model: string | null) => void;
  setHasBrowserTool: (value: boolean) => void;
  deleteSession: (id: string) => Promise<boolean>;
  clearMessages: () => void;
  resetNavForAgentSwitch: () => void;
  markInitialNavDone: (agentId: string) => void;
}

export function useChatSessionActions({
  navigationOwner,
  resolvedAgentId,
  activeThreadId,
  sessions,
  isMobile,
  setActiveAgent,
  setSidebarOpen,
  setSelectedModel,
  setHasBrowserTool,
  deleteSession,
  clearMessages,
  resetNavForAgentSwitch,
  markInitialNavDone,
}: UseChatSessionActionsParams) {
  const { begin, current, navigate, release } = navigationOwner;

  const handleNewChat = useCallback(() => {
    setSelectedModel(null);
    setHasBrowserTool(false);
    const agent = resolvedAgentId;
    if (!agent) return;
    const token = begin("explicit");
    markInitialNavDone(agent);
    navigate(token, `/chat/${agent}`);
    release(token);
  }, [
    navigate,
    begin,
    release,
    markInitialNavDone,
    resolvedAgentId,
    setSelectedModel,
    setHasBrowserTool,
  ]);

  /**
   * New chat with an arbitrary expert. Unlike {@link navigateToAgent} this stays
   * on the empty-chat view instead of jumping to that expert's latest thread.
   */
  const handleNewChatWithAgent = useCallback(
    (agentId: string) => {
      if (!agentId) return;
      const token = begin("explicit");
      setSelectedModel(null);
      setHasBrowserTool(false);
      if (agentId !== resolvedAgentId) {
        resetNavForAgentSwitch();
        setActiveAgent(agentId);
      }
      markInitialNavDone(agentId);
      navigate(token, `/chat/${agentId}`);
      chatStore.clearMessages(EMPTY_CHAT_SESSION_KEY);
      if (isMobile) setSidebarOpen(false);
      release(token);
    },
    [
      navigate,
      begin,
      release,
      resolvedAgentId,
      isMobile,
      setActiveAgent,
      setSidebarOpen,
      setSelectedModel,
      setHasBrowserTool,
      resetNavForAgentSwitch,
      markInitialNavDone,
    ],
  );

  const handleSelectSession = useCallback(
    (id: string) => {
      const agent = resolvedAgentId;
      if (!agent) return;
      const token = begin("explicit");
      if (id === activeThreadId) return;
      setSelectedModel(null);
      void octopThreadsApi.rebind(agent, id).catch(() => {});
      navigate(token, `/chat/${agent}/${id}`);
      if (isMobile) setSidebarOpen(false);
      release(token);
    },
    [
      activeThreadId,
      navigate,
      begin,
      release,
      isMobile,
      resolvedAgentId,
      setSidebarOpen,
      setSelectedModel,
    ],
  );

  const navigateToAgent = useCallback(
    (agentId: string) => {
      if (!agentId) return;
      const token = begin("preferred");
      resetNavForAgentSwitch();
      navigate(token, `/chat/${agentId}`, { replace: true });
      setActiveAgent(agentId);
      // We land on the new-chat view, so only that session is stale here.
      // Clearing the thread we are leaving would drop an in-flight turn.
      chatStore.clearMessages(EMPTY_CHAT_SESSION_KEY);
      if (isMobile) setSidebarOpen(false);

      void (async () => {
        try {
          const rows = await octopThreadsApi.list(agentId);
          if (!current(token)) return;
          const preferred = pickPreferredSession(rows.map(toSession));
          if (preferred) {
            markInitialNavDone(agentId);
            void octopThreadsApi.rebind(agentId, preferred.id).catch(() => {});
            navigate(token, `/chat/${agentId}/${preferred.id}`, {
              replace: true,
            });
          } else {
            markInitialNavDone(agentId);
          }
        } catch {
          /* initialNav effect picks thread once sessions load */
        } finally {
          release(token);
        }
      })();
    },
    [
      setActiveAgent,
      navigate,
      begin,
      current,
      release,
      isMobile,
      setSidebarOpen,
      resetNavForAgentSwitch,
      markInitialNavDone,
    ],
  );

  const handleDeleteSession = useCallback(
    async (id: string) => {
      const token = begin("explicit");
      const deleted = await deleteSession(id);
      if (!deleted || !current(token)) {
        release(token);
        return;
      }
      const agent = resolvedAgentId;
      if (id === activeThreadId && agent) {
        const remaining = sessions.filter((s) => s.id !== id);
        const preferred = pickPreferredSession(remaining);
        if (preferred) {
          navigate(token, `/chat/${agent}/${preferred.id}`, { replace: true });
        } else {
          navigate(token, `/chat/${agent}`, { replace: true });
          clearMessages();
        }
      }
      release(token);
    },
    [
      activeThreadId,
      sessions,
      deleteSession,
      navigate,
      begin,
      current,
      release,
      clearMessages,
      resolvedAgentId,
    ],
  );

  return {
    handleNewChat,
    handleNewChatWithAgent,
    handleSelectSession,
    navigateToAgent,
    handleDeleteSession,
  };
}
