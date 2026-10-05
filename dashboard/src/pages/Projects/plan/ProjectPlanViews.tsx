import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Alert, Button, Input, Modal, Select, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";
import {
  projectPlanViewsApi,
  normalizePlanDefinition,
  type AnyPlanDefinition,
  type PlanQueryGroupKey,
  type PlanQueryWindow,
  type PlanView,
  type PlanViewType,
  type PlanViewListResponse,
  type PlanViewMutationResponse,
  type PlanViewCreateBody,
} from "../../../api/modules/projectPlanViews";
import type {
  ProjectTodo,
  ProjectTodoBulkBody,
  ProjectTodoBulkItem,
  ProjectTodoUpdateBody,
} from "../../../api/modules/projectTodos";
import type { ProjectMember, ProjectRole } from "../../../api/modules/projects";
import type { ProjectTodoCatalog } from "../../../api/modules/projectTodoCatalog";
import { projectTodosApi } from "../../../api/modules/projectTodos";
import { projectsApi } from "../../../api/modules/projects";
import {
  useProjectPlanQuery,
  planErrorKey,
  planHttpStatus,
} from "./useProjectPlanQuery";
import {
  clearSelectedViewId,
  readSelectedViewId,
  writeSelectedViewId,
  type PlanSelectionStorage,
} from "./planViewSelection";
import { addPlanDays, isPlanDate, planMonthGrid } from "../planDates";
import { clipPlanWindow, planWeekGrid } from "./planViewDates";
import PlanViewManager from "./PlanViewManager";
import PlanViewSettings from "./PlanViewSettings";
import PlanList from "./PlanList";
import PlanTable from "./PlanTable";
import PlanBoard from "./PlanBoard";
import PlanGantt from "./PlanGantt";
import PlanCalendar from "./PlanCalendar";
import styles from "./ProjectPlanViews.module.less";
import {
  compareTodoSnapshot,
  uniqueTodoSnapshots,
  validTodoSnapshot,
} from "./todoSnapshot";

export interface PlanOperationScope {
  readonly lifetime: number;
  readonly accountId: number;
  readonly projectId: string;
  readonly viewId: string;
  readonly queryGeneration: number;
  readonly channel: string;
  readonly operationGeneration: number;
}
export interface PlanLaneState {
  readonly laneId: string;
  readonly groupKey: PlanQueryGroupKey | null;
  readonly bucket: "scheduled" | "unscheduled" | null;
  readonly items: readonly ProjectTodo[];
  readonly serverCount: number;
  readonly nextCursor: string | null;
  readonly queryFingerprint: string | null;
  readonly loadingFirst: boolean;
  readonly loadingMore: boolean;
  readonly errorKey: string | null;
  readonly paused: boolean;
}
export interface PlanTodoPatchProposal {
  readonly changes: Omit<
    ProjectTodoUpdateBody,
    "expected_version" | "expected_catalog_revision"
  >;
  readonly baseVersion: number;
  readonly catalogRevision?: number;
}
export type PlanTodoMutationResult =
  | { status: "saved"; todo: ProjectTodo }
  | { status: "conflict"; current?: ProjectTodo; messageKey: string }
  | {
      status: "forbidden" | "invalid" | "stale" | "failed";
      messageKey: string;
    };
export type PlanTodoCompareResult =
  | {
      state: "ready";
      todo: ProjectTodo;
      catalogRevision: number;
      serverToday: string;
      serverTimezone: string;
    }
  | { state: "stale" | "forbidden" | "failed"; messageKey?: string };
export interface PlanViewActionBaseline {
  readonly view: PlanView;
  readonly collectionRevision: number;
  readonly catalogRevision: number;
}
export interface PlanViewCollectionBaseline {
  readonly revision: number;
  readonly catalogRevision: number;
}
export type PlanViewActionResult =
  | { state: "saved"; view?: PlanView }
  | {
      state: "conflict" | "forbidden" | "invalid" | "stale" | "failed";
      messageKey: string;
    };
export interface PlanViewComparison {
  readonly baseline: PlanViewActionBaseline;
  readonly scope: PlanOperationScope;
}
export type PlanViewCompareResult =
  | { state: "ready"; comparison: PlanViewComparison }
  | { state: "stale" | "forbidden" | "failed"; messageKey: string };
export interface PlanViewCreateAction {
  readonly baseline: PlanViewCollectionBaseline;
  readonly name: string;
  readonly type: PlanViewType;
  readonly definition: AnyPlanDefinition;
}
export interface PlanViewRenameAction {
  readonly baseline: PlanViewActionBaseline;
  readonly name: string;
}
export interface PlanViewChangeTypeAction {
  readonly baseline: PlanViewActionBaseline;
  readonly type: PlanViewType;
  readonly definition: AnyPlanDefinition;
}
export interface PlanViewOrderAction {
  readonly baseline: PlanViewCollectionBaseline;
  readonly viewIds: readonly string[];
}
export interface PlanViewDefaultAction {
  readonly baseline: PlanViewCollectionBaseline;
  readonly viewId: string;
}
export interface PlanViewSaveAction {
  readonly baseline: PlanViewActionBaseline;
  readonly definition: AnyPlanDefinition;
}
export interface ProjectPlanViewsHandle {
  captureOperation(channel: string): PlanOperationScope | null;
  isCurrentOperation(scope: PlanOperationScope): boolean;
  acceptTodo(
    scope: PlanOperationScope,
    todo: ProjectTodo,
    catalog?: ProjectTodoCatalog,
  ): Promise<boolean>;
  acceptTodos(
    scope: PlanOperationScope,
    todos: readonly ProjectTodo[],
  ): Promise<boolean>;
  getKnownTodo(todoId: string): ProjectTodo | undefined;
  hasAcceptedTodo(scope: PlanOperationScope, todo: ProjectTodo): boolean;
  refreshCurrent(): Promise<boolean>;
  refreshAfterTodos(
    scope: PlanOperationScope,
    todos: readonly ProjectTodo[],
  ): Promise<boolean>;
  refreshTodoCompare(todo: ProjectTodo): Promise<PlanTodoCompareResult>;
  clearPrivate(): void;
}
export interface ProjectPlanViewsProps {
  accountId: number;
  projectId: string;
  role: ProjectRole;
  members: readonly ProjectMember[];
  catalog: ProjectTodoCatalog | null;
  catalogLoading: boolean;
  catalogError: unknown;
  onCatalogRetry(): Promise<boolean>;
  onCatalogSnapshot?(catalog: ProjectTodoCatalog): void;
  selectedTodoId?: string | null;
  canEdit(todo: ProjectTodo): boolean;
  canDelete(todo: ProjectTodo): boolean;
  onCreateTodo(scope: PlanOperationScope): void;
  onEditTodo(todo: ProjectTodo, scope: PlanOperationScope): void;
  onDeleteTodo(todo: ProjectTodo, scope: PlanOperationScope): void;
  onOpenTodo(
    todo: ProjectTodo,
    trigger: HTMLElement | null,
    scope: PlanOperationScope,
  ): void;
  onOpenParentTodo(
    todoId: string,
    trigger: HTMLElement | null,
    scope: PlanOperationScope,
  ): void;
  onProposeTodoPatch(
    todo: ProjectTodo,
    proposal: PlanTodoPatchProposal,
    scope: PlanOperationScope,
  ): Promise<PlanTodoMutationResult>;
  onBulkTodo(
    items: readonly ProjectTodoBulkItem[],
    changes: Omit<ProjectTodoBulkBody, "items">,
    scope: PlanOperationScope,
  ): Promise<void>;
  onLoadedTodosChanged(
    todos: readonly ProjectTodo[],
    scope: PlanOperationScope,
  ): void;
  onProjectAccessLost(): void;
}
export interface PlanRendererProps {
  readonly accountId: number;
  readonly projectId: string;
  readonly view: PlanView;
  readonly definition: AnyPlanDefinition;
  readonly lanes: readonly PlanLaneState[];
  readonly total: number;
  readonly matchedTotal: number;
  readonly unscheduledTotal: number | null;
  readonly catalog: ProjectTodoCatalog | null;
  readonly members: readonly ProjectMember[];
  readonly serverToday: string | null;
  readonly serverTimezone: string | null;
  readonly window: PlanQueryWindow | null;
  readonly isManager: boolean;
  canEdit(todo: ProjectTodo): boolean;
  canDelete(todo: ProjectTodo): boolean;
  onOpenTodo(todo: ProjectTodo, trigger: HTMLElement | null): void;
  onOpenParentTodo(todoId: string, trigger: HTMLElement | null): void;
  onEditTodo(todo: ProjectTodo): void;
  onDeleteTodo(todo: ProjectTodo): void;
  onProposeTodoPatch(
    todo: ProjectTodo,
    proposal: PlanTodoPatchProposal,
  ): Promise<PlanTodoMutationResult>;
  onRefreshTodoCompare(todo: ProjectTodo): Promise<PlanTodoCompareResult>;
  onBulkTodo(
    items: readonly ProjectTodoBulkItem[],
    changes: Omit<ProjectTodoBulkBody, "items">,
  ): Promise<void>;
  onLoadFirst(laneId: string): Promise<boolean>;
  onLoadMore(laneId: string): Promise<boolean>;
  onWindowChange(window: PlanQueryWindow): void;
  onTemporaryDefinitionChange(definition: AnyPlanDefinition): void;
}
interface ViewDraft {
  baseline: PlanViewActionBaseline;
  definition: AnyPlanDefinition;
  locked: boolean;
  comparison: PlanViewComparison | null;
}
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
const same = (a: unknown, b: unknown) =>
  JSON.stringify(a) === JSON.stringify(b);
