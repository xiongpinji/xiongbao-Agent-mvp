import { useCallback, useLayoutEffect, useRef, useState } from "react";
import {
  projectPlanViewsApi,
  type AnyPlanDefinition,
  type PlanQueryGroupKey,
  type PlanQueryRequest,
  type PlanQueryResponse,
  type PlanQueryWindow,
  type PlanView,
} from "../../../api/modules/projectPlanViews";
import type { ProjectTodo } from "../../../api/modules/projectTodos";
import { projectsApi } from "../../../api/modules/projects";
import { parseApiError } from "../../../utils/apiError";
import { isPlanDate } from "../planDates";
import type { PlanLaneState, PlanOperationScope } from "./ProjectPlanViews";
export interface ProjectPlanQueryOptions {
  accountId: number;
  projectId: string;
  view: PlanView | null;
  catalogRevision: number | null;
  overrideDefinition?: AnyPlanDefinition;
  window: PlanQueryWindow | null;
  enabled?: boolean;
  accessKey?: string;
  onProjectAccessLost(): void;
  onViewUnavailable?(): void;
}
interface QueryState {
  key: string;
  lanes: readonly PlanLaneState[];
  total: number;
  matchedTotal: number;
  unscheduledTotal: number | null;
  serverToday: string | null;
  serverTimezone: string | null;
  loading: boolean;
  errorKey: string | null;
  paused: boolean;
  unavailableFilterIndices: readonly number[];
}
interface QueryFrame {
  key: string;
  shapeKey: string;
  identityKey: string;
  lifetime: number;
  generation: number;
  options: ProjectPlanQueryOptions;
  alive: boolean;
  cleared: boolean;
  paused: boolean;
  preserveRead: boolean;
  metadataAccepted: boolean;
  operations: Map<string, { generation: number; controller: AbortController }>;
  depths: Map<string, number>;
  highWater: Map<string, ProjectTodo>;
  acceptedWrites: Set<string>;
}
let nextLifetime = 0;
const prefix = "projects.planViews.";
export function planHttpStatus(error: unknown): number | null {
  const match =
    error instanceof Error
      ? error.message.match(/(?:Request failed:|Upload failed:)\s*(\d{3})\b/)
      : null;
  return match
    ? Number(match[1])
    : parseApiError(error)?.code === "NOT_FOUND"
    ? 404
    : null;
}
export function planErrorKey(error: unknown): string {
  const reasons: Record<string, string> = {
    query_changed: "queryChanged",
    filter_reference_unavailable: "filterReferenceUnavailable",
    transaction_conflict: "transactionConflict",
    invalid_query_request: "invalidQuery",
    name_conflict: "nameConflict",
    view_revision_conflict: "viewConflict",
    view_version_conflict: "viewConflict",
    catalog_revision_conflict: "catalogConflict",
    active_limit: "activeLimit",
    total_limit: "totalLimit",
    last_active_view: "lastActiveView",
    no_change: "noChange",
    invalid_lifecycle: "invalidLifecycle",
    invalid_order: "invalidOrder",
    invalid_definition: "invalidDefinition",
    invalid_view_request: "invalidView",
    invalid_catalog_reference: "invalidCatalogReference",
    invalid_assignee: "invalidAssignee",
    project_archived: "projectArchived",
  };
  const reason = parseApiError(error)?.details?.reason;
  if (typeof reason === "string" && reasons[reason])
    return prefix + reasons[reason];
  const status = planHttpStatus(error);
  return (
    prefix +
    (status === 403
      ? "forbidden"
      : status === 409
      ? "viewConflict"
      : status === 422
      ? "invalidQuery"
      : "failed")
  );
}
const groupLaneId = (key: PlanQueryGroupKey) =>
  `group:${JSON.stringify([key.kind, key.id])}`;
