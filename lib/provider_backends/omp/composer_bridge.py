"""OMP-only native editor bridge embedded in the managed completion extension."""

IMPORTS = '''import { createServer, createConnection } from "node:net";
import { chmodSync, lstatSync, unlinkSync } from "node:fs";
'''

BRIDGE = r'''
  let editorContext: any = null;
  let editorServer: ReturnType<typeof createServer> | null = null;
  const editorPath = process.env.CCB_OMP_COMPOSER_SOCKET || "";
  let modelControlBusy = false;
  const modelAttempts = new Map<string, { signature: string, result: Promise<any> }>();
  const modelSnapshot = (ctx: any) => {
    const available = ctx.modelRegistry?.getAvailable?.();
    if (!Array.isArray(available) || available.length > 4096)
      return { error: "OMP model registry unavailable", status_code: 503 };
    // Matches pi-catalog/model-thinking.getSupportedEfforts: a reasoning
    // model without baked thinking metadata has no controllable effort ladder.
    const efforts = (model: any) => model?.reasoning
      ? ["off", ...(Array.isArray(model.thinking?.efforts) ? model.thinking.efforts : [])].filter((value: any) =>
          ["off", "minimal", "low", "medium", "high", "xhigh", "max"].includes(value)) : ["off"];
    const model = ctx.model ? { provider: String(ctx.model.provider), id: String(ctx.model.id) } : null;
    const thinking = pi.getThinkingLevel?.() ?? "off";
    const entries = ctx.sessionManager?.getEntries?.() ?? [];
    const lastChange = entries.findLast((entry: any) => entry.type === "model_change" || entry.type === "thinking_level_change");
    return {
      runtime_instance_id: runtimeInstanceId,
      session_id: String(ctx.sessionManager?.getSessionId?.() || ""),
      model, thinking_level: thinking,
      model_revision: Buffer.from(JSON.stringify([runtimeInstanceId, ctx.sessionManager?.getSessionId?.(), lastChange?.id ?? null, model, thinking])).toString("base64url"),
      models: available.map((item: any) => ({ provider: String(item.provider), id: String(item.id),
        name: String(item.name || item.id), reasoning_levels: efforts(item) })),
    };
  };
  const editorReply = async (request: any) => {
    const ctx = editorContext;
    const sessionId = String(ctx?.sessionManager?.getSessionId?.() || "");
    const base = { runtime_instance_id: runtimeInstanceId, session_id: sessionId, pid: process.pid, leaf_id: ctx?.sessionManager?.getLeafId?.() ?? null };
    if (request?.actor !== actor || request?.launch_session_id !== launchSessionId ||
        !ctx?.hasUI || typeof ctx.ui?.getEditorText !== "function" ||
        typeof ctx.ui?.setEditorText !== "function" || typeof ctx.isIdle !== "function") {
      return { ...base, state: "unknown", reason: "editor_binding_unavailable" };
    }
    if (request.operation === "ancestry") {
      const entries = ctx.sessionManager?.getEntries?.();
      if (!Array.isArray(entries) || entries.length > 16384)
        return { ...base, error: "OMP ancestry exceeds mobile budget", status_code: 503 };
      // Collab deliberately omits custom metadata records. Export identities
      // only, never their private contents, so active parent chains remain exact.
      return { ...base, parent_links: entries.map((entry: any) => [entry.id, entry.parentId ?? null]) };
    }
    if (request.operation === "models") return { ...base, ...modelSnapshot(ctx) };
    if (request.operation === "set_model") {
      const current = modelSnapshot(ctx);
      const signature = JSON.stringify([request.model, request.thinking, request.session_id,
        request.runtime_instance_id, request.expected_model, request.expected_thinking, request.expected_model_revision]);
      const key = request.idempotency_key;
      if (typeof key !== "string" || !key || key.length > 256)
        return { ...base, error: "Invalid OMP model request", status_code: 409 };
      const previous = modelAttempts.get(key);
      if (previous) return previous.signature === signature ? await previous.result
        : { ...base, error: "OMP model idempotency conflict", status_code: 409 };
      if (current.error) return { ...base, ...current };
      if (modelControlBusy || modelAttempts.size >= 256 || !ctx.isIdle() || ctx.hasQueuedMessages?.() || ctx.ui.getEditorText() !== "")
        return { ...base, error: "OMP owner busy or editor draft pending", status_code: 409 };
      if (request.runtime_instance_id !== runtimeInstanceId || request.session_id !== sessionId ||
          request.expected_model?.provider !== current.model?.provider || request.expected_model?.id !== current.model?.id ||
          request.expected_thinking !== current.thinking_level ||
          typeof request.expected_model_revision !== "string" || request.expected_model_revision !== current.model_revision)
        return { ...base, error: "OMP model state changed; refresh", status_code: 409 };
      const target = ctx.modelRegistry.getAvailable().find((item: any) =>
        item.provider === request.model?.provider && item.id === request.model?.id);
      const row = current.models.find((item: any) => item.provider === target?.provider && item.id === target?.id);
      if (!target || (request.thinking != null && !row.reasoning_levels.includes(request.thinking)))
        return { ...base, error: "OMP model or reasoning level unavailable", status_code: 409 };
      modelControlBusy = true;
      const result = Promise.resolve().then(async () => {
        try {
          const fresh = modelSnapshot(ctx);
          if (editorContext !== ctx || ctx.sessionManager?.getSessionId?.() !== sessionId ||
              !ctx.isIdle() || ctx.hasQueuedMessages?.() || ctx.ui.getEditorText() !== "" ||
              fresh.model_revision !== request.expected_model_revision)
            return { ...base, error: "OMP model state changed; refresh", status_code: 409 };
          // Only the documented native model APIs; no command or model prompt.
          if (!await pi.setModel(target)) return { ...base, error: "OMP model authentication unavailable", status_code: 409 };
          if (editorContext !== ctx || ctx.sessionManager?.getSessionId?.() !== sessionId || !ctx.isIdle() ||
              ctx.hasQueuedMessages?.() || ctx.ui.getEditorText() !== "" ||
              ctx.model?.provider !== target.provider || ctx.model?.id !== target.id)
            return { ...base, error: "OMP model change raced owner; refresh", status_code: 502 };
          if (request.thinking != null) pi.setThinkingLevel(request.thinking);
          return { ...base, ...modelSnapshot(ctx) };
        } catch {
          return { ...base, error: "OMP model change unknown; refresh without replay", status_code: 502 };
        } finally { modelControlBusy = false; }
      });
      modelAttempts.set(key, { signature, result });
      return await result;
    }
    if (!ctx.isIdle()) return { ...base, state: "unknown", reason: "provider_busy" };
    if (request.operation === "clear") {
      if (request.runtime_instance_id !== runtimeInstanceId || request.session_id !== sessionId)
        return { ...base, state: "unknown", reason: "editor_binding_changed" };
      ctx.ui.setEditorText("");
    } else if (request.operation !== "inspect") {
      return { ...base, state: "unknown", reason: "unsupported_operation" };
    }
    // Never expose draft contents, even to the CCB daemon or its logs.
    return { ...base, state: ctx.ui.getEditorText() === "" ? "empty" : "nonempty" };
  };
  pi.on("session_start", async (_event: any, ctx: any) => {
    editorContext = ctx;
    if (!editorPath || editorServer) return;
    // A killed process leaves its socket behind. Only remove an unchanged,
    // same-owner socket that explicitly refuses connections; never displace
    // a live bridge or a file/symlink at the configured path.
    try {
      const before = lstatSync(editorPath);
      if (!before.isSocket() || before.uid !== process.getuid?.()) return;
      const stale = await new Promise<boolean>((resolve) => {
        const probe = createConnection(editorPath);
        probe.setTimeout(500, () => { probe.destroy(); resolve(false); });
        probe.once("connect", () => { probe.destroy(); resolve(false); });
        // Bun 1.3 reports ENOENT for an existing abandoned Unix socket,
        // where Node reports ECONNREFUSED. Recheck inode/owner below before
        // unlinking; neither error permits replacing a live or changed path.
        probe.once("error", (error: any) => {
          probe.destroy();
          resolve(error.code === "ECONNREFUSED" || error.code === "ENOENT");
        });
      });
      if (!stale) return;
      const after = lstatSync(editorPath);
      if (!after.isSocket() || after.uid !== before.uid ||
          after.ino !== before.ino || after.dev !== before.dev) return;
      unlinkSync(editorPath);
    } catch (error: any) {
      if (error.code !== "ENOENT") return;
    }
    const server = createServer((client) => {
      let input = "";
      let handled = false;
      client.setTimeout(1000, () => client.destroy());
      client.on("error", () => {});
      client.on("data", async (chunk) => {
        if (handled) return;
        input += chunk.toString("utf8");
        if (input.length > 4096) { handled = true; client.destroy(); return; }
        if (!input.includes("\n")) return;
        handled = true;
        try { client.end(JSON.stringify(await editorReply(JSON.parse(input))) + "\n"); }
        catch { client.end('{"state":"unknown","reason":"editor_error"}\n'); }
      });
    });
    server.on("error", () => {});
    server.listen(editorPath, () => { try { chmodSync(editorPath, 0o600); } catch {} });
    server.unref();
    editorServer = server;
  });
  pi.on("session_switch", async (_event: any, ctx: any) => { editorContext = ctx; });
  pi.on("session_shutdown", async () => { editorContext = null; editorServer?.close(); });
'''
