// Promptfoo provider that replays recorded Onramp evaluation outcomes. It never calls a model:
// the prompt text is the prompt version, the provider config names the model, and the case comes
// from the test's vars. See backend/src/onramp/evaluation/promptfoo.py.
const fs = require('fs');

class ReplayProvider {
  constructor(options) {
    this.model = options.config.model;
    this.results = JSON.parse(fs.readFileSync(options.config.results, 'utf8'));
    this.label = options.label || this.model;
  }

  id() {
    return `replay:${this.model}`;
  }

  async callApi(prompt, context) {
    const run = this.results[`${this.model}|${prompt}`] || {};
    const recorded = run[context.vars.scenario_id];
    if (!recorded) {
      return { error: `no recorded outcome for ${this.model} ${prompt} ${context.vars.scenario_id}` };
    }
    return { output: JSON.stringify(recorded), cost: recorded.cost_usd };
  }
}

module.exports = ReplayProvider;
