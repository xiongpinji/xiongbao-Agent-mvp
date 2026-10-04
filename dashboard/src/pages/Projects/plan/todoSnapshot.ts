import type { ProjectTodo } from "../../../api/modules/projectTodos";
import { isPlanDate } from "../planDates";

export type TodoSnapshotOrder = "accept" | "keep" | "incomparable" | "invalid";
const positiveInteger = (value: unknown) =>
  typeof value === "number" && Number.isSafeInteger(value) && value > 0;

export function validTodoSnapshot(
  value: unknown,
  projectId?: string,
  todoId?: string,
): value is ProjectTodo {
  if (!value || typeof value !== "object") return false;
  const todo = value as ProjectTodo;
  return (
    typeof todo.project_id === "string" &&
    !!todo.project_id &&
    typeof todo.todo_id === "string" &&
    !!todo.todo_id &&
    (projectId === undefined || todo.project_id === projectId) &&
    (todoId === undefined || todo.todo_id === todoId) &&
    positiveInteger(todo.display_revision) &&
    positiveInteger(todo.catalog_revision) &&
    positiveInteger(todo.version) &&
    positiveInteger(todo.creator_user_id) &&
    (todo.assignee_user_id === null ||
      positiveInteger(todo.assignee_user_id)) &&
    typeof todo.title === "string" &&
    typeof todo.description === "string" &&
    ["plain", "markdown"].includes(todo.description_format) &&
    ["todo", "in_progress", "done"].includes(todo.status) &&
    (todo.start_date === null || isPlanDate(todo.start_date)) &&
    (todo.due_date === null || isPlanDate(todo.due_date)) &&
    (todo.priority_id === null || typeof todo.priority_id === "string") &&
    Array.isArray(todo.tag_ids) &&
    todo.tag_ids.every((id) => typeof id === "string") &&
    typeof todo.created_at === "number" &&
    Number.isFinite(todo.created_at) &&
    typeof todo.updated_at === "number" &&
    Number.isFinite(todo.updated_at)
  );
}

export function compareTodoSnapshot(
  current: ProjectTodo | null | undefined,
  incoming: ProjectTodo,
): TodoSnapshotOrder {
  if (!validTodoSnapshot(incoming) || (current && !validTodoSnapshot(current)))
    return "invalid";
  if (!current) return "accept";
  if (
    current.project_id !== incoming.project_id ||
    current.todo_id !== incoming.todo_id
  )
    return "invalid";
  if (
    incoming.display_revision >= current.display_revision &&
    incoming.catalog_revision >= current.catalog_revision
  )
    return "accept";
  if (
    incoming.display_revision <= current.display_revision &&
    incoming.catalog_revision <= current.catalog_revision
  )
    return "keep";
  return "incomparable";
}

export function uniqueTodoSnapshots(
  items: readonly ProjectTodo[],
): ProjectTodo[] {
  const unique = new Map<string, ProjectTodo>();
  for (const todo of items) {
    const id = JSON.stringify([todo.project_id, todo.todo_id]);
    if (compareTodoSnapshot(unique.get(id), todo) === "accept")
      unique.set(id, todo);
  }
  return [...unique.values()];
}
