// @vitest-environment jsdom

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownRenderer } from "./markdown-renderer";

describe("MarkdownRenderer", () => {
  it("renders inline and block LaTeX formulas", () => {
    const { container } = render(<MarkdownRenderer content={"Inline $E=mc^2$.\n\n$$\n\\int_0^1 x^2 dx\n$$"} />);

    expect(container.querySelector(".katex")).not.toBeNull();
    expect(container.querySelector(".katex-display")).not.toBeNull();
    expect(container.textContent).toContain("E");
  });
});