const staleView = (): PlanViewActionResult => ({
  state: "stale",
  messageKey: "projects.planViews.stale",
});
const staleTodo = (): PlanTodoMutationResult => ({
  status: "stale",
  messageKey: "projects.planViews.stale",
});
const storage = (): PlanSelectionStorage | null => {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
};
const definitionMatches = (type: PlanViewType, definition: AnyPlanDefinition) =>
  (type === "table"
    ? typeof definition.show_subtodos === "boolean"
    : !("show_subtodos" in definition)) &&
  definition.schema_version === 1 &&
  definition.fields[0] === "title" &&
  new Set(definition.fields).size === definition.fields.length &&
  definition.filters.length <= 12 &&
  definition.sort.length <= 3 &&
  (type === "gantt"
    ? definition.group_by === null && !!definition.gantt && !definition.calendar
    : type === "calendar"
    ? definition.group_by === null && !!definition.calendar && !definition.gantt
    : !definition.gantt &&
      !definition.calendar &&
      (type !== "board" || definition.group_by !== null));
const initialWindow = (
  view: PlanView,
  definition: AnyPlanDefinition,
  today: string | null,
): PlanQueryWindow | null => {
  if (!isPlanDate(today)) return null;
  if (view.type === "gantt")
    return clipPlanWindow(addPlanDays(today, -14), addPlanDays(today, 42));
  if (view.type === "calendar") {
    const grid =
      definition.calendar?.mode === "week"
        ? planWeekGrid(today)
        : planMonthGrid(today.slice(0, 7));
    const valid = grid.filter((day): day is string => day !== null);
    return valid.length
      ? clipPlanWindow(valid[0], valid[valid.length - 1])
      : null;
  }
  return null;
};
const completeTodo = (todo: ProjectTodo, projectId: string, todoId: string) =>
  validTodoSnapshot(todo, projectId, todoId) &&
  todo.project_id === projectId &&
  todo.todo_id === todoId &&
  Number.isSafeInteger(todo.version) &&
  todo.version > 0 &&
  Number.isSafeInteger(todo.catalog_revision) &&
  todo.catalog_revision > 0 &&
  typeof todo.title === "string" &&
  typeof todo.description === "string" &&
  ["plain", "markdown"].includes(todo.description_format) &&
  ["todo", "in_progress", "done"].includes(todo.status) &&
  (todo.start_date === null || isPlanDate(todo.start_date)) &&
  (todo.due_date === null || isPlanDate(todo.due_date)) &&
  (todo.priority_id === null || typeof todo.priority_id === "string") &&
  Array.isArray(todo.tag_ids) &&
  Number.isSafeInteger(todo.creator_user_id) &&
  (todo.assignee_user_id === null ||
    Number.isSafeInteger(todo.assignee_user_id)) &&
  Number.isFinite(todo.created_at) &&
  Number.isFinite(todo.updated_at);

const ProjectPlanViewsContent = forwardRef<
  ProjectPlanViewsHandle,
  ProjectPlanViewsProps
