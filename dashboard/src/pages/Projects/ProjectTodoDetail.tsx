import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Select, Spin, Tag } from "antd";
import { X } from "lucide-react";
import {
  PROJECT_TODO_COMMENTS_PAGE_SIZE,
  projectTodosApi,
  type ProjectTodo,
  type ProjectTodoComment,
  type ProjectTodoStatus,
  type ProjectTodoUpdateBody,
} from "../../api/modules/projectTodos";
import type { ProjectMember, ProjectRole } from "../../api/modules/projects";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import ProjectTodoMarkdown from "./ProjectTodoMarkdown";
import ProjectTodoSubtodos from "./ProjectTodoSubtodos";
import styles from "./ProjectTodoDetail.module.less";
import TodoFields, {
  todoFieldsEqual,
  type TodoFieldValues,
} from "./TodoFields";
import { useTodoCatalog } from "./useTodoCatalog";
import { validatePlanDates } from "./planDates";
import {
  catalogErrorMessage,
  isTodoAccessLost as isNotFoundApiError,
} from "./TodoCatalogManager";
import {
  projectTodoCatalogApi,
  type ProjectTodoCatalog,
} from "../../api/modules/projectTodoCatalog";
import { compareTodoSnapshot, validTodoSnapshot } from "./plan/todoSnapshot";

interface Props {
  projectId: string;
  todoId: string;
  role: ProjectRole;
  members: ProjectMember[];
  currentUserId: number | null;
  onClose: () => void;
  onChanged: (todo: ProjectTodo, catalog?: ProjectTodoCatalog) => void;
  onAccessLost: () => void;
  onOpenChild: (todo: ProjectTodo) => void;
}

interface DetailState {
  key: string;
  loading: boolean;
  todo: ProjectTodo | null;
  comments: ProjectTodoComment[];
  nextCursor: string | null;
  error: unknown;
  notFound: boolean;
  snapshotCatalog?: ProjectTodoCatalog;
}

const STATUS_VALUES: ProjectTodoStatus[] = ["todo", "in_progress", "done"];
const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const MAX_COMMENT_IMAGES = 5;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const MAX_COMMENT_IMAGE_BYTES = 20 * 1024 * 1024;
const EMPTY_FILES: File[] = [];
const EMPTY_URLS: string[] = [];
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

