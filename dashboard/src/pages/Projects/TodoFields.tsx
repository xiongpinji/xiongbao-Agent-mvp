import { useRef, useState } from "react";
import { Alert, Button, Modal } from "antd";
import { useTranslation } from "react-i18next";
import type { ProjectTodoCatalog } from "../../api/modules/projectTodoCatalog";
import { isNotFoundApiError } from "../../utils/apiError";
import TodoCatalogManager from "./TodoCatalogManager";
import {
  addPlanDays,
  isPlanDate,
  planMonthGrid,
  shiftPlanMonth,
  validatePlanDates,
} from "./planDates";
import styles from "./TodoFields.module.less";
export interface TodoFieldValues {
  start_date: string | null;
  due_date: string | null;
  priority_id: string | null;
  tag_ids: string[];
}
export interface TodoFieldsProps {
  values: TodoFieldValues;
  original?: TodoFieldValues;
  catalog: ProjectTodoCatalog | null;
  loading?: boolean;
  error?: unknown;
  onRetry?: () => unknown | Promise<unknown>;
  onChange?: (values: TodoFieldValues) => void;
  onCatalogChanged?: () => unknown | Promise<unknown>;
  projectId: string;
  accountId: number | null;
  canManage: boolean;
  disabled?: boolean;
  readOnly?: boolean;
}
export const emptyTodoFields = (): TodoFieldValues => ({
  start_date: null,
  due_date: null,
  priority_id: null,
  tag_ids: [],
});
export const todoFieldsEqual = (a: TodoFieldValues, b: TodoFieldValues) =>
  a.start_date === b.start_date &&
  a.due_date === b.due_date &&
  a.priority_id === b.priority_id &&
  JSON.stringify([...a.tag_ids].sort()) ===
    JSON.stringify([...b.tag_ids].sort());

