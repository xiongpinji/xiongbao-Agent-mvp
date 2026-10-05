import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  ConfigProvider,
  Input,
  Modal,
  Select,
  Tag,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  normalizePlanDefinition,
  type AnyPlanDefinition,
  type PlanFilterClause,
  type PlanGroupBy,
  type PlanSortSpec,
  type PlanView,
  type PlanViewType,
  type PlanVisibleField,
} from "../../../api/modules/projectPlanViews";
import type { ProjectTodoCatalog } from "../../../api/modules/projectTodoCatalog";
import type { ProjectMember } from "../../../api/modules/projects";
import type {
  PlanViewActionBaseline,
  PlanViewActionResult,
  PlanViewComparison,
  PlanViewCompareResult,
  PlanViewSaveAction,
} from "./ProjectPlanViews";
import { isPlanDate } from "../planDates";
import styles from "./PlanViewSettings.module.less";

export interface PlanViewSettingsProps {
  view: PlanView;
  definition: AnyPlanDefinition;
  catalog: ProjectTodoCatalog | null;
  members: readonly ProjectMember[];
  isManager: boolean;
  conflictLocked: boolean;
  busy: boolean;
  baseline: PlanViewActionBaseline;
  comparison: PlanViewComparison | null;
  errorKey?: string | null;
  onTemporaryChange: (definition: AnyPlanDefinition) => void;
  onSave: (action: PlanViewSaveAction) => Promise<PlanViewActionResult>;
  onRefreshCompare: (
    baseline: PlanViewActionBaseline,
  ) => Promise<PlanViewCompareResult>;
  onConfirmCompare: (comparison: PlanViewComparison) => boolean;
  onCancel: () => void;
}

type Translate = ReturnType<typeof useTranslation>["t"];
type FilterField = PlanFilterClause["field"];
const FIELD_NAMES: Record<PlanVisibleField, string> = {
  title: "标题",
  status: "状态",
  assignee: "处理人",
  priority: "优先级",
  tags: "标签",
  start_date: "开始日期",
  due_date: "截止日期",
  created_at: "创建时间",
  updated_at: "更新时间",
  source: "来源",
};
const ALL_FIELDS = Object.keys(FIELD_NAMES) as PlanVisibleField[];
const SORT_FIELDS = ALL_FIELDS.filter(
  (field): field is PlanSortSpec["field"] =>
    field !== "tags" && field !== "source",
);
const FILTER_FIELDS: FilterField[] = [
  "title",
  "status",
  "assignee",
  "priority",
  "tags",
  "start_date",
  "due_date",
  "source",
];
const STATUS_VALUES = ["todo", "in_progress", "done"] as const;
const OPS: Record<string, string> = {
  contains: "包含",
  not_contains: "不包含",
  in: "属于",
  not_in: "不属于",
  any: "包含任意标签",
  all: "包含全部标签",
  none_of: "不含这些标签",
  is_empty: "为空",
  not_empty: "不为空",
  on: "等于日期",
  before: "早于日期",
  after: "晚于日期",
  between: "日期区间",
  overdue: "是否逾期",
};
const NULL_MEMBER = "__unassigned__";
const NULL_PRIORITY = "__no_priority__";
const copy = <T,>(value: T): T => structuredClone(value);
const same = (left: unknown, right: unknown) =>
  JSON.stringify(left) === JSON.stringify(right);
const fieldLabel = (field: PlanVisibleField, t: Translate) =>
  t("projects.planViews.fields." + field, FIELD_NAMES[field]);

export function planDefinitionForType(
  type: PlanViewType,
  previous?: AnyPlanDefinition,
): AnyPlanDefinition {
  const defaultFields: PlanVisibleField[] =
    type === "board"
      ? ["title", "status", "assignee", "priority", "tags"]
      : type === "gantt" || type === "calendar"
      ? ["title", "status", "assignee", "priority"]
      : [
          "title",
          "status",
          "assignee",
          "priority",
          "tags",
          "start_date",
          "due_date",
        ];
  const common = {
    schema_version: 1 as const,
    fields: copy(previous?.fields ?? defaultFields),
    filters: copy(previous?.filters ?? []),
    sort: copy(
      previous?.sort ?? [
        { field: "updated_at" as const, direction: "desc" as const },
      ],
    ),
  };
  if (type === "calendar")
    return {
      ...common,
      group_by: null,
      calendar: { date_basis: "due_date", mode: "month" },
    };
  if (type === "gantt")
    return { ...common, group_by: null, gantt: { zoom: "week" } };
  if (type === "board")
    return { ...common, group_by: previous?.group_by ?? "status" };
  if (type === "table")
    return {
      ...common,
      group_by: previous?.group_by ?? null,
      show_subtodos: previous?.show_subtodos === true,
    };
  return { ...common, group_by: previous?.group_by ?? null };
}

function filterOps(field: FilterField): string[] {
  if (field === "title") return ["contains", "not_contains"];
  if (field === "tags")
    return ["any", "all", "none_of", "is_empty", "not_empty"];
  if (field === "start_date" || field === "due_date")
    return [
      "on",
      "before",
      "after",
      "between",
      "is_empty",
      "not_empty",
      ...(field === "due_date" ? ["overdue"] : []),
    ];
  return ["in", "not_in"];
}

