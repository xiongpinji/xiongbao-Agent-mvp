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
 * - loaded project: the oversized PageShell title/card is replaced by a
 *   compact breadcrumb/action header — the project name survives as a
 *   visually hidden h1 so the document keeps a true page-level heading —
 *   plus a keyboard-accessible project-info disclosure (role, description,
 *   member count, updated time). The disclosure dismisses on Escape, on a
 *   click/tap outside and on any route project-ID change, never traps
 *   focus, and its panel is viewport-bounded with an internal scrollport
 *   so long descriptions stay fully readable on short desktop windows;
 *   tabs get an elastic center work area
 * - fixed 项目配置 column: bounded cards for instructions, connectors,
 *   experts, skills and scheduled tasks. Real instructions render as a
 *   compact preview with a keyboard disclosure for the verbatim text and a
 *   real owner/admin edit entry into the project modal; connector / skill /
 *   scheduled-task cards stay honestly unavailable, and expert/member
 *   operations keep using their own ACL-gated APIs. The column stays inline
 *   at every desktop width (narrow viewports are handled by the global nav
 *   collapsing to its rail, not by a disclosure)
 * - non-members get 404 from the server; the UI shows a fixed not-found
 *   state and never renders the project name
 */

import {
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Breadcrumb, Button, Input, Spin, Tabs, Tag, Typography } from "antd";
import { Info, Link2, Pencil, ScrollText, Sparkles, Timer } from "lucide-react";
import PageShell from "../../layouts/PageShell";
import styles from "./ProjectDetail.module.less";
import { EmptyState } from "../../components/EmptyState";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../utils/formatMessageTime";
import { apiErrorMessage, isNotFoundApiError } from "../../utils/apiError";
import {
  DESKTOP_DRAG_REGION_CLASS,
  DESKTOP_NO_DRAG_CLASS,
  titleRowEndPadding,
} from "../../utils/desktopChrome";
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

/** Id shared by the project-info disclosure trigger and its panel. */
const INFO_PANEL_ID = "project-detail-info";

/** Id shared by the instruction disclosure trigger and its body. */
const INSTRUCTION_BODY_ID = "project-detail-instructions";

/** Id shared by the disabled composer and its visible explanatory hint. */
const COMPOSER_HINT_ID = "project-detail-composer-hint";

/** Preview budget; longer instructions collapse to this many characters. */
const INSTRUCTION_PREVIEW_CHARS = 80;

