import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Modal, Spin, Tag, Typography } from "antd";
import { KeyRound, Pencil, Plug, RefreshCw, Trash2 } from "lucide-react";
import {
  projectPublicConnectorsApi,
  type ProjectPublicConnector,
  type ProjectPublicConnectorsResponse,
} from "../../api/modules/projectPublicConnectors";
import { projectsApi, type ProjectRecord } from "../../api/modules/projects";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import { formatServerDateTime } from "../../utils/formatMessageTime";

const { Text } = Typography;

type ModalMode = "create" | "rename" | "credentials" | "revoke";

interface ModalState {
  mode: ModalMode;
  connector: ProjectPublicConnector | null;
}

interface DraftState {
  displayName: string;
  description: string;
  endpoint: string;
  bearerToken: string;
}

interface Props {
  projectId: string;
}

const blankDraft = (): DraftState => ({
  displayName: "",
  description: "",
  endpoint: "",
  bearerToken: "",
});

const safeTextDraft = (connector: ProjectPublicConnector): DraftState => ({
  displayName: connector.display_name,
  description: connector.description,
  endpoint: "",
  bearerToken: "",
});

function canManage(project: ProjectRecord | null): boolean {
  return project?.my_role === "owner" || project?.my_role === "admin";
}

function isChangedError(error: unknown): boolean {
  return parseApiError(error)?.code === "PROJECT_PUBLIC_CONNECTORS_CHANGED";
}

function isAccessLoss(error: unknown): boolean {
  const code = parseApiError(error)?.code;
  if (code === "NOT_FOUND" || code === "FORBIDDEN" || code === "UNAUTHORIZED")
    return true;
  const message = error instanceof Error ? error.message : String(error ?? "");
  return /\b(401|403|404|Unauthorized)\b/i.test(message);
}

export default function ProjectPublicConnectors({ projectId }: Props) {
  const user = useCurrentUser();
  if (!user) return null;
  const ownerKey = JSON.stringify([user?.id ?? null, projectId]);
  return (
    <ConnectorPanel key={ownerKey} projectId={projectId} ownerKey={ownerKey} />
  );
}

