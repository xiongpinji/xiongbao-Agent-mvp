import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import {
  projectTodoCatalogApi,
  type ProjectTodoCatalog,
} from "../../api/modules/projectTodoCatalog";
import { isNotFoundApiError } from "../../utils/apiError";
const snapshotKey = (
  key: string,
  revision: number | undefined,
  revisions: number[],
) =>
  JSON.stringify([
    key,
    revision,
    [...new Set(revisions)].sort((a, b) => a - b),
  ]);

export function useTodoCatalog(
  projectId: string,
  accountId: number | null,
  onAccessLost: () => void,
  requiredRevisions: number[] = [],
) {
  const key = JSON.stringify([accountId, projectId]);
  const currentKey = useRef(key);
  currentKey.current = key;
  const required = useRef(requiredRevisions);
  required.current = requiredRevisions;
  const active = useRef(true);
  const seq = useRef(0);
  const onLost = useRef(onAccessLost);
  onLost.current = onAccessLost;
  const [state, setState] = useState<{
    key: string;
    data: ProjectTodoCatalog | null;
    loading: boolean;
    error: unknown;
  }>({ key, data: null, loading: true, error: null });
  const [confirmedMismatch, setConfirmedMismatch] = useState<string | null>(
    null,
  );
  const pendingMismatch = useRef<string | null>(null);
  const clear = useCallback(() => {
    seq.current += 1;
    setState({
      key: currentKey.current,
      data: null,
      loading: false,
      error: null,
    });
  }, []);
  const reload = useCallback(async () => {
    if (!active.current || currentKey.current !== key) return false;
    const requestKey = key,
      requestSeq = ++seq.current;
    const minimumRevision = Math.max(0, ...required.current);
    setState((previous) => ({
      key,
      data: previous.key === key ? previous.data : null,
      loading: true,
      error: null,
    }));
    try {
      const data = await projectTodoCatalogApi.get(projectId);
      if (
        !active.current ||
        currentKey.current !== requestKey ||
        seq.current !== requestSeq
      )
        return false;
      if (data.project_id !== projectId)
        throw new Error("Invalid catalog project");
      if (data.revision < minimumRevision)
        throw new Error("Catalog snapshot is older than the todo snapshot");
      if (required.current.every((revision) => revision <= data.revision))
        setConfirmedMismatch(snapshotKey(key, data.revision, required.current));
      setState({ key, data, loading: false, error: null });
      return true;
    } catch (error: unknown) {
      if (
        !active.current ||
        currentKey.current !== requestKey ||
        seq.current !== requestSeq
      )
        return false;
      setState((previous) => ({
        key,
        data: isNotFoundApiError(error)
          ? null
          : previous.key === key
          ? previous.data
          : null,
        loading: false,
        error,
      }));
      if (isNotFoundApiError(error)) onLost.current();
      return false;
    }
  }, [key, projectId]);
  useLayoutEffect(() => {
    active.current = true;
    void reload();
    return () => {
      active.current = false;
      seq.current += 1;
    };
  }, [reload]);
  const mismatch =
    state.key === key &&
    state.data !== null &&
    requiredRevisions.some((revision) => revision !== state.data?.revision);
  const mismatchKey = snapshotKey(key, state.data?.revision, requiredRevisions);
  useEffect(() => {
    if (
      !mismatch ||
      state.loading ||
      state.error ||
      confirmedMismatch === mismatchKey ||
      pendingMismatch.current === mismatchKey
    )
      return;
    pendingMismatch.current = mismatchKey;
    void reload().then(() => {
      if (pendingMismatch.current === mismatchKey)
        pendingMismatch.current = null;
    });
  }, [
    mismatch,
    mismatchKey,
    state.loading,
    state.error,
    confirmedMismatch,
    reload,
    key,
  ]);
  const waitingForSnapshot = mismatch && confirmedMismatch !== mismatchKey;
  return {
    catalog: state.key === key && !waitingForSnapshot ? state.data : null,
    loading:
      state.key !== key ||
      state.loading ||
      (waitingForSnapshot && !state.error),
    error: state.key === key ? state.error : null,
    reload,
    clear,
  };
}