/** Compact preview of a long instruction; the full text stays available. */
function instructionPreview(text: string): string {
  const chars = Array.from(text);
  return chars.length > INSTRUCTION_PREVIEW_CHARS
    ? `${chars.slice(0, INSTRUCTION_PREVIEW_CHARS).join("")}…`
    : text;
}

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
  const timezone = useServerTimezone();
  const { projectId } = useParams<{ projectId: string }>();
  const id = projectId ?? "";

  const { project, setProject, members, loading, error, reload } =
    useProjectLoader(id);
  const [activeTab, setActiveTab] = useState<DetailTab>("activity");
  const [editOpen, setEditOpen] = useState(false);
  const [infoOpen, setInfoOpen] = useState(false);
  const [instructionsExpanded, setInstructionsExpanded] = useState(false);

  const canEdit = project?.my_role === "owner" || project?.my_role === "admin";

  /** Root of the info disclosure: trigger + panel; outside clicks close. */
  const infoRootRef = useRef<HTMLDivElement | null>(null);
  const infoTriggerRef = useRef<HTMLButtonElement | null>(null);

  /** A different project or instruction revision starts collapsed again. */
  useEffect(() => {
    setInstructionsExpanded(false);
  }, [
    project?.project_id,
    project?.instructions_sha256,
    project?.instructions,
  ]);

  /**
   * A route-level project switch keeps this component instance mounted, so
   * the disclosure must not carry its open state into the next project.
   */
  useEffect(() => {
    setInfoOpen(false);
  }, [id]);

  /**
   * While the info disclosure is open it dismisses on Escape and on any
   * pointer press outside its root (mouse click or touch tap). Escape
   * refocuses the trigger, so dismissal is predictable and reopening stays
   * one keystroke away; clicks inside keep the panel open because the
   * description and metadata are meant to be read and selected.
   */
  useEffect(() => {
    if (!infoOpen) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setInfoOpen(false);
      infoTriggerRef.current?.focus();
    };
    const onPointerDown = (event: PointerEvent | MouseEvent) => {
      const root = infoRootRef.current;
      const target = event.target;
      if (root && target instanceof Node && root.contains(target)) {
        return;
      }
      setInfoOpen(false);
    };

    document.addEventListener("keydown", onKeyDown);
    // pointerdown covers touch; mousedown keeps environments without
    // PointerEvent support (including JSDOM) working. Both handlers are
    // idempotent, so a browser that fires both only closes once.
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [infoOpen]);

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
        aria-describedby={COMPOSER_HINT_ID}
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
        <span id={COMPOSER_HINT_ID}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            {t(
              "projects.taskComposer.hint",
              "在本页直接发送消息、协同写入、本地/云端与移交需后续后端权限；任务卡片摘要可在“任务”页显式分享给指定成员。",
            )}
          </Text>
        </span>
        <Button type="primary" disabled>
          {t("projects.taskComposer.send", "发送")}
        </Button>
      </div>
    </div>
  );

  /**
   * Compact project metadata: role, description, member count and updated
   * time stay reachable behind a plain keyboard-operable disclosure instead
   * of occupying the work area. The panel is tabbable so keyboard users can
   * scroll its viewport-bounded content, and it is a labelled group rather
   * than a dialog — focus is never trapped and Escape refocuses the trigger.
   */
  const projectInfo = (
    <div className={styles.projectInfo} ref={infoRootRef}>
      <button
        type="button"
        ref={infoTriggerRef}
        className={styles.infoTrigger}
        aria-expanded={infoOpen}
        aria-controls={INFO_PANEL_ID}
        onClick={() => setInfoOpen((open) => !open)}
      >
        <Info size={14} aria-hidden />
        {t("common.viewDetail", "查看详情")}
      </button>
      <div
        id={INFO_PANEL_ID}
        className={styles.infoPanel}
        role="group"
        aria-label={t("projects.detail.infoPanel", "项目信息")}
        tabIndex={0}
        hidden={!infoOpen}
      >
        {project.description?.trim() ? (
          <p className={styles.infoDescription}>{project.description}</p>
        ) : null}
        <div className={styles.infoMeta}>
          <Tag color={role.color} style={{ marginInlineEnd: 0 }}>
            {t(role.labelKey, role.fallback)}
          </Tag>
          <span>
            {t("projects.memberCount", "{{total}} 名成员", {
              total: project.member_count,
            })}
          </span>
          <span>
            {t("projects.updatedAt", "更新于 {{time}}", {
              time: formatServerDateTime(project.updated_at, timezone),
            })}
          </span>
        </div>
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

  const instructionsText = project.instructions ?? "";
  const hasInstructions = instructionsText.trim().length > 0;
  const instructionsLong =
    Array.from(instructionsText.trim()).length > INSTRUCTION_PREVIEW_CHARS;

  /** Honest placeholder card: label + unavailable badge, never a fake action. */
  const unavailableCard = (key: string, icon: ReactNode, label: string) => (
    <section key={key} className={styles.card} aria-label={label}>
      <div className={styles.cardHeader}>
        <span className={styles.cardTitle}>
          {icon}
          {label}
        </span>
        {unavailableBadge}
      </div>
    </section>
  );

  /**
   * Desktop: fixed-width column beside the work area with independent
   * scrolling. Mobile: full-width stack below the work column. Always
   * inline — the configuration is never hidden behind a drawer or toggle.
   */
  const configPanel = (
    <aside className={styles.configPanel}>
      <div className={styles.configTitle}>
        {t("projects.config.title", "项目配置")}
      </div>

      <section
        className={styles.card}
        aria-label={t("projects.config.instructions", "指令")}
      >
        <div className={styles.cardHeader}>
          <span className={styles.cardTitle}>
            <ScrollText size={14} aria-hidden />
            {t("projects.config.instructions", "指令")}
          </span>
          {canEdit && (
            <Button
              size="small"
              type="text"
              icon={<Pencil size={14} />}
              onClick={() => setEditOpen(true)}
            >
              {t("projects.config.editInstructions", "编辑指令")}
            </Button>
          )}
        </div>
        {hasInstructions ? (
          <>
            <div id={INSTRUCTION_BODY_ID} className={styles.instructionBody}>
              {instructionsExpanded
                ? instructionsText
                : instructionPreview(instructionsText.trim())}
            </div>
            {instructionsLong && (
              <button
                type="button"
                className={styles.instructionToggle}
                aria-expanded={instructionsExpanded}
                aria-controls={INSTRUCTION_BODY_ID}
                onClick={() => setInstructionsExpanded((open) => !open)}
              >
                {instructionsExpanded
                  ? t("projects.config.collapseInstructions", "收起")
                  : t("projects.config.expandInstructions", "展开全部")}
              </button>
            )}
          </>
        ) : (
          <div className={styles.cardMuted}>
            {t("projects.config.instructionsEmpty", "还没有项目指令。")}
          </div>
        )}
        <div className={styles.cardNote}>
          {t(
            "projects.config.instructionsNewTasksOnly",
            "仅新任务采用当前指令；修改指令不会改变已创建任务。",
          )}
        </div>
      </section>

      {unavailableCard(
        "connectors",
        <Link2 size={14} aria-hidden />,
        t("projects.config.connectors", "连接器"),
      )}

      {/*
        ProjectExperts renders its own named <section aria-label="专家">
        landmark, so the card wrapper deliberately stays a plain styled
        div: a second named section here would duplicate the spoken region
        without adding semantics.
      */}
      <div className={styles.card}>
        <ProjectExperts projectId={project.project_id} role={project.my_role} />
      </div>

      {unavailableCard(
        "skills",
        <Sparkles size={14} aria-hidden />,
        t("projects.config.skills", "技能"),
      )}
      {unavailableCard(
        "scheduledTasks",
        <Timer size={14} aria-hidden />,
        t("projects.config.scheduledTasks", "定时任务"),
      )}
      <div className={styles.cardNote}>
        {t(
          "projects.unavailable.config",
          "暂未开放：连接器、技能与定时任务仍需后续后端支持。",
        )}
      </div>

      {/*
        ProjectMembersPanel renders its own named <section aria-label="成员">
        landmark; keep this wrapper non-landmark so assistive tech sees
        exactly one region per card (same rule as the experts card).
      */}
      <div className={styles.card}>
        <ProjectMembersPanel
          projectId={project.project_id}
          role={project.my_role}
          members={members}
          onChanged={reload}
        />
      </div>
    </aside>
  );

  return (
    <>
      <div className={`${DESKTOP_DRAG_REGION_CLASS} ${styles.shell}`}>
        <header
          className={styles.header}
          style={{ paddingRight: titleRowEndPadding(16) }}
        >
          {/*
            Page-level heading: the compact header replaced PageShell's
            title, so the project name is restored as a visually hidden h1
            — the breadcrumb stays the visible identity while assistive
            tech still gets one true document heading.
          */}
          <h1 className={styles.visuallyHidden}>{project.name}</h1>
          <div className={styles.path}>
            <Breadcrumb
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
          </div>
          <div className={styles.headerActions}>
            {projectInfo}
            {canEdit && (
              <Button
                size="small"
                icon={<Pencil size={14} />}
                onClick={() => setEditOpen(true)}
              >
                {t("projects.detail.edit", "编辑项目资料")}
              </Button>
            )}
          </div>
        </header>

        <div className={`${DESKTOP_NO_DRAG_CLASS} ${styles.body}`}>
          <div className={styles.center}>
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
    </>
  );
}