function newFilter(
  field: FilterField,
  op = filterOps(field)[0],
  previous?: PlanFilterClause,
): PlanFilterClause {
  const old = previous?.field === field ? previous : undefined;
  switch (field) {
    case "title":
      return {
        field,
        op: op === "not_contains" ? "not_contains" : "contains",
        value: old?.field === "title" ? old.value : "",
      };
    case "status":
      return {
        field,
        op: op === "not_in" ? "not_in" : "in",
        values: old?.field === "status" ? copy(old.values) : ["todo"],
      };
    case "assignee":
      return {
        field,
        op: op === "not_in" ? "not_in" : "in",
        values: old?.field === "assignee" ? copy(old.values) : [null],
      };
    case "priority":
      return {
        field,
        op: op === "not_in" ? "not_in" : "in",
        values: old?.field === "priority" ? copy(old.values) : [null],
      };
    case "source":
      return {
        field,
        op: op === "not_in" ? "not_in" : "in",
        values: old?.field === "source" ? copy(old.values) : ["manual"],
      };
    case "tags":
      if (op === "is_empty" || op === "not_empty") return { field, op };
      return {
        field,
        op: op === "all" || op === "none_of" ? op : "any",
        values:
          old?.field === "tags" && "values" in old ? copy(old.values) : [],
      };
    case "start_date":
    case "due_date":
      if (op === "is_empty" || op === "not_empty") return { field, op };
      if (field === "due_date" && op === "overdue")
        return {
          field,
          op,
          value:
            old?.field === "due_date" && old.op === "overdue"
              ? old.value
              : true,
        };
      if (op === "between")
        return {
          field,
          op,
          values: old && old.op === "between" ? copy(old.values) : ["", ""],
        };
      return {
        field,
        op: op === "before" || op === "after" ? op : "on",
        value:
          old && "value" in old && typeof old.value === "string"
            ? old.value
            : "",
      };
  }
}

function validateDefinition(
  definition: AnyPlanDefinition,
  type: PlanViewType,
  catalog: ProjectTodoCatalog | null,
  members: readonly ProjectMember[],
) {
  const unique = (values: readonly unknown[]) =>
    new Set(values).size === values.length;
  const priorities = new Set(
    catalog?.priorities.map((item) => item.priority_id),
  );
  const tags = new Set(catalog?.tags.map((item) => item.tag_id));
  const memberIds = new Set(members.map((member) => member.user_id));
  const lostAssignees: number[] = [];
  const invalidFilters: number[] = [];
  definition.filters.forEach((clause, index) => {
    let valid = filterOps(clause.field).includes(clause.op);
    const valueKeys =
      clause.op === "is_empty" || clause.op === "not_empty"
        ? []
        : clause.field === "title" ||
          clause.op === "on" ||
          clause.op === "before" ||
          clause.op === "after" ||
          clause.op === "overdue"
        ? ["value"]
        : ["values"];
    const expectedKeys = ["field", "op", ...valueKeys];
    valid &&=
      Object.keys(clause).length === expectedKeys.length &&
      Object.keys(clause).every((key) => expectedKeys.includes(key));
    if (clause.field === "title")
      valid &&=
        [...clause.value].length >= 1 && [...clause.value].length <= 200;
    else if (clause.field === "status")
      valid &&=
        clause.values.length >= 1 &&
        clause.values.length <= 3 &&
        unique(clause.values) &&
        clause.values.every((value) => STATUS_VALUES.includes(value));
    else if (clause.field === "assignee") {
      const lost = clause.values.some(
        (value) => value !== null && !memberIds.has(value),
      );
      if (lost) lostAssignees.push(index);
      valid &&=
        clause.values.length >= 1 &&
        clause.values.length <= 50 &&
        unique(clause.values) &&
        clause.values.every(
          (value) =>
            value === null ||
            (Number.isSafeInteger(value) && value > 0 && memberIds.has(value)),
        );
    } else if (clause.field === "priority")
      valid &&=
        clause.values.length >= 1 &&
        clause.values.length <= 32 &&
        unique(clause.values) &&
        clause.values.every((value) => value === null || priorities.has(value));
    else if (clause.field === "source")
      valid &&= clause.values.length === 1 && clause.values[0] === "manual";
    else if (clause.field === "tags") {
      if ("values" in clause)
        valid &&=
          clause.values.length >= 1 &&
          clause.values.length <= 20 &&
          unique(clause.values) &&
          clause.values.every((value) => tags.has(value));
    } else if (clause.op === "between")
      valid &&=
        clause.values.length === 2 &&
        isPlanDate(clause.values[0]) &&
        isPlanDate(clause.values[1]) &&
        clause.values[0] <= clause.values[1];
    else if (clause.op === "overdue")
      valid &&= typeof clause.value === "boolean";
    else if ("value" in clause) valid &&= isPlanDate(clause.value);
    if (!valid) invalidFilters.push(index);
  });
  const allowedGroups: PlanGroupBy[] = [
    null,
    "status",
    "assignee",
    "priority",
    "tag",
    "source",
  ];
  const definitionKeys = [
    "schema_version",
    "fields",
    "group_by",
    "filters",
    "sort",
    ...(type === "table" ? ["show_subtodos"] : []),
    ...(type === "calendar" ? ["calendar"] : type === "gantt" ? ["gantt"] : []),
  ];
  let general =
    Object.keys(definition).length === definitionKeys.length &&
    Object.keys(definition).every((key) => definitionKeys.includes(key)) &&
    definition.schema_version === 1 &&
    definition.fields[0] === "title" &&
    unique(definition.fields) &&
    definition.fields.every((field) => ALL_FIELDS.includes(field)) &&
    allowedGroups.includes(definition.group_by) &&
    definition.filters.length <= 12 &&
    definition.sort.length <= 3 &&
    unique(definition.sort.map((sort) => sort.field)) &&
    definition.sort.every(
      (sort) =>
        Object.keys(sort).length === 2 &&
        Object.keys(sort).every(
          (key) => key === "field" || key === "direction",
        ) &&
        SORT_FIELDS.includes(sort.field) &&
        (sort.direction === "asc" || sort.direction === "desc"),
    );
  if (type === "board") general &&= definition.group_by !== null;
  if (type === "table")
    general &&= typeof definition.show_subtodos === "boolean";
  if (type === "calendar")
    general &&=
      definition.group_by === null &&
      !!definition.calendar &&
      Object.keys(definition.calendar).length === 2 &&
      Object.keys(definition.calendar).every(
        (key) => key === "date_basis" || key === "mode",
      ) &&
      ["start_date", "due_date"].includes(definition.calendar.date_basis) &&
      ["month", "week"].includes(definition.calendar.mode) &&
      !definition.gantt;
  else if (type === "gantt")
    general &&=
      definition.group_by === null &&
      !!definition.gantt &&
      Object.keys(definition.gantt).length === 1 &&
      ["day", "week", "month"].includes(definition.gantt.zoom) &&
      !definition.calendar;
  else general &&= !definition.gantt && !definition.calendar;
  return {
    general,
    invalidFilters,
    lostAssignees,
    valid: general && invalidFilters.length === 0 && catalog !== null,
  };
}

