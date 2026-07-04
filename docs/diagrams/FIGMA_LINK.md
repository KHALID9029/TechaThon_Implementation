# System Diagrams — Figma Source

Live, editable FigJam board (both diagrams below live here as one file):

https://www.figma.com/board/quHdN9vRGuVkYxfXhjZuJD

- `system_architecture.png` — 15 Devices -> Simulator -> SQLite + Event Bus -> {Web Dashboard via WS, Discord Bot via commands/alert posts}, with Gemini humanizing bot replies.
- `data_flow.png` — sequence diagram: a device toggle's path to the dashboard, and the alert evaluator's periodic check fanning out to the dashboard and to Discord (via Gemini).

Known issue in the live board (not in the exported PNGs, which were rendered independently): there is a leftover empty "System Architecture Export Frame" section sitting near the architecture diagram, left over from generating the PNG exports. Figma's MCP tool-call limit for this account was hit before it could be cleaned up — select that section in the file and delete it (or send it to back) next time you're in Figma with access.
