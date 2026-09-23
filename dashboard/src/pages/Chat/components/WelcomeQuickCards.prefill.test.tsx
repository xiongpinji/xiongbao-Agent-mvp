import { createRef } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import WelcomeQuickCards from "./WelcomeQuickCards";

vi.mock("../../Experts/components/iconForName", () => ({
  ExpertIcon: () => null,
}));

describe("WelcomeQuickCards", () => {
  it("prefills an ordinary quick card without sending a task", async () => {
    const onPromptClick = vi.fn();
    render(
      <WelcomeQuickCards
        cards={[
          {
            title: "整理资料",
            description: "归纳文件",
            prompt: "请整理这些资料",
            color: "#D4A35A",
          },
        ]}
        showToggle={false}
        expanded={false}
        onToggle={vi.fn()}
        onPromptClick={onPromptClick}
        sectionTitleRef={createRef<HTMLSpanElement>()}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /整理资料/ }));
    expect(onPromptClick).toHaveBeenCalledWith("请整理这些资料", {
      prefill: true,
    });
  });
});
