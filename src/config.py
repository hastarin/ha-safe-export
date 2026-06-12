"""Load and validate the installation-specific configuration from config.yaml."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass
class ProviderPeriod:
    name: str
    start_date: date


@dataclass
class AbsencePeriod:
    start: date
    end: date


@dataclass
class SensorConfig:
    battery_soc: str
    pv: str
    load: str
    grid_import: str
    grid_export: str
    battery_charged: str
    battery_discharged: str
    outdoor_temp: str
    indoor_temp: str
    weather_temp: str
    weather_feels_like: str
    weather_rain: str
    weather_wind: str
    weather_gust: str
    weather_humidity: str
    solcast: str | None = None
    guests: str | None = None
    median_temp: str | None = None
    median_humidity: str | None = None
    forecast_temp: str | None = None
    forecast_humidity: str | None = None


@dataclass
class ModelConfig:
    heating_intercept: float
    heating_b_temp: float
    heating_b_solcast: float
    heating_temp_only_intercept: float
    heating_temp_only_b_temp: float
    cooling_intercept: float
    cooling_b_temp: float
    cooling_b_humidity: float
    cooling_temp_only_intercept: float
    cooling_temp_only_b_temp: float
    mild_p50: float
    mild_p75: float
    mild_p90: float
    mild_p95: float
    warm_boundary_p50: float
    warm_boundary_p75: float
    warm_boundary_p90: float
    warm_boundary_p95: float
    heating_p95_buffer_kwh: float
    cooling_p95_buffer_kwh: float


@dataclass
class Config:
    battery_capacity_wh: float
    battery_reserve_fraction: float
    timezone: ZoneInfo
    sensors: SensorConfig
    providers: list[ProviderPeriod]
    model: ModelConfig
    absence_periods: list[AbsencePeriod] = field(default_factory=list)
    data_gap_dates: frozenset[date] = field(default_factory=frozenset)

    def provider_for(self, d: date) -> str:
        """Return the provider name for a given date."""
        sorted_providers = sorted(self.providers, key=lambda p: p.start_date)
        current = sorted_providers[0].name
        for p in sorted_providers:
            if d >= p.start_date:
                current = p.name
        return current

    def is_absence(self, d: date) -> bool:
        return any(p.start <= d <= p.end for p in self.absence_periods)

    def is_data_gap(self, d: date) -> bool:
        return d in self.data_gap_dates

    @property
    def sensor_ids(self) -> dict[str, str | None]:
        """Return the sensor config as a dict for use in extract.py."""
        s = self.sensors
        return {
            "battery_soc": s.battery_soc,
            "pv": s.pv,
            "load": s.load,
            "grid_import": s.grid_import,
            "grid_export": s.grid_export,
            "battery_charged": s.battery_charged,
            "battery_discharged": s.battery_discharged,
            "outdoor_temp": s.outdoor_temp,
            "indoor_temp": s.indoor_temp,
            "guests": s.guests,
            "weather_temp": s.weather_temp,
            "weather_feels_like": s.weather_feels_like,
            "weather_rain": s.weather_rain,
            "weather_wind": s.weather_wind,
            "weather_gust": s.weather_gust,
            "solcast": s.solcast,
            "median_temp": s.median_temp,
            "weather_humidity": s.weather_humidity,
            "median_humidity": s.median_humidity,
            "forecast_temp": s.forecast_temp,
            "forecast_humidity": s.forecast_humidity,
        }


def load_config(path: Path) -> Config:
    """Load and validate config.yaml. Raises ValueError on missing required fields."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    battery = raw["battery"]
    capacity_wh = float(battery["capacity_wh"])
    reserve_fraction = float(battery["reserve_fraction"])

    tz = ZoneInfo(raw["timezone"])

    s = raw["sensors"]
    sensors = SensorConfig(
        battery_soc=s["battery_soc"],
        pv=s["pv"],
        load=s["load"],
        grid_import=s["grid_import"],
        grid_export=s["grid_export"],
        battery_charged=s["battery_charged"],
        battery_discharged=s["battery_discharged"],
        outdoor_temp=s["outdoor_temp"],
        indoor_temp=s["indoor_temp"],
        weather_temp=s["weather_temp"],
        weather_feels_like=s["weather_feels_like"],
        weather_rain=s["weather_rain"],
        weather_wind=s["weather_wind"],
        weather_gust=s["weather_gust"],
        weather_humidity=s["weather_humidity"],
        solcast=s.get("solcast") or None,
        guests=s.get("guests") or None,
        median_temp=s.get("median_temp") or None,
        median_humidity=s.get("median_humidity") or None,
        forecast_temp=s.get("forecast_temp") or None,
        forecast_humidity=s.get("forecast_humidity") or None,
    )

    providers = [
        ProviderPeriod(name=p["name"], start_date=date.fromisoformat(p["start_date"]))
        for p in raw["providers"]
    ]
    if not providers:
        raise ValueError("config.yaml must define at least one provider period")

    absence_periods = [
        AbsencePeriod(start=date.fromisoformat(a["start"]), end=date.fromisoformat(a["end"]))
        for a in (raw.get("absence_periods") or [])
    ]

    data_gap_dates = frozenset(
        date.fromisoformat(d) for d in (raw.get("data_gap_dates") or [])
    )

    m = raw["model"]
    model = ModelConfig(
        heating_intercept=float(m["heating_intercept"]),
        heating_b_temp=float(m["heating_b_temp"]),
        heating_b_solcast=float(m["heating_b_solcast"]),
        heating_temp_only_intercept=float(m["heating_temp_only_intercept"]),
        heating_temp_only_b_temp=float(m["heating_temp_only_b_temp"]),
        cooling_intercept=float(m["cooling_intercept"]),
        cooling_b_temp=float(m["cooling_b_temp"]),
        cooling_b_humidity=float(m["cooling_b_humidity"]),
        cooling_temp_only_intercept=float(m["cooling_temp_only_intercept"]),
        cooling_temp_only_b_temp=float(m["cooling_temp_only_b_temp"]),
        mild_p50=float(m["mild_p50"]),
        mild_p75=float(m["mild_p75"]),
        mild_p90=float(m["mild_p90"]),
        mild_p95=float(m["mild_p95"]),
        warm_boundary_p50=float(m["warm_boundary_p50"]),
        warm_boundary_p75=float(m["warm_boundary_p75"]),
        warm_boundary_p90=float(m["warm_boundary_p90"]),
        warm_boundary_p95=float(m["warm_boundary_p95"]),
        heating_p95_buffer_kwh=float(m["heating_p95_buffer_kwh"]),
        cooling_p95_buffer_kwh=float(m["cooling_p95_buffer_kwh"]),
    )

    return Config(
        battery_capacity_wh=capacity_wh,
        battery_reserve_fraction=reserve_fraction,
        timezone=tz,
        sensors=sensors,
        providers=providers,
        model=model,
        absence_periods=absence_periods,
        data_gap_dates=data_gap_dates,
    )
