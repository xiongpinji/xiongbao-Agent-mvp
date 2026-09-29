import { useEffect, useRef, useState } from "react";
import { Alert, Button, ConfigProvider, Input, Modal, Select, Tag } from "antd";
import { useTranslation } from "react-i18next";
import type {
  AnyPlanDefinition,
  PlanView,
  PlanViewType,
} from "../../../api/modules/projectPlanViews";
import type { ProjectRole } from "../../../api/modules/projects";
import type {
  PlanViewActionBaseline,
  PlanViewActionResult,
  PlanViewChangeTypeAction,
  PlanViewCollectionBaseline,
  PlanViewComparison,
  PlanViewCompareResult,
  PlanViewCreateAction,
  PlanViewDefaultAction,
  PlanViewOrderAction,
  PlanViewRenameAction,
} from "./ProjectPlanViews";
import {
  planDefinitionForType,
  PlanDefinitionSummary,
} from "./PlanViewSettings";
import styles from "./PlanViewManager.module.less";

export interface PlanViewManagerProps {
  views: readonly PlanView[];
  selectedViewId: string | null;
  defaultViewId: string;
  role: ProjectRole;
  busy: boolean;
  revision: number;
  catalogRevision: number | null;
  errorKey?: string | null;
  onSelect: (viewId: string) => void;
  onCreate: (action: PlanViewCreateAction) => Promise<PlanViewActionResult>;
  onRename: (action: PlanViewRenameAction) => Promise<PlanViewActionResult>;
  onChangeType: (
    action: PlanViewChangeTypeAction,
  ) => Promise<PlanViewActionResult>;
  onOrder: (action: PlanViewOrderAction) => Promise<PlanViewActionResult>;
  onDefault: (action: PlanViewDefaultAction) => Promise<PlanViewActionResult>;
  onArchive: (
    baseline: PlanViewActionBaseline,
  ) => Promise<PlanViewActionResult>;
  onRestore: (
    baseline: PlanViewActionBaseline,
  ) => Promise<PlanViewActionResult>;
  onRefreshCompare: (
    baseline: PlanViewActionBaseline,
  ) => Promise<PlanViewCompareResult>;
  onConfirmCompare: (comparison: PlanViewComparison) => boolean;
}

interface CollectionSnapshot {
  views: readonly PlanView[];
  baseline: PlanViewCollectionBaseline;
  defaultViewId: string;
}
interface Editor {
  mode: "create" | "rename" | "type";
  viewId: string | null;
  name: string;
  type: PlanViewType;
  definition: AnyPlanDefinition;
}
interface CompareContext {
  kind: "view" | "collection";
  viewId: string;
}
const TYPES: PlanViewType[] = ["list", "table", "board", "gantt", "calendar"];
const TYPE_NAMES: Record<PlanViewType, string> = {
  list: "列表",
  table: "表格",
  board: "看板",
  gantt: "甘特",
  calendar: "日历",
};
const copy = <T,>(value: T): T => structuredClone(value);
const same = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right);
const activeViews = (views: readonly PlanView[]) =>
  views
    .filter((view) => view.archived_at === null)
    .sort((left, right) => left.position - right.position);
const canManage = (role: ProjectRole) => role === "owner" || role === "admin";
const validName = (name: string) => {
  const trimmed = name.trim();
  return (
    [...trimmed].length >= 1 &&
    [...trimmed].length <= 40 &&
    !/\p{Cc}/u.test(trimmed)
  );
};
function capture(props: PlanViewManagerProps): CollectionSnapshot {
  return {
    views: copy(props.views),
    baseline: {
      revision: props.revision,
      catalogRevision: props.catalogRevision ?? 0,
    },
    defaultViewId: props.defaultViewId,
  };
}

export default function PlanViewManager(props: PlanViewManagerProps) {
  return (
    <ConfigProvider prefixCls="octop" button={{ autoInsertSpace: false }}>
      <ManagerContent key={props.views[0]?.project_id ?? "empty"} {...props} />
    </ConfigProvider>
  );
}

