import { describe, expect, it } from "vitest";
import { ApiError, makeClient, ok, setCsrf } from "./client";

function recorder(status = 200, body: unknown = { ok: true }) {
  const seen: Request[] = [];
  const fetchImpl = async (input: RequestInfo | URL) => {
    seen.push(input as Request);
    return new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": status < 400 ? "application/json" : "application/problem+json" },
    });
  };
  return { seen, client: makeClient("http://desk.test", fetchImpl as typeof fetch) };
}

describe("the API client", () => {
  it("sends the CSRF token on writes only", async () => {
    const { seen, client } = recorder();
    setCsrf("token-1");
    await client.GET("/api/v1/health");
    await client.POST("/api/v1/auth/logout", { body: null });
    expect(seen[0]?.headers.get("X-CSRF-Token")).toBeNull();
    expect(seen[1]?.headers.get("X-CSRF-Token")).toBe("token-1");
    setCsrf(null);
    await client.POST("/api/v1/auth/logout", { body: null });
    expect(seen[2]?.headers.get("X-CSRF-Token")).toBeNull();
  });

  it("raises the server's problem with its fields", async () => {
    const problem = {
      type: "about:blank",
      title: "Invalid input",
      status: 422,
      detail: "the request is not valid: see errors",
      errors: [
        { field: "coupon.barrier", message: "must be at most 3" },
        { field: "coupon.barrier", message: "is required" },
        { field: "options.n_paths", message: "too many" },
      ],
    };
    const { client } = recorder(422, problem);
    const error = await ok(client.GET("/api/v1/health")).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    const api = error as ApiError;
    expect(api.status).toBe(422);
    expect(api.message).toBe("the request is not valid: see errors");
    expect(api.fields()).toEqual({
      "coupon.barrier": "must be at most 3; is required",
      "options.n_paths": "too many",
    });
  });

  it("gives the data of a success", async () => {
    const { client } = recorder(200, { ok: true });
    await expect(ok(client.GET("/api/v1/health"))).resolves.toEqual({ ok: true });
  });
});
