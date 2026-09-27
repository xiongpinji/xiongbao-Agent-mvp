import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Select, Spin, Tag } from "antd";
import { X } from "lucide-react";
import {
  PROJECT_TODO_COMMENTS_PAGE_SIZE,
  projectTodosApi,
  type ProjectTodo,
  type ProjectTodoComment,
  type ProjectTodoStatus,
} from "../../api/modules/projectTodos";
import type { ProjectMember, ProjectRole } from "../../api/modules/projects";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import styles from "./ProjectTodoDetail.module.less";

interface Props {
  projectId: string;
  todoId: string;
  role: ProjectRole;
  members: ProjectMember[];
  currentUserId: number | null;
  onClose: () => void;
  onChanged: (todo: ProjectTodo) => void;
  onAccessLost: () => void;
}

interface DetailState {
  key: string;
  loading: boolean;
  todo: ProjectTodo | null;
  comments: ProjectTodoComment[];
  nextCursor: string | null;
  error: unknown;
  notFound: boolean;
}

const STATUS_VALUES: ProjectTodoStatus[] = ["todo", "in_progress", "done"];
const STATUS_FALLBACKS: Record<ProjectTodoStatus, string> = {
  todo: "待处理",
  in_progress: "进行中",
  done: "已完成",
};

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

function chronological(items: ProjectTodoComment[]): ProjectTodoComment[] {
  return [...items].sort(
    (a, b) =>
      a.created_at - b.created_at || a.comment_id.localeCompare(b.comment_id),
  );
}

function isConflict(error: unknown): boolean {
  const parsed = parseApiError(error);
  if (parsed?.code === "CONFLICT" || parsed?.code === "VERSION_CONFLICT")
    return true;
  return error instanceof Error && /\b409\b/.test(error.message);
}

