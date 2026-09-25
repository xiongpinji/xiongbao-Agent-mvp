/**
 * ProjectExperts — PS-07 first slice: project-level expert list.
 *
 * All members see the server-ordered list and its count. An expert that is
 * no longer shared/enabled is a generic unavailable card: the server returns
 * `name=null, description=null` and the panel never falls back to locally
 * cached Agent details, so no private resource leaks.
 *
 * Owner/admin additionally get a centered two-column "manage" modal:
 * - left: the draft list in order, with move up/down and remove;
 * - right: the caller's shared, enabled, single experts with search/add.
 *
 * Cancel discards the draft. Confirm sends one PUT with the revision the
 * dialog opened with; a 409 (`PROJECT_EXPERTS_CHANGED`) keeps the draft
 * visible, reloads the server revision/list and asks for confirmation again
 * instead of silently overwriting either side. A still-valid locally added
 * expert keeps its candidate label across that refresh because it was
 * explicitly added in this dialog; redacted/stopped/team Agents never do. If
 * the conflict refresh itself fails, the dialog says so and blocks Confirm
 * until an explicit reload succeeds. A server-listed unavailable expert stays
 * visible as a generic placeholder and removable, but blocks Confirm while
 * selected so the user must remove it before saving. Saving disables cancel,
 * the mask and the close button and can never be submitted twice. The list is
 * a candidate gate for new project tasks only — it is not an Agent grant and
 * never exposes private Agent resources.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Modal, Spin, Tag } from "antd";
import {
  ArrowDown,
  ArrowUp,
  GraduationCap,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import {
  PROJECT_EXPERTS_LIMIT,
  projectExpertsApi,
  type ProjectExpert,
} from "../../api/modules/projectExperts";
import type { ProjectRole } from "../../api/modules/projects";
import { useAgent } from "../../context/AgentContext";
import { useIsMobile } from "../../hooks/useIsMobile";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";

interface Props {
  projectId: string;
  role: ProjectRole;
}

interface DraftEntry {
  agentId: string;
  label: string;
  description: string | null;
}

const mutedStyle: React.CSSProperties = {
  fontSize: 12,
  color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "6px 0",
  borderBottom: "1px solid var(--fn-border-color-split, rgba(0,0,0,0.06))",
};

export default function ProjectExperts({ projectId, role }: Props) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const { agents } = useAgent();
  const canManage = role === "owner" || role === "admin";

  const [items, setItems] = useState<ProjectExpert[]>([]);
  const [revision, setRevision] = useState(0);
  const [loadedProjectId, setLoadedProjectId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [manageOpen, setManageOpen] = useState(false);
  const [draft, setDraft] = useState<string[]>([]);
  /** Only IDs explicitly added in this dialog may use local candidate labels. */
  const [localDraftIds, setLocalDraftIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [search, setSearch] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);
  /** The post-409 refresh failed: Confirm stays disabled until a reload works. */
  const [refreshFailed, setRefreshFailed] = useState(false);
  const [reloading, setReloading] = useState(false);

  const loadSeq = useRef(0);
  const saveSeq = useRef(0);
  const savingRef = useRef(false);

  const load = useCallback(async () => {
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const response = await projectExpertsApi.list(projectId);
      if (seq !== loadSeq.current) return;
      setItems(response.items);
      setRevision(response.revision);
      setLoadedProjectId(projectId);
    } catch (err) {
      if (seq !== loadSeq.current) return;
      setItems([]);
      setError(err);
      setLoadedProjectId(projectId);
    } finally {
      if (seq === loadSeq.current) setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    const sequence = loadSeq;
    return () => {
      sequence.current++;
    };
  }, [load, reloadKey]);

  /** Switching projects invalidates drafts and any in-flight save. */
  useEffect(() => {
    saveSeq.current++;
    savingRef.current = false;
    setSaving(false);
    setManageOpen(false);
    setDraft([]);
    setLocalDraftIds(new Set());
    setSearch("");
    setSaveError(null);
    setConflict(false);
    setRefreshFailed(false);
    setReloading(false);
  }, [projectId]);

  const loaded = loadedProjectId === projectId;
  const selectedCount = items.length;

  /** Only shared, enabled, single experts can be added; never teams. */
  const candidates = useMemo(() => {
    const selected = new Set(draft);
    return agents.filter(
      (agent) =>
        agent.state === "running" &&
        agent.kind !== "team" &&
        agent.is_shared === true &&
        !selected.has(agent.agent_id),
    );
  }, [agents, draft]);

  const keyword = search.trim().toLowerCase();
  const visibleCandidates = useMemo(() => {
    if (!keyword) return candidates;
    return candidates.filter((agent) =>
      `${agent.name} ${agent.description ?? ""}`
        .toLowerCase()
        .includes(keyword),
    );
  }, [candidates, keyword]);

  /**
   * Resolve a draft row. Server rows win; an `unavailable` server row is a
   * generic placeholder and must never fall back to local Agent details.
   */
  const draftEntries = useMemo<DraftEntry[]>(
    () =>
      draft.map((agentId) => {
        const serverItem = items.find((item) => item.agent_id === agentId);
        if (serverItem) {
          if (serverItem.status === "unavailable") {
            return {
              agentId,
              label: t("projects.experts.unavailable", "专家不可用"),
              description: null,
            };
          }
          return {
            agentId,
            label: serverItem.name ?? agentId,
            description: serverItem.description,
          };
        }
        const local = localDraftIds.has(agentId)
          ? agents.find(
              (agent) =>
                agent.agent_id === agentId &&
                agent.is_shared === true &&
                agent.state === "running" &&
                agent.kind !== "team",
            )
          : null;
        if (local) {
          return {
            agentId,
            label: local.name,
            description: local.description,
          };
        }
        return {
          agentId,
          label: t("projects.experts.unavailable", "专家不可用"),
          description: null,
        };
      }),
    [agents, draft, items, localDraftIds, t],
  );

  /** A server-listed unavailable expert must be removed before the PUT. */
  const hasUnavailableSelected = useMemo(
    () =>
      draft.some(
        (agentId) =>
          items.find((item) => item.agent_id === agentId)?.status ===
          "unavailable",
      ),
    [draft, items],
  );

  const openManage = () => {
    setDraft(items.map((item) => item.agent_id));
    setLocalDraftIds(new Set());
    setSearch("");
    setSaveError(null);
    setConflict(false);
    setRefreshFailed(false);
    setManageOpen(true);
  };

  const closeManage = () => {
    if (savingRef.current) return;
    // A manual conflict reload may still be pending. Its response must not
    // replace the list/revision of a later dialog opened for this project.
    saveSeq.current++;
    setManageOpen(false);
    setDraft([]);
    setLocalDraftIds(new Set());
    setSearch("");
    setSaveError(null);
    setConflict(false);
    setRefreshFailed(false);
    setReloading(false);
  };

  const addDraft = (agentId: string) => {
    if (
      draft.length >= PROJECT_EXPERTS_LIMIT ||
      draft.includes(agentId) ||
      !candidates.some((agent) => agent.agent_id === agentId)
    )
      return;
    setDraft((prev) =>
      prev.length >= PROJECT_EXPERTS_LIMIT || prev.includes(agentId)
        ? prev
        : [...prev, agentId],
    );
    setLocalDraftIds((prev) => new Set(prev).add(agentId));
  };

  const removeDraft = (agentId: string) => {
    setDraft((prev) => prev.filter((id) => id !== agentId));
    setLocalDraftIds((prev) => {
      const next = new Set(prev);
      next.delete(agentId);
      return next;
    });
  };

  const moveDraft = (index: number, delta: number) => {
    setDraft((prev) => {
      const target = index + delta;
      if (target < 0 || target >= prev.length) return prev;
      const next = [...prev];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const confirm = async () => {
    if (savingRef.current || refreshFailed || hasUnavailableSelected) return;
    savingRef.current = true;
    const seq = ++saveSeq.current;
    setSaving(true);
    setSaveError(null);
    setConflict(false);
    setRefreshFailed(false);
    try {
      const response = await projectExpertsApi.set(projectId, revision, draft);
      if (seq !== saveSeq.current) return;
      setItems(response.items);
      setRevision(response.revision);
      setLoadedProjectId(projectId);
      setManageOpen(false);
      setDraft([]);
      setLocalDraftIds(new Set());
      setSearch("");
      setRefreshFailed(false);
    } catch (err) {
      if (seq !== saveSeq.current) return;
      setSaveError(err);
      if (parseApiError(err)?.code === "PROJECT_EXPERTS_CHANGED") {
        setConflict(true);
        try {
          const fresh = await projectExpertsApi.list(projectId);
          if (seq !== saveSeq.current) return;
          setItems(fresh.items);
          setRevision(fresh.revision);
          setLoadedProjectId(projectId);
          // A locally added expert was explicitly chosen in this dialog, so
          // keep its candidate label when it is still shared/running/single;
          // draftEntries still redacts server rows and invalid Agents.
        } catch {
          if (seq !== saveSeq.current) return;
          // Never claim the list was refreshed: block Confirm until reload.
          setRefreshFailed(true);
        }
      }
    } finally {
      if (seq === saveSeq.current) {
        savingRef.current = false;
        setSaving(false);
      }
    }
  };

  /** Explicit retry after a failed post-409 refresh; unlocks Confirm on success. */
  const reloadAfterConflict = async () => {
    if (savingRef.current || reloading) return;
    const seq = ++saveSeq.current;
    setReloading(true);
    try {
      const fresh = await projectExpertsApi.list(projectId);
      if (seq !== saveSeq.current) return;
      setItems(fresh.items);
      setRevision(fresh.revision);
      setLoadedProjectId(projectId);
      setRefreshFailed(false);
      setSaveError(null);
    } catch (err) {
      if (seq !== saveSeq.current) return;
      setSaveError(err);
      setRefreshFailed(true);
    } finally {
      if (seq === saveSeq.current) setReloading(false);
    }
  };

  return (
    <section
      aria-label={t("projects.experts.title", "专家")}
      style={{ marginTop: 12, marginBottom: 12 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 8,
          marginBottom: 4,
        }}
      >
        <span
          style={{
            fontSize: 13,
            fontWeight: 500,
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
          }}
        >
          <GraduationCap size={14} />
          {t("projects.experts.count", "专家（{{value}}/{{max}}）", {
            value: loaded ? selectedCount : "—",
            max: PROJECT_EXPERTS_LIMIT,
          })}
        </span>
        {canManage && loaded && error == null && (
          <Button size="small" onClick={openManage}>
            {t("projects.experts.manage", "管理专家")}
          </Button>
        )}
      </div>

      {!loaded || loading ? (
        <Spin size="small" />
      ) : error != null ? (
        <div>
          <div style={mutedStyle}>
            {apiErrorMessage(
              error,
              t("projects.experts.loadFailed", "加载项目专家失败"),
              t,
            )}
          </div>
          <Button
            size="small"
            type="link"
            onClick={() => setReloadKey((key) => key + 1)}
          >
            {t("common.retry", "重试")}
          </Button>
        </div>
      ) : items.length === 0 ? (
        <div style={mutedStyle}>
          {t(
            "projects.experts.empty",
            "尚未选择项目专家，新任务可从本人可用专家中选择。",
          )}
        </div>
      ) : (
        <ol
          aria-label={t("projects.experts.list", "项目专家名单")}
          style={{ listStyle: "none", margin: 0, padding: 0 }}
        >
          {items.map((item) => (
            <li key={item.agent_id} style={{ padding: "6px 0" }}>
              {item.status === "unavailable" ? (
                <div>
                  <Tag style={{ marginInlineEnd: 0 }}>
                    {t("projects.experts.unavailable", "专家不可用")}
                  </Tag>
                  <div style={{ ...mutedStyle, marginTop: 4 }}>
                    {t(
                      "projects.experts.unavailableHint",
                      "该专家已取消共享或停用，等待管理员调整名单。",
                    )}
                  </div>
                </div>
              ) : (
                <div>
                  <div style={{ fontSize: 13 }}>
                    {item.name ?? item.agent_id}
                  </div>
                  {item.description ? (
                    <div style={mutedStyle}>{item.description}</div>
                  ) : null}
                </div>
              )}
            </li>
          ))}
        </ol>
      )}

      <div style={{ ...mutedStyle, marginTop: 6 }}>
        {t(
          "projects.experts.scopeNote",
          "名单仅作为新项目任务的候选门禁，不代表 Agent 授权或凭据共享。",
        )}
      </div>

      {canManage && (
        <Modal
          title={t("projects.experts.manageTitle", "管理专家")}
          open={manageOpen}
          onCancel={closeManage}
          centered
          width={isMobile ? undefined : 760}
          maskClosable={!saving}
          keyboard={!saving}
          closable={!saving}
          destroyOnHidden
          footer={[
            <Button key="cancel" disabled={saving} onClick={closeManage}>
              {t("projects.experts.cancel", "取消")}
            </Button>,
            <Button
              key="confirm"
              type="primary"
              loading={saving}
              disabled={
                saving || reloading || refreshFailed || hasUnavailableSelected
              }
              onClick={() => void confirm()}
            >
              {t("projects.experts.confirm", "确定")}
            </Button>,
          ]}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: isMobile ? "1fr" : "1fr 1fr",
              gap: 16,
            }}
          >
            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>
                {t(
                  "projects.experts.selectedTitle",
                  "已添加（{{value}}/{{max}}）",
                  { value: draft.length, max: PROJECT_EXPERTS_LIMIT },
                )}
              </div>
              {draftEntries.length === 0 ? (
                <div style={mutedStyle}>
                  {t("projects.experts.selectedEmpty", "尚未添加专家。")}
                </div>
              ) : (
                <ol
                  aria-label={t("projects.experts.selectedList", "已添加专家")}
                  style={{ listStyle: "none", margin: 0, padding: 0 }}
                >
                  {draftEntries.map((entry, index) => (
                    <li key={entry.agentId} style={rowStyle}>
                      <span
                        style={{
                          flex: 1,
                          minWidth: 0,
                          overflowWrap: "anywhere",
                        }}
                      >
                        <span style={{ fontSize: 13 }}>{entry.label}</span>
                        {entry.description ? (
                          <div style={mutedStyle}>{entry.description}</div>
                        ) : null}
                      </span>
                      <Button
                        size="small"
                        type="text"
                        icon={<ArrowUp size={14} />}
                        disabled={saving || index === 0}
                        aria-label={t(
                          "projects.experts.moveUpNamed",
                          "上移 {{name}}",
                          { name: entry.label },
                        )}
                        onClick={() => moveDraft(index, -1)}
                      />
                      <Button
                        size="small"
                        type="text"
                        icon={<ArrowDown size={14} />}
                        disabled={saving || index === draftEntries.length - 1}
                        aria-label={t(
                          "projects.experts.moveDownNamed",
                          "下移 {{name}}",
                          { name: entry.label },
                        )}
                        onClick={() => moveDraft(index, 1)}
                      />
                      <Button
                        size="small"
                        type="text"
                        danger
                        icon={<Trash2 size={14} />}
                        disabled={saving}
                        aria-label={t(
                          "projects.experts.removeNamed",
                          "移除 {{name}}",
                          { name: entry.label },
                        )}
                        onClick={() => removeDraft(entry.agentId)}
                      />
                    </li>
                  ))}
                </ol>
              )}
              {draft.length >= PROJECT_EXPERTS_LIMIT && (
                <div style={{ ...mutedStyle, marginTop: 6 }}>
                  {t(
                    "projects.experts.maxReached",
                    "最多可添加 {{max}} 位专家。",
                    { max: PROJECT_EXPERTS_LIMIT },
                  )}
                </div>
              )}
            </div>

            <div>
              <div style={{ fontWeight: 500, marginBottom: 8 }}>
                {t("projects.experts.candidatesTitle", "可添加的共享专家")}
              </div>
              <Input
                allowClear
                prefix={<Search size={14} />}
                placeholder={t(
                  "projects.experts.candidatesSearchPlaceholder",
                  "搜索专家",
                )}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                disabled={saving}
                style={{ marginBottom: 8 }}
              />
              {candidates.length === 0 ? (
                <div style={mutedStyle}>
                  {t(
                    "projects.experts.candidatesEmpty",
                    "暂无已共享且启用的单专家可添加。",
                  )}
                </div>
              ) : visibleCandidates.length === 0 ? (
                <div style={mutedStyle}>
                  {t("projects.experts.candidatesNoMatch", "没有匹配的专家。")}
                </div>
              ) : (
                <ul
                  aria-label={t(
                    "projects.experts.candidateList",
                    "可添加的共享专家",
                  )}
                  style={{ listStyle: "none", margin: 0, padding: 0 }}
                >
                  {visibleCandidates.map((agent) => (
                    <li key={agent.agent_id} style={rowStyle}>
                      <span
                        style={{
                          flex: 1,
                          minWidth: 0,
                          overflowWrap: "anywhere",
                        }}
                      >
                        <span style={{ fontSize: 13 }}>{agent.name}</span>
                        {agent.description ? (
                          <div style={mutedStyle}>{agent.description}</div>
                        ) : null}
                      </span>
                      <Button
                        size="small"
                        icon={<Plus size={14} />}
                        disabled={
                          saving || draft.length >= PROJECT_EXPERTS_LIMIT
                        }
                        aria-label={t(
                          "projects.experts.addNamed",
                          "添加 {{name}}",
                          { name: agent.name },
                        )}
                        onClick={() => addDraft(agent.agent_id)}
                      >
                        {t("projects.experts.add", "添加")}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>

          {refreshFailed ? (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message={t(
                "projects.experts.conflictRefreshFailed",
                "刷新服务端名单失败，你的修改仍保留；请重新加载成功后再保存。",
              )}
              action={
                <Button
                  size="small"
                  loading={reloading}
                  disabled={saving}
                  onClick={() => void reloadAfterConflict()}
                >
                  {t("projects.experts.reload", "重新加载")}
                </Button>
              }
            />
          ) : conflict ? (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={t(
                "projects.experts.conflict",
                "配置已被其他管理员修改，已刷新服务端名单；你的修改仍保留，请确认后重试。",
              )}
            />
          ) : null}
          {hasUnavailableSelected && (
            <Alert
              type="warning"
              showIcon
              style={{ marginTop: 12 }}
              message={t(
                "projects.experts.unavailableBlocksSave",
                "已选名单中包含不可用专家，请先移除后再保存。",
              )}
            />
          )}
          {saveError != null && !conflict && !refreshFailed && (
            <Alert
              type="error"
              showIcon
              style={{ marginTop: 12 }}
              message={apiErrorMessage(
                saveError,
                t("projects.experts.saveFailed", "保存项目专家失败"),
                t,
              )}
            />
          )}
        </Modal>
      )}
    </section>
  );
}