const newLane = (
  laneId: string,
  groupKey: PlanQueryGroupKey | null = null,
  bucket: "scheduled" | "unscheduled" | null = null,
  serverCount = 0,
): PlanLaneState => ({
  laneId,
  groupKey,
  bucket,
  items: [],
  serverCount,
  nextCursor: null,
  queryFingerprint: null,
  loadingFirst: false,
  loadingMore: false,
  errorKey: null,
  paused: false,
});
const emptyState = (frame: QueryFrame): QueryState => ({
  key: frame.key,
  lanes: [],
  total: 0,
  matchedTotal: 0,
  unscheduledTotal: null,
  serverToday: null,
  serverTimezone: null,
  loading: false,
  errorKey: null,
  paused: frame.paused,
  unavailableFilterIndices: [],
});
const abortFrame = (frame: QueryFrame) => {
  frame.alive = false;
  for (const operation of frame.operations.values())
    operation.controller.abort();
};
const newerTodo = (a: ProjectTodo | undefined, b: ProjectTodo) =>
  !a ||
  b.version > a.version ||
  (b.version === a.version && b.catalog_revision > a.catalog_revision)
    ? b
    : a;
const uniqueItems = (
  items: readonly ProjectTodo[],
  highWater: Map<string, ProjectTodo>,
): ProjectTodo[] => {
  const unique = new Map<string, ProjectTodo>();
  for (const incoming of items) {
    const highest = newerTodo(highWater.get(incoming.todo_id), incoming);
    highWater.set(incoming.todo_id, highest);
    unique.set(
      incoming.todo_id,
      newerTodo(unique.get(incoming.todo_id), highest),
    );
  }
  return [...unique.values()];
};

