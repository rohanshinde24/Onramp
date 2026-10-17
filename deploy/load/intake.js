// Opens one payout case per iteration at a constant arrival rate, through the API.
// Run by `onramp-load-run`, which seeds the subjects and passes RUN_ID, RATE, DURATION, API
// and TOKEN. Iteration i names subject loadtest_<RUN_ID>_s_<i>, which the runner seeded.
import http from "k6/http";
import exec from "k6/execution";
import { check } from "k6";

const rate = Number(__ENV.RATE);
const duration = `${Number(__ENV.DURATION)}s`;

export const options = {
  discardResponseBodies: true,
  scenarios: {
    intake: {
      executor: "constant-arrival-rate",
      rate,
      timeUnit: "1s",
      duration,
      preAllocatedVUs: Math.max(10, rate * 2),
      maxVUs: Math.max(50, rate * 10),
    },
  },
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

const headers = {
  Authorization: `Bearer ${__ENV.TOKEN}`,
  "Content-Type": "application/json",
};

export default function () {
  const i = exec.scenario.iterationInTest;
  const body = JSON.stringify({
    case_type: "payout",
    subject_id: `loadtest_${__ENV.RUN_ID}_s_${i}`,
    intake_key: `${__ENV.RUN_ID}-${i}`,
    description: "Load test: where is my payout?",
  });
  const response = http.post(`${__ENV.API}/cases`, body, { headers, timeout: "30s" });
  check(response, { "case opened": (r) => r.status === 201 });
}
