import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";
import { setActiveAgentId } from "../api/request";
import { agentApi as legacyAgentApi } from "../api/modules/agent";

/**
 * Multi-Agent navigation state.
 *
 * Plan §14.3: the dashboard fetches the current user's agents on login,
 * stores the list + selected id in this context, persists the selection
 * in ``localStorage`` (``octop:active-agent``), and pipes the selected id
 * into ``api/request.ts`` so every agent-scoped HTTP call gets an
 * ``X-Octop-Agent-Id`` header.
 *
 * 030A: the owner-scoped raw list may also carry minimal ``internal`` cards
 * for private project-task runtimes. They stay private to this provider and
 * are only reachable through ``getChatAgentById`` for an explicit existing
 * owner thread; every ordinary list, default and picker uses the filtered
 * projection, so an internal runtime can never be selected implicitly.
 */

export interface OctopAgent {
  /** Surrogate integer primary key from the database. */
  id: number;
  /** Public agent id used in API paths and ``X-Octop-Agent-Id``. */
  agent_id: string;
  /** Owning user id (present on list responses). */
  user_id?: number | null;
  /** Resolved username for admin list view. */
  owner_username?: string | null;
  /** Whether the owner has shared this expert with other users. */
  is_shared?: boolean;
  /** Whether the current user owns this expert. */
  is_owner?: boolean;
  name: string;
  description: string | null;
  persona_mbti: string | null;
  default_model: string | null;
  system_prompt: string | null;
  template_name: string | null;
  state: "running" | "stopped" | "failed" | "starting" | "stopping" | string;
  last_error: string | null;
  icon: string | null;
  icon_name: string | null;
  icon_url: string | null;
  color: string | null;
  max_iters?: number | null;
  max_input_length?: number | null;
  temperature?: number | null;
  top_p?: number | null;
  max_tokens?: number | null;
  /** Absent on the minimal card of an internal project-task runtime. */
  config?: Record<string, unknown>;
  /**
   * True for the owner's private project-task runtime (`runtime_kind=
   * project_task_files`). Internal cards are kept out of every ordinary
   * expert list/picker/default; only existing owner threads may resolve them.
   */
  internal?: boolean;
  /** Knowledge bases opened by default in new chats with this expert. */
  knowledge_base_ids?: string[];
  /** Connectors opened by default in new chats with this expert. */
  mcp_servers?: string[];
  /** Aggregated unread count across all sessions for this agent (current user). */
  unread_count?: number;
  /** True while BOOTSTRAP.md onboarding has not written ``.bootstrapped`` yet. */
  bootstrap_pending?: boolean;
  /** ``expert`` (default) or ``team`` host. */
  kind?: "expert" | "team" | string;
  /** Member agent ids when ``kind === "team"``. */
  member_ids?: string[];
  welcome_message?: string | null;
}

interface AgentContextValue {
  /**
   * Latest *ordinary* agents fetched from ``GET /api/agents``. Owner-private
   * internal project-task runtimes are filtered out centrally so every
   * picker, default and localStorage reconciliation only ever sees experts.
   */
  agents: OctopAgent[];
  /** Active agent id, or ``null`` when no agent is selected. */
  activeAgentId: string | null;
  /** Convenience: the full record for ``activeAgentId``. */
  activeAgent: OctopAgent | null;
  /** True while the initial fetch is in flight. */
  loading: boolean;
  /** Last fetch error message, or ``null``. */
  error: string | null;
  /** Switch the active agent (updates context + localStorage + request.ts). */
  setActiveAgent: (id: string | null) => void;
  /** Force a re-fetch of ``/api/agents`` (e.g. after creating one). */
  refresh: (options?: { silent?: boolean; force?: boolean }) => Promise<void>;
  /**
   * Raw owner-scoped lookup used only to resolve an already known existing
   * thread's internal project-task runtime. Returns ordinary cards too, so
   * callers must still gate on ``internal`` before using it as a private
   * runtime. Never use this to populate an ordinary expert picker.
   */
  getChatAgentById: (id: string | null | undefined) => OctopAgent | null;
}

