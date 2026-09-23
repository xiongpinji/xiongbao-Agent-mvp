import { useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { Briefcase, Code, Palette, type LucideIcon } from "lucide-react";
import { useWelcomeQuickCardsLayout } from "../hooks/useWelcomeQuickCardsLayout";
import WelcomeQuickCards, { WelcomeQuickCardProbe } from "./WelcomeQuickCards";
import styles from "../index.module.less";

const BRAND_LOGO = "/xiongbao-logo.png";

type ScenarioId = "office" | "code" | "design";

interface ScenarioTask {
  labelKey: string;
  promptKey: string;
}

interface Scenario {
  id: ScenarioId;
  labelKey: string;
  icon: LucideIcon;
  tasks: ScenarioTask[];
}

const DEFAULT_SCENARIO: ScenarioId = "office";

const SCENARIOS: Scenario[] = [
  {
    id: "office",
    labelKey: "chatWelcome.scenarioOffice",
    icon: Briefcase,
    tasks: [
      {
        labelKey: "chatWelcome.scenarioOfficeTask1",
        promptKey: "chatWelcome.scenarioOfficePrompt1",
      },
      {
        labelKey: "chatWelcome.scenarioOfficeTask2",
        promptKey: "chatWelcome.scenarioOfficePrompt2",
      },
      {
        labelKey: "chatWelcome.scenarioOfficeTask3",
        promptKey: "chatWelcome.scenarioOfficePrompt3",
      },
      {
        labelKey: "chatWelcome.scenarioOfficeTask4",
        promptKey: "chatWelcome.scenarioOfficePrompt4",
      },
    ],
  },
  {
    id: "code",
    labelKey: "chatWelcome.scenarioCode",
    icon: Code,
    tasks: [
      {
        labelKey: "chatWelcome.scenarioCodeTask1",
        promptKey: "chatWelcome.scenarioCodePrompt1",
      },
      {
        labelKey: "chatWelcome.scenarioCodeTask2",
        promptKey: "chatWelcome.scenarioCodePrompt2",
      },
      {
        labelKey: "chatWelcome.scenarioCodeTask3",
        promptKey: "chatWelcome.scenarioCodePrompt3",
      },
      {
        labelKey: "chatWelcome.scenarioCodeTask4",
        promptKey: "chatWelcome.scenarioCodePrompt4",
      },
    ],
  },
  {
    id: "design",
    labelKey: "chatWelcome.scenarioDesign",
    icon: Palette,
    tasks: [
      {
        labelKey: "chatWelcome.scenarioDesignTask1",
        promptKey: "chatWelcome.scenarioDesignPrompt1",
      },
      {
        labelKey: "chatWelcome.scenarioDesignTask2",
        promptKey: "chatWelcome.scenarioDesignPrompt2",
      },
      {
        labelKey: "chatWelcome.scenarioDesignTask3",
        promptKey: "chatWelcome.scenarioDesignPrompt3",
      },
      {
        labelKey: "chatWelcome.scenarioDesignTask4",
        promptKey: "chatWelcome.scenarioDesignPrompt4",
      },
    ],
  },
];

export interface WelcomeQuickCard {
  title: string;
  description: string;
  prompt: string;
  color: string;
  icon_name?: string | null;
  icon_url?: string | null;
  expertName?: string;
}

interface WelcomeScreenProps {
  onPromptClick: (text: string, options?: { prefill?: boolean }) => void;
  agentName?: string | null;
  welcomeSuffix?: string | null;
  quickCards: WelcomeQuickCard[];
  hideMascot?: boolean;
  isTeam?: boolean;
}

export default function WelcomeScreen({
  onPromptClick,
  agentName,
  welcomeSuffix,
  quickCards,
  hideMascot = false,
  isTeam = false,
}: WelcomeScreenProps) {
  const { t } = useTranslation();
  const [scenario, setScenario] = useState<ScenarioId>(DEFAULT_SCENARIO);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const {
    welcomeRef,
    headingRef,
    sectionTitleRef,
    probeRef,
    expanded,
    setExpanded,
    cards,
    showToggle,
    autoHideMascot,
  } = useWelcomeQuickCardsLayout(quickCards);

  const showMascot = !hideMascot && !autoHideMascot;
  const isDefaultScenario = scenario === DEFAULT_SCENARIO;

  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    let nextIndex = index;
    if (event.key === "ArrowRight") {
      nextIndex = (index + 1) % SCENARIOS.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (index - 1 + SCENARIOS.length) % SCENARIOS.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = SCENARIOS.length - 1;
    } else {
      return;
    }
    event.preventDefault();
    setScenario(SCENARIOS[nextIndex].id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div className={styles.welcome} ref={welcomeRef}>
      <div className={styles.welcomeInner}>
        <div className={styles.welcomeHeading} ref={headingRef}>
          {showMascot && (
            <img
              className={styles.welcomeBrandLogo}
              src={BRAND_LOGO}
              alt=""
              draggable={false}
            />
          )}
          <h1 className={styles.welcomeTitle}>
            {isTeam ? t("chatWelcome.teamHeading") : t("chatWelcome.greeting")}
          </h1>
          <p className={styles.welcomeSubtitle}>
            {isTeam ? (
              <span className={styles.welcomeSubtitleText}>
                {welcomeSuffix ?? t("chatWelcome.descriptionWithTeamSuffix")}
              </span>
            ) : agentName ? (
              <>
                <span className={styles.welcomeAgentLine}>
                  <span className={styles.welcomeAgentMention}>
                    @{agentName}
                  </span>
                </span>
                <span className={styles.welcomeSubtitleText}>
                  {welcomeSuffix ?? t("chatWelcome.descriptionWithAgentSuffix")}
                </span>
              </>
            ) : (
              t("chatWelcome.description")
            )}
          </p>
        </div>

        <div
          className={styles.scenarioTabs}
          role="tablist"
          aria-label={t("chatWelcome.scenarioTabsLabel")}
        >
          {SCENARIOS.map((item, index) => {
            const Icon = item.icon;
            const selected = item.id === scenario;
            return (
              <button
                key={item.id}
                ref={(el) => {
                  tabRefs.current[index] = el;
                }}
                type="button"
                role="tab"
                id={`welcome-scenario-tab-${item.id}`}
                aria-selected={selected}
                aria-controls={`welcome-scenario-panel-${item.id}`}
                tabIndex={selected ? 0 : -1}
                className={`${styles.scenarioTab}${
                  selected ? ` ${styles.scenarioTabActive}` : ""
                }`}
                onClick={() => setScenario(item.id)}
                onKeyDown={(event) => handleTabKeyDown(event, index)}
              >
                <Icon size={15} strokeWidth={1.9} aria-hidden />
                {t(item.labelKey)}
              </button>
            );
          })}
        </div>

        <div className={styles.scenarioPanels}>
          {SCENARIOS.map((item) => (
            <div
              key={item.id}
              role="tabpanel"
              id={`welcome-scenario-panel-${item.id}`}
              aria-labelledby={`welcome-scenario-tab-${item.id}`}
              hidden={item.id !== scenario}
              className={styles.scenarioPanel}
            >
              {item.tasks.map((task) => (
                <button
                  key={task.labelKey}
                  type="button"
                  className={styles.scenarioChip}
                  onClick={() =>
                    onPromptClick(t(task.promptKey), { prefill: true })
                  }
                >
                  {t(task.labelKey)}
                </button>
              ))}
            </div>
          ))}
        </div>

        {isDefaultScenario && quickCards.length > 0 && (
          <WelcomeQuickCards
            cards={cards}
            showToggle={showToggle}
            expanded={expanded}
            onToggle={() => setExpanded((prev) => !prev)}
            onPromptClick={onPromptClick}
            sectionTitleRef={sectionTitleRef}
          />
        )}
      </div>

      {quickCards.length > 0 && <WelcomeQuickCardProbe probeRef={probeRef} />}
    </div>
  );
}
