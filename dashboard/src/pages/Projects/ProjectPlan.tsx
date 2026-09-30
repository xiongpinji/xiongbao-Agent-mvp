/** Project plan: shared views own reads; this bridge owns existing todo writes and details. */
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Modal, Select } from "antd";
import { EmptyState } from "../../components/EmptyState";
import {
  PROJECT_TODOS_BULK_MAX,
  normalizeTodoBulkResponse,
  projectTodosApi,
  type ProjectTodo,
  type ProjectTodoCreateBody,
  type ProjectTodoUpdateBody,
  type ProjectTodoStatus,
  type ProjectTodoBulkBody,
  type ProjectTodoBulkItem,
} from "../../api/modules/projectTodos";
import {
  projectsApi,
  type ProjectMember,
  type ProjectRole,
} from "../../api/modules/projects";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { parseApiError } from "../../utils/apiError";
import { message } from "../../utils/antdMessage";
import ProjectTodoDetail from "./ProjectTodoDetail";
import TodoFields, {
  emptyTodoFields,
  todoFieldsEqual,
  type TodoFieldValues,
} from "./TodoFields";
import { useTodoCatalog } from "./useTodoCatalog";
import { validatePlanDates } from "./planDates";
import {
  catalogErrorMessage,
  isTodoAccessLost as isNotFoundApiError,
} from "./TodoCatalogManager";
import type { ProjectTodoCatalog } from "../../api/modules/projectTodoCatalog";
import ProjectPlanViews, {
  type ProjectPlanViewsHandle,
  type PlanOperationScope,
  type PlanTodoPatchProposal,
  type PlanTodoMutationResult,
} from "./plan/ProjectPlanViews";
import styles from "./ProjectPlan.module.less";