export interface EnabledExpertsOptions {
  /**
   * When ``true`` (opt-in; the default is ``false``), keep the
   * ``resolvedAgentId`` expert in the returned list even if it does not
   * match the predicate. Today every caller passes ``false`` so a disabled
   * expert disappears from the sidebar / @-picker the moment the user stops
   * it, including when it is the currently focused expert. The main panel
   * still renders ``AgentNotReadyScreen`` on the same URL so users get a
   * clear path to ``/experts`` to restart it.
   */
  pinActive?: boolean;
}

const STORAGE_KEY = "octop:active-agent";

/**
 * Pure helper: narrow ``agents`` down to those that are "enabled" for the
 * chat surface (running experts). The same predicate is reused by:
 *   • the chat page left sidebar (``Chat/index.tsx``)
 *   • the minimal-layout records pane (``MinimalRecordsHost`` — non-/chat
 *     routes like ``/experts`` show expert folders here)
 *   • the chat composer's ``@``-mention picker + slash menu
 *
 * Keep this helper in sync with ``isAgentChatReady`` so disabled experts
 * never leak into any chat-side surface.
 */
export function selectEnabledExperts(
  agents: OctopAgent[],
  resolvedAgentId: string | null | undefined,
  options: EnabledExpertsOptions = {},
): OctopAgent[] {
  const { pinActive = false } = options;
  // Defense in depth: the context already filters internal runtimes out of
  // ``agents``, but a raw list passed directly here must never leak one.
  const visible = agents.filter((a) => !a.internal);
  const enabled = visible.filter((a) => a.state === "running");
  if (!pinActive || !resolvedAgentId) return enabled;
  if (enabled.some((a) => a.agent_id === resolvedAgentId)) return enabled;
  const pinnedActive = visible.find((a) => a.agent_id === resolvedAgentId);
  if (!pinnedActive) return enabled;
  return [pinnedActive, ...enabled];
}

function sameMemberIds(left?: string[], right?: string[]): boolean {
  const a = left ?? [];
  const b = right ?? [];
  return a.length === b.length && a.every((id, index) => id === b[index]);
}

/**
 * Shared projection used by the chat composer (`ChatInput` /
 * ``composerLookups``). Keeps the lightweight ``ChatAgentOption`` shape
 * consistent across surfaces — name/icon for the chip, shared badge for
 * the picker, owner_username for the current-user indicator.
 */
export function projectChatAgentOption(agent: OctopAgent): {
  agent_id: string;
  name: string;
  icon_name: string | null;
  icon_url: string | null;
  color: string | null;
  is_shared: boolean;
  is_owner: boolean;
  owner_username: string | null;
} {
  return {
    agent_id: agent.agent_id,
    name: agent.name,
    icon_name: agent.icon_name,
    icon_url: agent.icon_url,
    color: agent.color,
    is_shared: Boolean(agent.is_shared),
    is_owner: Boolean(agent.is_owner),
    owner_username: agent.owner_username ?? null,
  };
}

const defaultValue: AgentContextValue = {
  agents: [],
  activeAgentId: null,
  activeAgent: null,
  loading: false,
  error: null,
  setActiveAgent: () => undefined,
  refresh: async () => undefined,
  getChatAgentById: () => null,
};

const AgentContext = createContext<AgentContextValue>(defaultValue);

interface ListAgentsResponse {
  // Server returns OctopAgent[]; typed loosely so legacy agent.ts module
  // (which has a different ``agentApi`` shape for finnie endpoints) stays
  // untouched.
  list: () => Promise<OctopAgent[]>;
}

/**
 * Fetch ``/api/agents``. Tries the orca-flavored ``listAll`` method first,
 * falls back to a direct request if the legacy module hasn't been
 * regenerated yet.
 */
async function fetchAgents(): Promise<OctopAgent[]> {
  const candidate = legacyAgentApi as Partial<ListAgentsResponse> &
    Record<string, unknown>;
  if (typeof candidate.list === "function") {
    return candidate.list();
  }
  // Direct fallback so 14.3 doesn't depend on 14.6's API module rewrite.
  const { request } = await import("../api/request");
  return request<OctopAgent[]>("/agents");
}

