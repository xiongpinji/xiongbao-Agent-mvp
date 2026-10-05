import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { ArchiveMutationResult } from "../hooks/useSessions";

/** Personal organization only: restoring never changes navigation or execution. */
export default function ThreadArchiveBanner({
  onRestore,
}: {
  onRestore: () => Promise<ArchiveMutationResult>;
}) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  return (
    <div
      role="status"
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "8px 16px",
        background: "var(--fn-bg-tertiary)",
        color: "var(--fn-text-primary)",
      }}
    >
      <span>{t("archive.currentBanner")}</span>
      <button
        type="button"
        disabled={busy}
        onClick={() => {
          setBusy(true);
          void onRestore().finally(() => setBusy(false));
        }}
      >
        {t(busy ? "dataManagement.restoring" : "archive.restore")}
      </button>
    </div>
  );
}