function filterValueText(
  clause: PlanFilterClause,
  catalog: ProjectTodoCatalog | null,
  members: readonly ProjectMember[],
  t: Translate,
): string {
  const unavailable = t("projects.planViews.unavailableOption", "不可用选项");
  if ("value" in clause)
    return typeof clause.value === "boolean"
      ? t(
          clause.value
            ? "projects.planViews.overdueYes"
            : "projects.planViews.overdueNo",
          clause.value ? "逾期" : "未逾期",
        )
      : clause.value;
  if (!("values" in clause)) return "";
  if (clause.field === "status")
    return clause.values
      .map((value) => t("projects.planViews.status." + value))
      .join("、");
  if (clause.field === "assignee")
    return clause.values
      .map((value) =>
        value === null
          ? t("projects.planViews.unassigned", "未指派")
          : members.find((member) => member.user_id === value)?.username ??
            unavailable,
      )
      .join("、");
  if (clause.field === "priority")
    return clause.values
      .map((value) =>
        value === null
          ? t("projects.planViews.noPriority", "无优先级")
          : catalog?.priorities.find((item) => item.priority_id === value)
              ?.name ?? unavailable,
      )
      .join("、");
  if (clause.field === "tags")
    return clause.values
      .map(
        (value) =>
          catalog?.tags.find((item) => item.tag_id === value)?.name ??
          unavailable,
      )
      .join("、");
  if (clause.field === "source")
    return t("projects.planViews.manualSource", "手动创建");
  return clause.values.join(" — ");
}

export function PlanDefinitionSummary({
  definition,
  catalog = null,
  members = [],
}: {
  definition: AnyPlanDefinition;
  catalog?: ProjectTodoCatalog | null;
  members?: readonly ProjectMember[];
}) {
  const { t } = useTranslation();
  return (
    <dl className={styles.summary}>
      <dt>{t("projects.planViews.visibleFields", "显示字段")}</dt>
      <dd>
        {definition.fields.map((field) => fieldLabel(field, t)).join("、")}
      </dd>
      <dt>{t("projects.planViews.groupBy", "分组方式")}</dt>
      <dd>
        {t("projects.planViews.groups." + (definition.group_by ?? "none"))}
      </dd>
      {"show_subtodos" in definition && (
        <>
          <dt>{t("projects.planViews.showSubtodos", "显示子待办")}</dt>
          <dd>
            {t(definition.show_subtodos ? "common.enabled" : "common.disabled")}
          </dd>
        </>
      )}
      <dt>{t("projects.planViews.filtersTitle", "筛选条件")}</dt>
      <dd>
        {definition.filters.length ? (
          <ol>
            {definition.filters.map((clause, index) => (
              <li key={index}>
                {fieldLabel(clause.field, t)}{" "}
                {t("projects.planViews.ops." + clause.op, OPS[clause.op])}{" "}
                {filterValueText(clause, catalog, members, t)}
              </li>
            ))}
          </ol>
        ) : (
          t("projects.planViews.noFilters", "未设置筛选条件")
        )}
      </dd>
      <dt>{t("projects.planViews.sortTitle", "排序")}</dt>
      <dd>
        {definition.sort.length
          ? definition.sort
              .map(
                (sort) =>
                  fieldLabel(sort.field, t) +
                  " " +
                  t(
                    sort.direction === "asc"
                      ? "projects.planViews.sortAsc"
                      : "projects.planViews.sortDesc",
                    sort.direction === "asc" ? "升序" : "降序",
                  ),
              )
              .join("、")
          : t("projects.planViews.defaultSort", "默认按更新时间降序")}
      </dd>
      {definition.calendar && (
        <>
          <dt>{t("projects.planViews.calendarBasis", "日历日期依据")}</dt>
          <dd>
            {fieldLabel(definition.calendar.date_basis, t)} ·{" "}
            {t("projects.planViews.dateModes." + definition.calendar.mode)}
          </dd>
        </>
      )}
      {definition.gantt && (
        <>
          <dt>{t("projects.planViews.ganttZoom", "甘特缩放")}</dt>
          <dd>{t("projects.planViews.dateModes." + definition.gantt.zoom)}</dd>
        </>
      )}
    </dl>
  );
}

