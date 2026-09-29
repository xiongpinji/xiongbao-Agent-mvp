import type { PlanRendererProps } from "./ProjectPlanViews";
import type { PlanLaneState } from "./ProjectPlanViews";
import type { ProjectTodo } from "../../../api/modules/projectTodos";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import PlanTodoAttributes, {
  PlanLaneFooter,
  PlanTodoActions,
  PlanTodoPatchDialog,
  planLaneLabel,
  uniquePlanTodos,
  usePlanRendererFrame,
} from "./PlanTodoAttributes";
import type { PlanTodoEditRequest } from "./PlanTodoAttributes";
import styles from "./PlanBoard.module.less";

export default function PlanBoard(props: PlanRendererProps) {
  const { t } = useTranslation();
  const frame = usePlanRendererFrame(props);
  const [request, setRequest] = useState<
    (PlanTodoEditRequest & { id: number }) | null
  >(null);
  const nextRequest = useRef(0);
  const drag = useRef<{
    todo: ProjectTodo;
    renderer: PlanRendererProps;
    generation: number;
  } | null>(null);
  const columns = useRef(new Map<string, HTMLElement>());
  const scroll = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    setRequest(null);
    drag.current = null;
  }, [frame.key]);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const lane = props.lanes.find(
            (item) =>
              item.laneId === (entry.target as HTMLElement).dataset.laneId,
          );
          if (
            lane &&
            lane.serverCount > 0 &&
            lane.queryFingerprint === null &&
            !lane.loadingFirst &&
            !lane.paused
          )
            void props.onLoadFirst(lane.laneId);
        }
      },
      { root: scroll.current, threshold: 0.05 },
    );
    columns.current.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [props]);
  const group = props.definition.group_by;
  const dragEnabled = (todo: ProjectTodo) =>
    props.canEdit(todo) &&
    (group === "status" ||
      (group === "priority" && !!props.catalog) ||
      (group === "assignee" && props.isManager));
  const canLand = (lane: PlanLaneState) => {
    const key = lane.groupKey;
    if (!key || key.kind === "tag" || key.kind === "source") return false;
    if (key.kind === "assignee")
      return (
        props.isManager &&
        (key.id === null ||
          props.members.some((member) => String(member.user_id) === key.id))
      );
    if (key.kind === "priority")
      return (
        !!props.catalog &&
        (key.id === null ||
          props.catalog.priorities.some(
            (item) => item.priority_id === key.id && item.archived_at === null,
          ))
      );
    return (
      key.kind === "status" &&
      ["todo", "in_progress", "done"].includes(key.id ?? "")
    );
  };
  const open = (
    todo: ProjectTodo,
    mode: PlanTodoEditRequest["mode"],
    trigger: HTMLElement | null,
  ) =>
    setRequest({
      id: ++nextRequest.current,
      todo: { ...todo, tag_ids: [...todo.tag_ids] },
      renderer: props,
      mode,
      trigger,
    });
  const drop = (lane: PlanLaneState) => {
    const original = drag.current;
    drag.current = null;
    if (
      !original ||
      !frame.isCurrent(original.generation) ||
      !dragEnabled(original.todo) ||
      !canLand(lane) ||
      !lane.groupKey
    )
      return;
    const key = lane.groupKey;
    const changes =
      key.kind === "status"
        ? { status: key.id as ProjectTodo["status"] }
        : key.kind === "assignee"
        ? { assignee_user_id: key.id === null ? null : Number(key.id) }
        : { priority_id: key.id };
    const changed =
      key.kind === "status"
        ? changes.status !== original.todo.status
        : key.kind === "assignee"
        ? changes.assignee_user_id !== original.todo.assignee_user_id
        : changes.priority_id !== original.todo.priority_id;
    if (!changed) return;
    setRequest({
      id: ++nextRequest.current,
      todo: original.todo,
      renderer: original.renderer,
      mode:
        key.kind === "status"
          ? "status"
          : key.kind === "assignee"
          ? "assignee"
          : "priority",
      trigger: null,
      changes,
      autoSubmit: true,
    });
  };
  const explanation =
    group === "tag"
      ? ["dragDisabledTag", "多标签分组请通过字段编辑修改标签。"]
      : group === "source"
      ? ["dragDisabledSource", "来源分组不支持拖动修改。"]
      : group === "assignee" && !props.isManager
      ? ["dragManagerAssignee", "只有项目管理者可拖动修改处理人。"]
      : null;
  return (
    <div className={styles.board} data-plan-renderer="board">
      <div className={styles.summary}>
        {t("projects.planViews.todoTotal", "{{count}} 条待办", {
          count: props.total,
        })}
        {explanation && (
          <span role="note">
            {t(`projects.planViews.${explanation[0]}`, explanation[1])}
          </span>
        )}
      </div>
      <div className={styles.columns} ref={scroll}>
        {props.lanes.map((lane) => {
          const label = planLaneLabel(lane, props, t);
          const landing = canLand(lane);
          return (
            <section
              key={lane.laneId}
              aria-label={label}
              className={styles.column}
              data-lane-id={lane.laneId}
              data-drop-enabled={landing}
              ref={(node) => {
                if (node) columns.current.set(lane.laneId, node);
                else columns.current.delete(lane.laneId);
              }}
              onDragOver={(event) => {
                if (drag.current && landing) {
                  event.preventDefault();
                  event.dataTransfer.dropEffect = "move";
                }
              }}
              onDrop={(event) => {
                event.preventDefault();
                drop(lane);
              }}
            >
              <div className={styles.columnHeading}>
                <h3>{label}</h3>
                <span>{lane.serverCount}</span>
              </div>
              {lane.groupKey?.kind === "priority" && !landing && (
                <p className={styles.note}>
                  {t(
                    "projects.planViews.archivedDropDisabled",
                    "停用项不能作为新的落点。",
                  )}
                </p>
              )}
              <div className={styles.cards}>
                {uniquePlanTodos(lane.items).map((todo) => (
                  <article
                    aria-label={todo.title}
                    key={todo.todo_id}
                    className={styles.card}
                    draggable={dragEnabled(todo)}
                    onDragStart={(event) => {
                      if (!dragEnabled(todo)) {
                        event.preventDefault();
                        return;
                      }
                      drag.current = {
                        todo: { ...todo, tag_ids: [...todo.tag_ids] },
                        renderer: props,
                        generation: frame.generation,
                      };
                      event.dataTransfer.setData(
                        "application/x-project-plan-todo",
                        todo.todo_id,
                      );
                      event.dataTransfer.effectAllowed = "move";
                    }}
                    onDragEnd={() => {
                      drag.current = null;
                    }}
                  >
                    <PlanTodoAttributes
                      todo={todo}
                      fields={props.definition.fields}
                      catalog={props.catalog}
                      members={props.members}
                      serverToday={props.serverToday}
                      serverTimezone={props.serverTimezone}
                      onOpenTodo={props.onOpenTodo}
                    />
                    <div className={styles.cardActions}>
                      {props.canEdit(todo) && (
                        <>
                          <button
                            type="button"
                            onClick={(event) =>
                              open(todo, "status", event.currentTarget)
                            }
                          >
                            {t("projects.planViews.changeStatus", "修改状态")}
                          </button>
                          <button
                            type="button"
                            disabled={!props.catalog}
                            onClick={(event) =>
                              open(todo, "priority", event.currentTarget)
                            }
                          >
                            {t(
                              "projects.planViews.changePriority",
                              "修改优先级",
                            )}
                          </button>
                          {props.isManager && (
                            <button
                              type="button"
                              onClick={(event) =>
                                open(todo, "assignee", event.currentTarget)
                              }
                            >
                              {t(
                                "projects.planViews.changeAssignee",
                                "修改处理人",
                              )}
                            </button>
                          )}
                        </>
                      )}
                      <PlanTodoActions todo={todo} renderer={props} />
                    </div>
                  </article>
                ))}
              </div>
              <PlanLaneFooter lane={lane} renderer={props} />
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
