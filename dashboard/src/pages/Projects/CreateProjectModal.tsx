/**
 * CreateProjectModal — create a project, or edit an existing one when
 * `editTarget` is provided (owner/admin only; enforced again server-side).
 *
 * Rules (PROJECT_SPACE_SPEC.md PS-01):
 * - name is trimmed and must be 1–15 characters; submit stays disabled otherwise
 * - the five template cards only prefill description/instructions — the user
 *   names the project, and no fake projects are created
 * - switching templates while the form is dirty asks before overwriting
 * - a seed applies once per closed → open edge, so parent prop drift or a
 *   locale change never resets a draft in progress
 * - on POST/PATCH failure the form is preserved and the error shown inline
 */

import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Alert, Button, Input, Modal } from "antd";
import { message } from "../../utils/antdMessage";
import { apiErrorMessage } from "../../utils/apiError";
import { projectsApi, type ProjectRecord } from "../../api/modules/projects";

const NAME_MAX = 15;

export interface TemplateSeed {
  id: string;
  /** Original 熊宝 template copy (zh source; en lives in the locale file). */
  name: string;
  description: string;
  instructions: string;
}

/** Five prefill seeds shared by the modal cards and the home template entries. */
export const TEMPLATES: TemplateSeed[] = [
  {
    id: "requirements",
    name: "产品需求管理",
    description: "收集与评审产品需求，跟踪状态与版本计划。",
    instructions:
      "整理需求时写明背景、目标用户、功能点、验收标准与优先级，输出需求文档草稿。",
  },
  {
    id: "competitor",
    name: "竞品分析",
    description: "持续跟踪竞品动态，沉淀功能对比与差异化结论。",
    instructions:
      "对比竞品时列出功能矩阵、定价、优劣势与可借鉴点，标注信息来源与观察时间。",
  },
  {
    id: "knowledge",
    name: "团队知识库",
    description: "集中维护团队文档、规范与常见问题解答。",
    instructions: "回答时优先引用知识库条目并注明出处；缺失内容标记为待补充。",
  },
  {
    id: "delivery",
    name: "项目交付",
    description: "管理交付里程碑、风险与验收清单。",
    instructions:
      "按里程碑推进交付：拆解任务、明确负责人与截止时间，及时暴露风险。",
  },
  {
    id: "bugTracking",
    name: "Bug 跟踪",
    description: "记录、分派并跟踪缺陷直至修复验证。",
    instructions:
      "每个缺陷记录复现步骤、影响范围、严重级别与修复状态，修复后回归验证。",
  },
];

interface FormSnapshot {
  name: string;
  description: string;
  instructions: string;
}

const EMPTY_FORM: FormSnapshot = {
  name: "",
  description: "",
  instructions: "",
};

/**
 * Resolve a seed's localized copy into a form snapshot. The name is always
 * left blank: templates only prefill description and instructions, matching
 * the observed create flow where the user names the project.
 */
function templateSnapshot(tpl: TemplateSeed, t: TFunction): FormSnapshot {
  return {
    name: "",
    description: t(`projects.templates.${tpl.id}.description`, tpl.description),
    instructions: t(
      `projects.templates.${tpl.id}.instructions`,
      tpl.instructions,
    ),
  };
}

export interface CreateProjectModalProps {
  open: boolean;
  /**
   * Create mode only: prefill the form from this template seed when the modal
   * opens. Ignored while `editTarget` is set and never persisted on its own.
   */
  initialTemplateId?: string | null;
  /** When set, the modal edits this project instead of creating one. */
  editTarget?: ProjectRecord | null;
  onClose: () => void;
  /** Called after a successful POST/PATCH with the server record. */
  onSaved: (project: ProjectRecord) => void;
}

