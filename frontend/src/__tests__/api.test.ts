import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api";

describe("api GET requests", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rejects non-2xx JSON responses instead of returning error bodies", async () => {
    const json = vi.fn().mockResolvedValue({ detail: "course not found" });
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 404,
      json,
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.course("missing/course")).rejects.toThrow(
      "request failed: 404",
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/courses/missing%2Fcourse");
    expect(json).not.toHaveBeenCalled();
  });

  it("returns decoded JSON for successful GET responses", async () => {
    const payload = { status: "ok", version: "0.5.0" };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: vi.fn().mockResolvedValue(payload),
      }),
    );

    await expect(api.health()).resolves.toEqual(payload);
  });
});