export function AgentProvider({ children }: { children: ReactNode }) {
  // Raw owner-scoped list (may include internal project-task runtimes). It stays
  // private to this provider: ``agents`` below is the filtered ordinary
  // projection every consumer sees, and ``getChatAgentById`` is the only raw
  // lookup, used to resolve an already known owner thread.
  const [rawAgents, setRawAgents] = useState<OctopAgent[]>([]);
  const rawAgentsRef = useRef<OctopAgent[]>([]);
  const [activeAgentId, setActiveAgentIdState] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** Central ordinary-Agent filter: internal runtimes never leak out. */
  const agents = useMemo(
    () => rawAgents.filter((a) => a.internal !== true),
    [rawAgents],
  );

  const persistAndApply = useCallback((id: string | null) => {
    // An internal project-task runtime must never become the globally
    // persisted/default agent; only its explicit existing thread may use it.
    if (
      id != null &&
      rawAgentsRef.current.some((a) => a.agent_id === id && a.internal === true)
    ) {
      return;
    }
    setActiveAgentIdState((prev) => {
      // Skip the re-render when the id hasn't changed.
      if (prev === id) return prev;
      if (id) {
        localStorage.setItem(STORAGE_KEY, id);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
      setActiveAgentId(id); // populates request.ts module-level cache
      return id;
    });
  }, []);

  const getChatAgentById = useCallback(
    (id: string | null | undefined): OctopAgent | null => {
      if (!id) return null;
      return rawAgents.find((a) => a.agent_id === id) ?? null;
    },
    [rawAgents],
  );

  const refresh = useCallback(
    async (options?: { silent?: boolean; force?: boolean }): Promise<void> => {
      if (!options?.silent) {
        setLoading(true);
      }
      setError(null);
      try {
        const list = await fetchAgents();
        rawAgentsRef.current = list;
        // Only update state when content actually changed, to prevent
        // unnecessary re-renders of every component subscribed to this context
        // (the chat page polls every 10 s to refresh unread badges).
        setRawAgents((prev) => {
          if (
            !options?.force &&
            prev.length === list.length &&
            prev.every((a, i) => {
              const b = list[i];
              return (
                a.agent_id === b.agent_id &&
                a.state === b.state &&
                a.unread_count === b.unread_count &&
                a.bootstrap_pending === b.bootstrap_pending &&
                a.name === b.name &&
                a.icon === b.icon &&
                a.icon_name === b.icon_name &&
                a.color === b.color &&
                a.kind === b.kind &&
                a.is_shared === b.is_shared &&
                sameMemberIds(a.member_ids, b.member_ids)
              );
            })
          ) {
            return prev; // nothing changed — keep the same reference
          }
          return list;
        });

        // Reconcile selection against the *ordinary* list only: a stored or
        // first-position internal runtime is ignored instead of activated.
        const ordinary = list.filter((a) => a.internal !== true);
        const stored = localStorage.getItem(STORAGE_KEY);
        const haveStored =
          stored != null && ordinary.some((a) => a.agent_id === stored);
        if (haveStored) {
          persistAndApply(stored);
        } else if (ordinary.length > 0) {
          persistAndApply(ordinary[0].agent_id);
        } else {
          persistAndApply(null);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load agents");
        // On failure, leave whatever previous state was — don't blow away
        // a valid selection just because a fetch hiccuped.
      } finally {
        if (!options?.silent) {
          setLoading(false);
        }
      }
    },
    [persistAndApply],
  );

  // Initial fetch — fire once on mount. Login flow lives elsewhere; this
  // provider sits inside AuthGuard so by the time we mount, the JWT is
  // already in localStorage.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Re-sync agent runtime state when the user returns to the tab (e.g. after
  // stopping an expert on another page).
  useEffect(() => {
    const onFocus = () => void refresh({ silent: true });
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);

  const activeAgent = useMemo(
    () => agents.find((a) => a.agent_id === activeAgentId) ?? null,
    [agents, activeAgentId],
  );

  const value = useMemo<AgentContextValue>(
    () => ({
      agents,
      activeAgentId,
      activeAgent,
      loading,
      error,
      setActiveAgent: persistAndApply,
      refresh,
      getChatAgentById,
    }),
    [
      agents,
      activeAgentId,
      activeAgent,
      loading,
      error,
      persistAndApply,
      refresh,
      getChatAgentById,
    ],
  );

  return (
    <AgentContext.Provider value={value}>{children}</AgentContext.Provider>
  );
}

export function useAgent(): AgentContextValue {
  return useContext(AgentContext);
}