function FilterValueEditor({
  clause,
  index,
  catalog,
  members,
  disabled,
  onChange,
}: {
  clause: PlanFilterClause;
  index: number;
  catalog: ProjectTodoCatalog | null;
  members: readonly ProjectMember[];
  disabled: boolean;
  onChange: (clause: PlanFilterClause) => void;
}) {
  const { t } = useTranslation();
  const label = t("projects.planViews.filterValueNamed", "筛选值 {{index}}", {
    index: index + 1,
  });
  const archivedLabel = (name: string, archived: number | null) =>
    archived === null
      ? name
      : name + "（" + t("projects.planViews.archived", "已停用") + "）";
  const common = {
    "aria-label": label,
    virtual: false,
    disabled,
    className: styles.value,
    optionFilterProp: "label",
  };
  if (clause.field === "title")
    return (
      <Input
        aria-label={label}
        value={clause.value}
        disabled={disabled}
        onChange={(event) => onChange({ ...clause, value: event.target.value })}
      />
    );
  if (clause.field === "status")
    return (
      <Select
        {...common}
        mode="multiple"
        value={[...clause.values]}
        options={STATUS_VALUES.map((value) => ({
          value,
          label: t("projects.planViews.status." + value),
        }))}
        onChange={(values) => onChange({ ...clause, values })}
      />
    );
  if (clause.field === "assignee") {
    const missing = clause.values.filter(
      (value): value is number =>
        value !== null && !members.some((member) => member.user_id === value),
    );
    return (
      <Select
        {...common}
        mode="multiple"
        showSearch
        maxCount={50}
        value={clause.values.map((value) => value ?? NULL_MEMBER)}
        options={[
          {
            value: NULL_MEMBER,
            label: t("projects.planViews.unassigned", "未指派"),
          },
          ...members.map((member) => ({
            value: member.user_id,
            label: member.username,
          })),
          ...missing.map((value) => ({
            value,
            label: t("projects.planViews.unavailableOption", "不可用选项"),
            disabled: true,
          })),
        ]}
        onChange={(values: (number | string)[]) =>
          onChange({
            ...clause,
            values: values.map((value) =>
              value === NULL_MEMBER ? null : Number(value),
            ),
          })
        }
      />
    );
  }
  if (clause.field === "priority")
    return (
      <Select
        {...common}
        mode="multiple"
        showSearch
        maxCount={32}
        value={clause.values.map((value) => value ?? NULL_PRIORITY)}
        options={[
          {
            value: NULL_PRIORITY,
            label: t("projects.planViews.noPriority", "无优先级"),
          },
          ...(catalog?.priorities ?? []).map((item) => ({
            value: item.priority_id,
            label: archivedLabel(item.name, item.archived_at),
          })),
        ]}
        onChange={(values: string[]) =>
          onChange({
            ...clause,
            values: values.map((value) =>
              value === NULL_PRIORITY ? null : value,
            ),
          })
        }
      />
    );
  if (clause.field === "source")
    return (
      <Select
        {...common}
        mode="multiple"
        value={[...clause.values]}
        options={[
          {
            value: "manual",
            label: t("projects.planViews.manualSource", "手动创建"),
          },
        ]}
        onChange={(values: "manual"[]) => onChange({ ...clause, values })}
      />
    );
  if (clause.field === "tags")
    return "values" in clause ? (
      <Select
        {...common}
        mode="multiple"
        showSearch
        maxCount={20}
        value={[...clause.values]}
        options={(catalog?.tags ?? []).map((item) => ({
          value: item.tag_id,
          label: archivedLabel(item.name, item.archived_at),
        }))}
        onChange={(values: string[]) => onChange({ ...clause, values })}
      />
    ) : null;
  if (clause.op === "overdue")
    return (
      <Select
        {...common}
        value={clause.value ? "true" : "false"}
        options={[
          { value: "true", label: t("projects.planViews.overdueYes", "逾期") },
          {
            value: "false",
            label: t("projects.planViews.overdueNo", "未逾期"),
          },
        ]}
        onChange={(value) => onChange({ ...clause, value: value === "true" })}
      />
    );
  if (clause.op === "between")
    return (
      <div className={styles.range}>
        <Input
          type="date"
          min="1900-01-01"
          max="9999-12-31"
          aria-label={t(
            "projects.planViews.filterStartNamed",
            "筛选起始日期 {{index}}",
            { index: index + 1 },
          )}
          value={clause.values[0]}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              ...clause,
              values: [event.target.value, clause.values[1]],
            })
          }
        />
        <Input
          type="date"
          min="1900-01-01"
          max="9999-12-31"
          aria-label={t(
            "projects.planViews.filterEndNamed",
            "筛选结束日期 {{index}}",
            { index: index + 1 },
          )}
          value={clause.values[1]}
          disabled={disabled}
          onChange={(event) =>
            onChange({
              ...clause,
              values: [clause.values[0], event.target.value],
            })
          }
        />
      </div>
    );
  if ("value" in clause)
    return (
      <Input
        type="date"
        min="1900-01-01"
        max="9999-12-31"
        aria-label={label}
        value={clause.value}
        disabled={disabled}
        onChange={(event) => onChange({ ...clause, value: event.target.value })}
      />
    );
  return null;
}

export default function PlanViewSettings(props: PlanViewSettingsProps) {
  return (
    <ConfigProvider prefixCls="octop" button={{ autoInsertSpace: false }}>
      <SettingsContent
        key={props.view.project_id + ":" + props.view.view_id}
        {...props}
      />
    </ConfigProvider>
  );
}

