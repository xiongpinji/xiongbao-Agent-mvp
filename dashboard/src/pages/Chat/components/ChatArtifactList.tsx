import { useMemo } from "react";
import { Empty } from "antd";
import { Package } from "lucide-react";
import { useTranslation } from "react-i18next";
import { fileTreeIcon } from "../../../utils/fileTreeIcon";
import {
  dockFileBasename,
  listDockFilePathsForTree,
} from "../utils/dockFilePath";
import styles from "../index.module.less";

interface ChatArtifactListProps {
  agentId: string;
  /** Recorded artifacts of the active thread only (no opened file tabs). */
  artifacts: string[];
  onOpenFile: (path: string) => void;
}

/**
 * Current-thread deliverables (“产物”). Unlike the merged file-changes tree,
 * this list only ever shows artifacts recorded for the active thread.
 */
export default function ChatArtifactList({
  agentId,
  artifacts,
  onOpenFile,
}: ChatArtifactListProps) {
  const { t } = useTranslation();
  const paths = useMemo(
    () => listDockFilePathsForTree(artifacts, agentId),
    [artifacts, agentId],
  );

  if (paths.length === 0) {
    return (
      <div className={styles.dockArtifactList}>
        <div className={styles.dockArtifactListEmpty}>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={t("chat.dockArtifactsEmpty", "当前任务暂无产物")}
          />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.dockArtifactList}>
      <div className={styles.dockArtifactListSummary}>
        <Package size={15} strokeWidth={2} aria-hidden />
        <span>
          {t("chat.dockArtifactsCount", {
            count: paths.length,
            defaultValue: "{{count}} 个产物",
          })}
        </span>
      </div>
      <ul className={styles.dockArtifactItems}>
        {paths.map((path) => (
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
    </div>
  );
}
