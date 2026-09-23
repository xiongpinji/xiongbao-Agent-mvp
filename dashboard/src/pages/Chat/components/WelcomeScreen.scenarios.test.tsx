import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import WelcomeScreen from "./WelcomeScreen";

function renderWelcome(
  onPromptClick: (text: string, options?: { prefill?: boolean }) => void,
) {
  return render(
    <WelcomeScreen quickCards={[]} onPromptClick={onPromptClick} />,
  );
}

describe("WelcomeScreen task scenarios", () => {
  it("switches suggestions and only prefills the selected task", async () => {
    const user = userEvent.setup();
    const onPromptClick = vi.fn();
    renderWelcome(onPromptClick);

    expect(
      screen
        .getByRole("tab", { name: "chatWelcome.scenarioOffice" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "chatWelcome.scenarioOfficeTask1" }),
    ).toBeTruthy();
    expect(onPromptClick).not.toHaveBeenCalled();

    await user.click(
      screen.getByRole("tab", { name: "chatWelcome.scenarioCode" }),
    );
    expect(
      screen.queryByRole("button", { name: "chatWelcome.scenarioOfficeTask1" }),
    ).toBeNull();
    await user.click(
      screen.getByRole("button", { name: "chatWelcome.scenarioCodeTask2" }),
    );
    expect(onPromptClick).toHaveBeenCalledOnce();
    expect(onPromptClick).toHaveBeenCalledWith(
      "chatWelcome.scenarioCodePrompt2",
      { prefill: true },
    );
  });

  it("supports arrow, Home and End navigation across the scenario tabs", async () => {
    const user = userEvent.setup();
    renderWelcome(vi.fn());

    const office = screen.getByRole("tab", {
      name: "chatWelcome.scenarioOffice",
    });
    office.focus();
    await user.keyboard("{ArrowRight}");
    expect(
      screen
        .getByRole("tab", { name: "chatWelcome.scenarioCode" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    await user.keyboard("{End}");
    expect(
      screen
        .getByRole("tab", { name: "chatWelcome.scenarioDesign" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(
      screen.getByRole("button", { name: "chatWelcome.scenarioDesignTask4" }),
    ).toBeTruthy();
    await user.keyboard("{Home}");
    expect(office.getAttribute("aria-selected")).toBe("true");
  });
});
