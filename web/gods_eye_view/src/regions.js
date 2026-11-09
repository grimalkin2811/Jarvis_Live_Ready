/**
 * Named focus regions (closed table — the only places world_focus_region is
 * allowed to fly to without explicit coordinates). Altitudes are "context"
 * views: enough to see a metropolis and its air traffic.
 */
export const FOCUS_REGIONS = Object.freeze({
  paris:      { label: 'Paris',        latitude: 48.8566,  longitude: 2.3522,   altitude_m: 150_000 },
  londres:    { label: 'Londres',      latitude: 51.5072,  longitude: -0.1276,  altitude_m: 150_000 },
  berlin:     { label: 'Berlin',       latitude: 52.52,    longitude: 13.405,   altitude_m: 150_000 },
  madrid:     { label: 'Madrid',       latitude: 40.4168,  longitude: -3.7038,  altitude_m: 150_000 },
  rome:       { label: 'Rome',         latitude: 41.9028,  longitude: 12.4964,  altitude_m: 150_000 },
  amsterdam:  { label: 'Amsterdam',    latitude: 52.3676,  longitude: 4.9041,   altitude_m: 150_000 },
  bruxelles:  { label: 'Bruxelles',    latitude: 50.8503,  longitude: 4.3517,   altitude_m: 150_000 },
  geneve:     { label: 'Genève',       latitude: 46.2044,  longitude: 6.1432,   altitude_m: 150_000 },
  lyon:       { label: 'Lyon',         latitude: 45.764,   longitude: 4.8357,   altitude_m: 120_000 },
  marseille:  { label: 'Marseille',    latitude: 43.2965,  longitude: 5.3698,   altitude_m: 120_000 },
  toulouse:   { label: 'Toulouse',     latitude: 43.6047,  longitude: 1.4442,   altitude_m: 120_000 },
  strasbourg: { label: 'Strasbourg',   latitude: 48.5734,  longitude: 7.7521,   altitude_m: 120_000 },
  nice:       { label: 'Nice',         latitude: 43.7102,  longitude: 7.262,    altitude_m: 120_000 },
  nantes:     { label: 'Nantes',       latitude: 47.2184,  longitude: -1.5536,  altitude_m: 120_000 },
  lille:      { label: 'Lille',        latitude: 50.6292,  longitude: 3.0573,   altitude_m: 120_000 },
  singapour:  { label: 'Singapour',    latitude: 1.3521,   longitude: 103.8198, altitude_m: 150_000 },
  tokyo:      { label: 'Tokyo',        latitude: 35.6762,  longitude: 139.6503, altitude_m: 150_000 },
  istanbul:   { label: 'Istanbul',     latitude: 41.0082,  longitude: 28.9784,  altitude_m: 150_000 },
  dubai:      { label: 'Dubaï',        latitude: 25.2048,  longitude: 55.2708,  altitude_m: 150_000 },
  newyork:    { label: 'New York',     latitude: 40.7128,  longitude: -74.006,  altitude_m: 150_000 },
  montreal:   { label: 'Montréal',     latitude: 45.5019,  longitude: -73.5674, altitude_m: 150_000 },
  sanfrancisco:{ label: 'San Francisco',latitude: 37.7749,  longitude: -122.4194,altitude_m: 150_000 },
  losangeles: { label: 'Los Angeles',  latitude: 34.0522,  longitude: -118.2437,altitude_m: 150_000 },
  sydney:     { label: 'Sydney',       latitude: -33.8688, longitude: 151.2093, altitude_m: 150_000 },
});

export const REGION_NAMES = Object.freeze(Object.keys(FOCUS_REGIONS));

export function resolveRegion(name) {
  return FOCUS_REGIONS[String(name || '').toLowerCase().trim()] ?? null;
}
