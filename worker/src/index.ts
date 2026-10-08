/**
 * Cloudflare Worker vault for quick secret retrieval.
 *
 * Minimal design: namespace-based isolation, bearer token auth, optional
 * analytics. Optimized for fast reads with no mutating side effects.
 */

/** Bindings and variables provided by the Cloudflare runtime. */
export interface Env {
  /** KV namespace holding secrets. */
  SECRETS: KVNamespace;
  /** Bearer token for authentication (set in wrangler.toml [vars]). */
  VAULT_BEARER_TOKEN: string;
  /** Optional: Enable analytics tracking (set to "true" to enable). */
  VAULT_ENABLE_ANALYTICS?: string;
}

/** A stored secret record. */
interface Secret {
  namespace: string;
  key: string;
  value: string;
  updated_at: string;
}

/** List response envelope. */
interface SecretList {
  secrets: Secret[];
  count: number;
}



const VERSION = "0.2.0";
const SLUG_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/;

/**
 * Build a JSON response with explicit status code.
 *
 * @param body - JSON-serializable response body.
 * @param status - HTTP status code.
 * @returns The encoded response.
 */
function json(body: unknown, status: number): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Verify bearer token authentication.
 *
 * @param request - Incoming HTTP request.
 * @param env - Worker environment bindings.
 * @returns `true` if the request carries a valid bearer token.
 */
function isAuthorized(request: Request, env: Env): boolean {
  const auth = request.headers.get("Authorization");
  if (!auth || !env.VAULT_BEARER_TOKEN) {
    return false;
  }
  const expected = `Bearer ${env.VAULT_BEARER_TOKEN}`;
  return auth === expected;
}

/**
 * Validate namespace or key format.
 *
 * @param value - Candidate slug.
 * @returns `true` if the value matches the slug pattern.
 */
function isValidSlug(value: unknown): value is string {
  return typeof value === "string" && SLUG_PATTERN.test(value);
}

/**
 * Build the KV key for a secret.
 *
 * @param namespace - Owning namespace.
 * @param key - Secret key.
 * @returns The KV storage key.
 */
function secretKey(namespace: string, key: string): string {
  return `${namespace}:${key}`;
}

/**
 * Record a secret access event for analytics (non-blocking).
 *
 * @param env - Worker environment bindings.
 * @param namespace - Namespace accessed.
 * @param key - Secret key accessed.
 */
function recordAccess(env: Env, namespace: string, key: string): void {
  if (env.VAULT_ENABLE_ANALYTICS !== "true") {
    return;
  }
  const analyticsKey = `analytics:${namespace}:${key}:${new Date().toISOString().slice(0, 10)}`;
  env.SECRETS.get(analyticsKey, "text").then((current) => {
    const count = current ? parseInt(current, 10) + 1 : 1;
    env.SECRETS.put(analyticsKey, count.toString(), { expirationTtl: 2592000 }); // 30 days
  }).catch(() => {
    // Silently ignore analytics failures
  });
}

/**
 * Route an incoming request to the appropriate handler.
 *
 * @param request - Incoming HTTP request.
 * @param env - Worker bindings and variables.
 * @returns The HTTP response.
 */
export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const method = request.method.toUpperCase();

    // Health check (unauthenticated)
    if (method === "GET" && url.pathname === "/health") {
      return json({ status: "ok", version: VERSION }, 200);
    }

    // Authentication required for all other endpoints
    if (!isAuthorized(request, env)) {
      return json({ detail: "Unauthorized" }, 401);
    }

    const segments = url.pathname.split("/").filter((s) => s.length > 0);

    // GET /secrets?namespace=<ns> - List secrets
    if (method === "GET" && segments.length === 1 && segments[0] === "secrets") {
      const namespace = url.searchParams.get("namespace");
      if (namespace && !isValidSlug(namespace)) {
        return json({ detail: "Invalid namespace format" }, 422);
      }

      const prefix = namespace ? `${namespace}:` : "";
      const keys: string[] = [];
      let cursor: string | undefined;

      do {
        const page = await env.SECRETS.list({ prefix, cursor });
        for (const item of page.keys) {
          if (!item.name.startsWith("analytics:")) {
            keys.push(item.name);
          }
        }
        cursor = page.list_complete ? undefined : page.cursor;
      } while (cursor);

      keys.sort();
      const secrets: Secret[] = [];

      for (let i = 0; i < keys.length; i += 100) {
        const chunk = keys.slice(i, i + 100);
        const values = await env.SECRETS.get<Secret>(chunk, "json");
        for (const secret of values.values()) {
          if (secret !== null) {
            secrets.push(secret);
          }
        }
      }

      return json({ secrets, count: secrets.length } as SecretList, 200);
    }

    // GET /secrets/<namespace>/<key> - Get secret
    if (method === "GET" && segments.length === 3 && segments[0] === "secrets") {
      const namespace = decodeURIComponent(segments[1] ?? "");
      const key = decodeURIComponent(segments[2] ?? "");

      if (!isValidSlug(namespace) || !isValidSlug(key)) {
        return json({ detail: "Invalid namespace or key format" }, 422);
      }

      const stored = await env.SECRETS.get<Secret>(secretKey(namespace, key), "json");
      if (!stored) {
        return json({ detail: `Secret '${namespace}/${key}' not found` }, 404);
      }

      recordAccess(env, namespace, key);
      return json(stored, 200);
    }

    // PUT /secrets/<namespace>/<key> - Create or update secret
    if (method === "PUT" && segments.length === 3 && segments[0] === "secrets") {
      const namespace = decodeURIComponent(segments[1] ?? "");
      const key = decodeURIComponent(segments[2] ?? "");

      if (!isValidSlug(namespace) || !isValidSlug(key)) {
        return json({ detail: "Invalid namespace or key format" }, 422);
      }

      let payload: { value?: string };
      try {
        payload = await request.json();
      } catch {
        return json({ detail: "Invalid JSON body" }, 422);
      }

      if (!payload.value || typeof payload.value !== "string" || payload.value.trim().length === 0) {
        return json({ detail: "Field 'value' is required and must be non-empty" }, 422);
      }

      const secret: Secret = {
        namespace,
        key,
        value: payload.value,
        updated_at: new Date().toISOString(),
      };

      await env.SECRETS.put(secretKey(namespace, key), JSON.stringify(secret));
      return json(secret, 200);
    }

    // DELETE /secrets/<namespace>/<key> - Delete secret
    if (method === "DELETE" && segments.length === 3 && segments[0] === "secrets") {
      const namespace = decodeURIComponent(segments[1] ?? "");
      const key = decodeURIComponent(segments[2] ?? "");

      if (!isValidSlug(namespace) || !isValidSlug(key)) {
        return json({ detail: "Invalid namespace or key format" }, 422);
      }

      const stored = await env.SECRETS.get(secretKey(namespace, key));
      if (!stored) {
        return json({ detail: `Secret '${namespace}/${key}' not found` }, 404);
      }

      await env.SECRETS.delete(secretKey(namespace, key));
      return new Response(null, { status: 204 });
    }

    return json({ detail: "Not Found" }, 404);
  },
};
