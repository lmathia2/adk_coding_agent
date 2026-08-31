/** Version-1 wire contract. No provider or harness implementation imports. */
export type WireObject = Record<string, unknown>;
export function object(value: unknown): WireObject {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("expected object");
  return value as WireObject;
}
export function string(value: unknown): string {
  if (typeof value !== "string") throw new Error("expected string");
  return value;
}
const required: Record<string, string[]> = {
  RUN_STARTED: ["runId", "threadId"], RUN_FINISHED: ["runId", "threadId"], RUN_ERROR: ["message"],
  TEXT_MESSAGE_START: ["messageId"], TEXT_MESSAGE_CONTENT: ["messageId", "delta"], TEXT_MESSAGE_END: ["messageId"],
  TOOL_CALL_START: ["toolCallId", "toolCallName"], TOOL_CALL_ARGS: ["toolCallId", "delta"],
  TOOL_CALL_END: ["toolCallId"], TOOL_CALL_RESULT: ["toolCallId", "content"],
  STATE_SNAPSHOT: [], STATE_DELTA: [], CUSTOM: ["name"], STEP_STARTED: ["stepName"], STEP_FINISHED: ["stepName"],
};
export function decode(text: string): WireObject {
  if (Buffer.byteLength(text) > 1_048_576) throw new Error("server frame exceeds limit");
  const message = object(JSON.parse(text));
  const type = string(message.type);
  if (message.protocol_version !== 1) throw new Error("unsupported protocol version");
  if (type === "event") {
    string(message.run_id);
    if (!Number.isSafeInteger(message.sequence) || Number(message.sequence) < 1) throw new Error("invalid sequence");
    const event = object(message.event);
    const fields = Object.hasOwn(required, string(event.type)) ? required[string(event.type)] : undefined;
    if (!fields) throw new Error("unknown event type");
    for (const field of fields) string(event[field]);
  } else if (type === "server.hello") {
    string(object(message.harness).display_name);
  } else if (type === "task.accepted") {
    for (const field of ["request_id", "thread_id", "run_id"]) string(message[field]);
  } else if (type === "error") {
    string(message.message);
  } else if (type === "control.result") {
    for (const field of ["run_id", "command_id", "operation"]) string(message[field]);
    if (typeof message.accepted !== "boolean") throw new Error("invalid control result");
  } else if (type === "pong") string(message.nonce);
  else throw new Error("unknown server message");
  return message;
}
