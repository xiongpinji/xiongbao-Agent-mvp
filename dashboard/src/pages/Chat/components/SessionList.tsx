import { memo, useCallback, useMemo, useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Dropdown } from "antd";
import type { MenuProps } from "antd";
import {
  Pencil,
  MoreHorizontal,
  Trash2,
  Pin,
  PinOff,
  MessageSquarePlus,
  Search,
  GitFork,
  Eye,
  EyeOff,
  X,
} from "lucide-react";
import type { Session, SessionSearch } from "../hooks/useSessions";
import { apiErrorMessage } from "../../../utils/apiError";
import type { OctopAgent } from "../../../context/AgentContext";
import { isAgentChatReady } from "../../../utils/agentError";
import { showConfirmModal } from "../../../utils/confirmModal";
import { ExpertIcon } from "../../Experts/components/iconForName";
import { useHiddenSharedExperts } from "../hooks/useHiddenSharedExperts";
import SessionChannelIcon from "./SessionChannelIcon";
import SharedExpertHint from "./SharedExpertHint";
import TeamChatBadge from "./TeamChatBadge";
import styles from "../index.module.less";

function AgentUnreadBadge({ count }: { count: number }) {
  const { t } = useTranslation();
  if (!count || count <= 0) return null;
  return (
    <span
      className={styles.agentUnreadBadge}
      aria-label={t("chat.unreadMessages", "未读消息")}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  forkDisabled?: boolean;
  forkDisabledHint?: string;
}

const SessionItem = memo(function SessionItem({
  session,
  isActive,
  onSelect,
  onDelete,
  onRename,
  onPin,
  onFork,
  forkDisabled,
  forkDisabledHint,
}: SessionItemProps) {
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(session.name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isEditing) setEditValue(session.name);
  }, [session.name, isEditing]);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const commitEdit = useCallback(() => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== session.name) {
      onRename(session.id, trimmed);
    } else {
      setEditValue(session.name);
    }
    setIsEditing(false);
  }, [editValue, session.name, session.id, onRename]);

  const itemForkDisabled = Boolean(forkDisabled) || !session.hasActivity;
  const itemForkHint = !session.hasActivity
    ? t("chat.forkNoAssistant")
    : forkDisabledHint;

  const menuItems: MenuProps["items"] = [
    {
      key: "pin",
      label: session.pinned
        ? t("chat.unpin", "取消置顶")
        : t("chat.pin", "置顶"),
      icon: session.pinned ? <PinOff size={14} /> : <Pin size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onPin(session.id, !session.pinned);
      },
    },
    {
      key: "fork",
      label: t("chat.fork", "分叉"),
      icon: <GitFork size={14} />,
      disabled: itemForkDisabled,
      title: itemForkDisabled && itemForkHint ? itemForkHint : undefined,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onFork(session.id);
      },
    },
    {
      key: "rename",
      label: t("common.rename"),
      icon: <Pencil size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        setIsEditing(true);
      },
    },
    {
      key: "delete",
      label: t("common.delete", "Delete"),
      icon: <Trash2 size={14} />,
      danger: true,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        showConfirmModal({
          title: t("chat.deleteSessionConfirm"),
          okText: t("common.delete"),
          cancelText: t("common.cancel"),
          okButtonProps: { danger: true },
          onOk: () => {
            onDelete(session.id);
          },
        });
      },
    },
  ];

  return (
    <div
      className={`${styles.sessionRow} ${
        isActive ? styles.sessionRowActive : ""
      } ${session.pinned ? styles.sessionRowPinned : ""}`}
      onClick={() => {
        if (!isEditing) onSelect(session.id);
      }}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (
          e.target === e.currentTarget &&
          (e.key === "Enter" || e.key === " ") &&
          !isEditing
        ) {
          e.preventDefault();
          onSelect(session.id);
        }
      }}
    >
      <SessionChannelIcon
        channelType={session.channelType}
        size={12}
        className={styles.sessionRowIcon}
      />
      {isEditing ? (
        <input
          ref={inputRef}
          className={styles.sessionNameInput}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter") commitEdit();
            if (e.key === "Escape") {
              setEditValue(session.name);
              setIsEditing(false);
            }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <>
          <span className={styles.sessionRowTitle}>{session.name}</span>
          {session.pinned ? (
            <span
              className={styles.sessionRowPinIndicator}
              title={t("chat.unpin")}
            >
              <Pin size={12} strokeWidth={2} />
            </span>
          ) : null}
          <Dropdown
            menu={{ items: menuItems }}
            trigger={["click"]}
            placement="bottomRight"
          >
            <button
              type="button"
              className={styles.sessionRowMore}
              aria-label={t("common.more", "More")}
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal size={15} />
            </button>
          </Dropdown>
        </>
      )}
    </div>
  );
});

