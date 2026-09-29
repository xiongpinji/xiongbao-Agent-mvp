import type { PlanRendererProps } from "./ProjectPlanViews";
import type { ProjectTodo } from "../../../api/modules/projectTodos";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  addPlanDays,
  isPlanDate,
  planMonthGrid,
  shiftPlanMonth,
} from "../planDates";
import { clipPlanWindow, planWeekGrid } from "./planViewDates";
import PlanTodoAttributes, {
  PlanLaneFooter,
  PlanTodoActions,
  PlanTodoPatchDialog,
  uniquePlanTodos,
  usePlanRendererFrame,
} from "./PlanTodoAttributes";
import type { PlanTodoEditRequest } from "./PlanTodoAttributes";
import styles from "./PlanCalendar.module.less";

export default function PlanCalendar(props: PlanRendererProps) {
  const { t } = useTranslation();
  const frame = usePlanRendererFrame(props);
  const [anchor, setAnchor] = useState(
    () => props.serverToday ?? props.window?.start_date ?? "",
  );
  const [request, setRequest] = useState<
    (PlanTodoEditRequest & { id: number }) | null
  >(null);
  const nextRequest = useRef(0);
  const drag = useRef<{
    todo: ProjectTodo;
    renderer: PlanRendererProps;
    generation: number;
  } | null>(null);
  const calendar = props.definition.calendar ?? {
    date_basis: "due_date" as const,
    mode: "month" as const,
  };
  const days = useMemo(
    () =>
      calendar.mode === "month"
        ? planMonthGrid(anchor.slice(0, 7))
        : planWeekGrid(anchor),
    [anchor, calendar.mode],
  );
  const visibleWindow = useMemo(
    () =>
      days.length > 0 ? clipPlanWindow(days[0], days[days.length - 1]) : null,
    [days],
  );
  const metadataReady = isPlanDate(props.serverToday) && !!props.serverTimezone;
  const scheduled = props.lanes.find((lane) => lane.bucket === "scheduled");
  const unscheduled = props.lanes.find((lane) => lane.bucket === "unscheduled");
  const contextKey = JSON.stringify([
    props.accountId,
    props.projectId,
    props.view.view_id,
  ]);
  const lastContext = useRef(contextKey);
  const { onWindowChange, window: queryWindow } = props;
  useEffect(() => {
    if (lastContext.current === contextKey) return;
    lastContext.current = contextKey;
    setAnchor(props.serverToday ?? props.window?.start_date ?? "");
  }, [contextKey, props.serverToday, props.window?.start_date]);
  useEffect(() => {
    setRequest(null);
    drag.current = null;
  }, [frame.key]);
  useEffect(() => {
    if (
      visibleWindow &&
      (queryWindow?.start_date !== visibleWindow.start_date ||
        queryWindow?.end_date !== visibleWindow.end_date)
    )
      onWindowChange(visibleWindow);
  }, [
    visibleWindow,
    queryWindow?.start_date,
    queryWindow?.end_date,
    onWindowChange,
  ]);
  useEffect(() => {
    if (!isPlanDate(anchor) && isPlanDate(props.serverToday))
      setAnchor(props.serverToday);
  }, [anchor, props.serverToday]);
  const adjacent = (direction: number) =>
    calendar.mode === "week"
      ? addPlanDays(anchor, direction * 7)
      : (() => {
          const month = shiftPlanMonth(anchor.slice(0, 7), direction);
          return month ? `${month}-01` : null;
        })();
  const previous = adjacent(-1);
  const next = adjacent(1);
  const openDates = (todo: ProjectTodo, trigger: HTMLElement | null) =>
    setRequest({
      id: ++nextRequest.current,
      todo: { ...todo, tag_ids: [...todo.tag_ids] },
      renderer: props,
      mode: "calendar",
      trigger,
      dateOperation: {
        kind: "calendar",
        basis: calendar.date_basis,
        date: todo[calendar.date_basis],
      },
    });
  const drop = (date: string) => {
    const original = drag.current;
    drag.current = null;
    if (
      !original ||
      !frame.isCurrent(original.generation) ||
      !metadataReady ||
      !props.canEdit(original.todo) ||
      original.todo[calendar.date_basis] === date
    )
      return;
    setRequest({
      id: ++nextRequest.current,
      todo: original.todo,
      renderer: original.renderer,
      mode: "calendar",
      trigger: null,
      dateOperation: { kind: "calendar", basis: calendar.date_basis, date },
      autoSubmit: true,
    });
  };
  const renderTodo = (todo: ProjectTodo, movable: boolean) => (
    <article
      key={todo.todo_id}
      aria-label={todo.title}
      className={styles.todo}
      draggable={movable}
      onDragStart={(event) => {
        if (!movable) {
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
      <button
        type="button"
        disabled={!metadataReady || !props.canEdit(todo)}
        onClick={(event) => openDates(todo, event.currentTarget)}
      >
        {t("projects.planViews.changeDates", "修改日期")}
      </button>
      <PlanTodoActions todo={todo} renderer={props} />
    </article>
  );
  const todos = scheduled ? uniquePlanTodos(scheduled.items) : [];
  return (
    <div className={styles.calendar} data-plan-renderer="calendar">
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
          onClick={() => previous && setAnchor(previous)}
        >
          ‹
        </button>
        <span>
          {calendar.mode === "month"
            ? anchor.slice(0, 7)
            : `${days[0] ?? "1900-01-01"} – ${days[6] ?? "9999-12-31"}`}
        </span>
        <button
          type="button"
          aria-label={t("projects.planViews.nextWindow", "下一个时间窗口")}
          disabled={!next}
          onClick={() => next && setAnchor(next)}
        >
          ›
        </button>
        <button
          type="button"
          disabled={!metadataReady}
          onClick={() => props.serverToday && setAnchor(props.serverToday)}
        >
          {t("projects.planViews.today", "今天")}
        </button>
        <label>
          {t("projects.planViews.calendarMode", "日历模式")}
          <select
            aria-label={t("projects.planViews.calendarMode", "日历模式")}
            value={calendar.mode}
            onChange={(event) =>
              props.onTemporaryDefinitionChange({
                schema_version: 1,
                fields: props.definition.fields,
                filters: props.definition.filters,
                sort: props.definition.sort,
                group_by: null,
                calendar: {
                  ...calendar,
                  mode: event.target.value as "month" | "week",
                },
              })
            }
          >
            <option value="month">{t("projects.planViews.month", "月")}</option>
            <option value="week">{t("projects.planViews.week", "周")}</option>
          </select>
        </label>
        <label>
          {t("projects.planViews.dateBasis", "日期依据")}
          <select
            aria-label={t("projects.planViews.dateBasis", "日期依据")}
            value={calendar.date_basis}
            onChange={(event) =>
              props.onTemporaryDefinitionChange({
                schema_version: 1,
                fields: props.definition.fields,
                filters: props.definition.filters,
                sort: props.definition.sort,
                group_by: null,
                calendar: {
                  ...calendar,
                  date_basis: event.target.value as "start_date" | "due_date",
                },
              })
            }
          >
            <option value="due_date">
              {t("projects.planViews.fields.due_date", "截止日期")}
            </option>
            <option value="start_date">
              {t("projects.planViews.fields.start_date", "开始日期")}
            </option>
          </select>
        </label>
      </div>
      <div className={styles.body}>
        <div className={styles.calendarScroll}>
          <div
            role="grid"
            aria-label={t("projects.planViews.calendar", "计划日历")}
            className={styles.grid}
            onKeyDown={(event) => {
              const moves: Record<string, number> = {
                ArrowLeft: -1,
                ArrowRight: 1,
                ArrowUp: -7,
                ArrowDown: 7,
              };
              const current = (
                event.target as HTMLElement
              ).closest<HTMLElement>("[data-calendar-day]");
              const day = current?.dataset.calendarDay;
              if (!(event.key in moves) || !day) return;
              const nextDay = addPlanDays(day, moves[event.key]);
              const target =
                nextDay &&
                event.currentTarget.querySelector<HTMLElement>(
                  `[data-calendar-day="${nextDay}"]`,
                );
              if (target) {
                event.preventDefault();
                target.focus();
              }
            }}
          >
            <div role="row" className={styles.weekdays}>
              {(["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const).map(
                (day) => (
                  <div role="columnheader" key={day}>
                    {t(
                      `projects.planViews.weekdays.${day}`,
                      {
                        mon: "周一",
                        tue: "周二",
                        wed: "周三",
                        thu: "周四",
                        fri: "周五",
                        sat: "周六",
                        sun: "周日",
                      }[day],
                    )}
                  </div>
                ),
              )}
            </div>
            {Array.from(
              { length: calendar.mode === "month" ? 6 : 1 },
              (_, row) => (
                <div role="row" key={row} className={styles.weekRow}>
                  {days.slice(row * 7, row * 7 + 7).map((day, index) => (
                    <div
                      role="gridcell"
                      aria-label={day ?? undefined}
                      aria-disabled={!day}
                      key={day ?? `null${row}-${index}`}
                      data-plan-null={day === null ? "true" : undefined}
                      className={
                        day && day.slice(0, 7) === anchor.slice(0, 7)
                          ? styles.day
                          : styles.otherMonth
                      }
                      onDragOver={(event) => {
                        if (day && drag.current && metadataReady)
                          event.preventDefault();
                      }}
                      onDrop={(event) => {
                        event.preventDefault();
                        if (day) drop(day);
                      }}
                    >
                      {day && (
                        <>
                          <button
                            type="button"
                            data-calendar-day={day}
                            className={
                              day === props.serverToday
                                ? styles.today
                                : styles.dayNumber
                            }
                            aria-label={day}
                            onClick={(event) => event.currentTarget.focus()}
                          >
                            {Number(day.slice(8))}
                          </button>
                          <div className={styles.dayTodos}>
                            {todos
                              .filter(
                                (todo) => todo[calendar.date_basis] === day,
                              )
                              .map((todo) =>
                                renderTodo(
                                  todo,
                                  metadataReady && props.canEdit(todo),
                                ),
                              )}
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                </div>
              ),
            )}
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
              uniquePlanTodos(unscheduled.items).map((todo) =>
                renderTodo(todo, metadataReady && props.canEdit(todo)),
              )}
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
