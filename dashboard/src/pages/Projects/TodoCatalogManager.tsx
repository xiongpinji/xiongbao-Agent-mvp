import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, Button, Modal } from "antd";
import { useTranslation } from "react-i18next";
import {
  projectTodoCatalogApi,
  type ProjectTodoCatalog,
  type TodoCatalogColor,
  type TodoPriority,
  type TodoTag,
} from "../../api/modules/projectTodoCatalog";
import {
  apiErrorMessage,
  isNotFoundApiError,
  parseApiError,
} from "../../utils/apiError";
import styles from "./TodoFields.module.less";
export interface TodoCatalogManagerProps {
  projectId: string;
  accountId: number | null;
  canManage: boolean;
  catalog: ProjectTodoCatalog;
  loading?: boolean;
  error?: unknown;
  disabled?: boolean;
  kind: "priority" | "tag";
  onClose: () => void;
  onRefresh: () => unknown | Promise<unknown>;
  onCreated?: (kind: "priority" | "tag", id: string) => void;
}
export const CATALOG_COLORS: TodoCatalogColor[] = [
  "red",
  "orange",
  "yellow",
  "green",
  "blue",
  "purple",
  "gray",
];

export function catalogErrorMessage(
  error: unknown,
  t: ReturnType<typeof useTranslation>["t"],
): string {
  const reason = parseApiError(error)?.details?.reason;
  const reasons = [
    "catalog_revision_conflict",
    "name_conflict",
    "active_limit",
    "total_limit",
    "project_archived",
    "invalid_priority",
    "invalid_tags",
    "invalid_dates",
    "no_change",
  ];
  if (typeof reason === "string" && reasons.includes(reason))
    return t(`projects.todoFields.errors.${reason}`);
  return apiErrorMessage(
    error,
    t("projects.todoFields.saveFailed", "保存失败，请重试。"),
    t,
  );
}
export function isTodoAccessLost(error: unknown): boolean {
  const reason = parseApiError(error)?.details?.reason;
  if (
    reason === "invalid_priority" ||
    reason === "invalid_tags" ||
    (error instanceof Error && /\b422\b/.test(error.message))
  )
    return false;
  return isNotFoundApiError(error);
}