export default function ProjectTodoDetail({
  projectId,
  todoId,
  role,
  members,
  currentUserId,
  onClose,
  onChanged,
  onAccessLost,
}: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const key = `${projectId}\u0000${todoId}`;
  const [reloadKey, setReloadKey] = useState(0);
  const [state, setState] = useState<DetailState>({
    key: "",
    loading: true,
    todo: null,
    comments: [],
    nextCursor: null,
    error: null,
    notFound: false,
  });
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [posting, setPosting] = useState(false);
  const [fieldBusy, setFieldBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const draftRequestId = useRef<string | null>(null);
  const postBusy = useRef(false);
  const postSeq = useRef(0);
  const fieldSeq = useRef(0);
  const fetchSeq = useRef(0);
  const moreSeq = useRef(0);
  const currentKey = useRef(key);
  currentKey.current = key;
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const onChangedRef = useRef(onChanged);
  const onAccessLostRef = useRef(onAccessLost);
  onChangedRef.current = onChanged;
  onAccessLostRef.current = onAccessLost;

  const current = state.key === key ? state : null;

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    postSeq.current += 1;
    fieldSeq.current += 1;
    moreSeq.current += 1;
    draftRequestId.current = null;
    postBusy.current = false;
    setDraft("");
    setDraftError(null);
    setFieldError(null);
    setConflict(false);
    setPosting(false);
    setFieldBusy(false);
    setLoadingMore(false);
  }, [key]);

  useEffect(() => {
    const seq = ++fetchSeq.current;
    moreSeq.current += 1;
    setLoadingMore(false);
    setState({
      key,
      loading: true,
      todo: null,
      comments: [],
      nextCursor: null,
      error: null,
      notFound: false,
    });
    void Promise.all([
      projectTodosApi.get(projectId, todoId),
      projectTodosApi.listComments(projectId, todoId, {
        limit: PROJECT_TODO_COMMENTS_PAGE_SIZE,
      }),
    ])
      .then(([todo, page]) => {
        if (key !== currentKey.current || seq !== fetchSeq.current) return;
        setState({
          key,
          loading: false,
          todo,
          comments: chronological(page.items),
          nextCursor: page.next_cursor,
          error: null,
          notFound: false,
        });
      })
      .catch((error: unknown) => {
        if (key !== currentKey.current || seq !== fetchSeq.current) return;
        const notFound = isNotFoundApiError(error);
        setState({
          key,
          loading: false,
          todo: null,
          comments: [],
          nextCursor: null,
          error,
          notFound,
        });
        if (notFound) onAccessLostRef.current();
      });
    return () => {
      fetchSeq.current += 1;
      moreSeq.current += 1;
    };
  }, [key, projectId, todoId, reloadKey]);

  const clearPrivateState = () => {
    if (key !== currentKey.current) return;
    fetchSeq.current += 1;
    moreSeq.current += 1;
    postSeq.current += 1;
    fieldSeq.current += 1;
    postBusy.current = false;
    setPosting(false);
    setFieldBusy(false);
    setState({
      key,
      loading: false,
      todo: null,
      comments: [],
      nextCursor: null,
      error: null,
      notFound: true,
    });
    setDraft("");
    draftRequestId.current = null;
    onAccessLostRef.current();
  };

  const saveField = async (
    field: { status: ProjectTodoStatus } | { assignee_user_id: number | null },
  ) => {
    const todo = current?.todo;
    if (!todo || fieldBusy) return;
    const seq = ++fieldSeq.current;
    setFieldBusy(true);
    setFieldError(null);
    setConflict(false);
    try {
      const updated = await projectTodosApi.update(projectId, todoId, {
        expected_version: todo.version,
        ...field,
      });
      if (key !== currentKey.current || seq !== fieldSeq.current) return;
      setState((previous) =>
        previous.key === key ? { ...previous, todo: updated } : previous,
      );
      onChangedRef.current(updated);
    } catch (error: unknown) {
      if (key !== currentKey.current || seq !== fieldSeq.current) return;
      if (isNotFoundApiError(error)) {
        clearPrivateState();
      } else if (isConflict(error)) {
        setConflict(true);
      } else {
        setFieldError(
          apiErrorMessage(
            error,
            t("projects.todoDetail.saveFailed", "更新待办失败"),
            t,
          ),
        );
      }
    } finally {
      if (key === currentKey.current && seq === fieldSeq.current)
        setFieldBusy(false);
    }
  };

  const submitComment = async () => {
    if (postBusy.current || !current?.todo) return;
    const body = draft.trim();
    if (!body || body.length > 4000) {
      setDraftError(
        !body
          ? t("projects.todoDetail.bodyRequired", "请输入评论")
          : t("projects.todoDetail.bodyTooLong", "评论最多 4000 个字符"),
      );
      return;
    }
    const requestId = draftRequestId.current ?? uuidV4();
    draftRequestId.current = requestId;
    const seq = ++postSeq.current;
    postBusy.current = true;
    setPosting(true);
    setDraftError(null);
    try {
      const created = await projectTodosApi.createComment(projectId, todoId, {
        body,
        client_request_id: requestId,
      });
      if (key !== currentKey.current || seq !== postSeq.current) return;
      setState((previous) =>
        previous.key === key
          ? {
              ...previous,
              comments: chronological([
                ...previous.comments.filter(
                  (item) => item.comment_id !== created.comment_id,
                ),
                created,
              ]),
            }
          : previous,
      );
      setDraft("");
      draftRequestId.current = null;
      setReloadKey((value) => value + 1);
    } catch (error: unknown) {
      if (key !== currentKey.current || seq !== postSeq.current) return;
      if (isNotFoundApiError(error)) {
        clearPrivateState();
      } else {
        setDraftError(
          apiErrorMessage(
            error,
            t("projects.todoDetail.postFailed", "发表评论失败"),
            t,
          ),
        );
      }
    } finally {
      if (key === currentKey.current && seq === postSeq.current) {
        postBusy.current = false;
        setPosting(false);
      }
    }
  };

  const loadMore = async () => {
    if (!current?.nextCursor || loadingMore) return;
    const seq = ++moreSeq.current;
    setLoadingMore(true);
    setDraftError(null);
    try {
      const page = await projectTodosApi.listComments(projectId, todoId, {
        limit: PROJECT_TODO_COMMENTS_PAGE_SIZE,
        cursor: current.nextCursor,
      });
      if (key !== currentKey.current || seq !== moreSeq.current) return;
      setState((previous) => {
        if (previous.key !== key) return previous;
        const byId = new Map(
          [...previous.comments, ...page.items].map((item) => [
            item.comment_id,
            item,
          ]),
        );
        return {
          ...previous,
          comments: chronological([...byId.values()]),
          nextCursor: page.next_cursor,
        };
      });
    } catch (error: unknown) {
      if (key !== currentKey.current || seq !== moreSeq.current) return;
      if (isNotFoundApiError(error)) clearPrivateState();
      else
        setDraftError(
          apiErrorMessage(
            error,
            t("projects.todoDetail.loadCommentsFailed", "加载评论失败"),
            t,
          ),
        );
    } finally {
      if (key === currentKey.current && seq === moreSeq.current)
        setLoadingMore(false);
    }
  };

  const todo = current?.todo;
  const isManager = role === "owner" || role === "admin";
  const canEditStatus =
    todo != null &&
    (isManager ||
      (currentUserId != null &&
        (todo.creator_user_id === currentUserId ||
          todo.assignee_user_id === currentUserId)));
  const assignee = members.find(
    (member) => member.user_id === todo?.assignee_user_id,
  );

  return (
    <div className={styles.backdrop}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={t("projects.todoDetail.dialog", "待办详情")}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            event.stopPropagation();
            onClose();
          }
        }}
      >
        <div className={styles.header}>
          <span className={styles.headerLabel}>
            {t("projects.todoDetail.dialog", "待办详情")}
          </span>
          <Button
            ref={closeRef}
            type="text"
            icon={<X size={18} aria-hidden />}
            aria-label={t("projects.todoDetail.close", "关闭待办详情")}
            onClick={onClose}
          />
        </div>
        {current?.loading !== false ? (
          <div className={styles.centered}>
            <Spin />
          </div>
        ) : current.notFound ? (
          <div className={styles.centered}>
            <Alert
              type="warning"
              message={t(
                "projects.todoDetail.notFound",
                "待办不存在或你无权访问",
              )}
            />
          </div>
        ) : current.error || !todo ? (
          <div className={styles.centered}>
            <Alert
              type="error"
              message={apiErrorMessage(
                current.error,
                t("projects.todoDetail.loadFailed", "加载待办详情失败"),
                t,
              )}
              action={
                <Button onClick={() => setReloadKey((value) => value + 1)}>
                  {t("common.retry", "重试")}
                </Button>
              }
            />
          </div>
        ) : (
          <div className={styles.columns}>
            <section
              className={styles.left}
              aria-label={t("projects.todoDetail.content", "待办内容与评论")}
            >
              <div className={styles.scroller}>
                <h2 className={styles.title}>{todo.title}</h2>
                <h3 className={styles.sectionTitle}>
                  {t("projects.plan.descriptionLabel", "描述")}
                </h3>
                <div
                  className={styles.description}
                  data-testid="todo-description"
                >
                  {todo.description ||
                    t("projects.todoDetail.noDescription", "暂无描述")}
                </div>
                <h3 className={styles.sectionTitle}>
                  {t("projects.todoDetail.comments", "评论")}
                </h3>
                {current.comments.length === 0 ? (
                  <p className={styles.muted}>
                    {t("projects.todoDetail.noComments", "还没有评论")}
                  </p>
                ) : (
                  <div className={styles.comments}>
                    {current.comments.map((comment) => (
                      <article
                        key={comment.comment_id}
                        className={styles.comment}
                      >
                        <div className={styles.commentMeta}>
                          <strong>{comment.author_name}</strong>
                          <span>
                            {formatServerDateTime(comment.created_at, timezone)}
                          </span>
                        </div>
                        <div className={styles.commentBody}>{comment.body}</div>
                      </article>
                    ))}
                  </div>
                )}
                {current.nextCursor && (
                  <Button loading={loadingMore} onClick={() => void loadMore()}>
                    {t("projects.todoDetail.loadMore", "加载更多评论")}
                  </Button>
                )}
              </div>
              <div className={styles.composer}>
                <label htmlFor="project-todo-comment">
                  {t("projects.todoDetail.commentLabel", "评论")}
                </label>
                <Input.TextArea
                  id="project-todo-comment"
                  aria-label={t("projects.todoDetail.commentLabel", "评论")}
                  value={draft}
                  maxLength={4000}
                  rows={3}
                  disabled={posting}
                  onChange={(event) => {
                    setDraft(event.target.value);
                    setDraftError(null);
                    draftRequestId.current = null;
                  }}
                />
                <div className={styles.composerActions}>
                  {draftError && <Alert type="error" message={draftError} />}
                  <Button
                    type="primary"
                    loading={posting}
                    disabled={posting}
                    onClick={() => void submitComment()}
                  >
                    {t("projects.todoDetail.post", "发表评论")}
                  </Button>
                </div>
              </div>
            </section>
            <aside
              className={styles.right}
              aria-label={t("projects.todoDetail.properties", "待办属性")}
            >
              {conflict && (
                <Alert
                  type="warning"
                  message={t(
                    "projects.todoDetail.conflict",
                    "待办已被更新，请刷新后比较再保存。",
                  )}
                  action={
                    <Button onClick={() => setReloadKey((value) => value + 1)}>
                      {t("common.refresh", "刷新")}
                    </Button>
                  }
                />
              )}
              {fieldError && <Alert type="error" message={fieldError} />}
              <div className={styles.property}>
                <label htmlFor="project-todo-detail-status">
                  {t("projects.plan.statusLabel", "状态")}
                </label>
                {canEditStatus ? (
                  <Select<ProjectTodoStatus>
                    id="project-todo-detail-status"
                    value={todo.status}
                    disabled={fieldBusy}
                    options={STATUS_VALUES.map((value) => ({
                      value,
                      label: t(
                        `projects.plan.status.${value}`,
                        STATUS_FALLBACKS[value],
                      ),
                    }))}
                    onChange={(value) => void saveField({ status: value })}
                  />
                ) : (
                  <Tag>
                    {t(
                      `projects.plan.status.${todo.status}`,
                      STATUS_FALLBACKS[todo.status],
                    )}
                  </Tag>
                )}
              </div>
              <div className={styles.property}>
                <label htmlFor="project-todo-detail-assignee">
                  {t("projects.plan.assigneeLabel", "处理人")}
                </label>
                {isManager ? (
                  <Select<number | null>
                    id="project-todo-detail-assignee"
                    value={todo.assignee_user_id}
                    disabled={fieldBusy}
                    options={[
                      {
                        value: null,
                        label: t("projects.plan.unassigned", "未指派"),
                      },
                      ...members.map((member) => ({
                        value: member.user_id,
                        label: member.username,
                      })),
                    ]}
                    onChange={(value) =>
                      void saveField({ assignee_user_id: value })
                    }
                  />
                ) : (
                  <span>
                    {assignee?.username ??
                      t("projects.plan.unassigned", "未指派")}
                  </span>
                )}
              </div>
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
