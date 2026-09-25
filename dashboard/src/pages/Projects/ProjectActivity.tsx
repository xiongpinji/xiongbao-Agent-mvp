/**
 * ProjectActivity — 项目详情“动态”页签 (PS-03A).
 *
 * Real `GET /projects/{id}/activity` timeline plus plain-text
 * `POST /projects/{id}/messages`:
 * - default scope 与我相关 (`related`), switchable to 成员动态 (`members`);
 *   filtering happens server-side before paging, never in the browser
 * - cursor paging with the opaque `next_cursor`; pages are deduped by
 *   `event_id` when appended
 * - request state is keyed by `projectId\u0000scope`, so switching project or
 *   scope never flashes stale rows and late responses are dropped
 * - a 404 clears the timeline and shows a truthful no-access state (the
 *   composer is hidden); `PROJECT_ACTIVITY_CURSOR_INVALID` offers a refresh
 * - message bodies are rendered as inert text; `object_id` and raw payloads
 *   are never displayed, and deleted actors fall back to localized copy
 *
 * Images, @ mentions, rich text and notifications have no storage/ACL yet,
 * so no entry point for them is rendered.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Input,
  Segmented,
  Spin,
  Tooltip,
  Typography,
} from "antd";
import { RefreshCw } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import {
  PROJECT_ACTIVITY_PAGE_SIZE,
  PROJECT_MESSAGE_MAX_LENGTH,
  projectActivityApi,
  type ProjectActivityItem,
  type ProjectActivityScope,
} from "../../api/modules/projectActivity";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import { message } from "../../utils/antdMessage";

const { Text } = Typography;

interface Props {
  projectId: string;
}

interface TimelineState {
  /** `${projectId}\u0000${scope}` this page belongs to. */
  key: string;
  items: ProjectActivityItem[];
  nextCursor: string | null;
  error: unknown;
  appendError: unknown;
  cursorExpired: boolean;
  loading: boolean;
  loadingMore: boolean;
}

const INITIAL_KEY = "\u0000initial";

const secondaryStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

function mergeUniqueEvents(
  previous: ProjectActivityItem[],
  incoming: ProjectActivityItem[],
): ProjectActivityItem[] {
  const seen = new Set(previous.map((item) => item.event_id));
  const merged = [...previous];
  for (const item of incoming) {
    if (seen.has(item.event_id)) continue;
    seen.add(item.event_id);
    merged.push(item);
  }
  return merged;
}

function isCursorInvalidError(error: unknown): boolean {
  return parseApiError(error)?.code === "PROJECT_ACTIVITY_CURSOR_INVALID";
}

interface ActivityAction {
  labelKey: string;
  fallback: string;
}

/** Translate only allowlisted event types; never interpret payload JSON. */
function activityAction(item: ProjectActivityItem): ActivityAction {
  switch (item.event_type) {
    case "project.created":
      return {
        labelKey: "projects.activity.eventProjectCreated",
        fallback: "创建了项目",
      };
    case "project.updated":
      return {
        labelKey: "projects.activity.eventProjectUpdated",
        fallback: "更新了项目",
      };
    case "project.experts_updated":
      return {
        labelKey: "projects.activity.eventExpertsUpdated",
        fallback: "更新了项目专家",
      };
    case "project.member_joined":
      return {
        labelKey: "projects.activity.eventMemberJoined",
        fallback: "加入了项目",
      };
    case "project.member_role_changed":
      return {
        labelKey: "projects.activity.eventMemberRoleChanged",
        fallback: "变更了成员角色",
      };
    case "project.member_removed":
      return {
        labelKey: "projects.activity.eventMemberRemoved",
        fallback: "移除了成员",
      };
    case "project.todo_created":
      return {
        labelKey: "projects.activity.eventTodoCreated",
        fallback: "创建了待办",
      };
    case "project.todo_updated":
      return {
        labelKey: "projects.activity.eventTodoUpdated",
        fallback: "更新了待办",
      };
    case "project.todo_deleted":
      return {
        labelKey: "projects.activity.eventTodoDeleted",
        fallback: "删除了待办",
      };
    case "project.message_created":
      return {
        labelKey: "projects.activity.eventMessageCreated",
        fallback: "发表了留言",
      };
    default:
      // The server whitelists types; generic copy keeps a future event from
      // leaking raw type names or payload fields.
      return {
        labelKey: "projects.activity.eventUnknown",
        fallback: "有新的项目动态",
      };
  }
}