function ConnectorPanel({ projectId, ownerKey }: Props & { ownerKey: string }) {
  const { t } = useTranslation();
  const timezone = useServerTimezone();

  const safeErrorMessage = (failure: unknown, fallback: string): string => {
    const code = parseApiError(failure)?.code;
    if (!code) return fallback;
    const translated = t(`apiErrors.${code}`, { defaultValue: "" });
    if (!translated || translated === `apiErrors.${code}`) return fallback;
    return apiErrorMessage(
      new Error(JSON.stringify({ error: { code, message: translated } })),
      fallback,
      t,
    );
  };

  const [detail, setDetail] = useState<ProjectRecord | null>(null);
  const [collection, setCollection] =
    useState<ProjectPublicConnectorsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [modal, setModal] = useState<ModalState | null>(null);
  const [draft, setDraft] = useState<DraftState>(() => blankDraft());
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [conflict, setConflict] = useState(false);

  const loadSeq = useRef(0);
  const writeSeq = useRef(0);
  const modalSeq = useRef(0);
  const ownerKeyRef = useRef(ownerKey);
  ownerKeyRef.current = ownerKey;

  const clearSecrets = useCallback(() => {
    setDraft((current) => ({ ...current, endpoint: "", bearerToken: "" }));
  }, []);

  const closeModal = useCallback(() => {
    modalSeq.current += 1;
    writeSeq.current += 1;
    setDraft(blankDraft());
    setModal(null);
    setSubmitting(false);
    setSubmitError(null);
    setConflict(false);
  }, []);

  const applyCollection = useCallback(
    (response: ProjectPublicConnectorsResponse) => {
      setCollection(response);
      setError(null);
    },
    [],
  );

  const load = useCallback(async () => {
    const key = ownerKeyRef.current;
    const seq = ++loadSeq.current;
    setLoading(true);
    setError(null);
    try {
      const [project, connectors] = await Promise.all([
        projectsApi.get(projectId),
        projectPublicConnectorsApi.list(projectId),
      ]);
      if (key !== ownerKeyRef.current || seq !== loadSeq.current) return;
      setDetail(project);
      applyCollection(connectors);
    } catch (err) {
      if (key !== ownerKeyRef.current || seq !== loadSeq.current) return;
      setDetail(null);
      setCollection(null);
      setError(err);
    } finally {
      if (key === ownerKeyRef.current && seq === loadSeq.current)
        setLoading(false);
    }
  }, [applyCollection, projectId]);

  useEffect(() => {
    writeSeq.current += 1;
    modalSeq.current += 1;
    setModal(null);
    setDraft(blankDraft());
    setSubmitting(false);
    setSubmitError(null);
    setConflict(false);
    setDetail(null);
    setCollection(null);
    void load();
    return () => {
      loadSeq.current += 1;
      writeSeq.current += 1;
      modalSeq.current += 1;
    };
  }, [load, reloadKey, ownerKey]);

  const refresh = useCallback(() => setReloadKey((value) => value + 1), []);

  const refreshAfterOutcome = useCallback(
    async (key: string, seq: number) => {
      try {
        const [project, connectors] = await Promise.all([
          projectsApi.get(projectId),
          projectPublicConnectorsApi.list(projectId),
        ]);
        if (key !== ownerKeyRef.current || seq !== writeSeq.current) return;
        setDetail(project);
        applyCollection(connectors);
      } catch (err) {
        if (key !== ownerKeyRef.current || seq !== writeSeq.current) return;
        setDetail(null);
        setCollection(null);
        setDraft(blankDraft());
        setModal(null);
        modalSeq.current += 1;
        setError(err);
      }
    },
    [applyCollection, projectId],
  );

  const openCreate = () => {
    modalSeq.current += 1;
    setModal({ mode: "create", connector: null });
    setDraft(blankDraft());
    setSubmitError(null);
    setConflict(false);
  };

  const openRename = (connector: ProjectPublicConnector) => {
    modalSeq.current += 1;
    setModal({ mode: "rename", connector });
    setDraft(safeTextDraft(connector));
    setSubmitError(null);
    setConflict(false);
  };

  const openCredentials = (connector: ProjectPublicConnector) => {
    modalSeq.current += 1;
    setModal({ mode: "credentials", connector });
    setDraft(safeTextDraft(connector));
    setSubmitError(null);
    setConflict(false);
  };

  const openRevoke = (connector: ProjectPublicConnector) => {
    modalSeq.current += 1;
    setModal({ mode: "revoke", connector });
    setDraft(safeTextDraft(connector));
    setSubmitError(null);
    setConflict(false);
  };

  const items = collection?.items ?? [];
  const manager = canManage(detail);
  const archived = detail?.archived === true;
  const projectRevision = collection?.public_connectors_revision ?? 0;
  const loaded = detail != null && collection != null;
  const disableWrites = !loaded || !manager || submitting;
  const currentConnector = modal?.connector
    ? items.find((item) => item.connector_id === modal.connector?.connector_id)
    : null;

  const activeItems = items.filter((item) => item.state === "active");

  const submit = async () => {
    if (!modal || !collection || !canSubmit) return;
    const key = ownerKeyRef.current;
    const seq = ++writeSeq.current;
    const openSeq = modalSeq.current;
    setSubmitting(true);
    setSubmitError(null);
    setConflict(false);
    try {
      let response: ProjectPublicConnectorsResponse;
      if (modal.mode === "create") {
        response = await projectPublicConnectorsApi.create(projectId, {
          expected_project_revision: projectRevision,
          kind: "http_mcp_static_bearer",
          display_name: draft.displayName,
          description: draft.description,
          endpoint: draft.endpoint,
          bearer_token: draft.bearerToken,
        });
      } else if (
        modal.mode === "rename" &&
        modal.connector &&
        currentConnector
      ) {
        response = await projectPublicConnectorsApi.rename(
          projectId,
          modal.connector.connector_id,
          {
            expected_project_revision: projectRevision,
            expected_grant_revision: currentConnector.grant_revision,
            display_name: draft.displayName,
            description: draft.description,
          },
        );
      } else if (
        modal.mode === "credentials" &&
        modal.connector &&
        currentConnector
      ) {
        response = await projectPublicConnectorsApi.replaceCredentials(
          projectId,
          modal.connector.connector_id,
          {
            expected_project_revision: projectRevision,
            expected_grant_revision: currentConnector.grant_revision,
            endpoint: draft.endpoint,
            bearer_token: draft.bearerToken,
          },
        );
      } else if (
        modal.mode === "revoke" &&
        modal.connector &&
        currentConnector
      ) {
        response = await projectPublicConnectorsApi.revoke(
          projectId,
          modal.connector.connector_id,
          {
            expected_project_revision: projectRevision,
            expected_grant_revision: currentConnector.grant_revision,
          },
        );
      } else {
        return;
      }
      if (
        key !== ownerKeyRef.current ||
        seq !== writeSeq.current ||
        openSeq !== modalSeq.current
      ) {
        return;
      }
      applyCollection(response);
      setDraft(blankDraft());
      setModal(null);
      setSubmitError(null);
      setConflict(false);
      modalSeq.current += 1;
    } catch (err) {
      if (
        key !== ownerKeyRef.current ||
        seq !== writeSeq.current ||
        openSeq !== modalSeq.current
      ) {
        return;
      }
      clearSecrets();
      setSubmitError(err);
      if (isChangedError(err)) {
        setConflict(true);
        await refreshAfterOutcome(key, seq);
      } else if (isAccessLoss(err)) {
        setCollection(null);
        setDetail(null);
        setDraft(blankDraft());
        setModal(null);
        modalSeq.current += 1;
        setError(err);
      } else {
        await refreshAfterOutcome(key, seq);
      }
    } finally {
      if (key === ownerKeyRef.current && seq === writeSeq.current)
        setSubmitting(false);
    }
  };

  const modalTitle = modal
    ? modal.mode === "create"
      ? t("projects.publicConnectors.createTitle", "Add public connector")
      : modal.mode === "rename"
      ? t("projects.publicConnectors.renameTitle", "Rename connector")
      : modal.mode === "credentials"
      ? t("projects.publicConnectors.credentialsTitle", "Replace credentials")
      : t("projects.publicConnectors.revokeTitle", "Revoke connector")
    : "";

  const needsCredentials =
    modal?.mode === "create" || modal?.mode === "credentials";
  const canSubmit =
    loaded &&
    manager &&
    !submitting &&
    modal != null &&
    (modal.mode === "create"
      ? !archived
      : currentConnector?.state === "active" &&
        (modal.mode === "revoke" || !archived)) &&
    (modal.mode === "revoke" ||
      (draft.displayName.trim().length > 0 &&
        (!needsCredentials ||
          (draft.endpoint.trim().length > 0 && draft.bearerToken.length > 0))));

  return (
    <section
      aria-label={t("projects.publicConnectors.title", "Public connectors")}
    >
      <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            fontWeight: 500,
          }}
        >
          <Plug size={14} aria-hidden />
          {t("projects.publicConnectors.title", "Public connectors")}
        </span>
        {loaded ? (
          <Tag style={{ marginInlineEnd: 0 }}>{items.length}</Tag>
        ) : null}
      </div>
      <div style={{ marginTop: 8, fontSize: 12, color: "rgba(0,0,0,0.45)" }}>
        {t(
          "projects.publicConnectors.scopeNote",
          "Stored project connector settings only. Project tasks cannot call these public tools yet.",
        )}
      </div>

      {loading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 16 }}>
          <Spin size="small" />
        </div>
      ) : error ? (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 10 }}
          message={safeErrorMessage(
            error,
            t(
              "projects.publicConnectors.loadFailed",
              "Failed to load connector settings",
            ),
          )}
          action={
            <Button
              size="small"
              icon={<RefreshCw size={14} />}
              onClick={refresh}
            >
              {t("projects.publicConnectors.reload", "Reload")}
            </Button>
          }
        />
      ) : (
        <>
          {archived ? (
            <Alert
              type="info"
              showIcon
              style={{ marginTop: 10 }}
              message={t(
                "projects.publicConnectors.archivedNote",
                "This project is archived. You can read settings and revoke active connectors, but cannot create, rename, or replace credentials.",
              )}
            />
          ) : null}
          {manager ? (
            <Button
              size="small"
              type="primary"
              style={{ marginTop: 10 }}
              onClick={openCreate}
              disabled={disableWrites || archived}
            >
              {t("projects.publicConnectors.create", "Add connector")}
            </Button>
          ) : (
            <div
              style={{ marginTop: 10, fontSize: 12, color: "rgba(0,0,0,0.45)" }}
            >
              {t(
                "projects.publicConnectors.readOnly",
                "Members can view safe connector metadata. Owners and admins manage settings.",
              )}
            </div>
          )}
          {items.length === 0 ? (
            <div style={{ marginTop: 10, color: "rgba(0,0,0,0.45)" }}>
              {t(
                "projects.publicConnectors.empty",
                "No public connectors configured.",
              )}
            </div>
          ) : (
            <ul style={{ listStyle: "none", margin: "10px 0 0", padding: 0 }}>
              {items.map((item) => {
                const revoked = item.state === "revoked";
                const mayChange = manager && !archived && !revoked;
                const mayRevoke = manager && !revoked;
                return (
                  <li
                    key={item.connector_id}
                    style={{
                      borderTop: "1px solid rgba(0,0,0,0.08)",
                      padding: "10px 0",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 8,
                      }}
                    >
                      <div style={{ minWidth: 0 }}>
                        <div
                          style={{ fontWeight: 500, overflowWrap: "anywhere" }}
                        >
                          {item.display_name}
                        </div>
                        {item.description ? (
                          <div
                            style={{
                              fontSize: 12,
                              color: "rgba(0,0,0,0.45)",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {item.description}
                          </div>
                        ) : null}
                      </div>
                      <Tag
                        color={revoked ? "default" : "green"}
                        style={{ marginInlineEnd: 0 }}
                      >
                        {revoked
                          ? t("projects.publicConnectors.revoked", "Revoked")
                          : t("projects.publicConnectors.active", "Active")}
                      </Tag>
                    </div>
                    <div
                      style={{
                        marginTop: 6,
                        fontSize: 12,
                        color: "rgba(0,0,0,0.45)",
                      }}
                    >
                      {t(
                        "projects.publicConnectors.updatedAt",
                        "Updated {{time}}",
                        {
                          time: formatServerDateTime(item.updated_at, timezone),
                        },
                      )}
                    </div>
                    {revoked ? (
                      <div
                        style={{
                          marginTop: 6,
                          fontSize: 12,
                          color: "rgba(0,0,0,0.45)",
                        }}
                      >
                        {t(
                          "projects.publicConnectors.revokedNote",
                          "Revoked connectors are terminal tombstones and cannot be reactivated.",
                        )}
                      </div>
                    ) : null}
                    {manager ? (
                      <div
                        style={{
                          display: "flex",
                          flexWrap: "wrap",
                          gap: 6,
                          marginTop: 8,
                        }}
                      >
                        <Button
                          size="small"
                          icon={<Pencil size={14} />}
                          disabled={!mayChange || submitting}
                          onClick={() => openRename(item)}
                        >
                          {t("projects.publicConnectors.rename", "Rename")}
                        </Button>
                        <Button
                          size="small"
                          icon={<KeyRound size={14} />}
                          disabled={!mayChange || submitting}
                          onClick={() => openCredentials(item)}
                        >
                          {t(
                            "projects.publicConnectors.replaceCredentials",
                            "Replace credentials",
                          )}
                        </Button>
                        <Button
                          size="small"
                          danger
                          icon={<Trash2 size={14} />}
                          disabled={!mayRevoke || submitting}
                          onClick={() => openRevoke(item)}
                        >
                          {t("projects.publicConnectors.revoke", "Revoke")}
                        </Button>
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
          {manager && archived && activeItems.length > 0 ? (
            <div
              style={{ marginTop: 8, fontSize: 12, color: "rgba(0,0,0,0.45)" }}
            >
              {t(
                "projects.publicConnectors.archivedRevokeOnly",
                "Archived projects allow revoke only for active connectors.",
              )}
            </div>
          ) : null}
        </>
      )}

      <Modal
        title={modalTitle}
        open={modal != null}
        okText={
          modal?.mode === "revoke"
            ? t("projects.publicConnectors.confirmRevoke", "Revoke")
            : t("projects.publicConnectors.save", "Save")
        }
        okButtonProps={{
          danger: modal?.mode === "revoke",
          disabled: !canSubmit,
          loading: submitting,
        }}
        onOk={() => void submit()}
        onCancel={closeModal}
        maskClosable={!submitting}
        closable={!submitting}
        destroyOnHidden
      >
        {modal?.mode === "revoke" ? (
          <Alert
            type="warning"
            showIcon
            message={t(
              "projects.publicConnectors.revokeConfirm",
              "Revoke this connector? The connector ID stays as a terminal tombstone and cannot be reused.",
            )}
          />
        ) : (
          <>
            <label style={{ display: "block", marginBottom: 8 }}>
              <div>
                {t("projects.publicConnectors.displayName", "Display name")}
              </div>
              <Input
                value={draft.displayName}
                disabled={submitting}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    displayName: event.target.value,
                  }))
                }
              />
            </label>
            <label style={{ display: "block", marginBottom: 8 }}>
              <div>
                {t("projects.publicConnectors.description", "Description")}
              </div>
              <Input.TextArea
                rows={2}
                value={draft.description}
                disabled={submitting}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    description: event.target.value,
                  }))
                }
              />
            </label>
            {needsCredentials ? (
              <>
                <label style={{ display: "block", marginBottom: 8 }}>
                  <div>
                    {t("projects.publicConnectors.endpoint", "HTTPS endpoint")}
                  </div>
                  <Input
                    value={draft.endpoint}
                    disabled={submitting}
                    autoComplete="off"
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        endpoint: event.target.value,
                      }))
                    }
                  />
                </label>
                <label style={{ display: "block", marginBottom: 8 }}>
                  <div>
                    {t("projects.publicConnectors.bearerToken", "Bearer token")}
                  </div>
                  <Input.Password
                    value={draft.bearerToken}
                    disabled={submitting}
                    autoComplete="off"
                    onChange={(event) =>
                      setDraft((current) => ({
                        ...current,
                        bearerToken: event.target.value,
                      }))
                    }
                  />
                </label>
                <Alert
                  type="info"
                  showIcon
                  message={t(
                    "projects.publicConnectors.volatileSecretWarning",
                    "Endpoint and token are write-only. They are cleared after every outcome and are never shown by GET.",
                  )}
                />
              </>
            ) : null}
          </>
        )}
        {conflict ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginTop: 12 }}
            message={t(
              "projects.publicConnectors.conflict",
              "Connector settings changed on the server. Safe metadata was refreshed; re-enter credentials before submitting again.",
            )}
          />
        ) : null}
        {submitError ? (
          <Alert
            type="error"
            showIcon
            style={{ marginTop: 12 }}
            message={safeErrorMessage(
              submitError,
              t(
                "projects.publicConnectors.saveFailed",
                "Failed to save connector settings",
              ),
            )}
          />
        ) : null}
        <Text type="secondary" style={{ display: "block", marginTop: 12 }}>
          {t(
            "projects.publicConnectors.modalScopeNote",
            "This only saves project settings. Task/runtime use of public tools is a later slice.",
          )}
        </Text>
      </Modal>
    </section>
  );
}
