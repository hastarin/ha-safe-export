# Manual prediction

Run the canonical Python predictor by hand, for example to check a night's result against the live system.
Run these from the repository root with the project venv and a filled-in `config/config.yaml`.

Read the values from HA and call `predict()` from the command line:

```bash
.venv\Scripts\python -c "
from pathlib import Path
from src.config import load_config
from src.model import PredictInputs, predict
cfg = load_config(Path('config/config.yaml'))
result = predict(PredictInputs(
    soc_at_6pm=85.0,                      # live battery SoC at 6pm (%)
    bom_temp_mean=10.5,                   # sensor.overnight_forecast_temp_mean
    bom_humidity_mean=87.0,               # sensor.overnight_forecast_humidity_mean
    solcast_forecast_tomorrow_wh=18000,   # sensor.solcast_pv_forecast_forecast_tomorrow
    min_soc=0.10,                         # battery min SoC setting (0.20 in storm mode)
    confidence=0.90,
), cfg)
print(f'Safe export: {result.safe_export_wh:.0f} Wh  ({result.safe_export_wh/1000:.2f} kWh)')
print(result.reasoning)
"
```

Or from a Python script/REPL:

```python
from pathlib import Path
from src.config import load_config
from src.model import PredictInputs, predict

cfg = load_config(Path("config/config.yaml"))
result = predict(PredictInputs(
    soc_at_6pm=85.0,               # live battery SoC at 6pm (%)
    bom_temp_mean=10.5,
    bom_humidity_mean=87.0,
    solcast_forecast_tomorrow_wh=18000,
    min_soc=0.10,
    confidence=0.90,
), cfg)

print(f"Safe export: {result.safe_export_wh:.0f} Wh")
print(f"Zone: {result.zone}, model: {result.model_variant}")
print(f"Predicted consumption: {result.predicted_consumption_kwh:.1f} kWh "
      f"+ {result.error_buffer_kwh:.1f} kWh buffer")
print(result.reasoning)
```
