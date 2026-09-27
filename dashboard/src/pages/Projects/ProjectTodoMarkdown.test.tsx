import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import ProjectTodoMarkdown from "./ProjectTodoMarkdown";

describe("ProjectTodoMarkdown", () => {
  it("renders only the approved formatting and HTTPS links", () => {
    render(
      <ProjectTodoMarkdown
        content={
          "# 标题\n\n**加粗** 和 [文档](https://example.com/path)\n\n- 第一项\n- 第二项"
        }
      />,
    );

    expect(screen.getByRole("heading", { name: "标题" })).toBeVisible();
    expect(screen.getByText("加粗").tagName).toBe("STRONG");
    expect(screen.getByRole("list")).toBeVisible();
    const link = screen.getByRole("link", { name: "文档" });
    expect(link).toHaveAttribute("href", "https://example.com/path");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("drops raw HTML, images and unsafe links without loading external content", () => {
    const { container } = render(
      <ProjectTodoMarkdown
        content={
          '<script>alert("x")</script>\n\n<img src="https://outside.example/raw.png">\n\n![远程图](https://outside.example/image.png)\n\n[危险](javascript:alert(1)) [数据](data:text/html;base64,PHNjcmlwdD4=) [邮件](mailto:test@example.com) [安全](http://example.com)'
        }
      />,
    );

    expect(container.querySelector("script,img,iframe")).toBeNull();
    expect(within(container).queryByRole("link", { name: "危险" })).toBeNull();
    expect(within(container).queryByRole("link", { name: "数据" })).toBeNull();
    expect(within(container).queryByRole("link", { name: "邮件" })).toBeNull();
    expect(screen.getByRole("link", { name: "安全" })).toHaveAttribute(
      "href",
      "http://example.com/",
    );
    expect(container.innerHTML).not.toContain("outside.example");
  });
});
