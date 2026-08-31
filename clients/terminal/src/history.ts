import { decode, object, string, type WireObject } from "./protocol.js";
import { SessionState } from "./session.js";

export interface HistoryPort { request(operation: string, parameters?: WireObject): Promise<WireObject>; }
export interface HistoryPage { state: SessionState; snapshot: WireObject; older?: string; turns: number; }

/** Reuse the live reducer on a bounded page; no task starts, tools, or acknowledgements. */
export async function loadHistory(port: HistoryPort, thread: string, signal: AbortSignal, before?: string): Promise<HistoryPage> {
  signal.throwIfAborted();
  const snapshot = await port.request("get", {thread_id: thread, ...(before ? {before_run_id: before} : {})});
  signal.throwIfAborted();
  if (snapshot.thread_id !== thread || !Array.isArray(snapshot.runs)) throw new Error("Invalid conversation history");
  const state = new SessionState(); state.threadId = thread;
  const runs = snapshot.runs.map(object).reverse();
  for (const run of runs) {
    signal.throwIfAborted();
    const id = string(run.run_id);
    state.begin(); state.runId = id;
    let highWater: number | undefined;
    do {
      const page = await port.request("events", {thread_id: thread, run_id: id, after_sequence: state.cursor,
        ...(highWater === undefined ? {} : {high_water_sequence: highWater})});
      signal.throwIfAborted();
      if (page.thread_id !== thread || object(page.run).run_id !== id || !Array.isArray(page.events)
        || !Number.isSafeInteger(page.high_water_sequence) || Number(page.high_water_sequence) < state.cursor
        || (highWater !== undefined && highWater !== page.high_water_sequence)) throw new Error("Invalid transcript snapshot");
      if (highWater === undefined) state.user(`${id}:user`, string(object(page.run).input));
      highWater = Number(page.high_water_sequence);
      const previous = state.cursor;
      for (const event of page.events) {
        if (state.envelope(decode(JSON.stringify(event))) !== "applied") throw new Error("Transcript replay has a sequence gap or duplicate");
      }
      if (state.cursor > highWater) throw new Error("Transcript exceeds its snapshot boundary");
      if (page.next_after_sequence === null && state.cursor === highWater) break;
      if (page.next_after_sequence !== state.cursor || state.cursor <= previous) throw new Error("Transcript cursor did not advance");
    } while (true);
  }
  return {state, snapshot, turns: runs.length,
    older: typeof snapshot.next_before_run_id === "string" ? snapshot.next_before_run_id : undefined};
}
