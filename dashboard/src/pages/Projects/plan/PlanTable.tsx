import type { PlanRendererProps } from "./ProjectPlanViews";
import type {
  PlanSortSpec,
  PlanVisibleField,
} from "../../../api/modules/projectPlanViews";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  PlanBulkToolbar,
  PlanFieldLabel,
  PlanLaneFooter,
  PlanSelectionCheckbox,
  PlanTodoActions,
  PlanTodoPatchDialog,
  PlanTodoFieldValue,
  planLaneLabel,
  uniquePlanTodos,
  usePlanRendererFrame,
  usePlanSelection,
} from "./PlanTodoAttributes";
import type { PlanTodoEditRequest } from "./PlanTodoAttributes";
import styles from "./PlanTable.module.less";

export default function PlanTable(props: PlanRendererProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<readonly string[]>([]);
  const [request, setRequest] = useState<
    (PlanTodoEditRequest & { id: number }) | null
  >(null);
  const nextRequest = useRef(0);
  const frame = usePlanRendererFrame(props);
  const selection = usePlanSelection(props);
  useEffect(() => {
    setExpanded([]);
    setRequest(null);
  }, [frame.key]);
  const sortable = (field: PlanVisibleField): field is PlanSortSpec["field"] =>
    field !== "tags" && field !== "source";
  const sort = (field: PlanSortSpec["field"]) => {
    const old = props.definition.sort.find((item) => item.field === field);
    const rest = props.definition.sort.filter((item) => item.field !== field);
    props.onTemporaryDefinitionChange({
      ...props.definition,
      sort:
        old?.direction === "desc"
          ? rest
          : ([{ field, direction: old ? "desc" : "asc" }, ...rest].slice(
              0,
              3,
            ) as PlanSortSpec[]),
    });
  };
  return (
    <div className={styles.tableView} data-plan-renderer="table">
      <div className={styles.summary}>
        {t("projects.planViews.todoTotal", "{{count}} 条待办", {
          count: props.total,
        })}
      </div>
      <PlanBulkToolbar renderer={props} selection={selection} />
      <div className={styles.scroll}>
        {props.lanes.map((lane) => {
          const grouped = props.definition.group_by !== null;
          const open = !grouped || expanded.includes(lane.laneId);
          const label = planLaneLabel(lane, props, t);
          return (
            <section
              key={lane.laneId}
              className={styles.group}
              aria-label={label}
            >
              {grouped && (
                <button
                  type="button"
                  className={styles.groupHeading}
                  aria-expanded={open}
                  onClick={() => {
                    setExpanded((old) =>
                      open
                        ? old.filter((id) => id !== lane.laneId)
                        : [...old, lane.laneId],
                    );
                    if (
                      !open &&
                      lane.queryFingerprint === null &&
                      !lane.loadingFirst &&
                      !lane.paused &&
                      lane.serverCount > 0
                    )
                      void props.onLoadFirst(lane.laneId);
                  }}
                >
                  <span aria-hidden="true">{open ? "▾" : "▸"}</span>
                  <span>{label}</span> <span>{lane.serverCount}</span>
                </button>
              )}
              {open && (
                <>
                  <table className={styles.table} aria-label={label}>
                    <thead>
                      <tr>
                        {props.isManager && (
                          <th scope="col">
                            {t("projects.planViews.selection", "选择")}
                          </th>
                        )}
                        {props.definition.fields.map((field) => {
                          const spec = props.definition.sort.find(
                            (item) => item.field === field,
                          );
                          return (
                            <th
                              key={field}
                              scope="col"
                              aria-sort={
                                spec
                                  ? spec.direction === "asc"
                                    ? "ascending"
                                    : "descending"
                                  : undefined
                              }
                            >
                              {sortable(field) ? (
                                <button
                                  type="button"
                                  className={styles.sortButton}
                                  aria-label={t(
                                    "projects.planViews.sortField",
                                    "按{{field}}排序",
                                    {
                                      field: t(
                                        `projects.planViews.fields.${field}`,
                                        {
                                          title: "标题",
                                          status: "状态",
                                          assignee: "处理人",
                                          priority: "优先级",
                                          start_date: "开始日期",
                                          due_date: "截止日期",
                                          created_at: "创建时间",
                                          updated_at: "更新时间",
                                        }[field],
                                      ),
                                    },
                                  )}
                                  onClick={() => sort(field)}
                                >
                                  <PlanFieldLabel field={field} />
                                  {spec && (
                                    <span aria-hidden="true">
                                      {spec.direction === "asc" ? " ↑" : " ↓"}
                                    </span>
                                  )}
                                </button>
                              ) : (
                                <PlanFieldLabel field={field} />
                              )}
                            </th>
                          );
                        })}
                        <th scope="col">
                          {t("projects.planViews.actions", "操作")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {uniquePlanTodos(lane.items).map((todo) => (
                        <tr key={todo.todo_id}>
                          {props.isManager && (
                            <td>
                              <PlanSelectionCheckbox
                                todo={todo}
                                renderer={props}
                                selection={selection}
                              />
                            </td>
                          )}
                          {props.definition.fields.map((field) => (
                            <td key={field} data-plan-field={field}>
                              <PlanTodoFieldValue
                                todo={todo}
                                field={field}
                                catalog={props.catalog}
                                members={props.members}
                                serverToday={props.serverToday}
                                serverTimezone={props.serverTimezone}
                                onOpenTodo={props.onOpenTodo}
                              />
                            </td>
                          ))}
                          <td>
                            <PlanTodoActions
                              todo={todo}
                              renderer={props}
                              onQuickStatus={(item, trigger) =>
                                setRequest({
                                  id: ++nextRequest.current,
                                  todo: { ...item, tag_ids: [...item.tag_ids] },
                                  renderer: props,
                                  mode: "status",
                                  trigger,
                                })
                              }
                            />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <PlanLaneFooter lane={lane} renderer={props} />
                </>
              )}
            </section>
          );
        })}
      </div>
      {request && (
        <PlanTodoPatchDialog
          key={request.id}
          request={request}
          renderer={props}
          onClose={() => setRequest(null)}
        />
      )}
    </div>
  );
}