function SettingsContent(props: PlanViewSettingsProps) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const normalized = normalizePlanDefinition(props.view.type, props.definition);
  const [draft, setDraft] = useState(() => copy(normalized));
  const [baseline, setBaseline] = useState(() => copy(props.baseline));
  const [comparison, setComparison] = useState<PlanViewComparison | null>(null);
  const [compareRequested, setCompareRequested] = useState(false);
  const [remoteLocked, setRemoteLocked] = useState(false);
  const [pending, setPending] = useState(false);
  const [errorKey, setErrorKey] = useState<string | null>(null);
  const [confirmClose, setConfirmClose] = useState(false);
  const originalType = useRef(props.view.type).current;
  const appliedDefinition = useRef(copy(normalized));
  const latest = useRef(props);
  latest.current = props;
  const mounted = useRef(true),
    operation = useRef(0),
    pendingRef = useRef(false);
  const confirmedExternalLock = useRef(false);
  const returnFocus = useRef(
    document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null,
  );
  const cancelFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      operation.current += 1;
    };
  }, []);
  if (!props.conflictLocked) confirmedExternalLock.current = false;
  const typeChanged =
    props.view.type !== originalType || baseline.view.type !== originalType;
  const viewUnavailable =
    props.view.archived_at !== null || baseline.view.archived_at !== null;
  const catalogAdvanced =
    props.catalog !== null && props.catalog.revision > baseline.catalogRevision;
  const viewAdvanced =
    props.view.version > baseline.view.version ||
    props.view.archived_at !== baseline.view.archived_at;
  const locked =
    remoteLocked ||
    viewAdvanced ||
    catalogAdvanced ||
    (props.conflictLocked && !confirmedExternalLock.current);
  const validation = validateDefinition(
    draft,
    originalType,
    props.catalog,
    props.members,
  );
  const controlsDisabled = pending || props.busy;
  const saveDisabled =
    controlsDisabled ||
    !props.isManager ||
    locked ||
    viewUnavailable ||
    typeChanged ||
    !validation.valid ||
    same(
      draft,
      normalizePlanDefinition(baseline.view.type, baseline.view.definition),
    );
  const candidate = comparison ?? (compareRequested ? props.comparison : null);
  const candidateStale =
    candidate !== null &&
    (candidate.baseline.view.view_id !== baseline.view.view_id ||
      candidate.baseline.view.project_id !== baseline.view.project_id ||
      candidate.baseline.view.archived_at !== null ||
      props.view.version > candidate.baseline.view.version ||
      (props.view.version === candidate.baseline.view.version &&
        props.view.archived_at !== candidate.baseline.view.archived_at) ||
      (props.catalog !== null &&
        props.catalog.revision > candidate.baseline.catalogRevision));
  const safeMessage = (key: string | null | undefined, fallback: string) =>
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
  const update = (next: AnyPlanDefinition) => {
    setDraft(next);
    setErrorKey(null);
  };
  const replaceFilter = (index: number, clause: PlanFilterClause) =>
    update({
      ...draft,
      filters: draft.filters.map((value, position) =>
        position === index ? clause : value,
      ),
    });
  const requestClose = () => {
    if (pendingRef.current || latest.current.busy) return;
    if (!same(draft, appliedDefinition.current)) {
      cancelFocus.current =
        document.activeElement instanceof HTMLElement
          ? document.activeElement
          : null;
      setConfirmClose(true);
    } else setOpen(false);
  };
  const applyTemporary = () => {
    if (
      pendingRef.current ||
      latest.current.busy ||
      typeChanged ||
      viewUnavailable ||
      !validation.valid
    )
      return;
    try {
      props.onTemporaryChange(copy(draft));
      appliedDefinition.current = copy(draft);
      setErrorKey(null);
    } catch {
      setErrorKey("projects.planViews.saveFailed");
    }
  };
  const save = async () => {
    if (pendingRef.current || saveDisabled || !latest.current.isManager) return;
    const seq = ++operation.current;
    pendingRef.current = true;
    setPending(true);
    setErrorKey(null);
    try {
      const result = await props.onSave({
        baseline: copy(baseline),
        definition: copy(draft),
      });
      if (!mounted.current || seq !== operation.current) return;
      if (result.state === "saved") {
        appliedDefinition.current = copy(draft);
        setOpen(false);
      } else {
        setErrorKey(result.messageKey);
        if (
          result.state === "conflict" ||
          result.state === "stale" ||
          result.state === "forbidden"
        ) {
          setRemoteLocked(true);
          confirmedExternalLock.current = false;
          setComparison(null);
          setCompareRequested(false);
        }
      }
    } catch {
      if (mounted.current && seq === operation.current)
        setErrorKey("projects.planViews.saveFailed");
    } finally {
      if (mounted.current && seq === operation.current) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };
  const refreshCompare = async () => {
    if (pendingRef.current || latest.current.busy) return;
    const seq = ++operation.current;
    pendingRef.current = true;
    setPending(true);
    setErrorKey(null);
    setComparison(null);
    setCompareRequested(true);
    try {
      const result = await props.onRefreshCompare(copy(baseline));
      if (!mounted.current || seq !== operation.current) return;
      if (
        result.state === "ready" &&
        result.comparison.baseline.view.view_id === baseline.view.view_id &&
        result.comparison.baseline.view.project_id === baseline.view.project_id
      )
        setComparison(result.comparison);
      else
        setErrorKey(
          result.state === "ready"
            ? "projects.planViews.comparisonStale"
            : result.messageKey,
        );
    } catch {
      if (mounted.current && seq === operation.current)
        setErrorKey("projects.planViews.refreshFailed");
    } finally {
      if (mounted.current && seq === operation.current) {
        pendingRef.current = false;
        setPending(false);
      }
    }
  };
  const confirmComparison = () => {
    if (!candidate || candidateStale || controlsDisabled) return;
    if (candidate.baseline.view.type !== originalType) {
      setErrorKey("projects.planViews.typeChanged");
      return;
    }
    if (!props.onConfirmCompare(candidate)) {
      setComparison(null);
      setCompareRequested(false);
      setErrorKey("projects.planViews.comparisonStale");
      setRemoteLocked(true);
      return;
    }
    setBaseline(copy(candidate.baseline));
    confirmedExternalLock.current = true;
    setRemoteLocked(false);
    setErrorKey(null);
    setComparison(null);
    setCompareRequested(false);
  };
  const moveField = (index: number, delta: number) => {
    const nextIndex = index + delta;
    if (index <= 0 || nextIndex <= 0 || nextIndex >= draft.fields.length)
      return;
    const fields = [...draft.fields];
    [fields[index], fields[nextIndex]] = [fields[nextIndex], fields[index]];
    update({ ...draft, fields });
  };
  const moveSort = (index: number, delta: number) => {
    const nextIndex = index + delta;
    if (nextIndex < 0 || nextIndex >= draft.sort.length) return;
    const sort = [...draft.sort];
    [sort[index], sort[nextIndex]] = [sort[nextIndex], sort[index]];
    update({ ...draft, sort });
  };
  const groups: {
    value: Exclude<PlanGroupBy, null> | "none";
    label: string;
  }[] = [
    { value: "none", label: t("projects.planViews.groups.none", "不分组") },
    ...(["status", "assignee", "priority", "tag", "source"] as const).map(
      (value) => ({ value, label: t("projects.planViews.groups." + value) }),
    ),
  ];
  const dateView = originalType === "gantt" || originalType === "calendar";
  const availableSortFields = SORT_FIELDS.filter(
    (field) => !draft.sort.some((sort) => sort.field === field),
  );
  return (
    <>
      <Modal
        open={open}
        title={t("projects.planViews.settingsTitle", "视图设置")}
        onCancel={requestClose}
        footer={null}
        width={760}
        maskClosable={false}
        keyboard={!controlsDisabled}
        style={{ top: 32, maxWidth: "calc(100vw - 32px)" }}
        afterClose={() => {
          props.onCancel();
          if (returnFocus.current?.isConnected) returnFocus.current.focus();
        }}
      >
        <div className={styles.panel}>
          <div className={styles.settings}>
            <div className={styles.heading}>
              <strong>{baseline.view.name}</strong>
              {!same(
                draft,
                normalizePlanDefinition(
                  baseline.view.type,
                  baseline.view.definition,
                ),
              ) && <Tag>{t("projects.planViews.unsaved", "未保存调整")}</Tag>}
            </div>
            {!props.isManager && (
              <Alert
                type="info"
                showIcon
                message={t(
                  "projects.planViews.savePermission",
                  "只有项目所有者或管理员可以保存共享设置。",
                )}
              />
            )}
            {typeChanged && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.typeChanged",
                  "视图类型已更改。草稿已保留，请关闭设置后重新打开当前视图。",
                )}
              />
            )}
            {locked && !typeChanged && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  catalogAdvanced
                    ? "projects.planViews.catalogConflict"
                    : "projects.planViews.conflictHint",
                  catalogAdvanced
                    ? "目录选项已更新。草稿已保留，请刷新后比较并确认。"
                    : "共享视图已被修改。草稿已保留，请刷新后比较并确认。",
                )}
              />
            )}
            {(errorKey || props.errorKey) && (
              <Alert
                type="error"
                showIcon
                message={safeMessage(errorKey ?? props.errorKey, "saveFailed")}
              />
            )}
            {!props.catalog && (
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.planViews.catalogUnavailable",
                  "目录信息暂不可用",
                )}
              />
            )}
            {!validation.general && (
              <Alert
                type="error"
                showIcon
                message={t(
                  "projects.planViews.invalidDefinition",
                  "设置无效，请检查字段、筛选条件和排序。",
                )}
              />
            )}
            {originalType === "table" && (
              <Checkbox
                checked={draft.show_subtodos === true}
                disabled={controlsDisabled}
                onChange={(event) => {
                  if (draft.gantt || draft.calendar) return;
                  update({
                    ...draft,
                    show_subtodos: event.target.checked,
                  });
                }}
              >
                {t("projects.planViews.showSubtodos", "显示子待办")}
              </Checkbox>
            )}
            <fieldset className={styles.fields}>
              <legend>
                {t("projects.planViews.visibleFields", "显示字段")}
              </legend>
              <p className={styles.hint}>
                {t(
                  "projects.planViews.fieldsFixedTitle",
                  "标题固定在首位，其他字段可显示、隐藏或调整顺序。",
                )}
              </p>
              {[
                ...draft.fields,
                ...ALL_FIELDS.filter((field) => !draft.fields.includes(field)),
              ].map((field) => {
                const index = draft.fields.indexOf(field),
                  name = fieldLabel(field, t);
                return (
                  <div className={styles.fieldRow} key={field}>
                    <Checkbox
                      checked={index >= 0}
                      disabled={field === "title" || controlsDisabled}
                      onChange={(event) => {
                        if (field === "title") return;
                        update({
                          ...draft,
                          fields: event.target.checked
                            ? [...draft.fields, field]
                            : draft.fields.filter((value) => value !== field),
                        });
                      }}
                    >
                      {name}
                    </Checkbox>
                    {index > 0 && (
                      <div className={styles.rowActions}>
                        <Button
                          size="small"
                          aria-label={t(
                            "projects.planViews.fieldUpNamed",
                            "上移显示字段 {{name}}",
                            { name },
                          )}
                          disabled={controlsDisabled || index <= 1}
                          onClick={() => moveField(index, -1)}
                        >
                          ↑
                        </Button>
                        <Button
                          size="small"
                          aria-label={t(
                            "projects.planViews.fieldDownNamed",
                            "下移显示字段 {{name}}",
                            { name },
                          )}
                          disabled={
                            controlsDisabled ||
                            index === draft.fields.length - 1
                          }
                          onClick={() => moveField(index, 1)}
                        >
                          ↓
                        </Button>
                      </div>
                    )}
                  </div>
                );
              })}
            </fieldset>
            <label className={styles.control}>
              {t("projects.planViews.groupBy", "分组方式")}
              <Select
                aria-label={t("projects.planViews.groupBy", "分组方式")}
                virtual={false}
                disabled={controlsDisabled || dateView}
                value={draft.group_by ?? "none"}
                options={
                  dateView
                    ? groups.slice(0, 1)
                    : originalType === "board"
                    ? groups.slice(1)
                    : groups
                }
                onChange={(value) =>
                  update({
                    ...draft,
                    group_by: value === "none" ? null : value,
                  } as AnyPlanDefinition)
                }
              />
            </label>
            {draft.calendar && (
              <div className={styles.dateOptions}>
                <label className={styles.control}>
                  {t("projects.planViews.calendarBasis", "日历日期依据")}
                  <Select
                    aria-label={t(
                      "projects.planViews.calendarBasis",
                      "日历日期依据",
                    )}
                    virtual={false}
                    disabled={controlsDisabled}
                    value={draft.calendar.date_basis}
                    options={["due_date", "start_date"].map((value) => ({
                      value,
                      label: fieldLabel(value as "due_date" | "start_date", t),
                    }))}
                    onChange={(value: "due_date" | "start_date") => {
                      if (draft.calendar)
                        update({
                          ...draft,
                          calendar: { ...draft.calendar, date_basis: value },
                        });
                    }}
                  />
                </label>
                <label className={styles.control}>
                  {t("projects.planViews.calendarMode", "日历模式")}
                  <Select
                    aria-label={t(
                      "projects.planViews.calendarMode",
                      "日历模式",
                    )}
                    virtual={false}
                    disabled={controlsDisabled}
                    value={draft.calendar.mode}
                    options={["month", "week"].map((value) => ({
                      value,
                      label: t("projects.planViews.dateModes." + value),
                    }))}
                    onChange={(value: "month" | "week") => {
                      if (draft.calendar)
                        update({
                          ...draft,
                          calendar: { ...draft.calendar, mode: value },
                        });
                    }}
                  />
                </label>
              </div>
            )}
            {draft.gantt && (
              <label className={styles.control}>
                {t("projects.planViews.ganttZoom", "甘特缩放")}
                <Select
                  aria-label={t("projects.planViews.ganttZoom", "甘特缩放")}
                  virtual={false}
                  disabled={controlsDisabled}
                  value={draft.gantt.zoom}
                  options={["day", "week", "month"].map((value) => ({
                    value,
                    label: t("projects.planViews.dateModes." + value),
                  }))}
                  onChange={(value: "day" | "week" | "month") => {
                    if (draft.gantt)
                      update({ ...draft, gantt: { zoom: value } });
                  }}
                />
              </label>
            )}
            <section
              aria-label={t("projects.planViews.filtersTitle", "筛选条件")}
              className={styles.section}
            >
              <div className={styles.sectionHeading}>
                <h3>{t("projects.planViews.filtersTitle", "筛选条件")}</h3>
                <span>{draft.filters.length} / 12</span>
              </div>
              <p className={styles.hint}>
                {t("projects.planViews.filtersAnd", "满足所有条件（AND）")}
              </p>
              {draft.filters.map((clause, index) => (
                <div key={index} className={styles.filterRow}>
                  <div className={styles.filterControls}>
                    <Select
                      aria-label={t(
                        "projects.planViews.filterFieldNamed",
                        "筛选字段 {{index}}",
                        { index: index + 1 },
                      )}
                      virtual={false}
                      disabled={controlsDisabled}
                      value={clause.field}
                      options={FILTER_FIELDS.map((value) => ({
                        value,
                        label: fieldLabel(value, t),
                      }))}
                      onChange={(field: FilterField) =>
                        replaceFilter(index, newFilter(field))
                      }
                    />
                    <Select
                      aria-label={t(
                        "projects.planViews.filterOperatorNamed",
                        "筛选运算 {{index}}",
                        { index: index + 1 },
                      )}
                      virtual={false}
                      disabled={controlsDisabled}
                      value={clause.op}
                      options={filterOps(clause.field).map((value) => ({
                        value,
                        label: t("projects.planViews.ops." + value, OPS[value]),
                      }))}
                      onChange={(op: string) =>
                        replaceFilter(
                          index,
                          newFilter(clause.field, op, clause),
                        )
                      }
                    />
                    <Button
                      aria-label={t(
                        "projects.planViews.removeFilterNamed",
                        "移除筛选条件 {{index}}",
                        { index: index + 1 },
                      )}
                      disabled={controlsDisabled}
                      onClick={() =>
                        update({
                          ...draft,
                          filters: draft.filters.filter(
                            (_, position) => position !== index,
                          ),
                        })
                      }
                    >
                      {t(
                        "projects.planViews.removeFilterNamed",
                        "移除筛选条件 {{index}}",
                        { index: index + 1 },
                      )}
                    </Button>
                  </div>
                  <FilterValueEditor
                    clause={clause}
                    index={index}
                    catalog={props.catalog}
                    members={props.members}
                    disabled={controlsDisabled}
                    onChange={(next) => replaceFilter(index, next)}
                  />
                  {validation.lostAssignees.includes(index) ? (
                    <Alert
                      type="warning"
                      message={t(
                        "projects.planViews.filterUnavailable",
                        "筛选条件 {{index}} 中的处理人已离开项目。请明确移除此条件或选择当前成员。",
                        { index: index + 1 },
                      )}
                    />
                  ) : validation.invalidFilters.includes(index) ? (
                    <Alert
                      type="error"
                      message={t(
                        "projects.planViews.filterInvalid",
                        "筛选条件 {{index}} 无效，请检查运算和值。",
                        { index: index + 1 },
                      )}
                    />
                  ) : null}
                </div>
              ))}
              <Button
                disabled={controlsDisabled || draft.filters.length >= 12}
                onClick={() =>
                  update({
                    ...draft,
                    filters: [...draft.filters, newFilter("title")],
                  })
                }
              >
                {t("projects.planViews.addFilter", "添加筛选条件")}
              </Button>
            </section>
            <section
              aria-label={t("projects.planViews.sortTitle", "排序")}
              className={styles.section}
            >
              <div className={styles.sectionHeading}>
                <h3>{t("projects.planViews.sortTitle", "排序")}</h3>
                <span>{draft.sort.length} / 3</span>
              </div>
              {!draft.sort.length && (
                <p className={styles.hint}>
                  {t("projects.planViews.defaultSort", "默认按更新时间降序")}
                </p>
              )}
              {draft.sort.map((sort, index) => (
                <div key={index} className={styles.sortRow}>
                  <Select
                    aria-label={t(
                      "projects.planViews.sortFieldNamed",
                      "排序字段 {{index}}",
                      { index: index + 1 },
                    )}
                    virtual={false}
                    disabled={controlsDisabled}
                    value={sort.field}
                    options={SORT_FIELDS.filter(
                      (field) =>
                        field === sort.field ||
                        !draft.sort.some((other) => other.field === field),
                    ).map((value) => ({ value, label: fieldLabel(value, t) }))}
                    onChange={(field: PlanSortSpec["field"]) =>
                      update({
                        ...draft,
                        sort: draft.sort.map((value, position) =>
                          position === index ? { ...value, field } : value,
                        ),
                      })
                    }
                  />
                  <Select
                    aria-label={t(
                      "projects.planViews.sortDirectionNamed",
                      "排序方向 {{index}}",
                      { index: index + 1 },
                    )}
                    virtual={false}
                    disabled={controlsDisabled}
                    value={sort.direction}
                    options={[
                      {
                        value: "asc",
                        label: t("projects.planViews.sortAsc", "升序"),
                      },
                      {
                        value: "desc",
                        label: t("projects.planViews.sortDesc", "降序"),
                      },
                    ]}
                    onChange={(direction: "asc" | "desc") =>
                      update({
                        ...draft,
                        sort: draft.sort.map((value, position) =>
                          position === index ? { ...value, direction } : value,
                        ),
                      })
                    }
                  />
                  <div className={styles.rowActions}>
                    <Button
                      size="small"
                      aria-label={t(
                        "projects.planViews.sortUpNamed",
                        "上移排序 {{index}}",
                        { index: index + 1 },
                      )}
                      disabled={controlsDisabled || index === 0}
                      onClick={() => moveSort(index, -1)}
                    >
                      ↑
                    </Button>
                    <Button
                      size="small"
                      aria-label={t(
                        "projects.planViews.sortDownNamed",
                        "下移排序 {{index}}",
                        { index: index + 1 },
                      )}
                      disabled={
                        controlsDisabled || index === draft.sort.length - 1
                      }
                      onClick={() => moveSort(index, 1)}
                    >
                      ↓
                    </Button>
                    <Button
                      aria-label={t(
                        "projects.planViews.removeSortNamed",
                        "移除排序 {{index}}",
                        { index: index + 1 },
                      )}
                      disabled={controlsDisabled}
                      onClick={() =>
                        update({
                          ...draft,
                          sort: draft.sort.filter(
                            (_, position) => position !== index,
                          ),
                        })
                      }
                    >
                      ×
                    </Button>
                  </div>
                </div>
              ))}
              <Button
                disabled={
                  controlsDisabled ||
                  draft.sort.length >= 3 ||
                  !availableSortFields.length
                }
                onClick={() => {
                  if (availableSortFields[0])
                    update({
                      ...draft,
                      sort: [
                        ...draft.sort,
                        { field: availableSortFields[0], direction: "asc" },
                      ],
                    });
                }}
              >
                {t("projects.planViews.addSort", "添加排序")}
              </Button>
            </section>
            <p className={styles.hint}>
              {t(
                "projects.planViews.manualSourceHint",
                "目前支持手动创建的待办。",
              )}
            </p>
            <p className={styles.hint}>
              {t(
                "projects.planViews.unavailableFeatures",
                "附件与外部数据源尚待独立实现。",
              )}
            </p>
            {candidate && (
              <section
                className={styles.comparison}
                role="region"
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
                    <strong>{candidate.baseline.view.name}</strong>
                    <PlanDefinitionSummary
                      definition={candidate.baseline.view.definition}
                      catalog={props.catalog}
                      members={props.members}
                    />
                  </section>
                  <section>
                    <h3>{t("projects.planViews.localDraft", "本地草稿")}</h3>
                    <PlanDefinitionSummary
                      definition={draft}
                      catalog={props.catalog}
                      members={props.members}
                    />
                  </section>
                </div>
                {candidateStale && (
                  <Alert
                    type="warning"
                    message={t(
                      "projects.planViews.comparisonStale",
                      "比较结果已失效，请重新刷新比较。",
                    )}
                  />
                )}
                <Button
                  disabled={
                    controlsDisabled ||
                    candidateStale ||
                    candidate.baseline.view.type !== originalType
                  }
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
            <p className={styles.hint}>
              {t(
                "projects.planViews.temporaryHint",
                "临时调整仅影响当前会话，刷新后恢复共享设置。",
              )}
            </p>
            <div className={styles.actions}>
              <Button
                disabled={controlsDisabled || typeChanged}
                onClick={() =>
                  update(
                    copy(
                      normalizePlanDefinition(
                        props.view.type,
                        props.view.definition,
                      ),
                    ),
                  )
                }
              >
                {t("projects.planViews.resetShared", "恢复共享设置")}
              </Button>
              <Button
                disabled={
                  controlsDisabled ||
                  typeChanged ||
                  viewUnavailable ||
                  !validation.valid
                }
                onClick={applyTemporary}
              >
                {t("projects.planViews.applyTemporary", "应用临时调整")}
              </Button>
              <Button
                type="primary"
                disabled={saveDisabled}
                loading={pending}
                onClick={() => {
                  void save();
                }}
              >
                {t("projects.planViews.saveShared", "保存到共享视图")}
              </Button>
              <Button
                disabled={controlsDisabled}
                onClick={() => {
                  void refreshCompare();
                }}
              >
                {t("projects.planViews.refreshCompare", "刷新后比较")}
              </Button>
              <Button
                aria-label={t("projects.planViews.cancel", "取消")}
                disabled={controlsDisabled}
                onClick={requestClose}
              >
                {t("projects.planViews.cancel", "取消")}
              </Button>
            </div>
          </div>
        </div>
      </Modal>
      <Modal
        open={confirmClose}
        title={t(
          "projects.planViews.draftCloseTitle",
          "保留或舍弃未应用的草稿",
        )}
        onCancel={() => setConfirmClose(false)}
        footer={null}
        maskClosable={false}
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
          <Button onClick={() => setConfirmClose(false)}>
            {t("projects.planViews.continueEditing", "继续编辑")}
          </Button>
          <Button
            danger
            onClick={() => {
              setConfirmClose(false);
              setOpen(false);
            }}
          >
            {t("projects.planViews.discardDraft", "舍弃草稿")}
          </Button>
        </div>
      </Modal>
    </>
  );
}
