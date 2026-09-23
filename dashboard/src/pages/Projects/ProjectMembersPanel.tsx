import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Spin,
  Switch,
  Tag,
} from "antd";
import { Users } from "lucide-react";
import {
  projectMembershipApi,
  type CreatedProjectInvite,
  type ProjectInvite,
  type ProjectJoinRequest,
} from "../../api/modules/projectMembership";
import type { ProjectMember, ProjectRole } from "../../api/modules/projects";
import { apiErrorMessage, isNotFoundApiError } from "../../utils/apiError";
import { message } from "../../utils/antdMessage";
import { copyText } from "../../utils/copyText";
import { projectRoleTag } from "./index";

interface Props {
  projectId: string;
  role: ProjectRole;
  members: ProjectMember[] | null;
  onChanged: () => void;
}

/** Project membership is distinct from Agent expert teams. All writes use project ACL APIs. */
export default function ProjectMembersPanel({
  projectId,
  role,
  members,
  onChanged,
}: Props) {
  const { t } = useTranslation();
  const canManage = role === "owner" || role === "admin";
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [invites, setInvites] = useState<ProjectInvite[]>([]);
  const [requests, setRequests] = useState<ProjectJoinRequest[]>([]);
  const [created, setCreated] = useState<CreatedProjectInvite | null>(null);
  const [requiresApproval, setRequiresApproval] = useState(false);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [copied, setCopied] = useState(false);
  const sequence = useRef(0);

  const load = useCallback(async () => {
    const current = ++sequence.current;
    setLoading(true);
    try {
      const [invitePage, requestPage] = await Promise.all([
        projectMembershipApi.listInvites(projectId),
        projectMembershipApi.listJoinRequests(projectId, "pending"),
      ]);
      if (current !== sequence.current) return;
      setInvites(invitePage.items);
      setRequests(requestPage.items);
      setError(null);
    } catch (err) {
      if (current !== sequence.current) return;
      setInvites([]);
      setRequests([]);
      setError(err);
      if (isNotFoundApiError(err)) {
        setOpen(false);
        onChanged();
      }
    } finally {
      if (current === sequence.current) setLoading(false);
    }
  }, [onChanged, projectId]);

  useEffect(() => {
    if (!open || !canManage) return;
    const requestSequence = sequence;
    void load();
    return () => {
      requestSequence.current++;
    };
  }, [canManage, load, open]);

  const close = () => {
    setOpen(false);
    setCreated(null);
    setCopied(false);
    setError(null);
  };

  const run = async (
    action: () => Promise<unknown>,
    membershipChanged = false,
  ) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      if (membershipChanged) onChanged();
      if (open) await load();
    } catch (err) {
      setError(err);
      if (isNotFoundApiError(err)) {
        close();
        onChanged();
      }
    } finally {
      setBusy(false);
    }
  };

  const createInvite = () =>
    void run(async () => {
      const invite = await projectMembershipApi.createInvite(projectId, {
        requires_approval: requiresApproval,
        expires_in_days: expiresInDays,
      });
      setCreated(invite);
      setCopied(false);
    });

  const inviteUrl = created
    ? `${window.location.origin}/projects?invite=${encodeURIComponent(
        created.token,
      )}`
    : "";

  return (
    <section aria-label={t("projects.config.members", "成员")}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: 16,
          marginBottom: 4,
        }}
      >
        <span style={{ fontSize: 13, fontWeight: 500 }}>
          <Users size={14} style={{ verticalAlign: "-2px", marginRight: 6 }} />
          {t("projects.members.count", {
            value: members?.length ?? "—",
            defaultValue: "成员（{{value}}）",
          })}
        </span>
      </div>
      {members === null ? (
        <div style={{ fontSize: 12 }}>
          {t("projects.config.membersLoadFailed", "成员列表加载失败")}
        </div>
      ) : (
        <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
          {members.map((member) => {
            const memberRole = projectRoleTag(member.role);
            const canRemove =
              canManage &&
              member.role !== "owner" &&
              (role === "owner" || member.role === "member");
            return (
              <li
                key={member.user_id}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 6,
                  padding: "6px 0",
                }}
              >
                <span
                  style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere" }}
                >
                  {member.username}
                </span>
                {role === "owner" && member.role !== "owner" ? (
                  <Select
                    size="small"
                    aria-label={t(
                      "projects.members.changeRole",
                      "更改成员角色",
                    )}
                    value={member.role}
                    disabled={busy}
                    style={{ width: 88 }}
                    options={[
                      {
                        value: "member",
                        label: t("projects.roleMember", "成员"),
                      },
                      {
                        value: "admin",
                        label: t("projects.roleAdmin", "管理员"),
                      },
                    ]}
                    onChange={(next: "member" | "admin") =>
                      void run(
                        () =>
                          projectMembershipApi.setMemberRole(
                            projectId,
                            member.user_id,
                            next,
                          ),
                        true,
                      )
                    }
                  />
                ) : (
                  <Tag color={memberRole.color} style={{ marginInlineEnd: 0 }}>
                    {t(memberRole.labelKey, memberRole.fallback)}
                  </Tag>
                )}
                {canRemove && (
                  <Popconfirm
                    title={t("projects.members.removeConfirm", "移除此成员？")}
                    onConfirm={() =>
                      void run(
                        () =>
                          projectMembershipApi.removeMember(
                            projectId,
                            member.user_id,
                          ),
                        true,
                      )
                    }
                  >
                    <Button size="small" type="text" danger disabled={busy}>
                      {t("projects.members.remove", "移除")}
                    </Button>
                  </Popconfirm>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {canManage && (
        <Button
          size="small"
          style={{ marginTop: 8 }}
          onClick={() => setOpen(true)}
        >
          {t("projects.config.invite", "邀请成员")}
        </Button>
      )}
      {error != null && !open && (
        <Alert
          type="error"
          showIcon
          style={{ marginTop: 8 }}
          message={apiErrorMessage(
            error,
            t("projects.members.actionFailed", "操作失败"),
            t,
          )}
        />
      )}

      <Modal
        title={t("projects.members.manageTitle", "邀请与加入申请")}
        open={open}
        onCancel={close}
        footer={null}
        destroyOnHidden
        maskClosable={!created}
        keyboard={!created}
      >
        <div style={{ display: "grid", gap: 14 }}>
          {error != null && (
            <Alert
              type="error"
              showIcon
              message={apiErrorMessage(
                error,
                t("projects.members.loadFailed", "加载失败"),
                t,
              )}
              action={
                <Button onClick={() => void load()}>
                  {t("common.retry", "重试")}
                </Button>
              }
            />
          )}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              flexWrap: "wrap",
            }}
          >
            <label htmlFor="project-invite-approval">
              {t("projects.members.approval", "加入需要审批")}
            </label>
            <Switch
              id="project-invite-approval"
              checked={requiresApproval}
              onChange={setRequiresApproval}
              disabled={busy}
            />
            <label htmlFor="project-invite-expiry">
              {t("projects.members.expiry", "有效天数")}
            </label>
            <InputNumber
              id="project-invite-expiry"
              min={1}
              max={7}
              precision={0}
              step={1}
              value={expiresInDays}
              onChange={(value) => setExpiresInDays(value ?? 7)}
              disabled={busy}
            />
            <Button type="primary" loading={busy} onClick={createInvite}>
              {t("projects.members.createLink", "生成邀请链接")}
            </Button>
          </div>
          {created && (
            <div>
              <Alert
                type="warning"
                showIcon
                message={t(
                  "projects.members.oneTime",
                  "链接仅在此显示一次，请现在复制。",
                )}
                style={{ marginBottom: 8 }}
              />
              <div style={{ display: "flex", gap: 8 }}>
                <Input
                  readOnly
                  value={inviteUrl}
                  aria-label={t("projects.members.link", "邀请链接")}
                />
                <Button
                  onClick={async () => {
                    const ok = await copyText(inviteUrl);
                    setCopied(ok);
                    if (!ok) {
                      void message.error(
                        t("common.copyFailed", "复制到剪贴板失败"),
                      );
                    }
                  }}
                >
                  {copied
                    ? t("projects.members.copied", "已复制")
                    : t("projects.members.copy", "复制")}
                </Button>
              </div>
            </div>
          )}
          <div>
            <strong>{t("projects.members.invites", "邀请链接")}</strong>
            {loading ? (
              <Spin style={{ display: "block", marginTop: 8 }} />
            ) : invites.length === 0 ? (
              <p>{t("projects.members.noInvites", "尚无邀请链接")}</p>
            ) : (
              <ul style={{ paddingLeft: 18 }}>
                {invites.map((invite) => (
                  <li key={invite.invite_id} style={{ marginTop: 6 }}>
                    {invite.requires_approval
                      ? t("projects.members.approval", "加入需要审批")
                      : t("projects.members.direct", "直接加入")}{" "}
                    ·{" "}
                    {t(
                      `projects.members.status.${invite.status}`,
                      invite.status,
                    )}
                    {invite.status === "pending" && (
                      <Button
                        size="small"
                        type="link"
                        danger
                        disabled={busy}
                        onClick={() =>
                          void run(() =>
                            projectMembershipApi.revokeInvite(
                              projectId,
                              invite.invite_id,
                            ),
                          )
                        }
                      >
                        {t("projects.members.revoke", "撤销")}
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <strong>{t("projects.members.requests", "待审批申请")}</strong>
            {loading ? (
              <Spin style={{ display: "block", marginTop: 8 }} />
            ) : requests.length === 0 ? (
              <p>{t("projects.members.noRequests", "暂无待审批申请")}</p>
            ) : (
              <ul style={{ paddingLeft: 18 }}>
                {requests.map((entry) => (
                  <li key={entry.request_id}>
                    {entry.username}{" "}
                    <Button
                      size="small"
                      type="link"
                      disabled={busy}
                      onClick={() =>
                        void run(
                          () =>
                            projectMembershipApi.approveJoinRequest(
                              projectId,
                              entry.request_id,
                            ),
                          true,
                        )
                      }
                    >
                      {t("projects.members.approve", "通过")}
                    </Button>
                    <Button
                      size="small"
                      type="link"
                      danger
                      disabled={busy}
                      onClick={() =>
                        void run(() =>
                          projectMembershipApi.rejectJoinRequest(
                            projectId,
                            entry.request_id,
                          ),
                        )
                      }
                    >
                      {t("projects.members.reject", "拒绝")}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </Modal>
    </section>
  );
}
