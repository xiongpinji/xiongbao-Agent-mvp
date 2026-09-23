/**
 * ProjectPlan — PS-04 plan tab (项目计划).
 *
 * One server-loaded todo collection drives both views:
 * - table: title / status / assignee / updated time + row actions
 * - board: three status columns with explicit status actions (no drag, no
 *   second card store)
 *
 * Search, status and assignee filters plus paging are server-side
 * (`GET /projects/{id}/todos`); `offset` resets whenever a filter changes.
 * Row controls only guide the user — the server remains authoritative, so
 * 403/404/409 are surfaced as recoverable states instead of hidden.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Table,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { Plus, RefreshCw } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import {
  PROJECT_TODOS_BULK_MAX,
  PROJECT_TODOS_PAGE_SIZE,
  normalizeTodoBulkResponse,
  projectTodosApi,
  type ProjectTodo,
  type ProjectTodoCreateBody,
  type ProjectTodoStatus,
  type ProjectTodoUpdateBody,
} from "../../api/modules/projectTodos";
import type { ProjectMember, ProjectRole } from "../../api/modules/projects";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text } = Typography;

const TODO_STATUSES: ProjectTodoStatus[] = ["todo", "in_progress", "done"];

const STATUS_FALLBACKS: Record<ProjectTodoStatus, string> = {
  todo: "待处理",
  in_progress: "进行中",
  done: "已完成",
};

const MOVE_FALLBACKS: Record<ProjectTodoStatus, string> = {
  todo: "移到待处理",
  in_progress: "移到进行中",
  done: "移到已完成",
};

const STATUS_TAG_COLORS: Record<ProjectTodoStatus, string> = {
  todo: "default",
  in_progress: "processing",
  done: "success",
};

type PlanView = "table" | "board";
type StatusFilter = ProjectTodoStatus | "all";
type AssigneeFilter = number | "all";

interface Props {
  projectId: string;
  role: ProjectRole;
  members: ProjectMember[] | null;
}

/** Extract the HTTP status from a failed `request()` error message. */
function httpStatus(error: unknown): number | null {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  const match = raw.match(/\b(4\d{2}|5\d{2})\b/);
  return match ? Number(match[1]) : null;
}

function isConflictApiError(error: unknown): boolean {
  const parsed = parseApiError(error);
  if (parsed?.details?.reason === "version_conflict") return true;
  const code = parsed?.code;
  if (code === "VERSION_CONFLICT" || code === "CONFLICT") return true;
  return httpStatus(error) === 409;
}

function mergeUniqueTodos(
  previous: ProjectTodo[],
  incoming: ProjectTodo[],
): ProjectTodo[] {
  const seen = new Set(previous.map((todo) => todo.todo_id));
  const merged = [...previous];
  for (const todo of incoming) {
    if (seen.has(todo.todo_id)) continue;
    seen.add(todo.todo_id);
    merged.push(todo);
  }
  return merged;
}

interface EditorValues {
  title: string;
  description: string;
  assignee: number | null;
}

interface TodoEditorModalProps {
  open: boolean;
  mode: "create" | "edit";
  todo: ProjectTodo | null;
  members: ProjectMember[];
  currentUserId: number | null;
  isManager: boolean;
  submitting: boolean;
  error: string | null;
  onClose: () => void;
  onSubmit: (values: EditorValues) => void;
}

