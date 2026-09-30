import type { PlanRendererProps } from "./ProjectPlanViews";
import type { ProjectTodo } from "../../../api/modules/projectTodos";
import type { CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { addPlanDays, isPlanDate } from "../planDates";
import {
  clipPlanWindow,
  planGanttInterval,
  planWeekGrid,
} from "./planViewDates";
import PlanTodoAttributes, {
  PlanLaneFooter,
  PlanTodoActions,
  PlanTodoPatchDialog,
  uniquePlanTodos,
  usePlanRendererFrame,
} from "./PlanTodoAttributes";
import type { PlanTodoEditRequest } from "./PlanTodoAttributes";
import styles from "./PlanGantt.module.less";

export default function PlanGantt(props: PlanRendererProps) {
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
    kind: "shift" | "start" | "end";
    anchor: string;
  } | null>(null);
  const window =
    props.window &&
    clipPlanWindow(props.window.start_date, props.window.end_date);
  const days: string[] = [];
  if (window)
    for (
      let day: string | null = window.start_date;
      day && day <= window.end_date && days.length < 366;
      day = addPlanDays(day, 1)
    )
      days.push(day);
  const zoom = props.definition.gantt?.zoom ?? "week";
  const dayWidth = zoom === "day" ? 42 : zoom === "week" ? 22 : 9;
  const metadataReady = isPlanDate(props.serverToday) && !!props.serverTimezone;
  const scheduled = props.lanes.find((lane) => lane.bucket === "scheduled");
  const unscheduled = props.lanes.find((lane) => lane.bucket === "unscheduled");
  useEffect(() => {
    setRequest(null);
    drag.current = null;
  }, [frame.key]);
  useEffect(() => {
    if (!props.window && isPlanDate(props.serverToday)) {
      const start = planWeekGrid(props.serverToday)[0] ?? props.serverToday;
      const initial = clipPlanWindow(start, addPlanDays(start, 27));
      if (initial) props.onWindowChange(initial);
    }
  }, [props]);
  const shiftedWindow = (amount: number) =>
    window
      ? clipPlanWindow(
          addPlanDays(window.start_date, amount),
          addPlanDays(window.end_date, amount),
        )
      : null;
  const previous = shiftedWindow(-days.length);
  const next = shiftedWindow(days.length);
  const openDates = (todo: ProjectTodo, trigger: HTMLElement | null) =>
    setRequest({
      id: ++nextRequest.current,
      todo: { ...todo, tag_ids: [...todo.tag_ids] },
      renderer: props,
      mode: "gantt",
      trigger,
    });
  const startDrag = (
    todo: ProjectTodo,
    kind: "shift" | "start" | "end",
    event: React.DragEvent<HTMLElement>,
  ) => {
    event.stopPropagation();
    const interval = planGanttInterval(todo);
    if (!metadataReady || !props.canEdit(todo) || !interval || !window) {
      event.preventDefault();
      return;
    }
    const visibleStart =
      interval.start_date < window.start_date
        ? window.start_date
        : interval.start_date;
    const track =
      event.currentTarget.closest<HTMLElement>("[data-gantt-track]");
    const rect = track?.getBoundingClientRect();
    const pointerDay =
      rect && rect.width > 0
        ? days[
            Math.max(
              0,
              Math.min(
                days.length - 1,
                Math.floor(
                  ((event.clientX - rect.left) / rect.width) * days.length,
                ),
              ),
            )
          ]
        : visibleStart;
    drag.current = {
      todo: { ...todo, tag_ids: [...todo.tag_ids] },
      renderer: props,
      generation: frame.generation,
      kind,
      anchor: pointerDay,
    };
    event.dataTransfer.setData("application/x-project-plan-todo", todo.todo_id);
    event.dataTransfer.effectAllowed = "move";
  };
  const drop = (day: string) => {
    const original = drag.current;
    drag.current = null;
    if (
      !original ||
      !frame.isCurrent(original.generation) ||
      !metadataReady ||
      !props.canEdit(original.todo)
    )
      return;
    const delta = days.indexOf(day) - days.indexOf(original.anchor);
    if (original.kind === "shift" && delta === 0) return;
    setRequest({
      id: ++nextRequest.current,
      todo: original.todo,
      renderer: original.renderer,
      mode: "gantt",
      trigger: null,
      autoSubmit: true,
      dateOperation:
        original.kind === "shift"
          ? { kind: "shift", delta }
          : {
              kind: "resize",
              edge: original.kind === "start" ? "start" : "end",
              date: day,
            },
    });
  };
  return (
    <div className={styles.gantt} data-plan-renderer="gantt">
      <div className={styles.toolbar}>
        <span>
          {t("projects.planViews.todoTotal", "{{count}} 条待办", {
            count: props.total,
          })}
        </span>
        <button
          type="button"
          aria-label={t("projects.planViews.previousWindow", "上一个时间窗口")}
          disabled={!previous}
          onClick={() => previous && props.onWindowChange(previous)}
        >
          ‹
        </button>
        <span className={styles.window}>
          {window?.start_date} – {window?.end_date}
        </span>
        <button
          type="button"
          aria-label={t("projects.planViews.nextWindow", "下一个时间窗口")}
          disabled={!next}
          onClick={() => next && props.onWindowChange(next)}
        >
          ›
        </button>
        <button
          type="button"
          disabled={!metadataReady}
          onClick={() => {
            if (!isPlanDate(props.serverToday)) return;
            const current = clipPlanWindow(
              props.serverToday,
              addPlanDays(props.serverToday, Math.max(1, days.length) - 1),
            );
            if (current) props.onWindowChange(current);
          }}
        >
          {t("projects.planViews.today", "今天")}
        </button>
        <label>
          {t("projects.planViews.zoom", "缩放")}
          <select
            aria-label={t("projects.planViews.zoom", "缩放")}
            value={zoom}
            onChange={(event) =>
              props.onTemporaryDefinitionChange({
                schema_version: 1,
                fields: props.definition.fields,
                filters: props.definition.filters,
                sort: props.definition.sort,
                group_by: null,
                gantt: { zoom: event.target.value as "day" | "week" | "month" },
              })
            }
          >
            {(["day", "week", "month"] as const).map((value) => (
              <option key={value} value={value}>
                {t(
                  `projects.planViews.${value}`,
                  { day: "日", week: "周", month: "月" }[value],
                )}
              </option>
            ))}
          </select>
        </label>
      </div>
      <p className={styles.hint}>
        {t(
          "projects.planViews.ganttShiftHint",
          "拖动条形整体平移，拖动边缘调整日期；也可使用修改日期。",
        )}
      </p>
      <div className={styles.body}>
        <div
          className={styles.timelineScroll}
          aria-label={t("projects.planViews.gantt", "甘特时间轴")}
        >
          <div
            className={styles.timeline}
            style={
              {
                "--plan-day-width": `${dayWidth}px`,
                "--plan-day-count": days.length,
                minWidth: `calc(var(--plan-gantt-label-width) + ${
                  days.length * dayWidth
                }px)`,
              } as CSSProperties
            }
          >
            <div className={styles.headerRow}>
              <div className={styles.rowLabel}>
                {t("projects.planViews.allTodos", "全部待办")} ·{" "}
                {props.matchedTotal}
              </div>
              <div
                className={styles.scale}
                style={{
                  gridTemplateColumns: `repeat(${days.length}, minmax(${dayWidth}px, 1fr))`,
                }}
              >
                {days.map((day, index) => (
                  <div
                    role="columnheader"
                    aria-label={day}
                    key={day}
                    className={
                      day === props.serverToday ? styles.todayTick : styles.tick
                    }
                    onDragOver={(event) => {
                      if (drag.current && metadataReady) event.preventDefault();
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      drop(day);
                    }}
                  >
                    {zoom === "day"
                      ? Number(day.slice(8))
                      : zoom === "week"
                      ? index % 7 === 0
                        ? day.slice(5)
                        : ""
                      : day.slice(8) === "01" || index === 0
                      ? day.slice(0, 7)
                      : ""}
                  </div>
                ))}
              </div>
            </div>
            {scheduled &&
              uniquePlanTodos(scheduled.items).map((todo) => {
                const interval = planGanttInterval(todo);
                if (!interval || !window) return null;
                const start = days.indexOf(
                  interval.start_date < window.start_date
                    ? window.start_date
                    : interval.start_date,
                );
                const end = days.indexOf(
                  interval.end_date > window.end_date
                    ? window.end_date
                    : interval.end_date,
                );
                if (start < 0 || end < start) return null;
                const writable = metadataReady && props.canEdit(todo);
                return (
                  <div key={todo.todo_id} className={styles.ganttRow}>
                    <div className={styles.rowLabel}>
                      <PlanTodoAttributes
                        todo={todo}
                        fields={props.definition.fields}
                        catalog={props.catalog}
                        members={props.members}
                        serverToday={props.serverToday}
                        serverTimezone={props.serverTimezone}
                        onOpenTodo={props.onOpenTodo}
                      />
                      <button
                        type="button"
                        disabled={!writable}
                        onClick={(event) =>
                          openDates(todo, event.currentTarget)
                        }
                      >
                        {t("projects.planViews.changeDates", "修改日期")}
                      </button>
                      <PlanTodoActions todo={todo} renderer={props} />
                    </div>
                    <div
                      data-gantt-track
                      className={styles.track}
                      onDragOver={(event) => {
                        if (drag.current && metadataReady)
                          event.preventDefault();
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        const rect =
                          event.currentTarget.getBoundingClientRect();
                        if (rect.width > 0)
                          drop(
                            days[
                              Math.max(
                                0,
                                Math.min(
                                  days.length - 1,
                                  Math.floor(
                                    ((event.clientX - rect.left) / rect.width) *
                                      days.length,
                                  ),
                                ),
                              )
                            ],
                          );
                      }}
                    >
                      <div
                        className={
                          interval.start_date === interval.end_date
                            ? styles.singleDay
                            : styles.bar
                        }
                        data-testid={`gantt-bar-${todo.todo_id}`}
                        data-start-date={interval.start_date}
                        data-end-date={interval.end_date}
                        data-span-days={end - start + 1}
                        data-single-day={
                          interval.start_date === interval.end_date
                        }
                        draggable={writable}
                        onDragStart={(event) => startDrag(todo, "shift", event)}
                        onDragEnd={() => {
                          drag.current = null;
                        }}
                        style={{
                          left: `${(start / days.length) * 100}%`,
                          width: `${((end - start + 1) / days.length) * 100}%`,
                        }}
                      >
                        <button
                          type="button"
                          className={styles.edge}
                          aria-label={t(
                            "projects.planViews.resizeStart",
                            "调整开始日期",
                          )}
                          draggable={writable}
                          disabled={!writable}
                          onDragStart={(event) =>
                            startDrag(todo, "start", event)
                          }
                          onClick={(event) =>
                            setRequest({
                              id: ++nextRequest.current,
                              todo,
                              renderer: props,
                              mode: "gantt",
                              trigger: event.currentTarget,
                              dateOperation: {
                                kind: "resize",
                                edge: "start",
                                date: todo.start_date ?? todo.due_date,
                              },
                            })
                          }
                        >
                          ⋮
                        </button>
                        <span
                          className={styles.barTitle}
                          onDoubleClick={(event) =>
                            props.onOpenTodo(todo, event.currentTarget)
                          }
                        >
                          {todo.title}
                        </span>
                        <button
                          type="button"
                          className={styles.edge}
                          aria-label={t(
                            "projects.planViews.resizeEnd",
                            "调整截止日期",
                          )}
                          draggable={writable}
                          disabled={!writable}
                          onDragStart={(event) => startDrag(todo, "end", event)}
                          onClick={(event) =>
                            setRequest({
                              id: ++nextRequest.current,
                              todo,
                              renderer: props,
                              mode: "gantt",
                              trigger: event.currentTarget,
                              dateOperation: {
                                kind: "resize",
                                edge: "end",
                                date: todo.due_date ?? todo.start_date,
                              },
                            })
                          }
                        >
                          ⋮
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
          </div>
          {scheduled && <PlanLaneFooter lane={scheduled} renderer={props} />}
        </div>
        <aside
          className={styles.unscheduled}
          aria-label={t("projects.planViews.unscheduled", "未排期")}
        >
          <h3>
            {t("projects.planViews.unscheduled", "未排期")}{" "}
            <span>
              {props.unscheduledTotal ?? unscheduled?.serverCount ?? 0}
            </span>
          </h3>
          <div className={styles.sideScroll}>
            {unscheduled &&
              uniquePlanTodos(unscheduled.items).map((todo) => (
                <article key={todo.todo_id} className={styles.unscheduledCard}>
                  <PlanTodoAttributes
                    todo={todo}
                    fields={props.definition.fields}
                    catalog={props.catalog}
                    members={props.members}
                    serverToday={props.serverToday}
                    serverTimezone={props.serverTimezone}
                    onOpenTodo={props.onOpenTodo}
                  />
                  <button
                    type="button"
                    disabled={!metadataReady || !props.canEdit(todo)}
                    onClick={(event) => openDates(todo, event.currentTarget)}
                  >
                    {t("projects.planViews.changeDates", "修改日期")}
                  </button>
                  <PlanTodoActions todo={todo} renderer={props} />
                </article>
              ))}
          </div>
          {unscheduled && (
            <PlanLaneFooter lane={unscheduled} renderer={props} />
          )}
        </aside>
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
