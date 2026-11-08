// Self-contained guide: no dependency on Leaflet or the dashboard's global helpers.
(() => {
'use strict';
const $ = id => document.getElementById(id);
const fmt = (value, digits = 0) => Number(value).toLocaleString(undefined, {maximumFractionDigits: digits});
const birdFacts = [
  ['White-throated Sparrows are the birds NYC Bird Alliance volunteers most often find after window collisions.',
    'NYC Bird Alliance', 'https://nycbirdalliance.org/our-work/conservation/project-safe-flight/collision-monitoring'],
  ['Peregrine Falcons breed in the NYC area. Bridges and tall buildings can serve as nesting sites.',
    'NYS DEC', 'https://extapps.dec.ny.gov/fs/programs/dfw/SWAP2025/Birds/Peregrine%20falcon.pdf'],
  ['The Common Yellowthroat passes through NYC during migration and also breeds in some of the city’s wilder places.',
    'NYC Bird Alliance', 'https://nycbirdalliance.org/our-work/conservation/project-safe-flight'],
];
let birdFactIndex = 0;
function showBirdFact() {
  const [fact, source, url] = birdFacts[birdFactIndex];
  $('guideFact').textContent = fact;
  $('guideSource').textContent = source;
  $('guideSource').href = url;
}
showBirdFact();
$('nextBirdFact').onclick = () => {
  birdFactIndex = (birdFactIndex + 1) % birdFacts.length;
  showBirdFact();
};
function closeBirdGuide() {
  $('birdGuide').open = false;
  $('birdGuide').querySelector('summary').focus();
}
$('closeBirdGuide').onclick = closeBirdGuide;
$('birdGuide').onkeydown = event => {
  if (event.key === 'Escape' && $('birdGuide').open) closeBirdGuide();
};

function renderNight(o) {
  const traffic = o.traffic_birds_per_km_h;
  const forecast = o.mode === 'forecast';
  const source = forecast ? 'the migration forecast' : 'radar';
  const messages = [traffic == null
    ? `For ${o.night}, ${source} couldn’t give a usable migration rate.`
    : `On ${o.night}, ${source} estimated ${fmt(traffic)} migrating birds per kilometer per hour around NYC. That’s a regional passage rate.`];
  if (o.dimming_estimates > 0 && Number.isFinite(o.mean_suggested_dimming_pct)) {
    messages.push(`The collision model informs dimming suggestions for ${fmt(o.dimming_estimates)} of ${fmt(o.buildings)} buildings, averaging ${fmt(o.mean_suggested_dimming_pct, 1)}% less nonessential light output.`);
    messages.push('These are planning estimates, not proven collision reductions. Red highlights roughly the largest 5% of suggested cuts.');
  } else {
    messages.push('Building dimming estimates are unavailable for this night. Check the analysis notes and building details for missing inputs.');
  }
  $('guideNight').textContent = messages.join(' ');
}
window.BirdGuide = Object.freeze({renderNight});
})();