export default function CreateProjectModal({
  open,
  initialTemplateId,
  editTarget,
  onClose,
  onSaved,
}: CreateProjectModalProps) {
  const { t } = useTranslation();

  const [form, setForm] = useState<FormSnapshot>(EMPTY_FORM);
  /** Snapshot of the last applied state (open/template) for dirty tracking. */
  const [lastApplied, setLastApplied] = useState<FormSnapshot>(EMPTY_FORM);
  const [appliedTemplate, setAppliedTemplate] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  /** Tracks the previous `open` value to detect the closing → opening edge. */
  const wasOpenRef = useRef(false);

  useEffect(() => {
    const justOpened = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    // The initial seed is one opening event: a parent prop drift or a locale
    // change while the modal stays open must not reset a draft in progress.
    if (!justOpened) return;
    let initial: FormSnapshot = EMPTY_FORM;
    let templateId: string | null = null;
    if (editTarget) {
      initial = {
        name: editTarget.name,
        description: editTarget.description ?? "",
        instructions: editTarget.instructions ?? "",
      };
    } else if (initialTemplateId) {
      const seed = TEMPLATES.find((tpl) => tpl.id === initialTemplateId);
      if (seed) {
        initial = templateSnapshot(seed, t);
        templateId = seed.id;
      }
    }
    setForm(initial);
    setLastApplied(initial);
    setAppliedTemplate(templateId);
    setSubmitting(false);
    setSubmitError(null);
  }, [open, editTarget, initialTemplateId, t]);

  const trimmedName = form.name.trim();
  const nameLength = Array.from(trimmedName).length;
  const nameTooLong = nameLength > NAME_MAX;
  const nameBlankTyped = form.name.length > 0 && trimmedName.length === 0;
  const canSubmit = nameLength >= 1 && !nameTooLong && !submitting;
  const isDirty =
    form.name !== lastApplied.name ||
    form.description !== lastApplied.description ||
    form.instructions !== lastApplied.instructions;

  const setField = (field: keyof FormSnapshot, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const applyTemplate = (tpl: TemplateSeed) => {
    const next = templateSnapshot(tpl, t);
    setForm(next);
    setLastApplied(next);
    setAppliedTemplate(tpl.id);
  };

  const handleTemplateClick = (tpl: TemplateSeed) => {
    if (appliedTemplate === tpl.id && !isDirty) return;
    if (isDirty) {
      Modal.confirm({
        title: t("projects.create.overwriteTitle", "覆盖未保存内容？"),
        content: t(
          "projects.create.overwriteContent",
          "切换模板会覆盖表单中尚未保存的修改。",
        ),
        okText: t("projects.create.overwriteOk", "覆盖"),
        cancelText: t("projects.create.overwriteCancel", "保留当前内容"),
        onOk: () => applyTemplate(tpl),
      });
      return;
    }
    applyTemplate(tpl);
  };

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setSubmitError(null);
    const description = form.description.trim();
    const instructions = form.instructions.trim();
    try {
      const saved = editTarget
        ? await projectsApi.update(editTarget.project_id, {
            name: trimmedName,
            description,
            instructions,
          })
        : await projectsApi.create({
            name: trimmedName,
            ...(description ? { description } : {}),
            ...(instructions ? { instructions } : {}),
          });
      message.success(
        editTarget
          ? t("projects.create.successSaved", "已保存")
          : t("projects.create.successCreated", "项目已创建"),
      );
      onSaved(saved);
    } catch (err) {
      // Keep the form open and preserve user input on failure.
      setSubmitError(
        apiErrorMessage(
          err,
          editTarget
            ? t("projects.create.saveFailed", "保存失败")
            : t("projects.create.createFailed", "创建项目失败"),
          t,
        ),
      );
    } finally {
      setSubmitting(false);
    }
  };

  const labelStyle: React.CSSProperties = {
    display: "block",
    fontSize: 13,
    fontWeight: 500,
    margin: "12px 0 6px",
  };
  const helpStyle: React.CSSProperties = { fontSize: 12, marginTop: 4 };

  return (
    <Modal
      open={open}
      title={
        editTarget
          ? t("projects.create.editTitle", "编辑项目资料")
          : t("projects.create.title", "新建项目")
      }
      onCancel={submitting ? undefined : onClose}
      maskClosable={false}
      footer={[
        <Button key="cancel" onClick={onClose} disabled={submitting}>
          {t("common.cancel", "取消")}
        </Button>,
        <Button
          key="submit"
          type="primary"
          loading={submitting}
          disabled={!canSubmit}
          onClick={() => void handleSubmit()}
        >
          {editTarget
            ? t("projects.create.save", "保存")
            : t("projects.create.submit", "创建")}
        </Button>,
      ]}
    >
      {!editTarget && (
        <div style={{ marginBottom: 4 }}>
          <div style={labelStyle}>
            {t("projects.create.templatesTitle", "从模板开始")}
          </div>
          <div
            style={{
              color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
              fontSize: 12,
              marginBottom: 8,
            }}
          >
            {t(
              "projects.create.templatesHint",
              "模板只预填下方表单，可自由修改。",
            )}
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
              gap: 8,
            }}
          >
            {TEMPLATES.map((tpl) => {
              const selected = appliedTemplate === tpl.id;
              return (
                <button
                  key={tpl.id}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => handleTemplateClick(tpl)}
                  style={{
                    textAlign: "left",
                    padding: "8px 10px",
                    borderRadius: 8,
                    cursor: "pointer",
                    background: selected
                      ? "var(--fn-color-primary-bg, rgba(0,0,0,0.04))"
                      : "transparent",
                    border: `1px solid ${
                      selected
                        ? "var(--fn-color-primary, #d4a017)"
                        : "var(--fn-border-color, rgba(0,0,0,0.12))"
                    }`,
                  }}
                >
                  <div style={{ fontSize: 13, fontWeight: 600 }}>
                    {t(`projects.templates.${tpl.id}.name`, tpl.name)}
                  </div>
                  <div
                    style={{
                      fontSize: 12,
                      color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
                      marginTop: 2,
                    }}
                  >
                    {t(
                      `projects.templates.${tpl.id}.description`,
                      tpl.description,
                    )}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {submitError && (
        <Alert
          type="error"
          showIcon
          message={submitError}
          style={{ marginTop: 12 }}
        />
      )}

      <label style={labelStyle} htmlFor="project-name-input">
        {t("projects.create.name", "项目名称")}
      </label>
      <Input
        id="project-name-input"
        value={form.name}
        disabled={submitting}
        placeholder={t("projects.create.namePlaceholder", "例如：产品需求管理")}
        onChange={(e) => setField("name", e.target.value)}
        onPressEnter={() => void handleSubmit()}
      />
      {nameTooLong ? (
        <div style={{ ...helpStyle, color: "var(--fn-color-danger, #cf1322)" }}>
          {t("projects.create.nameTooLong", "名称最多 15 个字符")}
        </div>
      ) : nameBlankTyped ? (
        <div style={{ ...helpStyle, color: "var(--fn-color-danger, #cf1322)" }}>
          {t("projects.create.nameRequired", "请输入项目名称")}
        </div>
      ) : (
        <div
          style={{
            ...helpStyle,
            color: "var(--fn-text-tertiary, rgba(0,0,0,0.45))",
          }}
        >
          {t("projects.create.nameRule", "名称 1–15 个字符")} ·{" "}
          {t("projects.create.nameCounter", "{{length}}/15", {
            length: nameLength,
          })}
        </div>
      )}

      <label style={labelStyle} htmlFor="project-description-input">
        {t("projects.create.description", "描述")}
      </label>
      <Input.TextArea
        id="project-description-input"
        value={form.description}
        disabled={submitting}
        rows={2}
        placeholder={t(
          "projects.create.descriptionPlaceholder",
          "可选：项目目标与范围",
        )}
        onChange={(e) => setField("description", e.target.value)}
      />

      <label style={labelStyle} htmlFor="project-instructions-input">
        {t("projects.create.instructions", "项目指令")}
      </label>
      <Input.TextArea
        id="project-instructions-input"
        value={form.instructions}
        disabled={submitting}
        rows={4}
        placeholder={t(
          "projects.create.instructionsPlaceholder",
          "可选：项目内任务默认遵循的指令",
        )}
        onChange={(e) => setField("instructions", e.target.value)}
      />
    </Modal>
  );
}
