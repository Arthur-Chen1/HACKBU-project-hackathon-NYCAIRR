# How the bird collision model would work

## What data can we pull today?

| Data | What we get | What it describes |
| --- | --- | --- |
| NYC building layout | Building outlines, location, roof height, footprint area, building ID (BIN), and tax-lot land use | One building and its lot. Land use helps identify commercial sites. |
| Artificial light | NASA Black Marble nighttime brightness, with quality and cloud flags | One satellite pixel about 500 m wide. Several buildings can share the same brightness reading. |
| Natural light | Sunset, sunrise, civil dusk and dawn, the fraction of the Moon lit, and moonrise and moonset, which we use to calculate time above the horizon | One NYC night. The Moon fields are clues about available moonlight. |
| Bird migration | A radar estimate of how many birds passed through the NYC region overnight | One regional migration estimate per monitored night. |
| Observed weather | Wind, visibility, cloud ceiling, and temperature from LaGuardia, summarized overnight | What happened during a historical night. |
| Weather forecast | Hourly NOAA forecasts for wind, visibility, cloud ceiling, cloud cover, and precipitation rate, summarized by night | What a forecast available before sunset predicted for a nearby weather grid cell. |
| Bird collision surveys | The study's building code, survey date, and number of birds found | The outcome the model learns from. A completed survey can record zero. An unsurveyed night has no label. |

The baseline CSV already contains the surveys, migration, and observed weather. The fall 2021 export adds Sun and Moon data for 76 consecutive nights. The NOAA forecast pull was tested for one night. NASA tile downloads require an Earthdata token. Connecting survey codes to individual NYC footprints requires a verified mapping to BINs. Building operators would provide actual lighting schedules, dimmer settings, and measurements in a later pilot. [Pipeline instructions and data sources](README.md#run-the-python-data-pipeline)

## What does one row represent?

One row is **one building on one NYC night**. `night_date` is the local date when the night starts. A survey done the next morning supplies the outcome for that row. Two buildings on the same night can share migration, moonlight, and weather values. Once matched to their footprints, they can have different roof heights, land uses, and neighborhood brightness. If two buildings have identical model inputs, the model gives them the same estimate.

## Will each building get a risk percentage?

**Yes, that’s the proposed output once a model is trained and validated.** A **30% risk** means that, for building-nights with similar inputs, a comparable morning survey is expected to find at least one bird collision about 30 times out of 100. The surveys count birds that were found, so this probability depends on how the buildings were monitored. The current pipeline produces the data for training. The hackathon demo can show **low, medium, or high priority** using rules while the team builds and tests the model.

## How do the inputs become a prediction?

1. **Build the row.** Match the survey site to a NYC building ID and attach the building details. Use its location to look up weather and satellite brightness, then add the region’s migration estimate and the night’s Sun and Moon data. Unmatched sites can still support a baseline using regional inputs.
2. **Learn from past surveys.** Turn each usable survey into a simple outcome. If the survey found at least one collision, the answer is yes. If it found zero, the answer is no. A simple probability model can learn whether combinations like heavy migration and poor visibility tend to come with more yes outcomes. The count model outlined in the README can also estimate how many birds a survey might find.
3. **Check the percentages.** Test on later nights and on buildings left out of training. Among rows scored near 30%, about 30% should have a positive survey outcome. Checking unseen buildings matters because the original study monitored a selected set of sites.
4. **Score tonight before sunset.** Make one input row per candidate building using its known details, tonight’s weather and migration forecasts, known Sun and Moon times, and previously available satellite brightness. Train and test this version using inputs that would have been available at that same cutoff. The current migration pull gives historical observations, so tonight’s migration forecast needs a separate input.

## How does risk become a dimming recommendation?

The risk estimate tells us **which buildings to prioritize**. A separate rule or pilot model decides **how much to dim the eligible lights and when**.

| Example dashboard field | Illustrative output |
| --- | --- |
| Building and night | Example Building A, October 1 |
| Estimated risk | 30% chance a morning survey finds at least one collision |
| Factors behind the alert | Heavy regional migration and poor forecast visibility |
| Demo lighting action | Reduce scheduled output of eligible lights by 50% from civil dusk to civil dawn |

The demo uses a configurable dimming rule. To learn an effective dimming percentage, a pilot would record the lighting settings that actually ran and compare survey outcomes across approved settings under similar conditions. Those measurements would let the system compare the building’s usual schedule with lower settings and choose an approved reduction with enough evidence to meet the pilot’s risk target. Required lighting stays outside the dimming plan.
