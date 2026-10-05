import type { ProjectTodo } from "../../../api/modules/projectTodos";
import type { ProjectTodoCatalog } from "../../../api/modules/projectTodoCatalog";
import type { ProjectMember } from "../../../api/modules/projects";
import type { PlanVisibleField } from "../../../api/modules/projectPlanViews";
import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TFunction } from "i18next";
import type { ProjectTodoBulkItem } from "../../../api/modules/projectTodos";
import type { PlanLaneState, PlanRendererProps } from "./ProjectPlanViews";
import type {
  PlanTodoCompareResult,
  PlanTodoPatchProposal,
} from "./ProjectPlanViews";
import { Button, Modal, Popconfirm } from "antd";
import { isPlanDate, validatePlanDates } from "../planDates";
import {
  planGanttInterval,
  proposeCalendarMove,
  proposeGanttResize,
  proposeGanttShift,
} from "./planViewDates";
import type { PlanDateBasis, PlanDateProposal } from "./planViewDates";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import styles from "./PlanTodoAttributes.module.less";

export interface PlanTodoAttributesProps {
  todo: ProjectTodo;
  fields: readonly PlanVisibleField[];
  catalog: ProjectTodoCatalog | null;
  members: readonly ProjectMember[];
  serverToday: string | null;
  serverTimezone: string | null;
  onOpenTodo?: (todo: ProjectTodo, trigger: HTMLElement | null) => void;
}

const fieldFallbacks: Record<PlanVisibleField, string> = {
  title: "标题",
  status: "状态",
  assignee: "处理人",
  priority: "优先级",
  tags: "标签",
  start_date: "开始日期",
  due_date: "截止日期",
  created_at: "创建时间",
  updated_at: "更新时间",
  source: "来源",
};

export function PlanFieldLabel({ field }: { field: PlanVisibleField }) {
  const { t } = useTranslation();
  return <>{t(`projects.planViews.fields.${field}`, fieldFallbacks[field])}</>;
}

export function PlanTodoFieldValue({
  todo,
  field,
  catalog,
  members,
  serverTimezone,
  onOpenTodo,
}: Omit<PlanTodoAttributesProps, "fields"> & { field: PlanVisibleField }) {
  const { t } = useTranslation();
  const none = t("projects.planViews.none", "无");
  const unavailable = t(
    "projects.planViews.catalogUnavailable",
    "目录信息暂不可用",
  );
  const catalogReady =
    catalog?.project_id === todo.project_id &&
    catalog.revision === todo.catalog_revision;
  const archived = (name: string, archivedAt: number | null) =>
    archivedAt === null
      ? name
      : `${name} · ${t("projects.planViews.archived", "已停用")}`;
  if (field === "title")
    return onOpenTodo ? (
      <button
        type="button"
        className={styles.title}
        aria-label={t("projects.planViews.openTodo", "查看待办：{{title}}", {
          title: todo.title,
        })}
        onClick={(event) => onOpenTodo(todo, event.currentTarget)}
      >
        {todo.title}
      </button>
    ) : (
      <span className={styles.titleText}>{todo.title}</span>
    );
  if (field === "status")
    return (
      <span className={styles.status} data-status={todo.status}>
        {t(
          `projects.planViews.status.${todo.status}`,
          {
            todo: "待开始",
            in_progress: "进行中",
            done: "已完成",
          }[todo.status],
        )}
      </span>
    );
  if (field === "assignee")
    return (
      <>
        {todo.assignee_user_id === null
          ? t("projects.planViews.unassigned", "未指派")
          : members.find((member) => member.user_id === todo.assignee_user_id)
              ?.username ?? t("projects.planViews.unknownMember", "未知成员")}
      </>
    );
  if (field === "start_date" || field === "due_date")
    return <>{todo[field] ?? none}</>;
  if (field === "source")
    return <>{t("projects.planViews.source.manual", "手动创建")}</>;
  if (field === "priority") {
    if (todo.priority_id === null) return <>{none}</>;
    const item =
      catalogReady &&
      catalog.priorities.find(
        (priority) => priority.priority_id === todo.priority_id,
      );
    return item ? (
      <span className={styles.chip} data-color={item.color}>
        {archived(item.name, item.archived_at)}
      </span>
    ) : (
      <>{unavailable}</>
    );
  }
  if (field === "tags") {
    if (todo.tag_ids.length === 0) return <>{none}</>;
    const items = catalogReady
      ? todo.tag_ids.map((id) => catalog.tags.find((tag) => tag.tag_id === id))
      : [];
    if (items.length !== todo.tag_ids.length || items.some((item) => !item))
      return <>{unavailable}</>;
    return (
      <>
        {items.map(
          (item) =>
            item && (
              <span
                key={item.tag_id}
                className={styles.chip}
                data-color={item.color}
              >
                {archived(item.name, item.archived_at)}
              </span>
            ),
        )}
      </>
    );
  }
  if (!serverTimezone)
    return (
      <>
        {t("projects.planViews.metadataUnavailable", "服务器日期信息暂不可用")}
      </>
    );
  try {
    return <>{formatServerDateTime(todo[field], serverTimezone)}</>;
  } catch {
    return (
      <>
        {t("projects.planViews.metadataUnavailable", "服务器日期信息暂不可用")}
      </>
    );
  }
}