function ManagerContent(props: PlanViewManagerProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement | null>(null);
  return (
    <section
      className={styles.manager}
      aria-label={t("projects.planViews.sharedViews", "共享视图")}
    >
      <nav
        className={styles.selection}
        aria-label={t("projects.planViews.sharedViews", "共享视图")}
      >
        {activeViews(props.views).map((view) => (
          <Button
            key={view.view_id}
            aria-label={t(
              "projects.planViews.selectNamed",
              "选择视图 {{name}}",
              { name: view.name },
            )}
            aria-pressed={props.selectedViewId === view.view_id}
            type={props.selectedViewId === view.view_id ? "primary" : "default"}
            disabled={props.busy}
            onClick={() => props.onSelect(view.view_id)}
          >
            {view.name}
            {view.view_id === props.defaultViewId && (
              <span className={styles.defaultMarker}>
                {t("projects.planViews.defaultView", "项目默认")}
              </span>
            )}
          </Button>
        ))}
        <Button
          ref={trigger}
          disabled={props.busy}
          onClick={() => setOpen(true)}
        >
          {t("projects.planViews.manageTitle", "管理视图")}
        </Button>
      </nav>
      {open && (
        <ManagerDialog
          {...props}
          onClose={() => {
            setOpen(false);
            trigger.current?.focus();
          }}
        />
      )}
    </section>
  );
}