export default function TodoCatalogManager(props: TodoCatalogManagerProps) {
  return (
    <TodoCatalogManagerContent
      key={JSON.stringify([props.accountId, props.projectId, props.kind])}
      {...props}
    />
  );
}
function TodoCatalogManagerContent({
  projectId,
  accountId,
  canManage,
  catalog,
  loading = false,
  error: catalogError,
  disabled = false,
  kind,
  onClose,
  onRefresh,
  onCreated,
}: TodoCatalogManagerProps) {
  const { t } = useTranslation();
  const key = JSON.stringify([accountId, projectId, kind]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const serverOrder = useMemo(
    () =>
      catalog.priorities
        .filter((item) => item.archived_at === null)
        .map((item) => item.priority_id),
    [catalog.priorities],
  );
  const orderBaseline = useRef({
    ids: serverOrder,
    revision: catalog.revision,
  });
  const latestCatalog = useRef(catalog);
  latestCatalog.current = catalog;
  const savedRevision = useRef(catalog.revision);
  const active = useRef(true);
  const [revision, setRevision] = useState(catalog.revision);
  const [formRevision, setFormRevision] = useState(catalog.revision);
  const [name, setName] = useState("");
  const [color, setColor] = useState<TodoCatalogColor>("blue");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingBaseline, setEditingBaseline] = useState<{
    name: string;
    color: TodoCatalogColor;
  } | null>(null);
  const [order, setOrder] = useState(serverOrder);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [comparedCatalog, setComparedCatalog] =
    useState<ProjectTodoCatalog | null>(null);
  const [comparisonReady, setComparisonReady] = useState(false);
  const busyRef = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);
  const items: (TodoPriority | TodoTag)[] =
    kind === "priority" ? catalog.priorities : catalog.tags;
  const idOf = (item: TodoPriority | TodoTag) =>
    "priority_id" in item ? item.priority_id : item.tag_id;
  const waitingForSavedSnapshot = catalog.revision < savedRevision.current;
  const snapshotReady =
    !loading && !waitingForSavedSnapshot && !catalogError && !disabled;
  const ordered = order
    .map((id) => catalog.priorities.find((item) => item.priority_id === id))
    .filter((item): item is TodoPriority => !!item);
  const orderChanged =
    JSON.stringify(order) !== JSON.stringify(orderBaseline.current.ids);
  const formDraft = editingId !== null || name !== "" || color !== "blue";
  const staleDraft =
    (editingId !== null && formRevision !== catalog.revision) ||
    (orderChanged && orderBaseline.current.revision !== catalog.revision);
  const comparisonRequired = conflict || staleDraft;
  const allowed = canManage && !busy && !comparisonRequired && snapshotReady;
  const compared =
    comparedCatalog !== null &&
    comparedCatalog.revision === catalog.revision &&
    snapshotReady;
  const validOrder =
    order.length === serverOrder.length &&
    order.every((id) => serverOrder.includes(id));
  const stillCurrent = () => active.current && currentKey.current === key;
  useEffect(() => {
    if (comparedCatalog && comparedCatalog.revision !== catalog.revision)
      setComparedCatalog(null);
    if (!snapshotReady || busy) return;
    if (staleDraft) setConflict(true);
    if (editingId === null && !conflict) setFormRevision(catalog.revision);
    if (editingId === null && !orderChanged && !conflict)
      setRevision((previous) => Math.max(previous, catalog.revision));
  }, [
    catalog.revision,
    comparedCatalog,
    snapshotReady,
    busy,
    staleDraft,
    formDraft,
    editingId,
    orderChanged,
    conflict,
  ]);
  useEffect(() => {
    if (waitingForSavedSnapshot || busy) return;
    if (JSON.stringify(order) === JSON.stringify(orderBaseline.current.ids)) {
      orderBaseline.current = { ids: serverOrder, revision: catalog.revision };
      setOrder(serverOrder);
    }
  }, [serverOrder, catalog.revision, order, waitingForSavedSnapshot, busy]);
  useEffect(() => {
    if (!comparisonReady || busy) return;
    setComparisonReady(false);
    if (snapshotReady) setComparedCatalog(catalog);
  }, [comparisonReady, busy, snapshotReady, catalog]);

  const mutate = async (
    operation: () => Promise<{ revision: number }>,
    createdKind?: "priority" | "tag",
    draftKind: "form" | "order" | "lifecycle" = "form",
  ) => {
    if (!allowed || busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(null);
    try {
      const response = await operation();
      if (!stillCurrent()) return;
      savedRevision.current = Math.max(
        savedRevision.current,
        response.revision,
      );
      setRevision(response.revision);
      const refreshed = await onRefresh();
      if (!stillCurrent()) return;
      if (createdKind && "item" in response) {
        const item = response.item as TodoPriority | TodoTag;
        if (refreshed !== false) onCreated?.(createdKind, idOf(item));
      }
      if (refreshed === false)
        setError(
          t(
            "projects.todoFields.catalogRefreshFailed",
            "目录已保存，但重新加载失败，请重试。",
          ),
        );
      else {
        setComparedCatalog(null);
        if (draftKind === "form") {
          setName("");
          setColor("blue");
          setEditingId(null);
          setEditingBaseline(null);
          setFormRevision(response.revision);
        } else if (draftKind === "order") {
          const latest = latestCatalog.current;
          const ids =
            latest.revision >= response.revision
              ? latest.priorities
                  .filter((item) => item.archived_at === null)
                  .map((item) => item.priority_id)
              : order;
          orderBaseline.current = {
            ids,
            revision: Math.max(response.revision, latest.revision),
          };
          setOrder(ids);
        }
      }
    } catch (reason: unknown) {
      if (!stillCurrent()) return;
      // Only the project/catalog GET is an access-revocation signal. An item 404 may be a stale id.
      if (isNotFoundApiError(reason)) {
        await onRefresh();
        if (!stillCurrent()) return;
      }
      setError(catalogErrorMessage(reason, t));
      if (reason instanceof Error && /\b409\b/.test(reason.message)) {
        setConflict(true);
        setComparedCatalog(null);
      }
    } finally {
      if (stillCurrent()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  };
  const save = () => {
    if (!allowed) return;
    const trimmed = name.trim();
    if (
      !trimmed ||
      Array.from(trimmed).length > 40 ||
      /[\p{Cc}\p{Cf}]/u.test(trimmed)
    ) {
      setError(
        t(
          "projects.todoFields.nameInvalid",
          "名称须为 1–40 个字符，且不能含控制字符。",
        ),
      );
      return;
    }
    const expectedRevision =
      editingId !== null ? formRevision : catalog.revision;
    const body = { expected_revision: expectedRevision, name: trimmed, color };
    if (editingId) {
      const patch = {
        expected_revision: expectedRevision,
        ...(trimmed !== editingBaseline?.name ? { name: trimmed } : {}),
        ...(color !== editingBaseline?.color ? { color } : {}),
      };
      if (!("name" in patch) && !("color" in patch)) return;
      void mutate(() =>
        kind === "priority"
          ? projectTodoCatalogApi.updatePriority(projectId, editingId, patch)
          : projectTodoCatalogApi.updateTag(projectId, editingId, patch),
      );
    } else
      void mutate(
        () =>
          kind === "priority"
            ? projectTodoCatalogApi.createPriority(projectId, body)
            : projectTodoCatalogApi.createTag(projectId, body),
        kind,
      );
  };
  const archive = (item: TodoPriority | TodoTag) => {
    const body = { expected_revision: revision },
      id = idOf(item);
    void mutate(
      () =>
        kind === "priority"
          ? item.archived_at === null
            ? projectTodoCatalogApi.archivePriority(projectId, id, body)
            : projectTodoCatalogApi.restorePriority(projectId, id, body)
          : item.archived_at === null
          ? projectTodoCatalogApi.archiveTag(projectId, id, body)
          : projectTodoCatalogApi.restoreTag(projectId, id, body),
      undefined,
      "lifecycle",
    );
  };
  const move = (index: number, delta: number) => {
    if (!allowed) return;
    if (!orderChanged)
      orderBaseline.current = { ids: serverOrder, revision: catalog.revision };
    setOrder((previous) => {
      const next = [...previous];
      [next[index], next[index + delta]] = [next[index + delta], next[index]];
      return next;
    });
  };
  const refreshCompare = async () => {
    if (busyRef.current || loading) return;
    busyRef.current = true;
    setBusy(true);
    setComparedCatalog(null);
    setComparisonReady(false);
    try {
      const result = await onRefresh();
      if (stillCurrent()) setComparisonReady(result !== false);
    } catch (reason: unknown) {
      if (stillCurrent()) setError(catalogErrorMessage(reason, t));
    } finally {
      if (stillCurrent()) {
        busyRef.current = false;
        setBusy(false);
      }
    }
  };
  return (
    <Modal
      open
      centered
      closable={{ "aria-label": t("common.close", "关闭") }}
      zIndex={1400}
      width={620}
      title={t("projects.todoFields.manageCatalog", "管理目录")}
      maskClosable={false}
      onCancel={busy ? undefined : onClose}
      footer={
        <Button onClick={onClose} disabled={busy}>
          {t("projects.todoFields.backToFields", "返回字段选择")}
        </Button>
      }
      styles={{
        body: { maxHeight: "calc(100dvh - 220px)", overflowY: "auto" },
      }}
    >
      {(loading || waitingForSavedSnapshot) && (
        <div role="status" aria-live="polite" className={styles.metadata}>
          {t("projects.todoFields.catalogLoading", "正在加载目录…")}
        </div>
      )}
      {!!catalogError && (
        <Alert
          type="error"
          message={apiErrorMessage(
            catalogError,
            t("projects.todoFields.catalogRequired", "请先加载项目目录。"),
            t,
          )}
          action={
            <Button disabled={busy || loading} onClick={() => void onRefresh()}>
              {t("common.retry", "重试")}
            </Button>
          }
        />
      )}
      {!canManage && (
        <Alert
          type="info"
          message={t(
            "projects.todoFields.managePermission",
            "只有项目所有者和管理员可管理目录。",
          )}
        />
      )}
      {comparisonRequired && (
        <Alert
          type="warning"
          message={t(
            "projects.todoFields.catalogConflict",
            "目录已被修改，草稿已保留，请刷新后比较再保存。",
          )}
          action={
            <Button
              disabled={busy || loading}
              onClick={() => void refreshCompare()}
            >
              {t("projects.todoFields.refreshCompare", "刷新后比较")}
            </Button>
          }
        />
      )}
      {error && <Alert type="error" message={error} />}
      {compared && (
        <section
          className={styles.compare}
          aria-label={t("projects.todoFields.serverValues", "服务器当前值")}
        >
          <strong>
            {t("projects.todoFields.serverValues", "服务器当前值")}
          </strong>
          <ul>
            {(kind === "priority"
              ? comparedCatalog.priorities
              : comparedCatalog.tags
            ).map((item) => (
              <li key={idOf(item)}>
                {item.name} · {t(`projects.todoFields.colors.${item.color}`)}{" "}
                {item.archived_at !== null &&
                  t("projects.todoFields.archived", "已停用")}
              </li>
            ))}
          </ul>
          <Button
            disabled={
              busy ||
              !snapshotReady ||
              (editingId !== null &&
                !(
                  kind === "priority"
                    ? comparedCatalog.priorities
                    : comparedCatalog.tags
                ).some((item) => idOf(item) === editingId))
            }
            onClick={() => {
              if (
                busy ||
                !snapshotReady ||
                !comparedCatalog ||
                comparedCatalog.revision !== catalog.revision
              )
                return;
              const current = (
                kind === "priority"
                  ? comparedCatalog.priorities
                  : comparedCatalog.tags
              ).find((item) => idOf(item) === editingId);
              if (editingId && !current) return;
              if (current && editingBaseline) {
                if (name.trim() === editingBaseline.name) setName(current.name);
                if (color === editingBaseline.color) setColor(current.color);
                setEditingBaseline({
                  name: current.name,
                  color: current.color,
                });
              }
              const ids = comparedCatalog.priorities
                .filter((item) => item.archived_at === null)
                .map((item) => item.priority_id);
              if (!orderChanged) setOrder(ids);
              orderBaseline.current = {
                ids,
                revision: comparedCatalog.revision,
              };
              setRevision(comparedCatalog.revision);
              setFormRevision(comparedCatalog.revision);
              setConflict(false);
              setComparedCatalog(null);
              setError(null);
            }}
          >
            {t("projects.todoFields.confirmCompare", "确认比较")}
          </Button>
        </section>
      )}
      <div className={styles.managerForm}>
        <label>
          {t("projects.todoFields.optionName", "选项名称")}
          <input
            value={name}
            maxLength={80}
            disabled={!canManage || busy || !snapshotReady}
            onChange={(event) => {
              if (!formDraft) setFormRevision(catalog.revision);
              setName(event.target.value);
            }}
          />
        </label>
        <label>
          {t("projects.todoFields.color", "颜色")}
          <select
            value={color}
            disabled={!canManage || busy || !snapshotReady}
            onChange={(event) => {
              if (!formDraft) setFormRevision(catalog.revision);
              setColor(event.target.value as TodoCatalogColor);
            }}
          >
            {CATALOG_COLORS.map((value) => (
              <option value={value} key={value}>
                {t(`projects.todoFields.colors.${value}`)}
              </option>
            ))}
          </select>
        </label>
        <div className={styles.actions}>
          <Button disabled={!allowed} onClick={save}>
            {editingId
              ? t("projects.todoFields.saveOption", "保存选项")
              : t("projects.todoFields.addOption", "新增选项")}
          </Button>
          {editingId && (
            <Button
              disabled={busy || !snapshotReady}
              onClick={() => {
                setEditingId(null);
                setEditingBaseline(null);
                setName("");
                setColor("blue");
                setFormRevision(catalog.revision);
                setComparedCatalog(null);
                if (!orderChanged) {
                  setRevision(catalog.revision);
                  setConflict(false);
                }
              }}
            >
              {t("common.cancel", "取消")}
            </Button>
          )}
        </div>
      </div>
      <ul className={styles.optionList}>
        {items.map((item) => (
          <li key={idOf(item)} className={styles.managerRow}>
            <span data-color={item.color} className={styles.chip}>
              {item.name}
              {item.archived_at !== null &&
                ` · ${t("projects.todoFields.archived", "已停用")}`}
            </span>
            <Button
              size="small"
              disabled={!allowed}
              aria-label={t("projects.todoFields.editOption", "编辑 {{name}}", {
                name: item.name,
              })}
              onClick={() => {
                setEditingId(idOf(item));
                setEditingBaseline({ name: item.name, color: item.color });
                setFormRevision(catalog.revision);
                setName(item.name);
                setColor(item.color);
              }}
            >
              {t("common.edit", "编辑")}
            </Button>
            <Button
              size="small"
              disabled={!allowed}
              aria-label={t(
                item.archived_at === null
                  ? "projects.todoFields.archiveOption"
                  : "projects.todoFields.restoreOption",
                { name: item.name },
              )}
              onClick={() => archive(item)}
            >
              {item.archived_at === null
                ? t("projects.todoFields.archive", "停用")
                : t("projects.todoFields.restore", "恢复")}
            </Button>
          </li>
        ))}
      </ul>
      {kind === "priority" && (
        <section className={styles.compare}>
          <strong>
            {t("projects.todoFields.priorityOrder", "优先级顺序")}
          </strong>
          <ol>
            {ordered.map((item, index) => (
              <li key={item.priority_id} className={styles.managerRow}>
                <span>{item.name}</span>
                <Button
                  size="small"
                  disabled={!allowed || index === 0}
                  aria-label={t("projects.todoFields.moveUp", "上移 {{name}}", {
                    name: item.name,
                  })}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </Button>
                <Button
                  size="small"
                  disabled={!allowed || index === order.length - 1}
                  aria-label={t(
                    "projects.todoFields.moveDown",
                    "下移 {{name}}",
                    { name: item.name },
                  )}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </Button>
              </li>
            ))}
          </ol>
          {!validOrder && (
            <Alert
              type="warning"
              message={t(
                "projects.todoFields.orderChanged",
                "有效选项已变化，请采用最新顺序后重新调整。",
              )}
            />
          )}
          <div className={styles.actions}>
            <Button
              disabled={!allowed || !orderChanged || !validOrder}
              onClick={() =>
                void mutate(
                  () =>
                    projectTodoCatalogApi.orderPriorities(projectId, {
                      expected_revision: orderBaseline.current.revision,
                      priority_ids: order,
                    }),
                  undefined,
                  "order",
                )
              }
            >
              {t("projects.todoFields.saveOrder", "保存顺序")}
            </Button>
            <Button
              disabled={busy || !snapshotReady}
              onClick={() => {
                if (!snapshotReady || busy) return;
                orderBaseline.current = {
                  ids: serverOrder,
                  revision: catalog.revision,
                };
                setOrder(serverOrder);
                setComparedCatalog(null);
                if (!formDraft) {
                  setRevision(catalog.revision);
                  setConflict(false);
                }
              }}
            >
              {t("projects.todoFields.useServerOrder", "使用最新顺序")}
            </Button>
          </div>
        </section>
      )}
    </Modal>
  );
}