function PlanSubtodoSummary({ todo }: { todo: ProjectTodo }) {
  const { t } = useTranslation();
  if (todo.parent_todo_id !== null || todo.children_count === 0) return null;
  return (
    <div className={styles.attribute} data-plan-field="subtodos">
      <dt>{t("projects.subtodos.countLabel", "子待办")}</dt>
      <dd>
        <span className={styles.chip}>
          {t("projects.subtodos.countSummary", "{{done}}/{{total}} 已完成", {
            done: todo.done_children_count,
            total: todo.children_count,
          })}
        </span>
      </dd>
    </div>
  );
}

export default function PlanTodoAttributes(props: PlanTodoAttributesProps) {
  return (
    <dl className={styles.attributes}>
      {props.fields.map((field) => (
        <div
          key={field}
          data-plan-field={field}
          className={
            field === "title" ? styles.titleAttribute : styles.attribute
          }
        >
          <dt>
            <PlanFieldLabel field={field} />
          </dt>
          <dd>
            <PlanTodoFieldValue {...props} field={field} />
          </dd>
        </div>
      ))}
      <PlanSubtodoSummary todo={props.todo} />
    </dl>
  );
}

export { uniqueTodoSnapshots as uniquePlanTodos } from "./todoSnapshot";

export function planLaneLabel(
  lane: PlanLaneState,
  renderer: PlanRendererProps,
  t: TFunction,
): string {
  const key = lane.groupKey;
  if (!key)
    return t(
      lane.bucket === "unscheduled"
        ? "projects.planViews.unscheduled"
        : "projects.planViews.allTodos",
      lane.bucket === "unscheduled" ? "未排期" : "全部待办",
    );
  if (key.kind === "status")
    return t(
      `projects.planViews.status.${key.id}`,
      { todo: "待开始", in_progress: "进行中", done: "已完成" }[
        key.id as "todo" | "in_progress" | "done"
      ] ?? "状态",
    );
  if (key.kind === "source")
    return t("projects.planViews.source.manual", "手动创建");
  if (key.kind === "assignee")
    return key.id === null
      ? t("projects.planViews.unassigned", "未指派")
      : renderer.members.find((member) => String(member.user_id) === key.id)
          ?.username ?? t("projects.planViews.unknownMember", "未知成员");
  if (key.id === null)
    return t(
      key.kind === "tag"
        ? "projects.planViews.noTags"
        : "projects.planViews.noPriority",
      key.kind === "tag" ? "无标签" : "无优先级",
    );
  const item =
    key.kind === "priority"
      ? renderer.catalog?.priorities.find(
          (priority) => priority.priority_id === key.id,
        )
      : renderer.catalog?.tags.find((tag) => tag.tag_id === key.id);
  if (!item)
    return t("projects.planViews.catalogUnavailable", "目录信息暂不可用");
  return item.archived_at === null
    ? item.name
    : `${item.name} · ${t("projects.planViews.archived", "已停用")}`;
}

export function usePlanRendererFrame(renderer: PlanRendererProps) {
  const key = JSON.stringify([
    renderer.accountId,
    renderer.projectId,
    renderer.view.view_id,
    renderer.view.type,
    renderer.definition,
    renderer.window,
  ]);
  const frame = useRef({ key, generation: 0, active: true });
  if (frame.current.key !== key)
    frame.current = {
      key,
      generation: frame.current.generation + 1,
      active: true,
    };
  useEffect(() => {
    frame.current.active = true;
    return () => {
      frame.current.active = false;
    };
  }, []);
  const isCurrent = useCallback(
    (generation: number) =>
      frame.current.active && frame.current.generation === generation,
    [],
  );
  return { key, generation: frame.current.generation, isCurrent };
}

