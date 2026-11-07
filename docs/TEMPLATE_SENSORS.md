# Home Assistant template sensors

The temperature and humidity inputs come from two trigger-based template sensors, used by both the `safe_export` integration and the Node-RED flow.

The model needs overnight mean temperature and humidity derived from the hourly weather forecast. Add these trigger-based template sensors to your HA `configuration.yaml` (or a package YAML file). They call `weather.get_forecasts` on a schedule and average the 6pm–11am window.

```yaml
template:
  - trigger:
      - trigger: time
        at: "17:59:00"
      - trigger: homeassistant
        event: start
      - trigger: event
        event_type: event_template_reloaded
    action:
      - action: weather.get_forecasts
        data:
          type: hourly
        target:
          entity_id: weather.truganina_hourly  # Replace with your weather entity
        response_variable: hourly
    sensor:
      - name: "Overnight Forecast Temp Mean"
        unique_id: overnight_forecast_temp_mean
        unit_of_measurement: "°C"
        state_class: measurement
        state: >
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}  {# Replace entity name #}
          {% set tomorrow = (now().date() + timedelta(days=1)).strftime('%Y-%m-%d') %}
          {% set tonight = now().date().strftime('%Y-%m-%d') %}
          {% set ns = namespace(total=0, count=0) %}
          {% for f in forecasts %}
            {% set dt = f.datetime %}
            {% set is_tonight = dt >= tonight ~ 'T18:00:00' and dt < tonight ~ 'T24:00:00' %}
            {% set is_tomorrow_morning = dt >= tomorrow ~ 'T00:00:00' and dt <= tomorrow ~ 'T11:00:00' %}
            {% if is_tonight or is_tomorrow_morning %}
              {% set ns.total = ns.total + f.temperature %}
              {% set ns.count = ns.count + 1 %}
            {% endif %}
          {% endfor %}
          {{ (ns.total / ns.count) | round(1) if ns.count > 0 else 'unknown' }}

      - name: "Overnight Forecast Humidity Mean"
        unique_id: overnight_forecast_humidity_mean
        unit_of_measurement: "%"
        state_class: measurement
        state: >
          {% set forecasts = hourly['weather.truganina_hourly']['forecast'] %}  {# Replace entity name #}
          {% set tomorrow = (now().date() + timedelta(days=1)).strftime('%Y-%m-%d') %}
          {% set tonight = now().date().strftime('%Y-%m-%d') %}
          {% set ns = namespace(total=0, count=0) %}
          {% for f in forecasts %}
            {% set dt = f.datetime %}
            {% set is_tonight = dt >= tonight ~ 'T18:00:00' and dt < tonight ~ 'T24:00:00' %}
            {% set is_tomorrow_morning = dt >= tomorrow ~ 'T00:00:00' and dt <= tomorrow ~ 'T11:00:00' %}
            {% if is_tonight or is_tomorrow_morning %}
              {% set ns.total = ns.total + f.humidity %}
              {% set ns.count = ns.count + 1 %}
            {% endif %}
          {% endfor %}
          {{ (ns.total / ns.count) | round(1) if ns.count > 0 else 'unknown' }}
```

The `event_template_reloaded` trigger fires immediately when you reload templates via the UI — no HA restart needed after a config change.
