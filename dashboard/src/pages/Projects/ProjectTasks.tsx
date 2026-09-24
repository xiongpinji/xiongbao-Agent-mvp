/**
 * ProjectTasks — PS-05 attribution + PS-05B card and opt-in text sharing.
 *
 * Scoped project task sharing:
 * - scope tabs map 1:1 to the server-filtered query (`own` / `shared` /
 *   `all`); "all" is only the caller's own tasks plus cards explicitly
 *   shared with them, never every project task and never a client filter;
 * - owners keep the existing `/chat/{agent}/{thread}` deep link, can detach
 *   and can grant/revoke reader access per task to individual members;
 * - reader cards (`access === "reader"`) open project-local read-only detail.
 *   A separate owner grant can add safe projected text, but never `/chat`,
 *   raw history, artifact, media, workspace or execution links;
 * - local/cloud execution, transfer and collaborative writing stay visibly
 *   disabled until their backend ACL exists (PS-05B-2/3, PS-05C, PS-08).
 *
 * The actor never comes from this component: no user id is sent except the
 * explicit share recipient, no role or source is sent at all, and no
 * `session_key`, artifact or workspace path is rendered.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Select,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import {
  ArrowRightLeft,
  Cloud,
  HardDrive,
  Link2,
  Link2Off,
  RefreshCw,
  Share2,
} from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { selectEnabledExperts, useAgent } from "../../context/AgentContext";
import {
  octopThreadsApi,
  type OctopThread,
} from "../../api/modules/octopThreads";
import type { ProjectMember } from "../../api/modules/projects";
import {
  PROJECT_TASK_MESSAGE_PAGE_SIZE,
  PROJECT_TASKS_PAGE_SIZE,
  projectTasksApi,
  type ProjectTask,
  type ProjectTaskMessage,
  type ProjectTaskMessagesStatus,
  type ProjectTaskScope,
  type ProjectTaskShare,
} from "../../api/modules/projectTasks";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { message } from "../../utils/antdMessage";
import { projectRoleTag } from "./index";

const { Text } = Typography;

/**
 * Bounded candidate scan. `octopThreadsApi.list` returns the caller's own
 * recent threads for one expert — never a complete, searchable history.
 */
export const TASK_CANDIDATE_LIMIT = 50;

/**
 * Reader text cache. Keyed by `thread_id` so a late response or a project
 * switch can never render another task's private text, and cleared whenever
 * the dialog closes or a fresh detail revokes text access.
 */
interface ReaderMessages {
  threadId: string;
  status: ProjectTaskMessagesStatus;
  items: ProjectTaskMessage[];
  hasMore: boolean;
  nextBeforeSeq: number | null;
}

/** Only the caller's own Dashboard DM threads are linkable candidates. */
export function isDashboardDmCandidate(thread: OctopThread): boolean {
  if (thread.channel_type !== "dashboard") return false;
  const parts = thread.session_key.split(":");
  return parts.length === 4 && parts[3] === "dm";
}

function isConflictApiError(error: unknown): boolean {
  return parseApiError(error)?.code === "PROJECT_TASK_LINK_CONFLICT";
}

function mergeUniqueTasks(
  previous: ProjectTask[],
  incoming: ProjectTask[],
): ProjectTask[] {
  const seen = new Set(previous.map((task) => task.thread_id));
  const merged = [...previous];
  for (const task of incoming) {
    if (seen.has(task.thread_id)) continue;
    seen.add(task.thread_id);
    merged.push(task);
  }
  return merged;
}

interface Props {
  projectId: string;
  /**
   * Roster already loaded by `ProjectDetail` (never re-fetched here).
   * `null` means the roster request failed: granting stays disabled with an
   * honest informational state, while revoking existing grants still works.
   */
  members: ProjectMember[] | null;
}