export function usePlanSelection(renderer: PlanRendererProps) {
  const [selected, setSelected] = useState<readonly ProjectTodoBulkItem[]>([]);
  const frame = usePlanRendererFrame(renderer);
  useEffect(() => {
    setSelected([]);
  }, [frame.key]);
  const toggle = (todo: ProjectTodo) => {
    if (!renderer.isManager) return;
    setSelected((old) =>
      old.some((item) => item.todo_id === todo.todo_id)
        ? old.filter((item) => item.todo_id !== todo.todo_id)
        : old.length < 50
        ? [...old, { todo_id: todo.todo_id, expected_version: todo.version }]
        : old,
    );
  };
  return { selected, toggle, clear: () => setSelected([]) };
}

export function PlanSelectionCheckbox({
  todo,
  renderer,
  selection,
}: {
  todo: ProjectTodo;
  renderer: PlanRendererProps;
  selection: ReturnType<typeof usePlanSelection>;
}) {
  const { t } = useTranslation();
  if (!renderer.isManager) return null;
  const checked = selection.selected.some(
    (item) => item.todo_id === todo.todo_id,
  );
  return (
    <input
      type="checkbox"
      aria-label={t("projects.planViews.selectTodo", "选择待办：{{title}}", {
        title: todo.title,
      })}
      checked={checked}
      disabled={!checked && selection.selected.length >= 50}
      onChange={() => selection.toggle(todo)}
    />
  );
}