function TodoEditorModal({
  open,
  mode,
  todo,
  members,
  currentUserId,
  isManager,
  submitting,
  error,
  onClose,
  onSubmit,
}: TodoEditorModalProps) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(todo?.title ?? "");
  const [description, setDescription] = useState(todo?.description ?? "");
  const [assignee, setAssignee] = useState<number | null>(
    todo?.assignee_user_id ?? null,
  );
  const [formError, setFormError] = useState<string | null>(null);

  const canPickAssignee = isManager || mode === "create";
  const assigneeOptions = (
    isManager ? members : members.filter((m) => m.user_id === currentUserId)
  ).map((member) => ({ value: member.user_id, label: member.username }));

  const trimmedTitle = title.trim();
  const trimmedDescription = description.trim();
  const changed =
    mode === "create"
      ? trimmedTitle.length > 0
      : todo != null &&
        (trimmedTitle !== todo.title ||
          trimmedDescription !== todo.description ||
          (isManager && assignee !== todo.assignee_user_id));

  const submit = () => {
    if (trimmedTitle.length === 0) {
      setFormError(t("projects.plan.titleRequired", "请输入待办标题"));
      return;
    }
    if (trimmedTitle.length > 200) {
      setFormError(t("projects.plan.titleTooLong", "标题最多 200 个字符"));
      return;
    }
    if (trimmedDescription.length > 4000) {
      setFormError(
        t("projects.plan.descriptionTooLong", "描述最多 4000 个字符"),
      );
      return;
    }
    setFormError(null);
    onSubmit({
      title: trimmedTitle,
      description: trimmedDescription,
      assignee,
    });
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: 13,
    fontWeight: 500,
    margin: "12px 0 6px",
  };
  const helpStyle: React.CSSProperties = {
    fontSize: 12,
    marginTop: 4,
    color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
  };

  return (
    <Modal
      open={open}
      title={
        mode === "create"
          ? t("projects.plan.createTitle", "新建待办")
          : t("projects.plan.editTitle", "编辑待办")
      }
      onCancel={submitting ? undefined : onClose}
      maskClosable={false}
      destroyOnHidden
      footer={[
        <Button key="cancel" onClick={onClose} disabled={submitting}>
          {t("common.cancel", "取消")}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          disabled={submitting || (mode === "edit" && !changed)}
          onClick={submit}
        >
          {mode === "create"
            ? t("projects.plan.create", "创建")
            : t("common.save", "保存")}
        </Button>,
      ]}
    >
      <label style={labelStyle} htmlFor="project-todo-title">
        {t("projects.plan.titleLabel", "标题")}
      </label>
      <Input
        id="project-todo-title"
        value={title}
        maxLength={200}
        disabled={submitting}
        placeholder={t(
          "projects.plan.titlePlaceholder",
          "输入待办标题（1–200 字符）",
        )}
        onChange={(event) => setTitle(event.target.value)}
      />
      <label style={labelStyle} htmlFor="project-todo-description">
        {t("projects.plan.descriptionLabel", "描述")}
      </label>
      <Input.TextArea
        id="project-todo-description"
        rows={3}
        value={description}
        maxLength={4000}
        disabled={submitting}
        placeholder={t(
          "projects.plan.descriptionPlaceholder",
          "可选：补充说明（不超过 4000 字符）",
        )}
        onChange={(event) => setDescription(event.target.value)}
      />
      {canPickAssignee && (
        <>
          <label style={labelStyle}>
            {t("projects.plan.assigneeLabel", "处理人")}
          </label>
          <Select<number>
            style={{ width: "100%" }}
            value={assignee ?? undefined}
            options={assigneeOptions}
            allowClear
            disabled={submitting}
            aria-label={t("projects.plan.assigneeLabel", "处理人")}
            placeholder={t("projects.plan.unassigned", "未指派")}
            onChange={(value) => setAssignee(value ?? null)}
          />
          {!isManager && (
            <div style={helpStyle}>
              {t(
                "projects.plan.assigneeHint",
                "普通成员只能指派给自己或留空。",
              )}
            </div>
          )}
        </>
      )}
      {formError != null && (
        <Alert
          type="warning"
          showIcon
          style={{ marginTop: 12 }}
          message={formError}
        />
      )}
      {error != null && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 12 }}
          message={error}
        />
      )}
    </Modal>
  );
}

