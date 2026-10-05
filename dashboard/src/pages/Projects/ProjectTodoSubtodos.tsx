import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Popconfirm, Select, Spin, Tag } from "antd";
import {
  projectTodosApi,
  type ProjectTodo,
  type ProjectTodoCreateChildBody,
  type ProjectTodoChildrenResponse,
  type ProjectTodoStatus,
} from "../../api/modules/projectTodos";
import type { ProjectRole } from "../../api/modules/projects";
import { parseApiError } from "../../utils/apiError";
import { isTodoAccessLost } from "./TodoCatalogManager";
import { validTodoSnapshot } from "./plan/todoSnapshot";
import styles from "./ProjectTodoSubtodos.module.less";

const STATUS_VALUES: ProjectTodoStatus[] = ["todo", "in_progress", "done"];
const STATUS_FALLBACKS: Record<ProjectTodoStatus, string> = {
  todo: "待处理",
  in_progress: "进行中",
  done: "已完成",
};

interface Props {
  projectId: string;
  parent: ProjectTodo;
  role: ProjectRole;
  currentUserId: number;
  onOpenChild(todo: ProjectTodo): void;
  onParentRefresh(): Promise<ProjectTodo>;
  onAccessLost(): void;
}

interface ChildIntent {
  body: ProjectTodoCreateChildBody;
  state: "submitting" | "unknown";
}

// Keep only identities when a detail is closed: private draft payloads stay in
// the mounted detail, and an unknown commit cannot become a fresh submission.
const unresolvedCreates = new Set<string>();
const SAFE_REASONS = new Set([
  "invalid_cursor",
  "query_changed",
  "child_result_invalidated",
  "idempotency_conflict",
  "children_revision_conflict",
  "children_confirmation_required",
  "children_active_limit",
  "children_retained_limit",
]);

function validCounts(value: {
  children_revision: number;
  parent_display_revision: number;
  active_count: number;
  done_count: number;
}) {
  return (
    Number.isSafeInteger(value.children_revision) &&
    value.children_revision > 0 &&
    Number.isSafeInteger(value.parent_display_revision) &&
    value.parent_display_revision > 0 &&
    Number.isSafeInteger(value.active_count) &&
    value.active_count >= 0 &&
    value.active_count <= 100 &&
    Number.isSafeInteger(value.done_count) &&
    value.done_count >= 0 &&
    value.done_count <= value.active_count
  );
}

function validPage(parent: ProjectTodo, page: ProjectTodoChildrenResponse) {
  return (
    validCounts(page) &&
    page.limit === 50 &&
    page.children_revision === parent.children_revision &&
    page.parent_display_revision === parent.display_revision &&
    page.active_count === parent.children_count &&
    page.done_count === parent.done_children_count &&
    typeof page.has_more === "boolean" &&
    (page.has_more
      ? typeof page.next_cursor === "string" && !!page.next_cursor
      : page.next_cursor === null) &&
    Array.isArray(page.items) &&
    page.items.length <= page.limit &&
    (!page.has_more || page.items.length === page.limit) &&
    page.items.every((child) => validChild(parent, child)) &&
    new Set(page.items.map((child) => child.todo_id)).size === page.items.length
  );
}

function uuidV4(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(
    12,
    16,
  )}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function canDeleteChild(
  todo: ProjectTodo,
  role: ProjectRole,
  currentUserId: number,
) {
  return (
    role === "owner" ||
    role === "admin" ||
    todo.creator_user_id === currentUserId
  );
}

function validChild(parent: ProjectTodo, child: ProjectTodo) {
  return (
    validTodoSnapshot(child, parent.project_id) &&
    child.parent_todo_id === parent.todo_id &&
    child.children_count === 0 &&
    child.done_children_count === 0 &&
    child.children_revision === null
  );
}

