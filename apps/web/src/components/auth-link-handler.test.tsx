import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AuthLinkHandler } from "./auth-link-handler";

const getSession = vi.fn();
const setSession = vi.fn();
const updateUser = vi.fn();

vi.mock("@/lib/api", () => ({
  supabaseBrowser: () => ({ auth: { getSession, setSession, updateUser } }),
}));

describe("Supabase authentication links", () => {
  beforeEach(() => {
    window.history.replaceState({}, "", "/#access_token=test-token&refresh_token=test-refresh-token&type=invite");
    getSession.mockResolvedValue({ data: { session: { user: { email: "admin@litmus-jp.com" } } } });
    setSession.mockResolvedValue({ data: { session: { user: { email: "admin@litmus-jp.com" } } }, error: null });
    updateUser.mockResolvedValue({ error: null });
  });

  it("lets an invited session set a password", async () => {
    render(<AuthLinkHandler />);
    expect(await screen.findByRole("heading", { name: "Finish setting up your account" })).toBeInTheDocument();
    expect(setSession).toHaveBeenCalledWith({ access_token: "test-token", refresh_token: "test-refresh-token" });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse-battery" } });
    fireEvent.change(screen.getByLabelText("Confirm password"), { target: { value: "correct-horse-battery" } });
    fireEvent.click(screen.getByRole("button", { name: "Set password" }));
    await waitFor(() => expect(updateUser).toHaveBeenCalledWith({ password: "correct-horse-battery" }));
    expect(await screen.findByText("Password set. You can now sign in to AlphaDesk."));
  });
});
