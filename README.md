<!--
SPDX-FileCopyrightText: 2026 Curtis Galloway
SPDX-License-Identifier: Apache-2.0
-->
# field-station

Processes outside-world sensor data (ADS-B and AIS contacts first) and
publishes it for others to consume.

Receiver URLs are supplied with flags or the `FIELD_STATION_ADSB_URL` and
`FIELD_STATION_AIS_URL` environment variables. There are no defaults.

Licensed under Apache 2.0.
