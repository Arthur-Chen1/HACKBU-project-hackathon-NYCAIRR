# Study site crosswalk

`study_building_bins.csv` maps the Dryad study's building letter codes to NYC Building Identification Numbers (BINs). The addresses come from Table S2 of the study's supplement ([Chen et al. 2024, Appendix 1](https://doi.org/10.1111/1365-2664.14590)). Each address was geocoded with [NYC GeoSearch](https://geosearch.planninglabs.nyc/) and checked against the building's height, construction year, and name in NYC Building Footprints. `survey_distance_m` is Table S2's "Distance (m)": the facade length volunteers surveyed at each site. The model uses it as the exposure behind each count. `python -m src.ingest sites` reads `study_building_id`, `bin`, and `survey_distance_m`. The other columns document the match.

Six sites are left out because the match could not be verified. Their survey rows still train the night-level model; they just carry no building traits.

| Code | Table S2 address | Why it is excluded |
| --- | --- | --- |
| o | 23-21 44th Ave, Queens | GeoSearch returned a different address (21-23 44 Ave, a 17 m 1915 building). |
| p | 25-25 44th Dr, Queens | No match. The nearest results are other addresses on 44 Dr. |
| s | 13 Furman St, Brooklyn | Matched a 5 m 1910 building, which does not fit the large building in Figure S1. |
| w | 85 St Nicholas Terrace (CUNY ASRC) | The campus lot has several footprints with the lot's 1930 date; the ASRC footprint could not be singled out. |
| x | 85 St Nicholas Terrace (CDI) | Same campus lot as w. |
| bb | 43-32 Crescent St, Queens | GeoSearch returned the placeholder BIN 4000000. The tax lot holds several buildings, and none clearly matches Figure S1. |

Code `r` (1 Hotel Brooklyn Bridge Park, fall 2021 facades) is also absent: Table S2 lists it, but the Dryad release has no rows coded `r`.

To add one of the excluded sites, look up the building's BIN (for example on [NYC ZoLa](https://zola.planning.nyc.gov/)), confirm it against Figure S1, add a row, and rerun `python -m src.ingest sites`.