>(function ProjectPlanViewsContent(props, ref) {
  const { t } = useTranslation();
  const callbacks = useRef(props);
  callbacks.current = props;
  const active = useRef(true);
  const userEpoch = useRef(0);
  const shellOperations = useRef(
    new Map<string, { generation: number; controller: AbortController }>(),
  );
  const commitListeners = useRef(new Set<() => void>());
  const acceptedCatalog = useRef({ data: props.catalog, serial: 0 });
  const [viewList, setViewList] = useState<PlanViewListResponse | null>(null);
  const viewListRef = useRef(viewList);
  viewListRef.current = viewList;
  const [selectedViewId, setSelectedViewId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ViewDraft | null>(null);
  const [queryWindow, setQueryWindow] = useState<PlanQueryWindow | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [pendingViewId, setPendingViewId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [accessLost, setAccessLost] = useState(false);
  const [manualRefreshing, setManualRefreshing] = useState(false);
  const [quickSearch, setQuickSearch] = useState("");
  const [quickStatus, setQuickStatus] = useState("all");
  const [quickAssignee, setQuickAssignee] = useState<number | "all">("all");
  const quickBase = useRef<AnyPlanDefinition | null>(null);
  const accessKey = JSON.stringify([
    props.role,
    [...props.members]
      .sort((a, b) => a.user_id - b.user_id)
      .map((member) => [member.user_id, member.username, member.role]),
  ]);
  const accessFrame = useRef(accessKey);
  const view =
    viewList?.items.find(
      (item) => item.view_id === selectedViewId && item.archived_at === null,
    ) ?? null;
  const definition =
    draft?.baseline.view.view_id === selectedViewId
      ? draft.definition
      : view
      ? normalizePlanDefinition(view.type, view.definition)
      : null;
  const isManager = props.role === "owner" || props.role === "admin";
  const catalogReady =
    !!props.catalog &&
    props.catalog.project_id === props.projectId &&
    !props.catalogLoading &&
    !props.catalogError;
  const draftDirty =
    !!draft &&
    !same(
      draft.definition,
      normalizePlanDefinition(
        draft.baseline.view.type,
        draft.baseline.view.definition,
      ),
    );
  const current = useRef({
    view,
    definition,
    draft,
    queryWindow,
    catalogReady,
    selectedViewId,
    isManager,
    manualRefreshing,
  });
  current.current = {
    view,
    definition,
    draft,
    queryWindow,
    catalogReady,
    selectedViewId,
    isManager,
    manualRefreshing,
  };
  const beginShellOperation = (channel: string) => {
    const old = shellOperations.current.get(channel);
    old?.controller.abort();
    const operation = {
      generation: (old?.generation ?? 0) + 1,
      controller: new AbortController(),
    };
    shellOperations.current.set(channel, operation);
    const epoch = userEpoch.current;
    return {
      controller: operation.controller,
      valid: () =>
        active.current &&
        epoch === userEpoch.current &&
        shellOperations.current.get(channel) === operation &&
        !operation.controller.signal.aborted,
    };
  };
  const touchUserFrame = () => {
    userEpoch.current += 1;
    for (const operation of shellOperations.current.values())
      operation.controller.abort();
    setBusy(false);
    setManualRefreshing(false);
    setListLoading(false);
  };
  const resetQuickFilters = () => {
    quickBase.current = null;
    setQuickSearch("");
    setQuickStatus("all");
    setQuickAssignee("all");
  };
  const clearPrivate = () => {
    touchUserFrame();
    queryRef.current.clear();
    clearSelectedViewId(storage(), props.accountId, props.projectId);
    viewListRef.current = null;
    setViewList(null);
    setSelectedViewId(null);
    setDraft(null);
    setQueryWindow(null);
    setSettingsOpen(false);
    setPendingViewId(null);
    setBusy(false);
    resetQuickFilters();
    setAccessLost(true);
  };
  const notifyAccessLost = () => {
    clearPrivate();
    callbacks.current.onProjectAccessLost();
  };
  const selectView = (id: string) => {
    const next = viewListRef.current?.items.find(
      (item) =>
        item.view_id === id &&
        item.archived_at === null &&
        item.project_id === props.projectId,
    );
    if (!next || current.current.selectedViewId === id) return;
    touchUserFrame();
    setSelectedViewId(id);
    setDraft(null);
    setSettingsOpen(false);
    setPendingViewId(null);
    setQueryWindow(null);
    resetQuickFilters();
    setErrorKey(null);
    writeSelectedViewId(
      storage(),
      props.accountId,
      props.projectId,
      id,
      viewListRef.current!.items,
    );
  };
  const acceptList = (response: PlanViewListResponse) => {
    if (
      response.project_id !== props.projectId ||
      response.items.some((item) => item.project_id !== props.projectId)
    )
      throw new Error("Invalid plan view project");
    const previous = viewListRef.current;
    if (previous && response.revision < previous.revision) return previous;
    const items = response.items.map((incoming) => {
      const known = previous?.items.find(
        (item) => item.view_id === incoming.view_id,
      );
      return known && known.version > incoming.version ? known : incoming;
    });
    const next = { ...response, items };
    viewListRef.current = next;
    setViewList(next);
    return next;
  };
  const acceptMutation = (response: PlanViewMutationResponse) => {
    if (response.item.project_id !== props.projectId)
      throw new Error("Invalid plan view project");
    const previous = viewListRef.current;
    if (!previous) return;
    const known = previous.items.find(
      (item) => item.view_id === response.item.view_id,
    );
    const item =
      known && known.version > response.item.version ? known : response.item;
    const items = previous.items.some((row) => row.view_id === item.view_id)
      ? previous.items.map((row) => (row.view_id === item.view_id ? item : row))
      : [...previous.items, item];
    const next = {
      ...previous,
      revision: Math.max(previous.revision, response.revision),
      default_view_id:
        response.revision >= previous.revision
          ? response.default_view_id
          : previous.default_view_id,
      items,
    };
    viewListRef.current = next;
    setViewList(next);
  };
  const loadViews = async (fallback = false) => {
    const operation = beginShellOperation("view-list");
    setListLoading(true);
    try {
      const response = await projectPlanViewsApi.list(props.projectId, {
        signal: operation.controller.signal,
      });
      if (!operation.valid()) return false;
      const accepted = acceptList(response);
      setAccessLost(false);
      setErrorKey(null);
      setListLoading(false);
      const stillActive = accepted.items.some(
        (item) =>
          item.view_id === current.current.selectedViewId &&
          item.archived_at === null,
      );
      if (fallback || !stillActive) {
        const chosen = readSelectedViewId(
          storage(),
          props.accountId,
          props.projectId,
          accepted.items,
          accepted.default_view_id,
        );
        if (chosen !== current.current.selectedViewId) {
          touchUserFrame();
          setSelectedViewId(chosen);
          setDraft(null);
          setSettingsOpen(false);
          setQueryWindow(null);
          resetQuickFilters();
          if (chosen)
            writeSelectedViewId(
              storage(),
              props.accountId,
              props.projectId,
              chosen,
              accepted.items,
            );
        }
      }
      return true;
    } catch (error: unknown) {
      if (!operation.valid()) return false;
      if (planHttpStatus(error) === 404) notifyAccessLost();
      else setErrorKey(planErrorKey(error));
      return false;
    } finally {
      if (operation.valid()) setListLoading(false);
    }
  };
  const query = useProjectPlanQuery({
    accountId: props.accountId,
    projectId: props.projectId,
    view,
    catalogRevision:
      props.catalog?.project_id === props.projectId
        ? props.catalog.revision
        : null,
    overrideDefinition: draft ? draft.definition : undefined,
    window: queryWindow,
    enabled: !manualRefreshing || accessFrame.current !== accessKey,
    accessKey,
    onProjectAccessLost: notifyAccessLost,
    onViewUnavailable: () => {
      clearSelectedViewId(storage(), props.accountId, props.projectId);
      void loadViews(true);
    },
    onCatalogSnapshot: (catalog) =>
      callbacks.current.onCatalogSnapshot?.(catalog),
  });
  const queryRef = useRef(query);
  queryRef.current = query;
  const rendererScope = useRef<PlanOperationScope | null>(null);
  if (
    !rendererScope.current ||
    !query.isCurrentOperation(rendererScope.current)
  )
    rendererScope.current = query.captureOperation("renderer-frame");
  const boundScope = rendererScope.current;
  const waitForCommit = (
    predicate: () => boolean,
    signal: AbortSignal,
  ): Promise<boolean> => {
    if (signal.aborted || !active.current) return Promise.resolve(false);
    if (predicate()) return Promise.resolve(true);
    return new Promise((resolve) => {
      const finish = (ok: boolean) => {
        clearTimeout(timer);
        commitListeners.current.delete(check);
        signal.removeEventListener("abort", abort);
        resolve(ok);
      };
      const check = () => {
        if (signal.aborted || !active.current) finish(false);
        else if (predicate()) finish(true);
      };
      const abort = () => finish(false);
      const timer = setTimeout(() => finish(false), 1000);
      commitListeners.current.add(check);
      signal.addEventListener("abort", abort, { once: true });
    });
  };
  const refreshCurrent = async (): Promise<boolean> => {
    const original = queryRef.current.captureOperation("manual-refresh");
    if (!original || !current.current.view) return false;
    const operation = beginShellOperation("manual-refresh");
    const selected = original.viewId;
    const valid = () =>
      operation.valid() && queryRef.current.isSameContextOperation(original);
    const before = acceptedCatalog.current.serial;
    queryRef.current.pauseForMetadata();
    setManualRefreshing(true);
    setErrorKey(null);
    try {
      const retried = await callbacks.current.onCatalogRetry();
      if (retried !== true || !valid()) return false;
      if (
        !(await waitForCommit(
          () =>
            acceptedCatalog.current.serial > before &&
            current.current.catalogReady,
          operation.controller.signal,
        )) ||
        !valid()
      )
        return false;
      const latest = await projectPlanViewsApi.get(props.projectId, selected, {
        signal: operation.controller.signal,
      });
      if (
        !valid() ||
        latest.project_id !== props.projectId ||
        latest.view_id !== selected
      )
        return false;
      const collection = await projectPlanViewsApi.list(props.projectId, {
        signal: operation.controller.signal,
      });
      if (!valid()) return false;
      const accepted = acceptList(collection);
      const known = accepted.items.find(
        (item) => item.view_id === latest.view_id,
      );
      const item = known && known.version > latest.version ? known : latest;
      const next = {
        ...accepted,
        items: accepted.items.map((row) =>
          row.view_id === item.view_id ? item : row,
        ),
      };
      viewListRef.current = next;
      setViewList(next);
      if (
        !(await waitForCommit(
          () => current.current.view?.version === item.version,
          operation.controller.signal,
        )) ||
        !valid()
      )
        return false;
      return (await queryRef.current.refresh()) && valid();
    } catch (error: unknown) {
      if (!valid()) return false;
      if (planHttpStatus(error) === 404) {
        try {
          await projectsApi.get(props.projectId);
          if (valid()) {
            clearSelectedViewId(storage(), props.accountId, props.projectId);
            await loadViews(true);
          }
        } catch (projectError: unknown) {
          if (valid() && planHttpStatus(projectError) === 404)
            notifyAccessLost();
        }
      } else setErrorKey(planErrorKey(error));
      return false;
    } finally {
      if (operation.valid()) setManualRefreshing(false);
    }
  };
  const refreshTodoCompare = async (
    todo: ProjectTodo,
    original: PlanOperationScope | null,
  ): Promise<PlanTodoCompareResult> => {
    if (
      !original ||
      !queryRef.current.isSameContextOperation(original) ||
      todo.project_id !== original.projectId
    )
      return { state: "stale" };
    if (
      !(await refreshCurrent()) ||
      !queryRef.current.isSameContextOperation(original)
    )
      return { state: "stale" };
    const scope = queryRef.current.captureOperation(
      `todo-compare:${todo.todo_id}`,
    );
    if (!scope) return { state: "stale" };
    try {
      const latest = await projectTodosApi.get(scope.projectId, todo.todo_id);
      if (!queryRef.current.isCurrentOperation(scope))
        return { state: "stale" };
      const metadata = queryRef.current.getAcceptedMetadata();
      const catalog = callbacks.current.catalog;
      const known = queryRef.current.getKnownTodo(todo.todo_id);
      if (
        !completeTodo(latest, scope.projectId, todo.todo_id) ||
        [todo, known].some(
          (accepted) =>
            accepted && compareTodoSnapshot(accepted, latest) !== "accept",
        ) ||
        latest.catalog_revision <
          Math.max(todo.catalog_revision, known?.catalog_revision ?? 0) ||
        !current.current.catalogReady ||
        !catalog ||
        latest.catalog_revision !== catalog.revision ||
        !metadata ||
        metadata.catalogRevision !== catalog.revision ||
        !isPlanDate(metadata.serverToday) ||
        !metadata.serverTimezone
      )
        return {
          state: "failed",
          messageKey: "projects.planViews.catalogUnavailable",
        };
      if (!(await queryRef.current.acceptTodo(scope, latest)))
        return { state: "failed" };
      return {
        state: "ready",
        todo: latest,
        catalogRevision: catalog.revision,
        serverToday: metadata.serverToday,
        serverTimezone: metadata.serverTimezone,
      };
    } catch (error: unknown) {
      if (!queryRef.current.isCurrentOperation(scope))
        return { state: "stale" };
      if (planHttpStatus(error) === 404) {
        try {
          await projectsApi.get(scope.projectId);
          if (!queryRef.current.isCurrentOperation(scope))
            return { state: "stale" };
        } catch (projectError: unknown) {
          if (!queryRef.current.isCurrentOperation(scope))
            return { state: "stale" };
          if (planHttpStatus(projectError) === 404) {
            notifyAccessLost();
            return { state: "stale" };
          }
          return {
            state:
              planHttpStatus(projectError) === 403 ? "forbidden" : "failed",
            messageKey: planErrorKey(projectError),
          };
        }
      }
      return {
        state: planHttpStatus(error) === 403 ? "forbidden" : "failed",
        messageKey: planErrorKey(error),
      };
    }
  };
  const refreshViewCompare = async (
    baseline: PlanViewActionBaseline,
  ): Promise<PlanViewCompareResult> => {
    if (
      !boundScope ||
      !queryRef.current.isSameContextOperation(boundScope) ||
      baseline.view.project_id !== props.projectId
    )
      return { state: "stale", messageKey: "projects.planViews.stale" };
    if (!(await refreshCurrent()))
      return {
        state: "failed",
        messageKey: "projects.planViews.catalogUnavailable",
      };
    const scope = queryRef.current.captureOperation(
      `view-compare:${baseline.view.view_id}`,
    );
    if (!scope)
      return { state: "stale", messageKey: "projects.planViews.stale" };
    try {
      const latest = await projectPlanViewsApi.get(
        props.projectId,
        baseline.view.view_id,
      );
      if (!queryRef.current.isCurrentOperation(scope))
        return { state: "stale", messageKey: "projects.planViews.stale" };
      const catalog = callbacks.current.catalog;
      if (
        latest.project_id !== props.projectId ||
        latest.view_id !== baseline.view.view_id ||
        !catalog ||
        !current.current.catalogReady
      )
        return {
          state: "failed",
          messageKey: "projects.planViews.catalogUnavailable",
        };
      const comparison = {
        scope,
        baseline: {
          view: copy(latest),
          collectionRevision: viewListRef.current!.revision,
          catalogRevision: catalog.revision,
        },
      };
      setDraft((old) =>
        old?.baseline.view.view_id === baseline.view.view_id
          ? { ...old, comparison, locked: true }
          : old,
      );
      return { state: "ready", comparison };
    } catch (error: unknown) {
      if (!queryRef.current.isCurrentOperation(scope))
        return { state: "stale", messageKey: "projects.planViews.stale" };
      if (planHttpStatus(error) === 404) {
        try {
          await projectsApi.get(scope.projectId);
          if (!queryRef.current.isCurrentOperation(scope))
            return { state: "stale", messageKey: "projects.planViews.stale" };
          await loadViews(true);
        } catch (projectError: unknown) {
          if (!queryRef.current.isCurrentOperation(scope))
            return { state: "stale", messageKey: "projects.planViews.stale" };
          if (planHttpStatus(projectError) === 404) {
            notifyAccessLost();
            return { state: "stale", messageKey: "projects.planViews.stale" };
          }
          return {
            state:
              planHttpStatus(projectError) === 403 ? "forbidden" : "failed",
            messageKey: planErrorKey(projectError),
          };
        }
        if (!queryRef.current.isCurrentOperation(scope))
          return { state: "stale", messageKey: "projects.planViews.stale" };
      }
      return {
        state: planHttpStatus(error) === 403 ? "forbidden" : "failed",
        messageKey: planErrorKey(error),
      };
    }
  };
  const confirmViewCompare = (comparison: PlanViewComparison) => {
    if (
      !queryRef.current.isCurrentOperation(comparison.scope) ||
      callbacks.current.catalog?.revision !==
        comparison.baseline.catalogRevision
    )
      return false;
    const known = viewListRef.current?.items.find(
      (item) => item.view_id === comparison.baseline.view.view_id,
    );
    if (known && known.version > comparison.baseline.view.version) return false;
    const old = current.current.draft;
    if (old?.baseline.view.view_id === comparison.baseline.view.view_id) {
      if (!definitionMatches(comparison.baseline.view.type, old.definition)) {
        setErrorKey("projects.planViews.invalidDefinition");
        return false;
      }
      setDraft({
        ...old,
        baseline: copy(comparison.baseline),
        comparison: null,
        locked: false,
      });
    }
    return true;
  };
  const viewWrite = async (
    channel: string,
    baseline: PlanViewActionBaseline | PlanViewCollectionBaseline,
    request: (signal: AbortSignal) => Promise<unknown>,
    onSuccess: (response: unknown) => PlanView | undefined,
  ): Promise<PlanViewActionResult> => {
    if (!boundScope || !queryRef.current.isCurrentOperation(boundScope))
      return staleView();
    if (!current.current.isManager)
      return {
        state: "forbidden",
        messageKey: "projects.planViews.managerRequired",
      };
    if ("view" in baseline) {
      const known = viewListRef.current?.items.find(
        (item) => item.view_id === baseline.view.view_id,
      );
      if (baseline.view.project_id !== props.projectId) return staleView();
      if (known && known.version > baseline.view.version)
        return {
          state: "conflict",
          messageKey: "projects.planViews.viewConflict",
        };
    }
    const scope = queryRef.current.captureOperation(channel);
    if (!scope) return staleView();
    const operation = beginShellOperation(channel);
    setBusy(true);
    setErrorKey(null);
    try {
      const response = await request(operation.controller.signal);
      if (!operation.valid() || !queryRef.current.isCurrentOperation(scope))
        return staleView();
      const item = onSuccess(response);
      setBusy(false);
      return { state: "saved", ...(item ? { view: item } : {}) };
    } catch (error: unknown) {
      if (!operation.valid() || !queryRef.current.isCurrentOperation(scope))
        return staleView();
      const status = planHttpStatus(error),
        key = planErrorKey(error);
      setErrorKey(key);
      if (status === 409 && "view" in baseline)
        setDraft((old) =>
          old?.baseline.view.view_id === baseline.view.view_id
            ? { ...old, locked: true, comparison: null }
            : old,
        );
      if (status === 404) {
        try {
          await projectsApi.get(props.projectId);
          if (operation.valid() && queryRef.current.isCurrentOperation(scope))
            await loadViews(true);
        } catch (projectError: unknown) {
          if (
            operation.valid() &&
            queryRef.current.isCurrentOperation(scope) &&
            planHttpStatus(projectError) === 404
          )
            notifyAccessLost();
        }
      }
      return {
        state:
          status === 409
            ? "conflict"
            : status === 403
            ? "forbidden"
            : status === 422
            ? "invalid"
            : "failed",
        messageKey: key,
      };
    } finally {
      if (operation.valid() && queryRef.current.isCurrentOperation(scope))
        setBusy(false);
    }
  };
  const saveDefinition = (action: PlanViewSaveAction) => {
    if (current.current.draft?.locked)
      return Promise.resolve<PlanViewActionResult>({
        state: "conflict",
        messageKey: "projects.planViews.viewConflict",
      });
    if (!definitionMatches(action.baseline.view.type, action.definition))
      return Promise.resolve<PlanViewActionResult>({
        state: "invalid",
        messageKey: "projects.planViews.invalidDefinition",
      });
    return viewWrite(
      `view-write:${action.baseline.view.view_id}`,
      action.baseline,
      (signal) =>
        projectPlanViewsApi.update(
          props.projectId,
          action.baseline.view.view_id,
          {
            expected_version: action.baseline.view.version,
            expected_catalog_revision: action.baseline.catalogRevision,
            definition: action.definition,
          },
          { signal },
        ),
      (response) => {
        const saved = response as PlanViewMutationResponse;
        acceptMutation(saved);
        setDraft(null);
        setSettingsOpen(false);
        resetQuickFilters();
        return saved.item;
      },
    );
  };
  const temporaryChange = (
    next: AnyPlanDefinition,
    fromQuickFilters = false,
  ) => {
    const selected = current.current.view;
    const catalog = callbacks.current.catalog;
    if (!selected || !catalog || !definitionMatches(selected.type, next)) {
      setErrorKey("projects.planViews.invalidDefinition");
      return;
    }
    touchUserFrame();
    setErrorKey(null);
    if (!fromQuickFilters) resetQuickFilters();
    setDraft((old) =>
      old
        ? { ...old, definition: copy(next), comparison: null }
        : {
            baseline: {
              view: copy(selected),
              collectionRevision: viewListRef.current!.revision,
              catalogRevision: catalog.revision,
            },
            definition: copy(next),
            comparison: null,
            locked: false,
          },
    );
  };
  const quickChange = (
    search: string,
    status: string,
    assignee: number | "all",
  ) => {
    const effective = quickBase.current ?? current.current.definition;
    if (!effective) return;
    const filters = [...effective.filters];
    const title = search.trim();
    if (title) filters.push({ field: "title", op: "contains", value: title });
    if (status !== "all")
      filters.push({
        field: "status",
        op: "in",
        values: [status as "todo" | "in_progress" | "done"],
      });
    if (assignee !== "all")
      filters.push({ field: "assignee", op: "in", values: [assignee] });
    if (filters.length > 12) {
      setErrorKey("projects.planViews.invalidDefinition");
      return;
    }
    quickBase.current ??= copy(effective);
    setQuickSearch(search);
    setQuickStatus(status);
    setQuickAssignee(assignee);
    temporaryChange({ ...effective, filters }, true);
  };
  useLayoutEffect(() => {
    if (acceptedCatalog.current.data !== props.catalog)
      acceptedCatalog.current = {
        data: props.catalog,
        serial: acceptedCatalog.current.serial + 1,
      };
    for (const listener of [...commitListeners.current]) listener();
  });
  useLayoutEffect(() => {
    if (accessFrame.current !== accessKey) {
      accessFrame.current = accessKey;
      touchUserFrame();
    }
  });
  useLayoutEffect(() => {
    if (view && definition && !queryWindow && catalogReady)
      setQueryWindow(
        initialWindow(view, definition, props.catalog!.server_today),
      );
  }, [view, definition, queryWindow, catalogReady, props.catalog]);
  useEffect(() => {
    const operations = shellOperations.current;
    const listeners = commitListeners.current;
    active.current = true;
    void loadViews();
    return () => {
      active.current = false;
      for (const operation of operations.values()) operation.controller.abort();
      for (const listener of [...listeners]) listener();
    };
    // The keyed component owns one account/project lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!draft || !view || draft.baseline.view.view_id !== view.view_id) return;
    if (
      view.version > draft.baseline.view.version ||
      (props.catalog && props.catalog.revision > draft.baseline.catalogRevision)
    )
      setDraft((old) =>
        old && !old.locked ? { ...old, locked: true, comparison: null } : old,
      );
  }, [view, props.catalog, draft]);
  const loadedTodos = useMemo(() => {
    return uniqueTodoSnapshots(query.lanes.flatMap((lane) => [...lane.items]));
  }, [query.lanes]);
  const loadedSignature = JSON.stringify(
    loadedTodos.map((todo) => [
      todo.todo_id,
      todo.version,
      todo.display_revision,
      todo.catalog_revision,
    ]),
  );
  const loadedFrame = boundScope
    ? JSON.stringify([boundScope.lifetime, boundScope.queryGeneration])
    : "none";
  useEffect(() => {
    const scope = queryRef.current.captureOperation("loaded-todos");
    if (scope && queryRef.current.isCurrentOperation(scope))
      callbacks.current.onLoadedTodosChanged(loadedTodos, scope);
    // Callback identity and row object identity must not create parent loops.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadedSignature, loadedFrame]);
  useImperativeHandle(ref, () => ({
    captureOperation: query.captureOperation,
    isCurrentOperation: query.isCurrentOperation,
    acceptTodo: query.acceptTodo,
    acceptTodos: query.acceptTodos,
    getKnownTodo: query.getKnownTodo,
    hasAcceptedTodo: query.hasAcceptedTodo,
    refreshCurrent,
    refreshAfterTodos: (scope, todos) => {
      if (!queryRef.current.isSameContextOperation(scope))
        return Promise.resolve(false);
      return todos.some((todo) =>
        queryRef.current.hasRefreshedTodo(scope, todo),
      )
        ? queryRef.current.refresh()
        : refreshCurrent();
    },
    refreshTodoCompare: (todo) =>
      refreshTodoCompare(
        todo,
        queryRef.current.captureOperation(`todo-compare-entry:${todo.todo_id}`),
      ),
    clearPrivate,
  }));
  const rendererProps: PlanRendererProps | null =
    view && definition
      ? {
          accountId: props.accountId,
          projectId: props.projectId,
          view,
          definition,
          lanes: manualRefreshing
            ? query.lanes.map((lane) => ({ ...lane, paused: true }))
            : query.lanes,
          total: query.total,
          matchedTotal: query.matchedTotal,
          unscheduledTotal: query.unscheduledTotal,
          catalog: props.catalog,
          members: props.members,
          serverToday:
            catalogReady && !manualRefreshing ? query.serverToday : null,
          serverTimezone:
            catalogReady && !manualRefreshing ? query.serverTimezone : null,
          window: queryWindow,
          isManager,
          canEdit: (todo) => callbacks.current.canEdit(todo),
          canDelete: (todo) => callbacks.current.canDelete(todo),
          onOpenTodo: (todo, trigger) => {
            if (!boundScope || !queryRef.current.isCurrentOperation(boundScope))
              return;
            const scope = queryRef.current.captureOperation("detail");
            if (scope) callbacks.current.onOpenTodo(todo, trigger, scope);
          },
          onOpenParentTodo: (todoId, trigger) => {
            if (!boundScope || !queryRef.current.isCurrentOperation(boundScope))
              return;
            const scope = queryRef.current.captureOperation("detail");
            if (scope)
              callbacks.current.onOpenParentTodo(todoId, trigger, scope);
          },
          onEditTodo: (todo) => {
            if (
              !boundScope ||
              !queryRef.current.isCurrentOperation(boundScope) ||
              !callbacks.current.canEdit(todo)
            )
              return;
            const scope = queryRef.current.captureOperation("editor");
            if (scope) callbacks.current.onEditTodo(todo, scope);
          },
          onDeleteTodo: (todo) => {
            if (
              !boundScope ||
              !queryRef.current.isCurrentOperation(boundScope) ||
              !callbacks.current.canDelete(todo)
            )
              return;
            const scope = queryRef.current.captureOperation(
              `delete:${todo.todo_id}`,
            );
            if (scope) callbacks.current.onDeleteTodo(todo, scope);
          },
          onProposeTodoPatch: async (todo, proposal) => {
            if (
              !boundScope ||
              !queryRef.current.isCurrentOperation(boundScope) ||
              current.current.manualRefreshing ||
              !current.current.catalogReady ||
              !queryRef.current.getAcceptedMetadata() ||
              !callbacks.current.canEdit(todo)
            )
              return staleTodo();
            const epoch = userEpoch.current;
            const scope = queryRef.current.captureOperation(
              `todo-patch:${todo.todo_id}`,
            );
            if (!scope) return staleTodo();
            const result = await callbacks.current.onProposeTodoPatch(
              todo,
              proposal,
              scope,
            );
            if (
              result.status === "saved" &&
              epoch === userEpoch.current &&
              queryRef.current.hasAcceptedTodo(scope, result.todo)
            )
              return result;
            if (!queryRef.current.isCurrentOperation(scope)) return staleTodo();
            if (result.status === "saved") {
              if (!(await queryRef.current.acceptTodo(scope, result.todo)))
                return {
                  status: "failed",
                  messageKey: "projects.planViews.failed",
                };
              void (queryRef.current.hasRefreshedTodo(scope, result.todo)
                ? queryRef.current.refresh()
                : refreshCurrent());
            }
            return result;
          },
          onRefreshTodoCompare: (todo) => refreshTodoCompare(todo, boundScope),
          onBulkTodo: async (items, changes) => {
            if (
              !boundScope ||
              !queryRef.current.isCurrentOperation(boundScope) ||
              current.current.manualRefreshing ||
              !current.current.catalogReady ||
              !queryRef.current.getAcceptedMetadata() ||
              !current.current.isManager
            )
              throw new Error("Stale plan operation");
            const epoch = userEpoch.current;
            const scope = queryRef.current.captureOperation("bulk");
            if (!scope) throw new Error("Stale plan operation");
            await callbacks.current.onBulkTodo(items, changes, scope);
            if (
              epoch !== userEpoch.current ||
              !queryRef.current.isSameContextOperation(scope)
            )
              throw new Error("Stale plan operation");
          },
          onLoadFirst: (laneId) =>
            boundScope &&
            queryRef.current.isCurrentOperation(boundScope) &&
            !current.current.manualRefreshing
              ? queryRef.current.loadFirst(laneId)
              : Promise.resolve(false),
          onLoadMore: (laneId) =>
            boundScope &&
            queryRef.current.isCurrentOperation(boundScope) &&
            !current.current.manualRefreshing
              ? queryRef.current.loadMore(laneId)
              : Promise.resolve(false),
          onWindowChange: (next) => {
            if (!boundScope || !queryRef.current.isCurrentOperation(boundScope))
              return;
            touchUserFrame();
            setQueryWindow(next);
          },
          onTemporaryDefinitionChange: (next) => {
            if (boundScope && queryRef.current.isCurrentOperation(boundScope))
              temporaryChange(next);
          },
        }
      : null;
  const Renderer = view
    ? {
        list: PlanList,
        table: PlanTable,
        board: PlanBoard,
        gantt: PlanGantt,
        calendar: PlanCalendar,
      }[view.type]
    : null;
  const baseline =
    draft?.baseline ??
    (view && props.catalog
      ? {
          view: copy(view),
          collectionRevision: viewList?.revision ?? 1,
          catalogRevision: props.catalog.revision,
        }
      : null);
  return (
    <section className={styles.root}>
      {viewList && (
        <PlanViewManager
          views={viewList.items}
          selectedViewId={selectedViewId}
          defaultViewId={viewList.default_view_id}
          role={props.role}
          busy={busy}
          revision={viewList.revision}
          catalogRevision={catalogReady ? props.catalog!.revision : null}
          errorKey={errorKey}
          onSelect={(id) => {
            if (draftDirty && id !== selectedViewId) setPendingViewId(id);
            else selectView(id);
          }}
          onCreate={(action) =>
            viewWrite(
              "view-create",
              action.baseline,
              (signal) =>
                projectPlanViewsApi.create(
                  props.projectId,
                  {
                    expected_revision: action.baseline.revision,
                    expected_catalog_revision: action.baseline.catalogRevision,
                    name: action.name,
                    type: action.type,
                    definition: action.definition,
                  } as PlanViewCreateBody,
                  { signal },
                ),
              (response) => {
                const saved = response as PlanViewMutationResponse;
                acceptMutation(saved);
                if (!draftDirty) selectView(saved.item.view_id);
                return saved.item;
              },
            )
          }
          onRename={(action) =>
            viewWrite(
              `view-write:${action.baseline.view.view_id}`,
              action.baseline,
              (signal) =>
                projectPlanViewsApi.update(
                  props.projectId,
                  action.baseline.view.view_id,
                  {
                    expected_version: action.baseline.view.version,
                    name: action.name,
                  },
                  { signal },
                ),
              (response) => {
                const saved = response as PlanViewMutationResponse;
                acceptMutation(saved);
                return saved.item;
              },
            )
          }
          onChangeType={(action) =>
            draftDirty && action.baseline.view.view_id === selectedViewId
              ? Promise.resolve({
                  state: "invalid",
                  messageKey: "projects.planViews.dirtyTypeChange",
                })
              : viewWrite(
                  `view-write:${action.baseline.view.view_id}`,
                  action.baseline,
                  (signal) =>
                    projectPlanViewsApi.update(
                      props.projectId,
                      action.baseline.view.view_id,
                      {
                        expected_version: action.baseline.view.version,
                        expected_catalog_revision:
                          action.baseline.catalogRevision,
                        type: action.type,
                        definition: action.definition,
                      },
                      { signal },
                    ),
                  (response) => {
                    const saved = response as PlanViewMutationResponse;
                    acceptMutation(saved);
                    if (selectedViewId === saved.item.view_id) {
                      setDraft(null);
                      setQueryWindow(null);
                      resetQuickFilters();
                    }
                    return saved.item;
                  },
                )
          }
          onOrder={(action) =>
            viewWrite(
              "view-order",
              action.baseline,
              (signal) =>
                projectPlanViewsApi.order(
                  props.projectId,
                  {
                    expected_revision: action.baseline.revision,
                    view_ids: action.viewIds,
                  },
                  { signal },
                ),
              (response) => {
                const saved = response as {
                  revision: number;
                  default_view_id: string;
                  items: PlanView[];
                };
                acceptList({
                  ...saved,
                  project_id: props.projectId,
                  items: [
                    ...saved.items,
                    ...(viewListRef.current?.items.filter(
                      (row) => row.archived_at !== null,
                    ) ?? []),
                  ],
                });
                return undefined;
              },
            )
          }
          onDefault={(action) =>
            viewWrite(
              "view-default",
              action.baseline,
              (signal) =>
                projectPlanViewsApi.setDefault(
                  props.projectId,
                  {
                    expected_revision: action.baseline.revision,
                    view_id: action.viewId,
                  },
                  { signal },
                ),
              (response) => {
                const saved = response as {
                  revision: number;
                  default_view_id: string;
                };
                if (
                  viewListRef.current &&
                  saved.revision >= viewListRef.current.revision
                ) {
                  const next = { ...viewListRef.current, ...saved };
                  viewListRef.current = next;
                  setViewList(next);
                }
                return undefined;
              },
            )
          }
          onArchive={(action) =>
            draftDirty && action.view.view_id === selectedViewId
              ? Promise.resolve({
                  state: "invalid",
                  messageKey: "projects.planViews.dirtyHint",
                })
              : viewWrite(
                  `view-write:${action.view.view_id}`,
                  action,
                  (signal) =>
                    projectPlanViewsApi.archive(
                      props.projectId,
                      action.view.view_id,
                      { expected_version: action.view.version },
                      { signal },
                    ),
                  (response) => {
                    const saved = response as PlanViewMutationResponse;
                    acceptMutation(saved);
                    if (saved.item.view_id === selectedViewId)
                      selectView(saved.default_view_id);
                    return saved.item;
                  },
                )
          }
          onRestore={(action) =>
            viewWrite(
              `view-write:${action.view.view_id}`,
              action,
              (signal) =>
                projectPlanViewsApi.restore(
                  props.projectId,
                  action.view.view_id,
                  { expected_version: action.view.version },
                  { signal },
                ),
              (response) => {
                const saved = response as PlanViewMutationResponse;
                acceptMutation(saved);
                return saved.item;
              },
            )
          }
          onRefreshCompare={refreshViewCompare}
          onConfirmCompare={confirmViewCompare}
        />
      )}
      <div className={styles.toolbar}>
        <Input
          aria-label={t("projects.planViews.search", "搜索待办")}
          placeholder={t("projects.planViews.search", "搜索待办")}
          maxLength={200}
          value={quickSearch}
          onChange={(event) =>
            quickChange(event.target.value, quickStatus, quickAssignee)
          }
        />
        <Select
          aria-label={t("projects.planViews.statusFilter", "筛选状态")}
          value={quickStatus}
          virtual={false}
          onChange={(status) => quickChange(quickSearch, status, quickAssignee)}
          options={[
            {
              value: "all",
              label: t("projects.planViews.allStatuses", "全部状态"),
            },
            ...["todo", "in_progress", "done"].map((status) => ({
              value: status,
              label: t(
                `projects.plan.status.${status}`,
                status === "todo"
                  ? "待处理"
                  : status === "in_progress"
                  ? "进行中"
                  : "已完成",
              ),
            })),
          ]}
        />
        <Select
          aria-label={t("projects.planViews.assigneeFilter", "筛选处理人")}
          value={quickAssignee}
          virtual={false}
          onChange={(assignee) =>
            quickChange(quickSearch, quickStatus, assignee)
          }
          options={[
            {
              value: "all",
              label: t("projects.planViews.allAssignees", "全部处理人"),
            },
            ...props.members.map((member) => ({
              value: member.user_id,
              label: member.username,
            })),
          ]}
        />
        <Button
          onClick={() => {
            if (!view || !props.catalog) return;
            if (!draft)
              setDraft({
                baseline: {
                  view: copy(view),
                  collectionRevision: viewList!.revision,
                  catalogRevision: props.catalog.revision,
                },
                definition: copy(
                  normalizePlanDefinition(view.type, view.definition),
                ),
                locked: false,
                comparison: null,
              });
            setSettingsOpen(true);
          }}
          disabled={!catalogReady || !view}
        >
          {t("projects.planViews.settingsTitle", "视图设置")}
        </Button>
        <Button
          loading={manualRefreshing}
          onClick={() => void (view ? refreshCurrent() : loadViews())}
        >
          {t("projects.planViews.refresh", "刷新")}
        </Button>
        <Button
          type="primary"
          onClick={() => {
            if (!boundScope || !queryRef.current.isCurrentOperation(boundScope))
              return;
            const scope = queryRef.current.captureOperation("editor");
            if (scope) callbacks.current.onCreateTodo(scope);
          }}
          disabled={!view}
        >
          {t("projects.plan.newTodo", "新建待办")}
        </Button>
      </div>
      {draftDirty && (
        <div className={styles.draft}>
          <Tag>{t("projects.planViews.unsaved", "未保存调整")}</Tag>
          <Button
            onClick={() => {
              touchUserFrame();
              setDraft(null);
              resetQuickFilters();
            }}
          >
            {t("projects.planViews.resetTemporary", "重置为共享配置")}
          </Button>
        </div>
      )}
      <div className={styles.count}>
        {t("projects.planViews.totalCount", "待办总数：{{count}}", {
          count: query.total,
        })}
      </div>
      {!catalogReady && (
        <Alert
          type="warning"
          message={t(
            "projects.planViews.catalogUnavailable",
            "无法加载日期与项目目录。请重试后继续。",
          )}
        />
      )}
      {(errorKey || query.errorKey) && (
        <Alert
          type="warning"
          showIcon
          message={t(errorKey ?? query.errorKey!)}
          action={
            <Button onClick={() => void refreshCurrent()}>
              {t("projects.planViews.refresh", "刷新")}
            </Button>
          }
        />
      )}
      {query.unavailableFilterIndices.map((index) => (
        <Button
          key={index}
          onClick={() => {
            if (definition)
              temporaryChange({
                ...definition,
                filters: definition.filters.filter(
                  (_condition, position) => position !== index,
                ),
              });
          }}
        >
          {t(
            "projects.planViews.removeUnavailableCondition",
            "移除失效条件 {{index}}",
            { index: index + 1 },
          )}
        </Button>
      ))}
      {accessLost ? (
        <Alert
          type="error"
          message={t("projects.plan.notFound", "项目不存在或你无权访问")}
        />
      ) : listLoading && !viewList ? (
        <Spin
          aria-label={t("projects.planViews.loadingViews", "正在加载共享视图")}
        />
      ) : Renderer && rendererProps ? (
        <Renderer {...rendererProps} />
      ) : (
        <Alert
          type="info"
          message={t("projects.planViews.noViews", "没有可用的共享视图。")}
        />
      )}
      {settingsOpen && view && definition && baseline && (
        <PlanViewSettings
          key={view.view_id}
          view={view}
          definition={definition}
          catalog={props.catalog}
          members={props.members}
          isManager={isManager}
          conflictLocked={draft?.locked ?? false}
          busy={busy}
          baseline={baseline}
          comparison={draft?.comparison ?? null}
          errorKey={errorKey}
          onTemporaryChange={temporaryChange}
          onSave={saveDefinition}
          onRefreshCompare={refreshViewCompare}
          onConfirmCompare={confirmViewCompare}
          onCancel={() => setSettingsOpen(false)}
        />
      )}
      <Modal
        open={pendingViewId !== null}
        title={t("projects.planViews.dirtyTitle", "未保存的视图调整")}
        footer={null}
        onCancel={() => setPendingViewId(null)}
      >
        <p>
          {t("projects.planViews.dirtyHint", "保存或舍弃当前调整后再切换。")}
        </p>
        <div className={styles.switchActions}>
          <Button onClick={() => setPendingViewId(null)}>
            {t("projects.planViews.continueEditing", "继续编辑")}
          </Button>
          <Button
            onClick={() => {
              if (pendingViewId) selectView(pendingViewId);
            }}
          >
            {t("projects.planViews.discardSwitch", "舍弃并切换")}
          </Button>
          {isManager && (
            <Button
              loading={busy}
              onClick={async () => {
                const target = pendingViewId;
                if (!target || !draft) return;
                const result = await saveDefinition({
                  baseline: draft.baseline,
                  definition: draft.definition,
                });
                if (result.state === "saved") selectView(target);
              }}
            >
              {t("projects.planViews.saveSwitch", "保存后切换")}
            </Button>
          )}
        </div>
      </Modal>
    </section>
  );
});
const ProjectPlanViews = forwardRef<
  ProjectPlanViewsHandle,
  ProjectPlanViewsProps
>(function ProjectPlanViews(props, ref) {
  return (
    <ProjectPlanViewsContent
      key={JSON.stringify([props.accountId, props.projectId])}
      {...props}
      ref={ref}
    />
  );
});
export default ProjectPlanViews;