export default function ProjectTodoSubtodos({
  projectId,
  parent,
  role,
  currentUserId,
  onOpenChild,
  onParentRefresh,
  onAccessLost,
}: Props) {
  const { t } = useTranslation();
  const safeError = (error: unknown, fallback: string) => {
    const reason = parseApiError(error)?.details?.reason;
    return typeof reason === "string" && SAFE_REASONS.has(reason)
      ? t(`projects.subtodos.errors.${reason}`, fallback)
      : fallback;
  };
  const scopeKey = JSON.stringify([currentUserId, projectId, parent.todo_id]);
  const [expanded, setExpanded] = useState(false);
  const [children, setChildren] = useState<ProjectTodo[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busyChild, setBusyChild] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [status, setStatus] = useState<ProjectTodoStatus>("todo");
  const [intent, setIntent] = useState<ChildIntent | null>(null);
  const lifetime = useRef({ key: scopeKey, generation: 0, active: true });
  if (lifetime.current.key !== scopeKey)
    lifetime.current = {
      key: scopeKey,
      generation: lifetime.current.generation + 1,
      active: true,
    };
  const pageSeq = useRef(0);
  const mutationBusy = useRef(false);
  const intentRef = useRef<ChildIntent | null>(null);
  const parentRef = useRef(parent);
  parentRef.current = parent;
  const pageBinding = useRef<string | null>(null);
  const binding = (snapshot: ProjectTodo) =>
    JSON.stringify([snapshot.children_revision, snapshot.display_revision]);
  const capture = () => ({ ...lifetime.current });
  const current = (run: ReturnType<typeof capture>) =>
    lifetime.current.active &&
    run.key === lifetime.current.key &&
    run.generation === lifetime.current.generation;

  useLayoutEffect(() => {
    lifetime.current.active = true;
    pageSeq.current += 1;
    pageBinding.current = null;
    mutationBusy.current = false;
    intentRef.current = null;
    setChildren([]);
    setCursor(null);
    setHasMore(false);
    setLoading(false);
    setBusyChild(null);
    setError(null);
    setTitle("");
    setDescription("");
    setStatus("todo");
    setIntent(null);
    setExpanded(false);
    return () => {
      lifetime.current.active = false;
      lifetime.current.generation += 1;
      pageSeq.current += 1;
      intentRef.current = null;
    };
  }, [scopeKey]);

  const loadChildren = async (
    mode: "first" | "more" = "first",
    snapshot = parentRef.current,
  ) => {
    if (snapshot.parent_todo_id !== null) return;
    const run = capture();
    const request = ++pageSeq.current;
    const previous = mode === "more" ? children : [];
    if (
      mode === "more" &&
      (!cursor || pageBinding.current !== binding(snapshot))
    )
      return;
    setLoading(true);
    setError(null);
    try {
      const page = await projectTodosApi.listChildren(
        projectId,
        snapshot.todo_id,
        {
          limit: 50,
          ...(mode === "more" && cursor ? { cursor } : {}),
        },
      );
      if (!current(run) || request !== pageSeq.current) return;
      const combined = [...previous, ...page.items];
      if (
        !validPage(snapshot, page) ||
        snapshot.display_revision < parentRef.current.display_revision ||
        (mode === "more" && pageBinding.current !== binding(snapshot)) ||
        new Set([...previous, ...page.items].map((child) => child.todo_id))
          .size !==
          previous.length + page.items.length ||
        combined.some((child, index) => {
          const before = combined[index - 1];
          return (
            before &&
            (before.created_at > child.created_at ||
              (before.created_at === child.created_at &&
                before.todo_id >= child.todo_id))
          );
        }) ||
        (!page.has_more &&
          combined.filter((child) => child.status === "done").length !==
            page.done_count) ||
        (page.has_more
          ? previous.length + page.items.length >= page.active_count
          : previous.length + page.items.length !== page.active_count)
      )
        throw new Error("Invalid subtodo response");
      pageBinding.current = binding(snapshot);
      setChildren(combined);
      setCursor(page.next_cursor);
      setHasMore(page.has_more);
    } catch (reason) {
      if (!current(run) || request !== pageSeq.current) return;
      if (isTodoAccessLost(reason)) onAccessLost();
      else
        setError(
          safeError(
            reason,
            t("projects.subtodos.loadFailed", "加载子待办失败"),
          ),
        );
    } finally {
      if (current(run) && request === pageSeq.current) setLoading(false);
    }
  };

  const submitChild = async () => {
    if (
      parent.parent_todo_id !== null ||
      mutationBusy.current ||
      (!intentRef.current && unresolvedCreates.has(scopeKey))
    )
      return;
    if (parent.children_revision === null) {
      setError(t("projects.subtodos.createFailed", "创建子待办失败"));
      return;
    }
    const body =
      intentRef.current?.body ??
      ({
        title: title.trim(),
        description,
        status,
        expected_children_revision: parent.children_revision,
        client_request_id: uuidV4(),
      } satisfies ProjectTodoCreateChildBody);
    if (!body.title) return;
    const run = capture();
    mutationBusy.current = true;
    unresolvedCreates.add(scopeKey);
    intentRef.current = { body, state: "submitting" };
    setIntent(intentRef.current);
    setError(null);
    try {
      const response = await projectTodosApi.createChild(
        projectId,
        parent.todo_id,
        body,
      );
      if (
        !validChild(parent, response.item) ||
        !validCounts(response) ||
        typeof response.replayed !== "boolean"
      )
        throw new Error("Invalid subtodo create response");
      unresolvedCreates.delete(scopeKey);
      if (!current(run)) return;
      // A valid receipt resolves creation even if the following parent GET fails.
      intentRef.current = null;
      setIntent(null);
      setTitle("");
      setDescription("");
      setStatus("todo");
      setExpanded(true);
      pageBinding.current = null;
      setChildren([]);
      try {
        const latest = await onParentRefresh();
        if (!current(run)) return;
        if (
          !validTodoSnapshot(latest, projectId, parent.todo_id) ||
          latest.parent_todo_id !== null
        )
          throw new Error("Invalid parent snapshot");
        await loadChildren("first", latest);
      } catch (reason) {
        if (!current(run)) return;
        if (isTodoAccessLost(reason)) onAccessLost();
        else
          setError(
            safeError(
              reason,
              t("projects.subtodos.loadFailed", "加载子待办失败"),
            ),
          );
      }
    } catch (reason) {
      if (!current(run)) return;
      if (isTodoAccessLost(reason)) {
        intentRef.current = null;
        setIntent(null);
        onAccessLost();
      } else {
        intentRef.current = { body, state: "unknown" };
        setIntent(intentRef.current);
        setError(
          safeError(
            reason,
            t("projects.subtodos.createFailed", "创建子待办失败"),
          ),
        );
      }
    } finally {
      if (current(run)) mutationBusy.current = false;
    }
  };

  const deleteChild = async (child: ProjectTodo) => {
    if (!canDeleteChild(child, role, currentUserId) || mutationBusy.current)
      return;
    const run = capture();
    mutationBusy.current = true;
    setBusyChild(child.todo_id);
    setError(null);
    try {
      await projectTodosApi.remove(projectId, child.todo_id, child.version);
      if (!current(run)) return;
      const latest = await onParentRefresh();
      if (!current(run)) return;
      if (
        !validTodoSnapshot(latest, projectId, parent.todo_id) ||
        latest.parent_todo_id !== null
      )
        throw new Error("Invalid parent snapshot");
      await loadChildren("first", latest);
    } catch (reason) {
      if (!current(run)) return;
      if (isTodoAccessLost(reason)) onAccessLost();
      else
        setError(
          safeError(
            reason,
            t("projects.subtodos.deleteFailed", "删除子待办失败"),
          ),
        );
    } finally {
      if (current(run)) {
        mutationBusy.current = false;
        setBusyChild(null);
      }
    }
  };

  if (parent.parent_todo_id !== null) return null;
  const blocked = !intent && unresolvedCreates.has(scopeKey);
  const formDisabled = intent !== null || blocked || busyChild !== null;
  const pageCurrent = pageBinding.current === binding(parent);
  const visibleChildren = pageCurrent ? children : [];
  return (
    <section
      className={styles.subtodos}
      aria-label={t("projects.subtodos.title", "子待办")}
    >
      <div className={styles.header}>
        <div>
          <h3>{t("projects.subtodos.title", "子待办")}</h3>
          <p>
            {t("projects.subtodos.countSummary", "{{done}}/{{total}} 已完成", {
              done: parent.done_children_count,
              total: parent.children_count,
            })}
          </p>
        </div>
        <Button
          onClick={() => {
            const next = !expanded;
            setExpanded(next);
            if (next) void loadChildren("first");
          }}
        >
          {expanded
            ? t("projects.subtodos.collapse", "收起子待办")
            : t("projects.subtodos.expand", "展开子待办")}
        </Button>
      </div>
      {expanded && (
        <div className={styles.body}>
          {error && <Alert type="warning" showIcon message={error} />}
          {!pageCurrent && !loading && (
            <Button onClick={() => void loadChildren("first")}>
              {t("projects.subtodos.refresh", "刷新子待办")}
            </Button>
          )}
          {loading && visibleChildren.length === 0 ? (
            <Spin />
          ) : visibleChildren.length === 0 ? (
            <p className={styles.muted}>
              {t("projects.subtodos.empty", "暂无子待办")}
            </p>
          ) : (
            <ul className={styles.list}>
              {visibleChildren.map((child) => (
                <li key={child.todo_id} className={styles.item}>
                  <button
                    type="button"
                    className={styles.openChild}
                    onClick={() => onOpenChild(child)}
                  >
                    {child.title}
                  </button>
                  <Tag>
                    {t(
                      `projects.plan.status.${child.status}`,
                      STATUS_FALLBACKS[child.status],
                    )}
                  </Tag>
                  <Popconfirm
                    title={t(
                      "projects.subtodos.deleteConfirm",
                      "删除这个子待办？",
                    )}
                    okText={t("common.delete", "删除")}
                    cancelText={t("common.cancel", "取消")}
                    disabled={!canDeleteChild(child, role, currentUserId)}
                    onConfirm={() => void deleteChild(child)}
                  >
                    <Button
                      danger
                      size="small"
                      loading={busyChild === child.todo_id}
                      disabled={
                        !canDeleteChild(child, role, currentUserId) ||
                        intent !== null ||
                        busyChild !== null
                      }
                    >
                      {t("projects.subtodos.delete", "删除子待办")}
                    </Button>
                  </Popconfirm>
                </li>
              ))}
            </ul>
          )}
          {pageCurrent && hasMore && (
            <Button loading={loading} onClick={() => void loadChildren("more")}>
              {t("projects.subtodos.loadMore", "加载更多子待办")}
            </Button>
          )}
          <div className={styles.createBox}>
            <h4>{t("projects.subtodos.createTitle", "创建子待办")}</h4>
            {(intent?.state === "unknown" || blocked) && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  blocked
                    ? "projects.subtodos.unresolvedAfterExit"
                    : "projects.subtodos.unknownSubmit",
                  blocked
                    ? "此前创建结果尚未确认，已阻止新的创建请求。"
                    : "上一次请求可能仍在提交；重试会使用同一个请求 ID。",
                )}
              />
            )}
            <Input
              value={title}
              maxLength={200}
              disabled={formDisabled}
              placeholder={t(
                "projects.subtodos.titlePlaceholder",
                "子待办标题",
              )}
              onChange={(event) => setTitle(event.target.value)}
            />
            <Input.TextArea
              value={description}
              maxLength={4000}
              rows={2}
              disabled={formDisabled}
              placeholder={t("projects.subtodos.descriptionLabel", "描述")}
              onChange={(event) => setDescription(event.target.value)}
            />
            <Select<ProjectTodoStatus>
              value={status}
              disabled={formDisabled}
              options={STATUS_VALUES.map((value) => ({
                value,
                label: t(
                  `projects.plan.status.${value}`,
                  STATUS_FALLBACKS[value],
                ),
              }))}
              onChange={setStatus}
            />
            <Button
              type="primary"
              loading={intent?.state === "submitting"}
              disabled={
                blocked || busyChild !== null || (!intent && !title.trim())
              }
              onClick={() => void submitChild()}
            >
              {intent?.state === "unknown"
                ? t("projects.subtodos.retryUnknown", "重试待确认请求")
                : t("projects.subtodos.create", "创建子待办")}
            </Button>
          </div>
        </div>
      )}
    </section>
  );
}
