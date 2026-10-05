import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import {
  octopThreadsApi,
  type ThreadArchivePage,
} from "../../../api/modules/octopThreads";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import {
  archiveMutationPending,
  onArchiveSaved,
  persistSessionArchive,
  useArchiveMutationRevision,
} from "../../Chat/hooks/useSessions";
import styles from "./index.module.less";

type ArchiveItem = ThreadArchivePage["items"][number];
type View = {
  context: object;
  rows: ArchiveItem[];
  hasMore: boolean;
  loading: boolean;
  failed: boolean;
};
const PAGE_SIZE = 20;
const EMPTY_ROWS: ArchiveItem[] = [];

export default function DataManagementPage() {
  const { t } = useTranslation();
  const actorId = useCurrentUser()?.id ?? null;
  useArchiveMutationRevision();
  const timeZone = useServerTimezone();
  const identity = useRef({ actorId, token: {} });
  if (identity.current.actorId !== actorId) {
    identity.current = { actorId, token: {} };
  }
  const context = identity.current.token;
  const mounted = useRef(false);
  const sequence = useRef(0);
  const query = useRef({ value: "", token: {} });
  const [draft, setDraft] = useState("");
  const [view, setView] = useState<View>({
    context,
    rows: [],
    hasMore: false,
    loading: false,
    failed: false,
  });
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [restoreError, setRestoreError] = useState(false);
  const [searchError, setSearchError] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const composing = useRef(false);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const focusAfterRestore = useRef<{
    context: object;
    query: object;
    index: number;
  } | null>(null);
  const currentView = view.context === context ? view : null;
  const rows = currentView?.rows ?? EMPTY_ROWS;
  const rowsRef = useRef(rows);
  rowsRef.current = rows;

  const load = useCallback(
    async (offset = 0, preserve = false) => {
      if (actorId === null) return;
      const requestContext = context;
      const requestQuery = query.current;
      const requestSequence = ++sequence.current;
      const current = () =>
        mounted.current &&
        identity.current.token === requestContext &&
        query.current === requestQuery &&
        sequence.current === requestSequence;
      setView((previous) => ({
        context: requestContext,
        rows:
          (offset || preserve) && previous.context === requestContext
            ? previous.rows
            : [],
        hasMore:
          offset && previous.context === requestContext
            ? previous.hasMore
            : false,
        loading: true,
        failed: false,
      }));
      try {
        const page = await octopThreadsApi.listArchived({
          q: requestQuery.value,
          limit: PAGE_SIZE,
          offset,
        });
        if (!current()) return;
        setView((previous) => ({
          context: requestContext,
          rows: offset ? [...previous.rows, ...page.items] : page.items,
          hasMore: page.has_more,
          loading: false,
          failed: false,
        }));
      } catch {
        if (!current()) return;
        setView((previous) => ({ ...previous, loading: false, failed: true }));
      }
    },
    [actorId, context],
  );

  useEffect(() => {
    mounted.current = true;
    query.current = { value: "", token: {} };
    setDraft("");
    setPending(new Set());
    setRestoreError(false);
    setSearchError(false);
    const unsubscribe = onArchiveSaved((event) => {
      if (event.actorId !== actorId || identity.current.token !== context)
        return;
      // Every save starts a new read; an earlier page cannot resurrect a restored row.
      if (event.archivedAt === null) {
        setView((previous) => ({
          ...previous,
          rows: previous.rows.filter((row) => row.thread_id !== event.id),
        }));
      }
      void load(0, true);
    });
    void load();
    return () => {
      mounted.current = false;
      unsubscribe();
    };
  }, [actorId, context, load]);

  useLayoutEffect(() => {
    const focus = focusAfterRestore.current;
    if (!focus) return;
    focusAfterRestore.current = null;
    if (focus.context !== context || focus.query !== query.current) return;
    const adjacent = rows[Math.min(focus.index, rows.length - 1)];
    const target = adjacent ? buttons.current.get(adjacent.thread_id) : null;
    if (target && !target.disabled) target.focus();
    else searchRef.current?.focus();
  }, [context, rows]);

  const search = (value: string) => {
    if (value.length > 256 || value.includes("\0")) {
      setSearchError(true);
      return;
    }
    setSearchError(false);
    query.current = { value, token: {} };
    setRestoreError(false);
    void load();
  };

  const restore = async (item: ArchiveItem) => {
    if (
      actorId === null ||
      archiveMutationPending(actorId, item.agent_id, item.thread_id)
    )
      return;
    const requestQuery = query.current;
    const index = rowsRef.current.findIndex(
      (row) => row.thread_id === item.thread_id,
    );
    const current = () =>
      mounted.current &&
      identity.current.token === context &&
      query.current === requestQuery;
    setPending((previous) => new Set(previous).add(item.thread_id));
    setRestoreError(false);
    try {
      const result = await persistSessionArchive({
        actorId,
        agentId: item.agent_id,
        id: item.thread_id,
        archived: false,
        isCurrent: current,
        isIdentityCurrent: () =>
          mounted.current && identity.current.token === context,
        onError: () => {
          if (current()) setRestoreError(true);
        },
      });
      if (result.status !== "saved" || !current()) return;
      focusAfterRestore.current = { context, query: requestQuery, index };
      setView((previous) => ({
        ...previous,
        rows: previous.rows.filter((row) => row.thread_id !== item.thread_id),
      }));
    } finally {
      if (mounted.current && identity.current.token === context) {
        setPending((previous) => {
          const next = new Set(previous);
          next.delete(item.thread_id);
          return next;
        });
      }
    }
  };

  return (
    <section className={styles.page} aria-label={t("dataManagement.title")}>
      <header>
        <h1>{t("dataManagement.title")}</h1>
        <p>{t("dataManagement.description")}</p>
      </header>
      <h2>{t("dataManagement.archived")}</h2>
      <form
        className={styles.search}
        onSubmit={(event) => {
          event.preventDefault();
          if (!composing.current) search(draft);
        }}
      >
        <input
          ref={searchRef}
          aria-label={t("dataManagement.search")}
          placeholder={t("dataManagement.searchPlaceholder")}
          maxLength={256}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onCompositionStart={() => {
            composing.current = true;
          }}
          onCompositionEnd={() => {
            composing.current = false;
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              (event.nativeEvent.isComposing || composing.current)
            )
              event.preventDefault();
          }}
        />
        <button type="submit" disabled={actorId === null}>
          {t("dataManagement.search")}
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft("");
            search("");
          }}
          disabled={actorId === null}
        >
          {t("dataManagement.clear")}
        </button>
      </form>
      {restoreError && <p role="alert">{t("dataManagement.restoreFailed")}</p>}
      {searchError && <p role="alert">{t("dataManagement.searchInvalid")}</p>}
      {currentView?.failed && (
        <div className={styles.error} role="alert">
          <span>{t("dataManagement.loadFailed")}</span>
          <button
            type="button"
            onClick={() =>
              void load(rows.length && currentView.hasMore ? rows.length : 0)
            }
          >
            {t("dataManagement.retry")}
          </button>
        </div>
      )}
      <ul className={styles.list}>
        {rows.map((item) => {
          const title = item.title || t("dataManagement.untitled");
          const busy =
            pending.has(item.thread_id) ||
            archiveMutationPending(actorId, item.agent_id, item.thread_id);
          return (
            <li key={item.thread_id} className={styles.row}>
              <div className={styles.details}>
                <strong>{title}</strong>
                <span>
                  {t(
                    item.mode === "files"
                      ? "dataManagement.files"
                      : "dataManagement.chat",
                  )}
                </span>
                <span>
                  {t("dataManagement.archivedAt")}{" "}
                  {formatServerDateTime(item.archived_at, timeZone)}
                </span>
              </div>
              <button
                ref={(button) => {
                  if (button) buttons.current.set(item.thread_id, button);
                  else buttons.current.delete(item.thread_id);
                }}
                type="button"
                disabled={busy}
                aria-label={t("dataManagement.restoreNamed", { title })}
                onClick={() => void restore(item)}
              >
                {t(
                  busy ? "dataManagement.restoring" : "dataManagement.restore",
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <div aria-live="polite">
        {currentView?.loading && (
          <p role="status">{t("dataManagement.loading")}</p>
        )}
        {!currentView?.loading && !currentView?.failed && rows.length === 0 && (
          <p>{t("dataManagement.empty")}</p>
        )}
      </div>
      {currentView?.hasMore && !currentView.failed && (
        <button
          type="button"
          disabled={currentView.loading}
          onClick={() => void load(rows.length)}
        >
          {t("dataManagement.loadMore")}
        </button>
      )}
    </section>
  );
}
