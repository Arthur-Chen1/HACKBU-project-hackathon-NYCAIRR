// Run with node tests/test_bird_guide.js. No browser or additional dependencies required.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dashboard = path.join(__dirname, '..', 'src', 'dashboard');
let focused = false;
const nodes = Object.fromEntries(['guideNight', 'guideFact', 'guideSource', 'nextBirdFact',
  'closeBirdGuide', 'birdGuide', 'nightTitle', 'stats', 'note'].map(id => [id, {}]));
nodes.birdGuide.open = true;
nodes.birdGuide.querySelector = () => ({focus: () => { focused = true; }});
const context = vm.createContext({window: {}, document: {getElementById: id => nodes[id]}});
vm.runInContext(fs.readFileSync(path.join(dashboard, 'bird-guide.js'), 'utf8'), context);
assert.deepEqual(Object.keys(context.window), ['BirdGuide']);
const first = nodes.guideFact.textContent;
nodes.nextBirdFact.onclick();
assert.notEqual(nodes.guideFact.textContent, first);
assert.match(nodes.guideSource.href, /^https:\/\//);
nodes.nextBirdFact.onclick();
nodes.nextBirdFact.onclick();
assert.equal(nodes.guideFact.textContent, first);
nodes.closeBirdGuide.onclick();
assert.equal(nodes.birdGuide.open, false);
assert.equal(focused, true);
nodes.birdGuide.open = true;
nodes.birdGuide.onkeydown({key: 'Escape'});
assert.equal(nodes.birdGuide.open, false);

const forecast = {night:'2026-11-08', mode:'forecast', traffic_birds_per_km_h:2000,
  dimming_estimates:100, buildings:100, mean_suggested_dimming_pct:25, dimming_bands:{high:5}};
context.window.BirdGuide.renderNight(forecast);
assert.match(nodes.guideNight.textContent, /migration forecast/);
assert.doesNotMatch(nodes.guideNight.textContent, /radar/);
assert.match(nodes.guideNight.textContent, /25%/);
assert.match(nodes.guideNight.textContent, /planning estimates/);
context.window.BirdGuide.renderNight({...forecast, mode:'observed', traffic_birds_per_km_h:0, mean_suggested_dimming_pct:0});
assert.match(nodes.guideNight.textContent, /radar estimated 0/);
assert.match(nodes.guideNight.textContent, /0% less/);
context.window.BirdGuide.renderNight({...forecast, traffic_birds_per_km_h:null, dimming_estimates:0});
assert.match(nodes.guideNight.textContent, /couldn’t give a usable migration rate/);
assert.match(nodes.guideNight.textContent, /dimming estimates are unavailable/);

// The existing summary still renders dimming stats and keeps the removed model banner absent.
const html = fs.readFileSync(path.join(dashboard, 'index.html'), 'utf8');
const script = html.split('<script>')[1].split('</script>')[0];
new vm.Script(script); // Parse the entire current dashboard, not just the widget.
const summary = script.slice(script.indexOf('function renderSummary('), script.indexOf('function renderMap('));
vm.runInContext(`const $ = id => document.getElementById(id);
  const css = key => key; const fmt = value => value == null ? '–' : String(value);
  const dimPct = value => value == null ? '–' : value + '%';\n` + summary, context);
context.input = {...forecast, scored_buildings:100, max_suggested_dimming_pct:80,
  level:'high', model:'REMOVED MODEL BANNER', note:''};
vm.runInContext('renderSummary(input)', context);
assert.match(nodes.stats.innerHTML, /80%/);
assert.equal(nodes.note.textContent, '');
assert.match(nodes.guideNight.textContent, /migration forecast/);
// A missing widget script must not prevent dashboard summaries from rendering.
delete context.window.BirdGuide;
vm.runInContext('renderSummary(input)', context);
assert.match(nodes.stats.innerHTML, /80%/);
console.log('Bird guide interactions and dashboard integration checks passed');
