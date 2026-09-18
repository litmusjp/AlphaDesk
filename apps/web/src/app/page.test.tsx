import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import WorkspaceLanding from "./page";

describe("connected paper landing", () => {
  it("offers only invite-based connected paper access", () => {
    render(<WorkspaceLanding />);
    expect(screen.getByRole("heading", { name: "Connected Paper Workspace" })).toBeInTheDocument();
    expect(screen.getByText("Invite-only access")).toBeInTheDocument();
    expect(screen.queryByText(/Public Demo Workspace/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/Choose your workspace/i)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Sign in with invitation/i })).toHaveAttribute("href", "/login");
  });
});