const TODO_STATUSES: ProjectTodoStatus[] = ["todo", "in_progress", "done"];
const STATUS_FALLBACKS: Record<ProjectTodoStatus, string> = {
  todo: "待处理",
  in_progress: "进行中",
  done: "已完成",
};
interface Props {
  projectId: string;
  role: ProjectRole;
  members: ProjectMember[] | null;
  selectedTodoId?: string | null;
  onOpenTodo?: (todoId: string) => void;
  onCloseTodo?: () => void;
}
function httpStatus(error: unknown): number | null {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  const match = raw.match(/\b(4\d{2}|5\d{2})\b/);
  return match ? Number(match[1]) : null;
}
function isConflictApiError(error: unknown): boolean {
  const parsed = parseApiError(error);
  return (
    parsed?.details?.reason === "version_conflict" ||
    parsed?.code === "VERSION_CONFLICT" ||
    parsed?.code === "CONFLICT" ||
    httpStatus(error) === 409
  );
}
function sameFrame(a: PlanOperationScope, b: PlanOperationScope): boolean {
  return (
    a.lifetime === b.lifetime &&
    a.accountId === b.accountId &&
    a.projectId === b.projectId &&
    a.viewId === b.viewId
  );
}
function validTodo(
  todo: ProjectTodo | null | undefined,
  projectId: string,
): todo is ProjectTodo {
  return (
    !!todo &&
    todo.project_id === projectId &&
    typeof todo.todo_id === "string" &&
    todo.todo_id.length > 0 &&
    Number.isSafeInteger(todo.version) &&
    todo.version > 0 &&
    Number.isSafeInteger(todo.catalog_revision) &&
    todo.catalog_revision > 0
  );
}
interface EditorValues {
  title: string;
  description: string;
  assignee: number | null;
  status: ProjectTodoStatus;
  fields: TodoFieldValues;
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
  projectId: string;
  catalog: ProjectTodoCatalog | null;
  catalogLoading: boolean;
  catalogError: unknown;
  onCatalogRetry: () => Promise<unknown>;
  conflict: boolean;
  comparison: ProjectTodo | null;
  onCompare: () => void;
  onConfirmComparison: () => void;
  comparisonReady: boolean;
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
  projectId,
  catalog,
  catalogLoading,
  catalogError,
  onCatalogRetry,
  conflict,
  comparison,
  onCompare,
  onConfirmComparison,
  comparisonReady,
}: TodoEditorModalProps) {
  const { t } = useTranslation();
  const [title, setTitle] = useState(todo?.title ?? "");
  const [description, setDescription] = useState(todo?.description ?? "");
  const [assignee, setAssignee] = useState<number | null>(
    todo?.assignee_user_id ?? null,
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [status, setStatus] = useState<ProjectTodoStatus>(
    todo?.status ?? "todo",
  );
  const [fields, setFields] = useState<TodoFieldValues>(
    todo
      ? {
          start_date: todo.start_date,
          due_date: todo.due_date,
          priority_id: todo.priority_id,
          tag_ids: todo.tag_ids,
        }
      : emptyTodoFields(),
  );
  const originalFields = todo ?? emptyTodoFields();

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
          status !== todo.status ||
          !todoFieldsEqual(fields, todo) ||
          (isManager && assignee !== todo.assignee_user_id));

  const submit = () => {
    const datesChanged =
      fields.start_date !== originalFields.start_date ||
      fields.due_date !== originalFields.due_date;
    if (datesChanged) {
      const dateError = validatePlanDates(
        fields,
        catalogLoading || catalogError ? null : catalog?.server_today ?? null,
        originalFields.due_date,
      );
      if (dateError) {
        setFormError(t(`projects.todoFields.${dateError}`));
        return;
      }
    }
    if (
      fields.priority_id !== originalFields.priority_id ||
      JSON.stringify(fields.tag_ids) !== JSON.stringify(originalFields.tag_ids)
    ) {
      if (!catalog || catalogLoading || catalogError) {
        setFormError(
          t("projects.todoFields.catalogRequired", "请先加载项目目录。"),
        );
        return;
      }
    }
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
      status,
      fields,
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
      centered
      closable={{ "aria-label": t("common.close", "关闭") }}
      title={
        mode === "create"
          ? t("projects.plan.createTitle", "新建待办")
          : t("projects.plan.editTitle", "编辑待办")
      }
      onCancel={submitting ? undefined : onClose}
      maskClosable={false}
      destroyOnHidden
      classNames={{ body: styles.editorBody }}
      styles={{
        body: { maxHeight: "calc(100dvh - 230px)", overflowY: "auto" },
      }}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={submitting}>
          {t("common.cancel", "取消")}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          disabled={submitting || conflict || !changed}
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
      <label style={labelStyle} htmlFor="project-todo-editor-status">
        {t("projects.plan.statusLabel", "状态")}
      </label>
      <Select<ProjectTodoStatus>
        id="project-todo-editor-status"
        aria-label={t("projects.plan.statusLabel", "状态")}
        style={{ width: "100%" }}
        value={status}
        disabled={submitting}
        options={TODO_STATUSES.map((value) => ({
          value,
          label:
            mode === "create" && value === "todo"
              ? t("projects.todoFields.notStarted", "待开始")
              : t(`projects.plan.status.${value}`, STATUS_FALLBACKS[value]),
        }))}
        onChange={setStatus}
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
      <div className={styles.editorFields}>
        <TodoFields
          projectId={projectId}
          accountId={currentUserId}
          values={fields}
          original={originalFields}
          catalog={catalog}
          loading={catalogLoading}
          error={catalogError}
          onRetry={onCatalogRetry}
          onCatalogChanged={onCatalogRetry}
          canManage={isManager}
          disabled={submitting}
          onChange={setFields}
        />
      </div>
      {conflict && (
        <div className={styles.comparison}>
          <Button disabled={submitting} onClick={onCompare}>
            {t("projects.todoFields.refreshCompare", "刷新后比较")}
          </Button>
          {comparison && (
            <section
              aria-label={t("projects.todoFields.serverValues", "服务器当前值")}
            >
              <strong>
                {t("projects.todoFields.serverValues", "服务器当前值")}
              </strong>
              <p>{comparison.title}</p>
              <p>{comparison.description}</p>
              <p>
                {t(
                  `projects.plan.status.${comparison.status}`,
                  STATUS_FALLBACKS[comparison.status],
                )}
              </p>
              <TodoFields
                projectId={projectId}
                accountId={currentUserId}
                values={comparison}
                catalog={catalog}
                canManage={false}
                readOnly
              />
              <Button onClick={onConfirmComparison}>
                {t("projects.todoFields.confirmCompare", "确认比较")}
              </Button>
            </section>
          )}
          {mode === "create" && comparisonReady && (
            <Button onClick={onConfirmComparison}>
              {t("projects.todoFields.confirmCompare", "确认比较")}
            </Button>
          )}
        </div>
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

export default function ProjectPlan(props: Props) {
  const user = useCurrentUser();
  return (
    <ProjectPlanContent
      key={JSON.stringify([user?.id ?? null, props.projectId])}
      {...props}
    />
  );
}

function ProjectPlanContent({
  projectId,
  role,
  members,
  selectedTodoId,
  onOpenTodo,
  onCloseTodo,
}: Props) {
  const { t } = useTranslation();
  const currentUserId = useCurrentUser()?.id ?? null;
  const isManager = role === "owner" || role === "admin";
  const permissions = useRef({ role, currentUserId });
  permissions.current = { role, currentUserId };
  const views = useRef<ProjectPlanViewsHandle>(null);
  const active = useRef(true);
  const [loadedTodos, setLoadedTodos] = useState<ProjectTodo[]>([]);
  const loaded = useRef<ProjectTodo[]>([]);
  const [accessLost, setAccessLost] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editing, setEditing] = useState<ProjectTodo | null>(null);
  const [editorSubmitting, setEditorSubmitting] = useState(false);
  const [editorError, setEditorError] = useState<string | null>(null);
  const [editorConflict, setEditorConflict] = useState(false);
  const [serverComparison, setServerComparison] = useState<ProjectTodo | null>(
    null,
  );
  const [comparisonReady, setComparisonReady] = useState(false);
  const editorSeq = useRef(0);
  const editorShown = useRef(false);
  const editorBusy = useRef(false);
  const editorLocked = useRef(false);
  const editorContext = useRef<{
    scope: PlanOperationScope;
    catalogRevision: number | null;
  } | null>(null);
  const comparisonContext = useRef<{
    scope: PlanOperationScope;
    catalogRevision: number;
  } | null>(null);
  const explicitEditorRefresh = useRef<number | null>(null);
  const [detail, setDetail] = useState<{
    todoId: string;
    scope: PlanOperationScope;
    trigger: HTMLElement | null;
  } | null>(null);
  const detailRef = useRef(detail);
  detailRef.current = detail;
  const suppressedDeepLink = useRef<string | null>(null);
  const lostCallback = useRef<() => void>(() => {});
  const catalogState = useTodoCatalog(
    projectId,
    currentUserId,
    () => lostCallback.current(),
    loadedTodos.map((todo) => todo.catalog_revision),
  );
  const catalogRef = useRef(catalogState);
  catalogRef.current = catalogState;
  const catalog = catalogState.catalog;
  const latestCallbacks = useRef({ onCloseTodo, onOpenTodo });
  latestCallbacks.current = { onCloseTodo, onOpenTodo };

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      editorSeq.current += 1;
    };
  }, []);

  const current = useCallback(
    (scope: PlanOperationScope) =>
      active.current && views.current?.isCurrentOperation(scope) === true,
    [],
  );
  const canEditTodo = useCallback((todo: ProjectTodo) => {
    const actor = permissions.current;
    return (
      actor.role === "owner" ||
      actor.role === "admin" ||
      (actor.currentUserId !== null &&
        (todo.creator_user_id === actor.currentUserId ||
          todo.assignee_user_id === actor.currentUserId))
    );
  }, []);
  const canDeleteTodo = useCallback((todo: ProjectTodo) => {
    const actor = permissions.current;
    return (
      actor.role === "owner" ||
      actor.role === "admin" ||
      (actor.currentUserId !== null &&
        todo.creator_user_id === actor.currentUserId)
    );
  }, []);
  const clearPrivate = () => {
    if (!active.current) return;
    editorSeq.current += 1;
    editorShown.current = false;
    editorBusy.current = false;
    editorLocked.current = false;
    editorContext.current = null;
    comparisonContext.current = null;
    explicitEditorRefresh.current = null;
    views.current?.clearPrivate();
    catalogState.clear();
    loaded.current = [];
    setLoadedTodos([]);
    setAccessLost(true);
    setActionError(null);
    setEditorOpen(false);
    setEditing(null);
    setEditorSubmitting(false);
    setEditorError(null);
    setEditorConflict(false);
    setServerComparison(null);
    setComparisonReady(false);
    setDetail(null);
    detailRef.current = null;
    suppressedDeepLink.current = selectedTodoId ?? null;
  };
  lostCallback.current = clearPrivate;
  const notifyLost = useCallback(() => lostCallback.current(), []);

  const closeDetail = (snapshot = detailRef.current) => {
    if (!snapshot || !active.current) return;
    setDetail(null);
    detailRef.current = null;
    suppressedDeepLink.current = snapshot.todoId;
    latestCallbacks.current.onCloseTodo?.();
    queueMicrotask(() => {
      if (!active.current) return;
      if (snapshot.trigger?.isConnected) snapshot.trigger.focus();
      else
        document
          .querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
          ?.focus();
    });
  };
  const attachDetail = (
    todoId: string,
    scope: PlanOperationScope,
    trigger: HTMLElement | null,
  ) => {
    const frame = { todoId, scope, trigger };
    detailRef.current = frame;
    setDetail(frame);
  };
  const openDetail = (
    todo: ProjectTodo,
    trigger: HTMLElement | null,
    scope: PlanOperationScope,
  ) => {
    if (!current(scope) || !validTodo(todo, projectId)) return;
    suppressedDeepLink.current = null;
    attachDetail(todo.todo_id, scope, trigger);
    latestCallbacks.current.onOpenTodo?.(todo.todo_id);
  };

  const loadedCallback = useRef<
    (todos: readonly ProjectTodo[], scope: PlanOperationScope) => void
  >(() => {});
  loadedCallback.current = (todos, scope) => {
    if (!current(scope)) return;
    const deduped = new Map<string, ProjectTodo>();
    for (const todo of todos) {
      if (!validTodo(todo, projectId)) continue;
      const prior = deduped.get(todo.todo_id);
      if (
        !prior ||
        todo.version > prior.version ||
        (todo.version === prior.version &&
          todo.catalog_revision > prior.catalog_revision)
      )
        deduped.set(todo.todo_id, todo);
    }
    const next = [...deduped.values()];
    loaded.current = next;
    setLoadedTodos((previous) =>
      JSON.stringify(previous) === JSON.stringify(next) ? previous : next,
    );
    const editor = editorContext.current;
    if (
      editorShown.current &&
      editor &&
      !current(editor.scope) &&
      explicitEditorRefresh.current === null
    ) {
      if (!editorLocked.current || editorBusy.current) editorSeq.current += 1;
      editorBusy.current = false;
      editorLocked.current = true;
      setEditorSubmitting(false);
      setEditorConflict(true);
      setComparisonReady(false);
      setServerComparison(null);
      comparisonContext.current = null;
      setEditorError(t("projects.planViews.integration.staleDraft"));
    }
    const old = detailRef.current;
    if (old && !current(old.scope)) {
      if (!sameFrame(old.scope, scope)) closeDetail(old);
      else {
        const fresh = views.current?.captureOperation("detail");
        if (fresh && current(fresh))
          attachDetail(old.todoId, fresh, old.trigger);
      }
    } else if (
      !old &&
      selectedTodoId &&
      suppressedDeepLink.current !== selectedTodoId
    ) {
      const fresh = views.current?.captureOperation("detail");
      if (fresh && current(fresh)) attachDetail(selectedTodoId, fresh, null);
    }
  };
  const onLoaded = useCallback(
    (todos: readonly ProjectTodo[], scope: PlanOperationScope) =>
      loadedCallback.current(todos, scope),
    [],
  );

  useEffect(() => {
    if (!selectedTodoId) {
      if (onOpenTodo) {
        setDetail(null);
        detailRef.current = null;
      }
      suppressedDeepLink.current = null;
      return;
    }
    const opened = detailRef.current;
    if (opened?.todoId === selectedTodoId && current(opened.scope)) return;
    if (suppressedDeepLink.current !== selectedTodoId) {
      const scope = views.current?.captureOperation("detail");
      if (scope && current(scope)) attachDetail(selectedTodoId, scope, null);
    }
  }, [selectedTodoId, onOpenTodo, current]);

  const todoErrorMessage = (error: unknown, fallback: string) => {
    const reason = parseApiError(error)?.details?.reason;
    if (reason === "invalid_assignee")
      return t("projects.plan.invalidAssignee");
    if (reason === "no_change") return t("projects.plan.noChange");
    if (
      [
        "invalid_priority",
        "invalid_tags",
        "invalid_dates",
        "catalog_revision_conflict",
      ].includes(String(reason))
    )
      return catalogErrorMessage(error, t);
    // Unknown transport text may contain internal details. Keep it out of the UI.
    return fallback;
  };
  const acceptTodo = (scope: PlanOperationScope, todo: ProjectTodo) => {
    if (!current(scope) || !validTodo(todo, projectId)) return false;
    views.current?.acceptTodo(scope, todo);
    loaded.current = loaded.current.map((old) =>
      old.todo_id === todo.todo_id && old.version <= todo.version ? todo : old,
    );
    setLoadedTodos(loaded.current);
    return true;
  };
  const refreshAccepted = () => {
    void views.current?.refreshCurrent();
  };
  const mutationFailure = async (
    error: unknown,
    scope: PlanOperationScope,
    options: { todoId?: string; refresh?: boolean } = {},
  ): Promise<Exclude<PlanTodoMutationResult, { status: "saved" }>> => {
    if (isNotFoundApiError(error)) {
      // A missing todo, comment or image does not prove that the project was lost.
      // Recheck within the original operation so late replies cannot clear a new
      // account, project, view or query generation.
      try {
        const project = await projectsApi.get(scope.projectId);
        if (!current(scope))
          return {
            status: "stale",
            messageKey: "projects.planViews.integration.staleOperation",
          };
        if (project.project_id !== projectId)
          return { status: "failed", messageKey: "projects.plan.actionFailed" };
      } catch (projectError) {
        if (!current(scope))
          return {
            status: "stale",
            messageKey: "projects.planViews.integration.staleOperation",
          };
        if (
          isNotFoundApiError(projectError) ||
          httpStatus(projectError) === 403
        ) {
          clearPrivate();
          return { status: "forbidden", messageKey: "projects.plan.notFound" };
        }
        return { status: "failed", messageKey: "projects.plan.actionFailed" };
      }
      if (options.todoId) {
        loaded.current = loaded.current.filter(
          (todo) => todo.todo_id !== options.todoId,
        );
        setLoadedTodos(loaded.current);
        if (detailRef.current?.todoId === options.todoId) closeDetail();
        if (editing?.todo_id === options.todoId) {
          editorBusy.current = false;
          closeEditor();
        }
      }
      const messageKey =
        options.refresh === false
          ? "projects.plan.createFailed"
          : "projects.todoDetail.notFound";
      setActionError(t(messageKey));
      if (options.refresh !== false)
        // Deliver the unavailable-todo result to its field dialog before a new
        // query generation invalidates that operation. A user switch cancels it.
        setTimeout(() => {
          if (current(scope)) refreshAccepted();
        }, 0);
      return { status: "failed", messageKey };
    }
    if (isConflictApiError(error))
      return {
        status: "conflict",
        messageKey: "projects.todoFields.todoConflict",
      };
    if (httpStatus(error) === 403)
      return {
        status: "forbidden",
        messageKey: "projects.planViews.integration.writeForbidden",
      };
    if (httpStatus(error) === 422)
      return {
        status: "invalid",
        messageKey:
          parseApiError(error)?.details?.reason === "invalid_dates"
            ? "projects.planViews.integration.dateRejected"
            : "projects.planViews.integration.invalidProposal",
      };
    return { status: "failed", messageKey: "projects.plan.actionFailed" };
  };
  const proposePatch = async (
    todo: ProjectTodo,
    proposal: PlanTodoPatchProposal,
    scope: PlanOperationScope,
  ): Promise<PlanTodoMutationResult> => {
    if (!current(scope) || !validTodo(todo, projectId))
      return {
        status: "stale",
        messageKey: "projects.planViews.integration.staleOperation",
      };
    const live =
      loaded.current.find((item) => item.todo_id === todo.todo_id) ?? todo;
    if (
      !canEditTodo(live) ||
      ("assignee_user_id" in proposal.changes &&
        permissions.current.role === "member")
    )
      return {
        status: "forbidden",
        messageKey: "projects.planViews.integration.writeForbidden",
      };
    if (
      proposal.baseVersion !== todo.version ||
      live.version > proposal.baseVersion
    )
      return {
        status: "conflict",
        messageKey: "projects.todoFields.todoConflict",
        current: live,
      };
    const body: ProjectTodoUpdateBody = {
      ...proposal.changes,
      expected_version: proposal.baseVersion,
    };
    if ("start_date" in body || "due_date" in body) {
      const meta = catalogRef.current;
      const error = validatePlanDates(
        {
          start_date:
            body.start_date === undefined ? todo.start_date : body.start_date,
          due_date: body.due_date === undefined ? todo.due_date : body.due_date,
        },
        meta.loading || meta.error ? null : meta.catalog?.server_today ?? null,
        todo.due_date,
      );
      if (error)
        return {
          status: "invalid",
          messageKey: `projects.todoFields.${error}`,
        };
    }
    if ("priority_id" in body || "tag_ids" in body) {
      const meta = catalogRef.current;
      if (
        !Number.isSafeInteger(proposal.catalogRevision) ||
        meta.loading ||
        meta.error ||
        proposal.catalogRevision !== meta.catalog?.revision
      )
        return {
          status: "conflict",
          messageKey: "projects.todoFields.todoConflict",
        };
      body.expected_catalog_revision = proposal.catalogRevision;
    }
    try {
      const updated = await projectTodosApi.update(
        projectId,
        todo.todo_id,
        body,
      );
      if (!current(scope))
        return {
          status: "stale",
          messageKey: "projects.planViews.integration.staleOperation",
        };
      if (
        !validTodo(updated, projectId) ||
        updated.todo_id !== todo.todo_id ||
        updated.version <= proposal.baseVersion
      )
        throw new Error("Invalid todo mutation response");
      acceptTodo(scope, updated);
      refreshAccepted();
      return { status: "saved", todo: updated };
    } catch (error) {
      if (!current(scope))
        return {
          status: "stale",
          messageKey: "projects.planViews.integration.staleOperation",
        };
      const result = await mutationFailure(error, scope, {
        todoId: todo.todo_id,
      });
      if (!isNotFoundApiError(error) && !current(scope))
        return {
          status: "stale",
          messageKey: "projects.planViews.integration.staleOperation",
        };
      if (result.status !== "stale")
        setActionError(todoErrorMessage(error, t(result.messageKey)));
      return result;
    }
  };
  const deleteTodo = async (todo: ProjectTodo, scope: PlanOperationScope) => {
    if (!current(scope) || !canDeleteTodo(todo) || !validTodo(todo, projectId))
      return;
    setActionError(null);
    try {
      await projectTodosApi.remove(projectId, todo.todo_id, todo.version);
      if (!current(scope)) return;
      loaded.current = loaded.current.filter(
        (item) => item.todo_id !== todo.todo_id,
      );
      setLoadedTodos(loaded.current);
      if (detailRef.current?.todoId === todo.todo_id) closeDetail();
      refreshAccepted();
      void message.success(t("projects.plan.deleted"));
    } catch (error) {
      if (!current(scope)) return;
      const failure = await mutationFailure(error, scope, {
        todoId: todo.todo_id,
      });
      if (
        failure.status !== "stale" &&
        (isNotFoundApiError(error) || current(scope))
      )
        setActionError(todoErrorMessage(error, t(failure.messageKey)));
    }
  };
  const bulkTodos = async (
    items: readonly ProjectTodoBulkItem[],
    changes: Omit<ProjectTodoBulkBody, "items">,
    scope: PlanOperationScope,
  ): Promise<void> => {
    const unique = new Map(items.map((item) => [item.todo_id, item]));
    if (
      !current(scope) ||
      !["owner", "admin"].includes(permissions.current.role) ||
      unique.size !== items.length ||
      unique.size < 1 ||
      unique.size > PROJECT_TODOS_BULK_MAX
    )
      throw new Error("Invalid or stale bulk action");
    if (
      items.some(
        (item) =>
          !Number.isSafeInteger(item.expected_version) ||
          item.expected_version < 1 ||
          !loaded.current.some(
            (todo) =>
              todo.todo_id === item.todo_id &&
              todo.version === item.expected_version,
          ),
      )
    )
      throw new Error("Bulk selection changed");
    setActionError(null);
    try {
      const response = await projectTodosApi.bulk(projectId, {
        items: [...items],
        ...changes,
      });
      if (!current(scope)) throw new Error("Stale bulk response");
      const updated = normalizeTodoBulkResponse(response);
      if (
        updated.length !== unique.size ||
        updated.some(
          (todo) =>
            !validTodo(todo, projectId) ||
            !unique.has(todo.todo_id) ||
            todo.version <= unique.get(todo.todo_id)!.expected_version,
        ) ||
        new Set(updated.map((todo) => todo.todo_id)).size !== unique.size
      )
        throw new Error("Invalid bulk mutation response");
      for (const todo of updated) acceptTodo(scope, todo);
      refreshAccepted();
      void message.success(
        t("projects.plan.bulkSuccess", { count: items.length }),
      );
    } catch (error) {
      if (current(scope)) {
        const failure = await mutationFailure(error, scope);
        if (
          failure.status !== "stale" &&
          (isNotFoundApiError(error) || current(scope))
        )
          setActionError(todoErrorMessage(error, t(failure.messageKey)));
      }
      throw error;
    }
  };

  const beginEditor = (todo: ProjectTodo | null, scope: PlanOperationScope) => {
    if (!current(scope) || (todo && !canEditTodo(todo))) return;
    editorSeq.current += 1;
    editorContext.current = {
      scope,
      catalogRevision: catalogRef.current.catalog?.revision ?? null,
    };
    editorShown.current = true;
    editorBusy.current = false;
    editorLocked.current = false;
    comparisonContext.current = null;
    explicitEditorRefresh.current = null;
    setEditing(todo);
    setEditorOpen(true);
    setEditorSubmitting(false);
    setEditorError(null);
    setEditorConflict(false);
    setServerComparison(null);
    setComparisonReady(false);
  };
  const lockEditor = (error: unknown) => {
    editorLocked.current = true;
    comparisonContext.current = null;
    setEditorConflict(true);
    setServerComparison(null);
    setComparisonReady(false);
    setEditorError(
      todoErrorMessage(error, t("projects.todoFields.todoConflict")),
    );
  };
  const closeEditor = () => {
    if (editorBusy.current) return;
    editorSeq.current += 1;
    editorShown.current = false;
    editorBusy.current = false;
    editorLocked.current = false;
    editorContext.current = null;
    comparisonContext.current = null;
    setEditorSubmitting(false);
    setEditorOpen(false);
    setEditorError(null);
    setEditing(null);
  };
  const submitEditor = async (values: EditorValues) => {
    const context = editorContext.current;
    if (
      !context ||
      !current(context.scope) ||
      editorBusy.current ||
      editorLocked.current ||
      (editing && !canEditTodo(editing))
    )
      return;
    const seq = editorSeq.current;
    const scope = context.scope;
    const owns = () =>
      seq === editorSeq.current && editorShown.current && current(scope);
    editorBusy.current = true;
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
          body.description_format = editing.description_format;
        }
        if (isManager && values.assignee !== editing.assignee_user_id)
          body.assignee_user_id = values.assignee;
        if (values.status !== editing.status) body.status = values.status;
        if (values.fields.start_date !== editing.start_date)
          body.start_date = values.fields.start_date;
        if (values.fields.due_date !== editing.due_date)
          body.due_date = values.fields.due_date;
        if (values.fields.priority_id !== editing.priority_id)
          body.priority_id = values.fields.priority_id;
        if (
          JSON.stringify([...values.fields.tag_ids].sort()) !==
          JSON.stringify([...editing.tag_ids].sort())
        )
          body.tag_ids = [...values.fields.tag_ids].sort();
        if ("priority_id" in body || "tag_ids" in body) {
          if (
            context.catalogRevision !== catalogRef.current.catalog?.revision ||
            catalogRef.current.loading ||
            catalogRef.current.error
          ) {
            lockEditor(new Error('409 - {"error":{"code":"CONFLICT"}}'));
            return;
          }
          body.expected_catalog_revision = context.catalogRevision ?? undefined;
        }
        const updated = await projectTodosApi.update(
          projectId,
          editing.todo_id,
          body,
        );
        if (!owns()) return;
        if (
          !validTodo(updated, projectId) ||
          updated.todo_id !== editing.todo_id ||
          updated.version <= editing.version
        )
          throw new Error("Invalid todo mutation response");
        acceptTodo(scope, updated);
        editorShown.current = false;
        setEditorOpen(false);
        setEditing(null);
        void message.success(t("projects.plan.saved"));
        refreshAccepted();
      } else {
        const body: ProjectTodoCreateBody = {
          title: values.title,
          description: values.description,
          status: values.status,
        };
        if (values.assignee !== null) body.assignee_user_id = values.assignee;
        if (values.fields.start_date !== null)
          body.start_date = values.fields.start_date;
        if (values.fields.due_date !== null)
          body.due_date = values.fields.due_date;
        if (values.fields.priority_id !== null)
          body.priority_id = values.fields.priority_id;
        if (values.fields.tag_ids.length)
          body.tag_ids = [...values.fields.tag_ids].sort();
        if (body.priority_id || body.tag_ids?.length) {
          if (
            context.catalogRevision !== catalogRef.current.catalog?.revision ||
            catalogRef.current.loading ||
            catalogRef.current.error
          ) {
            lockEditor(new Error('409 - {"error":{"code":"CONFLICT"}}'));
            return;
          }
          body.expected_catalog_revision = context.catalogRevision ?? undefined;
        }
        const created = await projectTodosApi.create(projectId, body);
        if (!owns()) return;
        if (!validTodo(created, projectId))
          throw new Error("Invalid todo create response");
        acceptTodo(scope, created);
        editorShown.current = false;
        setEditorOpen(false);
        void message.success(t("projects.plan.created"));
        refreshAccepted();
      }
    } catch (error) {
      if (!owns()) return;
      if (isNotFoundApiError(error)) {
        const failure = await mutationFailure(
          error,
          scope,
          editing ? { todoId: editing.todo_id } : { refresh: false },
        );
        if (owns() && failure.status !== "stale")
          setEditorError(t(failure.messageKey));
      } else if (
        isConflictApiError(error) ||
        (httpStatus(error) === 422 &&
          parseApiError(error)?.details?.reason === "invalid_dates")
      )
        lockEditor(error);
      else
        setEditorError(
          todoErrorMessage(
            error,
            t(
              editing
                ? "projects.plan.saveFailed"
                : "projects.plan.createFailed",
            ),
          ),
        );
    } finally {
      if (
        active.current &&
        seq === editorSeq.current &&
        (current(scope) || !editorShown.current)
      ) {
        editorBusy.current = false;
        setEditorSubmitting(false);
      }
    }
  };
  const compareEditor = async () => {
    const original = editorContext.current;
    const start = views.current?.captureOperation("editor-compare");
    if (
      !original ||
      !start ||
      !current(start) ||
      !sameFrame(original.scope, start) ||
      editorBusy.current
    )
      return;
    const seq = editorSeq.current;
    editorBusy.current = true;
    explicitEditorRefresh.current = seq;
    setEditorSubmitting(true);
    setComparisonReady(false);
    comparisonContext.current = null;
    try {
      const result = editing
        ? await views.current!.refreshTodoCompare(editing)
        : null;
      const refreshed = editing
        ? result?.state === "ready"
        : await views.current!.refreshCurrent();
      const fresh = views.current?.captureOperation("editor-comparison");
      if (
        !active.current ||
        seq !== editorSeq.current ||
        !fresh ||
        !current(fresh) ||
        !sameFrame(original.scope, fresh)
      )
        return;
      const revision = catalogRef.current.catalog?.revision;
      if (
        !refreshed ||
        revision === undefined ||
        catalogRef.current.loading ||
        catalogRef.current.error
      ) {
        setEditorError(t("projects.planViews.integration.compareFailed"));
        return;
      }
      if (
        result?.state === "ready" &&
        (!validTodo(result.todo, projectId) ||
          result.todo.todo_id !== editing?.todo_id ||
          result.catalogRevision !== revision)
      ) {
        setEditorError(t("projects.planViews.integration.compareFailed"));
        return;
      }
      // Loaded rows may commit after the refresh promise settles. Keep the
      // draft on that frame while its catalog baseline awaits confirmation.
      editorContext.current = { ...original, scope: fresh };
      comparisonContext.current = { scope: fresh, catalogRevision: revision };
      setServerComparison(result?.state === "ready" ? result.todo : null);
      setComparisonReady(true);
      setEditorError(null);
    } catch (error) {
      if (!active.current || seq !== editorSeq.current) return;
      const fresh = views.current?.captureOperation("editor-compare-error");
      if (!fresh || !current(fresh) || !sameFrame(original.scope, fresh))
        return;
      if (isNotFoundApiError(error)) clearPrivate();
      else
        setEditorError(todoErrorMessage(error, t("projects.plan.loadFailed")));
    } finally {
      if (active.current && seq === editorSeq.current && editorShown.current) {
        editorBusy.current = false;
        explicitEditorRefresh.current = null;
        setEditorSubmitting(false);
      }
    }
  };
  const confirmComparison = () => {
    const comparison = comparisonContext.current;
    if (
      !comparison ||
      !current(comparison.scope) ||
      !comparisonReady ||
      (editing && !serverComparison)
    )
      return;
    if (serverComparison && !canEditTodo(serverComparison)) {
      setEditorError(t("projects.planViews.integration.writeForbidden"));
      return;
    }
    const fresh = views.current?.captureOperation("editor");
    if (!fresh || !current(fresh) || !sameFrame(comparison.scope, fresh))
      return;
    if (serverComparison) setEditing(serverComparison);
    editorContext.current = {
      scope: fresh,
      catalogRevision: comparison.catalogRevision,
    };
    editorLocked.current = false;
    comparisonContext.current = null;
    setEditorConflict(false);
    setEditorError(null);
    setServerComparison(null);
    setComparisonReady(false);
  };
  const refreshEditorCatalog = async () => {
    const original = editorContext.current;
    const freshStart = views.current?.captureOperation("editor-catalog");
    if (
      !original ||
      !freshStart ||
      !current(freshStart) ||
      !sameFrame(original.scope, freshStart)
    )
      return false;
    const seq = editorSeq.current;
    explicitEditorRefresh.current = seq;
    try {
      const refreshed = await views.current!.refreshCurrent();
      const fresh = views.current?.captureOperation("editor");
      if (
        !active.current ||
        seq !== editorSeq.current ||
        !editorShown.current ||
        !fresh ||
        !current(fresh) ||
        !sameFrame(original.scope, fresh)
      )
        return false;
      if (
        !refreshed ||
        catalogRef.current.loading ||
        catalogRef.current.error ||
        !catalogRef.current.catalog
      )
        return false;
      // An explicit catalog action can advance R. It never advances the todo's V.
      editorContext.current = {
        scope: fresh,
        catalogRevision: catalogRef.current.catalog.revision,
      };
      return true;
    } finally {
      if (seq === editorSeq.current) explicitEditorRefresh.current = null;
    }
  };

  return (
    <div className={styles.plan}>
      {accessLost || currentUserId === null ? (
        <EmptyState
          title={t("projects.plan.notFound")}
          description={t("projects.plan.notFoundHint")}
          actionLabel={t("common.retry")}
          onAction={() => {
            setAccessLost(false);
            void catalogState.reload();
          }}
        />
      ) : (
        <ProjectPlanViews
          ref={views}
          accountId={currentUserId}
          projectId={projectId}
          role={role}
          members={members ?? []}
          catalog={catalog}
          catalogLoading={catalogState.loading}
          catalogError={catalogState.error}
          onCatalogRetry={catalogState.reload}
          selectedTodoId={selectedTodoId}
          canEdit={canEditTodo}
          canDelete={canDeleteTodo}
          onCreateTodo={(scope) => beginEditor(null, scope)}
          onEditTodo={(todo, scope) => beginEditor(todo, scope)}
          onDeleteTodo={(todo, scope) => {
            void deleteTodo(todo, scope);
          }}
          onOpenTodo={openDetail}
          onProposeTodoPatch={proposePatch}
          onBulkTodo={bulkTodos}
          onLoadedTodosChanged={onLoaded}
          onProjectAccessLost={notifyLost}
        />
      )}
      {actionError && !accessLost && (
        <Alert
          type="warning"
          showIcon
          className={styles.actionNotice}
          message={actionError}
          action={
            <Button
              size="small"
              onClick={() => {
                void views.current?.refreshCurrent().then((ok) => {
                  if (ok && active.current) setActionError(null);
                });
              }}
            >
              {t("projects.plan.refresh")}
            </Button>
          }
        />
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
          projectId={projectId}
          catalog={catalog}
          catalogLoading={catalogState.loading}
          catalogError={catalogState.error}
          onCatalogRetry={refreshEditorCatalog}
          conflict={editorConflict}
          comparison={serverComparison}
          comparisonReady={comparisonReady}
          onCompare={() => {
            void compareEditor();
          }}
          onConfirmComparison={confirmComparison}
          onClose={closeEditor}
          onSubmit={(values) => {
            void submitEditor(values);
          }}
        />
      )}
      {detail && !accessLost && currentUserId !== null && (
        <ProjectTodoDetail
          key={JSON.stringify([
            currentUserId,
            projectId,
            detail.todoId,
            detail.scope.lifetime,
            detail.scope.queryGeneration,
            detail.scope.operationGeneration,
          ])}
          projectId={projectId}
          todoId={detail.todoId}
          role={role}
          members={members ?? []}
          currentUserId={currentUserId}
          onClose={() => {
            if (current(detail.scope)) closeDetail(detail);
          }}
          onChanged={(todo) => {
            if (current(detail.scope) && acceptTodo(detail.scope, todo))
              refreshAccepted();
          }}
          onAccessLost={() => {
            if (current(detail.scope))
              void mutationFailure(
                new Error('404 - {"error":{"code":"NOT_FOUND"}}'),
                detail.scope,
                { todoId: detail.todoId },
              );
          }}
        />
      )}
    </div>
  );
}
