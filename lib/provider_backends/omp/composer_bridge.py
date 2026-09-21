"""OMP-only native editor bridge embedded in the managed completion extension."""

IMPORTS = '''import { createServer, createConnection } from "node:net";
import { chmodSync, lstatSync, unlinkSync } from "node:fs";
'''

BRIDGE = r'''
  let editorContext: any = null;
  let editorServer: ReturnType<typeof createServer> | null = null;
  const editorPath = process.env.CCB_OMP_COMPOSER_SOCKET || "";
  const editorReply = (request: any) => {
    const ctx = editorContext;
    const sessionId = String(ctx?.sessionManager?.getSessionId?.() || "");
    const base = { runtime_instance_id: runtimeInstanceId, session_id: sessionId };
    if (request?.actor !== actor || request?.launch_session_id !== launchSessionId ||
        !ctx?.hasUI || typeof ctx.ui?.getEditorText !== "function" ||
        typeof ctx.ui?.setEditorText !== "function" || typeof ctx.isIdle !== "function") {
      return { ...base, state: "unknown", reason: "editor_binding_unavailable" };
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
        probe.once("error", (error: any) => resolve(error.code === "ECONNREFUSED"));
      });
      if (!stale) return;
      const after = lstatSync(editorPath);
      if (after.ino !== before.ino || after.dev !== before.dev) return;
      unlinkSync(editorPath);
    } catch (error: any) {
      if (error.code !== "ENOENT") return;
    }
    const server = createServer((client) => {
      let input = "";
      let handled = false;
      client.setTimeout(1000, () => client.destroy());
      client.on("error", () => {});
      client.on("data", (chunk) => {
        if (handled) return;
        input += chunk.toString("utf8");
        if (input.length > 4096) { handled = true; client.destroy(); return; }
        if (!input.includes("\n")) return;
        handled = true;
        try { client.end(JSON.stringify(editorReply(JSON.parse(input))) + "\n"); }
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
