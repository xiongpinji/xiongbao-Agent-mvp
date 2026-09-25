/**
 * Projects home — 项目 (human project space).
 *
 * A compact branded hero (the page h1) introduces the space, then "我的项目"
 * lists server-side search + paging results from `GET /projects`, followed by
 * the five template entries. The home owns its full-height scroll region
 * instead of nesting inside PageShell's content card, so the template entries
 * stay reachable in a short window. All rows come from the API — no fake
 * project arrays, and template clicks only prefill the create form.
 * Invitation links require an explicit user click. `teamsApi` (expert Agent
 * teams) is unrelated.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Button, Card, Input, Spin, Tag, Tooltip } from "antd";
import { Plus, RefreshCw } from "lucide-react";
import { EmptyState } from "../../components/EmptyState";
import { OCTOP_EMPTY_MASCOT_SRC } from "../../assets/mascot";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import {
  DESKTOP_DRAG_REGION_CLASS,
  DESKTOP_NO_DRAG_CLASS,
} from "../../utils/desktopChrome";
import {
  PROJECTS_PAGE_SIZE,
  projectsApi,
  type ProjectSummary,
  type ProjectRole,
} from "../../api/modules/projects";
import { projectMembershipApi } from "../../api/modules/projectMembership";
import CreateProjectModal, { TEMPLATES } from "./CreateProjectModal";
import styles from "./ProjectsPage.module.less";

const TERMINAL_INVITE_CODES = new Set([
  "INVITE_INVALID",
  "INVITE_USED",
  "INVITE_EXPIRED",
  "INVITE_REVOKED",
]);

export function projectRoleTag(role: ProjectRole): {
  labelKey: string;
  fallback: string;
  color: string;
} {
  switch (role) {
    case "owner":
      return {
        labelKey: "projects.roleOwner",
        fallback: "所有者",
        color: "gold",
      };
    case "admin":
      return {
        labelKey: "projects.roleAdmin",
        fallback: "管理员",
        color: "blue",
      };
    default:
      return {
        labelKey: "projects.roleMember",
        fallback: "成员",
        color: "default",
      };
  }
}

export default function ProjectsPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const inviteToken = searchParams.get("invite")?.trim() ?? "";
  const timezone = useServerTimezone();

  const [query, setQuery] = useState("");
  const [items, setItems] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(0);
  const [reloadKey, setReloadKey] = useState(0);
  const [createOpen, setCreateOpen] = useState(false);
  /** Create mode only: seed applied when the modal opens (null = blank form). */
  const [initialTemplateId, setInitialTemplateId] = useState<string | null>(
    null,
  );
  const [inviteBusy, setInviteBusy] = useState(false);
  const [inviteError, setInviteError] = useState<unknown>(null);
  const [invitePending, setInvitePending] = useState(false);
  /** Monotonic guard so late responses never overwrite fresher results. */
  const fetchSeq = useRef(0);

  const load = useCallback(
    async (q: string, offset: number, append: boolean) => {
      const seq = ++fetchSeq.current;
      if (append) setLoadingMore(true);
      else setLoading(true);
      try {
        const data = await projectsApi.list({
          q,
          limit: PROJECTS_PAGE_SIZE,
          offset,
        });
        if (seq !== fetchSeq.current) return;
        setItems((prev) => (append ? [...prev, ...data.items] : data.items));
        setHasMore(data.has_more);
        setNextOffset(data.offset + data.items.length);
        setError(null);
      } catch (err) {
        if (seq !== fetchSeq.current) return;
        setError(err);
      } finally {
        if (seq === fetchSeq.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [],
  );

  useEffect(() => {
    void load(query, 0, false);
  }, [query, reloadKey, load]);

  const acceptInvite = async () => {
    if (!inviteToken) return;
    setInviteBusy(true);
    setInviteError(null);
    try {
      const result = await projectMembershipApi.acceptInvite(inviteToken);
      if (result.status === "joined") {
        navigate(`/projects/${encodeURIComponent(result.project_id)}`, {
          replace: true,
        });
      } else {
        setInvitePending(true);
        navigate("/projects", { replace: true });
      }
    } catch (err) {
      setInviteError(err);
      if (TERMINAL_INVITE_CODES.has(parseApiError(err)?.code ?? "")) {
        navigate("/projects", { replace: true });
      }
    } finally {
      setInviteBusy(false);
    }
  };

  const openCreate = () => {
    setInitialTemplateId(null);
    setCreateOpen(true);
  };

  const openCreateFromTemplate = (templateId: string) => {
    setInitialTemplateId(templateId);
    setCreateOpen(true);
  };

  const refreshButton = (
    <Tooltip title={t("common.refresh", "刷新")}>
      <Button
        icon={<RefreshCw size={14} />}
        disabled={loading}
        onClick={() => setReloadKey((k) => k + 1)}
        aria-label={t("common.refresh", "刷新")}
      />
    </Tooltip>
  );

  let content: React.ReactNode;
  if (error) {
    content = (
      <EmptyState
        variant="error"
        title={t("projects.errorTitle", "项目加载失败")}
        description={apiErrorMessage(
          error,
          t("projects.loadFailed", "加载项目失败"),
          t,
        )}
        actionLabel={t("common.retry", "重试")}
        onAction={() => setReloadKey((k) => k + 1)}
      />
    );
  } else if (loading && items.length === 0) {
    content = (
      <div style={{ display: "flex", justifyContent: "center", padding: 64 }}>
        <Spin />
      </div>
    );
  } else if (items.length === 0) {
    const searching = query.trim().length > 0;
    content = (
      <EmptyState
        variant="mascot"
        title={
          searching
            ? t("projects.emptySearchTitle", "没有匹配的项目")
            : t("projects.emptyTitle", "还没有项目")
        }
        description={
          searching
            ? t("projects.emptySearchHint", "换个关键词，或创建一个新项目。")
            : t(
                "projects.emptyHint",
                "创建第一个项目，把成员、计划和资料沉淀到项目空间。",
              )
        }
        actionLabel={t("projects.newProject", "新建项目")}
        onAction={openCreate}
      />
    );
  } else {
    content = (
      <>
        <div className={styles.projectGrid}>
          {items.map((project) => {
            const role = projectRoleTag(project.my_role);
            return (
              <Link
                key={project.project_id}
                to={`/projects/${encodeURIComponent(project.project_id)}`}
                className={styles.projectLink}
                aria-label={t("projects.openProject", "打开项目：{{name}}", {
                  name: project.name,
                })}
              >
                <Card
                  hoverable
                  size="small"
                  className={styles.projectCard}
                  styles={{ body: { padding: "14px 16px" } }}
                >
                  <div className={styles.projectCardHeader}>
                    <span className={styles.projectName}>{project.name}</span>
                    <Tag color={role.color} style={{ marginInlineEnd: 0 }}>
                      {t(role.labelKey, role.fallback)}
                    </Tag>
                  </div>
                  <div className={styles.projectMeta}>
                    <span className={styles.projectUpdated}>
                      {t("projects.updatedAt", "更新于 {{time}}", {
                        time: formatServerDateTime(
                          project.updated_at,
                          timezone,
                        ),
                      })}
                    </span>
                  </div>
                </Card>
              </Link>
            );
          })}
        </div>
        {hasMore && (
          <div className={styles.loadMore}>
            <Button
              loading={loadingMore}
              onClick={() => void load(query, nextOffset, true)}
            >
              {t("projects.loadMore", "加载更多")}
            </Button>
          </div>
        )}
      </>
    );
  }

  return (
    <>
      <div className={`${DESKTOP_DRAG_REGION_CLASS} ${styles.shell}`}>
        <div className={`${DESKTOP_NO_DRAG_CLASS} ${styles.scroll}`}>
          <section
            className={styles.hero}
            aria-labelledby="projects-home-title"
          >
            <div className={styles.heroBody}>
              <h1 id="projects-home-title" className={styles.heroTitle}>
                {t("pageShell.projects.title", "项目")}
              </h1>
              <p className={styles.heroIntro}>
                {t(
                  "pageShell.projects.subtitle",
                  "人的长期协作空间：成员、计划、任务与资产按项目沉淀。",
                )}
              </p>
            </div>
            <div className={styles.heroActions}>
              {refreshButton}
              <Button
                type="primary"
                icon={<Plus size={14} />}
                onClick={openCreate}
              >
                {t("projects.newProject", "新建项目")}
              </Button>
            </div>
            <img
              className={styles.heroArt}
              src={OCTOP_EMPTY_MASCOT_SRC}
              alt=""
              draggable={false}
            />
          </section>

          {inviteToken && (
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 16 }}
              message={t("projects.join.title", "收到项目邀请")}
              description={t(
                "projects.join.hint",
                "加入后才能查看项目内容；需要审批的邀请会先提交申请。",
              )}
              action={
                <div style={{ display: "flex", gap: 8 }}>
                  <Button
                    loading={inviteBusy}
                    type="primary"
                    onClick={() => void acceptInvite()}
                  >
                    {t("projects.join.accept", "接受邀请")}
                  </Button>
                  <Button
                    disabled={inviteBusy}
                    onClick={() => {
                      setInviteError(null);
                      navigate("/projects", { replace: true });
                    }}
                  >
                    {t("projects.join.cancel", "取消")}
                  </Button>
                </div>
              }
            />
          )}
          {inviteError != null && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 16 }}
              message={apiErrorMessage(
                inviteError,
                t("projects.join.failed", "加入项目失败"),
                t,
              )}
            />
          )}
          {invitePending && !inviteToken && (
            <Alert
              type="success"
              showIcon
              style={{ marginBottom: 16 }}
              message={t(
                "projects.join.pending",
                "加入申请已提交，等待项目管理员审批。",
              )}
            />
          )}

          <div className={styles.sectionHeader}>
            <h2 className={styles.sectionTitle}>
              {t("projects.myProjects", "我的项目")}
            </h2>
            <Input.Search
              className={styles.search}
              allowClear
              placeholder={t("projects.searchPlaceholder", "搜索项目名称")}
              onSearch={(value) => setQuery(value)}
              enterButton
            />
          </div>
          {content}

          <section
            className={styles.templateSection}
            aria-labelledby="project-templates-heading"
          >
            <h2
              id="project-templates-heading"
              className={`${styles.sectionTitle} ${styles.templateHeading}`}
            >
              {t("projects.create.templatesTitle", "从模板开始")}
            </h2>
            <div className={styles.templateGrid}>
              {TEMPLATES.map((tpl) => {
                const name = t(`projects.templates.${tpl.id}.name`, tpl.name);
                const description = t(
                  `projects.templates.${tpl.id}.description`,
                  tpl.description,
                );
                return (
                  <button
                    key={tpl.id}
                    type="button"
                    className={styles.templateCard}
                    onClick={() => openCreateFromTemplate(tpl.id)}
                    aria-label={t(
                      "projects.useTemplate",
                      "使用模板：{{name}}",
                      {
                        name,
                      },
                    )}
                  >
                    <span className={styles.templateName}>{name}</span>
                    <span className={styles.templateDescription}>
                      {description}
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        </div>
      </div>

      <CreateProjectModal
        open={createOpen}
        initialTemplateId={initialTemplateId}
        onClose={() => setCreateOpen(false)}
        onSaved={(project) => {
          setCreateOpen(false);
          navigate(`/projects/${encodeURIComponent(project.project_id)}`);
        }}
      />
    </>
  );
}
