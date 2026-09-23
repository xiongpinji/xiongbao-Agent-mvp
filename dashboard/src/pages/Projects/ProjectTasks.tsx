/**
 * ProjectTasks — PS-05 first slice (项目任务 → 个人任务).
 *
 * Only the honest first slice of the 021 ACL contract is active:
 * - the caller's own linked Dashboard conversations are loaded and searched
 *   server-side (`GET /projects/{id}/tasks`) with offset paging;
 * - attaching uses a bounded list of the caller's recent Dashboard DM threads
 *   per enabled expert (`octopThreadsApi.list`, not a full history search) —
 *   the backend still enforces ownership and the exact Dashboard DM session;
 * - detaching only removes the project link; the conversation stays intact;
 * - 全部/协同任务, sharing, local/cloud execution and transfer stay visibly
 *   disabled until their backend ACL exists (PS-05B / PS-05C / PS-08).
 *
 * The actor never comes from this component: no user id, role or source is
 * sent, and no `session_key`, artifact or workspace path is rendered.
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
import {
  PROJECT_TASKS_PAGE_SIZE,
  projectTasksApi,
  type ProjectTask,
} from "../../api/modules/projectTasks";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text } = Typography;

/**
 * Bounded candidate scan. `octopThreadsApi.list` returns the caller's own
 * recent threads for one expert — never a complete, searchable history.
 */
export const TASK_CANDIDATE_LIMIT = 50;

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
}

export default function ProjectTasks({ projectId }: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const { agents, activeAgentId } = useAgent();

  const [tasks, setTasks] = useState<ProjectTask[]>([]);
  const [tasksProjectId, setTasksProjectId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [listError, setListError] = useState<unknown>(null);
  /** Search is keyed by project so a switch cannot reuse the old keyword. */
  const [search, setSearch] = useState<{ projectId: string; q: string }>(
    () => ({ projectId, q: "" }),
  );
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
  /** Monotonic guards so late responses never overwrite fresher results. */
  const fetchSeq = useRef(0);
  const candidateSeq = useRef(0);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;

  const query = search.projectId === projectId ? search.q : "";
  const loaded = tasksProjectId === projectId;

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

  // A project switch must never show the previous project's private rows or
  // carry over its alerts / selected candidate.
  useEffect(() => {
    setConflict(false);
    setActionError(null);
    closePicker();
  }, [projectId, closePicker]);

  const load = useCallback(
    async (offset: number, append: boolean) => {
      const seq = ++fetchSeq.current;
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      try {
        const data = await projectTasksApi.list(projectId, {
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
        setTasksProjectId(projectId);
      } catch (err: unknown) {
        if (seq !== fetchSeq.current) return;
        // A failed first page cannot reuse rows from the prior project or
        // search. Keep already confirmed rows only for a later-page failure.
        if (!append || isNotFoundApiError(err)) {
          setTasks([]);
          setHasMore(false);
          setNextOffset(0);
        }
        setListError(err);
        setTasksProjectId(projectId);
      } finally {
        if (seq === fetchSeq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [projectId, query],
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

  const renderTask = (task: ProjectTask) => {
    const title =
      task.title?.trim() || t("projects.tasks.untitled", "未命名任务");
    const agentName = agentNames.get(task.agent_id) ?? task.agent_id;
    const busy = busyThreadIds.includes(task.thread_id);
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
            style={{ fontWeight: 600, fontSize: 14, wordBreak: "break-word" }}
          >
            {title}
          </Link>
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
    body = (
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
                "关联一条你已有的 Dashboard 对话，把它归属到项目；对话内容仍只对你可见。",
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
        <Segmented<string>
          value="personal"
          aria-label={t("projects.tasks.scopeFilter", "任务范围")}
          options={[
            {
              label: (
                <span
                  title={t(
                    "projects.tasks.scopeAllReason",
                    "全部任务需要明确的任务共享权限，暂未开放。",
                  )}
                >
                  {t("projects.tasks.scopeAll", "全部任务")}
                </span>
              ),
              value: "all",
              disabled: true,
            },
            {
              label: t("projects.tasks.scopePersonal", "个人任务"),
              value: "personal",
            },
            {
              label: (
                <span
                  title={t(
                    "projects.tasks.scopeCollabReason",
                    "协同任务需要成员协作与移交能力，暂未开放。",
                  )}
                >
                  {t("projects.tasks.scopeCollab", "协同任务")}
                </span>
              ),
              value: "collab",
              disabled: true,
            },
          ]}
        />
        <Input.Search
          allowClear
          style={{ maxWidth: 220 }}
          placeholder={t(
            "projects.tasks.searchPlaceholder",
            "搜索我的任务标题",
          )}
          aria-label={t("projects.tasks.searchPlaceholder", "搜索我的任务标题")}
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
        <Button type="primary" icon={<Link2 size={14} />} onClick={openPicker}>
          {t("projects.tasks.attach", "关联我的任务")}
        </Button>
      </div>

      <div style={{ ...secondaryStyle, marginBottom: 8 }}>
        {t(
          "projects.tasks.privacyHint",
          "项目任务默认私密：你只会看到自己关联的任务。",
        )}
      </div>

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
          t("projects.tasks.share", "共享"),
          t(
            "projects.tasks.shareReason",
            "共享任务前需让所有读取入口遵守任务权限，暂未开放。",
          ),
          <Share2 size={14} />,
          "task-capability-share",
        )}
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
            "projects.tasks.unavailableHint",
            "共享、本地/云端与移交需要后续后端权限，当前不可用。",
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
