"""OMP-only native editor and reply bridge in the managed completion extension.

Reply ingress v1 uses actor/launch-bound ``reply_capabilities`` to discover the
runtime/session binding. ``reply_deliver`` accepts delivery_id and body;
``reply_status`` accepts delivery_id plus optional body/body_sha256. Both require
that exact runtime/session. States are pending, received, missing, or unknown;
only native ccb-reply custom_message entries also verified in the session JSONL
authorize a received receipt with delivery_id/body_sha256. Owner-private fsynced
intents hold ambiguous attempts across restarts, never replaying them.
Admission and native processing have separate lifetimes, so busy owners can
admit multiple aside messages without interruption.
"""

IMPORTS = '''import { createServer, createConnection } from "node:net";
import { chmodSync, lstatSync, unlinkSync } from "node:fs";
import { createHash as replyCreateHash } from "node:crypto";
import { open as replyOpen } from "node:fs/promises";
import { openSync as replyOpenSync, closeSync as replyCloseSync, fstatSync as replyFstatSync,
  readFileSync as replyReadSync, writeSync as replyWriteSync, fsyncSync as replyFsyncSync,
  constants as replyFsConstants } from "node:fs";
import { dirname as replyDirname } from "node:path";
'''

BRIDGE = r'''
  let editorContext: any = null;
  let editorServer: ReturnType<typeof createServer> | null = null;
  const editorPath = process.env.CCB_OMP_COMPOSER_SOCKET || "";
  let modelControlBusy = false;
  const modelAttempts = new Map<string, { signature: string, result: Promise<any> }>();
  // Admission is serialized, not native processing: sendMessage may resolve
  // only after an entire business turn. Never put that promise on this tail.
  let replyAdmission = Promise.resolve();
  const replyAttempts = new Map<string, { hash: string, failed: boolean, processing: Promise<void> | null }>();
  const replyHash = (body: string) => replyCreateHash("sha256").update(body, "utf8").digest("hex");
  const replyIntentPath = (typeof eventPath === "string" && eventPath ? eventPath : editorPath) + ".reply-intents.jsonl";
  const replyJournal = (record?: any) => {
    const fd = replyOpenSync(replyIntentPath, replyFsConstants.O_RDWR | replyFsConstants.O_APPEND |
      replyFsConstants.O_CREAT | replyFsConstants.O_NOFOLLOW, 0o600);
    try {
      const stat = replyFstatSync(fd);
      if (!stat.isFile() || stat.uid !== process.getuid?.() || (stat.mode & 0o077) || stat.size > 1024 * 1024)
        throw Error("unsafe or full reply intent journal");
      const text = replyReadSync(fd, "utf8");
      if (text && !text.endsWith("\n")) throw Error("partial reply intent journal");
      const records = text.split("\n").filter(Boolean).map((line: string) => {
        const value = JSON.parse(line);
        if (typeof value.actor !== "string" || typeof value.launch_session_id !== "string" ||
            typeof value.session_id !== "string" || typeof value.delivery_id !== "string" ||
            !/^[a-f0-9]{64}$/.test(value.body_sha256)) throw Error("invalid reply intent");
        return value;
      });
      if (record) {
        const bytes = Buffer.from(JSON.stringify(record) + "\n");
        if (stat.size + bytes.length > 1024 * 1024) throw Error("full reply intent journal");
        let offset = 0;
        while (offset < bytes.length) {
          const written = replyWriteSync(fd, bytes, offset, bytes.length - offset);
          if (!written) throw Error("short reply intent write");
          offset += written;
        }
        replyFsyncSync(fd);
        const directory = replyOpenSync(replyDirname(replyIntentPath), replyFsConstants.O_RDONLY | replyFsConstants.O_DIRECTORY);
        try { replyFsyncSync(directory); } finally { replyCloseSync(directory); }
      }
      return records;
    } finally { replyCloseSync(fd); }
  };
  // Read only the bounded JSONL tail, never trust an in-memory append as a
  // receipt. Older records outside this budget remain held, not replayed.
  const replyOnDisk = async (path: string, matches: any[]) => {
    let file: any;
    try {
      file = await replyOpen(path, "r");
      const size = (await file.stat()).size;
      const length = Math.min(size, 4 * 1024 * 1024);
      const start = size - length;
      const buffer = Buffer.allocUnsafe(length);
      const { bytesRead } = await file.read(buffer, 0, length, start);
      const text = buffer.subarray(0, bytesRead).toString("utf8");
      const lines = text.split("\n");
      if (start > 0) lines.shift(); // May start in a record or UTF-8 code point.
      lines.pop(); // Only newline-terminated records are persisted receipts.
      const found = lines.some((line: string) => {
        let entry: any;
        try { entry = JSON.parse(line); } catch { return false; }
        return entry.type === "custom_message" && entry.customType === "ccb-reply" &&
          matches.some((match: any) => entry.id === match.id &&
            entry.details?.delivery_id === match.details.delivery_id &&
            entry.details?.body_sha256 === match.details.body_sha256 &&
            typeof entry.content === "string" && replyHash(entry.content) === match.details.body_sha256);
      });
      if (found) await file.sync();
      return found;
    } catch (error: any) {
      if (error.code === "ENOENT") return false;
      throw error;
    } finally { await file?.close(); }
  };
  const replyOperation = async (request: any) => {
    const ctx = editorContext;
    const sessionId = String(ctx?.sessionManager?.getSessionId?.() || "");
    const base = { runtime_instance_id: runtimeInstanceId, session_id: sessionId };
    const unknown = (reason: string, status_code = 409) => ({ ...base, state: "unknown", reason, status_code });
    if (request.actor !== actor || request.launch_session_id !== launchSessionId || !ctx || !sessionId)
      return unknown("reply_binding_unavailable");
    if (request.operation === "reply_capabilities") {
      if (typeof pi.sendMessage !== "function" || typeof ctx.sessionManager?.getEntries !== "function")
        return unknown("reply_ingress_unavailable", 503);
      return { ...base, reply_ingress_version: 1 };
    }
    if (request.runtime_instance_id !== runtimeInstanceId || request.session_id !== sessionId)
      return unknown("reply_binding_changed");
    const id = request.delivery_id;
    if (typeof id !== "string" || !id || id.length > 256)
      return unknown("invalid_delivery_id", 400);
    const delivering = request.operation === "reply_deliver";
    if ((delivering || request.body !== undefined) &&
        (typeof request.body !== "string" || Buffer.byteLength(request.body, "utf8") > 256 * 1024))
      return unknown("invalid_reply_body", 400);
    const hash = typeof request.body === "string" ? replyHash(request.body) : request.body_sha256;
    if (hash !== undefined && (typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash)))
      return unknown("invalid_reply_hash", 400);
    if (request.body_sha256 !== undefined && request.body_sha256 !== hash)
      return unknown("reply_body_conflict");
    // Session identity is part of the key. Never use another session's receipt
    // or replay a held attempt, including after sendMessage rejects.
    const key = JSON.stringify([sessionId, id]);
    const previous = replyAttempts.get(key);
    let intents: any[];
    try { intents = replyJournal().filter((record: any) => record.actor === actor &&
      record.launch_session_id === launchSessionId && record.session_id === sessionId && record.delivery_id === id); }
    catch { return unknown("reply_intent_unknown", 503); }
    if (intents.some((record: any) => hash !== undefined && record.body_sha256 !== hash))
      return unknown("reply_body_conflict");
    let entries: any;
    try { entries = ctx.sessionManager?.getEntries?.(); }
    catch { return unknown("reply_entries_unavailable", 503); }
    if (!Array.isArray(entries)) return unknown("reply_entries_unavailable", 503);
    const matches = entries.filter((entry: any) => entry.type === "custom_message" &&
      entry.customType === "ccb-reply" && entry.details?.delivery_id === id);
    if ((previous && hash !== undefined && previous.hash !== hash) ||
        matches.some((entry: any) => (hash !== undefined && entry.details?.body_sha256 !== hash) ||
          typeof entry.content !== "string" || replyHash(entry.content) !== entry.details?.body_sha256))
      return unknown("reply_body_conflict");
    if (matches.length) {
      const persistedHash = matches[0].details.body_sha256;
      if (matches.some((entry: any) => entry.details.body_sha256 !== persistedHash) ||
          intents.some((record: any) => record.body_sha256 !== persistedHash) ||
          (previous && previous.hash !== persistedHash)) return unknown("reply_body_conflict");
      const sessionFile = ctx.sessionManager?.getSessionFile?.();
      if (typeof sessionFile !== "string" || !sessionFile) return unknown("reply_persistence_unavailable", 503);
      let persisted = false;
      try {
        if (typeof ctx.sessionManager.flush === "function") await ctx.sessionManager.flush();
        persisted = await replyOnDisk(sessionFile, matches);
      } catch { return unknown("reply_persistence_unknown", 502); }
      if (editorContext !== ctx || String(ctx.sessionManager?.getSessionId?.() || "") !== sessionId ||
          ctx.sessionManager?.getSessionFile?.() !== sessionFile)
        return { runtime_instance_id: runtimeInstanceId,
          session_id: String(editorContext?.sessionManager?.getSessionId?.() || ""),
          state: "unknown", reason: "reply_binding_changed", status_code: 409 };
      if (!persisted) return { ...base, state: "pending", delivery_id: id, body_sha256: persistedHash };
      return { ...base, state: "received", delivery_id: id, body_sha256: persistedHash };
    }
    if (previous) return { ...base, state: previous.failed ? "unknown" : "pending",
      delivery_id: id, body_sha256: previous.hash,
      ...(previous.failed ? { reason: "reply_processing_unknown" } : {}) };
    if (intents.length) return { ...base, state: "unknown", reason: "reply_processing_unknown",
      delivery_id: id, body_sha256: intents[0].body_sha256 };
    if (!delivering) return { ...base, state: "missing", delivery_id: id };
    // Never evict unresolved/failed admissions: eviction would permit replay.
    // Receipted attempts may be evicted because native entries deduplicate them.
    if (replyAttempts.size >= 256) {
      for (const [oldKey, attempt] of replyAttempts) {
        const [oldSession, oldId] = JSON.parse(oldKey);
        if (oldSession === sessionId && !attempt.processing && entries.some((entry: any) =>
            entry.type === "custom_message" && entry.customType === "ccb-reply" &&
            entry.details?.delivery_id === oldId && entry.details?.body_sha256 === attempt.hash)) {
          replyAttempts.delete(oldKey);
          break;
        }
      }
    }
    if (replyAttempts.size >= 256) return unknown("reply_admission_full", 503);
    if (typeof pi.sendMessage !== "function") return unknown("reply_ingress_unavailable", 503);
    try { replyJournal({ actor, launch_session_id: launchSessionId, session_id: sessionId,
      delivery_id: id, body_sha256: hash }); }
    catch {
      replyAttempts.set(key, { hash: hash as string, failed: true, processing: null });
      return unknown("reply_intent_unknown", 503);
    }
    const attempt = { hash: hash as string, failed: false, processing: null as Promise<void> | null };
    replyAttempts.set(key, attempt);
    try {
      const processing = pi.sendMessage({ customType: "ccb-reply", content: request.body,
        display: true, details: { delivery_id: id, body_sha256: hash } },
        { deliverAs: "aside", triggerTurn: true });
      attempt.processing = Promise.resolve(processing).then(() => {}, () => { attempt.failed = true; })
        .finally(() => { attempt.processing = null; });
    } catch { attempt.failed = true; }
    return { ...base, state: attempt.failed ? "unknown" : "pending", delivery_id: id, body_sha256: hash,
      ...(attempt.failed ? { reason: "reply_processing_unknown" } : {}) };
  };
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
    if (["reply_capabilities", "reply_deliver", "reply_status"].includes(request?.operation)) {
      const result = replyAdmission.then(() => replyOperation(request));
      replyAdmission = result.then(() => {}, () => {});
      return await result;
    }
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
      const chunks: Buffer[] = [];
      let inputBytes = 0;
      let handled = false;
      client.setTimeout(1000, () => client.destroy());
      client.on("error", () => {});
      client.on("data", async (chunk) => {
        if (handled) return;
        chunks.push(chunk);
        inputBytes += chunk.length;
        if (inputBytes > 300 * 1024) { handled = true; client.destroy(); return; }
        if (!chunk.includes(10)) return;
        const input = Buffer.concat(chunks).toString("utf8");
        handled = true;
        try { client.end(JSON.stringify(await editorReply(JSON.parse(input))) + "\n"); }
        catch { client.end(JSON.stringify({ state: "unknown", reason: "editor_error",
          runtime_instance_id: runtimeInstanceId,
          session_id: String(editorContext?.sessionManager?.getSessionId?.() || "") }) + "\n"); }
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
