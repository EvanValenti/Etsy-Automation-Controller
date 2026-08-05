import type { EngineState, JobStatus } from "../api/types";
import { ENGINE_STATE, JOB_STATUS } from "../status";
import { Badge } from "./Badge";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const info = JOB_STATUS[status];
  return <Badge color={info.color} label={info.label} pulse={info.pulse} />;
}

export function EngineStateBadge({ state }: { state: EngineState }) {
  const info = ENGINE_STATE[state];
  return <Badge color={info.color} label={info.label} pulse={info.pulse} />;
}