export default function ProjectTasks({ projectId, members }: Props) {
  const { t } = useTranslation();
  const translationRef = useRef(t);
  translationRef.current = t;
  const timezone = useServerTimezone();
  const { agents, activeAgentId } = useAgent();

  const [tasks, setTasks] = useState<ProjectTask[]>([]);
  const [tasksKey, setTasksKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [listError, setListError] = useState<unknown>(null);
  /** Search is keyed by project so a switch cannot reuse the old keyword. */
  const [search, setSearch] = useState<{ projectId: string; q: string }>(
    () => ({ projectId, q: "" }),
  );
  /** Scope is keyed by project too: a switch always falls back to `own`. */
  const [scopeState, setScopeState] = useState<{
    projectId: string;
    scope: ProjectTaskScope;
  }>(() => ({ projectId, scope: "own" }));
  const [reloadKey, setReloadKey] = useState(0);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [busyThreadIds, setBusyThreadIds] = useState<string[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickedAgentId, setPickedAgentId] = useState<string | null>(null);
  const [candidates, setCandidates] = useState<OctopThread[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesError, setCandidatesError] = useState<unknown>(null);
  const [candidateReloadKey, setCandidateReloadKey] = useState(0);
  const [readerTarget, setReaderTarget] = useState<ProjectTask | null>(null);
  const [readerDetail, setReaderDetail] = useState<ProjectTask | null>(null);
  const [readerLoading, setReaderLoading] = useState(false);
  const [readerError, setReaderError] = useState<unknown>(null);
  const [shareTarget, setShareTarget] = useState<ProjectTask | null>(null);
  const [shares, setShares] = useState<ProjectTaskShare[] | null>(null);
  const [sharesLoading, setSharesLoading] = useState(false);
  const [sharesError, setSharesError] = useState<unknown>(null);
  const [sharesReloadKey, setSharesReloadKey] = useState(0);
  const [shareBusyUserId, setShareBusyUserId] = useState<number | null>(null);
  const [shareActionError, setShareActionError] = useState<string | null>(null);
  const [readerMessages, setReaderMessages] = useState<ReaderMessages | null>(
    null,
  );
  const [messagesMoreLoading, setMessagesMoreLoading] = useState(false);
  const [messagesError, setMessagesError] = useState<unknown>(null);
  const [messagesMoreError, setMessagesMoreError] = useState<unknown>(null);
  const [messagesReloadKey, setMessagesReloadKey] = useState(0);
  /** Monotonic guards so late responses never overwrite fresher results. */
  const fetchSeq = useRef(0);
  const candidateSeq = useRef(0);
  const readerSeq = useRef(0);
  const messagesSeq = useRef(0);
  const sharesSeq = useRef(0);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

  const query = search.projectId === projectId ? search.q : "";
  const scope = scopeState.projectId === projectId ? scopeState.scope : "own";
  const listKey = `${projectId}\u0000${scope}\u0000${query}`;
  /** Rows are only rendered when they belong to the current project + scope. */
  const loaded = tasksKey === listKey;

  const runningAgents = useMemo(
    () => selectEnabledExperts(agents, activeAgentId),
    [agents, activeAgentId],
  );
  const fallbackAgentId =
    activeAgentId != null &&
    runningAgents.some((agent) => agent.agent_id === activeAgentId)
      ? activeAgentId
      : runningAgents[0]?.agent_id ?? null;
  const pickerAgentId = pickedAgentId ?? fallbackAgentId;

  const agentNames = useMemo(
    () =>
      new Map(
        agents.map((agent): [string, string] => [agent.agent_id, agent.name]),
      ),
    [agents],
  );
  const memberNames = useMemo(
    () =>
      new Map(
        (members ?? []).map((member): [number, string] => [
          member.user_id,
          member.username,
        ]),
      ),
    [members],
  );

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);
  const reloadCandidates = useCallback(
    () => setCandidateReloadKey((key) => key + 1),
    [],
  );
  const closePicker = useCallback(() => {
    candidateSeq.current += 1;
    setPickerOpen(false);
    setPickedAgentId(null);
    setCandidates([]);
    setCandidatesError(null);
    setCandidatesLoading(false);
  }, []);
  const closeReader = useCallback(() => {
    readerSeq.current += 1;
    messagesSeq.current += 1;
    setReaderTarget(null);
    setReaderDetail(null);
    setReaderError(null);
    setReaderLoading(false);
    setReaderMessages(null);
    setMessagesMoreLoading(false);
    setMessagesError(null);
    setMessagesMoreError(null);
  }, []);

  /**
   * A detail or text 404 may mean either the card was removed or only the
   * separate text grant was revoked. Clear private text immediately and let
   * the authoritative list decide whether the card remains visible.
   */
  const resetReaderAccess = useCallback(() => {
    readerSeq.current += 1;
    messagesSeq.current += 1;
    setReaderTarget(null);
    setReaderDetail(null);
    setReaderError(null);
    setReaderLoading(false);
    setReaderMessages(null);
    setMessagesMoreLoading(false);
    setMessagesError(null);
    setMessagesMoreError(null);
    setActionError(
      translationRef.current(
        "projects.tasks.readerRevoked",
        "任务分享或文本权限已变化，列表已刷新。",
      ),
    );
    reload();
  }, [reload]);
  const closeShare = useCallback(() => {
    sharesSeq.current += 1;
    setShareTarget(null);
    setShares(null);
    setSharesError(null);
    setSharesLoading(false);
    setShareBusyUserId(null);
    setShareActionError(null);
  }, []);

  // A project or scope switch must never show the previous view's private
  // rows, alerts or open dialogs.
  useEffect(() => {
    setConflict(false);
    setActionError(null);
    closePicker();
    closeReader();
    closeShare();
  }, [projectId, scope, closePicker, closeReader, closeShare]);

  const load = useCallback(
    async (offset: number, append: boolean) => {
      const key = `${projectId}\u0000${scope}\u0000${query}`;
      const seq = ++fetchSeq.current;
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      try {
        const data = await projectTasksApi.list(projectId, {
          scope,
          q: query,
          limit: PROJECT_TASKS_PAGE_SIZE,
          offset,
        });
        if (seq !== fetchSeq.current) return;
        setTasks((previous) =>
          append ? mergeUniqueTasks(previous, data.items) : data.items,
        );
        setHasMore(data.has_more);
        setNextOffset(offset + data.items.length);
        setListError(null);
        setTasksKey(key);
      } catch (err: unknown) {
        if (seq !== fetchSeq.current) return;
        // A failed first page cannot reuse rows from the prior project or
        // scope. Keep already confirmed rows only for a later-page failure.
        if (!append || isNotFoundApiError(err)) {
          setTasks([]);
          setHasMore(false);
          setNextOffset(0);
        }
        setListError(err);
        setTasksKey(key);
      } finally {
        if (seq === fetchSeq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [projectId, scope, query],
  );

  useEffect(() => {
    void load(0, false);
  }, [load, reloadKey]);

  useEffect(() => {
    if (!pickerOpen || pickerAgentId == null) return;
    const seq = ++candidateSeq.current;
    setCandidatesLoading(true);
    octopThreadsApi
      .list(pickerAgentId, TASK_CANDIDATE_LIMIT)
      .then((rows) => {
        if (seq !== candidateSeq.current) return;
        setCandidates(rows.filter(isDashboardDmCandidate));
        setCandidatesError(null);
      })
      .catch((err: unknown) => {
        if (seq !== candidateSeq.current) return;
        setCandidates([]);
        setCandidatesError(err);
      })
      .finally(() => {
        if (seq === candidateSeq.current) setCandidatesLoading(false);
      });
  }, [pickerOpen, pickerAgentId, candidateReloadKey]);

  useEffect(() => {
    if (shareTarget == null) return;
    if (shareTarget.project_id !== projectId) return;
    const seq = ++sharesSeq.current;
    setSharesLoading(true);
    projectTasksApi
      .shares(projectId, shareTarget.thread_id)
      .then((data) => {
        if (seq !== sharesSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        setShares(data.items);
        setSharesError(null);
      })
      .catch((err: unknown) => {
        if (seq !== sharesSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        setShares([]);
        setSharesError(err);
      })
      .finally(() => {
        if (seq === sharesSeq.current) setSharesLoading(false);
      });
  }, [projectId, shareTarget, sharesReloadKey]);

  // Text is only fetched after a fresh detail says `can_read_text`; a card-only
  // reader must never hit the messages route. The card detail is the
  // authorization source, never the list row the reader clicked.
  useEffect(() => {
    if (readerTarget == null || readerDetail == null) return;
    if (readerDetail.project_id !== projectId) return;
    if (readerDetail.thread_id !== readerTarget.thread_id) return;
    if (!readerDetail.can_read_text) {
      messagesSeq.current += 1;
      setReaderMessages(null);
      setMessagesMoreLoading(false);
      setMessagesError(null);
      setMessagesMoreError(null);
      return;
    }
    const threadId = readerDetail.thread_id;
    const seq = ++messagesSeq.current;
    setMessagesMoreLoading(false);
    setMessagesError(null);
    setMessagesMoreError(null);
    projectTasksApi
      .messages(projectId, threadId, {
        limit: PROJECT_TASK_MESSAGE_PAGE_SIZE,
      })
      .then((data) => {
        if (seq !== messagesSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        setReaderMessages({
          threadId,
          status: data.status,
          items: data.items,
          hasMore: data.has_more,
          nextBeforeSeq: data.next_before_seq,
        });
      })
      .catch((err: unknown) => {
        if (seq !== messagesSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        if (isNotFoundApiError(err)) {
          resetReaderAccess();
        } else {
          setReaderMessages(null);
          setMessagesError(err);
        }
      });
  }, [
    projectId,
    readerTarget,
    readerDetail,
    messagesReloadKey,
    resetReaderAccess,
  ]);

  const loadOlderMessages = async () => {
    if (readerMessages == null) return;
    const threadId = readerMessages.threadId;
    const beforeSeq = readerMessages.nextBeforeSeq;
    if (!readerMessages.hasMore || beforeSeq == null) return;
    const seq = ++messagesSeq.current;
    setMessagesMoreLoading(true);
    setMessagesMoreError(null);
    try {
      const data = await projectTasksApi.messages(projectId, threadId, {
        limit: PROJECT_TASK_MESSAGE_PAGE_SIZE,
        beforeSeq,
      });
      if (seq !== messagesSeq.current) return;
      if (projectId !== currentProjectId.current) return;
      setReaderMessages((previous) => {
        if (previous == null || previous.threadId !== threadId) return previous;
        const seen = new Set(previous.items.map((item) => item.seq));
        const older = data.items.filter((item) => !seen.has(item.seq));
        return {
          threadId,
          status: data.status,
          items: [...previous.items, ...older],
          hasMore: data.has_more,
          nextBeforeSeq: data.next_before_seq,
        };
      });
    } catch (err: unknown) {
      if (seq !== messagesSeq.current) return;
      if (projectId !== currentProjectId.current) return;
      if (isNotFoundApiError(err)) {
        resetReaderAccess();
      } else {
        setMessagesMoreError(err);
      }
    } finally {
      if (seq === messagesSeq.current) setMessagesMoreLoading(false);
    }
  };

  const linkTask = async (thread: OctopThread) => {
    setBusyThreadIds((previous) => [...previous, thread.thread_id]);
    setActionError(null);
    try {
      await projectTasksApi.link(projectId, thread.thread_id);
      if (projectId !== currentProjectId.current) return;
      void message.success(
        t(
          "projects.tasks.attachSuccess",
          "已关联到项目；对话内容不会共享给其他成员。",
        ),
      );
      closePicker();
      reload();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (isConflictApiError(err)) {
        setConflict(true);
        closePicker();
        reload();
      } else {
        setActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.attachFailed", "关联任务失败"),
            t,
          ),
        );
        if (isNotFoundApiError(err)) reloadCandidates();
      }
    } finally {
      setBusyThreadIds((previous) =>
        previous.filter((id) => id !== thread.thread_id),
      );
    }
  };

  const unlinkTask = async (task: ProjectTask) => {
    setBusyThreadIds((previous) => [...previous, task.thread_id]);
    setActionError(null);
    try {
      await projectTasksApi.unlink(projectId, task.thread_id);
      if (projectId !== currentProjectId.current) return;
      setTasks((previous) =>
        previous.filter((item) => item.thread_id !== task.thread_id),
      );
      void message.success(
        t("projects.tasks.detached", "已从项目移除，原对话与历史仍保留。"),
      );
      reload();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (isNotFoundApiError(err)) {
        // A 404 may mean this one link was already removed while the project
        // remains accessible. Reload the authoritative list; that request
        // separately decides whether the project itself was revoked.
        setTasks((previous) =>
          previous.filter((item) => item.thread_id !== task.thread_id),
        );
        reload();
      } else {
        setActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.detachFailed", "取消关联失败"),
            t,
          ),
        );
      }
    } finally {
      setBusyThreadIds((previous) =>
        previous.filter((id) => id !== task.thread_id),
      );
    }
  };

  const openReader = (task: ProjectTask) => {
    const seq = ++readerSeq.current;
    messagesSeq.current += 1;
    setReaderTarget(task);
    setReaderDetail(null);
    setReaderError(null);
    setReaderLoading(true);
    setReaderMessages(null);
    setMessagesMoreLoading(false);
    setMessagesError(null);
    setMessagesMoreError(null);
    projectTasksApi
      .get(projectId, task.thread_id)
      .then((detail) => {
        if (seq !== readerSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        setReaderDetail(detail);
        setReaderError(null);
      })
      .catch((err: unknown) => {
        if (seq !== readerSeq.current) return;
        if (projectId !== currentProjectId.current) return;
        if (isNotFoundApiError(err)) {
          // Access revoked or card removed server-side: drop the stale card
          // and refresh the authoritative list instead of showing old data.
          resetReaderAccess();
        } else {
          setReaderError(err);
        }
      })
      .finally(() => {
        if (seq === readerSeq.current) setReaderLoading(false);
      });
  };

  const openShare = (task: ProjectTask) => {
    setShareTarget(task);
    setShares(null);
    setSharesError(null);
    setShareActionError(null);
    setShareBusyUserId(null);
    setSharesReloadKey((key) => key + 1);
  };

  const reloadShares = () => setSharesReloadKey((key) => key + 1);

  const grantShare = async (member: ProjectMember) => {
    if (shareTarget == null) return;
    const threadId = shareTarget.thread_id;
    setShareBusyUserId(member.user_id);
    setShareActionError(null);
    try {
      await projectTasksApi.share(projectId, threadId, member.user_id);
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      void message.success(
        t(
          "projects.tasks.shareGrantSuccess",
          "已分享给 {{name}}；正文和附件仍私密。",
          { name: member.username },
        ),
      );
      reloadShares();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      if (isNotFoundApiError(err)) {
        closeShare();
        setTasks((previous) =>
          previous.filter((item) => item.thread_id !== threadId),
        );
        setActionError(
          t(
            "projects.tasks.shareTargetGone",
            "任务或成员状态已变化，分享失败；列表已刷新。",
          ),
        );
        reload();
      } else {
        setShareActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.shareGrantFailed", "分享失败"),
            t,
          ),
        );
      }
    } finally {
      setShareBusyUserId(null);
    }
  };

  const revokeShare = async (userId: number, username: string) => {
    if (shareTarget == null) return;
    const threadId = shareTarget.thread_id;
    setShareBusyUserId(userId);
    setShareActionError(null);
    try {
      await projectTasksApi.revoke(projectId, threadId, userId);
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      // A revoked card drops its text grant too: clear that UI immediately,
      // then reconcile with the authoritative list.
      setShares((previous) =>
        previous == null
          ? previous
          : previous.filter((grant) => grant.user_id !== userId),
      );
      void message.success(
        t("projects.tasks.shareRevokeSuccess", "已撤回 {{name}} 的分享。", {
          name: username,
        }),
      );
      reloadShares();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      if (isNotFoundApiError(err)) {
        closeShare();
        setTasks((previous) =>
          previous.filter((item) => item.thread_id !== threadId),
        );
        setActionError(
          t(
            "projects.tasks.shareTargetGone",
            "任务或成员状态已变化，分享失败；列表已刷新。",
          ),
        );
        reload();
      } else {
        setShareActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.shareRevokeFailed", "撤回分享失败"),
            t,
          ),
        );
      }
    } finally {
      setShareBusyUserId(null);
    }
  };

  const grantText = async (userId: number, username: string) => {
    if (shareTarget == null) return;
    const threadId = shareTarget.thread_id;
    setShareBusyUserId(userId);
    setShareActionError(null);
    try {
      await projectTasksApi.grantText(projectId, threadId, userId);
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      setShares((previous) =>
        previous == null
          ? previous
          : previous.map((grant) =>
              grant.user_id === userId
                ? { ...grant, can_read_text: true }
                : grant,
            ),
      );
      void message.success(
        t(
          "projects.tasks.shareTextGrantSuccess",
          "已授予 {{name}} 对话文本；附件、工具与思考过程仍私密。",
          { name: username },
        ),
      );
      reloadShares();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      if (isNotFoundApiError(err)) {
        closeShare();
        setTasks((previous) =>
          previous.filter((item) => item.thread_id !== threadId),
        );
        setActionError(
          t(
            "projects.tasks.shareTargetGone",
            "任务或成员状态已变化，分享失败；列表已刷新。",
          ),
        );
        reload();
      } else {
        setShareActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.shareTextGrantFailed", "授予文本失败"),
            t,
          ),
        );
      }
    } finally {
      setShareBusyUserId(null);
    }
  };

  const revokeText = async (userId: number, username: string) => {
    if (shareTarget == null) return;
    const threadId = shareTarget.thread_id;
    setShareBusyUserId(userId);
    setShareActionError(null);
    try {
      await projectTasksApi.revokeText(projectId, threadId, userId);
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      setShares((previous) =>
        previous == null
          ? previous
          : previous.map((grant) =>
              grant.user_id === userId
                ? { ...grant, can_read_text: false }
                : grant,
            ),
      );
      void message.success(
        t(
          "projects.tasks.shareTextRevokeSuccess",
          "已撤回 {{name}} 的文本权限；卡片分享仍保留。",
          { name: username },
        ),
      );
      reloadShares();
    } catch (err: unknown) {
      if (projectId !== currentProjectId.current) return;
      if (shareTarget.thread_id !== threadId) return;
      if (isNotFoundApiError(err)) {
        closeShare();
        setTasks((previous) =>
          previous.filter((item) => item.thread_id !== threadId),
        );
        setActionError(
          t(
            "projects.tasks.shareTargetGone",
            "任务或成员状态已变化，分享失败；列表已刷新。",
          ),
        );
        reload();
      } else {
        setShareActionError(
          apiErrorMessage(
            err,
            t("projects.tasks.shareTextRevokeFailed", "撤回文本权限失败"),
            t,
          ),
        );
      }
    } finally {
      setShareBusyUserId(null);
    }
  };

  const openPicker = () => {
    setActionError(null);
    setPickerOpen(true);
  };

  const linkedThreadIds = useMemo(
    () => new Set(tasks.map((task) => task.thread_id)),
    [tasks],
  );

  const secondaryStyle: React.CSSProperties = {
    fontSize: 12,
    color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
  };

  const rowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    padding: "8px 0",
    borderBottom: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
  };

  const disableReason = (
    label: string,
    reason: string,
    icon: React.ReactNode,
    testId: string,
  ) => (
    <Tooltip title={reason}>
      <Button
        size="small"
        disabled
        icon={icon}
        data-testid={testId}
        aria-label={label}
      >
        {label}
      </Button>
    </Tooltip>
  );

  const scopeHint =
    scope === "own"
      ? t(
          "projects.tasks.privacyHintOwn",
          "项目任务默认私密；卡片与对话文本都需任务本人分别授权给指定成员，附件仍私密。",
        )
      : scope === "all"
      ? t(
          "projects.tasks.privacyHintAll",
          "“全部任务”只包含你关联的任务和被分享给你的卡片，不代表项目内所有成员的任务可见。",
        )
      : t(
          "projects.tasks.privacyHintShared",
          "只读视图：卡片由任务本人分享；对话文本需单独授权，附件与运行流仍私密。",
        );

  const renderTask = (task: ProjectTask) => {
    const title =
      task.title?.trim() || t("projects.tasks.untitled", "未命名任务");
    const agentName = agentNames.get(task.agent_id) ?? task.agent_id;
    const busy = busyThreadIds.includes(task.thread_id);
    const isReader = task.access === "reader";
    if (isReader) {
      const ownerName =
        memberNames.get(task.owner_user_id) ??
        t("projects.tasks.shareUnknownMember", "用户 #{{id}}", {
          id: task.owner_user_id,
        });
      return (
        <div
          key={task.thread_id}
          data-testid={`project-task-${task.thread_id}`}
          style={{
            background: "var(--fn-bg-elevated, #fff)",
            border: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
            borderRadius: 8,
            padding: 10,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              gap: 8,
            }}
          >
            <button
              type="button"
              onClick={() => openReader(task)}
              style={{
                flex: 1,
                minWidth: 0,
                textAlign: "left",
                background: "none",
                border: "none",
                padding: 0,
                cursor: "pointer",
                color: "var(--fn-color-primary, #1677ff)",
                fontWeight: 600,
                fontSize: 14,
                wordBreak: "break-word",
              }}
            >
              {title}
            </button>
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              {t("projects.tasks.readerTag", "分享给我的 · 只读")}
            </Tag>
          </div>
          <div
            style={{
              display: "flex",
              gap: 8,
              flexWrap: "wrap",
              alignItems: "center",
              marginTop: 6,
            }}
          >
            <Tag style={{ marginInlineEnd: 0 }}>{agentName}</Tag>
            <span style={secondaryStyle}>
              {t("projects.tasks.readerOwnerLabel", "任务所有者")}：{ownerName}
            </span>
            <span style={secondaryStyle}>
              {t("projects.tasks.activeAt", "最近活动于 {{time}}", {
                time: formatServerDateTime(
                  task.last_active > 0 ? task.last_active : task.created_at,
                  timezone,
                ),
              })}
            </span>
          </div>
        </div>
      );
    }
    return (
      <div
        key={task.thread_id}
        data-testid={`project-task-${task.thread_id}`}
        style={{
          background: "var(--fn-bg-elevated, #fff)",
          border: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
          borderRadius: 8,
          padding: 10,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            justifyContent: "space-between",
            gap: 8,
          }}
        >
          <Link
            to={`/chat/${encodeURIComponent(
              task.agent_id,
            )}/${encodeURIComponent(task.thread_id)}`}
            style={{
              flex: 1,
              minWidth: 0,
              fontWeight: 600,
              fontSize: 14,
              wordBreak: "break-word",
            }}
          >
            {title}
          </Link>
          <div
            style={{
              display: "inline-flex",
              alignItems: "center",
              gap: 4,
              flexShrink: 0,
            }}
          >
            <Button
              size="small"
              type="text"
              icon={<Share2 size={14} />}
              aria-label={t(
                "projects.tasks.shareActionNamed",
                "分享任务：{{title}}",
                { title },
              )}
              onClick={() => openShare(task)}
            >
              {t("projects.tasks.shareAction", "分享")}
            </Button>
            <Popconfirm
              title={t(
                "projects.tasks.detachConfirm",
                "从项目移除这条任务？原对话与历史会保留。",
              )}
              okText={t("projects.tasks.detachOk", "确认移除")}
              cancelText={t("common.cancel", "取消")}
              okButtonProps={{ danger: true, disabled: busy }}
              onConfirm={() => void unlinkTask(task)}
            >
              <Button
                size="small"
                type="text"
                danger
                icon={<Link2Off size={14} />}
                disabled={busy}
                aria-label={t(
                  "projects.tasks.detachNamed",
                  "取消关联：{{title}}",
                  {
                    title,
                  },
                )}
              >
                {t("projects.tasks.detach", "取消关联")}
              </Button>
            </Popconfirm>
          </div>
        </div>
        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            alignItems: "center",
            marginTop: 6,
          }}
        >
          <Tag style={{ marginInlineEnd: 0 }}>{agentName}</Tag>
          <span style={secondaryStyle}>
            {t("projects.tasks.sourceLabel", "来源")}：
            {task.source === "manual"
              ? t("projects.tasks.sourceManual", "手动关联")
              : task.source}
          </span>
          <span style={secondaryStyle}>
            {t("projects.tasks.activeAt", "最近活动于 {{time}}", {
              time: formatServerDateTime(
                task.last_active > 0 ? task.last_active : task.created_at,
                timezone,
              ),
            })}
          </span>
        </div>
      </div>
    );
  };

  const candidatesBody: React.ReactNode = (() => {
    if (candidatesLoading && candidates.length === 0) {
      return (
        <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
          <Spin />
        </div>
      );
    }
    if (candidatesError != null) {
      return (
        <Alert
          type="error"
          showIcon
          message={apiErrorMessage(
            candidatesError,
            t("projects.tasks.attachLoadFailed", "加载最近任务失败"),
            t,
          )}
          action={
            <Button size="small" onClick={reloadCandidates}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      );
    }
    if (candidates.length === 0) {
      return (
        <div
          style={{ ...secondaryStyle, textAlign: "center", padding: "16px 0" }}
        >
          {t(
            "projects.tasks.attachEmpty",
            "该专家最近没有可关联的 Dashboard 私聊任务。",
          )}
        </div>
      );
    }
    return (
      <div style={{ maxHeight: 320, overflowY: "auto" }}>
        {candidates.map((thread) => {
          const linked = linkedThreadIds.has(thread.thread_id);
          const busy = busyThreadIds.includes(thread.thread_id);
          return (
            <div
              key={thread.thread_id}
              data-testid={`task-candidate-${thread.thread_id}`}
              style={{
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                gap: 8,
                padding: "8px 0",
                borderBottom:
                  "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
              }}
            >
              <div style={{ flex: 1, minWidth: 0 }}>
                <div
                  style={{
                    fontWeight: 500,
                    fontSize: 13,
                    wordBreak: "break-word",
                  }}
                >
                  {thread.title?.trim() ||
                    t("projects.tasks.untitledThread", "未命名对话")}
                </div>
                <div style={secondaryStyle}>
                  {formatServerDateTime(
                    thread.last_active > 0
                      ? thread.last_active
                      : thread.created_at,
                    timezone,
                  )}
                </div>
              </div>
              <Button
                size="small"
                type="primary"
                ghost
                disabled={linked || busy}
                loading={busy}
                onClick={() => void linkTask(thread)}
              >
                {linked
                  ? t("projects.tasks.attachAlreadyLinked", "已关联")
                  : t("projects.tasks.attachAction", "关联到项目")}
              </Button>
            </div>
          );
        })}
      </div>
    );
  })();

  const notFoundError = listError != null && isNotFoundApiError(listError);
  const anySearch = query.trim().length > 0;

  let body: React.ReactNode;
  if (!loaded) {
    body = (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  } else if (notFoundError) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.tasks.notFound", "项目不存在或你无权访问")}
        description={t(
          "projects.tasks.notFoundHint",
          "项目任务数据已清除；重新加载成功前不会显示旧内容。",
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (loading && tasks.length === 0) {
    body = (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  } else if (tasks.length === 0 && listError == null) {
    body =
      scope === "shared" ? (
        <EmptyState
          variant="empty"
          title={t("projects.tasks.emptySharedTitle", "还没有分享给我的任务")}
          description={t(
            "projects.tasks.emptySharedHint",
            "任务本人需先显式分享卡片；对话文本还需单独授权，附件仍私密。",
          )}
        />
      ) : (
        <EmptyState
          variant="empty"
          title={
            anySearch
              ? t("projects.tasks.emptySearchTitle", "没有匹配的任务")
              : t("projects.tasks.emptyTitle", "还没有关联任务")
          }
          description={
            anySearch
              ? t("projects.tasks.emptySearchHint", "换个关键词，或清除搜索。")
              : t(
                  "projects.tasks.emptyHint",
                  "关联一条已有的 Dashboard 对话，归属到项目；默认仅本人可见，随后可分别授权卡片与文本。",
                )
          }
          actionLabel={
            anySearch ? undefined : t("projects.tasks.attach", "关联我的任务")
          }
          onAction={anySearch ? undefined : openPicker}
        />
      );
  } else if (tasks.length === 0) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.tasks.loadFailed", "加载项目任务失败")}
        description={apiErrorMessage(
          listError,
          t("projects.tasks.loadFailed", "加载项目任务失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else {
    body = (
      <div
        data-testid="project-task-list"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        {tasks.map(renderTask)}
      </div>
    );
  }

  const readerTask = readerDetail ?? readerTarget;
  const readerTitle =
    readerTask?.title?.trim() || t("projects.tasks.untitled", "未命名任务");
  const readerOwnerName =
    readerTask == null
      ? ""
      : memberNames.get(readerTask.owner_user_id) ??
        t("projects.tasks.shareUnknownMember", "用户 #{{id}}", {
          id: readerTask.owner_user_id,
        });

  const shareMembers =
    shareTarget == null
      ? []
      : (members ?? []).filter(
          (member) => member.user_id !== shareTarget.owner_user_id,
        );
  const grantsByUserId = useMemo(
    () =>
      new Map(
        (shares ?? []).map((grant): [number, ProjectTaskShare] => [
          grant.user_id,
          grant,
        ]),
      ),
    [shares],
  );

  /**
   * Separate text controls for an already card-shared member. Granting text
   * always goes through its own confirmation that names the sensitive-content,
   * archive-history and attachment limits; card sharing itself never flips
   * `can_read_text`.
   */
  const renderTextGrantAction = (grant: ProjectTaskShare, name: string) => {
    const busy = shareBusyUserId !== null;
    if (grant.can_read_text) {
      return (
        <>
          <Tag color="gold" style={{ marginInlineEnd: 0 }}>
            {t("projects.tasks.shareTextCanReadTag", "可读文本")}
          </Tag>
          <Popconfirm
            title={t(
              "projects.tasks.shareTextRevokeConfirm",
              "撤回后 {{name}} 将不再看到对话文本，但任务卡片分享仍保留。",
              { name },
            )}
            okText={t("projects.tasks.shareTextRevokeOk", "确认撤回文本")}
            cancelText={t("common.cancel", "取消")}
            okButtonProps={{ danger: true }}
            onConfirm={() => void revokeText(grant.user_id, name)}
          >
            <Button
              size="small"
              disabled={busy}
              loading={shareBusyUserId === grant.user_id}
              aria-label={t(
                "projects.tasks.shareTextRevokeNamed",
                "撤回 {{name}} 的文本权限",
                { name },
              )}
            >
              {t("projects.tasks.shareTextRevoke", "撤回文本")}
            </Button>
          </Popconfirm>
        </>
      );
    }
    return (
      <Popconfirm
        title={t(
          "projects.tasks.shareTextGrantConfirm",
          "允许 {{name}} 查看本任务的历史与后续同步文本？文本可能包含用户粘贴的路径、链接或敏感内容；附件、工具与思考过程仍私密。版本化历史存储暂不提供正文，界面会显示待支持/未同步，不能保证所有记录都可见。",
          { name },
        )}
        okText={t("projects.tasks.shareTextGrantOk", "确认授予文本")}
        cancelText={t("common.cancel", "取消")}
        onConfirm={() => void grantText(grant.user_id, name)}
      >
        <Button
          size="small"
          disabled={busy}
          loading={shareBusyUserId === grant.user_id}
          aria-label={t(
            "projects.tasks.shareTextGrantNamed",
            "授予 {{name}} 文本权限",
            { name },
          )}
        >
          {t("projects.tasks.shareTextGrant", "授予文本")}
        </Button>
      </Popconfirm>
    );
  };

  const renderGranteeRows = (
    grants: ProjectTaskShare[],
    nameFor: (grant: ProjectTaskShare) => string,
  ) => (
    <div data-testid="task-share-grantee-list">
      <div style={{ ...secondaryStyle, marginBottom: 4 }}>
        {t("projects.tasks.shareGrantedHeading", "已分享成员")}
      </div>
      {grants.map((grant) => {
        const name = nameFor(grant);
        const busy = shareBusyUserId !== null;
        return (
          <div
            key={grant.user_id}
            data-testid={`task-share-grantee-${grant.user_id}`}
            style={rowStyle}
          >
            <span style={{ fontSize: 13, wordBreak: "break-word" }}>
              {name}
            </span>
            <span
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                flexWrap: "wrap",
              }}
            >
              {renderTextGrantAction(grant, name)}
              <Popconfirm
                title={t(
                  "projects.tasks.shareRevokeConfirm",
                  "撤回后 {{name}} 将不再看到这张任务卡片。",
                  { name },
                )}
                okText={t("projects.tasks.shareRevokeOk", "确认撤回")}
                cancelText={t("common.cancel", "取消")}
                okButtonProps={{ danger: true }}
                onConfirm={() => void revokeShare(grant.user_id, name)}
              >
                <Button
                  size="small"
                  danger
                  disabled={busy}
                  loading={shareBusyUserId === grant.user_id}
                  aria-label={t(
                    "projects.tasks.shareRevokeNamed",
                    "撤回 {{name}} 的分享",
                    { name },
                  )}
                >
                  {t("projects.tasks.shareRevoke", "撤回分享")}
                </Button>
              </Popconfirm>
            </span>
          </div>
        );
      })}
    </div>
  );

  const orderedReaderMessages =
    readerMessages == null
      ? null
      : [...readerMessages.items].sort((a, b) => a.seq - b.seq);
  const hasReaderMessages =
    orderedReaderMessages != null && orderedReaderMessages.length > 0;

  const readerTextBody: React.ReactNode = (() => {
    if (readerMessages == null) {
      if (messagesError != null) {
        return (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              messagesError,
              t("projects.tasks.readerTextLoadFailed", "加载对话文本失败"),
              t,
            )}
            action={
              <Button
                size="small"
                onClick={() => setMessagesReloadKey((key) => key + 1)}
              >
                {t("common.retry", "重试")}
              </Button>
            }
          />
        );
      }
      return (
        <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
          <Spin />
        </div>
      );
    }
    if (readerMessages.status === "pending") {
      return (
        <Alert
          type="warning"
          showIcon
          message={t(
            "projects.tasks.readerTextPendingTitle",
            "对话文本暂不可读",
          )}
          description={t(
            "projects.tasks.readerTextPendingHint",
            "文本尚未同步，或该任务使用了本片暂不支持的版本化历史存储；这不代表对话为空。",
          )}
        />
      );
    }
    return (
      <>
        <div style={{ ...secondaryStyle, marginBottom: 8 }}>
          {t(
            "projects.tasks.readerTextPrivacyHint",
            "以纯文本只读显示；不解析 Markdown/HTML，也不自动加载链接或图片。",
          )}
        </div>
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t(
            "projects.tasks.readerTextOmitted",
            "仅显示已支持且已完成投影的纯文本；部分非文本或超大消息未显示，这里不是完整历史。",
          )}
        />
        {hasReaderMessages ? (
          <div
            data-testid="project-task-reader-text"
            aria-label={t("projects.tasks.readerTextTitle", "对话文本（只读）")}
            style={{
              maxHeight: 360,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 10,
              marginBottom: 12,
            }}
          >
            {orderedReaderMessages.map((item) => (
              <div
                key={item.seq}
                data-testid={`project-task-reader-text-${item.seq}`}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    flexWrap: "wrap",
                    marginBottom: 2,
                  }}
                >
                  <Tag style={{ marginInlineEnd: 0 }}>
                    {item.role === "user"
                      ? t("projects.tasks.readerRoleUser", "任务所有者")
                      : t("projects.tasks.readerRoleAssistant", "助手")}
                  </Tag>
                  <span style={secondaryStyle}>
                    {formatServerDateTime(item.created_at, timezone)}
                  </span>
                  {item.truncated && (
                    <Tag color="orange" style={{ marginInlineEnd: 0 }}>
                      {t(
                        "projects.tasks.readerTextTruncated",
                        "内容过长，已截断显示",
                      )}
                    </Tag>
                  )}
                </div>
                <div
                  style={{
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-word",
                    fontSize: 13,
                    lineHeight: 1.6,
                  }}
                >
                  {item.text}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div
            data-testid="project-task-reader-text-empty"
            style={{ ...secondaryStyle, padding: "12px 0" }}
          >
            <div>
              {t("projects.tasks.readerTextEmpty", "没有可显示的文本消息。")}
            </div>
            <div>
              {t(
                "projects.tasks.readerTextEmptyHint",
                "该任务可能只有非文本内容，或文本尚未同步。",
              )}
            </div>
          </div>
        )}
        {readerMessages.hasMore && readerMessages.nextBeforeSeq != null ? (
          <div style={{ textAlign: "center" }}>
            <Button
              loading={messagesMoreLoading}
              onClick={() => void loadOlderMessages()}
            >
              {t("projects.tasks.readerTextLoadMore", "加载更早的文本")}
            </Button>
          </div>
        ) : null}
        {messagesMoreError != null && (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message={apiErrorMessage(
              messagesMoreError,
              t("projects.tasks.readerTextLoadFailed", "加载对话文本失败"),
              t,
            )}
            action={
              <Button size="small" onClick={() => void loadOlderMessages()}>
                {t("common.retry", "重试")}
              </Button>
            }
          />
        )}
      </>
    );
  })();

  const shareBody: React.ReactNode =
    shareTarget == null ? null : members == null ? (
      <>
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t(
            "projects.tasks.shareRosterFailed",
            "成员名单加载失败，暂时无法选择分享对象；请刷新页面后重试。",
          )}
        />
        {sharesLoading && shares == null ? (
          <div
            style={{ display: "flex", justifyContent: "center", padding: 16 }}
          >
            <Spin />
          </div>
        ) : null}
        {sharesError != null && (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              sharesError,
              t("projects.tasks.shareLoadFailed", "加载已分享成员失败"),
              t,
            )}
            action={
              <Button size="small" onClick={reloadShares}>
                {t("common.retry", "重试")}
              </Button>
            }
          />
        )}
        {shares != null &&
          shares.length > 0 &&
          renderGranteeRows(shares, (grant) =>
            t("projects.tasks.shareUnknownMember", "用户 #{{id}}", {
              id: grant.user_id,
            }),
          )}
      </>
    ) : sharesLoading && shares == null ? (
      <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
        <Spin />
      </div>
    ) : sharesError != null ? (
      <Alert
        type="error"
        showIcon
        message={apiErrorMessage(
          sharesError,
          t("projects.tasks.shareLoadFailed", "加载已分享成员失败"),
          t,
        )}
        action={
          <Button size="small" onClick={reloadShares}>
            {t("common.retry", "重试")}
          </Button>
        }
      />
    ) : shareMembers.length === 0 ? (
      <div style={{ ...secondaryStyle, padding: "12px 0" }}>
        {t("projects.tasks.shareEmpty", "项目中没有其他成员可以分享。")}
      </div>
    ) : (
      <div data-testid="task-share-member-list">
        <div style={{ ...secondaryStyle, marginBottom: 4 }}>
          {t("projects.tasks.shareMembersHeading", "项目成员")}
        </div>
        {shareMembers.map((member) => {
          const grant = grantsByUserId.get(member.user_id);
          const busy = shareBusyUserId !== null;
          const role = projectRoleTag(member.role);
          return (
            <div
              key={member.user_id}
              data-testid={`task-share-member-${member.user_id}`}
              style={rowStyle}
            >
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                  minWidth: 0,
                }}
              >
                <span style={{ fontSize: 13, wordBreak: "break-word" }}>
                  {member.username}
                </span>
                <Tag style={{ marginInlineEnd: 0 }}>
                  {t(role.labelKey, role.fallback)}
                </Tag>
              </span>
              {grant != null ? (
                <span
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    flexWrap: "wrap",
                  }}
                >
                  <Tag color="green" style={{ marginInlineEnd: 0 }}>
                    {t("projects.tasks.shareGrantedTag", "已分享")}
                  </Tag>
                  {renderTextGrantAction(grant, member.username)}
                  <Popconfirm
                    title={t(
                      "projects.tasks.shareRevokeConfirm",
                      "撤回后 {{name}} 将不再看到这张任务卡片。",
                      { name: member.username },
                    )}
                    okText={t("projects.tasks.shareRevokeOk", "确认撤回")}
                    cancelText={t("common.cancel", "取消")}
                    okButtonProps={{ danger: true }}
                    onConfirm={() =>
                      void revokeShare(member.user_id, member.username)
                    }
                  >
                    <Button
                      size="small"
                      danger
                      disabled={busy}
                      loading={shareBusyUserId === member.user_id}
                      aria-label={t(
                        "projects.tasks.shareRevokeNamed",
                        "撤回 {{name}} 的分享",
                        { name: member.username },
                      )}
                    >
                      {t("projects.tasks.shareRevoke", "撤回分享")}
                    </Button>
                  </Popconfirm>
                </span>
              ) : (
                <Popconfirm
                  title={t(
                    "projects.tasks.shareGrantConfirm",
                    "确认分享给 {{name}}？目前只共享任务标题与卡片信息，正文和附件仍私密。",
                    { name: member.username },
                  )}
                  okText={t("projects.tasks.shareGrantOk", "确认分享")}
                  cancelText={t("common.cancel", "取消")}
                  onConfirm={() => void grantShare(member)}
                >
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    disabled={busy}
                    loading={shareBusyUserId === member.user_id}
                  >
                    {t("projects.tasks.shareGrantNamed", "分享给 {{name}}", {
                      name: member.username,
                    })}
                  </Button>
                </Popconfirm>
              )}
            </div>
          );
        })}
      </div>
    );

  return (
    <div>
      <div
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 8,
        }}
      >
        <Segmented<ProjectTaskScope>
          value={scope}
          onChange={(value) => setScopeState({ projectId, scope: value })}
          aria-label={t("projects.tasks.scopeFilter", "任务范围")}
          options={[
            {
              label: (
                <span
                  title={t(
                    "projects.tasks.scopeAllHint",
                    "全部任务只包含我的任务和被分享给我的卡片，不是项目内全员任务。",
                  )}
                >
                  {t("projects.tasks.scopeAll", "全部任务")}
                </span>
              ),
              value: "all",
            },
            {
              label: t("projects.tasks.scopeOwn", "个人任务"),
              value: "own",
            },
            {
              label: (
                <span
                  title={t(
                    "projects.tasks.scopeSharedHint",
                    "分享给我的任务为只读；对话文本需任务本人另行授权，附件仍私密。",
                  )}
                >
                  {t("projects.tasks.scopeShared", "分享给我的")}
                </span>
              ),
              value: "shared",
            },
          ]}
        />
        <Input.Search
          allowClear
          style={{ maxWidth: 220 }}
          placeholder={t("projects.tasks.searchPlaceholder", "搜索任务标题")}
          aria-label={t("projects.tasks.searchPlaceholder", "搜索任务标题")}
          onChange={(event) => {
            if (event.target.value === "") setSearch({ projectId, q: "" });
          }}
          onSearch={(value) => setSearch({ projectId, q: value })}
        />
        <Tooltip title={t("projects.tasks.refresh", "刷新任务")}>
          <Button
            icon={<RefreshCw size={14} />}
            disabled={loading}
            aria-label={t("projects.tasks.refresh", "刷新任务")}
            onClick={reload}
          />
        </Tooltip>
        {scope !== "shared" && (
          <Button
            type="primary"
            icon={<Link2 size={14} />}
            onClick={openPicker}
          >
            {t("projects.tasks.attach", "关联我的任务")}
          </Button>
        )}
      </div>

      <div style={{ ...secondaryStyle, marginBottom: 8 }}>{scopeHint}</div>

      <div
        data-testid="project-task-capabilities"
        style={{
          display: "flex",
          gap: 8,
          flexWrap: "wrap",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        {disableReason(
          t("projects.tasks.local", "本地"),
          t(
            "projects.tasks.localReason",
            "项目内本地任务需先支持受控工作目录，暂未开放。",
          ),
          <HardDrive size={14} />,
          "task-capability-local",
        )}
        {disableReason(
          t("projects.tasks.cloud", "云端"),
          t(
            "projects.tasks.cloudReason",
            "项目云端任务需先支持项目资源与协作，暂未开放。",
          ),
          <Cloud size={14} />,
          "task-capability-cloud",
        )}
        {disableReason(
          t("projects.tasks.transfer", "移交"),
          t(
            "projects.tasks.transferReason",
            "任务移交需要受控的成员协作与执行队列，暂未开放。",
          ),
          <ArrowRightLeft size={14} />,
          "task-capability-transfer",
        )}
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t(
            "projects.tasks.shareCardHint",
            "卡片分享仅开放摘要；任务本人可再单独授权指定成员只读对话文本。附件仍私密。",
          )}
        </Text>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t(
            "projects.tasks.unavailableHint",
            "本地/云端与移交需要后续后端权限，当前不可用。",
          )}
        </Text>
      </div>

      {conflict && (
        <Alert
          type="warning"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={t(
            "projects.tasks.conflict",
            "这条任务已关联到其他项目或状态冲突。已刷新项目任务列表，请检查后重试。",
          )}
          onClose={() => setConflict(false)}
        />
      )}
      {actionError != null && (
        <Alert
          type="error"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={actionError}
          onClose={() => setActionError(null)}
        />
      )}
      {listError != null && !notFoundError && tasks.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={apiErrorMessage(
            listError,
            t("projects.tasks.loadFailed", "加载项目任务失败"),
            t,
          )}
          action={
            <Button size="small" onClick={reload}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      )}

      {body}

      {hasMore && !notFoundError && tasks.length > 0 && (
        <div style={{ textAlign: "center", marginTop: 12 }}>
          <Button
            loading={loadingMore}
            disabled={loading}
            onClick={() => void load(nextOffset, true)}
          >
            {t("projects.tasks.loadMore", "加载更多")}
          </Button>
        </div>
      )}

      {readerTask != null && (
        <Modal
          open
          title={t("projects.tasks.readerModalTitle", "任务摘要（只读）")}
          onCancel={closeReader}
          destroyOnHidden
          footer={[
            <Button key="close" onClick={closeReader}>
              {t("common.close", "关闭")}
            </Button>,
          ]}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 12,
            }}
          >
            <Tag color="blue" style={{ marginInlineEnd: 0 }}>
              {t("projects.tasks.readerTag", "分享给我的 · 只读")}
            </Tag>
            <span style={{ fontWeight: 600, wordBreak: "break-word" }}>
              {readerTitle}
            </span>
          </div>
          {readerLoading && readerDetail == null ? (
            <div
              style={{ display: "flex", justifyContent: "center", padding: 24 }}
            >
              <Spin />
            </div>
          ) : readerError != null ? (
            <Alert
              type="error"
              showIcon
              message={apiErrorMessage(
                readerError,
                t("projects.tasks.readerLoadFailed", "加载任务摘要失败"),
                t,
              )}
              action={
                <Button
                  size="small"
                  onClick={() => readerTarget && openReader(readerTarget)}
                >
                  {t("common.retry", "重试")}
                </Button>
              }
            />
          ) : (
            <>
              {readerDetail?.can_read_text ? (
                readerTextBody
              ) : (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 12 }}
                  message={t(
                    "projects.tasks.readerNoBodyTitle",
                    "对话内容尚未共享",
                  )}
                  description={t(
                    "projects.tasks.readerNoBodyHint",
                    "这里只显示任务摘要；正文、附件、工作区文件与运行流仍只对任务所有者可见。",
                  )}
                />
              )}
              <div style={rowStyle}>
                <span style={secondaryStyle}>
                  {t("projects.tasks.readerOwnerLabel", "任务所有者")}
                </span>
                <span style={{ fontSize: 13 }}>{readerOwnerName}</span>
              </div>
              <div style={rowStyle}>
                <span style={secondaryStyle}>
                  {t("projects.tasks.sourceLabel", "来源")}
                </span>
                <span style={{ fontSize: 13 }}>
                  {readerTask?.source === "manual"
                    ? t("projects.tasks.sourceManual", "手动关联")
                    : readerTask?.source}
                </span>
              </div>
              <div style={rowStyle}>
                <span style={secondaryStyle}>
                  {t("projects.tasks.activeAtLabel", "最近活动")}
                </span>
                <span style={{ fontSize: 13 }}>
                  {readerTask
                    ? formatServerDateTime(
                        readerTask.last_active > 0
                          ? readerTask.last_active
                          : readerTask.created_at,
                        timezone,
                      )
                    : ""}
                </span>
              </div>
              <div style={rowStyle}>
                <span style={secondaryStyle}>
                  {t("projects.tasks.createdAtLabel", "创建时间")}
                </span>
                <span style={{ fontSize: 13 }}>
                  {readerTask
                    ? formatServerDateTime(readerTask.created_at, timezone)
                    : ""}
                </span>
              </div>
            </>
          )}
        </Modal>
      )}

      {shareTarget != null && (
        <Modal
          open
          title={t("projects.tasks.shareTitle", "分享任务卡片")}
          onCancel={closeShare}
          destroyOnHidden
          footer={[
            <Button key="close" onClick={closeShare}>
              {t("common.close", "关闭")}
            </Button>,
          ]}
        >
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 8 }}
            message={t(
              "projects.tasks.shareScopeNote",
              "卡片分享仅开放摘要；对话文本需对每位接收者另行确认。附件仍私密。",
            )}
          />
          <div style={{ ...secondaryStyle, marginBottom: 12 }}>
            {t(
              "projects.tasks.shareTextHint",
              "文本权限需要单独授予：卡片分享本身不会开放正文；文本可能包含用户粘贴的敏感内容。",
            )}
          </div>
          {shareActionError != null && (
            <Alert
              type="error"
              showIcon
              closable
              style={{ marginBottom: 12 }}
              message={shareActionError}
              onClose={() => setShareActionError(null)}
            />
          )}
          {shareBody}
        </Modal>
      )}

      {pickerOpen && (
        <Modal
          open
          title={t("projects.tasks.attachTitle", "关联我的任务")}
          onCancel={closePicker}
          destroyOnHidden
          footer={[
            <Button key="close" onClick={closePicker}>
              {t("common.close", "关闭")}
            </Button>,
          ]}
        >
          <div style={{ ...secondaryStyle, marginBottom: 12 }}>
            {t(
              "projects.tasks.attachHint",
              "仅显示每个专家最近 {{limit}} 条 Dashboard 私聊，不是完整历史；可刷新或切换专家，后端会再次校验任务归属。",
              { limit: TASK_CANDIDATE_LIMIT },
            )}
          </div>
          <div
            style={{
              display: "flex",
              gap: 8,
              marginBottom: 12,
              flexWrap: "wrap",
            }}
          >
            <Select<string>
              style={{ minWidth: 180, flex: 1 }}
              value={pickerAgentId ?? undefined}
              options={runningAgents.map((agent) => ({
                value: agent.agent_id,
                label: agent.name,
              }))}
              placeholder={t("projects.tasks.selectAgent", "选择专家")}
              aria-label={t("projects.tasks.selectAgent", "选择专家")}
              disabled={runningAgents.length === 0 || candidatesLoading}
              onChange={(value) => setPickedAgentId(value)}
            />
            <Tooltip title={t("projects.tasks.attachRefresh", "刷新最近任务")}>
              <Button
                icon={<RefreshCw size={14} />}
                disabled={pickerAgentId == null || candidatesLoading}
                aria-label={t("projects.tasks.attachRefresh", "刷新最近任务")}
                onClick={reloadCandidates}
              />
            </Tooltip>
          </div>
          {runningAgents.length === 0 && (
            <Alert
              type="info"
              showIcon
              message={t(
                "projects.tasks.noRunningAgents",
                "没有已启用的专家，无法读取最近任务。",
              )}
            />
          )}
          {candidatesBody}
          <div style={{ ...secondaryStyle, marginTop: 12 }}>
            {t(
              "projects.tasks.attachOwnershipHint",
              "关联只登记归属，不会向项目成员共享对话正文、附件或运行流。",
            )}
          </div>
        </Modal>
      )}
    </div>
  );
}
