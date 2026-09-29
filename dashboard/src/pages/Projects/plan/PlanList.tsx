import type { PlanRendererProps } from "./ProjectPlanViews";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import PlanTodoAttributes, {
  PlanBulkToolbar,
  PlanLaneFooter,
  PlanSelectionCheckbox,
  PlanTodoActions,
  PlanTodoPatchDialog,
  planLaneLabel,
  uniquePlanTodos,
  usePlanRendererFrame,
  usePlanSelection,
} from "./PlanTodoAttributes";
import type { PlanTodoEditRequest } from "./PlanTodoAttributes";
import styles from "./PlanList.module.less";

export default function PlanList(props: PlanRendererProps) {
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
  return (
    <div className={styles.list} data-plan-renderer="list">
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
              aria-label={label}
              className={styles.group}
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
                  <span>{label}</span>{" "}
                  <span className={styles.count}>{lane.serverCount}</span>
                </button>
              )}
              {open && (
                <>
                  <ol className={styles.rows}>
                    {uniquePlanTodos(lane.items).map((todo) => (
                      <li key={todo.todo_id} className={styles.row}>
                        <PlanSelectionCheckbox
                          todo={todo}
                          renderer={props}
                          selection={selection}
                        />
                        <div className={styles.attributes}>
                          <PlanTodoAttributes
                            todo={todo}
                            fields={props.definition.fields}
                            catalog={props.catalog}
                            members={props.members}
                            serverToday={props.serverToday}
                            serverTimezone={props.serverTimezone}
                            onOpenTodo={props.onOpenTodo}
                          />
                        </div>
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
                      </li>
                    ))}
                  </ol>
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