export default function ProjectActivity({ projectId }: Props) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();
  const [scope, setScope] = useState<ProjectActivityScope>("related");
  const [reloadKey, setReloadKey] = useState(0);
  const [draft, setDraft] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  /** Monotonic guard so late responses never overwrite fresher results. */
  const fetchSeq = useRef(0);
  /** Synchronous re-entrancy guard for double submits. */
  const postingRef = useRef(false);
  /** A post from a previous project must not update the current composer. */
  const postSeq = useRef(0);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;
  const [timeline, setTimeline] = useState<TimelineState>(() => ({
    key: INITIAL_KEY,
    items: [],
    nextCursor: null,
    error: null,
    appendError: null,
    cursorExpired: false,
    loading: true,
    loadingMore: false,
  }));

  const stateKey = `${projectId}\u0000${scope}`;
  const reload = useCallback(() => setReloadKey((key) => key + 1), []);

  useEffect(() => {
    // A draft belongs to one project; switching projects must not let it be
    // posted to the new one.
    postSeq.current += 1;
    postingRef.current = false;
    setPosting(false);
    setDraft("");
    setFormError(null);
  }, [projectId]);

  useEffect(
    () => () => {
      postSeq.current += 1;
    },
    [],
  );

  useEffect(() => {
    const seq = ++fetchSeq.current;
    const key = stateKey;
    setTimeline({
      key,
      items: [],
      nextCursor: null,
      error: null,
      appendError: null,
      cursorExpired: false,
      loading: true,
      loadingMore: false,
    });
    projectActivityApi
      .list(projectId, {
        scope,
        limit: PROJECT_ACTIVITY_PAGE_SIZE,
        cursor: undefined,
      })
      .then((data) => {
        if (seq !== fetchSeq.current) return;
        setTimeline({
          key,
          items: Array.isArray(data?.items) ? data.items : [],
          nextCursor: data?.next_cursor ?? null,
          error: null,
          appendError: null,
          cursorExpired: false,
          loading: false,
          loadingMore: false,
        });
      })
      .catch((error: unknown) => {
        if (seq !== fetchSeq.current) return;
        setTimeline({
          key,
          items: [],
          nextCursor: null,
          error,
          appendError: null,
          cursorExpired: false,
          loading: false,
          loadingMore: false,
        });
      });
  }, [projectId, scope, reloadKey, stateKey]);

  const current = timeline.key === stateKey ? timeline : null;
  const items = current?.items ?? [];
  const loading = current?.loading ?? true;
  const loadingMore = current?.loadingMore ?? false;
  const nextCursor = current?.nextCursor ?? null;
  const error = current?.error ?? null;
  const appendError = current?.appendError ?? null;
  const cursorExpired = current?.cursorExpired ?? false;
  const notFound = error != null && isNotFoundApiError(error);

  const loadMore = async () => {
    if (!nextCursor || loading || loadingMore) return;
    const seq = ++fetchSeq.current;
    const key = stateKey;
    setTimeline((previous) =>
      previous.key === key
        ? { ...previous, loadingMore: true, appendError: null }
        : previous,
    );
    try {
      const data = await projectActivityApi.list(projectId, {
        scope,
        limit: PROJECT_ACTIVITY_PAGE_SIZE,
        cursor: nextCursor,
      });
      if (seq !== fetchSeq.current) return;
      setTimeline((previous) =>
        previous.key !== key
          ? previous
          : {
              ...previous,
              items: mergeUniqueEvents(
                previous.items,
                Array.isArray(data?.items) ? data.items : [],
              ),
              nextCursor: data?.next_cursor ?? null,
              appendError: null,
              loadingMore: false,
            },
      );
    } catch (err: unknown) {
      if (seq !== fetchSeq.current) return;
      if (isNotFoundApiError(err)) {
        setTimeline((previous) =>
          previous.key !== key
            ? previous
            : {
                ...previous,
                items: [],
                nextCursor: null,
                error: err,
                appendError: null,
                cursorExpired: false,
                loadingMore: false,
              },
        );
      } else if (isCursorInvalidError(err)) {
        setTimeline((previous) =>
          previous.key !== key
            ? previous
            : {
                ...previous,
                cursorExpired: true,
                nextCursor: null,
                loadingMore: false,
              },
        );
      } else {
        setTimeline((previous) =>
          previous.key !== key
            ? previous
            : { ...previous, appendError: err, loadingMore: false },
        );
      }
    }
  };

  const submit = async () => {
    if (postingRef.current) return;
    const body = draft.trim();
    if (!body) {
      setFormError(t("projects.activity.bodyRequired", "请输入留言内容"));
      return;
    }
    if (body.length > PROJECT_MESSAGE_MAX_LENGTH) {
      setFormError(
        t("projects.activity.bodyTooLong", "留言最多 {{max}} 个字符", {
          max: PROJECT_MESSAGE_MAX_LENGTH,
        }),
      );
      return;
    }
    const seq = ++postSeq.current;
    const submittedProjectId = projectId;
    postingRef.current = true;
    setPosting(true);
    setFormError(null);
    try {
      // Only `{body}` — the server decides author, event type and timestamp.
      await projectActivityApi.postMessage(projectId, body);
      if (
        seq !== postSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      setDraft("");
      void message.success(t("projects.activity.posted", "留言已发表"));
      reload();
    } catch (err: unknown) {
      if (
        seq !== postSeq.current ||
        submittedProjectId !== currentProjectId.current
      )
        return;
      if (isNotFoundApiError(err)) {
        // A list started before revocation may still resolve after this 404.
        // Invalidate it before publishing the no-access state.
        fetchSeq.current += 1;
        const key = stateKey;
        setTimeline((previous) =>
          previous.key !== key
            ? previous
            : {
                ...previous,
                items: [],
                nextCursor: null,
                error: err,
                appendError: null,
                cursorExpired: false,
                loading: false,
                loadingMore: false,
              },
        );
      } else {
        setFormError(
          apiErrorMessage(
            err,
            t("projects.activity.postFailed", "发表留言失败"),
            t,
          ),
        );
      }
    } finally {
      if (seq === postSeq.current) {
        postingRef.current = false;
        setPosting(false);
      }
    }
  };

  const renderItem = (item: ProjectActivityItem) => {
    const action = activityAction(item);
    const actorName =
      typeof item.actor_name === "string" && item.actor_name.trim()
        ? item.actor_name
        : null;
    const messageBody =
      typeof item.message_body === "string" ? item.message_body : null;
    return (
      <article
        key={item.event_id}
        data-testid={`project-activity-${item.event_id}`}
        style={{
          background: "var(--fn-bg-elevated, #fff)",
          border: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
          borderLeft: "3px solid var(--fn-color-primary, #d4a017)",
          borderRadius: 8,
          padding: "10px 12px",
        }}
      >
        <div
          style={{
            display: "flex",
            gap: 8,
            flexWrap: "wrap",
            alignItems: "baseline",
          }}
        >
          <span
            style={{ fontWeight: 600, fontSize: 13, wordBreak: "break-word" }}
          >
            {actorName ?? t("projects.activity.deletedActor", "已删除用户")}
          </span>
          <span style={secondaryStyle}>
            {formatServerDateTime(item.created_at, timezone)}
          </span>
        </div>
        <div
          style={{
            marginTop: 4,
            fontSize: 13,
            color: "var(--fn-text-secondary, rgba(0,0,0,0.65))",
            wordBreak: "break-word",
          }}
        >
          {t(action.labelKey, action.fallback)}
        </div>
        {item.event_type === "project.message_created" &&
          messageBody != null &&
          messageBody.length > 0 && (
            <div
              style={{
                marginTop: 6,
                fontSize: 13,
                whiteSpace: "pre-wrap",
                wordBreak: "break-word",
              }}
            >
              {messageBody}
            </div>
          )}
      </article>
    );
  };

  let body: React.ReactNode;
  if (notFound) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.activity.notFound", "项目不存在或你无权访问")}
        description={t(
          "projects.activity.notFoundHint",
          "项目动态已清除；重新加载成功前不会显示旧内容。",
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (loading && items.length === 0) {
    body = (
      <div style={{ display: "flex", justifyContent: "center", padding: 48 }}>
        <Spin />
      </div>
    );
  } else if (error != null) {
    body = (
      <EmptyState
        variant="error"
        title={t("projects.activity.loadFailed", "加载动态失败")}
        description={apiErrorMessage(
          error,
          t("projects.activity.loadFailed", "加载动态失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={reload}
      />
    );
  } else if (items.length === 0) {
    const related = scope === "related";
    body = (
      <EmptyState
        variant="empty"
        title={
          related
            ? t("projects.activity.emptyRelatedTitle", "还没有与你相关的动态")
            : t("projects.activity.emptyTitle", "还没有动态")
        }
        description={
          related
            ? t(
                "projects.activity.emptyRelatedHint",
                "你的操作、指派给你的待办和你的留言会显示在这里。",
              )
            : t(
                "projects.activity.emptyHint",
                "项目成员的操作和留言会显示在这里。",
              )
        }
      />
    );
  } else {
    body = (
      <div
        data-testid="activity-timeline"
        style={{ display: "flex", flexDirection: "column", gap: 8 }}
      >
        {items.map(renderItem)}
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
        <Segmented<ProjectActivityScope>
          value={scope}
          aria-label={t("projects.activity.scopeLabel", "动态范围")}
          options={[
            {
              label: t("projects.activity.scopeRelated", "与我相关"),
              value: "related",
            },
            {
              label: t("projects.activity.scopeMembers", "成员动态"),
              value: "members",
            },
          ]}
          onChange={(value) => setScope(value)}
        />
        <Tooltip title={t("projects.activity.refreshNamed", "刷新动态")}>
          <Button
            icon={<RefreshCw size={14} />}
            disabled={loading}
            aria-label={t("projects.activity.refreshNamed", "刷新动态")}
            onClick={reload}
          />
        </Tooltip>
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t(
            "projects.activity.safeHint",
            "动态只展示安全的项目事件与纯文本留言，成员变动对象不会公开。",
          )}
        </Text>
      </div>

      {!notFound && (
        <div style={{ marginBottom: 12 }}>
          <Input.TextArea
            rows={2}
            value={draft}
            maxLength={PROJECT_MESSAGE_MAX_LENGTH}
            disabled={posting}
            placeholder={t(
              "projects.activity.placeholder",
              "以纯文本发表留言…",
            )}
            aria-label={t("projects.activity.placeholder", "以纯文本发表留言…")}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              gap: 12,
              marginTop: 8,
              flexWrap: "wrap",
            }}
          >
            <Text type="secondary" style={{ fontSize: 12 }}>
              {t(
                "projects.activity.composerHint",
                "仅支持纯文本留言；图片、@ 提及与富文本尚未开放。",
              )}
            </Text>
            <Button
              type="primary"
              loading={posting}
              disabled={posting}
              aria-label={t("projects.activity.post", "发表")}
              onClick={() => void submit()}
            >
              {t("projects.activity.post", "发表")}
            </Button>
          </div>
          {formError != null && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 8 }}
              message={formError}
            />
          )}
        </div>
      )}

      {cursorExpired && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={t(
            "projects.activity.cursorExpired",
            "动态列表已更新，请刷新后继续浏览。",
          )}
          action={
            <Button size="small" onClick={reload}>
              {t("projects.activity.refresh", "刷新")}
            </Button>
          }
        />
      )}

      {appendError != null && !cursorExpired && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 12 }}
          message={apiErrorMessage(
            appendError,
            t("projects.activity.loadFailed", "加载动态失败"),
            t,
          )}
          action={
            <Button size="small" onClick={() => void loadMore()}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      )}

      {body}

      {nextCursor != null &&
        nextCursor !== "" &&
        items.length > 0 &&
        error == null &&
        !cursorExpired && (
          <div style={{ textAlign: "center", marginTop: 12 }}>
            <Button
              loading={loadingMore}
              disabled={loading}
              onClick={() => void loadMore()}
            >
              {t("projects.activity.loadMore", "加载更多")}
            </Button>
          </div>
        )}
    </div>
  );
}
