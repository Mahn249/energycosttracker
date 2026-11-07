# Version 0.1.0
First alpha version for live testing.
## Known Bugs:
- The frontend must be forcibly reloaded manually after every update.
- The "Current energy price" sensor is not recognized as a numeric value by the Energy dashboard.
- The Lovelace card does not quite fit on narrow screens yet.

## Planned Changes
- Remove consumption tracking based on power, as it is not accurate enough: small consumption intervals are counted as 0 and are therefore not included in the calculation.
  - Users with only power sensors will need to create their own helper in Home Assistant (preferably the same one used by the Home Assistant Energy dashboard).
- Write only valid meter readings to the database so that older checkpoints remain valid.
- Add a "Developer view" switch in the integration settings to display the following on the card:
  - Last saved reading (timestamp and delta from the previous reading)
  - Data quality (longest period between individual checkpoints in the last 24 hours)
  - Make the card's date range configurable using the [Home Assistant energy date picker](https://www.home-assistant.io/dashboards/energy/#energy-date-picker).

# Version 0.2.0
- Consumption tracking accepts energy sensors with device class `energy` and unit `kWh`, regardless of state class.
- The meter reading and consumption interval are written to the database in a single transaction.
- Missing, invalid, or non-reconstructable consumption remains unknown; meter replacements do not create a difference between different entities.
- The recording interval is fixed at 10 minutes; the option has been removed from the settings.
- Daily and monthly costs use the same time-proportional interval allocation across local day, month, and tariff boundaries; base and consumption costs are calculated separately.

# Version 0.2.1
- Code cleanup
- Updated the README and changelog
- Added comments to files
