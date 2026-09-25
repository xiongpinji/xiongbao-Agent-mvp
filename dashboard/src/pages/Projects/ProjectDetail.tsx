/**
 * ProjectDetail — /projects/:projectId
 *
 * Loads the real project record + member list, and keeps every capability
 * the backend does not provide yet visibly unavailable:
 * - 计划 tab: real PS-04 todos (table + board) via `ProjectPlan`
 * - 动态 tab: real PS-03A activity feed (related/members) via `ProjectActivity`
 * - 资产 tab: real PS-06A / 023A private asset library via `ProjectAssets`
 * - 任务 tab: real private task creation with a confirmed instruction
 *   snapshot, plus the existing manual attach/share/read-only card flows
 * - one disabled bottom task composer stays mounted below every tab body
 *   (all four tabs) and beside the fixed configuration column on desktop;
 *   direct project send remains unavailable
 * - fixed 项目配置 column: real instructions (editable for owner/admin via
 *   PATCH); member invitations and approvals use project membership APIs,
 *   while connector / expert / skill / scheduled-task rows stay unavailable;
 *   the column stays inline at every desktop width (narrow viewports are
 *   handled by the global nav collapsing to its rail, not by a disclosure)
 * - non-members get 404 from the server; the UI shows a fixed not-found
 *   state and never renders the project name
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Breadcrumb, Button, Input, Spin, Tabs, Tag, Typography } from "antd";
import { Link2, Pencil, Sparkles, Timer } from "lucide-react";
import PageShell from "../../layouts/PageShell";
import styles from "./ProjectDetail.module.less";
import { EmptyState } from "../../components/EmptyState";
import { useIsMobile } from "../../hooks/useIsMobile";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { apiErrorMessage, isNotFoundApiError } from "../../utils/apiError";
import {
  projectsApi,
  type ProjectMember,
  type ProjectRecord,
} from "../../api/modules/projects";
import CreateProjectModal from "./CreateProjectModal";
import ProjectActivity from "./ProjectActivity";
import ProjectAssets from "./ProjectAssets";
import ProjectExperts from "./ProjectExperts";
import ProjectMembersPanel from "./ProjectMembersPanel";
import ProjectPlan from "./ProjectPlan";
import ProjectTasks from "./ProjectTasks";
import { projectRoleTag } from "./index";

const { Text } = Typography;

type DetailTab = "activity" | "plan" | "tasks" | "assets";

function useProjectLoader(projectId: string) {
  const [project, setProject] = useState<ProjectRecord | null>(null);
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null);
  const [members, setMembers] = useState<ProjectMember[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);
  /** Monotonic guard: responses from a previously viewed project are dropped. */
  const requestSeq = useRef(0);

  useEffect(() => {
    const seq = ++requestSeq.current;
    setLoading(true);
    setError(null);
    setProject(null);
    setMembers(null);
    Promise.all([
      projectsApi.get(projectId),
      // Members are auxiliary — a failed member list must not blank the page.
      projectsApi.members(projectId).catch(() => null),
    ])
      .then(([record, memberRows]) => {
        if (seq !== requestSeq.current) return;
        setProject(record);
        setMembers(memberRows);
        setError(null);
        setLoadedProjectId(projectId);
      })
      .catch((err: unknown) => {
        if (seq !== requestSeq.current) return;
        setError(err);
        setLoadedProjectId(projectId);
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
  }, [projectId, reloadKey]);

  const reload = useCallback(() => setReloadKey((k) => k + 1), []);
  return {
    project,
    setProject,
    members,
    loading: loading || loadedProjectId !== projectId,
    error,
    reload,
  };
}

export default function ProjectDetail() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const timezone = useServerTimezone();
  const { projectId } = useParams<{ projectId: string }>();
  const id = projectId ?? "";

  const { project, setProject, members, loading, error, reload } =
    useProjectLoader(id);
  const [activeTab, setActiveTab] = useState<DetailTab>("activity");
  const [editOpen, setEditOpen] = useState(false);

  const canEdit = project?.my_role === "owner" || project?.my_role === "admin";

  const notFound = error != null && isNotFoundApiError(error);

  if (loading) {
    return (
      <PageShell title={t("projects.detail.breadcrumb", "项目")}>
        <div style={{ display: "flex", justifyContent: "center", padding: 64 }}>
          <Spin />
        </div>
      </PageShell>
    );
  }

  if (error || !project) {
    return (
      <PageShell title={t("projects.detail.breadcrumb", "项目")}>
        <EmptyState
          variant="error"
          title={
            notFound
              ? t("projects.detail.notFound", "项目不存在或你无权访问")
              : t("projects.detail.loadFailed", "加载项目详情失败")
          }
          description={
            notFound
              ? undefined
              : apiErrorMessage(
                  error,
                  t("projects.detail.loadFailed", "加载项目详情失败"),
                  t,
                )
          }
          actionLabel={
            notFound
              ? t("projects.detail.back", "返回项目列表")
              : t("common.retry", "重试")
          }
          onAction={() => (notFound ? navigate("/projects") : reload())}
        />
      </PageShell>
    );
  }

  const role = projectRoleTag(project.my_role);
  const secondaryStyle: React.CSSProperties = {
    color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
    fontSize: 12,
  };

  const unavailableBadge = (
    <Tag style={{ marginInlineEnd: 0 }}>
      {t("projects.unavailable.badge", "暂未开放")}
    </Tag>
  );

  const taskComposer = (
    <div
      style={{
        width: "100%",
        maxWidth: 640,
        margin: "0 auto",
        padding: "8px 0 16px",
        boxSizing: "border-box",
        flexShrink: 0,
      }}
    >
      <Input.TextArea
        rows={2}
        disabled
        placeholder={t(
          "projects.taskComposer.placeholder",
          "项目内直接发送消息尚未开放：请在“任务”页新建项目任务并前往对话",
        )}
      />
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          marginTop: 8,
        }}
      >
        <Text type="secondary" style={{ fontSize: 12 }}>
          {t(
            "projects.taskComposer.hint",
            "在本页直接发送消息、协同写入、本地/云端与移交需后续后端权限；任务卡片摘要可在“任务”页显式分享给指定成员。",
          )}
        </Text>
        <Button type="primary" disabled>
          {t("projects.taskComposer.send", "发送")}
        </Button>
      </div>
    </div>
  );

  const tabItems = [
    {
      key: "activity",
      label: t("projects.tabs.activity", "动态"),
      children: <ProjectActivity projectId={project.project_id} />,
    },
    {
      key: "plan",
      label: t("projects.tabs.plan", "计划"),
      children: (
        <ProjectPlan
          projectId={project.project_id}
          role={project.my_role}
          members={members}
        />
      ),
    },
    {
      key: "tasks",
      label: t("projects.tabs.tasks", "任务"),
      children: (
        <ProjectTasks
          projectId={project.project_id}
          members={members}
          instructions={project.instructions}
          instructionsSha256={project.instructions_sha256}
          onProjectReload={reload}
        />
      ),
    },
    {
      key: "assets",
      label: t("projects.tabs.assets", "资产"),
      children: <ProjectAssets projectId={project.project_id} />,
    },
  ];

  const configRowStyle: React.CSSProperties = {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8,
    padding: "8px 0",
    borderBottom: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
    fontSize: 13,
  };

  const configRows = [
    {
      key: "connectors",
      icon: <Link2 size={14} />,
      label: t("projects.config.connectors", "连接器"),
    },
    {
      key: "skills",
      icon: <Sparkles size={14} />,
      label: t("projects.config.skills", "技能"),
    },
    {
      key: "scheduledTasks",
      icon: <Timer size={14} />,
      label: t("projects.config.scheduledTasks", "定时任务"),
    },
  ];

  /**
   * Desktop: fixed-width column beside the work area with independent
   * scrolling. Mobile: full-width stack below the work column. Always
   * inline — the configuration is never hidden behind a drawer or toggle.
   */
  const configPanel = (
    <aside
      style={{
        width: isMobile ? "100%" : 300,
        flexShrink: 0,
        borderLeft: isMobile
          ? "none"
          : "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
        borderTop: isMobile
          ? "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))"
          : "none",
        paddingLeft: isMobile ? 0 : 16,
        paddingTop: isMobile ? 16 : 0,
        minHeight: 0,
        overflowY: isMobile ? undefined : "auto",
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 8 }}>
        {t("projects.config.title", "项目配置")}
      </div>

      <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 4 }}>
        {t("projects.config.instructions", "指令")}
      </div>
      {project.instructions?.trim() ? (
        <div
          style={{
            fontSize: 13,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            color: "var(--fn-text-secondary, rgba(0,0,0,0.65))",
            marginBottom: 12,
          }}
        >
          {project.instructions}
        </div>
      ) : (
        <div style={{ ...secondaryStyle, marginBottom: 12 }}>
          {t("projects.config.instructionsEmpty", "还没有项目指令。")}
        </div>
      )}
      <div style={{ ...secondaryStyle, marginBottom: 12 }}>
        {t(
          "projects.config.instructionsNewTasksOnly",
          "仅新任务采用当前指令；修改指令不会改变已创建任务。",
        )}
      </div>

      <ProjectExperts projectId={project.project_id} role={project.my_role} />

      {configRows.map((row) => (
        <div key={row.key} style={configRowStyle}>
          <span
            style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
          >
            {row.icon}
            {row.label}
          </span>
          {unavailableBadge}
        </div>
      ))}
      <div style={{ ...secondaryStyle, marginTop: 6 }}>
        {t(
          "projects.unavailable.config",
          "暂未开放：连接器、技能与定时任务仍需后续后端支持。",
        )}
      </div>

      <ProjectMembersPanel
        projectId={project.project_id}
        role={project.my_role}
        members={members}
        onChanged={reload}
      />
    </aside>
  );

  return (
    <PageShell
      title={project.name}
      subtitle={project.description || undefined}
      actions={
        canEdit ? (
          <Button icon={<Pencil size={14} />} onClick={() => setEditOpen(true)}>
            {t("projects.detail.edit", "编辑项目资料")}
          </Button>
        ) : undefined
      }
      fill={!isMobile}
    >
      <Breadcrumb
        style={{ marginBottom: 12, flexShrink: 0 }}
        items={[
          {
            title: (
              <Link to="/projects">
                {t("projects.detail.breadcrumb", "项目")}
              </Link>
            ),
          },
          { title: project.name },
        ]}
      />

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          marginBottom: 12,
          flexShrink: 0,
        }}
      >
        <Tag color={role.color}>{t(role.labelKey, role.fallback)}</Tag>
        <span style={secondaryStyle}>
          {t("projects.memberCount", "{{total}} 名成员", {
            total: project.member_count,
          })}
        </span>
        <span style={secondaryStyle}>
          {t("projects.updatedAt", "更新于 {{time}}", {
            time: formatServerDateTime(project.updated_at, timezone),
          })}
        </span>
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: isMobile ? "column" : "row",
          gap: isMobile ? 16 : 24,
          alignItems: "stretch",
          flex: isMobile ? "none" : 1,
          minHeight: 0,
          minWidth: 0,
        }}
      >
        <div
          style={{
            flex: isMobile ? "none" : 1,
            minWidth: 0,
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          <div className={styles.workTabs}>
            <Tabs
              activeKey={activeTab}
              onChange={(key) => setActiveTab(key as DetailTab)}
              items={tabItems}
            />
          </div>
          {taskComposer}
        </div>
        {configPanel}
      </div>

      <CreateProjectModal
        open={editOpen}
        editTarget={project}
        onClose={() => setEditOpen(false)}
        onSaved={(saved) => {
          setEditOpen(false);
          setProject(saved);
        }}
      />
    </PageShell>
  );
}