export default function ProjectPlan({ projectId, role, members }: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const currentUser = useCurrentUser();
  const currentUserId = currentUser?.id ?? null;
  const isManager = role === "owner" || role === "admin";

  const [todos, setTodos] = useState<ProjectTodo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [listError, setListError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [assigneeFilter, setAssigneeFilter] = useState<AssigneeFilter>("all");
  const [view, setView] = useState<PlanView>("table");
  const [reloadKey, setReloadKey] = useState(0);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [bulkStatus, setBulkStatus] = useState<ProjectTodoStatus>("todo");
  const [bulkBusy, setBulkBusy] = useState(false);
  const [busyIds, setBusyIds] = useState<string[]>([]);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ProjectTodo | null>(null);
  const [editorSubmitting, setEditorSubmitting] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  /** Monotonic guard so late responses never overwrite fresher results. */
  const fetchSeq = useRef(0);

  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

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

  const load = useCallback(
    async (offset: number, append: boolean) => {
      const seq = ++fetchSeq.current;
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      try {
        const data = await projectTodosApi.list(projectId, {
          q: query,
          status: statusFilter === "all" ? undefined : statusFilter,
          assigneeUserId: assigneeFilter === "all" ? undefined : assigneeFilter,
          limit: PROJECT_TODOS_PAGE_SIZE,
          offset,
        });
        if (seq !== fetchSeq.current) return;
        setTodos((previous) =>
          append ? mergeUniqueTodos(previous, data.items) : data.items,
        );
        setHasMore(data.has_more);
        setNextOffset(offset + data.items.length);
        setListError(null);
        if (!append) setSelectedIds([]);
      } catch (err: unknown) {
        if (seq !== fetchSeq.current) return;
        if (isNotFoundApiError(err)) {
          setTodos([]);
          setHasMore(false);
          setSelectedIds([]);
        }
        setListError(err);
      } finally {
        if (seq === fetchSeq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [projectId, query, statusFilter, assigneeFilter],
  );

  useEffect(() => {
    void load(0, false);
  }, [load, reloadKey, members]);

  const todoErrorMessage = (err: unknown, fallback: string): string => {
    const reason = parseApiError(err)?.details?.reason;
    if (reason === "invalid_assignee") {
      return t("projects.plan.invalidAssignee", "处理人必须是当前项目成员。");
    }
    if (reason === "no_change") {
      return t("projects.plan.noChange", "待办内容没有变化。");
    }
    return apiErrorMessage(err, fallback, t);
  };

  const applyServerTodo = useCallback(
    (updated: ProjectTodo | null | undefined) => {
      if (
        updated != null &&
        typeof updated === "object" &&
        typeof updated.todo_id === "string"
      ) {
        const record: ProjectTodo = updated;
        setTodos((previous) =>
          previous.map((item) =>
            item.todo_id === record.todo_id ? record : item,
          ),
        );
        reload();
        return;
      }
      reload();
    },
    [reload],
  );

  const routeMutationError = (
    err: unknown,
    setInline: (value: string | null) => void,
    fallback: string,
  ) => {
    if (isNotFoundApiError(err)) {
      setTodos([]);
      setHasMore(false);
      setSelectedIds([]);
      setListError(err);
      setEditorOpen(false);
      setEditing(null);
      return;
    }
    if (isConflictApiError(err)) {
      setConflict(true);
      reload();
      setEditorOpen(false);
      setEditing(null);
      return;
    }
    setInline(todoErrorMessage(err, fallback));
  };

  const canEditTodo = (todo: ProjectTodo): boolean =>
    isManager ||
    (currentUserId != null &&
      (todo.creator_user_id === currentUserId ||
        todo.assignee_user_id === currentUserId));

  const canDeleteTodo = (todo: ProjectTodo): boolean =>
    isManager ||
    (currentUserId != null && todo.creator_user_id === currentUserId);

  const assigneeName = (todo: ProjectTodo): string => {
    if (todo.assignee_user_id == null) {
      return t("projects.plan.unassigned", "未指派");
    }
    return (
      memberNames.get(todo.assignee_user_id) ??
      t("projects.plan.unknownAssignee", "未知成员")
    );
  };

  const labelForStatus = (status: ProjectTodoStatus): string =>
    t(`projects.plan.status.${status}`, STATUS_FALLBACKS[status]);

  const changeStatus = async (todo: ProjectTodo, status: ProjectTodoStatus) => {
    if (status === todo.status) return;
    setBusyIds((previous) => [...previous, todo.todo_id]);
    setActionError(null);
    try {
      const updated = await projectTodosApi.update(projectId, todo.todo_id, {
        expected_version: todo.version,
        status,
      });
      applyServerTodo(updated);
    } catch (err: unknown) {
      routeMutationError(
        err,
        setActionError,
        t("projects.plan.actionFailed", "待办操作失败"),
      );
    } finally {
      setBusyIds((previous) => previous.filter((id) => id !== todo.todo_id));
    }
  };

  const deleteTodo = async (todo: ProjectTodo) => {
    setBusyIds((previous) => [...previous, todo.todo_id]);
    setActionError(null);
    try {
      await projectTodosApi.remove(projectId, todo.todo_id, todo.version);
      setTodos((previous) =>
        previous.filter((item) => item.todo_id !== todo.todo_id),
      );
      setSelectedIds((previous) =>
        previous.filter((id) => id !== todo.todo_id),
      );
      reload();
      void message.success(t("projects.plan.deleted", "待办已删除"));
    } catch (err: unknown) {
      routeMutationError(
        err,
        setActionError,
        t("projects.plan.actionFailed", "待办操作失败"),
      );
    } finally {
      setBusyIds((previous) => previous.filter((id) => id !== todo.todo_id));
    }
  };

  const applyBulkStatus = async () => {
    const selectedTodos = selectedIds
      .map((id) => todos.find((item) => item.todo_id === id))
      .filter((item): item is ProjectTodo => item != null);
    const uniqueTodos = Array.from(
      new Map(
        selectedTodos.map((item): [string, ProjectTodo] => [
          item.todo_id,
          item,
        ]),
      ).values(),
    );
    if (uniqueTodos.length === 0 || uniqueTodos.length > PROJECT_TODOS_BULK_MAX)
      return;
    setBulkBusy(true);
    setActionError(null);
    try {
      const response = await projectTodosApi.bulk(projectId, {
        items: uniqueTodos.map((item) => ({
          todo_id: item.todo_id,
          expected_version: item.version,
        })),
        status: bulkStatus,
      });
      const updated = normalizeTodoBulkResponse(response);
      if (updated.length > 0) {
        setTodos((previous) =>
          previous.map(
            (item) =>
              updated.find((record) => record.todo_id === item.todo_id) ?? item,
          ),
        );
      }
      setSelectedIds([]);
      reload();
      void message.success(
        t("projects.plan.bulkSuccess", "已批量更新 {{count}} 条待办", {
          count: uniqueTodos.length,
        }),
      );
    } catch (err: unknown) {
      routeMutationError(
        err,
        setActionError,
        t("projects.plan.actionFailed", "待办操作失败"),
      );
    } finally {
      setBulkBusy(false);
    }
  };

  const openCreate = () => {
    setEditing(null);
    setEditorError(null);
    setEditorOpen(true);
  };

  const openEdit = (todo: ProjectTodo) => {
    setEditing(todo);
    setEditorError(null);
    setEditorOpen(true);
  };

  const submitEditor = async (values: EditorValues) => {
    setEditorSubmitting(true);
    setEditorError(null);
    try {
      if (editing) {
        const body: ProjectTodoUpdateBody = {
          expected_version: editing.version,
        };
        if (values.title !== editing.title) body.title = values.title;
        if (values.description !== editing.description) {
          body.description = values.description;
        }
        if (isManager && values.assignee !== editing.assignee_user_id) {
          body.assignee_user_id = values.assignee;
        }
        const updated = await projectTodosApi.update(
          projectId,
          editing.todo_id,
          body,
        );
        applyServerTodo(updated);
        setEditorOpen(false);
        setEditing(null);
        void message.success(t("projects.plan.saved", "待办已更新"));
      } else {
        const body: ProjectTodoCreateBody = {
          title: values.title,
          description: values.description,
        };
        if (values.assignee != null) body.assignee_user_id = values.assignee;
        await projectTodosApi.create(projectId, body);
        setEditorOpen(false);
        void message.success(t("projects.plan.created", "待办已创建"));
        reload();
      }
    } catch (err: unknown) {
      routeMutationError(
        err,
        setEditorError,
        editing
          ? t("projects.plan.saveFailed", "保存待办失败")
          : t("projects.plan.createFailed", "创建待办失败"),
      );
    } finally {
      setEditorSubmitting(false);
    }
  };

  const statusOptions = TODO_STATUSES.map((status) => ({
    value: status,
    label: labelForStatus(status),
  }));

  const statusFilterOptions: { value: StatusFilter; label: string }[] = [
    { value: "all", label: t("projects.plan.allStatuses", "全部状态") },
    ...statusOptions,
  ];

  const assigneeFilterOptions: { value: AssigneeFilter; label: string }[] = [
    { value: "all", label: t("projects.plan.allAssignees", "全部处理人") },
    ...(members ?? []).map((member) => ({
      value: member.user_id,
      label: member.username,
    })),
  ];

  const secondaryStyle: React.CSSProperties = {
    fontSize: 12,
    color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
  };

  const columns: ColumnsType<ProjectTodo> = [
    {
      title: t("projects.plan.titleLabel", "标题"),
      dataIndex: "title",
      key: "title",
      render: (_value: unknown, todo: ProjectTodo) => (
        <div style={{ minWidth: 160 }}>
          <Text strong>{todo.title}</Text>
          {todo.description.trim() ? (
            <div style={{ ...secondaryStyle, wordBreak: "break-word" }}>
              {todo.description}
            </div>
          ) : null}
        </div>
      ),
    },
    {
      title: t("projects.plan.statusLabel", "状态"),
      dataIndex: "status",
      key: "status",
      width: 140,
      render: (_value: unknown, todo: ProjectTodo) => {
        if (!canEditTodo(todo)) {
          return (
            <Tag
              color={STATUS_TAG_COLORS[todo.status]}
              style={{ marginInlineEnd: 0 }}
            >
              {labelForStatus(todo.status)}
            </Tag>
          );
        }
        return (
          <Select<ProjectTodoStatus>
            size="small"
            style={{ width: 116 }}
            value={todo.status}
            options={statusOptions}
            disabled={busyIds.includes(todo.todo_id)}
            aria-label={t("projects.plan.changeStatus", "更改状态：{{title}}", {
              title: todo.title,
            })}
            onChange={(next) => void changeStatus(todo, next)}
          />
        );
      },
    },
    {
      title: t("projects.plan.assigneeLabel", "处理人"),
      dataIndex: "assignee_user_id",
      key: "assignee",
      width: 140,
      render: (_value: unknown, todo: ProjectTodo) => (
        <span style={secondaryStyle}>{assigneeName(todo)}</span>
      ),
    },
    {
      title: t("projects.plan.updatedAtLabel", "更新时间"),
      dataIndex: "updated_at",
      key: "updated_at",
      width: 180,
      render: (_value: unknown, todo: ProjectTodo) => (
        <span style={secondaryStyle}>
          {formatServerDateTime(todo.updated_at, timezone)}
        </span>
      ),
    },
    {
      title: t("common.actions", "操作"),
      key: "actions",
      width: 150,
      render: (_value: unknown, todo: ProjectTodo) => {
        const busy = busyIds.includes(todo.todo_id);
        const editable = canEditTodo(todo);
        const deletable = canDeleteTodo(todo);
        if (!editable && !deletable) return null;
        return (
          <div style={{ display: "flex", gap: 4 }}>
            {editable && (
              <Button
                size="small"
                type="text"
                disabled={busy}
                onClick={() => openEdit(todo)}
              >
                {t("common.edit", "编辑")}
              </Button>
            )}
            {deletable && (
              <Popconfirm
                title={t("projects.plan.deleteConfirm", "删除这条待办？")}
                okText={t("projects.plan.deleteOk", "确认删除")}
                cancelText={t("common.cancel", "取消")}
                okButtonProps={{ danger: true, disabled: busy }}
                onConfirm={() => void deleteTodo(todo)}
              >
                <Button size="small" type="text" danger disabled={busy}>
                  {t("common.delete", "删除")}
                </Button>
              </Popconfirm>
            )}
          </div>
        );
      },
    },
  ];

  const renderBoardCard = (todo: ProjectTodo) => {
    const busy = busyIds.includes(todo.todo_id);
    const editable = canEditTodo(todo);
    return (
      <div
        key={todo.todo_id}
        data-testid={`todo-card-${todo.todo_id}`}
        style={{
          background: "var(--fn-bg-elevated, #fff)",
          border: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
          borderRadius: 8,
          padding: 10,
          marginBottom: 8,
        }}
      >
        <div style={{ fontWeight: 600, fontSize: 13, wordBreak: "break-word" }}>
          {todo.title}
        </div>
        {todo.description.trim() ? (
          <div style={{ ...secondaryStyle, marginTop: 4 }}>
            {todo.description}
          </div>
        ) : null}
        <div style={{ ...secondaryStyle, marginTop: 6 }}>
          {assigneeName(todo)}
        </div>
        <div
          style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 8 }}
        >
          {editable &&
            TODO_STATUSES.filter((status) => status !== todo.status).map(
              (status) => (
                <Button
                  key={status}
                  size="small"
                  disabled={busy}
                  onClick={() => void changeStatus(todo, status)}
                >
                  {t(`projects.plan.moveTo.${status}`, MOVE_FALLBACKS[status])}
                </Button>
              ),
            )}
          {editable && (
            <Button
              size="small"
              type="text"
              disabled={busy}
              onClick={() => openEdit(todo)}
            >
              {t("common.edit", "编辑")}
            </Button>
          )}
        </div>
      </div>
    );
  };

  const anyFilterActive =
    query.trim().length > 0 ||
    statusFilter !== "all" ||
    assigneeFilter !== "all";

  const notFoundError = listError != null && isNotFoundApiError(listError);

  let body: React.ReactNode;
  if (notFoundError) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.plan.notFound", "项目不存在或你无权访问")}
        description={t(
          "projects.plan.notFoundHint",
          "项目待办数据已清除，重新加载成功前不会显示旧内容。",
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (loading && todos.length === 0) {
    body = (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  } else if (todos.length === 0 && listError == null) {
    body = (
      <EmptyState
        variant="empty"
        title={
          anyFilterActive
            ? t("projects.plan.emptySearchTitle", "没有匹配的待办")
            : t("projects.plan.emptyTitle", "还没有待办")
        }
        description={
          anyFilterActive
            ? t("projects.plan.emptySearchHint", "换个关键词或调整筛选条件。")
            : t(
                "projects.plan.emptyHint",
                "新建第一条待办；表格与看板会显示同一份数据。",
              )
        }
        actionLabel={
          anyFilterActive ? undefined : t("projects.plan.newTodo", "新建待办")
        }
        onAction={anyFilterActive ? undefined : openCreate}
      />
    );
  } else if (todos.length === 0) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.plan.loadFailed", "加载待办失败")}
        description={apiErrorMessage(
          listError,
          t("projects.plan.loadFailed", "加载待办失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (view === "table") {
    body = (
      <Table<ProjectTodo>
        rowKey="todo_id"
        size="small"
        loading={loading}
        columns={columns}
        dataSource={todos}
        pagination={false}
        scroll={{ x: 720 }}
        rowSelection={
          isManager
            ? {
                selectedRowKeys: selectedIds,
                onChange: (keys) => setSelectedIds(keys.map(String)),
              }
            : undefined
        }
      />
    );
  } else {
    body = (
      <div
        style={{
          display: "flex",
          gap: 12,
          alignItems: "flex-start",
          flexWrap: "wrap",
        }}
      >
        {TODO_STATUSES.map((status) => {
          const columnTodos = todos.filter((todo) => todo.status === status);
          return (
            <section
              key={status}
              aria-label={labelForStatus(status)}
              style={{
                flex: "1 1 240px",
                minWidth: 220,
                background: "var(--fn-fill-quaternary, rgba(0,0,0,0.03))",
                borderRadius: 8,
                padding: 10,
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  marginBottom: 8,
                }}
              >
                <Tag
                  color={STATUS_TAG_COLORS[status]}
                  style={{ marginInlineEnd: 0 }}
                >
                  {labelForStatus(status)}
                </Tag>
                <span style={secondaryStyle}>{columnTodos.length}</span>
              </div>
              {columnTodos.length === 0 ? (
                <div style={secondaryStyle}>
                  {t("projects.plan.boardEmpty", "此列暂无待办")}
                </div>
              ) : (
                columnTodos.map(renderBoardCard)
              )}
            </section>
          );
        })}
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
          marginBottom: 12,
        }}
      >
        <Input.Search
          allowClear
          style={{ maxWidth: 240 }}
          placeholder={t("projects.plan.searchPlaceholder", "搜索待办标题")}
          aria-label={t("projects.plan.searchPlaceholder", "搜索待办标题")}
          onSearch={(value) => setQuery(value)}
        />
        <Select<StatusFilter>
          style={{ width: 140 }}
          value={statusFilter}
          options={statusFilterOptions}
          aria-label={t("projects.plan.statusFilter", "状态筛选")}
          onChange={setStatusFilter}
        />
        <Select<AssigneeFilter>
          style={{ width: 160 }}
          value={assigneeFilter}
          options={assigneeFilterOptions}
          aria-label={t("projects.plan.assigneeFilter", "处理人筛选")}
          onChange={setAssigneeFilter}
        />
        <Tooltip title={t("projects.plan.refresh", "刷新待办")}>
          <Button
            icon={<RefreshCw size={14} />}
            disabled={loading}
            aria-label={t("projects.plan.refresh", "刷新待办")}
            onClick={reload}
          />
        </Tooltip>
        <Button type="primary" icon={<Plus size={14} />} onClick={openCreate}>
          {t("projects.plan.newTodo", "新建待办")}
        </Button>
        <div style={{ marginLeft: "auto" }}>
          <Segmented
            value={view}
            onChange={(value) => setView(value as PlanView)}
            options={[
              { label: t("projects.plan.viewTable", "表格"), value: "table" },
              { label: t("projects.plan.viewBoard", "看板"), value: "board" },
            ]}
          />
        </div>
      </div>

      {conflict && (
        <Alert
          type="warning"
          showIcon
          closable
          style={{ marginBottom: 12 }}
          message={t(
            "projects.plan.conflict",
            "待办已被他人更新，已为你刷新最新内容，请重试。",
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
      {listError != null && !notFoundError && todos.length > 0 && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={apiErrorMessage(
            listError,
            t("projects.plan.loadFailed", "加载待办失败"),
            t,
          )}
          action={
            <Button size="small" onClick={reload}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      )}

      {isManager && (
        <div
          data-testid="plan-bulk-toolbar"
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            alignItems: "center",
            marginBottom: 12,
          }}
        >
          <span style={secondaryStyle}>
            {t("projects.plan.selectedCount", "已选 {{count}} 项", {
              count: selectedIds.length,
            })}
          </span>
          <Select<ProjectTodoStatus>
            size="small"
            style={{ width: 130 }}
            value={bulkStatus}
            options={statusOptions}
            aria-label={t("projects.plan.bulkStatus", "批量状态")}
            onChange={setBulkStatus}
          />
          <Button
            size="small"
            loading={bulkBusy}
            disabled={
              selectedIds.length === 0 ||
              selectedIds.length > PROJECT_TODOS_BULK_MAX
            }
            onClick={() => void applyBulkStatus()}
          >
            {t("projects.plan.bulkApply", "批量更新")}
          </Button>
          {selectedIds.length > PROJECT_TODOS_BULK_MAX && (
            <Text type="danger" style={{ fontSize: 12 }}>
              {t("projects.plan.bulkLimit", "单次批量最多 50 条")}
            </Text>
          )}
        </div>
      )}

      {body}
      {hasMore && !notFoundError && todos.length > 0 && (
        <div style={{ textAlign: "center", marginTop: 12 }}>
          <Button
            loading={loadingMore}
            disabled={loading}
            onClick={() => void load(nextOffset, true)}
          >
            {t("projects.plan.loadMore", "加载更多")}
          </Button>
        </div>
      )}

      {editorOpen && (
        <TodoEditorModal
          open
          mode={editing ? "edit" : "create"}
          todo={editing}
          members={members ?? []}
          currentUserId={currentUserId}
          isManager={isManager}
          submitting={editorSubmitting}
          error={editorError}
          onClose={() => {
            if (editorSubmitting) return;
            setEditorOpen(false);
            setEditorError(null);
            setEditing(null);
          }}
          onSubmit={(values) => void submitEditor(values)}
        />
      )}
    </div>
  );
}