interface AgentCardProps {
  agent: OctopAgent;
  sessions: Session[];
  activeId: string | null;
  search: SessionSearch;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onRetrySearch: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onNewChat: (agentId: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
  onHide?: () => void;
}

function ActiveAgentCard({
  agent,
  sessions,
  activeId,
  search,
  hasMore,
  loadingMore,
  onLoadMore,
  onRetrySearch,
  onSelect,
  onNewChat,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
  onHide,
}: AgentCardProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";

  const searching = Boolean(search.query.trim());
  const filteredSessions = searching ? search.sessions : sessions;
  const showExpandMore = hasMore && !(searching && search.error);
  const sessionsEnabled = isAgentChatReady(agent.state);

  return (
    <div
      className={styles.agentCardActive}
      style={{
        background: `${accent}08`,
        borderColor: `${accent}18`,
      }}
    >
      <div className={styles.agentCardProfile}>
        <div
          className={styles.agentCardAvatar}
          style={{
            color: accent,
            background: `${accent}14`,
            boxShadow: `0 0 0 1px ${accent}22`,
          }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={16}
          />
        </div>
        <div className={styles.agentCardInfo}>
          <div className={styles.agentCardNameRow}>
            <div className={styles.agentNameCluster}>
              <div className={styles.agentCardName}>{agent.name}</div>
              <TeamChatBadge agent={agent} />
              <SharedExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
            <button
              type="button"
              className={styles.agentNewChatBtn}
              aria-label={t("chatWelcome.newChat")}
              title={t("chatWelcome.newChat")}
              onClick={(e) => {
                e.stopPropagation();
                onNewChat(agent.agent_id);
              }}
            >
              <MessageSquarePlus size={14} strokeWidth={1.75} aria-hidden />
            </button>
            {onHide ? (
              <button
                type="button"
                className={styles.agentHideBtn}
                aria-label={t("chat.expertHide")}
                title={t("chat.expertHide")}
                onClick={(e) => {
                  e.stopPropagation();
                  onHide();
                }}
              >
                <EyeOff size={14} aria-hidden />
              </button>
            ) : null}
          </div>
          {agent.description ? (
            <div className={styles.agentCardDesc}>{agent.description}</div>
          ) : (
            <div className={styles.agentCardDescMuted}>
              {t("chat.agentNoDescription", "暂无描述")}
            </div>
          )}
        </div>
      </div>

      <div className={styles.agentCardSessions}>
        {searching && search.loading ? (
          <div role="status">{t("chat.searchLoading")}</div>
        ) : null}
        {searching && search.error ? (
          <div role="alert">
            <div>{t("chat.searchFailed")}</div>
            <div>
              {apiErrorMessage(search.error, t("chat.searchFailed"), t)}
            </div>
            <button
              type="button"
              onClick={onRetrySearch}
              disabled={search.loading}
            >
              {t("common.retry")}
            </button>
          </div>
        ) : null}
        {!sessionsEnabled ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.agentNotRunningHint")}
          </div>
        ) : !searching && sessions.length === 0 ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSessionsYet", "直接发消息即可开始对话")}
          </div>
        ) : filteredSessions.length === 0 &&
          !search.loading &&
          !search.error ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSearchResults", "没有匹配的会话")}
          </div>
        ) : (
          <>
            {filteredSessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                isActive={activeId === s.id}
                onSelect={(id) => onSelect(id, agent.agent_id)}
                onDelete={onDelete}
                onRename={onRename}
                onPin={onPin}
                onFork={onFork}
                forkDisabled={
                  activeId === s.id ? activeForkDisabled : undefined
                }
                forkDisabledHint={
                  activeId === s.id ? activeForkDisabledHint : undefined
                }
              />
            ))}
            {showExpandMore ? (
              <button
                type="button"
                className={styles.sessionLoadMore}
                onClick={onLoadMore}
                disabled={loadingMore}
              >
                {loadingMore
                  ? t("common.loading")
                  : t("chat.expandMore", "展开更多")}
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

interface AgentRowProps {
  agent: OctopAgent;
  onSelect: () => void;
  onNewChat?: () => void;
  onHide?: () => void;
  onUnhide?: () => void;
}

function InactiveAgentRow({
  agent,
  onSelect,
  onNewChat,
  onHide,
  onUnhide,
}: AgentRowProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";

  return (
    <div className={styles.agentRowWrap}>
      <div
        className={styles.agentRow}
        onClick={onSelect}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (
            e.target === e.currentTarget &&
            (e.key === "Enter" || e.key === " ")
          ) {
            e.preventDefault();
            onSelect();
          }
        }}
      >
        <div
          className={styles.agentRowAvatar}
          style={{ color: accent, background: `${accent}12` }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={14}
          />
        </div>
        <div className={styles.agentRowInfo}>
          <div className={styles.agentRowNameRow}>
            <div className={styles.agentNameCluster}>
              <div className={styles.agentRowName}>{agent.name}</div>
              <TeamChatBadge agent={agent} />
              <SharedExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
            {onNewChat ? (
              <button
                type="button"
                className={styles.agentNewChatBtn}
                aria-label={t("chatWelcome.newChat")}
                title={t("chatWelcome.newChat")}
                onClick={(e) => {
                  e.stopPropagation();
                  onNewChat();
                }}
              >
                <MessageSquarePlus size={14} strokeWidth={1.75} aria-hidden />
              </button>
            ) : null}
          </div>
          <div className={styles.agentRowDesc}>{agent.description || "—"}</div>
        </div>
      </div>
      {onHide ? (
        <button
          type="button"
          className={styles.agentHideBtn}
          aria-label={t("chat.expertHide")}
          title={t("chat.expertHide")}
          onClick={onHide}
        >
          <EyeOff size={14} aria-hidden />
        </button>
      ) : null}
      {onUnhide ? (
        <button
          type="button"
          className={styles.agentHideBtn}
          aria-label={t("chat.expertUnhide")}
          title={t("chat.expertUnhide")}
          onClick={onUnhide}
        >
          <Eye size={14} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

interface SessionListProps {
  agents: OctopAgent[];
  sessions: Session[];
  activeId: string | null;
  activeAgentId: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  search: SessionSearch;
  onSearchChange: (query: string) => void;
  onLoadMoreSearch: () => void;
  onRetrySearch: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onAgentSelect: (agentId: string) => void;
  onNewChat: (agentId: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
}

export default function SessionList({
  agents,
  sessions,
  activeId,
  activeAgentId,
  hasMore,
  loadingMore,
  onLoadMore,
  search,
  onSearchChange,
  onLoadMoreSearch,
  onRetrySearch,
  onSelect,
  onAgentSelect,
  onNewChat,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
}: SessionListProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [searchDraft, setSearchDraft] = useState(search.query);
  const composingRef = useRef(false);
  const searchAgentRef = useRef(activeAgentId);
  useEffect(() => {
    if (searchAgentRef.current !== activeAgentId) {
      searchAgentRef.current = activeAgentId;
      composingRef.current = false;
    }
    if (!composingRef.current) setSearchDraft(search.query);
  }, [search.query, activeAgentId]);
  const [showingHidden, setShowingHidden] = useState(false);
  const { filterVisible, pickHidden, hide, unhide, canHide } =
    useHiddenSharedExperts();

  const hiddenAgents = useMemo(
    () => [...pickHidden(agents)].sort((a, b) => b.id - a.id),
    [agents, pickHidden],
  );

  const sortedAgents = useMemo(() => {
    const visible = filterVisible(agents, {
      keepAgentIds: activeAgentId ? [activeAgentId] : [],
    });
    return [...visible].sort((a, b) => b.id - a.id);
  }, [agents, filterVisible, activeAgentId]);

  // Leave the hidden-only view once nothing remains hidden.
  const viewingHidden = showingHidden && hiddenAgents.length > 0;
  const agentsToRender = viewingHidden ? hiddenAgents : sortedAgents;

  const expandedAgentId = useMemo(
    () =>
      viewingHidden ? null : activeAgentId ?? sortedAgents[0]?.agent_id ?? null,
    [activeAgentId, sortedAgents, viewingHidden],
  );
  const activeAgent = useMemo(
    () => sortedAgents.find((a) => a.agent_id === expandedAgentId) ?? null,
    [sortedAgents, expandedAgentId],
  );
  const showSessions = isAgentChatReady(activeAgent?.state);

  return (
    <div className={styles.sessionList}>
      {showSessions ? (
        <div className={styles.sessionSearchWrap}>
          <Search
            size={14}
            className={styles.sessionSearchIcon}
            strokeWidth={2}
          />
          <input
            type="search"
            className={styles.sessionSearchInput}
            value={searchDraft}
            maxLength={256}
            onChange={(e) => {
              setSearchDraft(e.target.value);
              if (!composingRef.current) onSearchChange(e.target.value);
            }}
            onCompositionStart={() => {
              composingRef.current = true;
            }}
            onCompositionEnd={(e) => {
              composingRef.current = false;
              onSearchChange(e.currentTarget.value);
            }}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                composingRef.current = false;
                setSearchDraft("");
                onSearchChange("");
              }
            }}
            placeholder={t("chat.searchSessions", "搜索会话")}
            aria-label={t("chat.searchSessions", "搜索会话")}
          />
          {searchDraft ? (
            <button
              type="button"
              className={styles.agentHideBtn}
              aria-label={t("chat.clearSearch")}
              onClick={() => {
                composingRef.current = false;
                setSearchDraft("");
                onSearchChange("");
              }}
            >
              <X size={14} aria-hidden />
            </button>
          ) : null}
        </div>
      ) : null}

      {agents.length === 0 ? (
        <div className={styles.sessionEmptyAgents}>
          <p className={styles.sessionEmptyAgentsText}>
            {t("chat.noAgentsHint")}
          </p>
          <button
            type="button"
            className={styles.sessionEmptyAgentsLink}
            onClick={() => navigate("/experts")}
          >
            {t("chat.createExpert")}
          </button>
        </div>
      ) : (
        <div className={styles.sessionItems}>
          {viewingHidden
            ? hiddenAgents.map((agent) => (
                <InactiveAgentRow
                  key={agent.agent_id}
                  agent={agent}
                  onSelect={() => {
                    unhide(agent.agent_id);
                    setShowingHidden(false);
                    onAgentSelect(agent.agent_id);
                  }}
                  onUnhide={() => unhide(agent.agent_id)}
                />
              ))
            : agentsToRender.map((agent) => {
                const expanded = agent.agent_id === expandedAgentId;
                if (expanded) {
                  return (
                    <ActiveAgentCard
                      key={agent.agent_id}
                      agent={agent}
                      sessions={sessions}
                      activeId={activeId}
                      search={search}
                      hasMore={search.query.trim() ? search.hasMore : hasMore}
                      loadingMore={
                        search.query.trim() ? search.loading : loadingMore
                      }
                      onLoadMore={
                        search.query.trim() ? onLoadMoreSearch : onLoadMore
                      }
                      onRetrySearch={onRetrySearch}
                      onSelect={onSelect}
                      onNewChat={onNewChat}
                      onDelete={onDelete}
                      onRename={onRename}
                      onPin={onPin}
                      onFork={onFork}
                      activeForkDisabled={activeForkDisabled}
                      activeForkDisabledHint={activeForkDisabledHint}
                      onHide={
                        canHide(agent) ? () => hide(agent.agent_id) : undefined
                      }
                    />
                  );
                }
                return (
                  <InactiveAgentRow
                    key={agent.agent_id}
                    agent={agent}
                    onSelect={() => onAgentSelect(agent.agent_id)}
                    onNewChat={() => onNewChat(agent.agent_id)}
                    onHide={
                      canHide(agent) ? () => hide(agent.agent_id) : undefined
                    }
                  />
                );
              })}
          {hiddenAgents.length > 0 ? (
            <button
              type="button"
              className={styles.expertHiddenToggle}
              onClick={() => {
                if (!viewingHidden) {
                  composingRef.current = false;
                  setSearchDraft("");
                  onSearchChange("");
                }
                setShowingHidden((v) => !v);
              }}
            >
              {viewingHidden
                ? t("chat.expertListShowVisible")
                : t("chat.expertListHidden", { count: hiddenAgents.length })}
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}