function PrivateCommentImage({
  projectId,
  todoId,
  commentId,
  image,
  reloadKey,
  onAccessLost,
}: {
  projectId: string;
  todoId: string;
  commentId: string;
  image: ProjectTodoComment["images"][number];
  reloadKey: number;
  onAccessLost: () => void;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const onAccessLostRef = useRef(onAccessLost);
  onAccessLostRef.current = onAccessLost;

  useEffect(() => {
    setUrl(null);
    setError(false);
    if (!IMAGE_TYPES.has(image.media_type)) {
      setError(true);
      return;
    }
    const controller = new AbortController();
    let active = true;
    let ownedUrl: string | null = null;
    void projectTodosApi
      .readCommentImage(projectId, todoId, commentId, image.image_id, {
        signal: controller.signal,
      })
      .then((blob) => {
        if (!active || controller.signal.aborted) return;
        if (blob.type && blob.type !== image.media_type) {
          setError(true);
          return;
        }
        ownedUrl = URL.createObjectURL(blob);
        setUrl(ownedUrl);
      })
      .catch((reason: unknown) => {
        if (!active || controller.signal.aborted) return;
        if (isNotFoundApiError(reason)) onAccessLostRef.current();
        else setError(true);
      });
    return () => {
      active = false;
      controller.abort();
      if (ownedUrl) URL.revokeObjectURL(ownedUrl);
    };
  }, [
    projectId,
    todoId,
    commentId,
    image.image_id,
    image.media_type,
    reloadKey,
    retry,
  ]);

  if (error) {
    return (
      <div className={styles.commentImageError}>
        {t("projects.todoDetail.imageLoadFailed", "图片加载失败")}
        <Button size="small" onClick={() => setRetry((value) => value + 1)}>
          {t("common.retry", "重试")}
        </Button>
      </div>
    );
  }
  if (!url) {
    return (
      <span className={styles.muted}>{t("common.loading", "加载中…")}</span>
    );
  }
  return (
    <img
      className={styles.commentImage}
      src={url}
      alt={t("projects.todoDetail.commentImage", "评论图片 {{number}}", {
        number: image.position + 1,
      })}
    />
  );
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
  onOpenChild,
}: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const key = JSON.stringify([currentUserId, projectId, todoId]);
  const mounted = useRef(true);
  const catalogAccessLost = useRef<() => void>(() => {});
  const [fieldDraft, setFieldDraft] = useState<{
    key: string;
    values: TodoFieldValues;
  } | null>(null);
  const [comparedVersion, setComparedVersion] = useState<number | null>(null);
  const [reloadRequest, setReloadRequest] = useState({
    key: "",
    sequence: 0,
    compare: false,
  });
  const reloadKey = reloadRequest.sequence;
  const reloadDetail = (compare = false) =>
    setReloadRequest((previous) => ({
      key,
      sequence: previous.sequence + 1,
      compare,
    }));
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
  const [draftImageState, setDraftImageState] = useState<{
    key: string;
    files: File[];
  }>({ key, files: [] });
  const [draftPreviewState, setDraftPreviewState] = useState<{
    key: string;
    urls: string[];
  }>({ key, urls: [] });
  const draftImages =
    draftImageState.key === key ? draftImageState.files : EMPTY_FILES;
  const draftPreviews =
    draftPreviewState.key === key ? draftPreviewState.urls : EMPTY_URLS;
  const setDraftImages = (next: File[] | ((previous: File[]) => File[])) => {
    setDraftImageState((previous) => {
      const files = previous.key === key ? previous.files : EMPTY_FILES;
      return { key, files: typeof next === "function" ? next(files) : next };
    });
  };
  const [draftError, setDraftError] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const conflictRef = useRef(conflict);
  conflictRef.current = conflict;
  const [editingDescription, setEditingDescription] = useState(false);
  const [descriptionDraft, setDescriptionDraft] = useState("");
  const [descriptionPreview, setDescriptionPreview] = useState(false);
  const [posting, setPosting] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [fieldBusy, setFieldBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const draftRequestId = useRef<string | null>(null);
  const postBusy = useRef(false);
  const postAbort = useRef<AbortController | null>(null);
  const postSeq = useRef(0);
  const fieldSeq = useRef(0);
  const childSeq = useRef(0);
  const fetchSeq = useRef(0);
  const moreSeq = useRef(0);
  const currentKey = useRef(key);
  currentKey.current = key;
  const acceptedSnapshot = useRef<{ key: string; todo: ProjectTodo | null }>({
    key,
    todo: null,
  });
  if (acceptedSnapshot.current.key !== key)
    acceptedSnapshot.current = { key, todo: null };
  const snapshotControllers = useRef(new Map<string, AbortController>());
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const descriptionTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const descriptionCursor = useRef<number | null>(null);
  const onChangedRef = useRef(onChanged);
  const onAccessLostRef = useRef(onAccessLost);
  onChangedRef.current = onChanged;
  onAccessLostRef.current = onAccessLost;

  const current = state.key === key ? state : null;
  const fieldCompared =
    comparedVersion !== null && current?.todo?.version === comparedVersion;
  const baseCatalogState = useTodoCatalog(
    projectId,
    currentUserId,
    () => catalogAccessLost.current(),
    current?.todo &&
      current.snapshotCatalog?.revision !== current.todo.catalog_revision
      ? [current.todo.catalog_revision]
      : [],
  );
  const catalogState = {
    ...baseCatalogState,
    catalog:
      current?.snapshotCatalog &&
      current.snapshotCatalog.revision >=
        (baseCatalogState.catalog?.revision ?? 0)
        ? current.snapshotCatalog
        : baseCatalogState.catalog,
  };

  const resolveSnapshot = useCallback(
    async (incoming: ProjectTodo, valid: () => boolean, channel: string) => {
      if (!valid() || !validTodoSnapshot(incoming, projectId, todoId))
        throw new Error("Invalid todo snapshot");
      let known = acceptedSnapshot.current.todo;
      let order = compareTodoSnapshot(known, incoming);
      if (order === "invalid") throw new Error("Invalid todo snapshot");
      let latest = incoming;
      let catalog: ProjectTodoCatalog | undefined;
      if (order === "incomparable") {
        const controller = new AbortController();
        snapshotControllers.current.get(channel)?.abort();
        snapshotControllers.current.set(channel, controller);
        try {
          [latest, catalog] = await Promise.all([
            projectTodosApi.get(projectId, todoId, {
              signal: controller.signal,
            }),
            projectTodoCatalogApi.get(projectId, { signal: controller.signal }),
          ]);
          if (!valid()) throw new Error("Stale todo snapshot refresh");
          known = acceptedSnapshot.current.todo;
          if (
            !validTodoSnapshot(latest, projectId, todoId) ||
            catalog.project_id !== projectId ||
            !Number.isSafeInteger(catalog.revision) ||
            catalog.revision < 1 ||
            latest.catalog_revision !== catalog.revision ||
            latest.display_revision <
              Math.max(
                incoming.display_revision,
                known?.display_revision ?? 0,
              ) ||
            latest.catalog_revision <
              Math.max(incoming.catalog_revision, known?.catalog_revision ?? 0)
          )
            throw new Error("Incompatible todo snapshot refresh");
          order = compareTodoSnapshot(known, latest);
        } finally {
          controller.abort();
          if (snapshotControllers.current.get(channel) === controller)
            snapshotControllers.current.delete(channel);
        }
      }
      if (!valid() || order === "invalid" || order === "incomparable")
        throw new Error("Incompatible todo snapshot refresh");
      const todo = order === "keep" ? known! : latest;
      acceptedSnapshot.current = { key, todo };
      return { todo, catalog };
    },
    [key, projectId, todoId],
  );

  useLayoutEffect(() => {
    closeRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    for (const controller of snapshotControllers.current.values())
      controller.abort();
    snapshotControllers.current.clear();
    postAbort.current?.abort();
    postAbort.current = null;
    postSeq.current += 1;
    fieldSeq.current += 1;
    childSeq.current += 1;
    moreSeq.current += 1;
    draftRequestId.current = null;
    postBusy.current = false;
    setDraft("");
    setDraftImageState({ key, files: [] });
    setDraftError(null);
    setFieldError(null);
    setFieldDraft(null);
    setComparedVersion(null);
    setConflict(false);
    setEditingDescription(false);
    setDescriptionDraft("");
    setDescriptionPreview(false);
    descriptionCursor.current = null;
    setPosting(false);
    setUploadProgress(null);
    setFieldBusy(false);
    setLoadingMore(false);
  }, [key]);

  useLayoutEffect(() => {
    const controllers = snapshotControllers.current;
    mounted.current = true;
    return () => {
      mounted.current = false;
      for (const controller of controllers.values()) controller.abort();
      controllers.clear();
      postAbort.current?.abort();
      fetchSeq.current += 1;
      postSeq.current += 1;
      fieldSeq.current += 1;
      childSeq.current += 1;
      moreSeq.current += 1;
    };
  }, []);

  useLayoutEffect(() => {
    const urls = draftImages.map((file) => URL.createObjectURL(file));
    setDraftPreviewState({ key, urls });
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [draftImages, key, reloadKey]);

  useLayoutEffect(() => {
    const position = descriptionCursor.current;
    const textarea = descriptionTextareaRef.current;
    if (position == null || !textarea) return;
    descriptionCursor.current = null;
    textarea.focus();
    textarea.setSelectionRange(position, position);
  }, [descriptionDraft, editingDescription, descriptionPreview]);

  useEffect(() => {
    const controllers = snapshotControllers.current;
    const seq = ++fetchSeq.current;
    moreSeq.current += 1;
    setLoadingMore(false);
    setState((previous) =>
      previous.key === key
        ? { ...previous, loading: true, error: null }
        : {
            key,
            loading: true,
            todo: null,
            comments: [],
            nextCursor: null,
            error: null,
            notFound: false,
          },
    );
    void Promise.all([
      projectTodosApi.get(projectId, todoId),
      projectTodosApi.listComments(projectId, todoId, {
        limit: PROJECT_TODO_COMMENTS_PAGE_SIZE,
      }),
    ])
      .then(async ([todo, page]) => {
        if (
          !mounted.current ||
          key !== currentKey.current ||
          seq !== fetchSeq.current
        )
          return;
        const accepted = await resolveSnapshot(
          todo,
          () =>
            mounted.current &&
            key === currentKey.current &&
            seq === fetchSeq.current,
          "read",
        );
        if (
          !mounted.current ||
          key !== currentKey.current ||
          seq !== fetchSeq.current
        )
          return;
        setState((previous) => ({
          key,
          loading: false,
          todo: accepted.todo,
          snapshotCatalog:
            accepted.catalog ??
            (previous.key === key ? previous.snapshotCatalog : undefined),
          comments: chronological(page.items),
          nextCursor: page.next_cursor,
          error: null,
          notFound: false,
        }));
        if (
          reloadRequest.key === key &&
          reloadRequest.compare &&
          conflictRef.current
        )
          setComparedVersion(accepted.todo.version);
      })
      .catch((error: unknown) => {
        if (
          !mounted.current ||
          key !== currentKey.current ||
          seq !== fetchSeq.current
        )
          return;
        const notFound = isNotFoundApiError(error);
        if (notFound) catalogAccessLost.current();
        else
          setState((previous) =>
            previous.key === key
              ? { ...previous, loading: false, error }
              : {
                  key,
                  loading: false,
                  todo: null,
                  comments: [],
                  nextCursor: null,
                  error,
                  notFound: false,
                },
          );
      });
    return () => {
      controllers.get("read")?.abort();
      controllers.delete("read");
      fetchSeq.current += 1;
      moreSeq.current += 1;
    };
  }, [
    key,
    projectId,
    todoId,
    reloadKey,
    reloadRequest.key,
    reloadRequest.compare,
    resolveSnapshot,
  ]);

  const clearPrivateState = () => {
    if (!mounted.current || key !== currentKey.current) return;
    catalogState.clear();
    acceptedSnapshot.current = { key, todo: null };
    for (const controller of snapshotControllers.current.values())
      controller.abort();
    snapshotControllers.current.clear();
    fetchSeq.current += 1;
    moreSeq.current += 1;
    postSeq.current += 1;
    postAbort.current?.abort();
    postAbort.current = null;
    fieldSeq.current += 1;
    postBusy.current = false;
    childSeq.current += 1;
    setPosting(false);
    setUploadProgress(null);
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
    setDraftImages([]);
    setDescriptionDraft("");
    setFieldDraft(null);
    setComparedVersion(null);
    setFieldError(null);
    setDraftError(null);
    setConflict(false);
    setEditingDescription(false);
    setDescriptionPreview(false);
    descriptionCursor.current = null;
    draftRequestId.current = null;
    onAccessLostRef.current();
  };
  catalogAccessLost.current = clearPrivateState;

  const saveField = async (
    field: Omit<ProjectTodoUpdateBody, "expected_version">,
  ) => {
    const todo = current?.todo;
    if (!todo || fieldBusy) return;
    const allowed =
      role === "owner" ||
      role === "admin" ||
      todo.creator_user_id === currentUserId ||
      todo.assignee_user_id === currentUserId;
    if (
      !allowed ||
      ("assignee_user_id" in field && role !== "owner" && role !== "admin")
    )
      return;
    const savesProperties = [
      "start_date",
      "due_date",
      "priority_id",
      "tag_ids",
    ].some((name) => name in field);
    const hasPendingProperties =
      fieldDraft?.key === key && !todoFieldsEqual(fieldDraft.values, todo);
    const seq = ++fieldSeq.current;
    setFieldBusy(true);
    setFieldError(null);
    try {
      const updated = await projectTodosApi.update(projectId, todoId, {
        expected_version: todo.version,
        ...field,
      });
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== fieldSeq.current
      )
        return;
      const accepted = await resolveSnapshot(
        updated,
        () =>
          mounted.current &&
          key === currentKey.current &&
          seq === fieldSeq.current,
        "write",
      );
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== fieldSeq.current
      )
        return;
      setState((previous) =>
        previous.key === key
          ? {
              ...previous,
              todo: accepted.todo,
              snapshotCatalog: accepted.catalog ?? previous.snapshotCatalog,
            }
          : previous,
      );
      if ("description" in field) setEditingDescription(false);
      if (savesProperties) {
        setFieldDraft(null);
        setConflict(false);
      } else if (!hasPendingProperties) setConflict(false);
      setComparedVersion(null);
      if (accepted.catalog)
        onChangedRef.current(accepted.todo, accepted.catalog);
      else onChangedRef.current(accepted.todo);
      if (accepted.todo.parent_todo_id) {
        const parent = await projectTodosApi.get(
          projectId,
          accepted.todo.parent_todo_id,
        );
        if (
          !mounted.current ||
          key !== currentKey.current ||
          seq !== fieldSeq.current
        )
          return;
        if (validTodoSnapshot(parent, projectId, accepted.todo.parent_todo_id))
          onChangedRef.current(parent);
        else throw new Error("Invalid parent snapshot");
      }
    } catch (error: unknown) {
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== fieldSeq.current
      )
        return;
      if (isNotFoundApiError(error)) {
        clearPrivateState();
      } else if (isConflict(error)) {
        setConflict(true);
        setComparedVersion(null);
      } else {
        if (
          error instanceof Error &&
          /\b422\b/.test(error.message) &&
          ("start_date" in field || "due_date" in field)
        )
          void catalogState.reload();
        setFieldError(
          [
            "invalid_dates",
            "invalid_priority",
            "invalid_tags",
            "no_change",
          ].includes(String(parseApiError(error)?.details?.reason))
            ? catalogErrorMessage(error, t)
            : apiErrorMessage(
                error,
                t("projects.todoDetail.saveFailed", "更新待办失败"),
                t,
              ),
        );
      }
    } finally {
      if (
        mounted.current &&
        key === currentKey.current &&
        seq === fieldSeq.current
      )
        setFieldBusy(false);
    }
  };
  const refreshParentFromSubtodos = useCallback(async () => {
    const seq = ++childSeq.current;
    const latest = await projectTodosApi.get(projectId, todoId);
    const accepted = await resolveSnapshot(
      latest,
      () =>
        mounted.current &&
        key === currentKey.current &&
        seq === childSeq.current,
      "subtodos",
    );
    if (
      !mounted.current ||
      key !== currentKey.current ||
      seq !== childSeq.current
    )
      throw new Error("Stale subtodo parent refresh");
    setState((previous) =>
      previous.key === key
        ? {
            ...previous,
            todo: accepted.todo,
            snapshotCatalog: accepted.catalog ?? previous.snapshotCatalog,
          }
        : previous,
    );
    if (accepted.catalog) onChangedRef.current(accepted.todo, accepted.catalog);
    else onChangedRef.current(accepted.todo);
    return accepted.todo;
  }, [key, projectId, resolveSnapshot, todoId]);

  const saveProperties = () => {
    const todo = current?.todo,
      values = fieldDraft?.key === key ? fieldDraft.values : null;
    if (
      !todo ||
      !values ||
      todoFieldsEqual(values, todo) ||
      (conflict && !fieldCompared)
    )
      return;
    const patch: Omit<ProjectTodoUpdateBody, "expected_version"> = {};
    if (
      values.start_date !== todo.start_date ||
      values.due_date !== todo.due_date
    ) {
      const problem = validatePlanDates(
        values,
        catalogState.loading || catalogState.error
          ? null
          : catalogState.catalog?.server_today ?? null,
        todo.due_date,
      );
      if (problem) {
        setFieldError(t(`projects.todoFields.${problem}`));
        return;
      }
      if (values.start_date !== todo.start_date)
        patch.start_date = values.start_date;
      if (values.due_date !== todo.due_date) patch.due_date = values.due_date;
    }
    if (values.priority_id !== todo.priority_id)
      patch.priority_id = values.priority_id;
    if (
      JSON.stringify([...values.tag_ids].sort()) !==
      JSON.stringify([...todo.tag_ids].sort())
    )
      patch.tag_ids = [...values.tag_ids].sort();
    if ("priority_id" in patch || "tag_ids" in patch) {
      if (!catalogState.catalog || catalogState.loading || catalogState.error) {
        setFieldError(
          t("projects.todoFields.catalogRequired", "请先加载项目目录。"),
        );
        return;
      }
      patch.expected_catalog_revision = catalogState.catalog.revision;
    }
    void saveField(patch);
  };

  const insertDescriptionMarkdown = (
    prefix: string,
    suffix: string,
    placeholder: string,
    block = false,
  ) => {
    const textarea = descriptionTextareaRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = descriptionDraft.slice(start, end);
    const newLine =
      block && start > 0 && descriptionDraft[start - 1] !== "\n" ? "\n" : "";
    const inserted = `${newLine}${prefix}${selected || placeholder}${suffix}`;
    const next =
      descriptionDraft.slice(0, start) + inserted + descriptionDraft.slice(end);
    if (next.length > 4000) {
      setFieldError(
        t("projects.todoDetail.descriptionTooLong", "描述最多 4000 个字符"),
      );
      return;
    }
    descriptionCursor.current = start + inserted.length;
    setDescriptionDraft(next);
    setFieldError(null);
  };

  const submitComment = async () => {
    if (postBusy.current || !current?.todo) return;
    const body = draft.trim();
    if ((!body && draftImages.length === 0) || body.length > 4000) {
      setDraftError(
        !body && draftImages.length === 0
          ? t("projects.todoDetail.bodyRequired", "请输入评论或粘贴图片")
          : t("projects.todoDetail.bodyTooLong", "评论最多 4000 个字符"),
      );
      return;
    }
    const requestId = draftRequestId.current ?? uuidV4();
    draftRequestId.current = requestId;
    const seq = ++postSeq.current;
    const controller = new AbortController();
    postAbort.current = controller;
    postBusy.current = true;
    setPosting(true);
    setUploadProgress(draftImages.length ? 0 : null);
    setDraftError(null);
    try {
      const created = await projectTodosApi.createComment(
        projectId,
        todoId,
        {
          body,
          client_request_id: requestId,
          ...(draftImages.length ? { images: draftImages } : {}),
        },
        { signal: controller.signal },
        draftImages.length
          ? (percent) => {
              if (key === currentKey.current && seq === postSeq.current)
                setUploadProgress(percent);
            }
          : undefined,
      );
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== postSeq.current
      )
        return;
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
      setDraftImages([]);
      draftRequestId.current = null;
      reloadDetail();
    } catch (error: unknown) {
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== postSeq.current
      )
        return;
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
      if (
        mounted.current &&
        key === currentKey.current &&
        seq === postSeq.current
      ) {
        if (postAbort.current === controller) postAbort.current = null;
        postBusy.current = false;
        setPosting(false);
        setUploadProgress(null);
      }
    }
  };

  const pasteCommentImages = (
    event: React.ClipboardEvent<HTMLTextAreaElement>,
  ) => {
    const incoming = Array.from(event.clipboardData.files);
    if (!incoming.length) return;
    event.preventDefault();
    if (incoming.some((file) => !IMAGE_TYPES.has(file.type))) {
      setDraftError(
        t(
          "projects.todoDetail.imageUnsupported",
          "仅支持 PNG、JPEG 或 WebP 图片",
        ),
      );
      return;
    }
    if (draftImages.length + incoming.length > MAX_COMMENT_IMAGES) {
      setDraftError(
        t("projects.todoDetail.imageCountLimit", "最多粘贴 5 张图片"),
      );
      return;
    }
    if (incoming.some((file) => file.size > MAX_IMAGE_BYTES)) {
      setDraftError(
        t("projects.todoDetail.imageSizeLimit", "每张图片最多 8 MiB"),
      );
      return;
    }
    const total = [...draftImages, ...incoming].reduce(
      (sum, file) => sum + file.size,
      0,
    );
    if (total > MAX_COMMENT_IMAGE_BYTES) {
      setDraftError(
        t("projects.todoDetail.imageTotalLimit", "图片合计最多 20 MiB"),
      );
      return;
    }
    setDraftImages((previous) => [...previous, ...incoming]);
    draftRequestId.current = null;
    setDraftError(null);
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
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== moreSeq.current
      )
        return;
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
      if (
        !mounted.current ||
        key !== currentKey.current ||
        seq !== moreSeq.current
      )
        return;
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
      if (
        mounted.current &&
        key === currentKey.current &&
        seq === moreSeq.current
      )
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
  const canEditDescription = canEditStatus;
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
        {!current || (current.loading && !todo) ? (
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
        ) : !todo ? (
          <div className={styles.centered}>
            <Alert
              type="error"
              message={apiErrorMessage(
                current.error,
                t("projects.todoDetail.loadFailed", "加载待办详情失败"),
                t,
              )}
              action={
                <Button onClick={() => reloadDetail()}>
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
                <div className={styles.descriptionHeading}>
                  <h3 className={styles.sectionTitle}>
                    {t("projects.plan.descriptionLabel", "描述")}
                  </h3>
                  {canEditDescription && !editingDescription && (
                    <Button
                      type="link"
                      size="small"
                      onClick={() => {
                        setDescriptionDraft(todo.description);
                        setDescriptionPreview(false);
                        setEditingDescription(true);
                      }}
                    >
                      {t("projects.todoDetail.editDescription", "编辑描述")}
                    </Button>
                  )}
                </div>
                {editingDescription ? (
                  <div className={styles.descriptionEditor}>
                    {descriptionPreview ? (
                      <ProjectTodoMarkdown
                        content={descriptionDraft}
                        className={styles.markdown}
                      />
                    ) : (
                      <>
                        <div
                          className={styles.markdownToolbar}
                          role="toolbar"
                          aria-label={t(
                            "projects.todoDetail.markdownToolbar",
                            "描述格式",
                          )}
                        >
                          {(
                            [
                              [
                                "heading",
                                t("projects.todoDetail.heading", "标题"),
                                "# ",
                                "",
                                t("projects.todoDetail.headingText", "标题"),
                                true,
                              ],
                              [
                                "bold",
                                t("projects.todoDetail.bold", "粗体"),
                                "**",
                                "**",
                                t("projects.todoDetail.boldText", "粗体文字"),
                                false,
                              ],
                              [
                                "italic",
                                t("projects.todoDetail.italic", "斜体"),
                                "*",
                                "*",
                                t("projects.todoDetail.italicText", "斜体文字"),
                                false,
                              ],
                              [
                                "list",
                                t("projects.todoDetail.list", "列表"),
                                "- ",
                                "",
                                t("projects.todoDetail.listText", "列表项"),
                                true,
                              ],
                              [
                                "numbered",
                                t("projects.todoDetail.numbered", "编号列表"),
                                "1. ",
                                "",
                                t("projects.todoDetail.listText", "列表项"),
                                true,
                              ],
                              [
                                "quote",
                                t("projects.todoDetail.quote", "引用"),
                                "> ",
                                "",
                                t("projects.todoDetail.quoteText", "引用文字"),
                                true,
                              ],
                              [
                                "code",
                                t("projects.todoDetail.code", "代码"),
                                "`",
                                "`",
                                t("projects.todoDetail.codeText", "代码"),
                                false,
                              ],
                              [
                                "link",
                                t("projects.todoDetail.link", "链接"),
                                "[",
                                "](https://)",
                                t("projects.todoDetail.linkText", "链接文字"),
                                false,
                              ],
                            ] as const
                          ).map(
                            ([
                              id,
                              label,
                              prefix,
                              suffix,
                              placeholder,
                              block,
                            ]) => (
                              <button
                                key={id}
                                type="button"
                                disabled={fieldBusy}
                                aria-label={label}
                                onClick={() =>
                                  insertDescriptionMarkdown(
                                    prefix,
                                    suffix,
                                    placeholder,
                                    block,
                                  )
                                }
                              >
                                {label}
                              </button>
                            ),
                          )}
                        </div>
                        <textarea
                          ref={descriptionTextareaRef}
                          aria-label={t(
                            "projects.todoDetail.descriptionEditor",
                            "待办描述",
                          )}
                          className={styles.descriptionTextarea}
                          value={descriptionDraft}
                          maxLength={4000}
                          rows={8}
                          disabled={fieldBusy}
                          onChange={(event) =>
                            setDescriptionDraft(event.target.value)
                          }
                        />
                      </>
                    )}
                    {conflict && (
                      <div className={styles.descriptionCompare}>
                        <strong>
                          {t(
                            "projects.todoDetail.serverDescription",
                            "服务器版本（刷新后可比较）",
                          )}
                        </strong>
                        {todo.description_format === "markdown" ? (
                          <ProjectTodoMarkdown content={todo.description} />
                        ) : (
                          <div className={styles.description}>
                            {todo.description ||
                              t(
                                "projects.todoDetail.noDescription",
                                "暂无描述",
                              )}
                          </div>
                        )}
                      </div>
                    )}
                    <div className={styles.descriptionActions}>
                      <Button
                        aria-label={
                          descriptionPreview
                            ? t("projects.todoDetail.editMarkdown", "编辑")
                            : t("projects.todoDetail.previewMarkdown", "预览")
                        }
                        onClick={() => setDescriptionPreview((value) => !value)}
                      >
                        {descriptionPreview
                          ? t("projects.todoDetail.editMarkdown", "编辑")
                          : t("projects.todoDetail.previewMarkdown", "预览")}
                      </Button>
                      <Button
                        onClick={() => setEditingDescription(false)}
                        disabled={fieldBusy}
                      >
                        {t("common.cancel", "取消")}
                      </Button>
                      <Button
                        type="primary"
                        loading={fieldBusy}
                        onClick={() =>
                          void saveField({
                            description: descriptionDraft,
                            description_format: "markdown",
                          })
                        }
                      >
                        {t("projects.todoDetail.saveDescription", "保存描述")}
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div
                    className={`${styles.description} ${
                      todo.description_format === "markdown"
                        ? styles.markdown
                        : ""
                    }`}
                    data-testid="todo-description"
                  >
                    {todo.description ? (
                      todo.description_format === "markdown" ? (
                        <ProjectTodoMarkdown content={todo.description} />
                      ) : (
                        todo.description
                      )
                    ) : (
                      t("projects.todoDetail.noDescription", "暂无描述")
                    )}
                  </div>
                )}
                {currentUserId !== null && (
                  <ProjectTodoSubtodos
                    projectId={projectId}
                    parent={todo}
                    role={role}
                    currentUserId={currentUserId}
                    onOpenChild={onOpenChild}
                    onParentRefresh={refreshParentFromSubtodos}
                    onAccessLost={clearPrivateState}
                  />
                )}
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
                        {comment.images.length > 0 && (
                          <div className={styles.commentImages}>
                            {[...comment.images]
                              .sort((a, b) => a.position - b.position)
                              .map((image) => (
                                <PrivateCommentImage
                                  key={image.image_id}
                                  projectId={projectId}
                                  todoId={todoId}
                                  commentId={comment.comment_id}
                                  image={image}
                                  reloadKey={reloadKey}
                                  onAccessLost={clearPrivateState}
                                />
                              ))}
                          </div>
                        )}
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
                  onPaste={pasteCommentImages}
                />
                {draftPreviews.length > 0 && (
                  <div className={styles.draftImages}>
                    {draftPreviews.map((url, index) => (
                      <div key={url} className={styles.draftImage}>
                        <img
                          src={url}
                          alt={t(
                            "projects.todoDetail.pendingImage",
                            "待发送图片 {{number}}",
                            {
                              number: index + 1,
                            },
                          )}
                        />
                        <Button
                          type="text"
                          aria-label={t(
                            "projects.todoDetail.removeImage",
                            "移除图片 {{number}}",
                            {
                              number: index + 1,
                            },
                          )}
                          disabled={posting}
                          onClick={() => {
                            setDraftImages((previous) =>
                              previous.filter(
                                (_, itemIndex) => itemIndex !== index,
                              ),
                            );
                            draftRequestId.current = null;
                          }}
                        >
                          {t("common.remove", "移除")}
                        </Button>
                      </div>
                    ))}
                  </div>
                )}
                {uploadProgress != null && (
                  <progress
                    aria-label={t(
                      "projects.todoDetail.uploadProgress",
                      "图片上传进度",
                    )}
                    value={uploadProgress}
                    max={100}
                  />
                )}
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
                    <Button
                      disabled={current.loading || fieldBusy}
                      onClick={() => {
                        setComparedVersion(null);
                        reloadDetail(true);
                        void catalogState.reload();
                      }}
                    >
                      {t("projects.todoFields.refreshCompare", "刷新后比较")}
                    </Button>
                  }
                />
              )}
              {fieldError && <Alert type="error" message={fieldError} />}
              {!!current.error && (
                <Alert
                  type="error"
                  message={apiErrorMessage(
                    current.error,
                    t("projects.todoDetail.loadFailed", "加载待办详情失败"),
                    t,
                  )}
                  action={
                    <Button onClick={() => reloadDetail()}>
                      {t("common.retry", "重试")}
                    </Button>
                  }
                />
              )}
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
              <TodoFields
                projectId={projectId}
                accountId={currentUserId}
                values={fieldDraft?.key === key ? fieldDraft.values : todo}
                original={todo}
                catalog={catalogState.catalog}
                loading={catalogState.loading}
                error={catalogState.error}
                onRetry={catalogState.reload}
                onCatalogChanged={catalogState.reload}
                canManage={isManager}
                disabled={fieldBusy}
                readOnly={!canEditStatus}
                onChange={(values) => setFieldDraft({ key, values })}
              />
              {canEditStatus && (
                <Button
                  type="primary"
                  loading={fieldBusy}
                  disabled={
                    fieldBusy ||
                    !fieldDraft ||
                    todoFieldsEqual(fieldDraft.values, todo) ||
                    (conflict && !fieldCompared)
                  }
                  onClick={saveProperties}
                >
                  {t("projects.todoFields.saveProperties", "保存属性")}
                </Button>
              )}
              {conflict && (
                <section className={styles.propertyComparison}>
                  {fieldCompared && (
                    <>
                      <strong>
                        {t("projects.todoFields.serverValues", "服务器当前值")}
                      </strong>
                      <TodoFields
                        projectId={projectId}
                        accountId={currentUserId}
                        values={todo}
                        catalog={catalogState.catalog}
                        canManage={false}
                        readOnly
                      />
                    </>
                  )}
                </section>
              )}
            </aside>
          </div>
        )}
      </div>
    </div>
  );
}