export default function TodoFields({
  values,
  original = values,
  catalog,
  loading = false,
  error,
  onRetry,
  onChange,
  onCatalogChanged,
  projectId,
  accountId,
  canManage,
  disabled = false,
  readOnly = false,
}: TodoFieldsProps) {
  const { t } = useTranslation();
  const [picker, setPicker] = useState<"priority" | "tag" | null>(null);
  const [search, setSearch] = useState("");
  const [manager, setManager] = useState(false);
  const managementKey = JSON.stringify([accountId, projectId]);
  const retainedCatalog = useRef<{
    key: string;
    data: ProjectTodoCatalog | null;
  }>({ key: managementKey, data: null });
  if (retainedCatalog.current.key !== managementKey)
    retainedCatalog.current = { key: managementKey, data: null };
  if (isNotFoundApiError(error) || (!catalog && !loading && !error))
    retainedCatalog.current.data = null;
  else if (catalog?.project_id === projectId)
    retainedCatalog.current.data = catalog;
  // A retained snapshot keeps only the management draft mounted; field names and writes
  // continue to require the current verified catalog supplied by the caller.
  const managementCatalog = retainedCatalog.current.data;
  const [selectionError, setSelectionError] = useState<string | null>(null);
  const [calendarField, setCalendarField] = useState<
    "start_date" | "due_date" | null
  >(null);
  const [calendarMonth, setCalendarMonth] = useState("");
  const [calendarYear, setCalendarYear] = useState("");
  const dateTriggers = useRef<
    Partial<
      Record<"start_date" | "due_date", HTMLButtonElement | HTMLAnchorElement>
    >
  >({});
  const priorityTrigger = useRef<HTMLButtonElement | null>(null);
  const tagTrigger = useRef<HTMLButtonElement | null>(null);
  const metadataReady =
    !!catalog &&
    !loading &&
    !error &&
    isPlanDate(catalog.server_today) &&
    !!catalog.server_timezone;
  const dateControlsDisabled = disabled || !metadataReady;
  const patch = (next: Partial<TodoFieldValues>) =>
    onChange?.({ ...values, ...next });
  const closePicker = () => {
    const trigger =
      picker === "priority" ? priorityTrigger.current : tagTrigger.current;
    setPicker(null);
    setSearch("");
    setSelectionError(null);
    queueMicrotask(() => trigger?.isConnected && trigger.focus());
  };
  const closeCalendar = () => {
    const trigger = calendarField ? dateTriggers.current[calendarField] : null;
    setCalendarField(null);
    queueMicrotask(() => trigger?.isConnected && trigger.focus());
  };
  const openCalendar = (field: "start_date" | "due_date") => {
    if (dateControlsDisabled) return;
    const value = values[field];
    const initial = isPlanDate(value) ? value : catalog.server_today;
    setCalendarMonth(initial.slice(0, 7));
    setCalendarYear(initial.slice(0, 4));
    setCalendarField(field);
  };
  const calendarDisabled = (day: string) =>
    dateControlsDisabled ||
    (calendarField === "due_date" &&
      !!catalog &&
      day < catalog.server_today &&
      day !== original.due_date);
  const chooseCalendarDate = (day: string | null) => {
    if (
      !calendarField ||
      dateControlsDisabled ||
      (day !== null && calendarDisabled(day))
    )
      return;
    patch({ [calendarField]: day });
    closeCalendar();
  };
  const changeMonth = (month: string) => {
    setCalendarMonth(month);
    setCalendarYear(month.slice(0, 4));
  };
  const priority = catalog?.priorities.find(
    (item) => item.priority_id === values.priority_id,
  );
  const selectedTags =
    catalog?.tags.filter((item) => values.tag_ids.includes(item.tag_id)) ?? [];
  const availablePriorities =
    catalog?.priorities.filter(
      (item) =>
        item.archived_at === null || item.priority_id === original.priority_id,
    ) ?? [];
  const availableTags =
    catalog?.tags.filter(
      (item) =>
        item.archived_at === null || original.tag_ids.includes(item.tag_id),
    ) ?? [];
  const matches = (name: string) =>
    name
      .normalize("NFKC")
      .toLocaleLowerCase()
      .includes(search.trim().normalize("NFKC").toLocaleLowerCase());
  const archivedLabel = t("projects.todoFields.archived", "已停用");
  const itemLabel = (item: { name: string; archived_at: number | null }) =>
    item.archived_at === null ? item.name : `${item.name} · ${archivedLabel}`;
  const dateError = metadataReady
    ? validatePlanDates(values, catalog.server_today, original.due_date)
    : null;
  const noValue = t("projects.todoFields.none", "无");
  const renderPriority = () =>
    values.priority_id === null ? (
      noValue
    ) : priority ? (
      <span className={styles.chip} data-color={priority.color}>
        {itemLabel(priority)}
      </span>
    ) : (
      t("projects.todoFields.catalogLoading", "正在加载目录…")
    );
  const renderTags = () =>
    values.tag_ids.length === 0
      ? noValue
      : selectedTags.length === values.tag_ids.length
      ? selectedTags.map((item) => (
          <span
            key={item.tag_id}
            className={styles.chip}
            data-color={item.color}
          >
            {itemLabel(item)}
          </span>
        ))
      : t("projects.todoFields.catalogLoading", "正在加载目录…");
  const dateMetadata = !metadataReady && (
    <div role="status" className={styles.metadata}>
      {loading
        ? t("projects.todoFields.dateLoading", "正在加载服务器日期…")
        : t(
            "projects.todoFields.dateLoadFailed",
            "服务器日期加载失败，重试后才能编辑日期。",
          )}
      {!loading && (
        <Button
          size="small"
          aria-label={t("common.retry", "重试")}
          onClick={() => void onRetry?.()}
        >
          {t("common.retry", "重试")}
        </Button>
      )}
    </div>
  );
  if (readOnly)
    return (
      <dl className={styles.summary}>
        <div>
          <dt>{t("projects.todoFields.startDate", "开始日期")}</dt>
          <dd>{values.start_date ?? noValue}</dd>
        </div>
        <div>
          <dt>{t("projects.todoFields.dueDate", "截止日期")}</dt>
          <dd>{values.due_date ?? noValue}</dd>
        </div>
        <div>
          <dt>{t("projects.todoFields.priority", "优先级")}</dt>
          <dd>{renderPriority()}</dd>
        </div>
        <div>
          <dt>{t("projects.todoFields.tags", "标签")}</dt>
          <dd>{renderTags()}</dd>
        </div>
      </dl>
    );
  return (
    <div
      className={styles.fields}
      onKeyDown={(event) => {
        // Nested field dialogs dismiss first, without closing the todo detail.
        if (event.key === "Escape" && (picker || manager || calendarField)) {
          event.stopPropagation();
          if (calendarField) closeCalendar();
          else if (!manager) closePicker();
        }
      }}
    >
      {(["start_date", "due_date"] as const).map((field) => (
        <label key={field} className={styles.dateField}>
          <span>
            {t(
              field === "start_date"
                ? "projects.todoFields.startDate"
                : "projects.todoFields.dueDate",
            )}
          </span>
          <input
            type="text"
            inputMode="numeric"
            placeholder="YYYY-MM-DD"
            maxLength={10}
            aria-label={t(
              field === "start_date"
                ? "projects.todoFields.startDate"
                : "projects.todoFields.dueDate",
            )}
            value={values[field] ?? ""}
            min={
              field === "start_date"
                ? "1900-01-01"
                : metadataReady
                ? catalog.server_today
                : "1900-01-01"
            }
            max="9999-12-31"
            disabled={dateControlsDisabled}
            onChange={(event) => {
              if (!dateControlsDisabled)
                patch({ [field]: event.target.value || null });
            }}
          />
          <Button
            ref={(node) => {
              dateTriggers.current[field] = node ?? undefined;
            }}
            size="small"
            disabled={dateControlsDisabled}
            aria-label={t(
              field === "start_date"
                ? "projects.todoFields.selectStartDate"
                : "projects.todoFields.selectDueDate",
            )}
            onClick={() => openCalendar(field)}
          >
            {t("projects.todoFields.openCalendar", "选择日期")}
          </Button>
          {field === "due_date" &&
            original.due_date !== null &&
            metadataReady &&
            original.due_date < catalog.server_today && (
              <Button
                size="small"
                disabled={disabled}
                onClick={() => {
                  if (!dateControlsDisabled)
                    patch({ due_date: original.due_date });
                }}
              >
                {t("projects.todoFields.keepOriginalDue", "保持原截止日期")}
              </Button>
            )}
          {values[field] !== null && (
            <Button
              size="small"
              disabled={dateControlsDisabled}
              aria-label={t(
                field === "start_date"
                  ? "projects.todoFields.clearStartDate"
                  : "projects.todoFields.clearDueDate",
              )}
              onClick={() => {
                if (!dateControlsDisabled) patch({ [field]: null });
              }}
            >
              {t("common.clear", "清空")}
            </Button>
          )}
        </label>
      ))}
      {dateMetadata}
      {dateError && (
        <Alert type="warning" message={t(`projects.todoFields.${dateError}`)} />
      )}
      <div className={styles.pickerField}>
        <span>{t("projects.todoFields.priority", "优先级")}</span>
        <button
          ref={priorityTrigger}
          type="button"
          className={styles.fieldButton}
          aria-label={t("projects.todoFields.selectPriority", "选择优先级")}
          disabled={disabled || !catalog || !!error || loading}
          onClick={() => {
            setPicker("priority");
            setSearch("");
          }}
        >
          {renderPriority()}
        </button>
      </div>
      <div className={styles.pickerField}>
        <span>{t("projects.todoFields.tags", "标签")}</span>
        <button
          ref={tagTrigger}
          type="button"
          className={styles.fieldButton}
          aria-label={t("projects.todoFields.selectTags", "选择标签")}
          disabled={disabled || !catalog || !!error || loading}
          onClick={() => {
            setPicker("tag");
            setSearch("");
          }}
        >
          {renderTags()}
        </button>
      </div>
      {calendarField && catalog && (
        <Modal
          open
          closable={{ "aria-label": t("common.close", "关闭") }}
          zIndex={1300}
          width={400}
          title={t(
            calendarField === "start_date"
              ? "projects.todoFields.startDate"
              : "projects.todoFields.dueDate",
          )}
          maskClosable={false}
          onCancel={closeCalendar}
          styles={{
            body: { maxHeight: "calc(100dvh - 230px)", overflowY: "auto" },
          }}
          footer={
            <div className={styles.actions}>
              <Button
                disabled={dateControlsDisabled}
                onClick={() => chooseCalendarDate(null)}
              >
                {t("common.clear", "清空")}
              </Button>
              <Button onClick={closeCalendar}>
                {t("common.cancel", "取消")}
              </Button>
            </div>
          }
        >
          {dateMetadata}
          <p className={styles.hint}>
            {catalog.server_today} · {catalog.server_timezone}
          </p>
          <div className={styles.calendarNavigation}>
            <Button
              aria-label={t("projects.todoFields.previousMonth", "上个月")}
              disabled={
                dateControlsDisabled || !shiftPlanMonth(calendarMonth, -1)
              }
              onClick={() => {
                if (dateControlsDisabled) return;
                const month = shiftPlanMonth(calendarMonth, -1);
                if (month) changeMonth(month);
              }}
            >
              ‹
            </Button>
            <label>
              {t("projects.todoFields.year", "年份")}
              <input
                type="number"
                min={1900}
                max={9999}
                value={calendarYear}
                disabled={dateControlsDisabled}
                onChange={(event) => {
                  if (dateControlsDisabled) return;
                  const year = event.target.value;
                  setCalendarYear(year);
                  if (
                    /^\d{4}$/.test(year) &&
                    Number(year) >= 1900 &&
                    Number(year) <= 9999
                  )
                    setCalendarMonth(`${year}-${calendarMonth.slice(5)}`);
                }}
                onBlur={() => setCalendarYear(calendarMonth.slice(0, 4))}
              />
            </label>
            <label>
              {t("projects.todoFields.month", "月份")}
              <select
                value={calendarMonth.slice(5)}
                disabled={dateControlsDisabled}
                onChange={(event) => {
                  if (dateControlsDisabled) return;
                  changeMonth(
                    `${calendarMonth.slice(0, 4)}-${event.target.value}`,
                  );
                }}
              >
                {Array.from({ length: 12 }, (_, index) => (
                  <option
                    key={index}
                    value={String(index + 1).padStart(2, "0")}
                  >
                    {index + 1}
                  </option>
                ))}
              </select>
            </label>
            <Button
              aria-label={t("projects.todoFields.nextMonth", "下个月")}
              disabled={
                dateControlsDisabled || !shiftPlanMonth(calendarMonth, 1)
              }
              onClick={() => {
                if (dateControlsDisabled) return;
                const month = shiftPlanMonth(calendarMonth, 1);
                if (month) changeMonth(month);
              }}
            >
              ›
            </Button>
          </div>
          <div
            role="grid"
            aria-label={calendarMonth}
            className={styles.calendar}
            onKeyDown={(event) => {
              if (dateControlsDisabled) return;
              const moves: Record<string, number> = {
                ArrowLeft: -1,
                ArrowRight: 1,
                ArrowUp: -7,
                ArrowDown: 7,
              };
              if (!(event.key in moves)) return;
              const current = (
                event.target as HTMLElement
              ).closest<HTMLButtonElement>("button[data-plan-date]");
              const day = current?.dataset.planDate;
              if (!day) return;
              event.preventDefault();
              const next = addPlanDays(day, moves[event.key]);
              if (next && !calendarDisabled(next)) {
                const target =
                  event.currentTarget.querySelector<HTMLButtonElement>(
                    `button[data-plan-date="${next}"]`,
                  );
                if (target) target.focus();
              }
            }}
          >
            <div role="row" className={styles.calendarRow}>
              {["mon", "tue", "wed", "thu", "fri", "sat", "sun"].map((day) => (
                <span role="columnheader" key={day}>
                  {t(`projects.todoFields.weekdays.${day}`)}
                </span>
              ))}
            </div>
            {Array.from({ length: 6 }, (_, row) => (
              <div role="row" key={row} className={styles.calendarRow}>
                {planMonthGrid(calendarMonth)
                  .slice(row * 7, row * 7 + 7)
                  .map((day, index) => (
                    <div role="gridcell" key={day ?? index}>
                      {day && (
                        <button
                          type="button"
                          data-plan-date={day}
                          aria-label={day}
                          aria-pressed={values[calendarField] === day}
                          disabled={calendarDisabled(day)}
                          className={
                            day.slice(0, 7) === calendarMonth
                              ? styles.calendarDay
                              : styles.otherMonth
                          }
                          onClick={() => chooseCalendarDate(day)}
                        >
                          {Number(day.slice(8))}
                        </button>
                      )}
                    </div>
                  ))}
              </div>
            ))}
          </div>
          <Button
            className={styles.todayButton}
            disabled={dateControlsDisabled}
            onClick={() => chooseCalendarDate(catalog.server_today)}
          >
            {t("projects.todoFields.today", "今天")}
          </Button>
        </Modal>
      )}
      {picker && (
        <Modal
          open
          centered
          closable={{ "aria-label": t("common.close", "关闭") }}
          zIndex={1300}
          width={420}
          title={t(
            picker === "priority"
              ? "projects.todoFields.priority"
              : "projects.todoFields.tags",
          )}
          maskClosable={false}
          onCancel={closePicker}
          styles={{
            body: { maxHeight: "calc(100dvh - 230px)", overflowY: "auto" },
          }}
          footer={
            <div className={styles.actions}>
              <Button disabled={!canManage} onClick={() => setManager(true)}>
                {t("projects.todoFields.manageCatalog", "管理目录")}
              </Button>
              <Button onClick={closePicker}>
                {t("projects.todoFields.doneSelection", "完成选择")}
              </Button>
            </div>
          }
        >
          <input
            type="search"
            autoFocus
            className={styles.search}
            aria-label={t("projects.todoFields.searchOptions", "搜索选项")}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          {!canManage && (
            <p className={styles.hint}>
              {t(
                "projects.todoFields.managePermission",
                "只有项目所有者和管理员可管理目录。",
              )}
            </p>
          )}
          {selectionError && <Alert type="warning" message={selectionError} />}
          {picker === "priority" ? (
            <div className={styles.optionList}>
              <button
                type="button"
                className={styles.option}
                onClick={() => {
                  patch({ priority_id: null });
                  closePicker();
                }}
              >
                {noValue}
              </button>
              {availablePriorities
                .filter((item) => matches(item.name))
                .map((item) => (
                  <button
                    type="button"
                    key={item.priority_id}
                    className={styles.option}
                    data-color={item.color}
                    disabled={
                      item.archived_at !== null &&
                      values.priority_id !== item.priority_id
                    }
                    onClick={() => {
                      patch({ priority_id: item.priority_id });
                      closePicker();
                    }}
                  >
                    {itemLabel(item)}
                  </button>
                ))}
            </div>
          ) : (
            <div className={styles.optionList}>
              <Button size="small" onClick={() => patch({ tag_ids: [] })}>
                {t("projects.todoFields.clearTags", "清空标签")}
              </Button>
              {availableTags
                .filter((item) => matches(item.name))
                .map((item) => {
                  const selected = values.tag_ids.includes(item.tag_id);
                  return (
                    <label
                      key={item.tag_id}
                      className={styles.option}
                      data-color={item.color}
                    >
                      <input
                        type="checkbox"
                        checked={selected}
                        disabled={item.archived_at !== null && !selected}
                        onChange={() => {
                          if (!selected && values.tag_ids.length >= 20) {
                            setSelectionError(
                              t(
                                "projects.todoFields.tagLimit",
                                "每条待办最多 20 个标签。",
                              ),
                            );
                            return;
                          }
                          setSelectionError(null);
                          patch({
                            tag_ids: selected
                              ? values.tag_ids.filter(
                                  (id) => id !== item.tag_id,
                                )
                              : [...values.tag_ids, item.tag_id].sort(),
                          });
                        }}
                      />
                      {itemLabel(item)}
                    </label>
                  );
                })}
            </div>
          )}
          {manager && managementCatalog && (
            <TodoCatalogManager
              projectId={projectId}
              accountId={accountId}
              canManage={canManage}
              catalog={managementCatalog}
              loading={loading}
              error={error}
              disabled={disabled || !catalog}
              kind={picker}
              onClose={() => setManager(false)}
              onRefresh={() => (onCatalogChanged ?? onRetry)?.()}
              onCreated={(kind, id) => {
                if (kind === "priority") patch({ priority_id: id });
                else if (values.tag_ids.length < 20)
                  patch({
                    tag_ids: [...new Set([...values.tag_ids, id])].sort(),
                  });
                setManager(false);
              }}
            />
          )}
        </Modal>
      )}
    </div>
  );
}
