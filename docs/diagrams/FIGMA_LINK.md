# System Diagrams — Figma Source

Live, editable FigJam board (both diagrams below live here as one file):

https://www.figma.com/board/quHdN9vRGuVkYxfXhjZuJD

- `system_architecture.png` — 15 Devices -> Simulator -> SQLite + Event Bus -> {Web Dashboard via WS, Discord Bot via commands/alert posts}, with Gemini humanizing bot replies.
- `data_flow.png` — sequence diagram: a device toggle's path to the dashboard, and the alert evaluator's periodic check fanning out to the dashboard and to Discord (via Gemini).