export function PlanBulkToolbar({
  renderer,
  selection,
}: {
  renderer: PlanRendererProps;
  selection: ReturnType<typeof usePlanSelection>;
}) {
  const { t } = useTranslation();
  const frame = usePlanRendererFrame(renderer);
  const [status, setStatus] = useState("");
  const [assignee, setAssignee] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  useEffect(() => {
    setStatus("");
    setAssignee("");
    setBusy(false);
    setError(false);
  }, [frame.key]);
  if (!renderer.isManager || selection.selected.length === 0) return null;
  const submit = async () => {
    if (busy || (!status && !assignee)) return;
    const generation = frame.generation;
    const items = selection.selected.map((item) => ({ ...item }));
    setBusy(true);
    setError(false);
    try {
      await renderer.onBulkTodo(items, {
        ...(status ? { status: status as ProjectTodo["status"] } : {}),
        ...(assignee
          ? { assignee_user_id: assignee === "none" ? null : Number(assignee) }
          : {}),
      });
      if (frame.isCurrent(generation)) selection.clear();
    } catch {
      if (frame.isCurrent(generation)) setError(true);
    } finally {
      if (frame.isCurrent(generation)) setBusy(false);
    }
  };
  return (
    <div className={styles.toolbar}>
      <span>
        {t(
          "projects.planViews.bulkSelected",
          "已选择 {{count}} 条待办，最多 50 条",
          { count: selection.selected.length },
        )}
      </span>
      <label>
        {t("projects.planViews.bulkStatus", "批量状态")}
        <select
          aria-label={t("projects.planViews.bulkStatus", "批量状态")}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
          disabled={busy}
        >
          <option value="">
            {t("projects.planViews.keepValue", "保持原值")}
          </option>
          {(["todo", "in_progress", "done"] as const).map((value) => (
            <option value={value} key={value}>
              {t(
                `projects.planViews.status.${value}`,
                { todo: "待开始", in_progress: "进行中", done: "已完成" }[
                  value
                ],
              )}
            </option>
          ))}
        </select>
      </label>
      <label>
        {t("projects.planViews.bulkAssignee", "批量处理人")}
        <select
          aria-label={t("projects.planViews.bulkAssignee", "批量处理人")}
          value={assignee}
          onChange={(event) => setAssignee(event.target.value)}
          disabled={busy}
        >
          <option value="">
            {t("projects.planViews.keepValue", "保持原值")}
          </option>
          <option value="none">
            {t("projects.planViews.unassigned", "未指派")}
          </option>
          {renderer.members.map((member) => (
            <option key={member.user_id} value={String(member.user_id)}>
              {member.username}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className={styles.control}
        disabled={busy || (!status && !assignee)}
        onClick={() => void submit()}
      >
        {t("projects.planViews.bulkApply", "应用批量修改")}
      </button>
      <button
        type="button"
        className={styles.control}
        disabled={busy}
        onClick={selection.clear}
      >
        {t("projects.planViews.clearSelection", "清除选择")}
      </button>
      {error && (
        <span role="alert">
          {t("projects.planViews.failed", "操作失败，请刷新比较后重试。")}
        </span>
      )}
    </div>
  );
}

export function PlanTodoActions({
  todo,
  renderer,
  onQuickStatus,
}: {
  todo: ProjectTodo;
  renderer: PlanRendererProps;
  onQuickStatus?: (todo: ProjectTodo, trigger: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const [deletion, setDeletion] = useState<{
    todo: ProjectTodo;
    renderer: PlanRendererProps;
  } | null>(null);
  const frame = usePlanRendererFrame(renderer);
  useEffect(() => {
    setDeletion(null);
  }, [frame.key]);
  return (
    <div className={styles.actions}>
      {renderer.canEdit(todo) && onQuickStatus && (
        <button
          type="button"
          className={styles.control}
          onClick={(event) => onQuickStatus(todo, event.currentTarget)}
        >
          {t("projects.planViews.changeStatus", "修改状态")}
        </button>
      )}
      {renderer.canEdit(todo) && (
        <button
          type="button"
          className={styles.control}
          onClick={() => renderer.onEditTodo(todo)}
        >
          {t("projects.planViews.editTodo", "编辑待办")}
        </button>
      )}
      {renderer.canDelete(todo) && (
        <Popconfirm
          open={deletion !== null}
          onOpenChange={(open) =>
            setDeletion(
              open
                ? { todo: { ...todo, tag_ids: [...todo.tag_ids] }, renderer }
                : null,
            )
          }
          title={t("projects.plan.deleteConfirm", "删除这条待办？")}
          okText={t("projects.plan.deleteOk", "确认删除")}
          cancelText={t("projects.planViews.cancel", "取消")}
          onConfirm={() => {
            if (deletion && renderer.canDelete(deletion.todo))
              deletion.renderer.onDeleteTodo(deletion.todo);
            setDeletion(null);
          }}
        >
          <button type="button" className={styles.control}>
            {t("projects.planViews.deleteTodo", "删除待办")}
          </button>
        </Popconfirm>
      )}
    </div>
  );
}

export type PlanDateOperation =
  | { kind: "shift"; delta: number }
  | { kind: "resize"; edge: "start" | "end"; date: string | null }
  | { kind: "calendar"; basis: PlanDateBasis; date: string | null };
export interface PlanTodoEditRequest {
  todo: ProjectTodo;
  renderer: PlanRendererProps;
  mode: "status" | "assignee" | "priority" | "gantt" | "calendar";
  trigger: HTMLElement | null;
  changes?: PlanTodoPatchProposal["changes"];
  dateOperation?: PlanDateOperation;
  autoSubmit?: boolean;
}

/** A draft and its version stay paired until the user confirms a fresh comparison. */
export function PlanTodoPatchDialog({
  request,
  renderer,
  onClose,
}: {
  request: PlanTodoEditRequest;
  renderer: PlanRendererProps;
  onClose(): void;
}) {
  const { t } = useTranslation();
  const frame = usePlanRendererFrame(renderer);
  const [baseline, setBaseline] = useState(() => ({
    ...request.todo,
    tag_ids: [...request.todo.tag_ids],
  }));
  const [catalogRevision, setCatalogRevision] = useState(
    request.renderer.catalog?.revision,
  );
  const [confirmedMetadata, setConfirmedMetadata] = useState({
    today: request.renderer.serverToday,
    timezone: request.renderer.serverTimezone,
  });
  const context = useRef(request.renderer);
  const [changes, setChanges] = useState<PlanTodoPatchProposal["changes"]>(
    () =>
      request.changes ??
      (request.mode === "status"
        ? { status: request.todo.status }
        : request.mode === "assignee"
        ? { assignee_user_id: request.todo.assignee_user_id }
        : request.mode === "priority"
        ? { priority_id: request.todo.priority_id }
        : {}),
  );
  const [operation, setOperation] = useState<PlanDateOperation>(
    () =>
      request.dateOperation ??
      (request.mode === "calendar" && request.renderer.definition.calendar
        ? {
            kind: "calendar",
            basis: request.renderer.definition.calendar.date_basis,
            date: request.todo[request.renderer.definition.calendar.date_basis],
          }
        : planGanttInterval(request.todo)
        ? { kind: "shift", delta: 0 }
        : {
            kind: "resize",
            edge: "start",
            date: request.renderer.serverToday,
          }),
  );
  const [heldChanges, setHeldChanges] = useState<
    PlanTodoPatchProposal["changes"] | null
  >(null);
  const [locked, setLocked] = useState(false);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [messageKey, setMessageKey] = useState<string | null>(null);
  const [comparison, setComparison] = useState<Extract<
    PlanTodoCompareResult,
    { state: "ready" }
  > | null>(null);
  const [confirmedMissing, setConfirmedMissing] = useState(false);
  const autoStarted = useRef(false);
  const dialogBody = useRef<HTMLDivElement | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    dialogBody.current
      ?.querySelector<HTMLElement>(
        "select:not(:disabled), input:not(:disabled)",
      )
      ?.focus();
  }, []);
  const isDate = request.mode === "gantt" || request.mode === "calendar";
  const metadataReady =
    isPlanDate(renderer.serverToday) && !!renderer.serverTimezone;
  const validationToday =
    isPlanDate(renderer.serverToday) &&
    isPlanDate(confirmedMetadata.today) &&
    renderer.serverToday > confirmedMetadata.today
      ? renderer.serverToday
      : confirmedMetadata.today;
  const dateResult: PlanDateProposal =
    heldChanges && isDate
      ? (() => {
          const error = validatePlanDates(
            { ...baseline, ...heldChanges },
            validationToday,
            baseline.due_date,
          );
          const reason =
            error &&
            ({
              metadata: "metadata",
              invalidDate: "invalid",
              dateOrder: "range",
              pastDue: "pastDue",
            }[error] as "metadata" | "invalid" | "range" | "pastDue" | null);
          return reason
            ? { ok: false, reason }
            : { ok: true, changes: heldChanges, requiresConfirmation: false };
        })()
      : operation.kind === "shift"
      ? proposeGanttShift(baseline, operation.delta, validationToday)
      : operation.kind === "resize"
      ? proposeGanttResize(
          baseline,
          operation.edge,
          operation.date,
          validationToday,
        )
      : proposeCalendarMove(
          baseline,
          operation.basis,
          operation.date,
          validationToday,
        );
  const proposed = useMemo<PlanTodoPatchProposal["changes"]>(
    () =>
      heldChanges ??
      (isDate ? (dateResult.ok ? dateResult.changes : {}) : changes),
    [changes, dateResult, heldChanges, isDate],
  );
  const needsCatalog = "priority_id" in proposed || "tag_ids" in proposed;
  const catalogChanged =
    needsCatalog &&
    (!renderer.catalog ||
      renderer.catalog.project_id !== baseline.project_id ||
      renderer.catalog.revision !== catalogRevision);
  const invalidPriority =
    typeof proposed.priority_id === "string" &&
    proposed.priority_id !== baseline.priority_id &&
    !renderer.catalog?.priorities.some(
      (item) =>
        item.priority_id === proposed.priority_id && item.archived_at === null,
    );
  const permitted =
    renderer.canEdit(baseline) &&
    (request.mode !== "assignee" || renderer.isManager);
  const noChange = !Object.entries(proposed).some(
    ([key, value]) => value !== baseline[key as keyof ProjectTodo],
  );
  const requiresConfirmation =
    isDate &&
    dateResult.ok &&
    dateResult.requiresConfirmation &&
    !confirmedMissing;
  const invalid =
    (isDate && (!metadataReady || !dateResult.ok)) || invalidPriority;
  const title = t(
    `projects.planViews.${
      isDate
        ? "changeDates"
        : request.mode === "status"
        ? "changeStatus"
        : request.mode === "assignee"
        ? "changeAssignee"
        : "changePriority"
    }`,
    isDate
      ? "修改日期"
      : request.mode === "status"
      ? "修改状态"
      : request.mode === "assignee"
      ? "修改处理人"
      : "修改优先级",
  );
  const close = useCallback(() => {
    const trigger = request.trigger;
    onClose();
    queueMicrotask(() => trigger?.isConnected && trigger.focus());
  }, [onClose, request.trigger]);
  const save = useCallback(async () => {
    if (
      busyRef.current ||
      locked ||
      catalogChanged ||
      !permitted ||
      invalid ||
      requiresConfirmation ||
      noChange
    )
      return;
    const generation = frame.generation;
    const proposal: PlanTodoPatchProposal = {
      changes: { ...proposed },
      baseVersion: baseline.version,
      ...(needsCatalog ? { catalogRevision } : {}),
    };
    busyRef.current = true;
    setBusy(true);
    setMessageKey(null);
    setHeldChanges(proposal.changes);
    try {
      const result = await context.current.onProposeTodoPatch(
        baseline,
        proposal,
      );
      if (!alive.current || !frame.isCurrent(generation)) return;
      if (result.status === "saved") {
        close();
        return;
      }
      if (result.messageKey === "projects.todoDetail.notFound") {
        close();
        return;
      }
      setLocked(true);
      setComparison(null);
      setMessageKey(result.messageKey);
    } catch {
      if (alive.current && frame.isCurrent(generation)) {
        setLocked(true);
        setMessageKey("projects.planViews.failed");
      }
    } finally {
      if (alive.current && frame.isCurrent(generation)) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  }, [
    baseline,
    catalogChanged,
    catalogRevision,
    close,
    frame,
    invalid,
    locked,
    needsCatalog,
    noChange,
    permitted,
    proposed,
    requiresConfirmation,
  ]);
  useEffect(() => {
    if (request.autoSubmit && !autoStarted.current) {
      autoStarted.current = true;
      if (!requiresConfirmation) void save();
    }
  }, [request.autoSubmit, requiresConfirmation, save]);
  const compare = async () => {
    if (busyRef.current) return;
    const generation = frame.generation;
    busyRef.current = true;
    setBusy(true);
    setComparison(null);
    try {
      const result = await context.current.onRefreshTodoCompare(baseline);
      if (!alive.current || !frame.isCurrent(generation)) return;
      if (
        result.state === "ready" &&
        result.todo.todo_id === baseline.todo_id &&
        result.todo.project_id === baseline.project_id &&
        (!isDate || (isPlanDate(result.serverToday) && !!result.serverTimezone))
      ) {
        setComparison({
          ...result,
          todo: { ...result.todo, tag_ids: [...result.todo.tag_ids] },
        });
      } else
        setMessageKey(
          result.state === "forbidden"
            ? "projects.planViews.writeForbidden"
            : "projects.planViews.compareFailed",
        );
    } catch {
      if (alive.current && frame.isCurrent(generation))
        setMessageKey("projects.planViews.compareFailed");
    } finally {
      if (alive.current && frame.isCurrent(generation)) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  };
  const confirmCompare = () => {
    if (
      !comparison ||
      busyRef.current ||
      !renderer.canEdit(comparison.todo) ||
      (request.mode === "assignee" && !renderer.isManager)
    )
      return;
    setBaseline(comparison.todo);
    setCatalogRevision(comparison.catalogRevision);
    setConfirmedMetadata({
      today: comparison.serverToday,
      timezone: comparison.serverTimezone,
    });
    // This explicit confirmation is the only point that adopts a fresh callback scope.
    context.current = renderer;
    setComparison(null);
    setLocked(false);
    setMessageKey(null);
  };
  const changeOperation = (next: PlanDateOperation) => {
    setOperation(next);
    setHeldChanges(null);
    setConfirmedMissing(false);
  };
  const fieldForMode =
    request.mode === "status"
      ? "status"
      : request.mode === "assignee"
      ? "assignee"
      : "priority";
  const showChanges = (
    todo: ProjectTodo,
    values: PlanTodoPatchProposal["changes"],
  ) => (
    <dl className={styles.comparisonValues}>
      {Object.entries(values).map(([key, value]) => {
        const field =
          key === "assignee_user_id"
            ? "assignee"
            : key === "priority_id"
            ? "priority"
            : (key as PlanVisibleField);
        return (
          <div key={key}>
            <dt>
              <PlanFieldLabel field={field} />
            </dt>
            <dd>
              <PlanTodoFieldValue
                todo={{ ...todo, [key]: value }}
                field={field}
                catalog={renderer.catalog}
                members={renderer.members}
                serverToday={renderer.serverToday}
                serverTimezone={renderer.serverTimezone}
              />
            </dd>
          </div>
        );
      })}
    </dl>
  );
  return (
    <Modal
      open
      title={title}
      width={460}
      maskClosable={false}
      zIndex={1300}
      closable={{ "aria-label": t("projects.planViews.close", "关闭") }}
      onCancel={close}
      styles={{
        body: { maxHeight: "calc(100dvh - 230px)", overflowY: "auto" },
      }}
      footer={
        <div className={styles.actions}>
          <Button onClick={close}>
            {t("projects.planViews.cancel", "取消")}
          </Button>
          <Button
            type="primary"
            disabled={
              busy ||
              locked ||
              catalogChanged ||
              !permitted ||
              invalid ||
              requiresConfirmation ||
              noChange
            }
            onClick={() => void save()}
          >
            {t(
              busy
                ? "projects.planViews.saving"
                : "projects.planViews.saveChanges",
              busy ? "正在保存…" : "保存更改",
            )}
          </Button>
        </div>
      }
    >
      <div
        ref={dialogBody}
        className={styles.dialogBody}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            close();
          }
        }}
      >
        <p className={styles.todoName}>{baseline.title}</p>
        {!isDate && (
          <label className={styles.formField}>
            <PlanFieldLabel field={fieldForMode} />
            <select
              aria-label={t(
                `projects.planViews.fields.${fieldForMode}`,
                fieldFallbacks[fieldForMode],
              )}
              disabled={busy || !permitted}
              value={
                request.mode === "status"
                  ? changes.status
                  : request.mode === "assignee"
                  ? String(changes.assignee_user_id ?? "none")
                  : changes.priority_id ?? "none"
              }
              onChange={(event) => {
                setHeldChanges(null);
                const value = event.target.value;
                setChanges(
                  request.mode === "status"
                    ? { status: value as ProjectTodo["status"] }
                    : request.mode === "assignee"
                    ? {
                        assignee_user_id:
                          value === "none" ? null : Number(value),
                      }
                    : { priority_id: value === "none" ? null : value },
                );
              }}
            >
              {request.mode === "status" ? (
                (["todo", "in_progress", "done"] as const).map((status) => (
                  <option key={status} value={status}>
                    {t(
                      `projects.planViews.status.${status}`,
                      { todo: "待开始", in_progress: "进行中", done: "已完成" }[
                        status
                      ],
                    )}
                  </option>
                ))
              ) : (
                <>
                  <option value="none">
                    {t(
                      request.mode === "assignee"
                        ? "projects.planViews.unassigned"
                        : "projects.planViews.none",
                      request.mode === "assignee" ? "未指派" : "无",
                    )}
                  </option>
                  {request.mode === "assignee" ? (
                    renderer.members.map((member) => (
                      <option
                        key={member.user_id}
                        value={String(member.user_id)}
                      >
                        {member.username}
                      </option>
                    ))
                  ) : (
                    <>
                      {renderer.catalog?.priorities
                        .filter(
                          (item) =>
                            item.archived_at === null ||
                            item.priority_id === baseline.priority_id ||
                            item.priority_id === changes.priority_id,
                        )
                        .map((item) => (
                          <option
                            key={item.priority_id}
                            value={item.priority_id}
                            disabled={
                              item.archived_at !== null &&
                              item.priority_id !== baseline.priority_id
                            }
                          >
                            {item.archived_at === null
                              ? item.name
                              : `${item.name} · ${t(
                                  "projects.planViews.archived",
                                  "已停用",
                                )}`}
                          </option>
                        ))}
                      {changes.priority_id &&
                        !renderer.catalog?.priorities.some(
                          (item) => item.priority_id === changes.priority_id,
                        ) && (
                          <option value={changes.priority_id} disabled>
                            {t(
                              "projects.planViews.catalogUnavailable",
                              "目录信息暂不可用",
                            )}
                          </option>
                        )}
                    </>
                  )}
                </>
              )}
            </select>
          </label>
        )}
        {isDate && (
          <>
            {request.mode === "gantt" && (
              <label className={styles.formField}>
                {t("projects.planViews.dateOperation", "日期操作")}
                <select
                  aria-label={t("projects.planViews.dateOperation", "日期操作")}
                  disabled={busy || !permitted || !metadataReady}
                  value={
                    operation.kind === "shift"
                      ? "shift"
                      : operation.kind === "resize"
                      ? operation.edge
                      : "start"
                  }
                  onChange={(event) =>
                    changeOperation(
                      event.target.value === "shift"
                        ? { kind: "shift", delta: 0 }
                        : {
                            kind: "resize",
                            edge: event.target.value as "start" | "end",
                            date:
                              event.target.value === "start"
                                ? baseline.start_date ?? renderer.serverToday
                                : baseline.due_date ?? renderer.serverToday,
                          },
                    )
                  }
                >
                  <option value="shift" disabled={!planGanttInterval(baseline)}>
                    {t("projects.planViews.shiftInterval", "整体平移")}
                  </option>
                  <option value="start">
                    {t("projects.planViews.resizeStart", "调整开始日期")}
                  </option>
                  <option value="end">
                    {t("projects.planViews.resizeEnd", "调整截止日期")}
                  </option>
                </select>
              </label>
            )}
            {operation.kind === "shift" ? (
              <label className={styles.formField}>
                {t("projects.planViews.shiftDays", "平移天数")}
                <input
                  type="number"
                  step={1}
                  aria-label={t("projects.planViews.shiftDays", "平移天数")}
                  disabled={busy || !permitted || !metadataReady}
                  value={operation.delta}
                  onChange={(event) =>
                    changeOperation({
                      kind: "shift",
                      delta: Number(event.target.value),
                    })
                  }
                />
              </label>
            ) : (
              <label className={styles.formField}>
                <PlanFieldLabel
                  field={
                    operation.kind === "calendar"
                      ? operation.basis
                      : operation.edge === "start"
                      ? "start_date"
                      : "due_date"
                  }
                />
                <input
                  type="text"
                  placeholder="YYYY-MM-DD"
                  inputMode="numeric"
                  maxLength={10}
                  aria-label={t("projects.planViews.targetDate", "目标日期")}
                  disabled={busy || !permitted || !metadataReady}
                  value={operation.date ?? ""}
                  onChange={(event) =>
                    changeOperation({
                      ...operation,
                      date: event.target.value || null,
                    })
                  }
                />
              </label>
            )}
            {requiresConfirmation && (
              <label className={styles.confirmation}>
                <input
                  type="checkbox"
                  checked={confirmedMissing}
                  onChange={(event) =>
                    setConfirmedMissing(event.target.checked)
                  }
                />
                {t("projects.planViews.confirmMissingDate", "确认补全另一日期")}
              </label>
            )}
            {requiresConfirmation && (
              <p>
                {t(
                  "projects.planViews.missingDateConfirmation",
                  "此操作将首次补全另一日期，请明确确认。",
                )}
              </p>
            )}
            {invalid && (
              <p role="alert">
                {t(
                  `projects.planViews.dateErrors.${
                    !metadataReady
                      ? "metadata"
                      : dateResult.ok
                      ? "invalid"
                      : dateResult.reason
                  }`,
                  !metadataReady
                    ? "服务器日期信息暂不可用，请刷新后比较。"
                    : dateResult.ok
                    ? "请输入有效的日历日期。"
                    : {
                        metadata: "服务器日期信息暂不可用，请刷新后比较。",
                        invalid: "请输入有效的日历日期。",
                        range: "开始日期不得晚于截止日期。",
                        pastDue: "新的截止日期不得早于服务器今天。",
                        outOfBounds: "日期超出 1900 至 9999 年范围。",
                      }[dateResult.reason],
                )}
              </p>
            )}
          </>
        )}
        {!noChange && (
          <div>
            <p>{t("projects.planViews.proposedValues", "拟议更改")}</p>
            {showChanges(baseline, proposed)}
          </div>
        )}
        {invalidPriority && (
          <p role="alert">
            {t(
              "projects.planViews.archivedDropDisabled",
              "停用项不能作为新的落点。",
            )}
          </p>
        )}
        {messageKey && (
          <p role="alert">
            {t(messageKey, "更改未保存，请刷新后比较并明确确认。")}
          </p>
        )}
        {!permitted && (
          <p role="alert">
            {t(
              "projects.planViews.writeForbidden",
              "当前没有修改权限，拟议更改已保留。",
            )}
          </p>
        )}
        {(locked || catalogChanged || invalid) && (
          <Button disabled={busy} onClick={() => void compare()}>
            {t("projects.planViews.refreshCompare", "刷新后比较")}
          </Button>
        )}
        {comparison && (
          <div className={styles.comparison}>
            <p>{t("projects.planViews.currentValues", "当前服务器值")}</p>
            {showChanges(
              comparison.todo,
              Object.fromEntries(
                Object.keys(proposed).map((key) => [
                  key,
                  comparison.todo[key as keyof ProjectTodo],
                ]),
              ),
            )}
            <Button
              disabled={busy || !renderer.canEdit(comparison.todo)}
              onClick={confirmCompare}
            >
              {t("projects.planViews.confirmCompare", "确认采用当前值继续编辑")}
            </Button>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function PlanLaneFooter({
  lane,
  renderer,
}: {
  lane: PlanLaneState;
  renderer: PlanRendererProps;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.laneFooter}>
      {lane.loadingFirst && (
        <span role="status">
          {t("projects.planViews.loading", "正在加载…")}
        </span>
      )}
      {lane.errorKey && (
        <span role="alert">
          {t(
            lane.errorKey,
            t("projects.planViews.loadFailed", "加载失败，请刷新后重试。"),
          )}
        </span>
      )}
      {!lane.loadingFirst &&
        lane.queryFingerprint === null &&
        !lane.paused &&
        lane.serverCount > 0 && (
          <button
            type="button"
            className={styles.control}
            onClick={() => void renderer.onLoadFirst(lane.laneId)}
          >
            {t("projects.planViews.loadGroup", "加载本组")}
          </button>
        )}
      {lane.nextCursor && (
        <button
          type="button"
          className={styles.control}
          disabled={lane.loadingMore || lane.paused}
          onClick={() => void renderer.onLoadMore(lane.laneId)}
        >
          {t(
            lane.loadingMore
              ? "projects.planViews.loading"
              : "projects.planViews.loadMore",
            lane.loadingMore ? "正在加载…" : "加载更多",
          )}
        </button>
      )}
      {!lane.loadingFirst &&
        lane.queryFingerprint !== null &&
        lane.items.length === 0 && (
          <span>{t("projects.planViews.emptyGroup", "本组暂无待办")}</span>
        )}
    </div>
  );
}
