# gravelord (webapp)

React dashboard for the gravelord orchestrator. Not yet implemented.

Will consume:
- `GET  /status` — snapshot of running and retrying agents
- `WS   /stream` — real-time event broadcast (`worker_dispatched`,
  `turn_started`, `turn_completed`, `worker_finished`, `worker_failed`,
  `worker_retrying`, `stall_detected`, `label_changed`,
  `rework_context_loaded`, `config_reloaded`)
