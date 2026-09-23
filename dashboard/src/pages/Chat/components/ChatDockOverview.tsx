import { useMemo, useState } from "react";
import { Package } from "lucide-react";
import { useTranslation } from "react-i18next";
import { fileTreeIcon } from "../../../utils/fileTreeIcon";
import {
  dockFileBasename,
  listDockFilePathsForTree,
} from "../utils/dockFilePath";
import styles from "../index.module.less";

interface ChatDockOverviewProps {
  agentId: string;
  agentName?: string | null;
  threadTitle?: string | null;
  isStreaming?: boolean;
  /** Recorded artifacts of the active thread only. */
  artifacts: string[];
  /** Open the artifacts tab; hidden when the dock has no such opener. */
  onOpenArtifacts?: () => void;
  onOpenFile: (path: string) => void;
}

const RECENT_ARTIFACT_LIMIT = 3;

/**
 * Desktop right-rail overview: greeting, current task status and a real
 * artifact opener. Quick actions only use existing dock handlers.
 */
export default function ChatDockOverview({
  agentId,
  agentName = null,
  threadTitle = null,
  isStreaming = false,
  artifacts,
  onOpenArtifacts,
  onOpenFile,
}: ChatDockOverviewProps) {
  const { t } = useTranslation();
  const [logoFallback, setLogoFallback] = useState(false);
  const paths = useMemo(
    () => listDockFilePathsForTree(artifacts, agentId),
    [artifacts, agentId],
  );
  const recent = paths.slice(0, RECENT_ARTIFACT_LIMIT);
  const name = agentName?.trim();

  return (
    <div className={styles.dockOverview}>
      <div className={styles.dockOverviewHeader}>
        {logoFallback ? (
          <span
            className={`${styles.dockOverviewLogo} ${styles.dockOverviewLogoFallback}`}
            aria-hidden="true"
          >
            熊
          </span>
        ) : (
          <img
            className={styles.dockOverviewLogo}
            src="/xiongbao-logo.png"
            alt=""
            onError={() => setLogoFallback(true)}
          />
        )}
        <div className={styles.dockOverviewHeadings}>
          <h3 className={styles.dockOverviewGreeting}>
            {name
              ? t("chat.dockOverviewGreeting", {
                  name,
                  defaultValue: "你好，{{name}}",
                })
              : t("chat.dockOverviewGreetingGeneric", "你好")}
          </h3>
          <p className={styles.dockOverviewTask} title={threadTitle ?? ""}>
            {threadTitle?.trim() ||
              t("chat.dockOverviewNoTask", "当前没有进行中的任务")}
          </p>
        </div>
      </div>

      <div className={styles.dockOverviewStatus}>
        <span
          className={`${styles.dockOverviewStatusDot} ${
            isStreaming ? styles.dockOverviewStatusDotActive : ""
          }`}
          aria-hidden
        />
        <span>
          {isStreaming
            ? t("chat.dockOverviewRunning", "任务进行中…")
            : t("chat.dockOverviewIdle", "任务待命中")}
        </span>
      </div>

      {onOpenArtifacts ? (
        <button
          type="button"
          className={styles.dockOverviewArtifactsBtn}
          onClick={onOpenArtifacts}
        >
          <Package size={16} strokeWidth={2} aria-hidden />
          <span>
            {t("chat.dockOverviewOpenArtifacts", {
              count: paths.length,
              defaultValue: "查看产物（{{count}}）",
            })}
          </span>
        </button>
      ) : null}

      <div className={styles.dockOverviewRecent}>
        <div className={styles.dockOverviewRecentTitle}>
          {t("chat.dockOverviewRecentArtifacts", "最近产物")}
        </div>
        {recent.length === 0 ? (
          <p className={styles.dockOverviewRecentEmpty}>
            {t("chat.dockOverviewRecentEmpty", "暂无产物")}
          </p>
        ) : (
          <ul className={styles.dockArtifactItems}>
            {recent.map((path) => (
              <li key={path}>
                <button
                  type="button"
                  className={styles.dockArtifactItem}
                  onClick={() => onOpenFile(path)}
                  title={path}
                >
                  <span className={styles.dockArtifactIcon} aria-hidden>
                    {fileTreeIcon(path, 16)}
                  </span>
                  <span className={styles.dockArtifactMeta}>
                    <span className={styles.dockArtifactName}>
                      {dockFileBasename(path)}
                    </span>
                    <span className={styles.dockArtifactPath}>{path}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
