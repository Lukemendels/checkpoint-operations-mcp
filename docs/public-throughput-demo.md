# Public TSA Throughput Demonstration

This read-only MCP path returns **historical reported passengers screened**, summed
by airport and report date over available checkpoint-hour observations. It is
separate from the synthetic PIMS-style data, wait modeling and staffing simulations.
It does not measure arrivals, unique travelers, waits, staffing or current operations.

## Demonstration

Call `public_tsa_airport_day_throughput` with
`{"airport":"IAD","day":"2018-12-30"}`. The result is **27,089 reported screenings**
across **83 source checkpoint-hour observations**, labeled
`public_origin_historical_tsa_report`. An uncovered airport/date returns
`no_observation` and a null count, not zero.

The embedded sample covers December 30–31, 2018: 829 airport-day aggregates
from 15,948 nonconflicting source observations and 417 distinct airports.
It is not an assertion that every airport reported a full day.

## Reproduction and limitations

- [Data.gov catalog](https://catalog.data.gov/dataset/tsa-foia-reading-room-weekly-passenger-throughput-data):
  TSA FOIA Reading Room weekly reports.
- [Mirrored weekly PDF](https://raw.githubusercontent.com/mikelor/TsaThroughput/main/data/december_30_2018_to_january_5_2019.pdf):
  public third-party mirror, not direct TSA retrieval. Git blob:
  `f2b6c5a30d29eed112a8f2dfa82d0ee6718eb0fa`.
- [Independent DataLumos catalog entry](https://www.datalumos.org/datalumos/project/230861/version/V1/view?path=%2Fdatalumos%2F230861%2Ffcr%3Aversions%2FV1%2Fdecember_30_2018_to_january_5_2019.pdf&type=file): corroborates the exact filename in a preserved TSA Reading Room collection, not byte equivalence.
- The included CSV is sufficient to run the demo. The underlying report
  calls this metric “PMIS - Total Customer Throughput (Unadjusted).” The
  separate intermediate SQLite archive is not included. The builder checks
  the expected archive checksum and observation counts if that input is
  independently available; it is not required for this demonstration.

The two-day sample is a bounded demonstration of access to public data, not
an evaluation or a production forecasting input. It is not fed into staffing.
