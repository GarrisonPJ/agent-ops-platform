import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import LifecycleBadge from "./LifecycleBadge";

describe("LifecycleBadge", () => {
  it.each([
    ["queued", "Queued"],
    ["cancelling", "Cancelling"],
    ["timed_out", "Timed out"],
    ["validated", "Validated"],
  ] as const)("renders %s with a readable label", (status, label) => {
    render(<LifecycleBadge status={status} />);
    expect(screen.getByText(label)).toBeTruthy();
  });
});
