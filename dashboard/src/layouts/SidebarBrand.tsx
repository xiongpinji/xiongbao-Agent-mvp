import AppVersionBadge from "../components/AppVersionBadge";
import CurrentVersionBadge from "../components/CurrentVersionBadge";
import { DESKTOP_NO_DRAG_CLASS } from "../utils/desktopChrome";
import styles from "./Sidebar.module.less";

interface SidebarBrandProps {
  name: string;
  collapsed: boolean;
  isMobile: boolean;
  onClick: () => void;
}

export default function SidebarBrand({
  name,
  collapsed,
  isMobile,
  onClick,
}: SidebarBrandProps) {
  return (
    <button
      type="button"
      className={`${styles.sidebarBrand} ${DESKTOP_NO_DRAG_CLASS}`}
      aria-label={name}
      onClick={onClick}
    >
      <img
        src="/xiongbao-logo.png"
        alt=""
        draggable={false}
        style={{
          height: collapsed ? 32 : isMobile ? 38 : 36,
          width: collapsed ? 32 : isMobile ? 38 : 36,
          objectFit: "contain",
          display: "block",
          flexShrink: 0,
          borderRadius: collapsed ? 8 : 10,
        }}
      />
      {!collapsed && <span className={styles.brandName}>{name}</span>}
      {!collapsed && !isMobile && (
        <>
          <CurrentVersionBadge isMobile={isMobile} />
          <AppVersionBadge isMobile={isMobile} />
        </>
      )}
    </button>
  );
}
