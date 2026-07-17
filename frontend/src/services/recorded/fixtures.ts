import { recordedJourneySchema } from "../../schemas/phase1";
import rawJourney from "./goldenJourney.json";


export const goldenJourney = recordedJourneySchema.parse(rawJourney);
export const goldenExperiment = goldenJourney.experiment;
export const secondaryExperiment = goldenJourney.secondary_experiment;
export const baselineAnalysis = goldenJourney.baseline_analysis;
export const replayAnalysis = goldenJourney.replay_analysis;
export const recordedEvents = goldenJourney.events;

export const GOLDEN_EXPERIMENT_ID = goldenExperiment.id;

function required<T>(value: T | null | undefined, name: string): T {
  if (value == null) throw new Error(`Recorded fixture is missing ${name}`);
  return value;
}

export const baselineRun = required(
  goldenExperiment.runs.find((run) => run.kind === "baseline"),
  "baseline run",
);
export const replayRun = required(
  goldenExperiment.runs.find((run) => run.kind === "replay"),
  "replay run",
);
export const candidatePolicy = required(
  goldenExperiment.candidate_policy,
  "candidate policy",
);

export const BASELINE_RUN_ID = baselineRun.id;
export const REPLAY_RUN_ID = replayRun.id;
export const CANDIDATE_POLICY_ID = candidatePolicy.id;

export function cloneFixture<T>(value: T): T {
  return structuredClone(value);
}