export function useProjectPlanQuery(options: ProjectPlanQueryOptions) {
  const callbacks = useRef(options);
  callbacks.current = options;
  const effectiveDefinition =
    options.overrideDefinition ?? options.view?.definition;
  const shapeKey = JSON.stringify([
    options.accountId,
    options.projectId,
    options.view?.view_id,
    options.view?.type,
    effectiveDefinition,
    options.window,
  ]);
  const identityKey = JSON.stringify([
    options.accountId,
    options.projectId,
    options.view?.view_id,
  ]);
  const key = JSON.stringify([
    shapeKey,
    options.view?.version,
    options.catalogRevision,
    options.accessKey,
  ]);
  const generation = useRef(0);
  const frameRef = useRef<QueryFrame | null>(null);
  const previous = frameRef.current;
  if (!previous || previous.key !== key) {
    const sameShape = previous?.shapeKey === shapeKey;
    const sameProject =
      previous?.options.accountId === options.accountId &&
      previous.options.projectId === options.projectId;
    if (previous) abortFrame(previous);
    frameRef.current = {
      key,
      shapeKey,
      identityKey,
      lifetime:
        previous?.identityKey === identityKey
          ? previous.lifetime
          : ++nextLifetime,
      generation: ++generation.current,
      options,
      alive: true,
      cleared: false,
      paused:
        sameShape && previous?.options.accessKey === options.accessKey
          ? previous!.paused
          : false,
      operations: new Map(),
      preserveRead: !!sameShape,
      metadataAccepted: false,
      depths: sameShape ? new Map(previous!.depths) : new Map(),
      highWater: sameProject ? previous!.highWater : new Map(),
      acceptedWrites:
        previous?.identityKey === identityKey
          ? previous.acceptedWrites
          : new Set(),
    };
  }
  const frame = frameRef.current!;
  frame.options = options;
  const [state, setState] = useState<QueryState>(() => emptyState(frame));
  const stateRef = useRef(state);
  const isFrameCurrent = useCallback(
    (target: QueryFrame) =>
      target.alive && !target.cleared && frameRef.current === target,
    [],
  );
  const isCurrentOperation = useCallback(
    (scope: PlanOperationScope) => {
      const current = frameRef.current;
      return (
        !!current &&
        isFrameCurrent(current) &&
        current.lifetime === scope.lifetime &&
        current.generation === scope.queryGeneration &&
        current.options.accountId === scope.accountId &&
        current.options.projectId === scope.projectId &&
        current.options.view?.view_id === scope.viewId &&
        current.operations.get(scope.channel)?.generation ===
          scope.operationGeneration
      );
    },
    [isFrameCurrent],
  );
  const isSameContextOperation = useCallback(
    (scope: PlanOperationScope) => {
      const current = frameRef.current;
      return (
        !!current &&
        isFrameCurrent(current) &&
        current.lifetime === scope.lifetime &&
        current.options.accountId === scope.accountId &&
        current.options.projectId === scope.projectId &&
        current.options.view?.view_id === scope.viewId
      );
    },
    [isFrameCurrent],
  );
  const captureFor = useCallback(
    (target: QueryFrame, channel: string): PlanOperationScope | null => {
      if (!isFrameCurrent(target) || !target.options.view) return null;
      const old = target.operations.get(channel);
      old?.controller.abort();
      const operation = {
        generation: (old?.generation ?? 0) + 1,
        controller: new AbortController(),
      };
      target.operations.set(channel, operation);
      return {
        lifetime: target.lifetime,
        accountId: target.options.accountId,
        projectId: target.options.projectId,
        viewId: target.options.view.view_id,
        queryGeneration: target.generation,
        channel,
        operationGeneration: operation.generation,
      };
    },
    [isFrameCurrent],
  );
  const captureOperation = useCallback(
    (channel: string) =>
      frameRef.current ? captureFor(frameRef.current, channel) : null,
    [captureFor],
  );
  const commit = useCallback(
    (target: QueryFrame, update: (current: QueryState) => QueryState) => {
      if (!isFrameCurrent(target)) return;
      const current =
        stateRef.current.key === target.key
          ? stateRef.current
          : emptyState(target);
      const next = update(current);
      stateRef.current = next;
      setState(next);
    },
    [isFrameCurrent],
  );
  const setLane = useCallback(
    (
      target: QueryFrame,
      laneId: string,
      update: (lane: PlanLaneState) => PlanLaneState,
    ) =>
      commit(target, (current) => ({
        ...current,
        lanes: current.lanes.map((lane) =>
          lane.laneId === laneId ? update(lane) : lane,
        ),
      })),
    [commit],
  );
  const bodyFor = (
    target: QueryFrame,
    lane: PlanLaneState | null,
    cursor?: string,
    metadata = false,
  ): PlanQueryRequest | null => {
    const current = target.options;
    if (!current.view || current.catalogRevision === null) return null;
    if (
      (current.view.type === "gantt" || current.view.type === "calendar") &&
      !current.window
    )
      return null;
    return {
      view_id: current.view.view_id,
      expected_view_version: current.view.version,
      expected_catalog_revision: current.catalogRevision,
      limit: metadata ? 1 : 50,
      ...(current.overrideDefinition
        ? { override_definition: current.overrideDefinition }
        : {}),
      ...(lane?.groupKey ? { group_key: lane.groupKey } : {}),
      ...(lane?.bucket ? { bucket: lane.bucket, window: current.window! } : {}),
      ...(cursor ? { cursor } : {}),
    };
  };
  const acceptMetadata = (
    target: QueryFrame,
    response: PlanQueryResponse,
    lane: PlanLaneState | null,
  ) => {
    target.metadataAccepted = true;
    commit(target, (current) => {
      const definition =
        target.options.overrideDefinition ?? target.options.view!.definition;
      let lanes = current.lanes;
      if (definition.group_by !== null)
        lanes = response.groups.map(({ key: groupKey, count }) => {
          const laneId = groupLaneId(groupKey);
          return {
            ...(current.lanes.find((item) => item.laneId === laneId) ??
              newLane(laneId, groupKey)),
            serverCount: count,
          };
        });
      return {
        ...current,
        lanes,
        total: response.total,
        matchedTotal:
          lane?.bucket === "unscheduled"
            ? current.matchedTotal
            : response.matched_total,
        unscheduledTotal: response.unscheduled_total,
        serverToday: response.server_today,
        serverTimezone: response.server_timezone,
        errorKey: null,
        unavailableFilterIndices: [],
      };
    });
  };
  const handleError = async (
    target: QueryFrame,
    scope: PlanOperationScope,
    error: unknown,
  ) => {
    if (!isCurrentOperation(scope)) return;
    const status = planHttpStatus(error);
    if (status === 404) {
      try {
        await projectsApi.get(scope.projectId);
        if (!isCurrentOperation(scope)) return;
        callbacks.current.onViewUnavailable?.();
      } catch (projectError: unknown) {
        if (!isCurrentOperation(scope)) return;
        if (planHttpStatus(projectError) === 404) {
          commit(target, () => emptyState(target));
          callbacks.current.onProjectAccessLost();
          return;
        }
      }
    }
    if (!isCurrentOperation(scope)) return;
    const details = parseApiError(error)?.details;
    const pause = status === 409;
    if (pause) target.paused = true;
    const definition =
      target.options.overrideDefinition ?? target.options.view?.definition;
    const indices =
      details?.reason === "filter_reference_unavailable" &&
      Array.isArray(details.condition_indices)
        ? [
            ...new Set(
              details.condition_indices.filter(
                (index): index is number =>
                  Number.isSafeInteger(index) &&
                  index >= 0 &&
                  definition?.filters[index]?.field === "assignee",
              ),
            ),
          ]
        : [];
    commit(target, (current) => ({
      ...current,
      paused: pause || current.paused,
      errorKey: planErrorKey(error),
      unavailableFilterIndices: indices,
      lanes: current.lanes.map((lane) => ({
        ...lane,
        paused: pause || lane.paused,
      })),
    }));
  };
  const validResponse = (target: QueryFrame, response: PlanQueryResponse) => {
    const current = target.options;
    return (
      response &&
      response.view_id === current.view?.view_id &&
      response.view_version === current.view?.version &&
      response.catalog_revision === current.catalogRevision &&
      isPlanDate(response.server_today) &&
      typeof response.server_timezone === "string" &&
      !!response.server_timezone &&
      typeof response.query_fingerprint === "string" &&
      !!response.query_fingerprint &&
      Array.isArray(response.items) &&
      response.items.every(
        (todo) =>
          todo.project_id === current.projectId &&
          Number.isSafeInteger(todo.version) &&
          todo.version > 0,
      ) &&
      [response.total, response.matched_total].every(
        (count) => Number.isSafeInteger(count) && count >= 0,
      )
    );
  };
  const requestPage = async (
    target: QueryFrame,
    laneId: string | null,
    append: boolean,
  ): Promise<boolean> => {
    if (!isFrameCurrent(target) || target.paused) return false;
    const lane = laneId
      ? stateRef.current.lanes.find((item) => item.laneId === laneId)
      : null;
    if (laneId && !lane) return false;
    if (append && (!lane?.nextCursor || lane.loadingMore || lane.loadingFirst))
      return false;
    const body = bodyFor(
      target,
      lane ?? null,
      append ? lane?.nextCursor ?? undefined : undefined,
      !laneId,
    );
    if (!body) return false;
    const scope = captureFor(
      target,
      laneId ? `lane:${laneId}` : "query-metadata",
    );
    if (!scope) return false;
    if (laneId)
      setLane(target, laneId, (current) => ({
        ...current,
        loadingFirst: !append,
        loadingMore: append,
        errorKey: null,
      }));
    else
      commit(target, (current) => ({
        ...current,
        loading: true,
        errorKey: null,
      }));
    try {
      const response = await projectPlanViewsApi.query(scope.projectId, body, {
        signal: target.operations.get(scope.channel)!.controller.signal,
      });
      if (!isCurrentOperation(scope) || target.paused) return false;
      if (!validResponse(target, response))
        throw new Error("Invalid plan query response");
      if (
        append &&
        lane?.queryFingerprint &&
        lane.queryFingerprint !== response.query_fingerprint
      ) {
        throw new Error(
          'Request failed: 409 - {"error":{"details":{"reason":"query_changed"}}}',
        );
      }
      acceptMetadata(target, response, lane ?? null);
      if (laneId)
        setLane(target, laneId, (current) => ({
          ...current,
          items: uniqueItems(
            append ? [...current.items, ...response.items] : response.items,
            target.highWater,
          ),
          serverCount: lane?.bucket
            ? response.matched_total
            : lane?.groupKey
            ? current.serverCount
            : response.matched_total,
          nextCursor: response.next_cursor,
          queryFingerprint: response.query_fingerprint,
          paused: false,
          errorKey: null,
        }));
      return true;
    } catch (error: unknown) {
      await handleError(target, scope, error);
      if (isCurrentOperation(scope) && laneId)
        setLane(target, laneId, (current) => ({
          ...current,
          errorKey: planErrorKey(error),
        }));
      return false;
    } finally {
      if (isCurrentOperation(scope)) {
        if (laneId)
          setLane(target, laneId, (current) => ({
            ...current,
            loadingFirst: false,
            loadingMore: false,
          }));
        else commit(target, (current) => ({ ...current, loading: false }));
      }
    }
  };
  const reloadLane = async (
    target: QueryFrame,
    laneId: string,
    depth: number,
  ) => {
    if (!(await requestPage(target, laneId, false))) return false;
    let loaded = 1;
    for (; loaded < depth; loaded += 1) {
      if (
        !stateRef.current.lanes.find((lane) => lane.laneId === laneId)
          ?.nextCursor
      )
        break;
      if (!(await requestPage(target, laneId, true))) return false;
    }
    if (!isFrameCurrent(target)) return false;
    target.depths.set(laneId, loaded);
    return true;
  };
  const reloadAll = async (target: QueryFrame, depths: Map<string, number>) => {
    if (
      !isFrameCurrent(target) ||
      !target.options.view ||
      target.options.catalogRevision === null
    )
      return false;
    const type = target.options.view.type;
    const definition =
      target.options.overrideDefinition ?? target.options.view.definition;
    if (type === "gantt" || type === "calendar") {
      if (!target.options.window) return false;
      commit(target, (current) => ({
        ...current,
        lanes: ["scheduled", "unscheduled"].map(
          (bucket) =>
            current.lanes.find((lane) => lane.laneId === bucket) ??
            newLane(bucket, null, bucket as "scheduled" | "unscheduled"),
        ),
      }));
      const responses = await Promise.all([
        reloadLane(target, "scheduled", depths.get("scheduled") ?? 1),
        reloadLane(target, "unscheduled", depths.get("unscheduled") ?? 1),
      ]);
      return isFrameCurrent(target) && responses.every(Boolean);
    }
    if (definition.group_by !== null) {
      if (!(await requestPage(target, null, false))) return false;
      const loaded = stateRef.current.lanes.filter((lane) =>
        depths.has(lane.laneId),
      );
      const responses = await Promise.all(
        loaded.map((lane) =>
          reloadLane(target, lane.laneId, depths.get(lane.laneId)!),
        ),
      );
      return isFrameCurrent(target) && responses.every(Boolean);
    }
    commit(target, (current) => ({
      ...current,
      lanes: [
        current.lanes.find((lane) => lane.laneId === "all") ?? newLane("all"),
      ],
    }));
    return reloadLane(target, "all", depths.get("all") ?? 1);
  };
  const loadFirst = async (laneId: string) => {
    const current = frameRef.current!;
    const success = await requestPage(current, laneId, false);
    if (success && isFrameCurrent(current)) current.depths.set(laneId, 1);
    return success;
  };
  const loadMore = async (laneId: string) => {
    const current = frameRef.current!;
    const depth = current.depths.get(laneId) ?? 1;
    const success = await requestPage(current, laneId, true);
    if (success && isFrameCurrent(current))
      current.depths.set(laneId, depth + 1);
    return success;
  };
  const pauseForMetadata = () => {
    const current = frameRef.current!;
    if (!isFrameCurrent(current)) return false;
    for (const operation of current.operations.values())
      operation.controller.abort();
    current.operations.clear();
    current.generation = ++generation.current;
    current.paused = true;
    current.metadataAccepted = false;
    commit(current, (value) => ({
      ...value,
      paused: true,
      loading: false,
      lanes: value.lanes.map((lane) => ({
        ...lane,
        paused: true,
        loadingFirst: false,
        loadingMore: false,
      })),
    }));
    return true;
  };
  const refresh = async () => {
    const current = frameRef.current!;
    if (!isFrameCurrent(current)) return false;
    for (const operation of current.operations.values())
      operation.controller.abort();
    current.operations.clear();
    current.generation = ++generation.current;
    current.paused = false;
    current.metadataAccepted = false;
    commit(current, (value) => ({
      ...value,
      paused: false,
      errorKey: null,
      unavailableFilterIndices: [],
      lanes: value.lanes.map((lane) => ({
        ...lane,
        paused: false,
        loadingFirst: false,
        loadingMore: false,
      })),
    }));
    return reloadAll(current, new Map(current.depths));
  };
  const acceptTodo = useCallback(
    (scope: PlanOperationScope, todo: ProjectTodo) => {
      const current = frameRef.current;
      if (
        !current ||
        !isCurrentOperation(scope) ||
        todo.project_id !== scope.projectId
      )
        return;
      const highest = newerTodo(current.highWater.get(todo.todo_id), todo);
      current.highWater.set(todo.todo_id, highest);
      current.acceptedWrites.add(
        JSON.stringify([scope, todo.todo_id, todo.version]),
      );
      commit(current, (value) => ({
        ...value,
        lanes: value.lanes.map((lane) => ({
          ...lane,
          items: lane.items.map((item) =>
            item.todo_id === todo.todo_id ? highest : item,
          ),
        })),
      }));
    },
    [commit, isCurrentOperation],
  );
  const hasAcceptedTodo = useCallback(
    (scope: PlanOperationScope, todo: ProjectTodo) =>
      isSameContextOperation(scope) &&
      !!frameRef.current?.acceptedWrites.has(
        JSON.stringify([scope, todo.todo_id, todo.version]),
      ),
    [isSameContextOperation],
  );
  const getKnownTodo = useCallback(
    (todoId: string) => frameRef.current?.highWater.get(todoId),
    [],
  );
  const getAcceptedMetadata = useCallback(() => {
    const target = frameRef.current,
      value = stateRef.current;
    if (
      !target ||
      !isFrameCurrent(target) ||
      target.paused ||
      !target.metadataAccepted ||
      value.key !== target.key ||
      !value.serverToday ||
      !value.serverTimezone
    )
      return null;
    return {
      serverToday: value.serverToday,
      serverTimezone: value.serverTimezone,
      viewVersion: target.options.view?.version,
      catalogRevision: target.options.catalogRevision,
    };
  }, [isFrameCurrent]);
  const removeTodo = useCallback(
    (scope: PlanOperationScope, todoId: string) => {
      const current = frameRef.current;
      if (!current || !isCurrentOperation(scope)) return;
      current.highWater.delete(todoId);
      commit(current, (value) => ({
        ...value,
        lanes: value.lanes.map((lane) => ({
          ...lane,
          items: lane.items.filter((todo) => todo.todo_id !== todoId),
        })),
      }));
    },
    [commit, isCurrentOperation],
  );
  const clear = useCallback(() => {
    const current = frameRef.current;
    if (!current) return;
    for (const operation of current.operations.values())
      operation.controller.abort();
    current.cleared = true;
    current.generation = ++generation.current;
    current.highWater.clear();
    current.depths.clear();
    current.acceptedWrites.clear();
    const next = emptyState(current);
    stateRef.current = next;
    setState(next);
  }, []);
  useLayoutEffect(() => {
    const target = frame;
    target.alive = true;
    const old = stateRef.current;
    const next =
      old.key === target.key
        ? old
        : {
            ...emptyState(target),
            ...(target.preserveRead
              ? {
                  lanes: old.lanes.map((lane) => ({
                    ...lane,
                    loadingFirst: false,
                    loadingMore: false,
                    paused: target.paused,
                  })),
                  total: old.total,
                  matchedTotal: old.matchedTotal,
                  unscheduledTotal: old.unscheduledTotal,
                  serverToday: old.serverToday,
                  serverTimezone: old.serverTimezone,
                  errorKey: old.errorKey,
                  unavailableFilterIndices: old.unavailableFilterIndices,
                }
              : {}),
          };
    stateRef.current = next;
    setState(next);
    if (!target.paused && target.options.enabled !== false)
      void reloadAll(target, new Map(target.depths));
    return () => abortFrame(target);
    // A logical key binds all metadata and callbacks through guarded refs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const currentState =
    state.key === frame.key && !frame.cleared ? state : emptyState(frame);
  return {
    ...currentState,
    serverToday: currentState.paused ? null : currentState.serverToday,
    serverTimezone: currentState.paused ? null : currentState.serverTimezone,
    loadFirst,
    loadMore,
    refresh,
    pauseForMetadata,
    acceptTodo,
    removeTodo,
    clear,
    captureOperation,
    isCurrentOperation,
    isSameContextOperation,
    hasAcceptedTodo,
    getKnownTodo,
    getAcceptedMetadata,
  };
}