function ManagerDialog(props: PlanViewManagerProps & { onClose: () => void }) {
  const { t } = useTranslation();
  // Each modal lifetime owns its opening collection and full per-view snapshots.
  const [snapshot, setSnapshot] = useState(() => capture(props));
  const [baselines, setBaselines] = useState<
    Record<string, PlanViewActionBaseline>
  >(() =>
    Object.fromEntries(
      props.views.map((view) => [
        view.view_id,
        {
          view: copy(view),
          collectionRevision: props.revision,
          catalogRevision: props.catalogRevision ?? 0,
        },
      ]),
    ),
  );
  const [order, setOrder] = useState(() =>
    activeViews(props.views).map((view) => view.view_id),
  );
  const [editor, setEditor] = useState<Editor | null>(null);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [open, setOpen] = useState(true);
  const [pending, setPending] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [lockedViews, setLockedViews] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const [collectionLocked, setCollectionLocked] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [comparison, setComparison] = useState<PlanViewComparison | null>(null);
  const [compareContext, setCompareContext] = useState<CompareContext | null>(
    null,
  );
  const [closeTarget, setCloseTarget] = useState<"manager" | "editor" | null>(
    null,
  );
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(true),
    sequence = useRef(0),
    pendingRef = useRef(false);
  const cancelFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      sequence.current += 1;
    };
  }, []);
  const manager = canManage(props.role);
  const controlsDisabled = pending || props.busy;
  const active = activeViews(snapshot.views);
  // Explicit per-view confirmation changes its presentation and lifecycle;
  // it does not change the independently captured collection or its order.
  const presentedViews = snapshot.views.map((view) => {
    const confirmed = baselines[view.view_id]?.view;
    return confirmed && confirmed.version >= view.version ? confirmed : view;
  });
  const presentedActive = activeViews(presentedViews);
  const ordered = [
    ...order
      .map((id) => presentedActive.find((view) => view.view_id === id))
      .filter((view): view is PlanView => !!view),
    ...presentedActive.filter((view) => !order.includes(view.view_id)),
  ];
  const archived = presentedViews.filter((view) => view.archived_at !== null);
  const collectionMembershipChanged = !same(
    active.map((view) => view.view_id).sort(),
    presentedActive.map((view) => view.view_id).sort(),
  );
  const orderDirty = !same(
    order,
    active.map((view) => view.view_id),
  );
  const actualActive = activeViews(props.views).length;
  const capacityReached = actualActive >= 30 || props.views.length >= 100;
  const catalogMissing = props.catalogRevision === null;
  const catalogAdvanced =
    props.catalogRevision !== null &&
    props.catalogRevision > snapshot.baseline.catalogRevision;
  const collectionBlocked =
    collectionLocked ||
    catalogMissing ||
    catalogAdvanced ||
    collectionMembershipChanged;
  const getBaseline = (id: string) => baselines[id];
  const viewBlocked = (id: string) => {
    const baseline = getBaseline(id),
      current = props.views.find((view) => view.view_id === id);
    return (
      !baseline ||
      !current ||
      lockedViews.has(id) ||
      current.version > baseline.view.version ||
      current.archived_at !== baseline.view.archived_at ||
      catalogMissing ||
      (props.catalogRevision !== null &&
        props.catalogRevision > baseline.catalogRevision)
    );
  };
  const editorBaseline = editor?.viewId ? getBaseline(editor.viewId) : null;
  const editorBlocked =
    editor?.mode === "create"
      ? collectionBlocked
      : editor?.viewId
      ? viewBlocked(editor.viewId)
      : false;
  const editorDirty =
    editor !== null &&
    (editor.mode === "create"
      ? editor.name !== "" || editor.type !== "table"
      : editorBaseline !== null &&
        (!same(editor.name, editorBaseline.view.name) ||
          editor.type !== editorBaseline.view.type ||
          !same(editor.definition, editorBaseline.view.definition)));
  const message = (key: string | null | undefined, fallback: string) =>
    t(
      key?.startsWith("projects.planViews.")
        ? key
        : "projects.planViews." + fallback,
      {
        defaultValue: t(
          "projects.planViews." + fallback,
          fallback === "refreshFailed"
            ? "无法获取最新共享设置，草稿已保留。"
            : "保存失败，草稿已保留，请重试。",
        ),
      },
    );
  const resetComparison = () => {
    setComparison(null);
    setCompareContext(null);
  };
  const beginEditor = (mode: Editor["mode"], view?: PlanView) => {
    if (
      controlsDisabled ||
      !canManage(latest.current.role) ||
      orderDirty ||
      editor
    )
      return;
    if (mode === "create") {
      if (capacityReached || collectionBlocked) return;
      setEditor({
        mode,
        viewId: null,
        name: "",
        type: "table",
        definition: planDefinitionForType("table"),
      });
    } else if (view && getBaseline(view.view_id)) {
      const original = getBaseline(view.view_id).view;
      setEditor({
        mode,
        viewId: view.view_id,
        name: original.name,
        type: original.type,
        definition: copy(original.definition),
      });
    }
    setReviewId(null);
    setErrorKey(null);
    resetComparison();
  };
  const requestClose = (target: "manager" | "editor") => {
    if (pendingRef.current || latest.current.busy) return;
    if (editorDirty || (target === "manager" && orderDirty)) {
      cancelFocus.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      setCloseTarget(target);
    } else if (target === "editor") {
      setEditor(null);
      setReviewId(null);
      resetComparison();
    } else setOpen(false);
  };
  const move = (id: string, delta: number) => {
    if (
      controlsDisabled ||
      !canManage(latest.current.role) ||
      editor ||
      collectionBlocked
    )
      return;
    const index = order.indexOf(id),
      destination = index + delta;
    if (index < 0 || destination < 0 || destination >= order.length) return;
    const next = [...order];
    [next[index], next[destination]] = [next[destination], next[index]];
    setOrder(next);
    setReviewId(null);
    setErrorKey(null);
    resetComparison();
  };
  const mutate = async (
    kind: "view" | "collection",
    viewId: string | null,
    action: () => Promise<PlanViewActionResult>,
  ) => {
    if (
      pendingRef.current ||
      latest.current.busy ||
      !canManage(latest.current.role)
    )
      return;
    const seq = ++sequence.current;
    pendingRef.current = true;
    setPending(true);
    setErrorKey(null);
    resetComparison();
    if (viewId) setReviewId(viewId);
    try {
      const result = await action();
      if (!mounted.current || seq !== sequence.current) return;
      if (result.state === "saved") setOpen(false);
      else {
        setErrorKey(result.messageKey);
        if (
          result.state === "conflict" ||
          result.state === "stale" ||
          result.state === "forbidden"
        ) {
          if (kind === "collection") setCollectionLocked(true);
          else if (viewId)
            setLockedViews((previous) => new Set([...previous, viewId]));
        }
      }
    } catch {
      if (mounted.current && seq === sequence.current)
        setErrorKey("projects.planViews.saveFailed");
    } finally {
      if (mounted.current && seq === sequence.current) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };
  const saveEditor = () => {
    if (
      !editor ||
      controlsDisabled ||
      editorBlocked ||
      !canManage(latest.current.role) ||
      !validName(editor.name)
    )
      return;
    const draft = copy(editor);
    if (draft.mode === "create") {
      if (
        activeViews(latest.current.views).length >= 30 ||
        latest.current.views.length >= 100
      )
        return;
      void mutate("collection", null, () =>
        props.onCreate({
          baseline: copy(snapshot.baseline),
          name: draft.name.trim(),
          type: draft.type,
          definition: copy(draft.definition),
        }),
      );
    } else if (draft.viewId) {
      const baseline = copy(getBaseline(draft.viewId));
      if (draft.mode === "rename" && draft.name.trim() !== baseline.view.name)
        void mutate("view", draft.viewId, () =>
          props.onRename({ baseline, name: draft.name.trim() }),
        );
      else if (draft.mode === "type" && draft.type !== baseline.view.type)
        void mutate("view", draft.viewId, () =>
          props.onChangeType({
            baseline,
            type: draft.type,
            definition: copy(draft.definition),
          }),
        );
    }
  };
  const saveOrder = () => {
    if (
      controlsDisabled ||
      !manager ||
      collectionBlocked ||
      !orderDirty ||
      editor
    )
      return;
    void mutate("collection", null, () =>
      props.onOrder({
        baseline: copy(snapshot.baseline),
        viewIds: copy(order),
      }),
    );
  };
  const setDefault = (view: PlanView) => {
    if (
      controlsDisabled ||
      !manager ||
      collectionBlocked ||
      editor ||
      orderDirty ||
      view.archived_at !== null ||
      view.view_id === snapshot.defaultViewId
    )
      return;
    void mutate("collection", null, () =>
      props.onDefault({
        baseline: copy(snapshot.baseline),
        viewId: view.view_id,
      }),
    );
  };
  const changeAvailability = (view: PlanView) => {
    if (
      controlsDisabled ||
      !manager ||
      viewBlocked(view.view_id) ||
      editor ||
      orderDirty
    )
      return;
    const current = latest.current.views.find(
      (item) => item.view_id === view.view_id,
    );
    if (
      !current ||
      (view.archived_at === null ? actualActive <= 1 : actualActive >= 30)
    )
      return;
    const baseline = copy(getBaseline(view.view_id));
    void mutate("view", view.view_id, () =>
      view.archived_at === null
        ? props.onArchive(baseline)
        : props.onRestore(baseline),
    );
  };
  const refreshCompare = async (requestedViewId?: string) => {
    if (pendingRef.current || latest.current.busy) return;
    const viewId = requestedViewId ?? editor?.viewId ?? reviewId;
    const anchorId =
      viewId ??
      snapshot.views.find((view) => view.view_id === props.selectedViewId)
        ?.view_id ??
      active[0]?.view_id ??
      snapshot.views[0]?.view_id;
    if (!anchorId || !getBaseline(anchorId)) return;
    const context: CompareContext = {
      kind: viewId ? "view" : "collection",
      viewId: anchorId,
    };
    if (viewId) setReviewId(viewId);
    const anchor = copy(getBaseline(anchorId));
    const seq = ++sequence.current;
    pendingRef.current = true;
    setPending(true);
    setErrorKey(null);
    setComparison(null);
    setCompareContext(context);
    try {
      const result = await props.onRefreshCompare(anchor);
      if (!mounted.current || seq !== sequence.current) return;
      if (
        result.state === "ready" &&
        result.comparison.baseline.view.view_id === anchorId &&
        result.comparison.baseline.view.project_id === anchor.view.project_id
      )
        setComparison(result.comparison);
      else
        setErrorKey(
          result.state === "ready"
            ? "projects.planViews.comparisonStale"
            : result.messageKey,
        );
    } catch {
      if (mounted.current && seq === sequence.current)
        setErrorKey("projects.planViews.refreshFailed");
    } finally {
      if (mounted.current && seq === sequence.current) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };
  const currentComparedView = comparison
    ? props.views.find(
        (view) => view.view_id === comparison.baseline.view.view_id,
      )
    : null;
  const comparisonStale =
    !!comparison &&
    (!compareContext ||
      !currentComparedView ||
      currentComparedView.version > comparison.baseline.view.version ||
      (currentComparedView.version === comparison.baseline.view.version &&
        currentComparedView.archived_at !==
          comparison.baseline.view.archived_at) ||
      props.catalogRevision === null ||
      props.catalogRevision > comparison.baseline.catalogRevision ||
      (compareContext.kind === "collection" &&
        props.catalogRevision !== comparison.baseline.catalogRevision) ||
      (compareContext.kind === "collection" &&
        props.revision !== comparison.baseline.collectionRevision));
  const confirmComparison = () => {
    if (!comparison || !compareContext || controlsDisabled || comparisonStale)
      return;
    if (!props.onConfirmCompare(comparison)) {
      setErrorKey("projects.planViews.comparisonStale");
      setComparison(null);
      if (compareContext.kind === "collection") setCollectionLocked(true);
      else
        setLockedViews(
          (previous) => new Set([...previous, compareContext.viewId]),
        );
      return;
    }
    if (compareContext.kind === "view") {
      setBaselines((previous) => ({
        ...previous,
        [compareContext.viewId]: copy(comparison.baseline),
      }));
      setLockedViews(
        (previous) =>
          new Set([...previous].filter((id) => id !== compareContext.viewId)),
      );
      setReviewId(null);
    } else {
      // The confirmed catalog/list handshake establishes only this collection.
      // Existing single-view snapshots remain independent until compared.
      const accepted = capture(latest.current);
      setSnapshot(accepted);
      const ids = activeViews(accepted.views).map((view) => view.view_id);
      setOrder((previous) => [
        ...previous.filter((id) => ids.includes(id)),
        ...ids.filter((id) => !previous.includes(id)),
      ]);
      setBaselines((previous) => ({
        ...Object.fromEntries(
          accepted.views
            .filter((view) => !previous[view.view_id])
            .map((view) => [
              view.view_id,
              {
                view: copy(view),
                collectionRevision: accepted.baseline.revision,
                catalogRevision: accepted.baseline.catalogRevision,
              },
            ]),
        ),
        ...previous,
      }));
      setCollectionLocked(false);
    }
    setErrorKey(null);
    resetComparison();
  };
  const writeDisabled = controlsDisabled || !manager || !!editor || orderDirty;
  const nameUnchanged =
    editor?.mode === "rename" &&
    editor.name.trim() === editorBaseline?.view.name;
  const typeUnchanged =
    editor?.mode === "type" && editor.type === editorBaseline?.view.type;
  const renderView = (view: PlanView, index: number, restored = false) => (
    <li key={view.view_id} className={styles.view}>
      <div className={styles.viewIdentity}>
        <strong>{view.name}</strong>
        <span>
          {t("projects.planViews.types." + view.type, TYPE_NAMES[view.type])}
        </span>
        {view.view_id === snapshot.defaultViewId && !restored && (
          <Tag>{t("projects.planViews.defaultView", "项目默认")}</Tag>
        )}
        {props.selectedViewId === view.view_id && !restored && (
          <Tag>{t("projects.planViews.selectedView", "当前选择")}</Tag>
        )}
        {viewBlocked(view.view_id) && (
          <Button
            aria-label={t(
              "projects.planViews.refreshCompareNamed",
              "刷新后比较 {{name}}",
              { name: view.name },
            )}
            disabled={
              controlsDisabled ||
              orderDirty ||
              (!!editor && editor.viewId !== view.view_id)
            }
            onClick={() => {
              void refreshCompare(view.view_id);
            }}
          >
            {t("projects.planViews.refreshCompare", "刷新后比较")}
          </Button>
        )}
      </div>
      <div className={styles.rowActions}>
        <Button
          aria-label={t("projects.planViews.renameNamed", "重命名 {{name}}", {
            name: view.name,
          })}
          disabled={writeDisabled}
          onClick={() => beginEditor("rename", view)}
        >
          {t("projects.planViews.rename", "重命名")}
        </Button>
        <Button
          aria-label={t(
            "projects.planViews.changeTypeNamed",
            "更改类型 {{name}}",
            { name: view.name },
          )}
          disabled={writeDisabled}
          onClick={() => beginEditor("type", view)}
        >
          {t("projects.planViews.changeType", "更改类型")}
        </Button>
        {!restored ? (
          <>
            <Button
              aria-label={t("projects.planViews.moveUpNamed", "上移 {{name}}", {
                name: view.name,
              })}
              disabled={
                controlsDisabled ||
                !manager ||
                !!editor ||
                collectionBlocked ||
                index === 0
              }
              onClick={() => move(view.view_id, -1)}
            >
              ↑
            </Button>
            <Button
              aria-label={t(
                "projects.planViews.moveDownNamed",
                "下移 {{name}}",
                { name: view.name },
              )}
              disabled={
                controlsDisabled ||
                !manager ||
                !!editor ||
                collectionBlocked ||
                index === ordered.length - 1
              }
              onClick={() => move(view.view_id, 1)}
            >
              ↓
            </Button>
            <Button
              aria-label={t(
                "projects.planViews.defaultNamed",
                "设为默认 {{name}}",
                { name: view.name },
              )}
              disabled={
                writeDisabled ||
                collectionBlocked ||
                view.view_id === snapshot.defaultViewId
              }
              onClick={() => setDefault(view)}
            >
              {t("projects.planViews.setDefault", "设为默认")}
            </Button>
            <Button
              danger
              aria-label={t(
                "projects.planViews.archiveNamed",
                "停用 {{name}}",
                { name: view.name },
              )}
              disabled={
                writeDisabled || viewBlocked(view.view_id) || actualActive <= 1
              }
              onClick={() => changeAvailability(view)}
            >
              {t("projects.planViews.archive", "停用")}
            </Button>
          </>
        ) : (
          <Button
            aria-label={t("projects.planViews.restoreNamed", "恢复 {{name}}", {
              name: view.name,
            })}
            disabled={
              writeDisabled || viewBlocked(view.view_id) || actualActive >= 30
            }
            onClick={() => changeAvailability(view)}
          >
            {t("projects.planViews.restore", "恢复")}
          </Button>
        )}
      </div>
    </li>
  );
  return (
    <>
      <Modal
        open={open}
        title={t("projects.planViews.manageTitle", "管理视图")}
        footer={null}
        width={760}
        maskClosable={false}
        keyboard={!controlsDisabled}
        style={{ top: 32, maxWidth: "calc(100vw - 32px)" }}
        onCancel={() => requestClose("manager")}
        afterClose={props.onClose}
      >
        <div className={styles.panel}>
          <div className={styles.content}>
            {!manager && (
              <Alert
                type="info"
                showIcon
                message={t(
                  "projects.planViews.managePermission",
                  "只有项目所有者或管理员可以修改共享视图。",
                )}
              />
            )}
            {catalogMissing && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.catalogUnavailable",
                  "目录信息暂不可用",
                )}
              />
            )}
            {collectionMembershipChanged && !editor && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.viewConflict",
                  "共享视图已变更。保留当前调整，刷新比较后确认继续。",
                )}
              />
            )}
            {catalogAdvanced && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.catalogConflict",
                  "目录选项已更新。草稿已保留，请刷新后比较并确认。",
                )}
              />
            )}
            {(editorBlocked ||
              collectionLocked ||
              (reviewId && viewBlocked(reviewId))) && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.conflictHint",
                  "共享视图已被修改。草稿已保留，请刷新后比较并确认。",
                )}
              />
            )}
            {(errorKey || props.errorKey) && (
              <Alert
                type="error"
                showIcon
                message={message(errorKey ?? props.errorKey, "saveFailed")}
              />
            )}
            <div className={styles.sectionHeading}>
              <h3>
                {t("projects.planViews.activeViews", "可用视图")}{" "}
                <span>{actualActive} / 30</span>
              </h3>
              <Button
                disabled={
                  controlsDisabled ||
                  !manager ||
                  !!editor ||
                  orderDirty ||
                  capacityReached ||
                  collectionBlocked
                }
                onClick={() => beginEditor("create")}
              >
                {t("projects.planViews.newView", "新建视图")}
              </Button>
            </div>
            {actualActive >= 30 && (
              <p className={styles.hint}>
                {t(
                  "projects.planViews.activeLimit",
                  "最多保留 30 个可用视图。",
                )}
              </p>
            )}
            {props.views.length >= 100 && (
              <p className={styles.hint}>
                {t(
                  "projects.planViews.totalLimit",
                  "最多保留 100 个视图（包括已停用视图）。",
                )}
              </p>
            )}
            {actualActive <= 1 && (
              <p className={styles.hint}>
                {t("projects.planViews.lastActive", "请至少保留一个可用视图。")}
              </p>
            )}
            <ul className={styles.views}>
              {ordered.map((view, index) => renderView(view, index))}
            </ul>
            {orderDirty && (
              <Alert
                type="info"
                message={t(
                  "projects.planViews.orderDirty",
                  "顺序已调整，保存后应用到共享视图。",
                )}
              />
            )}
            <Button
              aria-label={t("projects.planViews.archivedViews", "停用视图")}
              aria-expanded={archivedOpen}
              onClick={() => setArchivedOpen((value) => !value)}
            >
              {t("projects.planViews.archivedViews", "停用视图")} (
              {archived.length})
            </Button>
            {archivedOpen && (
              <ul className={styles.views}>
                {archived.map((view, index) => renderView(view, index, true))}
              </ul>
            )}
            {editor && (
              <section
                className={styles.form}
                aria-label={t(
                  editor.mode === "create"
                    ? "projects.planViews.newView"
                    : editor.mode === "rename"
                    ? "projects.planViews.rename"
                    : "projects.planViews.changeType",
                )}
              >
                <label>
                  {t("projects.planViews.viewName", "视图名称")}
                  <Input
                    autoFocus
                    aria-label={t("projects.planViews.viewName", "视图名称")}
                    value={editor.name}
                    disabled={controlsDisabled || editor.mode === "type"}
                    onChange={(event) => {
                      setEditor({ ...editor, name: event.target.value });
                      setErrorKey(null);
                    }}
                  />
                </label>
                {editor.name !== "" && !validName(editor.name) && (
                  <Alert
                    type="error"
                    message={t(
                      "projects.planViews.invalidName",
                      "名称须为 1 至 40 个字符，且不含控制字符。",
                    )}
                  />
                )}
                {editor.mode !== "rename" && (
                  <label>
                    {t("projects.planViews.viewType", "视图类型")}
                    <Select
                      aria-label={t("projects.planViews.viewType", "视图类型")}
                      virtual={false}
                      disabled={controlsDisabled}
                      value={editor.type}
                      options={TYPES.map((value) => ({
                        value,
                        label: t(
                          "projects.planViews.types." + value,
                          TYPE_NAMES[value],
                        ),
                      }))}
                      onChange={(type: PlanViewType) => {
                        setEditor({
                          ...editor,
                          type,
                          definition: planDefinitionForType(
                            type,
                            editor.mode === "type"
                              ? editor.definition
                              : undefined,
                          ),
                        });
                        setErrorKey(null);
                      }}
                    />
                  </label>
                )}
                {editor.mode !== "rename" && (
                  <>
                    <p className={styles.hint}>
                      {t(
                        "projects.planViews.typePreviewHint",
                        "下列完整设置将随新建或类型变更提交。可在视图设置中继续调整。",
                      )}
                    </p>
                    <PlanDefinitionSummary definition={editor.definition} />
                  </>
                )}
                <div className={styles.actions}>
                  <Button
                    type="primary"
                    loading={pending}
                    disabled={
                      controlsDisabled ||
                      !manager ||
                      editorBlocked ||
                      !validName(editor.name) ||
                      nameUnchanged ||
                      typeUnchanged ||
                      (editor.mode === "create" && capacityReached)
                    }
                    onClick={saveEditor}
                  >
                    {t(
                      editor.mode === "create"
                        ? "projects.planViews.createView"
                        : editor.mode === "rename"
                        ? "projects.planViews.saveName"
                        : "projects.planViews.saveType",
                      editor.mode === "create"
                        ? "创建视图"
                        : editor.mode === "rename"
                        ? "保存名称"
                        : "保存类型",
                    )}
                  </Button>
                  <Button
                    disabled={controlsDisabled}
                    onClick={() => requestClose("editor")}
                  >
                    {t("projects.planViews.cancelEdit", "取消编辑")}
                  </Button>
                </div>
              </section>
            )}
            {comparison && (
              <section
                className={styles.comparison}
                aria-label={t(
                  "projects.planViews.compareTitle",
                  "比较共享视图",
                )}
              >
                <div className={styles.compareColumns}>
                  <section>
                    <h3>
                      {t("projects.planViews.currentShared", "当前共享设置")}
                    </h3>
                    <strong>{comparison.baseline.view.name}</strong>
                    <p>
                      {t(
                        "projects.planViews.types." +
                          comparison.baseline.view.type,
                        TYPE_NAMES[comparison.baseline.view.type],
                      )}
                    </p>
                    <PlanDefinitionSummary
                      definition={comparison.baseline.view.definition}
                    />
                    {compareContext?.kind === "collection" &&
                      !comparisonStale && (
                        <>
                          <p>
                            {t("projects.planViews.activeViews", "有效视图")}
                          </p>
                          <ol>
                            {activeViews(props.views).map((view) => (
                              <li key={view.view_id}>
                                {view.name}
                                {view.view_id === props.defaultViewId &&
                                  " · " +
                                    t(
                                      "projects.planViews.defaultView",
                                      "项目默认",
                                    )}
                              </li>
                            ))}
                          </ol>
                        </>
                      )}
                  </section>
                  <section>
                    <h3>{t("projects.planViews.localDraft", "本地草稿")}</h3>
                    <strong>
                      {editor?.name ??
                        getBaseline(compareContext?.viewId ?? "")?.view.name}
                    </strong>
                    <PlanDefinitionSummary
                      definition={
                        editor?.definition ??
                        getBaseline(compareContext?.viewId ?? "")?.view
                          .definition ??
                        comparison.baseline.view.definition
                      }
                    />
                    {compareContext?.kind === "collection" && (
                      <ol>
                        {order.map((id) => (
                          <li key={id}>
                            {
                              snapshot.views.find((view) => view.view_id === id)
                                ?.name
                            }
                          </li>
                        ))}
                      </ol>
                    )}
                  </section>
                </div>
                {comparisonStale && (
                  <Alert
                    type="warning"
                    message={t(
                      "projects.planViews.comparisonStale",
                      "比较结果已失效，请重新刷新比较。",
                    )}
                  />
                )}
                <Button
                  disabled={controlsDisabled || comparisonStale}
                  onClick={confirmComparison}
                >
                  {t(
                    "projects.planViews.confirmCompare",
                    "确认采用当前值继续编辑",
                  )}
                </Button>
              </section>
            )}
          </div>
          <div className={styles.footer}>
            <div className={styles.actions}>
              <Button
                disabled={
                  controlsDisabled ||
                  !manager ||
                  collectionBlocked ||
                  !orderDirty ||
                  !!editor
                }
                onClick={saveOrder}
              >
                {t("projects.planViews.saveOrder", "保存顺序")}
              </Button>
              <Button
                disabled={controlsDisabled || !snapshot.views.length}
                onClick={() => {
                  void refreshCompare();
                }}
              >
                {t("projects.planViews.refreshCompare", "刷新后比较")}
              </Button>
              <Button
                aria-label={t("projects.planViews.cancel", "取消")}
                disabled={controlsDisabled}
                onClick={() => requestClose("manager")}
              >
                {t("projects.planViews.cancel", "取消")}
              </Button>
            </div>
          </div>
        </div>
      </Modal>
      <Modal
        open={closeTarget !== null}
        title={t(
          "projects.planViews.draftCloseTitle",
          "保留或舍弃未应用的草稿",
        )}
        footer={null}
        maskClosable={false}
        onCancel={() => setCloseTarget(null)}
        afterClose={() => {
          if (cancelFocus.current?.isConnected) cancelFocus.current.focus();
        }}
      >
        <p>
          {t(
            "projects.planViews.draftCloseHint",
            "这些修改尚未应用。继续编辑可保留输入，舍弃后关闭。",
          )}
        </p>
        <div className={styles.actions}>
          <Button onClick={() => setCloseTarget(null)}>
            {t("projects.planViews.continueEditing", "继续编辑")}
          </Button>
          <Button
            danger
            onClick={() => {
              if (closeTarget === "editor") {
                setEditor(null);
                setReviewId(null);
                resetComparison();
              } else setOpen(false);
              setCloseTarget(null);
            }}
          >
            {t("projects.planViews.discardDraft", "舍弃草稿")}
          </Button>
        </div>
      </Modal>
    </>
  );
}
